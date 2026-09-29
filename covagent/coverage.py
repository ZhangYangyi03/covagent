"""Read a real coverage report and turn it into a list of open holes.

The claim worth making is narrow: given the report a simulator already produces,
which coverage bins are empty, and what would have to happen for each to fill.
Everything downstream (stimulus generation, regression, the CI gate) is ordinary
engineering; the hard part was never the tooling, it is deciding what to try
next. That decision is what this module computes.
"""

import os
import re
import subprocess


class Bin:
    """One coverage point: what it is, where it lives, and whether it is hit."""

    def __init__(self, kind, name, location="", hits=0, target=1, detail=""):
        self.kind = kind          # line | branch | toggle | fsm_state | fsm_trans | expr
        self.name = name
        self.location = location
        self.hits = hits
        self.target = target
        self.detail = detail

    @property
    def closed(self):
        return self.hits >= self.target

    def to_dict(self):
        return {"kind": self.kind, "name": self.name, "location": self.location,
                "hits": self.hits, "target": self.target, "closed": self.closed,
                "detail": self.detail}

    def __repr__(self):
        return "<%s %s %s/%s %s>" % (self.kind, self.name, self.hits, self.target,
                                     "closed" if self.closed else "OPEN")


class CoverageReport:
    def __init__(self, bins, source=None, tool=None):
        self.bins = bins
        self.source = source
        self.tool = tool

    @property
    def open_bins(self):
        return [b for b in self.bins if not b.closed]

    def closure(self):
        if not self.bins:
            return 1.0
        return sum(1 for b in self.bins if b.closed) / float(len(self.bins))

    def by_kind(self):
        out = {}
        for b in self.bins:
            out.setdefault(b.kind, [0, 0])
            out[b.kind][1] += 1
            if b.closed:
                out[b.kind][0] += 1
        return out

    def to_dict(self):
        return {"tool": self.tool, "source": self.source,
                "closure": round(self.closure(), 4),
                "bins_total": len(self.bins), "bins_open": len(self.open_bins),
                "by_kind": self.by_kind(),
                "open": [b.to_dict() for b in self.open_bins[:500]]}


# ---------------------------------------------------------------- parsers ----
# Real simulators emit their own formats. Rather than pretend one exists, each
# parser is written against a specimen that is generated and checked into
# bench/ so the parsing claim stays testable without the tools installed.

def parse_toggle_report(text, kind="toggle"):
    """Parse `# Toggle Coverage Report` style tables.

    Recognises the shape:
        net                       : hits
        top.dut.state[2]          :    0
    """
    bins = []
    cur = None
    for line in text.splitlines():
        m = re.match(r"^\s*(\w+)\s+Coverage\s+Report\s*$", line, re.I)
        if m:
            cur = m.group(1).lower()
            continue
        m = re.match(r"^\s*([\w.\[\]\$\\<>:~]+)\s*:\s*(\d+)\s*$", line)
        if m and cur:
            bins.append(Bin(cur, m.group(1).split(".")[-1], location=m.group(1),
                            hits=int(m.group(2))))
    return bins


def parse_branch_report(text):
    """Parse `if`/`case` branch coverage of the form  `line 45: 0/1`."""
    bins = []
    for m in re.finditer(r"^\s*(?:line\s+)?(\d+)\s*:?\s*(\d+)\s*/\s*(\d+)\s*$",
                         text, re.M):
        line, hit, tot = m.group(1), int(m.group(2)), int(m.group(3))
        bins.append(Bin("branch", "line%s" % line, location="line %s" % line,
                        hits=hit, target=tot))
    return bins


def parse_urg_dir(path):
    """Read whatever text reports are in a URG/VCS coverage directory.

    VCS writes .txt companions next to its HTML; that is what is read here.
    The point is to consume the real artefact rather than a format invented
    for a demo.
    """
    bins = []
    for root, _, files in os.walk(path):
        for f in files:
            if not f.endswith((".txt", ".rpt")):
                continue
            p = os.path.join(root, f)
            txt = open(p, encoding="utf-8", errors="ignore").read()
            kind = ("toggle" if "toggle" in f.lower() else
                    "branch" if "branch" in f.lower() or "cond" in f.lower() else
                    "line" if "line" in f.lower() else "expr")
            for b in parse_toggle_report(txt, kind) or parse_branch_report(txt):
                b.location = b.location or p
                bins.append(b)
    return bins


def from_verilator_coverage(path):
    """Parse verilator_coverage output (`%` <file> <count> <line> ...)."""
    bins = []
    if not os.path.exists(path):
        return bins
    for line in open(path, encoding="utf-8", errors="ignore"):
        m = re.match(r"^\s*([\d.]+)\s+(.+?)\s+(\d+)\s+(\d+)\s*$", line)
        if m:
            loc, hits = m.group(2), int(m.group(3))
            bins.append(Bin("line", os.path.basename(loc) + ":" + m.group(4),
                            location="%s:%s" % (loc, m.group(4)), hits=hits))
    return bins


def toolchain_check():
    """Which simulators are actually on this host? Never fails; 'none' is an answer."""
    found = {}
    for t in ("verilator", "iverilog", "vvp", "vcs", "xrun", "vsim", "verilator_coverage"):
        found[t] = subprocess.run(
            ["where" if os.name == "nt" else "which", t],
            capture_output=True, text=True).returncode == 0
    return found
