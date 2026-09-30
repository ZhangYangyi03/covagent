"""Seven vector assemblies, one `assign` apart.

`src` counts 0..255 so every bit toggles a different number of times (bit0 256,
bit1 128, ... bit7 2). That is the whole instrument: it makes a permutation
visible. Under any uniform stimulus every bit would toggle alike and a reorder
would be indistinguishable from a correct wire.

    ok        seam_data = src                 correct
    cond      en ? src : seam_data            correct, latched
    reorder   {src[0], src[7:1]}              510 toggles, 8 bits lit, wrong
    swapped   {src[6:0], src[7]}              510 toggles, 8 bits lit, wrong
    shifted   {1'b0, src[7:1]}                bit 7 dropped
    partial   {src[7:4], 4'b0}                lower nibble zeroed
    dead      8'd0                            carries nothing

reorder and swapped are the ones worth staring at: every total the report can
produce is identical to `ok`, including the number of lines covered. What differs
is which bit has which count.

The scalar/vector split matters for what a verdict MEANS. For a scalar seam,
BOUND_MISMATCH says the wire is bound to the wrong driver. For a vector seam,
BIT_MISMATCH with kind=ORDER says something narrower and more actionable: nothing
is missing and nothing extra arrived, so it is the bit order, and no re-synthesis
will fix it -- only a corrected connection will.
"""

VEC_LINE = "assign seam_data = src;          /* VEC: the vector wiring under test */"

VARIANTS = [
    {"name": "ok", "expect": "CROSSED", "kind": "",
     "line": "assign seam_data = src;                 /* correct */",
     "why": "the control: a correct bus must not be flagged"},
    {"name": "cond", "expect": "CROSSED", "kind": "",
     "line": "assign seam_data = en ? src : seam_data; /* correct, latched */",
     "why": "a conditioned bus is legitimate -- flagging it makes the check "
            "unusable on real RTL, which always has enables"},
    {"name": "reorder", "expect": "BIT_MISMATCH", "kind": "ORDER",
     "line": "assign seam_data = {src[0], src[7:1]};  /* reordered */",
     "why": "the case no total can see: same count, same support, wrong order"},
    {"name": "swapped", "expect": "BIT_MISMATCH", "kind": "ORDER",
     "line": "assign seam_data = {src[6:0], src[7]};  /* rotated */",
     "why": "a rotation of the same bus -- also invisible to every total"},
    {"name": "shifted", "expect": "BIT_MISMATCH", "kind": "SUPPORT",
     "line": "assign seam_data = {1'b0, src[7:1]};    /* shifted, bit7 dropped */",
     "why": "a bit the wiring does not carry"},
    {"name": "partial", "expect": "BIT_MISMATCH", "kind": "SUPPORT",
     "line": "assign seam_data = {src[7:4], 4'b0};    /* upper nibble only */",
     "why": "half the bus never arrives"},
    {"name": "dead", "expect": "NOT_ARRIVED", "kind": "",
     "line": "assign seam_data = 8'd0;                /* carries nothing */",
     "why": "the driver is busy and nothing reaches the port at all"},
]
