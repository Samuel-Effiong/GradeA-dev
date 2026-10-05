# H-109: the like-for-like baseline, expected counts, written before the run

d5, 2026-10-05, before the run. H-97's battery on the UNEDITED test module
at `085adecd`. If the difference from H-97's record belongs to `f1e0e9d7`
(the module's later change) and not to H-109, the counts at `085adecd`
equal today's at `d58a65d3`, mutant for mutant and subtest for subtest:

| Mutant | Expected at 085adecd (= seen at d58a65d3) | H-97's record |
|---|---|---|
| D1 | failures=43, errors=9 | failures=20, errors=3 |
| D2 | failures=16 | failures=16 |
| D3 | failures=1, errors=7 | failures=1, errors=7 |
| D4 | errors=15 | errors=1 |
| D5 | failures=12 | failures=12 |

If they differ from the middle column, I report both tables and nothing
more than they show.
