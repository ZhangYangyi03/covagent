"""The five assemblies, and what the seam check must say about each.

Every entry rewrites exactly one line of tb_seam.v: the `assign` that feeds the
FIFO's write enable. The two block files are identical in all five.

The expectations are the point, not the numbers. Two of them exist to catch the
check being wrong in the direction that matters -- a check that flags every
conditioned wire as broken gets switched off by the second design it sees, and
then it catches nothing:

    ok       correct wiring, straight through          -> CROSSED
    clip     same wire written `en & en`               -> CROSSED
             (a re-synthesised or tool-written netlist does this often; if the
              check keyed on the wire's identity rather than its driver's
              activity, this is what it would break on)
    cond     `en & ~full`, correct and conditioned     -> CROSSED
    broken   the producer's enable swapped for rd_en   -> BOUND_MISMATCH
    dead     1'b0, a wire carrying nothing             -> UNEXERCISED
"""

SEAM_LINE = "assign seam_en = en;          /* SEAM: the wiring under test */"

VARIANTS = [
    {
        "name": "ok",
        "line": "assign seam_en = en;            /* correct: the producer's enable */",
        "expect": "CROSSED",
        "why": "the control case: a correct wire must not be reported as broken",
    },
    {
        "name": "clip",
        "line": "assign seam_en = en & en;        /* correct, written as the tool would */",
        "expect": "CROSSED",
        "why": "an equivalent rewrite -- guards the check against keying on the "
               "wire's name instead of its driver's activity",
    },
    {
        "name": "cond",
        "line": "assign seam_en = en & ~full;     /* correct, conditioned on the FIFO's full flag */",
        "expect": "CROSSED",
        "why": "backpressure is legitimate; a check that flags it is unusable",
    },
    {
        "name": "broken",
        "line": "assign seam_en = rd_en;         /* BROKEN: the producer's enable never arrives */",
        "expect": "BOUND_MISMATCH",
        "why": "the actual defect this is built for",
    },
    {
        "name": "dead",
        "line": "assign seam_en = 1'b0;          /* a wire that carries nothing */",
        "expect": "UNEXERCISED",
        "why": "quiet on both sides is a weak test, not a wrong wire -- the report "
               "has to say which, or it sends someone to debug good RTL",
    },
]
