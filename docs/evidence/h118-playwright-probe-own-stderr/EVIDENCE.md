# H-118: the renderer tests' Chromium probe runs in a child with its own streams

**Author:** d5. **Branch:** `task/h118-playwright-probe-own-stderr`, off
beta `63c3da22`. For batch 8. **Verifier:** v2. Test-only: two test
modules and a mutation runner. No production code, no migration, no
settings change.

Source: ed's reading for H-110, confirmed by d5 (H-107's record).

## The change (4 points)
1. **Before:** `assignments/tests_pdf_renderer.py` asks, when it is
   imported, whether a headless Chromium can be launched, so that the
   real-rendering tests can skip where there is none. It asked by
   starting Playwright's driver (Node) and a browser inside the importing
   process. Playwright hands the driver that process's own stderr, and
   Node leaves a pipe it is given in non-blocking mode for every process
   that holds it. In a parallel run the importing process is the test
   runner's parent, at discovery: for the length of the probe the whole
   run's output pipe was non-blocking (H-107, finding B). The driver
   exits by itself and the mode goes back, so the window is short, and
   H-107's result stream and rule 18 already cover it; H-110 does not
   remove it, because the probe does not go through the renderer.
2. **After:** the same launch runs in a child interpreter whose stdin,
   stdout and stderr are the null device. The answer is the child's exit
   code. A child that cannot be run, or does not end in 120 seconds,
   means "not available", as any failure of the launch did before.
3. **Unchanged:** what the tests skip on, and every test that uses the
   answer (`tests_pdf_renderer`, `tests_download_pdf`,
   `tests_renderer_crash`, `tests_load`). The probe still runs once, at
   import.
4. **New guard module,** `AutoGrader/tests_no_playwright_at_import.py`,
   three kinds of test:
   - the defect itself: a fresh interpreter imports the module with
     Playwright's way of starting its driver replaced by a recorder, and
     counts the starts attempted in the importing process (must be none);
   - the probe: its child gets the null device for all three streams, is
     this interpreter running the launch, has a time limit, and its exit
     code is the answer;
   - a repo-wide rule, by reading the source: no test module reaches
     Playwright's starters (`sync_playwright`, `async_playwright`) in
     code that runs at import, directly or through the module's own
     functions. 384 test modules are read today.

## What it does not cover
- **The source rule is a reading, not a proof.** It follows calls
  through functions of the same module only. A start reached through
  another module's helper, or through `exec` of a string, is invisible
  to it (mutant P7 is exactly that, and only the fresh-interpreter test
  catches it). The fresh-interpreter test covers one module,
  `assignments.tests_pdf_renderer`, the only one that probes today.
- **The probe still costs one real browser launch per import** of the
  module, now in a child. A spawned parallel worker would probe again;
  forked workers inherit the answer.
- **A probe that times out answers "not available"** and the
  real-rendering tests then skip quietly, as they did before on any
  failure. The gate scripts stop if any test was skipped for want of
  Chromium; nothing in the tree does.
- **CI has no browser** (H-121): there the child fails, the answer is
  "not available" and the real-rendering tests skip, as they do today.
  H-118 does not change that and does not turn CI red: the
  fresh-interpreter test only asserts that the answer is a yes or a no
  and that no driver was started in the importing process, and the other
  tests of the module use no browser. **This is by reading, not by a CI
  run.**
- `ai_processor/benchmark/render.py` starts Playwright too, inside a
  function, and is not a test module; untouched.

| Commit | What |
|---|---|
| `3d2e6b50` | tests (red): `AutoGrader/tests_no_playwright_at_import.py` |
| `48cdcbe8` | the probe in a child (`assignments/tests_pdf_renderer.py`); the mutation runner |
| `7c2a55f3` | the runner only: each mutant's log keeps its "Ran" line; a kill needs that line and named failing tests |

## Gates
On the frozen tip `7c2a55f3`, 2026-10-05, under 0b's grants. Times are
the shell's clock. Before these runs nothing of H-118 had been run under
Django; two things were checked in plain Python, with no test run and no
driver started: the source rule over its own sample shapes and over the
tree (one offender before the change, none after: `plain_check.py.txt`),
and that a refused driver start raises instead of hanging
(`diag_refused_start.py.txt`, `diag_refused_start.out.gz`).

| Gate | Start – end | Result | Log |
|---|---|---|---|
| Repro: the new module at `3d2e6b50` (h78-repro worktree) | 16:05:12 – 16:05:25 | RED as expected: 11 tests, failures=4, errors=2, exit 1 | `repro_3d2e6b50.log.gz` |
| (a) 25 labels, serial, `--verbosity 2` | 16:05:25 – 16:10:44 | GREEN: 340 tests, OK; none skipped for want of Chromium, none skipped at all | `a_modules_7c2a55f3.log.gz` |
| (b) battery (`test_h118_mut`), 11 mutants | 16:10:44 – 16:15:03 | 11/11 killed, restore verified, none BROKEN; every mutant's failing tests are the expected set; each log has its "Ran" line | `b_mutation_battery_7c2a55f3.log`, `battery_7c2a55f3.tar.gz` (raw output of each run in `logs/raw/`), `expected_kills.py.txt`, `expected_kills_7c2a55f3.txt` |
| (c) the owning app, assignments, `--parallel 2`: **first run** | 17:01:50 – 17:10:00 | **RED: 628 tests in 372.9 s, 1 failure**, 13 opt-in skips, none for want of Chromium, no stall. Machine heavily loaded (below) | `c_assignments_p2_7c2a55f3.log.gz`, `LOAD_AT_RED.txt`, `iso.status` |
| (c) the same, **second run**, a stated exception approved by the SM, on a quiet machine | 17:49:30 – 17:52:56 | GREEN: 628 tests in 150.4 s, OK, 13 opt-in skips, none for want of Chromium, no stall. Load average 3.80 at the start, 5.84 at the end | `c_assignments_p2_run2_7c2a55f3.log.gz`, `c_assignments_p2_run2_7c2a55f3.load.txt`, `C_RERUN_PREDICTION.md`, `iso.status` |

**(c) took two runs, and the first stays red in this record.**
- *The first run* failed one test:
  `ConcurrentRenderingTest.test_one_slow_render_does_not_stall_the_others`,
  a wall-clock limit. The slowest of six healthy renders took 7.2 s
  against 4.0 s; all six took between 5.6 and 7.2 s. Its result reached
  the parent at 17:07:41. I RELEASEd at once and re-ran nothing.
- *Observed about the machine:* the same 628 tests had taken 128.8 s at
  12:10 the same day (H-91's tip, same form); this run took 372.9 s, 2.9
  times as long. beside it, 0b ran four whole-tree credential scan passes (not test
  runs) back to back from about 17:03:30 to 17:10:34, their output files
  stamped 17:05:23, 17:07:20, 17:09:01 and 17:10:34, and said so; Vezi, who share the
  laptop, had test runs with their own headless browsers going. One
  minute after the run the load average was 19.2 / 21.6 / 16.0 on 8
  cores (0b read 22.0 / 22.2 / 16.2); the 15-minute figure covers the
  whole run.
- *What that run alone could not show:* whether the limit was missed
  because of the load or because of the defect the test guards (healthy
  renders pinned to the hung render's 5 seconds), which would give the
  same 5 to 7 s. H-118 touches no renderer code, but that is an
  argument, not evidence.
- *The check:* one second run, same tip, same form, on a quiet machine,
  approved by the SM as a stated exception (a red is never re-run
  silently). The expectation was written down before asking
  (`C_RERUN_PREDICTION.md`): about 130 to 170 s of tests and 628 OK if
  load was the cause; a second red on a quiet machine would be a real
  finding, to the SM, with no third run. The script records the load
  average at the start and the end and refuses to start above 4
  (`c_h118_2.sh.txt`). Result: 150.4 s, 628 OK, the timing test passed.
- *So:* the first red is read as the test's absolute 4-second limit
  missed on an overloaded machine. It is not shown to be anything else,
  and it is not hidden. The limit itself is its own row, H-123 (LOW);
  nothing in that test is changed here. From this day a whole-tree
  credential scan takes the slot like a run, and runs with wall-clock
  assertions start only on a quiet machine with the load recorded (SM).

**The repro, plainly.** All six failing tests fail on behaviour; none is
an import error. The fresh interpreter counted one driver start in the
importing process. The source rule named `tests_pdf_renderer.py`. The
old probe ran no child: one test fails on the missing call and two error
reading the call's arguments. And with the driver's start refused, the
old probe answers "not available" where the test expects "available".
Two of the probe's tests (a non-zero exit code; a child that cannot be
run or does not end) pass at the red commit too, because the old probe
also answers "not available" there. They pin the new code, and mutants
P4 and P5 hold them, not the repro.

**(a)'s labels.** `AutoGrader.tests_no_playwright_at_import`; the three
modules that use the probe's answer and are not load tests:
`assignments.tests_pdf_renderer`, `assignments.tests_download_pdf`,
`assignments.tests_renderer_crash`; and the repo-wide guard list of 0b's
grants of the day: `AutoGrader` `tests_no_wildcard_invalidation`,
`tests_cache_invalidation_coverage`, `tests_migration_rollback_defaults`,
`tests_redis_test_isolation`, `tests_beat_health`, `tests_beat_locks`,
`tests_management_commands_are_commands`, `tests_error_messages`,
`tests_no_pii_in_logs`, `tests_log_scrubbing`, `tests_sentry_scrubbing`,
`tests_redis_hygiene_databases`, `tests_redis_hygiene`,
`tests_patient_test_stream`, `tests_pool_worker_sigterm`;
`billing.tests.test_logs_carry_no_email`,
`billing.tests.test_log_scrubbing_end_to_end`;
`classrooms.tests_teacher_access_sweep`,
`classrooms.tests_course_roster_scope_sweep`;
`assignments.tests_schema_extension`, `users.tests_schema_extension`.

**The hard stop.** After (a) and after (c) the gate script counts the
tests skipped with "Headless Chromium not available" and stops if there
is one: with the probe answering "not available" every real-rendering
test would skip and a green would mean nothing. The count was 0 every
time: after (a) and after both runs of (c). (0b made this a condition of the grants.)

**Every mutant's failing tests are the expected set** (0b's standing
check). The sets were written down before any run of H-118
(`expected_kills.py.txt`, 15:42) and all eleven matched.

**How the runs were made.** `--settings=settings_worktree`, an empty
`EXEMPT_EMAIL_DOMAINS`, `systemd-inhibit --what=idle:sleep:handle-lid-switch
… --mode=block systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0
nice -n 10 timeout -k 60 1800` (rules 12, 13, 16). Rule 18: every run,
the battery's inner runs included, wrote stdout and stderr straight to a
file with stdin from the null device; (c) ran under `flock
~/.machine-fullsuite.lock` with `--verbosity 2`, `PYTHONFAULTHANDLER=1`
and the 300 s log-silence watchdog, its timestamps added from the side
by a reader of the log file. Rule 17: the battery and its baseline ran
with `PYTHONDONTWRITEBYTECODE=1`, and the runner deleted `__pycache__`
in each mutated module's package before the baseline, before each mutant
and after each restore. **The battery is on the final test module:**
neither test module has changed since `48cdcbe8` (`7c2a55f3` changed the
runner only). A mutant counts as killed only with its run's own "Ran"
line, named failing tests and no load failure. The gate scripts stop at
the first failed step (`set -euo pipefail`) and assert the frozen tip
and a clean tree first. After the chain no Playwright driver or headless
browser process of these runs was left (counted by pattern; nothing was
killed).

## Mutants
P1 to P8 are in the probe (`assignments/tests_pdf_renderer.py`), G1 to
G3 in the source rule (`AutoGrader/tests_no_playwright_at_import.py`).

| Id | Guards | Failing tests (expected = actual) |
|---|---|---|
| P1 | the child's stderr is its own | 1 |
| P2 | the child's stdout is its own | 1 |
| P3 | the child's stdin is its own | 1 |
| P4 | a child that fails means not available | 1 |
| P5 | a child that does not end means not available | 1 |
| P6 | the child has a time limit | 1 |
| P7 | nothing at module level starts the driver here (a start by `exec` of a string, unseen by the source rule) | 1: the fresh-interpreter test alone |
| P8 | the probe itself does not start the driver here (the old form put back inside it) | 8: the fresh-interpreter test, the source rule, and the six tests of the probe |
| G1 | the rule follows a call through the module's own functions | 1 |
| G2 | the rule does not read the bodies of functions as run at import | 1 |
| G3 | the rule knows the async starter too | 1 |

## For the verifier
- The fresh-interpreter test replaces `asyncio.create_subprocess_exec`,
  which is how the installed Playwright starts its driver
  (`playwright/_impl/_transport.py`). If an upgrade starts the driver
  another way, that test would pass without seeing a start. Worth a
  probe: does the recorder still fire with the installed version when
  the old probe is put back (P8 says yes today)?
- Worth a probe: the source rule's blind spots named above (another
  module's helper; `exec`), and a false alarm it might raise on a module
  that only patches the starters by name.
- The probe's child inherits the environment and the working directory,
  nothing else of the run.
- The new module costs one extra real launch (in the fresh interpreter's
  child): a few seconds per run of that module.
- Rule 15: please don't repeat (c). If you want to weigh the first red
  yourself, both logs and the load record are here; a third run needs
  the SM's word.
