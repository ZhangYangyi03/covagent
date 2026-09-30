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

A vector seam needs a different measure, and bench/seam/vec/ is the experiment
that shows why. Drive an 8-bit bus 0..255 and every bit toggles a DIFFERENT
number of times (256, 128, ... 2). Then:

    ok         seam_data = src                    510 toggles, 8 bits lit
    reorder    {src[0], src[7:1]}                 510 toggles, 8 bits lit
    swapped    {src[6:0], src[7]}                 510 toggles, 8 bits lit

A total is identical. The number of bits that toggled is identical. Every bit is
lit in all three. What moved is the PER-BIT PROFILE -- which bit has which count
-- and that is the only place the rotation is visible. So for a multi-bit seam the
comparison is per bit, and the verdict says which kind of difference it found
(order, support, or driver), because they send someone to different places.
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


def bits_of(tog, scope, name):
    """{bit index or None: count} for one signal in one scope.

    verilator records a multi-bit net as one bin per bit (`wr_data[3]`), so this
    is where a per-bit profile comes from. A whole-vector bin, if a tool emits
    one, is kept under None rather than mixed into the bit indices.
    """
    out = {}
    for (s, o), n in tog.items():
        if s != scope:
            continue
        if o == name:
            out[None] = out.get(None, 0) + n
        elif o.startswith(name + "[") and o.endswith("]"):
            key = o[len(name) + 1:-1]
            try:
                key = int(key)
            except ValueError:
                pass
            out[key] = out.get(key, 0) + n
    return out


def profile_of(tog, name, under=None):
    """The per-bit profile of `name`, read in the scope that owns the seam.

    `under` restricts the search to an instance (a scope ending in that name),
    which is how the sink side is read. Without it, the shallowest scope holding
    the name wins -- the same rule the scalar path uses for the driver, and for
    the same reason: a port named `en` or `wr_data` exists on the testbench too,
    and summing across the hierarchy invents a difference on a correct design.
    """
    scopes = [s for (s, o) in tog if s and (o == name or o.startswith(name + "["))]
    if under:
        scopes = [s for s in scopes if s.endswith(under)]
    scopes.sort(key=lambda s: (s.count("."), len(s)))
    if not scopes:
        return {}
    return bits_of(tog, scopes[0], name)


def vector_seam(tog, b):
    """One multi-bit seam: compare the per-bit profile across the boundary.

    Counting is not enough here, and the measured assemblies are why:

        ok         seam_data = src            510 toggles, 8 bits lit
        reorder    {src[0], src[7:1]}         510 toggles, 8 bits lit
        swapped    {src[6:0], src[7]}         510 toggles, 8 bits lit

    Identical totals, identical support, every bit lit on both sides -- and the
    bus is wired wrong. Only the per-bit profile moves, so that is what is
    compared, and the verdict names the KIND of difference because the three kinds
    send someone to different places:

        ORDER    the same bits are lit on both sides carrying each other's counts
                 -- the bit order is not preserved
        SUPPORT  bits toggle at the driver and never arrive -- the wiring does not
                 carry them
        DRIVER   the port sees bits the driver never moves -- what arrives belongs
                 to something else

    The driver is read in the scope that holds the net, not by searching for the
    driver's name: when a manifest names the sink as its own driver, a name search
    lands in the sink's scope and the comparison is the sink against itself.
    Measured, and it reported every correct assembly as SINK_ONLY.
    """
    net = b["net"]
    src = b.get("driver") or net
    sink = b.get("sink", net)
    mod = b["sink_module"]

    net_scopes = [s for (s, o) in tog
                  if s and (o == net or o.startswith(net + "["))]
    net_scopes.sort(key=lambda s: (s.count("."), len(s)))
    d_prof = bits_of(tog, net_scopes[0], src) if net_scopes else {}
    if not any(v for k, v in d_prof.items() if k is not None):
        d_prof = bits_of(tog, net_scopes[0], net) if net_scopes else {}
    s_prof = profile_of(tog, sink, under=mod)

    drv_l = {k: v for k, v in d_prof.items() if k is not None and v > 0}
    snk_l = {k: v for k, v in s_prof.items() if k is not None and v > 0}
    d_total = sum(v for k, v in d_prof.items() if k is not None)
    s_total = sum(v for k, v in s_prof.items() if k is not None)
    added = sorted(set(snk_l) - set(drv_l))
    lost = sorted(set(drv_l) - set(snk_l))
    kind = ""

    if not drv_l and not snk_l:
        verdict = "UNEXERCISED"
        why = "no bit of either side toggled; the test never drove this bus"
    elif not snk_l:
        verdict = "NOT_ARRIVED"
        why = ("%d bits of the driver toggled and no bit of the port it feeds did: "
               "the connection exists in the netlist and not in the behaviour"
               % len(drv_l))
    elif not drv_l:
        verdict = "SINK_ONLY"
        why = ("%d bits of the port toggled with no activity on the bus declared "
               "to drive it; the driver named is not the one in the design"
               % len(snk_l))
    elif drv_l == snk_l:
        verdict = "CROSSED"
        why = ("all %d lit bits carry the driver's own counts" % len(drv_l))
    else:
        verdict = "BIT_MISMATCH"
        if not added and not lost:
            kind = "ORDER"
            why = ("the same %d bits are lit on both sides -- the bus is active "
                   "and nothing is missing, but the counts have moved between bit "
                   "positions, so the bit ORDER is not preserved across the "
                   "boundary" % len(drv_l))
        elif lost and not added:
            kind = "SUPPORT"
            why = ("bits %s toggle at the driver and never at the port; the wiring "
                   "does not carry them" % ",".join(str(x) for x in lost))
        elif added and not lost:
            kind = "DRIVER"
            why = ("the port toggles bits %s that the bus it is declared to come "
                   "from never moves; what arrives belongs to another signal"
                   % ",".join(str(x) for x in added))
        else:
            kind = "DRIVER"
            why = ("both sides moved and the counts disagree: bits %s lost and %s "
                   "appeared, so the port is not being driven by the net named"
                   % (",".join(str(x) for x in lost) or "none",
                      ",".join(str(x) for x in added) or "none"))
        why += " -- driver %s vs sink %s" % (profile_str(d_prof), profile_str(s_prof))

    return {"net": net, "sink": sink, "module": mod, "driver": b.get("driver"),
            "width": b.get("width"), "kind": kind, "verdict": verdict,
            "driven": d_total, "arrived": s_total, "expected": d_total,
            "driver_profile": {k: d_prof.get(k, 0) for k in sorted(drv_l)},
            "sink_profile": {k: s_prof.get(k, 0) for k in sorted(snk_l)},
            "per_bit": {"%s[%s]" % (sink, k): v for k, v in sorted(s_prof.items())
                        if k is not None},
            "why": why}


def profile_str(prof):
    """A per-bit profile as `bit:count`, low bit first."""
    return "{" + ", ".join("%s:%s" % (k, prof.get(k, 0))
                           for k in sorted(x for x in prof if x is not None)) + "}"


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

    Scalar seams are compared on counts; vector seams are compared PER BIT, and
    the vector check exists because a rotation is invisible to everything else:
    510 toggles on the net, 510 at the sink, all 8 bits lit on both sides, and the
    bus is wired wrong (bench/seam/vec/ measures exactly that).
    """
    tog = toggles(dump_path)
    # A converted report is not a database, and the failure is silent: the lcov
    # form has no toggle bins at all, so every seam would come back UNEXERCISED --
    # a quiet "your test is weak" in answer to a question about wiring. Refuse
    # instead. Measured: 98 toggle bins in the raw database, 0 in the report.
    for b in boundaries:
        net = b.get("net")
        if net and not b.get("width"):
            # A bus has no whole-vector bin, only per-bit ones. A manifest that
            # omits the width compares a name that does not exist and reports the
            # seam UNEXERCISED -- which reads as "your test is weak" when the
            # truth is "the manifest is incomplete". Name it instead.
            lit = [o for (s, o) in tog if s and o.startswith(str(net) + "[")]
            if lit and not any(o == net for (s, o) in tog if s):
                raise ValueError(
                    "seam net %r appears only as per-bit bins (%d of them), so it "
                    "is a bus: give the manifest a `width`. Without it the seam is "
                    "compared as a scalar name and reports UNEXERCISED for a test "
                    "that did exercise it." % (net, len(lit)))
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

        if b.get("width") and mod:
            out.append(vector_seam(tog, b))
            continue

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
    lines.append("%-16s %-14s %-8s %7s %7s %6s  %s"
                 % ("seam net", "verdict", "kind", "driver", "expect", "sink", ""))
    for r in report:
        exp = "-" if r.get("expected") is None else str(r["expected"])
        lines.append("%-16s %-14s %-8s %7d %7s %6d  %s"
                     % (r["net"], r["verdict"], r.get("kind") or "-", r["driven"],
                        exp, r["arrived"], r.get("driver") or ""))
        lines.append("%-16s %s" % ("", r["why"]))
        if r.get("driver_profile"):
            lines.append("%-16s driver %s" % ("", profile_str(r["driver_profile"])))
            lines.append("%-16s sink   %s" % ("", profile_str(r["sink_profile"])))
    return "\n".join(lines)
