"""The boundary check, tested against the real dumps and against fabricated ones.

The fixtures under bench/fixtures/seam are verilator's own output for the five
assemblies in bench/seam/variants.py, so these tests need no simulator. What they
pin is not just "the check runs" -- it is the three ways it could be wrong and
still look right:

  1. It could call a correct wire broken.  (ok / clip / cond must be CROSSED)
  2. It could call a broken wire fine.     (broken must not be CROSSED)
  3. It could read a signal from the wrong scope, or read a converted report as
     an empty one and answer a wiring question with a test-quality answer.
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from covagent import boundary                                    # noqa: E402
from bench.seam.variants import VARIANTS                         # noqa: E402

FIX = os.path.join(ROOT, "bench", "fixtures", "seam")
FIXV = os.path.join(FIX, "vec")
SEAM_DIR = os.path.join(ROOT, "bench", "seam")


def seams():
    return json.load(open(os.path.join(SEAM_DIR, "seam.json"), encoding="utf-8"))["seams"]


def fixture(name):
    return os.path.join(FIX, "dump_%s.txt" % name)


EXPECTED = {v["name"]: v["expect"] for v in VARIANTS}


def test_dump_parses():
    rows = boundary.parse_dump(fixture("ok"))
    assert len(rows) > 50
    pages = {p for p, _, _ in rows}
    assert any("toggle" in p for p in pages), "fixture has no toggle bins"
    assert any(p.startswith("v_line") or p.startswith("v_branch") for p in pages)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_verdict_matches_the_declared_expectation(name):
    r = boundary.seam_report(seams(), fixture(name))[0]
    assert r["verdict"] == EXPECTED[name], r["why"]


def test_correct_wire_toggles_on_both_sides():
    """Case 1: the control. A check that flags this is worse than no check."""
    r = boundary.seam_report(seams(), fixture("ok"))[0]
    assert r["verdict"] == "CROSSED"
    assert r["driven"] == r["expected"] == r["arrived"]


def test_equivalent_rewrite_is_not_flagged():
    """`en & en` is the same wire. Keying on the expression would break here."""
    assert boundary.seam_report(seams(), fixture("clip"))[0]["verdict"] == "CROSSED"


def test_backpressure_is_not_flagged():
    """`en & ~full` is correct, conditioned. Conditioned is not broken."""
    assert boundary.seam_report(seams(), fixture("cond"))[0]["verdict"] == "CROSSED"


def test_broken_wire_is_caught_and_not_as_a_crossing():
    """Case 2: the defect. Both sides still toggle -- by inheriting rd_en."""
    r = boundary.seam_report(seams(), fixture("broken"))[0]
    assert r["verdict"] != "CROSSED"
    assert r["verdict"] == "BOUND_MISMATCH"
    assert r["driven"] != r["expected"], "the net count should not match its driver's"
    assert r["arrived"] > 0, "the sink does toggle; that is why counting is not enough"


def test_dead_wire_is_reported_as_a_weak_test_not_a_fault():
    """Quiet on both sides is not the same finding as a wire that never arrives."""
    r = boundary.seam_report(seams(), fixture("dead"))[0]
    assert r["verdict"] == "UNEXERCISED"
    assert r["driven"] == 0 and r["arrived"] == 0


def test_converted_report_is_refused_rather_than_answered():
    """Case 3a: --write-info drops every toggle bin. Answering anyway would take a
    wiring question and return a test-quality verdict."""
    info = os.path.join(FIX, "info_ok.info")
    if not os.path.exists(info):
        pytest.skip("no fixture")
    txt = open(info, encoding="utf-8", errors="ignore").read()
    assert "toggle" not in txt.lower()
    with pytest.raises(ValueError) as e:
        boundary.seam_report(seams(), info)
    assert "raw database" in str(e.value)


def test_driver_is_read_in_the_seam_scope(tmp_path):
    """Case 3b: a port named `en` exists on the testbench AND inside every instance
    it feeds. Summing across the hierarchy invents a mismatch on a correct design;
    that bug was written, measured, and is pinned here."""
    header = "# SystemC::Coverage-3\n"
    def rec(scope, obj, n):
        return ("C '\x01f\x01tb.v\x01l\x011\x01n\x011\x01page\x02v_toggle/top"
                "\x01o\x02%s\x01h\x02%s' %d\n" % (obj, scope, n))
    body = (header
            + rec("TOP", "seam_en", 2)      # the net under test
            + rec("TOP", "en", 2)           # its driver, in the seam's own scope
            + rec("TOP.u_prod", "en", 7)    # a different port that shares the name
            + rec("TOP.u_fifo", "wr_en", 2))
    p = tmp_path / "dump.txt"
    p.write_text(body, encoding="utf-8")
    r = boundary.seam_report(seams(), str(p))[0]
    assert r["verdict"] == "CROSSED", r["why"]
    assert r["expected"] == 2, "the driver's count was summed across scopes"


def test_line_coverage_cannot_separate_these_assemblies():
    """The premise of the whole addition, asserted rather than asserted-in-prose."""
    def lc(name):
        txt = open(os.path.join(FIX, "info_%s.info" % name), encoding="utf-8",
                   errors="ignore").read()
        tot = hit = 0
        for line in txt.splitlines():
            if line.startswith("DA:"):
                tot += 1
                hit += 1 if line.strip().split(",")[-1] != "0" else 0
        return hit, tot
    correct, broken = lc("ok"), lc("broken")
    assert correct[1] == broken[1]
    assert abs(correct[0] - broken[0]) <= 2, (correct, broken)

# --------------------------------------------------------------- vector ---- #
# The scalar tests above prove a wrong DRIVER is invisible to the report. These
# prove something stronger about a bus: a REORDERED one is invisible to every
# total the report can produce -- same line count, same toggle count, same number
# of lit bits -- and is still wrong.

VEC_EXPECTED = {"ok": ("CROSSED", ""), "cond": ("CROSSED", ""),
                "reorder": ("BIT_MISMATCH", "ORDER"),
                "swapped": ("BIT_MISMATCH", "ORDER"),
                "shifted": ("BIT_MISMATCH", "SUPPORT"),
                "partial": ("BIT_MISMATCH", "SUPPORT"),
                "dead": ("NOT_ARRIVED", "")}


def vec_seams():
    return json.load(open(os.path.join(ROOT, "bench", "seam", "vec", "vec.json"),
                          encoding="utf-8"))["seams"]


def vdump(name):
    return os.path.join(FIXV, "dump_%s.txt" % name)


@pytest.mark.parametrize("name", sorted(VEC_EXPECTED))
def test_vector_verdict_matches(name):
    want_v, want_k = VEC_EXPECTED[name]
    r = boundary.seam_report(vec_seams(), vdump(name))[0]
    assert (r["verdict"], r.get("kind", "")) == (want_v, want_k), r["why"]


def test_a_correct_bus_is_not_flagged():
    """Control. A check that fires on a correct 8-bit bus is not usable."""
    r = boundary.seam_report(vec_seams(), vdump("ok"))[0]
    assert r["verdict"] == "CROSSED"
    assert r["driver_profile"] == r["sink_profile"]


def test_a_conditioned_bus_is_not_flagged():
    """`en ? src : seam_data` is correct. Real RTL always has enables."""
    assert boundary.seam_report(vec_seams(), vdump("cond"))[0]["verdict"] == "CROSSED"


def test_reorder_is_visible_only_in_the_profile():
    """The finding, asserted on the data rather than described in prose.

    A reordered bus has the same toggle total, the same number of lit bits, and
    the same line coverage. If any of those three could separate it, the per-bit
    comparison would not be necessary; the assertion is that none of them can.
    """
    ok = boundary.seam_report(vec_seams(), vdump("ok"))[0]
    ro = boundary.seam_report(vec_seams(), vdump("reorder"))[0]

    assert ro["verdict"] == "BIT_MISMATCH" and ro["kind"] == "ORDER"
    assert ro["driven"] == ok["driven"], "the totals are supposed to be identical"
    assert (len([v for v in ro["sink_profile"].values() if v > 0])
            == len([v for v in ok["sink_profile"].values() if v > 0]))
    assert sorted(ro["sink_profile"].values()) == sorted(ok["sink_profile"].values()), \
        "the multiset of per-bit counts is the same; only their positions differ"
    assert ro["sink_profile"] != ok["sink_profile"], "and that is the whole signal"

    def lc(name):
        txt = open(os.path.join(FIXV, "info_%s.info" % name), encoding="utf-8",
                   errors="ignore").read()
        hit = sum(1 for l in txt.splitlines()
                  if l.startswith("DA:") and l.strip().split(",")[-1] != "0")
        return hit
    assert abs(lc("reorder") - lc("ok")) <= 1, "line coverage must not separate them"


def test_reorder_covers_more_lines_than_the_correct_assembly():
    """The unflattering half, kept because it is the argument.

    A number that goes UP while the wiring is wrong is why "coverage improved"
    is not evidence of a correct machine.
    """
    def lc(name):
        txt = open(os.path.join(FIXV, "info_%s.info" % name), encoding="utf-8",
                   errors="ignore").read()
        return sum(1 for l in txt.splitlines()
                   if l.startswith("DA:") and l.strip().split(",")[-1] != "0")
    assert lc("reorder") > lc("ok")


def test_bit_order_mismatch_names_both_profiles():
    """The report has to be actionable: which bit carries which count."""
    r = boundary.seam_report(vec_seams(), vdump("reorder"))[0]
    assert r["driver_profile"] and r["sink_profile"]
    assert "{" in r["why"] and "}" in r["why"], r["why"]


def test_partial_bus_is_a_support_problem_not_an_order_one():
    """Half the bus zeroed is bits missing, not bits moved -- different fix."""
    r = boundary.seam_report(vec_seams(), vdump("partial"))[0]
    assert r["kind"] == "SUPPORT"
    assert len(r["sink_profile"]) < len(r["driver_profile"])


def test_driver_profile_is_read_where_the_net_is(tmp_path):
    """A manifest that names the sink as its own driver must not compare the sink
    against itself. That bug reported every correct bus as SINK_ONLY (measured)."""
    import re as _re
    body = "# SystemC::Coverage-3\n"
    def rec(scope, obj, n):
        return ("C '\x01f\x01tb.v\x01l\x011\x01n\x011\x01page\x02v_toggle/top"
                "\x01o\x02%s\x01h\x02%s' %d\n" % (obj, scope, n))
    body += rec("TOP", "seam_data[0]", 4) + rec("TOP", "seam_data[1]", 2)
    body += rec("TOP", "src[0]", 4) + rec("TOP", "src[1]", 2)
    body += rec("TOP.u_fifo", "wr_data[0]", 4) + rec("TOP.u_fifo", "wr_data[1]", 2)
    p = tmp_path / "d.txt"
    p.write_text(body, encoding="utf-8")
    seam = [{"net": "seam_data", "driver": "src", "sink": "wr_data",
             "sink_module": "u_fifo", "width": 2}]
    assert boundary.seam_report(seam, str(p))[0]["verdict"] == "CROSSED"
    # and with the sink named as its own driver, the answer must not change
    seam2 = [dict(seam[0], driver="wr_data")]
    r2 = boundary.seam_report(seam2, str(p))[0]
    assert r2["verdict"] == "CROSSED", r2["why"]


def test_a_bus_without_a_declared_width_is_refused():
    """A bus has per-bit bins and no whole-vector bin. A manifest that omits the
    width compares a name that does not exist and reports UNEXERCISED for a test
    that did exercise the bus -- a manifest error wearing a test-quality verdict.
    """
    seam = [{"net": "seam_data", "driver": "src", "sink": "wr_data",
             "sink_module": "u_fifo"}]           # no "width"
    with pytest.raises(ValueError) as e:
        boundary.seam_report(seam, vdump("ok"))
    assert "width" in str(e.value)
