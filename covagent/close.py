"""Close the listed coverage holes by rewriting the stimulus block of a testbench.

The design is never edited. The model is given:
  - the RTL lines that are dark,
  - for each one, why it is dark and the stimulus that would reach it,
  - the CURRENT stimulus block, so it edits rather than rewrites,
and must return a replacement stimulus block.

Acceptance is not the model's opinion. It is: build, run, re-read coverage.info,
and check that the targeted lines moved from 0 to non-zero. A run whose closure
did not improve is discarded and the previous testbench is restored, so the
number reported is the number that survived the solver -- here, the simulator.
"""

import os
import re
import shutil

from . import holes as holes_mod
from . import llm as _llm
from . import sim

SYSTEM = """You edit Verilog testbenches to close coverage holes.

Rules, all of them learned from runs that failed:
- Return ONLY the testbench inside one fenced verilog block. No explanation.
- Keep the module name, the port list, the DUT instantiation and the clock
  generator character-for-character as they are. Only the stimulus changes.
- Keep the `$display("TB_DONE"); $finish;` at the end of the initial block.
- Every hole you are given names a signal and a value. Drive exactly that.
- Use the existing `drive(...)` task if one exists; do not invent a new timing
  style. Keep total simulation length under 200 clock cycles.
- Do not remove existing stimulus: coverage already earned must not be lost."""

USER = """The design under test:

```verilog
{dut}
```

Current coverage: {closure:.1f}% ({open_bins} bins still dark).

The dark lines, each with the reason and the stimulus that reaches it:
{holes}

The DRIVER testbench, which only covers part of the design:

```verilog
{driver}
```

The CURRENT testbench (the thing you are editing):

```verilog
{tb}
```

Return the full testbench with additional stimulus that reaches every dark line
listed above, while keeping everything the current testbench already did."""


def stimulus_block(tb):
    """Extract the `initial begin ... end` block that holds the stimulus."""
    m = re.search(r"initial\s+begin(.*?)\n\s*end\b", tb, re.S)
    return m.group(1) if m else ""


def closed_lines(report, lines):
    """Which of `lines` are no longer dark."""
    hits = {}
    for b in report.bins:
        m = re.search(r":(\d+)$", b.location or "")
        if m:
            hits[int(m.group(1))] = max(hits.get(int(m.group(1)), 0), b.hits)
    return [ln for ln in lines if hits.get(ln, 0) > 0], hits


class CloseResult:
    def __init__(self, status, rounds=None, before=None, after=None, tb=None,
                 reason=""):
        self.status = status
        self.rounds = rounds or []
        self.before = before
        self.after = after
        self.tb = tb
        self.reason = reason

    def to_dict(self):
        return {"status": self.status, "reason": self.reason,
                "before": self.before, "after": self.after,
                "rounds": self.rounds}


class Closer:
    """One design, a hole list, N attempts to close them."""

    def __init__(self, design_dir, rtl_file, tb_file, driver_file=None,
                 client=None, target=0.95, max_rounds=3, timeout=600,
                 model=None, wsl_timeout=900):
        self.design_dir = design_dir
        self.rtl_path = os.path.join(design_dir, rtl_file)
        self.tb_path = os.path.join(design_dir, tb_file)
        self.tb_file = tb_file
        self.driver_file = driver_file
        self.client = client or _llm.Client(model=model)
        self.target = target
        self.max_rounds = max_rounds
        self.timeout = timeout
        self.wsl_timeout = wsl_timeout
        self.rtl = open(self.rtl_path, encoding="utf-8", errors="ignore").read()

    def run(self):
        run = sim.run_make(self.design_dir, "cov", timeout=self.wsl_timeout)
        before = run.report.closure()
        if not run.report.bins:
            return CloseResult("NO_COVERAGE", before=before,
                               reason="the simulator produced no bins; log tail:\n"
                                      + run.log[-800:])
        rounds = []
        best_tb = open(self.tb_path, encoding="utf-8", errors="ignore").read()
        best_closure = before
        last_failure = ""

        for i in range(self.max_rounds):
            hs = holes_mod.find_holes(run.report, self.rtl_path)
            if not hs:
                return CloseResult("DONE", rounds, before, best_closure, best_tb,
                                   "no dark lines remain")
            if run.report.closure() >= self.target:
                break

            driver = (open(os.path.join(self.design_dir, self.driver_file),
                           encoding="utf-8", errors="ignore").read()
                      if self.driver_file else "")

            def esc(s):
                return s.replace("{", "{{").replace("}", "}}")

            prompt = (USER.format(dut=esc(self.rtl),
                                  closure=run.report.closure(),
                                  open_bins=len(run.report.open_bins),
                                  holes=esc(holes_mod.summarize(run.report, hs)),
                                  driver=esc(driver), tb=esc(best_tb)))
            reply = self.client.chat(prompt, system=SYSTEM, max_tokens=3000)
            cand = _extract(reply)
            if not cand or "module" not in cand:
                last_failure = "model returned no usable testbench"
                rounds.append({"round": i, "attempt": "REJECTED",
                               "reason": last_failure,
                               "reply_head": reply[:200]})
                continue

            backup = best_tb
            sim.write_testbench(self.design_dir, self.tb_file, cand)
            rr = sim.run_make(self.design_dir, "cov", timeout=self.wsl_timeout)
            improved = rr.report.closure() > best_closure + 1e-9
            built = rr.report.bins and rr.ok
            rounds.append({
                "round": i, "attempt": "KEPT" if (built and improved) else "DISCARDED",
                "closure": round(rr.report.closure(), 4),
                "prev": round(best_closure, 4),
                "bins": len(rr.report.bins),
                "log_tail": rr.log[-400:] if not built else "",
            })
            if built and improved:
                best_tb = cand
                best_closure = rr.report.closure()
                run = rr
            else:
                sim.write_testbench(self.design_dir, self.tb_file, backup)
                run = sim.run_make(self.design_dir, "cov", timeout=self.wsl_timeout)
                last_failure = ("the candidate did not build" if not built
                                else "closure did not improve (%.1f%% -> %.1f%%)"
                                % (best_closure * 100, rr.report.closure() * 100))
                if not built:
                    # a broken testbench will be regenerated from its own
                    # failure; do not let it poison the next prompt
                    pass

        status = "TARGET" if best_closure >= self.target else (
            "IMPROVED" if best_closure > before + 1e-9 else "NO_GAIN")
        return CloseResult(status, rounds, before, best_closure, best_tb,
                           last_failure or "rounds exhausted")


def _extract(reply):
    m = re.search(r"```(?:verilog|systemverilog|sv)?\n(.*?)```", reply, re.S)
    body = (m.group(1) if m else reply).strip()
    i = body.find("module")
    if i > 0:
        body = body[i:]
    return body
