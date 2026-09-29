"""lcov parsing and the WSL path bridge."""

import os

from covagent import sim

HERE = os.path.dirname(os.path.abspath(__file__))
INFO = os.path.join(os.path.dirname(HERE), "bench", "fixtures", "lazy_coverage.info")


def test_lcov_da_lines_become_bins():
    rep = sim.parse_info(INFO)
    names = [b.name for b in rep.bins]
    assert any("alu.v:" in n for n in names)


def test_hits_are_preserved_not_flattened_to_booleans():
    rep = sim.parse_info(INFO)
    hits = [b.hits for b in rep.bins]
    assert max(hits) > 1, "hit counts were lost: every bin reads 0/1"


def test_zero_hit_bins_are_the_open_ones():
    rep = sim.parse_info(INFO)
    assert all(b.hits == 0 for b in rep.open_bins)


def test_unix_paths_are_left_alone():
    assert sim.win_to_wsl("/tmp/rtl") == "/tmp/rtl"


def test_win_path_maps_to_wsl_mount():
    import platform
    import pytest
    if platform.system() != "Windows":
        pytest.skip("the D:\\ -> /mnt/d/ translation is a Windows-only operation")
    assert sim.win_to_wsl("D:\\bench\\rtl") == "/mnt/d/bench/rtl"


def test_parsing_a_missing_file_is_empty_not_fatal():
    assert sim.parse_info("nope.info").bins == []
