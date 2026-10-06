# H-123: the renderer's stall test takes its limit from the machine it runs on

**Author:** d5. **Branch:** `task/h123-renderer-stall-test-relative-limit`,
on `task/beta-batch-9` `cc9682b8` (H-110 merged). For batch 9.
**Verifier:** v2. Test-only: `assignments/tests_pdf_renderer.py` and a
mutation runner. No production code, no migration, no settings change.

Source: the red owning-app run of H-118 on 2026-10-05 (row H-123, SM).

## The change (5 points)
1. **Before:** `ConcurrentRenderingTest.test_one_slow_render_does_not_stall_the_others`
   gave the hung render a 5-second timeout and required the six healthy
   renders beside it to finish within 4.0 seconds, whatever the machine.
   On 2026-10-05 it failed at 7.2 s with no stall at all, under a load
   average of 22.
2. **After:** the test first renders the same six healthy documents
   alone and takes the slowest as its baseline. `stall_limits(baseline)`
   gives the hung render's timeout, `max(5, 4 x baseline)` seconds, and
   the limit for the healthy renders beside it, 0.8 of that timeout. On
   a machine whose baseline is 1.25 s or less these are the old numbers,
   5 and 4.
3. **What it guards does not change.** A render pinned to the hung one
   takes at least the hung render's whole timeout, and the limit is
   always under that timeout. Mutant S1 puts the stall back and the test
   fails (below).
4. **A stretched limit must not become a test that cannot fail.** Past a
   30-second timeout (a baseline over 7.5 s) the test FAILS with "too
   loaded to judge"; it does not skip and does not pass.
5. **After v2's pre-read:** the test asserts that the hung render ran and
   gave up (without it the healthy renders prove nothing); the wiring
   test (`StallTestWiringTest`) needs no browser, so it also runs where
   the browser tests are skipped; mutant S3 covers the new assertion.
   Every run prints its numbers on a line starting `[stall test]`.

## What the numbers were on this machine (an observation, not a failure)
The baseline was never at or under 1.25 s in these runs, so the limit was
always above the old fixed 4.0 s:

| Run | Load (1 min) at start | Baseline | Hung timeout | Limit |
|---|---|---|---|---|
| (a), serial | 4.00 | 1.94 s | 7.75 s | 6.20 s |
| battery baseline | 2.02 | 1.50 s | 5.99 s | 4.79 s |
| S1 | about 2 | 1.59 s | 6.35 s | 5.08 s |
| S3 | about 2 | 1.30 s | 5.18 s | 4.15 s |
| (c), `--parallel 2` | 3.11 | 2.29 s | 9.15 s | 7.32 s |
| the deliberate-load run | 11.36 | 11.5 s | (46 s: over the ceiling) | none: FAILED, "too loaded to judge" |

- **Why the baseline is over a second even when the machine is fairly
  quiet:** it is six renders at once on a fresh worker, so it includes
  the browser's start.
- **What that costs:** the test now lets healthy renders take longer
  beside a hung one than the old test did on the same machine. It does
  not cost the guard: in S1 the four stalled renders took 7.69 to 8.03 s
  against a limit of 5.08 s, because each waited out the hung render's
  6.35 s timeout first. A stalled render is over the limit by
  construction (`test_a_pinned_render_is_over_the_limit_whatever_the_machine`).
- **What a slowdown that is not a stall would look like:** healthy
  renders that got slower beside a hung one by less than 0.8 of its
  timeout pass. That was true before as well (under 4 s of a 5 s
  timeout); the window is now wider on a slow machine.

## What it does not cover
- **The test runs longer:** one more round of six renders and, on a slow
  machine, a longer hung timeout (at most 30 s).
- **A real stall on a machine too loaded to judge reads as "too loaded",
  not as "stall".** It is still a red test with its reason.
- **The baseline and the judged round are separate measurements.** A
  burst of load between them can still fail the test without a stall;
  the limit is 3.2 times the baseline, which is the margin for that.
- **The owning-app run under `--parallel 2` and the serial run use the
  same code;** nothing here is specific to parallel runs.

## Commits
| Commit | What |
|---|---|
| `be1409ee` | The change, the arithmetic tests, the wiring test, the runner |
| `a080db4a` | Base update onto `task/beta-batch-8` `e578e3db` (H-118 in the same file; no conflict) |
| `d0beb906` | v2's pre-read points: the hung render asserted, the wiring test without a browser, mutant S3 |
| `d9396620` | Base update onto `task/beta-batch-9` `cc9682b8` (H-110; no conflict). The gates ran on this tip |

No gate ran before `d9396620`: the SM ruled that H-110, which changes the
renderer this test times, lands first and H-123 is gated once.

## Gates (2026-10-06, each on 0b's GRANT)
Every run's output went straight to a file. These runs hold real-browser
tests with wall-clock assertions, so each started only at a 1-minute load
of 4.00 or under; the load is recorded at the start and the end.

| Step | Result, from the raw log | Load (1 min) start / end |
|---|---|---|
| (a) the changed module, H-118's guard, H-110's driver-stderr module, two renderer modules, the repo-wide guard list; 26 labels, serial | Ran 353 tests in 236.082s, OK, exit=0 | 4.00 / 2.02 |
| (b) 8 mutants | baseline Ran 15 tests in 21.470s, OK; 8 of 8 KILLED; runner exit=0, 123 s | 2.02 / 2.97 |
| (c) the owning app, `assignments`, `--parallel 2`, watchdog | Ran 663 tests in 138.918s, OK (skipped=13), exit=0; stalled=0 | 3.11 / 5.12 |

- **(a):** GRANT 11:47:40; a waiter read the load every 20 s
  (`wait_for_quiet.log`) and started the chain at 11:51:08, the first
  reading at 4.00. Tests skipped for want of Chromium: 0.
- **(c):** GRANT 11:58:55, started 11:59:02, one try. Tests skipped for
  want of Chromium: 0; the 13 skips are the app's usual ones (the same
  count as in H-118's runs).
- **A repro of the old red is not run again.** The failure this row
  answers is in H-118's evidence (`c_assignments_p2_7c2a55f3.raw.log.gz`,
  7.20 s against 4.0 s, with `LOAD_AT_RED.txt`). `StallLimitsTest`
  replays its numbers, and the deliberate-load run below is the live
  check.

## Mutants
The expected failing tests were written by name before any run
(`expected_kills.py.txt`: S1, S2, L1 to L5 on 2026-10-05 17:59, S3 added
23:58; 0b read it at the grant, sha256 prefix `dbaa185b9bdebdde`). Every
mutant's failing set equals its expected set
(`expected_kills_d9396620.txt`).

| Mutant | What is broken | Run against | Failing tests |
|---|---|---|---|
| S1 | the stall itself put back in the renderer: a recycle waits for in-flight renders | the browser class | 1: the stall test |
| S2 | "too loaded" returns instead of failing | the wiring test | 1: the wiring test |
| S3 | the hung render's thread is never started | the browser class | 1: the stall test (its new assertion) |
| L1 | no floor of 5 s | the arithmetic tests | 2 |
| L2 | three times the baseline, not four | the arithmetic tests | 2 |
| L3 | no ceiling | the arithmetic tests | 1 |
| L4 | the healthy share is 1.0 | the arithmetic tests | 4 |
| L5 | exactly the ceiling raises | the arithmetic tests | 2 |

- **S1 was the prediction I was least sure of** (a second failing test
  in the class would have been a finding about those tests). Exactly the
  stall test fails. Its message: two healthy renders at 1.05 s, four at
  7.69 to 8.03 s, limit 5.08 s (`logs/raw/S1.out` in the archive).
- **S1's anchor is in `assignments/pdf_renderer.py`,** which H-110
  changes. After the base update the anchor is found once, and H-110's
  hunks do not touch the recycle branch it mutates.
- **Rules 17 and 18:** `PYTHONDONTWRITEBYTECODE=1` on every run;
  `__pycache__` of the mutated modules' package deleted before the
  baseline, before each mutant and after each restore; each inner run
  writes to its own file with stdin from the null device; a kill needs
  the run's own "Ran" line and named failing tests. Every restore equals
  the commit's blob by sha256.
- **A database is left behind:** the runner passes `--keepdb`, so
  `test_h123_mut` stays on the local server after the battery.
- **The battery is on the final test module:** the module is blob
  `f08e88cc` at `d0beb906` and at `d9396620`.

## The deliberate-load run (the SM's extra run)
`load_h123.sh d9396620`: the stall test alone, after 60 seconds of 12
niced busy loops on this 8-core machine. 0b's GRANT 12:02:47, agreed with
the other project's manager beforehand; one run; either outcome was to be
a result.

- **Outcome: FAILED, "too loaded to judge".** Ran 1 test in 12.020s,
  FAILED (failures=1), exit=1. The message: "the slowest healthy render
  took 11.5 s on its own, so the hung render would need a 46 s timeout
  (over 30 s): this machine is too loaded to judge whether one slow
  render stalls the others. Run it again on a quiet machine."
- **Load:** 2.83 before the loops, 11.36 at the test's start, 13.70 at
  the end (`load_stall_test_d9396620.load.txt`). All 12 loops were
  stopped; none was left.
- **No `[stall test]` line in this run:** the line is printed after the
  limits are worked out, and the test failed at that step, before a hung
  render was started.
- **What it shows:** the ceiling at work. A machine too slow to judge
  gives a red test that says why, not a false "stall" and not a green.
- **What it does not show:** a pass with a stretched limit under heavy
  load. The loops ran at nice 5 and the test at nice 10, so the test got
  less of the processor than in the real case of 2026-10-05 (healthy
  renders at 5.6 to 7.2 s under a load of 22). The stretched limit
  passing under real load is shown by (c): baseline 2.29 s, limit
  7.32 s, green, where the old fixed 4.0 s limit had less room.
- **Not run again:** one run was granted; a gentler one is the SM's call.

## The credential pattern
This folder, archives opened: 0 URLs with anything in the password
position, 0 encoded ones. One file holds an assignment-form line whose
name contains "pass": `expected_kills.py.txt`, the words "expected but
passed:".

## Files
- Raw logs: `a_modules_d9396620.log.gz`,
  `b_mutation_battery_d9396620.log`,
  `c_assignments_p2_d9396620.raw.log.gz` (the evidence) with its stamped
  copy `c_assignments_p2_d9396620.log.gz` (a convenience) and
  `c_assignments_p2_d9396620.load.txt`; `iso.status` (the result line of
  (c)).
- `battery_d9396620.tar.gz`: `results.tsv`, the short log per mutant, and
  `logs/raw/*.out`, each inner run's whole output.
- `chain.status` (times and load of (a) and (b)), `wait_for_quiet.log`,
  `expected_kills_d9396620.txt`.
- The load run: `load_stall_test_d9396620.log.gz` (raw) and
  `load_stall_test_d9396620.load.txt`.
- Scripts as run: `chain.sh.txt`, `c_h123.sh.txt` with `iso_file.sh.txt`,
  `load_h123.sh.txt`, `expected_kills.py.txt`; `wait_and_run.sh.txt` and
  `run_c.sh.txt` are the small starters that waited for the load.
