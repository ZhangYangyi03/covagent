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
