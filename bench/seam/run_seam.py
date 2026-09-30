"""Build every seam variant, run the coverage flow, and check what the report says.

    python bench/seam/run_seam.py                build and measure (needs verilator)
    python bench/seam/run_seam.py --replay       re-read the checked-in results

The claim under test is not "coverage is high". It is: **two assemblies with the
same line coverage are not the same assembly, and the difference is visible in
the data the simulator already writes** -- provided you read the raw database and
not the lcov summary, which drops every toggle bin.

Run from the repo root, or from anywhere: the paths are resolved from this file.
"""

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from covagent import boundary, sim                      # noqa: E402
from bench.seam.variants import SEAM_LINE, VARIANTS     # noqa: E402

WORK = os.path.join(HERE, "_work")
FIX = os.path.join(ROOT, "bench", "fixtures", "seam")
VFLAGS = ("--cc --timing --coverage --coverage-line --coverage-toggle -Wno-fatal "
          "-Wno-INITIALDLY")


def render(variant):
    """tb_seam.v with its one seam line rewritten, and the module renamed."""
    src = open(os.path.join(HERE, "tb_seam.v"), encoding="utf-8").read()
    if SEAM_LINE not in src:
        raise SystemExit("tb_seam.v no longer contains the seam line:\n  %s" % SEAM_LINE)
    src = src.replace(SEAM_LINE, variant["line"])
    name = "tb_seam_%s" % variant["name"]
    src = src.replace("module tb_seam;", "module %s;" % name, 1)
    return name, src


def build(name, todo, tb_src, timeout=600):
    """One variant: verilate, make, run, then convert the database twice."""
    d = os.path.join(WORK, name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "%s.v" % name), "w", encoding="utf-8", newline="\n").write(tb_src)

    m = sim.win_to_wsl(HERE)
    w = sim.win_to_wsl(d)
    for f in ("counter.v", "sync_fifo.v"):
        shutil.copy(os.path.join(HERE, "rtl", f), os.path.join(d, f))
    for f in ("sim_main.cpp", "cfg.h.in"):
        shutil.copy(os.path.join(ROOT, "bench", "rtl", f), os.path.join(d, f))
    open(os.path.join(d, "cfg.h"), "w", encoding="utf-8", newline="\n").write(
        '#define VTOP_HEADER "V%s.h"\n#define VTOP V%s\n#define COVERAGE_FILE "cov.dat"\n'
        % (name, name))

    script = """cd %s || exit 7
rm -rf obj
verilator %s --top-module %s %s.v counter.v sync_fifo.v sim_main.cpp \
    -Mdir obj -o sim --exe > build.log 2>&1
make -C obj -f V%s.mk >> build.log 2>&1
(cd obj && ./sim > ../run.log 2>&1)
verilator_coverage --write dump.txt obj/cov.dat >> build.log 2>&1
verilator_coverage --write-info info.info obj/cov.dat >> build.log 2>&1
grep -c . dump.txt
""" % (w, VFLAGS, name, name, name)
    out, rc = sim.wsl_bash(script, timeout=timeout)
    return {"rc": rc, "out": out, "dir": d,
            "dump": os.path.join(d, "dump.txt"), "info": os.path.join(d, "info.info"),
            "log": os.path.join(d, "run.log")}


def report_stats(dump_path, info_path):
    """The two numbers this whole experiment turns on.

    `raw` is how many toggle bins the simulator actually recorded; `written` is
    how many of them survive `verilator_coverage --write-info`. Measured: 98 and
    0. The conversion keeps line records only, so every toggle the design
    recorded -- including the one whose count is the evidence -- is dropped from
    the artefact a regression report is built from. Reading the raw database is
    not an optimisation here, it is the difference between having the data and
    not having it.
    """
    raw = written = 0
    if os.path.exists(dump_path):
        raw = len({(a.get("h", ""), a.get("o", ""))
                   for page, a, _ in boundary.parse_dump(dump_path)
                   if "toggle" in page and a.get("o")})
    if os.path.exists(info_path):
        written = sum(1 for line in open(info_path, encoding="utf-8", errors="ignore")
                      if line.startswith("BRDA:") or "toggle" in line.lower())
    return raw, written


def line_coverage(info_path):
    """The number a regression report would show."""
    if not os.path.exists(info_path):
        return None
    hits = total = 0
    for line in open(info_path, encoding="utf-8", errors="ignore"):
        if line.startswith("DA:"):
            total += 1
            hits += 1 if line.strip().split(",")[-1] != "0" else 0
    return (hits, total)


def main(argv=None):
    p = argparse.ArgumentParser(prog="run_seam.py", description=__doc__)
    p.add_argument("--replay", action="store_true",
                   help="read the checked-in fixtures instead of building")
    p.add_argument("--json", default=None)
    args = p.parse_args(argv)

    seams = json.load(open(os.path.join(HERE, "seam.json"), encoding="utf-8"))["seams"]
    rows, failures = [], []

    for v in VARIANTS:
        name = "tb_seam_%s" % v["name"]
        if args.replay:
            dump = os.path.join(FIX, "dump_%s.txt" % v["name"])
            info = os.path.join(FIX, "info_%s.info" % v["name"])
            if not os.path.exists(dump):
                failures.append("%s: no fixture at %s" % (v["name"], dump))
                continue
        else:
            _, tb_src = render(v)
            b = build(name, v, tb_src)
            dump, info = b["dump"], b["info"]
            if b["rc"] != 0 or not os.path.exists(dump):
                failures.append("%s: build/run failed (rc=%s)\n%s"
                                % (v["name"], b["rc"], b["out"][-600:]))
                continue

        r = boundary.seam_report(seams, dump)[0]
        lc = line_coverage(info)
        raw, written = report_stats(dump, info)
        ok = (r["verdict"] == v["expect"])
        if not ok:
            failures.append("%s: expected %s, got %s -- %s"
                            % (v["name"], v["expect"], r["verdict"], r["why"]))
        rows.append({"variant": v["name"], "expect": v["expect"],
                     "verdict": r["verdict"], "ok": ok, "why": v["why"],
                     "net": r["driven"], "expected_toggles": r["expected"],
                     "sink": r["arrived"], "toggle_bins_raw": raw,
                     "toggle_bins_in_report": written,
                     "lines": "%d/%d" % lc if lc else "-"})

    print("seam: %s" % ", ".join("%s -> %s" % (s["net"], s["sink"]) for s in seams))
    print("")
    print("%-8s %-15s %-15s %5s %8s %5s  %-9s %s"
          % ("variant", "expected", "verdict", "net", "expected", "sink",
             "line cov", "toggles raw/report"))
    print("-" * 88)
    for r in rows:
        print("%-8s %-15s %-15s %5d %8s %5d  %-9s %d / %d"
              % (r["variant"], r["expect"], r["verdict"], r["net"],
                 r["expected_toggles"], r["sink"], r["lines"],
                 r["toggle_bins_raw"], r["toggle_bins_in_report"]))
    print("")
    for r in rows:
        print("%-8s %s" % (r["variant"], r["why"]))

    seen = sorted(r["lines"] for r in rows if r["lines"] != "-")
    if seen:
        print("")
        print("note: line coverage spans %s across all five assemblies -- the "
              "broken one is one line off the correct one, which is not a signal "
              "anyone reads. The verdict column is not a threshold; it is what the "
              "toggle counts say about which signal reached the FIFO's write port."
              % ("%s .. %s" % (seen[0], seen[-1]) if seen[0] != seen[-1] else seen[0]))
    dropped = [r for r in rows if r["toggle_bins_raw"] and not r["toggle_bins_in_report"]]
    if dropped:
        print("note: every variant recorded %d toggle bins and the written report "
              "carries %d of them -- the data the check reads is thrown away by "
              "`--write-info`, so a report-only flow cannot run this check at all."
              % (dropped[0]["toggle_bins_raw"], dropped[0]["toggle_bins_in_report"]))

    if args.json:
        json.dump({"seams": seams, "rows": rows, "failures": failures},
                  open(args.json, "w", encoding="utf-8"), indent=2)

    print("")
    if failures:
        print("FAIL (%d)" % len(failures))
        for f in failures:
            print("  " + f)
        return 2
    print("PASS -- %d/%d variants matched the expected verdict" % (len(rows), len(VARIANTS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
