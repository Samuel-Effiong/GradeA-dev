# H-107: a parallel test run that exits silently and then hangs: both halves fixed in the test runner

**Author:** d5. **Branch:** `task/h107-pool-worker-sigterm`, off
`task/beta-batch-7` `085adecd`. Bundle 7. Test runner only: no production
code, no migration, no settings change.

The full record of how it was found, with every statement marked observed,
demonstrated or inferred, is `H107_RECORD.md` in this folder.

## The change (4 points)
1. **Before:** a run of the suite with `--parallel` whose output went
   through a pipe could end with no failure, no summary and no exit: all
   tests reported so far ok, then silence for ever. H-91's regression did
   it twice on 2026-10-02 and twice on 2026-10-05. Two defects, one after
   the other:
   - **B, the parent stops.** The run's output pipe is shared by the
     parent, the workers and their children. The PDF renderer's Playwright
     driver (Node) inherits it as stderr and leaves it non-blocking for
     every holder while it lives. With the reader behind and the pipe
     full, the parent's write of a test's name raised `BlockingIOError`
     out of Django's result loop; the traceback could not be printed to
     the same pipe.
   - **A, the exit hangs.** The runner turns SIGTERM into SystemExit in
     the main process (`AutoGrader/redis_test_hygiene.py`, from
     `d50aa744`, 2026-09-25; on beta `141c8031` and on the epic), and
     forked pool workers inherit that handler. The exiting parent
     terminates its pool; a terminate that reaches a worker in the middle
     of a test is caught by unittest's catch-all and recorded as a test
     error; the worker lives on and waits for ever on the task queue's
     lock, which the parent holds while it waits for the worker.
2. **After, A** (`AutoGrader/redis_test_runner.py`): the suite class sets
   its own `init_worker`, which restores SIGTERM's default action and then
   calls Django's initializer unchanged. Django runs it in every pool
   worker, forked or spawned. (`process_setup`, the documented hook, runs
   in spawned workers only; it restores the action too.) A terminated
   worker dies.
3. **After, B** (`AutoGrader/testing/patient_stream.py`, new): the runner
   reports on a stream that waits when a write would block and carries on
   from the byte it stopped at: nothing lost, nothing twice, order kept.
   It writes the encoded text to the file descriptor itself, because
   Python's text layer does not know how much went out after a failed
   flush. A reader that has gone is `BrokenPipeError` at once; a reader
   that takes nothing for 120 seconds ends the write with `OutputNotRead`.
4. **Tests:** `AutoGrader/tests_pool_worker_sigterm.py` and
   `AutoGrader/tests_patient_test_stream.py`.

## What it does not cover
- **The cause of the non-blocking pipe.** That is in the renderer, where
  Playwright is started with the process's own stderr: H-110 (ed). The SM
  asked whether the tests' driver could be started without the runner's
  stderr from the runner alone: no. Playwright takes stderr from the
  process at the moment `assignments/pdf_renderer.py` starts it, and the
  tests use that production class.
- **Writes that do not go through the runner's result stream.** Django's
  own few prints to stdout (creating and destroying test databases, "Found
  N tests"), a test that prints, and a worker's log lines still meet the
  pipe as it is. A lost log line is invisible; a print inside a test would
  be that test's error. In the
  slow-pipe diagnostic below, about 1,090 of the run's 2,582 lines were
  lost this way, silently.
- **A stuck reader's message.** If the reader is stuck, `OutputNotRead`'s
  own traceback goes to the same pipe and may not be seen. The run still
  ends, non-zero, instead of hanging.
- Rule 18 stays: a gate's output goes straight to a file, and parallel
  runs carry the watchdog. These fixes do not retire it (SM).

## Why 120 seconds
`PATIENCE` is per wait, with the reader taking nothing at all. It is longer
than any pause of a live reader on a loaded machine, and shorter than the
five minutes of silence after which the gate's watchdog steps in, so a run
with a stuck reader ends itself, with a reason, first. A slow reader that
keeps taking a little is never given up on (tested).

## `init_worker` is not a documented hook
It is a class attribute of Django's `ParallelTestSuite` (5.2.6). Two pin
tests fail with a plain message if Django no longer has it or if ours is
not the one in use, and the main test goes through Django's own
`ParallelTestSuite.run`, so an upgrade cannot drop the fix silently.

`_worker_setup` (H-65) running for spawn only is not a gap in H-65: its
Beat-lock isolation is installed in the main process before the fork and
inherited (H-65's own docstring says so). SM, 2026-10-05: no row.

| Commit | What |
|---|---|
| `e487b97a` | tests for A (red) |
| `380b97db` | A: a pool worker starts with SIGTERM's default action; the mutation runner |
| `de91a6a0` | tests for B (red) |
| `f3c7ee13` | B: the result stream waits; eight more mutants |
| `c3c10348` | test-only: the stream tests' helper reader reads through its own descriptor |
| `9a4bcd1f` | evidence for the gates at `c3c10348` |
| `6370f662` | test-only, after 1a's verification: a wiring test that tells `sys.stderr` from `sys.__stderr__`; mutant P9 |

## Gates
On the frozen tip `c3c10348`, 2026-10-05, under 0b's grants. Status:
`chain.status`, `iso.status`. Rule 18: every run wrote straight to a file
with stdin from `/dev/null`. The stated exceptions are the three
diagnostic runs in the next section; they are not gates.

| Gate | Result | Log |
|---|---|---|
| Repro: A's tests at `e487b97a` (h78-repro worktree) | RED as expected: 4 tests, failures=3 | `repro_e487b97a.log` |
| Repro: B's tests at `de91a6a0` | RED as expected: 13 tests, errors=13 | `repro_de91a6a0.log` |
| (a) eleven modules | GREEN: 127 tests, OK | `a_modules_c3c10348.log` |
| (b) battery (`test_h107_mut`), 12 mutants | 12/12 killed, restore verified; every mutant's failing tests are the expected set | `b_mutation_battery_c3c10348.log`, `battery_c3c10348/`, `expected_kills.py.txt` |
| The two new modules 15 times in a row | 15 of 15 green | `flake_c3c10348.log` |
| (c) part 1: the AutoGrader app + the four sweep/schema guards, serially | GREEN: 517 tests, OK | `c1_autograder_serial_c3c10348.log.gz` |
| (c) part 2: billing + users, `--parallel 2`, watchdog | GREEN: 2748 tests, OK (skipped=4), no stall | `c2_billing_users_p2_c3c10348.log.gz` |

**The repro of A is the defect itself, inside the suite.** At `e487b97a`
the main test starts a real pool from our suite class in a fresh
interpreter, lets Django's own loop terminate it while the worker is
inside a test, and the probe prints "THE WORKER SURVIVED THE TERMINATE".

**The repro of B is weaker, and I say so.** At `de91a6a0` all 13 tests
error because `AutoGrader.testing.patient_stream` does not exist yet: an
import error, not a behavioural failure. The behaviour is shown two other
ways: the `BlockingIOError` observed in the real run of 2026-10-05 13:14
(`H107_RECORD.md`), and the control test at the tip,
`test_the_condition_is_real_a_plain_stream_raises`, which shows a plain
stream raising on the same kind of pipe the other tests write through.

(a)'s modules: the two new ones; `tests_redis_hygiene`,
`tests_redis_hygiene_databases`, `tests_redis_test_isolation`,
`tests_beat_locks`, `tests_network_guard`; and the guards that read the
runner or scan `AutoGrader/testing/` as production:
`tests_no_wildcard_invalidation`, `tests_cache_invalidation_coverage`,
`tests_management_commands_are_commands` and H-80's log guard
(`billing.tests.test_logs_carry_no_email`).

(c)'s scope is the SM's: the change is to how parallel workers start and
to the stream the runner reports on, so the AutoGrader app (which holds
the runner's own tests and the repo-wide guards) serially, and one
parallel run of two large apps. Bundle 7's strict full run is the wider
check.

**The first run at `f3c7ee13`, and a flaky test of mine.** The same gates
were green at `f3c7ee13` (127 OK, 12/12). Reading that battery's logs I
found that under mutant W3, besides the test W3 is meant to break, an
unrelated test had failed: `test_it_gives_up_loudly_after_its_patience_
not_never`, whose pipe nobody reads. The test before it left a reader
thread that could still be running when the test closed its read end; the
next test's new pipe can be given the same descriptor number, and the old
reader then read it. `c3c10348` (test-only) makes the helper reader read
through a descriptor of its own (a dup) and close it itself, and makes the
slow-reader test close its stream and join its reader before it ends.
Then the gates ran again and the two new modules ran 15 times in a row.
Fifteen green rounds make such a race unlikely, not impossible; what the
fix removes is structural: no shared descriptor number, and no reader
outliving its test. `battery_f3c7ee13/` is kept.

**Every mutant's failing tests are the expected set** (0b's condition:
an unrelated second failure hides a flaky test). I wrote the sets down
before the run (`expected_kills.py.txt`). Eleven of twelve matched; the
twelfth was my expectation: I had listed a fifth test for P1 that P1 does
not break (its waiting is done by flush, which is P4's subject). The
script has the reason beside it. At `f3c7ee13` two sets differed, both
through the helper fault above.

**How the runs were made.** `--settings=settings_worktree`, an empty
`EXEMPT_EMAIL_DOMAINS`, `systemd-inhibit --what=idle:sleep:handle-lid-switch
… --mode=block systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0
nice -n 10 timeout -k 60 1800` (rules 12, 13, 16). (c) under `flock
~/.machine-fullsuite.lock`, `--verbosity 2`, `PYTHONFAULTHANDLER=1`, the
300 s watchdog; timestamps added from the side by a reader of the log
file. Rule 17: the battery and its baseline ran with
`PYTHONDONTWRITEBYTECODE=1`, and the runner deleted `__pycache__` in each
mutated module's package before the baseline, before each mutant and after
each restore.

## After verification: 1a's surviving mutant, folded (rule 15.4)
1a's verification at `9a4bcd1f` found a mutant my 13 stream tests did not
kill (1a's Y10): the runner wrapping `sys.__stderr__` in place of
`sys.stderr`. My wiring test compared the stream with `sys.stderr`, which
in an ordinary run is the same object, so it could not tell them apart.
1a's own probe killed it. The SM ruled FOLD before the merge.

`6370f662` (test-only) adds
`test_the_stream_is_whatever_stderr_is_when_the_run_is_set_up`: it
replaces `sys.stderr`, asks the runner for its arguments and expects the
stream to wrap the replacement. The battery gains that mutant as **P9,
which is 1a's Y10**. No change to `AutoGrader/redis_test_runner.py` or
`AutoGrader/testing/patient_stream.py`.

| Run at `6370f662`, 2026-10-05 14:50 | Result | Log |
|---|---|---|
| The two new test modules | GREEN: 18 tests, OK | `t_modules_6370f662.log` |
| The whole battery, 13 mutants (`test_h107_mut`, rule 17) | 13/13 killed, restore verified; every mutant's failing tests are the expected set | `b_mutation_battery_6370f662.log`, `battery_6370f662/` |

The expected sets were written down before the run
(`expected_kills.py.txt`): P9 fails exactly the new test; P7 (the wiring
removed) now fails two, the old wiring test and the new one; the other
eleven as at `c3c10348`. All thirteen matched.

**This battery is on the final test module** (the SM's addendum to rule
17, 2026-10-05): the last change to either test module is `6370f662`, and
the battery ran at `6370f662`. The battery at `c3c10348` in the Gates
table predates that change; it is kept in `battery_c3c10348/` and is
superseded by this one for the mutants table below. The repros, (a) and
(c) ran at `c3c10348`; since then only one test module and the mutation
runner have changed.

## The slow-pipe diagnostics: B's fix against the real fault
Stated exceptions to rule 18 (SM, 2026-10-05), and not gates: runs whose
output deliberately goes through a pipe to a slow reader, with the PDF
renderer's tests in the list, `--parallel 2`. Eight labels:
`assignments.tests_pdf_renderer`, `tests_pdf_renderer_gevent`,
`tests_upload_batch_billing`, `tests_upload_batch_scale`,
`tests_upload_task_retry_policy`, `users.tests_renderers`,
`AutoGrader.tests_beat_locks`, `AutoGrader.tests_cache_invalidation_coverage`.
Line 1 of each log says what it is. Logs, scripts and my written
prediction are in `diagnostics/`.

| Run | Runner | Reader | Result |
|---|---|---|---|
| 14:01 | fixed, `c3c10348` | one line per 2 ms | Ran 139 tests in 101.5 s, OK. **Proves little:** the reader was faster than the run writes (about 20 lines a second), so the pipe very likely never filled. I chose the form without doing that arithmetic. |
| 14:23, the control | **unfixed**, `085adecd` | one line per 100 ms | **No "Ran" line.** 77 tests reported, all ok, the last at 14:25:28; then silence; ended by the watchdog at 14:30:55, exit 134. The parent is in interpreter exit, joining its pool: the stall's known shape. |
| 14:31 | fixed, `c3c10348` | one line per 100 ms | **Ran 139 tests in 97.7 s, OK**, exit 0. The stamped log has 1,490 lines of the 2,582 the run writes. |

**What the pair shows (the SM's two signs, set before the run).** (1) The
same form, minutes apart: the unfixed runner leaves its result loop
silently and hangs; the fixed runner completes. (2) In the fixed run about
1,090 lines are missing, and a line is only refused when the pipe is full
and non-blocking, which is the condition that breaks the unfixed parent.
Both hold, so the run counts as having exercised B. The parent's own lines
all arrived: 139 results and the summary.

**What it does not show.** That the parent waited is not observed from
inside the run (that would need a system-call trace, which changes the
timing and hid the stall on 2026-10-05 12:15; the SM ruled no strace). And
the 1,090 lost lines are the limit named above: worker log lines and
tracebacks, lost without a trace. That is H-110's to remove.

**Against my prediction** (`diagnostics/DIAG_PREDICTION.md`, written at
14:07, before either run): the control's outcome was as predicted; its
fault came at about +120 s, not +80 s, because the database set-up's own
lines went through the slow reader first. The fixed run's time, predicted
100 to 130 s, was 97.7 s. Its line count, predicted about 2,100, was
1,490: I had counted the reader's drain after the tests twice. Before
that, I had told 0b the time would stretch to four or five minutes; that
was wrong, and I corrected it before the run.

(The commit hooks trimmed trailing whitespace and the final newline in two
captures, `D4_full_p2_observe.observe` and
`diagnostics/diag_slow_pipe_100ms_control_085adecd.tree.txt`; nothing else
in them differs from the originals in `GAP-d5-runs/`.)

## Mutants
The battery at `6370f662`, on the final test modules.

| Id | Guards | Result |
|---|---|---|
| W1 | every worker gets the default action back (the pool initializer) | KILLED, `FAILED (failures=1)` |
| W2 | our suite starts its workers through our initializer | KILLED, `FAILED (failures=2)` |
| W3 | a spawned worker's setup gives the default action back too | KILLED, `FAILED (failures=1)` |
| W4 | the action is the default one (die), not ignore | KILLED, `FAILED (failures=2)` |
| P1 | a write that would block waits and carries on (no retry: it raises) | KILLED, `FAILED (failures=2, errors=2)` |
| P2 | it carries on from the byte it stopped at (nothing twice) | KILLED, `FAILED (failures=3)` |
| P3 | what the stream itself still holds goes out first | KILLED, `FAILED (failures=1)` |
| P4 | a flush that would block waits too | KILLED, `FAILED (errors=1)` |
| P5 | a reader that takes nothing ends the wait (no bound: a new hang) | KILLED, `FAILED (failures=1)` |
| P6 | the default patience is 120 seconds | KILLED, `FAILED (failures=1)` |
| P7 | the runner reports on the patient stream | KILLED, `FAILED (failures=2)` |
| P8 | a stream with no file descriptor is written to as it is | KILLED, `FAILED (errors=1)` |
| P9 | the stream wraps sys.stderr as it is when the run is set up (1a's Y10) | KILLED, `FAILED (failures=1)` |

## For the verifier
- The main test of A runs a probe in a fresh interpreter (a pool cannot be
  started from inside one of `--parallel`'s own workers). Under the fix it
  takes a second or two; without it, about twelve, by design.
- P5 (the bound removed) makes its test wait ten seconds, by design.
- B changes what the parent of EVERY run reports through, serial runs too.
  Worth a probe: a run with `--buffer`, with `--debug-sql`, and with
  stderr a terminal.
- `PatientStream` passes every other attribute to the stream it wraps, and
  writes as it is to a stream with no file descriptor.
- H-110 (ed) is the fix at the cause; its test of the pipe's mode lives
  there, once.
- Rule 15: please don't repeat (c).
