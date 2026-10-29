# H-124: the no-Playwright-at-import rule follows other names, methods and getattr

**Author:** d5. **Branch:** `task/h124-no-playwright-rule-misses`, begun on
`task/beta-batch-8` `5fc8a456`, now on `task/beta-batch-9` `9fb6d4fe`
(H-110 and H-123 merged). For batch 9. **Verifier:** v2. Test-only: one guard
module, `AutoGrader/tests_no_playwright_at_import.py`, and a mutation
runner. No production code, no migration, no settings change.

Source: v2's record for H-118, note 1 (the rule's four misses), my
reading for this row, and v2's two pre-reads of this branch.

**State of the gates at this commit:** all have run. (r), (a) and (b) ran
on the code tip `7134815d`, all as predicted. The branch was then
base-updated onto the batch 9 tip (`b03f0569`; the guard module is the
same blob, `1b466b7c`, before and after), and **(c), the owning-app run,
ran once there: green.**

**Two rounds.** The gates first ran on `860541a4` (evidence commit
`c9ae46b3`). v2 then read that tip before any hand-over and found that
the new rule had lost a shape the old rule caught (point 6 below). The
SM ruled on the scope, the module changed, and the gates ran again whole
on `7134815d`. The first round is kept below as history; **its battery
is stale for this tip** (rule 17 addendum) and is not relied on.

## The change (7 points)
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

6. **`staticmethod(thing)` and `classmethod(thing)` stand for the thing**
   when a name is bound. Without this the new rule was silent on a
   helper made a static method under its own name and asked of the
   class, which the old rule named (v2's pre-read, P1).
7. **Three more shapes are followed** (v2's pre-read, P2; SM's ruling):
   a class whose `__init__` or `__new__` reaches a starter counts when
   it is called by its bare name; a decorator applied without brackets
   is a call of the decorator, on a function or a class; a name bound to
   a lambda whose body reaches a starter counts.

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
- **What it gave up without my seeing it, now fixed:** the helper made a
  static method under its own name (`available = staticmethod(available)`
  in a class body, then `T.available()`). v2 found it; point 6 above
  restores it, and `test_the_rule_reads_through_staticmethod_and_classmethod`
  pins it.
- Pinned by the samples "a method sharing its name with a helper that
  starts one" and "a helper sharing its name with a method that starts
  one", and by mutants N5 and N6.

## What it does not cover
- **Still a reading, not a proof.** The rule reads one file at a time: a
  helper in another module that starts Playwright, called at import, is
  invisible to it. So is `exec` of a string. The SM's ruling: this rule
  is a cheap first net and the fresh-interpreter test is the real one;
  shape-chasing for this row ends here, and anything found later is a
  limit.
- **Eleven limits, each pinned by a sample in
  `test_what_the_rule_still_does_not_see`** (each starts a driver at
  import and the rule names nothing): a starter kept in a list and
  called by index; `getattr` with a name worked out at run time; and
  nine of v2's twelve (M4 to M12): a starter passed as an argument;
  `functools.partial` bound to a name and called later; a tuple
  assignment; `:=` on a line of its own; a conditional expression; a
  property read at import; a parameter's default value; `__enter__`
  through a `with`; a `for` loop.
- **Four more limits, listed and not pinned** (v2's second pre-read, K1
  to K4, all of the new class step): a subclass of a class whose
  `__init__` starts one, made at import; such a class reached as an
  attribute, for example a nested class; a dataclass whose
  `__post_init__` starts one; an object whose `__call__` starts one.
  Not pinned because the module was frozen for its battery by then; v2
  says at the hand-over if one needs a pin.
- **`functools.partial`, corrected.** The first version of this file
  said it "is in fact caught". That holds only when the partial is
  called in the same expression (`functools.partial(sp)()`: the
  starter's name stands inside the call that is made; the scan test's
  bracketed file uses that shape). Bound to a name and called later, it
  is a pinned limit.
- **Four false alarms, kept on the safe side and pinned by
  `test_the_false_alarms_that_are_known_and_kept`:** a helper with a
  local variable named like a starter; a starter bound inside one
  function, which makes an unrelated function of that name count
  (bindings are followed in any scope); another object's method that
  shares its name with a method that reaches a starter; and the same
  with a common name, a thread started at import beside a method
  `start()` that reaches one. The rule reads names, not scopes or types.
- **Three more false alarms, listed and not pinned** (v2's second
  pre-read, K8 to K10): a decorator named like a method that reaches a
  starter (`@thing.start`); the `@ok.setter` line of a property whose
  getter reaches a starter, although nothing is read at import; a call
  on something read off a class whose `__init__` starts one
  (`B.NAME.upper()`).
- **The fresh-interpreter test is unchanged** and still covers one
  module, `assignments.tests_pdf_renderer`.
- **v2's sample files** (outside the repository, read-only for me):
  `GAP-v2-handover/h124_preread_samples.py` (sha256 prefix
  `e56e39fa2f915199`) and `h124_preread_samples_round2.py`
  (`16f9f54e6d7f6c73`).

## Commits
| Commit | What |
|---|---|
| `77628465` | Red tests first, and the scan loop moved into a function, unchanged |
| `8c7022a7` | The rule and the first look |
| `e5cd14ef` | Two more bound-name samples, added before any run: two branches of the binding step had no sample |
| `860541a4` | The mutation runner. The first round of gates ran on this tip |
| `c9ae46b3` | Evidence of the first round (docs only) |
| `4e22fda5` | Red tests for v2's pre-read points, and the pinned limits and false alarms |
| `0dffc395` | The rule: points 6 and 7 |
| `7134815d` | The runner gains N15 to N19. The second round of gates ran on this tip |
| `b41de114` | Evidence of the second round (docs only) |
| `b03f0569` | Base update onto `task/beta-batch-9` `9fb6d4fe` (H-110, H-123; no conflict; the guard module untouched). (c) ran on this tip |

## Gates, second round (0b's GRANT 11:33:01 WAT on 2026-10-06, RELEASE 11:47)
One chain, `chain2.sh 7134815d 4e22fda5`, serial, 6G cap, every run's
output straight to a file. No test here has a wall-clock assertion. The
machine was busy (the tail of another project's runs): the load is
recorded, and no test failed on time.

| Step | Result, from the raw log | Load (1 min) start / end |
|---|---|---|
| (r) the second red commit `4e22fda5`, the guard module, in a disposable worktree | Ran 22 tests in 16.493s, FAILED (failures=8), exit=1 | 10.27 / 11.67 |
| (a) the guard module and the repo-wide guard list, 22 labels | Ran 272 tests in 311.842s, OK, exit=0 | 11.67 / 9.84 |
| (b) 22 mutants, each against the guard module | baseline Ran 22 tests in 8.771s, OK; 22 of 22 KILLED; runner exit=0 | 9.84 / 6.03 |
| (c) the owning app, `AutoGrader`, `--parallel 2`, watchdog, on `b03f0569` | Ran 573 tests in 327.581s, OK (skipped=2), exit=0; stalled=0 | 3.89 / 2.81 |

- **(c):** 0b's GRANT 13:03:53, on the batch 9 base so that it runs once.
  A waiter read the load every 20 s (`c_wait.log`) and started the run at
  13:10:36, the first reading at 4.00 or under; it ended at 13:16:48. No
  test skipped for want of Chromium. The guard's two tree-wide tests pass
  in it: the scan now reads 386 test modules (H-110's two joined).
- **The laptop was suspended from 13:26:28 to 13:59:07** (0b, from the
  journal). (c) had ended ten minutes before, so the run is not touched
  by it; this evidence was committed after the wake.
- **The battery is not repeated after the base update:** the merge did
  not touch the guard module (blob `1b466b7c` at `7134815d`, `b41de114`
  and `b03f0569`), and every anchor is still found exactly once.
- **(r):** the 8 failures are the predicted red set, by test and sample
  (`expected_repro_4e22fda5.txt`): both samples of each of the four new
  "follows" tests, and nothing else.
- **(a):** in the verbose log both tree-wide tests of the guard pass at
  the tip: the fresh-interpreter test and the repo scan.
- **(b):** every mutant's raw output holds its own "Ran 22 tests" line
  and a FAILED line; no BROKEN, no exit 124 or 137. Every restore equals
  the commit's blob by sha256.

## Mutants, second round
The expected failing tests and samples were written before any run of
this tip (`expected_kills_2.py.txt`, file clock 11:05:50; 0b read it at
the grant, sha256 prefix `db0867870c84b950`). It keeps the first
version's sets and adds N15 to N19 and what the new samples do to the
older mutants. Every mutant's failing set equals its expected set
(`expected_kills_7134815d.txt`).

| Mutant | What is broken | Failing samples |
|---|---|---|
| N1 | another import name is not followed | 2 |
| N2 | no binding is followed | 10 |
| N3 | any mention binds a name | 4 |
| N4 | methods are not followed | 7 |
| N5 | module functions count when asked of something too | 1 |
| N6 | methods count by bare name everywhere | 1 |
| N7 | no bare-name match in the class body | 1 |
| N8 | `getattr` is not read | 3 |
| N9 | the old first look | 1 |
| N10 | annotated bindings forgotten | 1 |
| N11 | `module.starter` as a value binds nothing | 1 |
| N12 | a bare name as a value binds nothing | 7 |
| N13 | binding to an attribute forgotten | 1 |
| N14 | a class-body name is not asked of the class | 3 |
| N15 | `staticmethod` and `classmethod` not read through | 2 |
| N16 | a class made at import is not followed | 2 |
| N17 | a decorator without brackets is not a call | 2 |
| N18 | a lambda binds nothing | 2 |
| N19 | any method that reaches a starter makes its class count | 3 |
| G1 | H-118's G1, new anchor: module functions are not followed | 13 |
| G2 | H-118's G2: function bodies read as run at import | 10 |
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
  H-123's from `LOAD_FAILURE` down, byte for byte (0b diffed it at both
  grants).
- **No database is left behind:** the runner passes `--keepdb`, but the
  module is `SimpleTestCase` only and makes none.
- **The battery is on the final test module:** the module has not
  changed since `0dffc395`.

## First round, kept as history (0b's GRANT 10:47:32, RELEASE 10:57)
`chain.sh 860541a4 77628465`. Its (r) stands: it is the red proof of the
first set of tests. Its (a) and (b) are superseded by the second round.

| Step | Result, from the raw log | Load (1 min) start / end |
|---|---|---|
| (r) the red commit `77628465` | Ran 18 tests in 7.879s, FAILED (failures=15), exit=1; the predicted red set (`expected_repro_77628465.txt`) | 2.28 / 3.42 |
| (a) 22 labels | Ran 268 tests in 189.103s, OK, exit=0 | 3.42 / 3.64 |
| (b) 17 mutants | 17 of 17 KILLED, every failing set as in `expected_kills.py.txt` (10:44:20, `10f03799504b8c5c`) | 3.64 / 5.59 |

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
- **The tree:** the scan read 384 test modules on the first base and
  reads 386 on the batch 9 base (H-110's two joined); it names none on
  either. (c) ran it as a test on the batch 9 base.
- **v2's samples:** v2 called the rule from the git blobs on 20 and then
  32 samples of its own, predictions written first; all as predicted.

## The credential pattern
This folder, archives opened: 0 URLs with anything in the password
position, 0 encoded ones. Five files hold assignment-form lines whose
names contain "pass" or "key"; all are code (`prefilter_passes=`,
`key=str`, "expected but passed:").

## Files
- Second round, raw logs: `r_repro_4e22fda5.log.gz`,
  `a_modules_7134815d.log.gz`, `b_mutation_battery_7134815d.log`;
  `battery_7134815d.tar.gz` (`results.tsv`, the short log per mutant, and
  `logs/raw/*.out`, each inner run's whole output);
  `expected_repro_4e22fda5.txt`, `expected_kills_7134815d.txt`.
- First round, the same set under `77628465` and `860541a4`.
- `chain.status`: times and load of both rounds.
- Scripts as run: `chain2.sh.txt` and `expected_kills_2.py.txt` (second
  round), `chain.sh.txt` and `expected_kills.py.txt` (first round);
  `c_h124.sh.txt` is (c), with `iso_file.sh.txt` and its starter
  `wait_c.sh.txt`.
- (c): `c_autograder_p2_b03f0569.raw.log.gz` (the evidence) with its
  stamped copy `c_autograder_p2_b03f0569.log.gz` (a convenience),
  `c_autograder_p2_b03f0569.load.txt`, `iso.status` (its result line) and
  `c_wait.log` (the load readings before the start).
