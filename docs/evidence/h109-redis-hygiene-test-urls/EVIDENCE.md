# H-109: the Redis hygiene URL-shape test builds its URLs from parts

**Author:** d5. **Branch:** `task/h109-redis-hygiene-test-urls`, off
`task/beta-batch-7` `085adecd`. Bundle 7. Test-only: one file,
`AutoGrader/tests_redis_hygiene_databases.py` (H-97's test module). No
production code.

## The change (4 points)
1. **Before:** `test_the_database_is_set_whatever_the_url_says` feeds
   nineteen URL shapes to `location_for`. Eight of them were written in
   the source as whole URLs with the test's made-up password in the
   password position.
2. **After:** those eight are built from their parts by a small helper
   inside the test (`secured(scheme, user, rest)`). No line of the file
   holds a URL with a password.
3. **Reach:** the test's source only. The nineteen strings the test feeds
   to `location_for` are the same strings in the same order: compared
   before and after the edit by evaluating both tables (not by a test
   run). The loop and the assertions are untouched; they already named
   the shape and never printed the URL.
4. **Why:** SM, 2026-10-05: the tree that reaches beta should hold no URL
   with a password in it, even a made-up one. H-89's pattern count found
   these eight lines.

**What remains in the file, in words:** one line that assigns the test's
made-up password to a variable (an assignment form, 23 characters, in a
test file). The URLs are built from that variable. It is not a credential.

| Commit | What |
|---|---|
| `d58a65d3` | the eight URLs built from parts |

## Gates (rule 15.4: a test-only change)
On the frozen tip `d58a65d3`, 2026-10-05, under 0b's grant. Output
straight to files (rule 18). Status: `chain.status`.

| Gate | Result | Log |
|---|---|---|
| The touched module | GREEN: 9 tests, OK | `t_module_d58a65d3.log` |
| H-97's battery (`test_h97_mut`), 5 mutants, from `docs/evidence/h97-redis-hygiene-all-databases/run_mutants.py` | 5/5 killed, restore verified | `b_mutation_battery_d58a65d3.log`, `battery_d58a65d3/` |
| The same battery on the unedited module at `085adecd` (the baseline) | 5/5 killed; every failing line identical to the row above | `b_mutation_battery_baseline_085adecd.log`, `battery_baseline_085adecd/` |
| Redis after the run, H-97's read-only listing over all 16 databases | 0 `gaplus-t<pid>` keys, of dead or live pids | `redis_listing_after.txt` |

**Every mutant's failing tests against H-97's record**
(`docs/evidence/h97-redis-hygiene-all-databases/results.tsv` and `logs/`):

| Mutant | H-97's record | This run | |
|---|---|---|---|
| D2 | failures=16 | failures=16 | identical, subtest for subtest |
| D3 | failures=1, errors=7 | failures=1, errors=7 | identical |
| D5 | failures=12 | failures=12 | identical |
| D1 | failures=20, errors=3 | failures=43, errors=9 | the same three tests fail; one of them reports more subtests |
| D4 | errors=1 | errors=15 | the same one test fails; it reports more subtests |

**The difference in D1 and D4 is not H-109's: shown by a like-for-like
baseline.** 0b granted one more run: H-97's battery on the UNEDITED module
at `085adecd` (in the h78-repro worktree, put back afterwards). I wrote the
expected counts down first (`BASELINE_EXPECTED.md`).

| Mutant | Unedited module, `085adecd` | Edited module, `d58a65d3` |
|---|---|---|
| D1 | failures=43, errors=9 | failures=43, errors=9 |
| D2 | failures=16 | failures=16 |
| D3 | failures=1, errors=7 | failures=1, errors=7 |
| D4 | errors=15 | errors=15 |
| D5 | failures=12 | failures=12 |

The failing lines of each mutant, subtest labels included, are identical
between the two runs (compared line for line). So H-109 changes nothing in
what the battery sees.

**A note on H-97's evidence, not a defect in H-97** (for v2, who verified
it): H-97's recorded D1 to D4 ran at `a4f379be`. The test module changed
after that, in `f1e0e9d7` ("a socket URL keeps its credentials"), which
gave `test_the_database_is_set_whatever_the_url_says` a subtest per URL
shape and database and more shapes; only D5 was recorded at `f1e0e9d7`.
Against the module as it finally is, D1 fails 43 and 9 and D4 fails 15
(five socket shapes times three databases), not the 20 and 3, and 1, of
H-97's `results.tsv`. The mutants are killed either way.

**Rule 17.** The battery and its baseline ran with
`PYTHONDONTWRITEBYTECODE=1`, and the runner deleted `__pycache__` in the
mutated module's package before the baseline, before each mutant and
after each restore. H-97's own committed `logs/` and `results.tsv`, which
the runner writes beside itself, were put back after the run; this run's
are in `battery_d58a65d3/`.

## For the verifier (v2: the delta since your H-97 verdict)
- The whole delta is `git diff 085adecd d58a65d3`: one test module, +21/-14.
- The comparison of the nineteen shapes can be repeated without a test run
  by evaluating the `shapes` table of both versions with the same
  password.
- The credential pattern (a URL with anything in the password position)
  gives 0 lines in the module now; it gave 8.
