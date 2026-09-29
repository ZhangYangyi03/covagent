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

## Layout

    covagent/coverage.py   report parsers and the Bin/CoverageReport model
    covagent/holes.py      dark line -> reason + suggestion, from the RTL
    covagent/sim.py        the WSL bridge, make runner, lcov reader
    covagent/close.py      the generate/verify/keep-or-restore loop
    bench/rtl/sim_main.cpp the main that actually writes coverage.dat
    bench/rtl/alu.v        the design; tb_alu.v the lazy testbench it starts from

## Honest limits

- Closure improving is not correctness. This closes holes; it does not check the
  design does the right thing. Pair it with a checker (see `assertforge`).
- The suggestion engine understands `case` items, `default` arms and simple
  comparisons. Toggle coverage on a wide bus, FSM arc coverage, and
  cross-coverage are parsed if the simulator emits them but are not yet turned
  into suggestions.
- The loop is greedy: one round accepts the first improvement. A hill-climbing
  search over several candidates would close more, at more LLM and build cost.
- Testbenches that only build under a commercial simulator (VCS/Xcelium) are not
  covered. The parsing is written against lcov-format output, which verilator
  and VCS both produce, but only verilator is exercised here.

## License

Apache-2.0.
