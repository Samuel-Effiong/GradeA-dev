# H-124: the no-Playwright-at-import rule follows other names, methods and getattr

**Author:** d5. **Branch:** `task/h124-no-playwright-rule-misses`, off
`task/beta-batch-8` `5fc8a456`. **Verifier:** v2. Test-only: one guard
module, `AutoGrader/tests_no_playwright_at_import.py`, and a mutation
runner. No production code, no migration, no settings change.

Source: v2's record for H-118, note 1 (the rule's four misses), and my
reading for this row.

**State of the gates at this commit:** (r), (a) and (b) have run on
`860541a4`, all as predicted. **(c), the owning-app run, has NOT run.**
It is held, by agreement with 0b and the SM's ruling of 2026-10-06, until
this branch is base-updated onto the batch 9 tip that holds H-110 and
H-123; it then runs once.

## The change (5 points)
1. **The rule read one set of names.** It now keeps two, because a call
   is written in two ways: names that reach a starter when called bare
   (`name()`), and names that reach one when asked of something
   (`thing.name()`, or `getattr(thing, "name")()` with the name written
   out).
2. **Other names are followed.** A starter imported under another name;
   a name bound to a starter, or to a function or method that reaches
   one (plain or annotated binding, to a name or to an attribute). The
   value must be the thing itself: not a call of it, and not something
   read off it.
3. **Methods are followed.** A method that reaches a starter counts when
   asked of its class or of an object (static, class and instance
   methods, and through a second method), and by its bare name inside its
   own class body.
4. **`getattr` with the name written out is read** as asking for that
   name.
5. **The scan's first look.** Before the rule reads a file, the scan
   looks whether the file mentions a starter at all. That look split the
   text into words after turning `(` and `.` into spaces, so a starter's
   name beside a quote, a comma or a closing bracket was not a word and
   the file was never read. It is now a plain search for the name
   anywhere in the file. To test the scan itself, its loop moved out of
   the test into `starts_at_import_under(root)` (unchanged in the red
   commit), and `all_test_modules` takes the root.

## A behaviour change for the verifier to try
- **Before:** a module function that reaches a starter was matched by
  name anywhere in a call, so `thing.available()` at import was flagged
  whenever the module also had a function `available` that reaches a
  starter. v2 listed this as a false alarm in H-118's record.
- **After:** a module function counts only when called by its bare name.
  `T().available()` is no longer flagged for the function's sake.
- **What that gives up:** a module function reached at import through an
  attribute, for example a module that imports itself and calls
  `itself.available()`. The rule no longer sees that. I know of no such
  code in the tree, and the tree scan names nothing before or after.
- Pinned by the samples "a method sharing its name with a helper that
  starts one" and "a helper sharing its name with a method that starts
  one", and by mutants N5 and N6.

## What it does not cover
- **Still a reading, not a proof.** The rule reads one file at a time: a
  helper in another module that starts Playwright, called at import, is
  invisible to it. So is `exec` of a string.
- **Two limits, pinned by `test_what_the_rule_still_does_not_see`:** a
  starter kept in a container and called from it (`starters[0]()`), and
  `getattr` with a name worked out at run time.
- **Two false alarms, kept on the safe side and pinned by
  `test_the_false_alarms_that_are_known_and_kept`:** a helper with a
  local variable named like a starter (would need scope analysis); and,
  new with this row, another object's method that shares its name with a
  method that reaches a starter (the rule reads names, not types).
- **Bindings are followed in any scope.** A name bound to a starter
  inside one function counts as a starter everywhere in the file. Safe
  side; no sample pins it.
- **The SM's ruling on `functools.partial`:** it was to be a stated
  limit. It is in fact caught once other import names are followed (the
  starter's name stands inside the call that is made), with no code of
  its own; the scan test's bracketed file uses that shape.
- **The fresh-interpreter test is unchanged** and still covers one
  module, `assignments.tests_pdf_renderer`.

## Commits
| Commit | What |
|---|---|
| `77628465` | Red tests first, and the scan loop moved into a function, unchanged |
| `8c7022a7` | The rule and the first look |
| `e5cd14ef` | Two more bound-name samples, added before any run: two branches of the binding step had no sample |
| `860541a4` | The mutation runner. The gates ran on this tip |

## Gates (0b's GRANT 10:47:32 WAT on 2026-10-06, RELEASE 10:57)
One chain, `chain.sh 860541a4 77628465`, serial, 6G cap, every run's
output straight to a file. No test here has a wall-clock assertion.

| Step | Result, from the raw log | Load (1 min) start / end |
|---|---|---|
| (r) the red commit `77628465`, the guard module, in a disposable worktree | Ran 18 tests in 7.879s, FAILED (failures=15), exit=1 | 2.28 / 3.42 |
| (a) the guard module and the repo-wide guard list, 22 labels | Ran 268 tests in 189.103s, OK, exit=0 | 3.42 / 3.64 |
| (b) 17 mutants, each against the guard module | baseline Ran 18 tests in 8.291s, OK; 17 of 17 KILLED; runner exit=0 | 3.64 / 5.59 |
| (c) the owning app, `AutoGrader`, `--parallel 2` | **not run: held for the batch 9 base** | |

- **(r):** the 15 failures are the predicted red set, by test and sample
  (`expected_repro_77628465.txt`): 5 other-name samples, 5 method
  samples, 2 `getattr` samples, the method that shares a helper's name
  (flagged then), the new false alarm (not raised then), and the scan
  test.
- **(a):** in the verbose log both tree-wide tests of the guard pass at
  the tip: the fresh-interpreter test and the repo scan.
- **(b):** every mutant's raw output holds its own "Ran 18 tests" line
  and a FAILED line; no BROKEN, no exit 124 or 137. Every restore equals
  the commit's blob by sha256.

## Mutants
The expected failing tests and samples were written before any run
(`expected_kills.py.txt`, file clock 10:44:20; 0b read it at the grant,
sha256 prefix `10f03799504b8c5c`). Every mutant's failing set equals its
expected set (`expected_kills_860541a4.txt`).

| Mutant | What is broken | Failing samples |
|---|---|---|
| N1 | another import name is not followed | 2 |
| N2 | no binding is followed | 5 |
| N3 | any mention binds a name | 2 |
| N4 | methods are not followed | 6 |
| N5 | module functions count when asked of something too | 1 |
| N6 | methods count by bare name everywhere | 1 |
| N7 | no bare-name match in the class body | 1 |
| N8 | `getattr` is not read | 3 |
| N9 | the old first look | 1 |
| N10 | annotated bindings forgotten | 1 |
| N11 | `module.starter` as a value binds nothing | 1 |
| N12 | a bare name as a value binds nothing | 4 |
| N13 | binding to an attribute forgotten | 1 |
| N14 | a class-body name is not asked of the class | 1 |
| G1 | H-118's G1, new anchor: module functions are not followed | 8 |
| G2 | H-118's G2: function bodies read as run at import | 6 |
| G3 | H-118's G3: the async starter forgotten | 2 |

- **Why G1 to G3 are here (rule 17 addendum):** this row rewrites the
  function those three H-118 mutants changed, so they are run again on
  the new module. H-118's P1 to P8 change
  `assignments/tests_pdf_renderer.py` and are judged by the probe tests,
  which this row does not touch; they are not repeated.
- **Rules 17 and 18:** `PYTHONDONTWRITEBYTECODE=1` on every run;
  `__pycache__` of the mutated module's package deleted before the
  baseline, before each mutant and after each restore; each inner run
  writes to its own file with stdin from the null device. The runner is
  H-123's from `LOAD_FAILURE` down, byte for byte (0b diffed it).
- **No database is left behind:** the runner passes `--keepdb`, but the
  module is `SimpleTestCase` only and makes none.
- **The battery is on the final test module:** nothing has changed since
  `e5cd14ef`.

## Checked without a test run
- **The samples, in plain Python** (`plain_check.py.txt`): the rule
  called on every sample string of the test module, before each commit.
  This was my prediction of red and green, not evidence; the runs above
  are the evidence.
- **The first reading** (`probe_samples.py.txt`, `probe_samples.out.txt`):
  the rule of `e578e3db` on fifteen samples, including whether the old
  first look let each through.
- **Anchors:** each of the 17 anchors occurs once in the module and each
  mutant still parses.
- **The tree:** the scan at the tip reads 384 test modules and names
  none.

## The credential pattern
This folder, archives opened: 0 URLs with anything in the password
position, 0 encoded ones. Three files hold assignment-form lines whose
names contain "pass" or "key"; all are code (`prefilter_passes=`,
`key=str`, "expected but passed:").

## Files
- Raw logs: `r_repro_77628465.log.gz`, `a_modules_860541a4.log.gz`,
  `b_mutation_battery_860541a4.log`.
- `battery_860541a4.tar.gz`: `results.tsv`, the short log per mutant, and
  `logs/raw/*.out`, each inner run's whole output.
- `chain.status` (times and load), `expected_repro_77628465.txt`,
  `expected_kills_860541a4.txt`.
- Scripts as run: `chain.sh.txt`, `expected_kills.py.txt`; `c_h124.sh.txt`
  is the held (c).
