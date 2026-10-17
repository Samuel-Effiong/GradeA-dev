# Verification: H-107: a parallel test run that exits silently and then hangs @ 61307784

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-05.
**Branch:** `task/h107-pool-worker-sigterm` @ **61307784** (code tip `6370f662`), on `task/beta-batch-7` `085adecd`. Bundle 7. **Test runner only:** no production code, no migration, no settings change.
- Fix A: `e487b97a` (tests), `380b97db` (a parallel worker starts with SIGTERM's default action)
- Fix B: `de91a6a0` (tests), `f3c7ee13` (the run's result stream waits when a write would block)
- Test only: `c3c10348` (d5's flaky helper fixed), `6370f662` (the wiring test adopted from my verification)
- `9a4bcd1f`, `61307784`: evidence (docs only)

The evidence is in `docs/evidence/h107-pool-worker-sigterm/`.

**I ran at `9a4bcd1f`** in 0b's slot, from my detached scratch checkout, serial, under rule 16's `systemd-inhibit`, the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`; rule 18: output straight to a file, stdin `/dev/null`. **The delta to `61307784` I checked by reading**, citing d5's run of it (SM ruling: no new run of mine unless the delta were more than the one test). Under rule 15 I cite d5's regression (the AutoGrader app serially, 517 OK; billing + users at `--parallel 2`, 2748 OK, skipped=4; at `c3c10348`) and don't repeat it.

**Verdict: VERIFIED-WITH-NOTES.** Both fixes hold on everything I drove. The one gap I found, a mutant of the wiring line that the author's tests did not kill, is folded and closed. Nothing is required before the merge. N1 and N2 answer 0b's two questions; N3 lists what the fix does not cover.

## What the two fixes are
- **A.** The project's runner turns SIGTERM into `SystemExit` in the main process so that a stopped run still cleans Redis. A forked pool worker inherited that. Terminated in the middle of a test, it recorded the signal as that test's error, lived on, and waited for ever; so did the parent. Now each worker starts with SIGTERM's default action and dies.
- **B.** A run's output pipe can be left in non-blocking mode by a child process (the PDF renderer's browser driver; H-110). When the pipe was full, the parent's write of a test's name raised `BlockingIOError`, left Django's result loop, and brought on A. Now the stream the runner reports on waits until the pipe can take more and carries on from the byte it stopped at; after 120 seconds with nothing read it gives up with an error.

## What I checked by running (at 9a4bcd1f)
| Probe | Result |
|---|---|
| **R4 (fix A): a real pool of two forked workers.** Built by Django's own suite code from the project's suite class, in a fresh interpreter, with the runner's real SIGTERM handler in place. One worker idle on the task queue, one inside a test, when the pool is terminated. | **Both die.** The pool joined 1.1 s after the terminate, 0 of the 2 workers alive, 0 errors reported (the busy worker did not record the signal as a test error). d5's own test covers one busy worker; this adds the idle one beside it. |
| **R1 (fix B): text of every byte length.** 3,000 lines with 1-, 2-, 3- and 4-byte characters through a 4 KB non-blocking pipe read late and in small pieces. | **121,890 bytes arrive identical.** A partial write can stop inside a character; nothing is lost or doubled. d5's lines are ASCII. |
| **R2 (fix B): a real unittest run through the runner's own arguments.** 401 tests (one failing) at verbosity 2, reporting on the same kind of pipe, in three modes: plain, `--buffer`, `--debug-sql`. | **Complete in all three.** Every test's line in order, the failure, the "Ran 401 tests" line and `FAILED (failures=1)`; with `--buffer` the failing test's captured output too. 42,718, 42,748 and 71,261 bytes, ten times the pipe and more. `--debug-sql` used Django's own result class. |
| **R2's control** | The same run on a plain stream **raises `BlockingIOError`**, as the parent of the stalled runs did. (Replayed outside Django: after 91 tests, inside unittest's `startTest`.) |
| **R3 (fix B): a terminal.** | Over a pty the stream still says it is a terminal and the line arrives as a terminal shows it. |

## The gap I found, now closed
- **My mutant Y10:** the runner wraps `sys.__stderr__` (the interpreter's original) in place of `sys.stderr` (whatever stderr is when the run is set up). At `9a4bcd1f` it was killed only by my R2, in all three modes. d5's 13 stream tests passed under it: the wiring test compared with `sys.stderr`, and in an ordinary run the two are the same object.
- **What it would have meant:** a run started with stderr replaced would report around the replacement. Small, and not what happened in the stalls; but it is the line that wires fix B into the runner. The SM ruled FOLD.
- **The fold, `6370f662`, test only:** `test_the_stream_is_whatever_stderr_is_when_the_run_is_set_up` replaces `sys.stderr`, asks the runner for its arguments and expects the stream to wrap the replacement. It also asserts the replacement is not `sys.__stderr__`, so it cannot pass by accident. The battery gains P9, which is my Y10.
- **Checked by reading at `61307784`:** `git diff 9a4bcd1f 61307784` outside the evidence folder is that one test (24 lines in `AutoGrader/tests_patient_test_stream.py`). The runner and the stream module are unchanged. In d5's logs, P9 fails exactly the new test (`failures=1`), the two modules are 18 OK, and the battery is 13 of 13 with the restore verified.

## Evidence
| Check | Result |
|---|---|
| **Run** @ 9a4bcd1f: my probes R1–R4 + `AutoGrader.tests_pool_worker_sigterm` + `AutoGrader.tests_patient_test_stream` | **21 tests OK** (20 s): my 4 and d5's 17. No test database was created. |
| **My mutant Y10** @ 9a4bcd1f | Killed by my R2 only (16 tests, failures=3); survived d5's tests. Folded: see above. |
| d5's gates (cited) | Repros red at `e487b97a` and `de91a6a0`; eleven modules 127 OK; 12/12 mutants at `c3c10348`; the two new modules green 15 times in a row; the regression above. After the fold, at `6370f662`: the two modules 18 OK, 13/13 mutants. |
| Hooks | `pre-commit run --from-ref 085adecd --to-ref 61307784` passes (the range; I did not run each commit separately). |
| Merges | `git merge-tree --write-tree` of `61307784` is **clean** against `task/beta-batch-7` at `c823cdca` and against H-89's `830ea9d5`. |
| Nothing left behind | After my runs no process of mine remained; the pool probes stop workers only by their own pids, and none had to be stopped. |

**Rule 17.** Both runs had `PYTHONDONTWRITEBYTECODE=1`, and the mutant was applied with `python -B`. `__pycache__` under `AutoGrader/` was deleted before the baseline, before the mutant and after the restore (the logs show 0 directories each time). The restored `AutoGrader/redis_test_runner.py` matched the commit blob's sha256, and no tracked file was changed afterwards.

**One line in my baseline log that is not a result.** `VF1A-CAPTURED-STDOUT` is printed once on its own: it is the control's failing test, whose name sorts before the 400 others, so it runs before the pipe fills.

**The credential check on my own files** (the widened form, values not printed): the record, the probe, the mutant and the two logs hold no URL with anything in the password position, no assignment form and no percent-encoded form.

## Notes
**N1 (0b's question): giving a worker SIGTERM's default action back loses no Redis clean-up.** By reading, not by a run against Redis.
- The handler exists so that the hygiene teardown runs when a run is stopped. That teardown is the main process's: a forked pool worker never returns into it, with or without the handler, and leaves through `os._exit`.
- Each process writes its test keys under its own pid's prefix. A terminated worker's keys are removed by the main process's end-of-run sweep of dead prefixes, which runs after the pool has joined, and again by the next run's start-of-run sweep.
- The main process keeps its handler; only workers change.

**N2 (0b's question): the 120-second give-up is sure but may be silent.** When nothing reads the pipe for 120 seconds the write raises `OutputNotRead`. That leaves the result loop, so the run ends with a failing exit status, and with fix A the pool no longer hangs. But the error's message and traceback go to the same unread pipe, so the text may never be seen: the exit status is the only sure signal. With the output in a file (rule 18) it cannot happen at all. d5's evidence says the same.

**N3 (what H-107 does not cover; in the evidence).**
- **The cause:** the renderer's browser driver leaving the pipe non-blocking is H-110 (ed).
- **Writes that do not go through the runner's stream** can still raise on a full non-blocking pipe: Django's own prints, a test's print, a worker's log lines, and unittest's echo of captured output for a failing test under `--buffer`. Worker output is lost silently there.
- **The repro of B is weak:** at `de91a6a0` the tests error because the module does not exist. The behaviour rests on the control (a plain stream raises on the same pipe), which my R2 repeats on a real unittest run.
- **The slow-pipe diagnostics** are stated exceptions to rule 18 and not gates; that the parent waited was not observed from inside a run.
- **`init_worker` is not a documented Django hook.** Two pin tests fail loudly if Django stops calling it or ours is not in use; a Django upgrade should expect them.
- **d5's helper race** (a reader thread reading the next test's pipe) is fixed test-only in `c3c10348`. The module ran green in my baseline and again under my mutant; fifteen green rounds make a remaining race unlikely, not impossible.

Logs: `runs/h107_9a4bcd1f.log`, `runs/h107_mutant_Y10_9a4bcd1f.log`. Probe: `h107_probe_tests_vf1a_h107_probe.py`. Mutant: `h107_mutant_Y10.py`.
