"""CLI. Thin on purpose; the claims live in the modules."""

import argparse
import json
import os
import sys

from . import boundary, close, holes, llm, sim


def cmd_doctor(args):
    ok, ver = sim.toolchain_check()
    print("covagent %s" % __import__("covagent").__version__)
    print("simulator   : %s" % ("yes -- " + ver if ok else "NO"))
    try:
        c = llm.Client(model=args.model) if args.model else llm.Client()
        r = c.chat("Reply with the single word OK.", max_tokens=12)
        print("llm endpoint: %s" % c.model)
        print("llm round   : %s (%.2fs)" % (r.strip()[:20], c.seconds))
    except Exception as e:
        print("llm endpoint: UNAVAILABLE -- %s" % e)
    return 0 if ok else 1


def cmd_holes(args):
    rep = sim.parse_info(os.path.join(args.dir, "coverage.info"))
    if not rep.bins:
        print("no coverage.info in %s -- run `make cov` first" % args.dir)
        return 1
    hs = holes.find_holes(rep, os.path.join(args.dir, args.rtl),
                          max_holes=args.max)
    print(holes.summarize(rep, hs))
    if args.json:
        json.dump({"closure": rep.closure(),
                   "holes": [h.to_dict() for h in hs]},
                  open(args.json, "w", encoding="utf-8"), indent=2)
        print("\nwrote %s" % args.json)
    return 0 if not hs else 2


def cmd_close(args):
    driver = args.driver
    cl = close.Closer(args.dir, args.rtl, args.tb, driver_file=driver,
                      client=llm.Client(model=args.model) if args.model else llm.Client(),
                      target=args.target, max_rounds=args.rounds,
                      wsl_timeout=args.timeout)
    res = cl.run()
    print("covagent %s" % __import__("covagent").__version__)
    print("status  : %s" % res.status)
    print("closure : %.1f%% -> %.1f%%" % (res.before * 100, res.after * 100))
    for r in res.rounds:
        line = "  round %s %s" % (r.get("round"), r.get("attempt"))
        if "closure" in r:
            line += "  %.1f%% -> %.1f%%" % (r["prev"] * 100, r["closure"] * 100)
        print(line)
        if r.get("reason"):
            print("          %s" % r["reason"])
    if args.json:
        json.dump(res.to_dict(), open(args.json, "w", encoding="utf-8"), indent=2)
        print("wrote %s" % args.json)
    return 0 if res.status in ("DONE", "TARGET", "IMPROVED") else 2


def cmd_boundary(args):
    """Did the right signal cross each declared seam?

    Reads the RAW database, not coverage.info: the conversion keeps line records
    only, so a report-driven version of this command would report every seam as
    UNEXERCISED.
    """
    if args.seam_json:
        seams = json.load(open(args.seam_json, encoding="utf-8"))["seams"]
    elif args.net:
        seams = [{"net": args.net, "driver": args.driver, "sink": args.sink,
                  "sink_module": args.sink_module}]
    else:
        print("give --seam-json, or --net with --driver/--sink/--sink-module")
        return 1
    try:
        report = boundary.seam_report(seams, args.dump)
    except ValueError as e:
        print("cannot check: %s" % e)
        return 1
    if args.info:
        print(boundary.summarize(report, args.dump, args.info if args.info else None))
    else:
        print(boundary.summarize(report, args.dump))
    bad = [r for r in report
           if r["verdict"] in ("BOUND_MISMATCH", "BIT_MISMATCH", "NOT_ARRIVED",
                               "SINK_ONLY")]
    if args.json:
        json.dump(report, open(args.json, "w", encoding="utf-8"), indent=2)
        print("wrote %s" % args.json)
    return 2 if bad else 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="covagent", description=__doc__)
    p.add_argument("--model", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor"); d.set_defaults(func=cmd_doctor)

    h = sub.add_parser("holes")
    h.add_argument("--dir", required=True)
    h.add_argument("--rtl", required=True)
    h.add_argument("--max", type=int, default=12)
    h.add_argument("--json", default=None)
    h.set_defaults(func=cmd_holes)

    c = sub.add_parser("close")
    c.add_argument("--dir", required=True)
    c.add_argument("--rtl", required=True)
    c.add_argument("--tb", required=True)
    c.add_argument("--driver", default=None)
    c.add_argument("--target", type=float, default=0.95)
    c.add_argument("--rounds", type=int, default=3)
    c.add_argument("--timeout", type=int, default=900)
    c.add_argument("--json", default=None)
    c.set_defaults(func=cmd_close)

    b = sub.add_parser("boundary", help="check the wiring between sub-modules")
    b.add_argument("--dump", required=True,
                   help="the RAW verilator coverage database (obj/cov.dat or a "
                        "--write dump), not coverage.info")
    b.add_argument("--seam-json", default=None, help="a seam manifest")
    b.add_argument("--net", default=None, help="one seam: the net under test")
    b.add_argument("--driver", default=None, help="the net it is declared to come from")
    b.add_argument("--sink", default=None, help="the port it is declared to feed")
    b.add_argument("--sink-module", default=None, help="the instance that owns the port")
    b.add_argument("--info", default=None, help="coverage.info, for the line-coverage line")
    b.add_argument("--json", default=None)
    b.set_defaults(func=cmd_boundary)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
