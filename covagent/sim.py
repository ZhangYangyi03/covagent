"""Run a simulation, read the coverage it produced, and parse both.

Wraps the WSL side so the rest of the package never shells out itself. The
contract is: give it a directory with a Makefile, get back a RunResult carrying
the exit status, the transcript, and a CoverageReport built from the real data
the simulator wrote.
"""

import base64
import os
import re
import subprocess
import time

from . import coverage

WSL_DISTRO = os.environ.get("COVAGENT_WSL", "Ubuntu")


class RunResult:
    def __init__(self, ok, seconds, log, report, rc=None):
        self.ok = ok
        self.seconds = seconds
        self.log = log
        self.report = report
        self.rc = rc

    def to_dict(self):
        return {"ok": self.ok, "seconds": round(self.seconds, 2),
                "closure": round(self.report.closure(), 4) if self.report else None,
                "bins": len(self.report.bins) if self.report else 0,
                "log_tail": self.log[-1200:]}


def win_to_wsl(path):
    p = os.path.abspath(path).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    return "/mnt/" + m.group(1).lower() + "/" + m.group(2) if m else p


def wsl_bash(script, timeout=900):
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    cmd = "echo %s | base64 -d > /tmp/ca_run.sh && bash /tmp/ca_run.sh" % b64
    try:
        r = subprocess.run(["wsl", "-d", WSL_DISTRO, "--", "bash", "-lc", cmd],
                           capture_output=True, timeout=timeout)
        return (r.stdout or b"").decode("utf-8", "replace"), r.returncode
    except subprocess.TimeoutExpired:
        return "", -9


def parse_info(path):
    """Read an lcov-format coverage.info into a CoverageReport."""
    if not os.path.exists(path):
        return coverage.CoverageReport([])
    bins = []
    cur = None
    for line in open(path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if line.startswith("SF:"):
            cur = os.path.basename(line[3:])
        elif line.startswith("DA:") and cur:
            try:
                ln, hits = line[3:].split(",")[:2]
                bins.append(coverage.Bin("line", "%s:%s" % (cur, ln),
                                         location="%s:%s" % (cur, ln),
                                         hits=int(hits)))
            except ValueError:
                pass
        elif line.startswith("BRDA:") and cur:
            parts = line[5:].split(",")
            if len(parts) >= 4:
                ln, _, _, taken = parts[:4]
                hits = 0 if taken in ("-", "") else int(taken)
                bins.append(coverage.Bin("branch", "%s:%s:%s" % (cur, ln, parts[1]),
                                         location="%s:%s" % (cur, ln), hits=hits))
        elif line.startswith("TN:") or line.startswith("end_of_record"):
            cur = None if line.startswith("end_of_record") else cur
    return coverage.CoverageReport(bins, source=path, tool="verilator")


def run_make(design_dir, target="cov", timeout=900, extra_env=""):
    """Run `make <target>` in WSL and read back the coverage it produced."""
    mount = win_to_wsl(design_dir)
    script = "cd %s || exit 7\n%s make %s 2>&1\n" % (mount, extra_env, target)
    t0 = time.time()
    log, rc = wsl_bash(script, timeout=timeout)
    dt = time.time() - t0
    info = os.path.join(design_dir, "coverage.info")
    rep = parse_info(info)
    ok = ("TB_DONE" in log) or (rc == 0 and rep.bins)
    return RunResult(ok, dt, log, rep, rc)


def write_testbench(design_dir, filename, body):
    p = os.path.join(design_dir, filename)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return p


def toolchain_check():
    out, _ = wsl_bash("which verilator verilator_coverage iverilog 2>/dev/null; "
                      "verilator --version 2>/dev/null", 120)
    lines = [l for l in out.splitlines() if l.strip()]
    return bool(lines), " | ".join(lines)
