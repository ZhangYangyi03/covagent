"""Build every vector variant, run coverage, and check the per-bit verdicts.

    python bench/seam/vec/run_vec.py             build and measure (needs verilator)
    python bench/seam/vec/run_vec.py --replay    re-read the checked-in dumps

The scalar experiment next door (../) shows that a wire bound to the wrong driver
leaves the line-coverage report untouched. This one shows something stronger: a
REORDERED bus leaves every total untouched. Measured, 8-bit bus driven 0..255:

    variant   wire                    line cover   toggles   bits lit   verdict
    ok        src                     29/35        510       8          CROSSED
    cond      en ? src : seam_data    29/35        510       8          CROSSED
    reorder   {src[0], src[7:1]}      30/35        510       8          BIT_MISMATCH/ORDER
    swapped   {src[6:0], src[7]}      29/35        510       8          BIT_MISMATCH/ORDER
    shifted   {1'b0, src[7:1]}        27/35        510       7          BIT_MISMATCH/SUPPORT
    partial   {src[7:4], 4'b0}        29/35        510       4          BIT_MISMATCH/SUPPORT
    dead      8'd0                    27/35        510       0          NOT_ARRIVED

Read the reorder row twice. It covers MORE lines than the correct assembly and is
wired wrong. The only column that separates it from `ok` is the per-bit profile,
which is why the check compares profiles bit by bit rather than in total.
"""

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, ROOT)

from covagent import boundary, sim                          # noqa: E402
from bench.seam.vec.variants import VEC_LINE, VARIANTS       # noqa: E402

WORK = os.path.join(HERE, "_work")
FIX = os.path.join(ROOT, "bench", "fixtures", "seam", "vec")
VFLAGS = ("--cc --timing --coverage --coverage-line --coverage-toggle -Wno-fatal "
          "-Wno-INITIALDLY")


def line_coverage(info_path):
    if not os.path.exists(info_path):
        return None
    hits = total = 0
    for line in open(info_path, encoding="utf-8", errors="ignore"):
        if line.startswith("DA:"):
            total += 1
            hits += 1 if line.strip().split(",")[-1] != "0" else 0
    return (hits, total)


def build(name, tb_src, timeout=600):
    d = os.path.join(WORK, name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "%s.v" % name), "w", encoding="utf-8", newline="\n").write(tb_src)
    for f in ("sync_fifo.v",):
        shutil.copy(os.path.join(HERE, "rtl", f), os.path.join(d, f))
    for f in ("sim_main.cpp", "cfg.h.in"):
        shutil.copy(os.path.join(ROOT, "bench", "rtl", f), os.path.join(d, f))
    open(os.path.join(d, "cfg.h"), "w", encoding="utf-8", newline="\n").write(
        '#define VTOP_HEADER "V%s.h"\n#define VTOP V%s\n#define COVERAGE_FILE "cov.dat"\n'
        % (name, name))
    w = sim.win_to_wsl(d)
    script = """cd %s || exit 7
rm -rf obj
verilator %s --top-module %s %s.v sync_fifo.v sim_main.cpp -Mdir obj -o sim --exe \\
    > build.log 2>&1
make -C obj -f V%s.mk >> build.log 2>&1
(cd obj && ./sim > ../run.log 2>&1)
verilator_coverage --write dump.txt obj/cov.dat >> build.log 2>&1
verilator_coverage --write-info info.info obj/cov.dat >> build.log 2>&1
""" % (w, VFLAGS, name, name, name)
    out, rc = sim.wsl_bash(script, timeout=timeout)
    return rc, out, os.path.join(d, "dump.txt"), os.path.join(d, "info.info")


def main(argv=None):
    p = argparse.ArgumentParser(prog="run_vec.py", description=__doc__)
    p.add_argument("--replay", action="store_true")
    p.add_argument("--json", default=None)
    args = p.parse_args(argv)

    seams = json.load(open(os.path.join(HERE, "vec.json"), encoding="utf-8"))["seams"]
    src = open(os.path.join(HERE, "tb_vec.v"), encoding="utf-8").read()
    if VEC_LINE not in src:
        raise SystemExit("tb_vec.v no longer contains the seam line")

    rows, failures = [], []
    for v in VARIANTS:
        name = "tb_vec_%s" % v["name"]
        if args.replay:
            dump = os.path.join(FIX, "dump_%s.txt" % v["name"])
            info = os.path.join(FIX, "info_%s.info" % v["name"])
            if not os.path.exists(dump):
                failures.append("%s: no fixture" % v["name"]); continue
        else:
            body = src.replace(VEC_LINE, v["line"]).replace(
                "module tb_vec;", "module %s;" % name, 1)
            rc, out, dump, info = build(name, body)
            if rc != 0 or not os.path.exists(dump):
                failures.append("%s: build/run failed (rc=%s)\n%s"
                                % (v["name"], rc, out[-500:])); continue

        r = boundary.seam_report(seams, dump)[0]
        lc = line_coverage(info)
        ok = r["verdict"] == v["expect"] and r.get("kind", "") == v["kind"]
        if not ok:
            failures.append("%s: expected %s/%s, got %s/%s"
                            % (v["name"], v["expect"], v["kind"] or "-",
                               r["verdict"], r.get("kind") or "-"))
        rows.append({"variant": v["name"], "expect": v["expect"], "kind": v["kind"],
                     "verdict": r["verdict"], "got_kind": r.get("kind", ""),
                     "ok": ok, "toggles": r["driven"], "sink": r["arrived"],
                     "bits_lit": len([k for k, v in r.get("sink_profile", {}).items()
                                      if v > 0]),
                     "lines": "%d/%d" % lc if lc else "-", "why": v["why"]})

    print("bus: %s[%s] -> %s.%s" % (seams[0]["net"], seams[0].get("width", "?"),
                                    seams[0]["sink_module"], seams[0]["sink"]))
    print("")
    print("%-8s %-14s %-8s %-8s %5s %5s  %s"
          % ("variant", "expected", "verdict", "kind", "toggl", "bits", "line cov"))
    print("-" * 72)
    for r in rows:
        print("%-8s %-14s %-8s %-8s %5d %5d  %s"
              % (r["variant"], r["expect"], r["verdict"], r["got_kind"] or "-",
                 r["toggles"], r["bits_lit"], r["lines"]))
    print("")
    for r in rows:
        print("%-8s %s" % (r["variant"], r["why"]))

    wrong = next((r for r in rows if r["verdict"] == "BIT_MISMATCH"), None)
    ctrl = next((r for r in rows if r["variant"] == "ok"), None)
    if wrong and ctrl and wrong["lines"] != "-":
        rel = ("more" if int(wrong["lines"].split("/")[0]) > int(ctrl["lines"].split("/")[0])
               else "no")
        print("")
        print("note: the reordered assembly covers %s lines, the correct one %s -- %s "
              "and it is wired wrong. Toggles %d vs %d, bits lit %d vs %d. Every "
              "total the report can produce agrees; only the per-bit profile does not."
              % (wrong["lines"], ctrl["lines"], rel, wrong["toggles"], ctrl["toggles"],
                 wrong["bits_lit"], ctrl["bits_lit"]))

    if args.json:
        json.dump({"seams": seams, "rows": rows, "failures": failures},
                  open(args.json, "w", encoding="utf-8"), indent=2)
    print("")
    if failures:
        print("FAIL (%d)" % len(failures))
        for f in failures:
            print("  " + f)
        return 2
    print("PASS -- %d/%d vector variants matched" % (len(rows), len(VARIANTS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
