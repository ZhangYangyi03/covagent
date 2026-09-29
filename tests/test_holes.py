"""The hole analyser is the actionable half; test it against a real report."""

import os
import re

from covagent import coverage, holes, sim

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(os.path.dirname(HERE), "bench", "rtl")

# A frozen report from the LAZY testbench. Reading bench/rtl/coverage.info would
# make these tests depend on whatever the last run happened to leave behind --
# which is exactly what happened the first time: the closure loop had already
# fixed the holes and the tests that look for holes started failing.
FIXTURE = os.path.join(os.path.dirname(HERE), "bench", "fixtures",
                       "lazy_coverage.info")


def _report():
    return sim.parse_info(FIXTURE)


def test_real_report_parses_into_bins():
    rep = _report()
    assert rep.bins, "coverage.info produced no bins"
    assert 0.0 <= rep.closure() <= 1.0


def test_case_selector_is_recovered_from_the_rtl():
    # the suggestion must name the signal, not just the line
    rep = _report()
    hs = holes.find_holes(rep, os.path.join(BENCH, "alu.v"))
    case_holes = [h for h in hs if "op with" in h.suggestion]
    assert case_holes, "no case-item suggestion was produced"
    assert all("op" in h.suggestion for h in case_holes)


def test_port_declarations_are_not_reported_as_holes():
    rep = _report()
    hs = holes.find_holes(rep, os.path.join(BENCH, "alu.v"))
    for h in hs:
        assert not re.match(r"^\s*(input|output|inout)\b", h.text)


def test_hole_describe_is_readable():
    rep = _report()
    hs = holes.find_holes(rep, os.path.join(BENCH, "alu.v"))
    assert hs
    text = hs[0].describe()
    assert "why:" in text and "try:" in text


def test_default_arm_is_recognised_as_its_own_kind_of_hole():
    rep = _report()
    hs = holes.find_holes(rep, os.path.join(BENCH, "alu.v"), max_holes=50)
    defaults = [h for h in hs if "default arm" in h.reason]
    assert defaults, "the default arm hole was not classified"


def test_missing_rtl_is_not_a_crash():
    rep = _report()
    assert holes.find_holes(rep, os.path.join(BENCH, "does_not_exist.v")) == []
