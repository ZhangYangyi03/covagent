# covagent

Coverage closure: read the report, name the dark lines, generate the stimulus
that reaches them, and keep the change only if the simulator says the number
went up.

## The problem, precisely

A coverage report after a regression run says something like `72.1%`. That
number is a fact about the past and tells nobody what to do next. The work that
follows is a person opening the HTML report, finding the red lines, reading the
RTL, working out which input sequence reaches each one, editing the testbench,
rebuilding, and re-running -- for days, until the number is high enough to sign
off. The simulator is free and fast. The bottleneck is the loop in a human head.

This implements that loop with no human in it, and the acceptance criterion is
the simulator's, not a model's:

```
  make cov  ->  coverage.info  ->  dark lines + why + what reaches them
                     ^                          |
                     |                          v
                 re-run  <--  edited testbench  <--  LLM
                     |
              keep only if closure went UP, else restore
```

## What makes this different from "generate tests with an LLM"

1. The unit of work is a line of RTL, not a test. `covagent.holes` reads the
   lcov report, finds `alu.v:19` is dark, reads the RTL, and emits:

       line 19  3'd2: y <= a & b;                  // AND
        why: this case item was never taken; op never took this value in the last run
        try: drive op with 3'd2 (the values already exercised are 3'd0, 3'd1)

   The "try" clause is computed from the RTL -- the `case` selector is found by
   walking back to the enclosing `case (...)` -- not written by hand and not
   guessed by the model. The model receives a target, not a percentage.

2. Every candidate is verified by the simulator, and a candidate that does not
   build, or that builds without raising closure, is discarded and the previous
   testbench is restored. The number in the report is the number that survived,
   so a hallucinated testbench cannot inflate it.

3. Verilator's `--coverage` does not write the database. Measured on 5.020: the
   generated `main()` instruments the design and never calls
   `coveragep()->write()`, so `coverage.dat` is never created and
   `verilator_coverage` reports `Total coverage (0/0) 0.00%`. This repo ships
   `sim_main.cpp`, a replacement main that flushes before the model is
   destroyed. Without it there is no measurement at all, which is a trap worth
   documenting because the failure looks like "my design has no coverage".

## Measured result

On `bench/rtl/alu.v` with a deliberately lazy testbench that only exercises ADD
and SUB:

    before   closure 72.1%   (dark: op 3'd2 .. 3'd7, plus default)
    after    closure 92.0%   (dark: default only)
    rounds   1 kept, 0 discarded
    wall     66s including two verilator builds

The one remaining hole is the `default:` arm of a `case` over a 3-bit selector
with all 8 values already enumerated. It is unreachable, and reporting it rather
than "fixing" it is the correct answer.

## The coverage report cannot see a mis-wired assembly

This is the limit of the flow above, measured, and the reason `covagent/boundary.py`
exists. A coverage number is a claim about a report, not about a machine.

`bench/seam/` builds the same two blocks -- a counter and a FIFO, byte-identical
files -- over five testbenches that differ in ONE line: the `assign` that feeds
the FIFO's write enable.

    variant   what the wire is               line coverage   verdict
    ok        en, straight through           38/47           CROSSED
    clip      en & en, same wire             38/47           CROSSED
    cond      en & ~full, correct backpressure 38/47         CROSSED
    broken    rd_en -- the producer's enable never arrives
                                             37/47           BOUND_MISMATCH
    dead      1'b0, a wire carrying nothing  32/47           UNEXERCISED

The broken assembly is within one line of the correct one. In the raw database,
`seam_en` toggles 2 times where the net it is declared to come from toggles 6:
the activity arriving at the FIFO's write port is the read enable's, so the wire
is bound to the wrong driver. That is the whole signal, and it is one subtraction.

Run it:

    python bench/seam/run_seam.py              # rebuilds all five, needs verilator
    python bench/seam/run_seam.py --replay     # re-reads the checked-in dumps

The check is deliberately asymmetric, because the two directions are not equally
trustworthy. Activity on the net with none at the sink is evidence of a fault.
Quiet on both sides is evidence of a weak test, and is reported as UNEXERCISED --
sending someone to debug correct RTL is how a check gets switched off.

Three ways this check could have been wrong, and each one is a test:

    tests/test_boundary.py::test_correct_wire_toggles_on_both_sides
      a correct wire must not be flagged
    ...::test_equivalent_rewrite_is_not_flagged
      `en & en` is the same wire -- keying on the expression breaks re-synthesised
    ...::test_backpressure_is_not_flagged
      conditioned is not broken
    ...::test_driver_is_read_in_the_seam_scope
      a port named `en` exists on the testbench AND inside every instance it
      feeds; summing across the hierarchy invents a mismatch on a correct design
    ...::test_converted_report_is_refused_rather_than_answered
      see below

### Why the report alone cannot do this

`verilator_coverage --write-info` writes lcov, which has line and branch records
and no toggle records. Measured on the same run: the raw database holds 98 toggle
bins, the written report holds 0. Every number in the verdict column above comes
from the raw file.

So a report-only flow does not get a weaker answer, it gets no answer -- and the
dangerous version of that is a silent one. `boundary.seam_report` raises rather
than returning UNEXERCISED for every seam when it is handed a converted report:
the question was about wiring, and "your test is weak" is a different question's
answer.

## Install

    pip install -e .

Simulator side (WSL or Linux):

    apt-get install -y verilator

Any OpenAI-compatible endpoint:

    export COVAGENT_BASE_URL=https://your-endpoint/v1
    export COVAGENT_MODEL=your-model
    export COVAGENT_API_KEY=...

## Use

    python -m covagent doctor
    python -m covagent holes --dir bench/rtl --rtl alu.v
    python -m covagent close --dir bench/rtl --rtl alu.v --tb tb_alu.v \
        --driver ..\\driver_tb_alu.v --target 0.95 --rounds 3

`doctor` reports whether verilator is reachable and whether the model endpoint
answers, before any design is touched. `holes` prints the actionable list on its
own, which is the useful half of this if you already have a testbench you trust.

The boundary check, on a raw database and a seam manifest:

    python -m covagent boundary --dump obj/cov.dat --seam-json bench/seam/seam.json \
        --info coverage.info

It exits 2 when a seam is bound to the wrong driver, and 1 rather than 2 when it
is handed a converted report it cannot read -- the difference between "the
wiring is wrong" and "I was not given the data to say", which is worth an exit
code of its own.

## Layout

    covagent/coverage.py   report parsers and the Bin/CoverageReport model
    covagent/holes.py      dark line -> reason + suggestion, from the RTL
    covagent/sim.py        the WSL bridge, make runner, lcov reader
    covagent/close.py      the generate/verify/keep-or-restore loop
    covagent/boundary.py   seam check: did the right signal cross the boundary
    bench/rtl/sim_main.cpp the main that actually writes coverage.dat
    bench/rtl/alu.v        the design; tb_alu.v the lazy testbench it starts from
    bench/seam/            five assemblies, one wire apart, and their dumps

## Honest limits

- Closure improving is not correctness. This closes holes; it does not check the
  design does the right thing. Pair it with a checker (see `assertforge`).
- The suggestion engine understands `case` items, `default` arms and simple
  comparisons. Toggle coverage on a wide bus, FSM arc coverage, and
  cross-coverage are parsed if the simulator emits them but are not yet turned
  into suggestions.
- The loop is greedy: one round accepts the first improvement. A hill-climbing
  search over several candidates would close more, at more LLM and build cost.
- The boundary check reads toggle counts, so a short simulation can under-count a
  correct wire. That is why quiet-on-both-sides is UNEXERCISED rather than a
  fault, and why the driver's count is compared in the seam's own scope rather
  than across the hierarchy. It is a check for a wire bound to the wrong driver,
  not a proof that a wire is connected.
- `boundary.py` is exercised on one seam shape (a combinational net feeding a
  sub-module port). Multi-bit buses are compared at net level, not per bit.
- Testbenches that only build under a commercial simulator (VCS/Xcelium) are not
  covered. The parsing is written against lcov-format output, which verilator
  and VCS both produce, but only verilator is exercised here.

## License

Apache-2.0.
