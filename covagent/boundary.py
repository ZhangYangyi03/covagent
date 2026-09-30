"""The coverage blind spot: the number can be identical while the wiring is wrong.

This is the covagent counterpart to assertforge's seam experiment, and it exists
because "coverage was high" is a claim about a report, not about a machine.

Two assemblies are built from the same two block files over a testbench that
differs in ONE assign:

    seam_ok        seam_en = en;       the producer's enable reaches the FIFO
    seam_broken    seam_en = rd_en;    it does not -- the read enable drives the write

Both blocks are byte-identical. Both simulate to completion. Then:

    v_toggle/.../seam_en         2 toggles  ->  1
    v_toggle/sync_fifo/wr_en     2 toggles  ->  1

The seam net itself is instrumented, so the wiring difference IS in the database.
It is not in the report: `verilator_coverage --write-info` emits line records only
and drops every toggle bin, and line coverage is 40/47 on both.

So the check this module implements is not "is coverage high". It is: **for every
seam net, did activity arrive on the far side of the boundary?** A net that
toggles while the port it drives does not is a connection that exists in the
netlist and not in the behaviour.

    python bench/run_seam.py

What this is not: toggle counts are not a proof of connection -- a short
simulation can under-count a correct wire, which is why a seam net that is dark
on BOTH sides is reported as UNEXERCISED rather than as a fault. The assertion
here is asymmetric on purpose: activity on the driver with none on the sink is
evidence of a fault; quiet on both is evidence of a weak test.
"""

import os
import re

# The coverage database verilator writes is a text format with \x01-separated
# key/value pairs inside each record. `verilator_coverage --write-info` converts
# it to lcov and keeps only the line records, which is the trap this module is
# built around: the raw file has 98 toggle bins for a two-block assembly and the
# converted file has none.
REC_RE = re.compile(r"^C '(.*)' (\d+)$")


def parse_dump(path):
    """Read a raw coverage database into (page, attribute-dict, count) rows."""
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding="utf-8", errors="replace"):
        m = REC_RE.match(line.strip())
        if not m:
            continue
        attrs = {}
        for piece in m.group(1).split("\x01"):
            if not piece:
                continue
            k, _, v = piece.partition("\x02")
            attrs[k] = v
        rows.append((attrs.get("page", ""), attrs, int(m.group(2))))
    return rows


def toggles(path):
    """Every toggle bin: {(scope, object): count}.

    The scope is kept, because it is what makes this a boundary check rather than
    a net-level one: `wr_en` exists twice in a two-block assembly, once as the
    testbench's net and once as the FIFO's port, and the two counts are the two
    sides of the connection.
    """
    out = {}
    for page, attrs, n in parse_dump(path):
        if "toggle" not in page:
            continue
        obj = attrs.get("o", "")
        if not obj:
            continue
        out[(attrs.get("h", ""), obj)] = n
        out.setdefault(("", obj), 0)      # net-wide view, filled below
        out[("", obj)] += n
    return out


def net_counts(tog):
    """Object name -> total toggles across every scope it appears in."""
    out = {}
    for (scope, obj), n in tog.items():
        if scope == "":
            continue
        out[obj] = out.get(obj, 0) + n
    return out


def seam_report(boundaries, dump_path):
    """For each declared seam, did the right activity cross it?

    `boundaries` is a list of {"net": ..., "driver": ..., "sink": ...,
    "sink_module": ...}. `driver` is the net the seam is *supposed* to be wired
    from, and it is what makes the check sharp.

    Landing the sink on its own is not enough, and the measured broken assembly
    is why: when `seam_en = rd_en` instead of `seam_en = en`, the seam net still
    toggles and the FIFO's write enable still toggles -- both simply inherit the
    read enable. A pure "did anything arrive" test calls that a crossing. What
    moved is the *source*, so the count is compared against the intended driver:
    2 toggles expected, 1 observed, BOUND_MISMATCH.

    Widths are not expanded here -- a bit-level report would need per-bit names,
    and the net-level total is enough to say whether the right thing arrived.
    """
    tog = toggles(dump_path)
    # A converted report is not a database, and the failure is silent: the lcov
    # form has no toggle bins at all, so every seam would come back UNEXERCISED --
    # a quiet "your test is weak" in answer to a question about wiring. Refuse
    # instead. Measured: 98 toggle bins in the raw database, 0 in the report.
    if not any(scope for scope, _ in tog):
        raise ValueError(
            "no toggle bins in %s -- this looks like a converted lcov report "
            "rather than verilator's raw database. `verilator_coverage "
            "--write-info` keeps line records only, so a boundary check cannot be "
            "run on its output." % dump_path)
    per_net = net_counts(tog)
    by_scope = {}
    for (scope, obj), n in tog.items():
        if scope:
            by_scope.setdefault(scope, {})[obj] = n
    out = []
    for b in boundaries:
        net = b["net"]
        sink = b.get("sink", net)
        mod = b.get("sink_module")
        src = b.get("driver")
        inside = {}
        for (scope, obj), n in tog.items():
            if scope == "" or not mod or not scope.endswith(mod):
                continue
            if obj == sink or obj.startswith(sink + "["):
                inside[obj] = n
        sunk = sum(inside.values())
        driven = per_net.get(net, 0)
        # The driver is read in the seam's OWN scope, not summed across the
        # hierarchy. Measured: a port named `en` exists on both the testbench and
        # inside each instance it feeds, so a net-wide total for `en` is 4 where
        # the value that matters is 2 -- the sum invents a mismatch on a correct
        # design, which is the failure mode worse than no check at all.
        src_n = None
        if src:
            scopes = [s for s, objs in by_scope.items() if net in objs]
            scopes.sort(key=lambda s: s.count("."))
            src_n = (by_scope[scopes[0]].get(src) if scopes
                     else None) or per_net.get(src, None)

        if driven == 0 and sunk == 0:
            verdict, why = "UNEXERCISED", ("nothing toggled on either side; the "
                                           "test never drove this seam")
        elif src_n is not None and src != net and driven not in (0, src_n):
            verdict, why = "BOUND_MISMATCH", (
                "the seam toggled %d times where the net it is declared to come "
                "from toggled %d: the activity arriving at the sink is some other "
                "signal's, so the wire is bound to the wrong driver"
                % (driven, src_n))
        elif sunk == 0:
            verdict, why = "NOT_ARRIVED", (
                "the net toggled %d times and the port it feeds did not toggle at "
                "all: the connection exists in the netlist and not in the "
                "behaviour" % driven)
        elif driven == 0:
            verdict, why = "SINK_ONLY", (
                "the port toggled %d times with no activity on the net declared to "
                "drive it; the driver named in the report is not the one in the "
                "design" % sunk)
        else:
            verdict, why = "CROSSED", ("%d toggles on the net, %d at the sink%s"
                                       % (driven, sunk,
                                          ", matching %s" % src if src_n == driven else ""))
        out.append({"net": net, "sink": sink, "module": mod, "driver": src,
                    "driven": driven, "expected": src_n, "arrived": sunk,
                    "verdict": verdict, "why": why, "per_bit": inside})
    return out


def summarize(report, dump_path=None, info_path=None):
    lines = []
    if dump_path:
        tog = toggles(dump_path)
        n = len({(s, o) for (s, o) in tog if s})
        lines.append("toggle bins in the raw database : %d" % n)
    if info_path and os.path.exists(info_path):
        txt = open(info_path, encoding="utf-8", errors="ignore").read()
        lines.append("toggle bins in the written report: %d   (--write-info keeps "
                     "line records only)" % txt.count("toggle"))
        hit = tot = 0
        for m in re.finditer(r"^DA:(\d+),(\d+)", txt, re.M):
            tot += 1
            hit += 1 if int(m.group(2)) else 0
        lines.append("line coverage                   : %d/%d" % (hit, tot))
    lines.append("")
    lines.append("%-16s %-15s %8s %8s %8s  %s"
                 % ("seam net", "verdict", "net", "expect", "sink", ""))
    for r in report:
        exp = "-" if r.get("expected") is None else str(r["expected"])
        lines.append("%-16s %-15s %8d %8s %8d  %s"
                     % (r["net"], r["verdict"], r["driven"], exp, r["arrived"],
                        r.get("driver") or ""))
        lines.append("%-16s %s" % ("", r["why"]))
    return "\n".join(lines)
