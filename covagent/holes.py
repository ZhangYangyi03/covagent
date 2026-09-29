"""Turn a coverage report into a description of what to try next.

A coverage number is not actionable. `87%` does not tell a person, and it does
not tell a model, what stimulus to write. This module reduces an open bin to a
statement of the form:

    alu.v line 19 (case item op==3'd1) was never executed;
    in the last run op took the values [0, 1] and never 2..7;
    to reach it, drive op with 3'd1 while en is high.

That last clause is generated, not hand-written, and it is what makes the loop
close: the model gets a target, not a number.
"""

import os
import re

from . import coverage


class Hole:
    def __init__(self, line, text, reason, suggestion, kind="line"):
        self.line = line
        self.text = text           # the RTL line that is dark
        self.reason = reason       # why it is dark
        self.suggestion = suggestion
        self.kind = kind

    def to_dict(self):
        return {"line": self.line, "kind": self.kind, "rtl": self.text,
                "reason": self.reason, "suggestion": self.suggestion}

    def describe(self):
        return ("line %s  %s\n   why: %s\n   try: %s"
                % (self.line, self.text.strip(), self.reason, self.suggestion))


CASE_ITEM = re.compile(r"(\d+)'([bdho])([0-9a-fA-F_]+)\s*:")
DEFAULT_ITEM = re.compile(r"\bdefault\s*:")
COMPARE = re.compile(r"([A-Za-z_]\w*)\s*(==|!=|<|>|<=|>=)\s*([A-Za-z_]\w*|\d+'[bdh][0-9a-fA-F_]+)")


def _decode(literal):
    m = re.match(r"(\d+)'([bdho])([0-9a-fA-F_]+)", literal)
    if not m:
        return literal
    width, base, digits = int(m.group(1)), m.group(2), m.group(3).replace("_", "")
    val = int(digits, {"b": 2, "d": 10, "h": 16, "o": 8}[base])
    return "%d'd%d" % (width, val)


def _selector_of(case_line, rtl_lines, case_start):
    """Find which signal the case statement switches on.

    Walks back from the `case (...)` line so a case item can be described in
    terms of the signal, which is the only way the suggestion is usable.
    """
    for i in range(case_start, max(-1, case_start - 20), -1):
        m = re.search(r"\bcase\s*\(\s*([A-Za-z_]\w*)", rtl_lines[i])
        if m:
            return m.group(1)
    return None


def find_holes(report, rtl_path, max_holes=12, min_hits=0):
    """Pair dark coverage lines with the RTL that produced them."""
    if not os.path.exists(rtl_path):
        return []
    rtl_lines = open(rtl_path, encoding="utf-8", errors="ignore").read().splitlines()
    by_line = {}
    for b in report.bins:
        if b.kind not in ("line", "branch", "expr"):
            continue
        m = re.search(r":?(\d+)$", b.location or b.name or "")
        if not m:
            continue
        ln = int(m.group(1))
        if b.hits <= min_hits and (ln not in by_line or not by_line[ln].closed):
            by_line[ln] = b

    # where did the case statement start, for selector lookup
    case_starts = [i for i, l in enumerate(rtl_lines) if re.search(r"\bcase\s*\(", l)]

    holes = []
    for ln in sorted(by_line):
        idx = ln - 1
        if idx < 0 or idx >= len(rtl_lines):
            continue
        text = rtl_lines[idx]
        stripped = text.strip()
        if not stripped or stripped.startswith("//"):
            continue
        # A port declaration is not reachable code -- verilator reports it as a
        # dark line, and chasing it wastes an LLM call and a compile.
        if re.match(r"^(input|output|inout)\b", stripped):
            continue
        ci = CASE_ITEM.search(text)
        reason, sugg = "", ""
        if ci:
            selector = _selector_of(text, rtl_lines, idx)
            start = max([s for s in case_starts if s <= idx], default=None)
            block = rtl_lines[start:idx] if start is not None else []
            taken = sorted({_decode(m.group(0).split(":")[0])
                            for m in (CASE_ITEM.search(l) for l in block) if m})
            val = _decode(ci.group(0).split(":")[0])
            reason = ("this case item was never taken; "
                      "%s never took this value in the last run" % (selector or "the selector"))
            if taken:
                sugg = ("drive %s with %s (the values already exercised are %s)"
                        % (selector or "the selector", val, ", ".join(taken)))
            else:
                sugg = "drive %s with %s" % (selector or "the selector", val)
        elif DEFAULT_ITEM.search(text):
            reason = "the default arm was never reached"
            sugg = "drive the selector with a value no other case item matches"
        else:
            cmp_ = COMPARE.search(text)
            if cmp_:
                reason = "this assignment never executed"
                sugg = ("satisfy the guard containing `%s`" % cmp_.group(0))
            else:
                reason = "this line never executed"
                sugg = "construct a sequence that reaches it"
        holes.append(Hole(ln, text, reason, sugg, by_line[ln].kind))
        if len(holes) >= max_holes:
            break
    return holes


def summarize(report, holes):
    lines = []
    lines.append("closure: %.1f%% (%d/%d bins)"
                 % (report.closure() * 100, len(report.bins) - len(report.open_bins),
                    len(report.bins)))
    for kind, (hit, tot) in sorted(report.by_kind().items()):
        lines.append("  %-8s %d/%d" % (kind, hit, tot))
    if holes:
        lines.append("")
        lines.append("open holes (%d shown):" % len(holes))
        for h in holes:
            lines.append("  " + h.describe())
    return "\n".join(lines)
