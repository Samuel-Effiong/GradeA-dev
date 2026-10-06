# Verification: H-110: the PDF renderer's Playwright driver shared the service's stderr @ ba4c84e1

**Verifier:** 1a. **Author:** ed. **Date:** 2026-10-06.
**Branch:** `task/h110-renderer-driver-stderr` @ **ba4c84e1** (code tip `66f76d72`), on `task/beta-batch-9` `74065867` (batch 8 as pushed). For batch 9. MEDIUM. One production file, `assignments/pdf_renderer.py`; no migration, no model change, no settings change.
- `655bdf38` (test first), `c03fd2b6` (the change), `2d9463d6`, `6d5ed3ff` (tests), `267d7c0c` (the "pipe not taken" ERROR once per process)
- `66f76d72`: one test added after my first runs, for the gap I found (below). Test only.
- `45bff75f`: 0b's base update onto `74065867`. Everything else up to `ba4c84e1` is evidence and the backlog (docs only).

The evidence is in `docs/evidence/h110-renderer-driver-stderr/`.

**I ran at two tips**, each time in 0b's slot, from my own detached scratch checkout, serial, under rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`; under rule 18 the output went straight to a file with stdin from `/dev/null`. No test database was created.
- **At `c10ee47d`** (2026-10-06, 00:16 to 00:20 WAT): my probes with the author's two modules, and my mutants Y12 and Y13.
- **At `ba4c84e1`** (10:58 to 11:01 WAT): the author's two modules alone, and Y12 again.
- **The first runs stand for the production file:** `assignments/pdf_renderer.py` is the same blob (`5cb894b3`) at `c10ee47d`, `66f76d72` and `ba4c84e1`, and so is the reader test module. Between the two tips the only change outside `docs/` is the one added test.

Under rule 15 I cite ed's regression (the `assignments` app, serial, 655 OK, skipped=13, at `59b844c4`) and ed's short step on the final base (438 OK, skipped=8, at `45bff75f`), and repeat neither.

**Verdict: VERIFIED-WITH-NOTES.** The fix does what the evidence says on everything I drove, with a real driver that writes as well as on the bytes the author's tests write. I found one gap in the tests, not in the code; it is closed by a committed test, and my run shows that test catches it. Nothing is required before the merge. N1 to N3 are what a reader should know about the evidence; N4 and N5 are for operators.

## What H-110 changes
Playwright starts its Node driver with the calling process's own stderr, and Node sets a pipe it is given non-blocking. That setting belongs to the pipe, so after the first render every process sharing the service's stderr could lose or cut a log line written while the pipe was full, with no error. Now the renderer gives the driver a pipe of its own, and a small daemon thread reads that pipe into the module's logger, one WARNING per line, cut at 2000 characters, control characters replaced. If Playwright does not take the pipe (its private hook renamed), the renderer logs one ERROR per process and renders as before.

## What I checked by running (at c10ee47d)
In all of the author's runs the real driver wrote nothing to its stderr. So no run of theirs shows a real driver's line arriving in the log. My probes add that, a normal exit, and failed starts.

| Probe | Result |
|---|---|
| **A1: a real driver that does write.** A fresh interpreter whose stderr is a pipe the test owns and keeps read; Playwright's own debug output switched on by its environment variable; one render. | **108 driver lines were in the log** when the child reported, a second after the first one arrived (4 on the browser channel, 104 on the protocol channel), 4 of them cut at the limit, none longer than the limit, none with a control character. **0 bytes of it reached the process's own stderr.** The render succeeded, the pipe stayed blocking, Playwright's hook was the original again, and no ERROR was logged. |
| **A2: the control.** The same run with the hand-over switched off in the child. | The defect shows: the process's stderr pipe was **non-blocking**, 9 debug lines were on the process's own stderr, 0 in the log, and the "pipe not taken" ERROR was logged once. So A1 can see the defect when it is there. |
| **A3: a normal exit after a render** (no kill). | The interpreter ended by itself with status 0 in 0.1 s; all 6 processes of the driver's tree were gone; the pipe was blocking; 0 bytes on stderr at exit. |
| **B1: a start that fails after the real driver is up** (Chromium does not launch), ten times. | Each refused with the renderer's own error. Open descriptors 4 before and 4 after; no renderer thread, no child process left; the hook the original. |
| **B2: a start that fails before any driver** (Playwright's start raises), ten times. | The same: 4 and 4, no renderer thread left. |
| **C1 to C3: the reader on input the author's tests do not give it.** | A 2-, 3- and 4-byte character split across two writes arrives whole (6 split points). A carriage return, VT, FF, FS, GS, RS, NEL, the two Unicode line separators and NUL in one driver line give **one** log line, each replaced. A 400,000-byte line is logged once, cut, and the next line follows. |

## The gap I found, now closed
- **My mutant Y12:** the reader thread is not a daemon thread. Python joins every non-daemon thread at exit before the atexit step that stops the driver, and the reader ends only when the driver is gone. So a process that has rendered would never end by itself: a gunicorn worker being recycled, a Celery child at its task limit.
- **At `c10ee47d` it survived all 27 of the author's tests** and was killed only by my A3 (`Ran 35 tests`, failures=1), as I had written down before the run. Their fresh-interpreter tests killed a child that did not end after 30 seconds, without failing.
- **The code was right** (`daemon=True`); no committed test would have noticed the change. The SM ruled (2026-10-06 10:30 WAT) that the gap is closed by a committed test before the merge.
- **The test, `66f76d72`:** `TheProcessStderrStaysBlockingTest.test_a_process_that_has_rendered_still_ends_by_itself`. It closes the child's input after a render and requires exit status 0 inside 30 s, the driver gone inside 10 s and the pipe still blocking. Test only: +28 lines in one file, nothing existing changed.
- **My run at `ba4c84e1` shows it catches Y12.** The author's two modules unmutated: **28 tests OK**. Under Y12: `Ran 28 tests`, **FAILED (failures=1)**, and the one failing test is that test, with its own message; the other 27 passed. That is exactly what I wrote down before the run (`h110_expected_kills_delta.txt`, file time 10:38:52).
- The evidence also mentions a run of this test "by 1c". It has no log and the SM ruled it does not count; it was not mine. Nothing here rests on it.

## Evidence
| Check | Result |
|---|---|
| **Run** @ c10ee47d: my probes + the author's two modules | **35 tests OK** (27 s; wall 34 s): my 8 and the author's 27. Load before: 2.07. |
| **Y12** @ c10ee47d | `Ran 35 tests`, failures=1: my A3 only. Load before: 2.62. See above. |
| **My mutant Y13** @ c10ee47d (a line past the bound on an unfinished line is logged cut every time it passes the bound, not once; not in the author's battery) | **KILLED** (`Ran 35 tests`, failures=2): my C3 and the author's `test_a_line_that_never_ends_is_logged_before_it_ends`. Within what I wrote down before the run; see N2. Load before: 1.80. |
| **Run** @ ba4c84e1: the author's two modules | **28 tests OK** (10 s; wall 18 s). Load read before the start: 3.28; after: 3.19. |
| **Y12** @ ba4c84e1 | **KILLED** by the one named test (`Ran 28 tests in 132.293s`, failures=1). Load read before the start: 3.09; after: 5.54 (N3). |
| ed's gates (cited) | Reproduce-first red on the unchanged renderer (Ran 5, failures=3); the two modules, seven caller modules and the guards 426 OK; 19 of 19 mutants; `assignments` 655 OK; on the final base 438 OK with the new test in it. |
| The battery is on the final test modules, except the new test | The reader test module has not changed since `267d7c0c`, before the battery's tip `b8e9200e`. The other module gained only the new test, which none of the 19 mutants is aimed at; my Y12 is its mutant. |
| No production change after my first runs | `git diff c10ee47d ba4c84e1` outside `docs/` is the one added test. |
| The base update `45bff75f` | Differs from the plain merge in `docs/HARDENING_BACKLOG.md` only (0b kept both sides and renumbered ed's new row to H-126). Nothing else is on neither parent. Against `74065867` the branch differs outside `docs/` in three files: the renderer and the two test modules. |
| Hooks | `pre-commit run --from-ref 74065867 --to-ref ba4c84e1` passes (the range; I did not run each commit separately). |
| Merges | `ba4c84e1` contains `task/beta-batch-9` at `74065867`, so the merge into it brings H-110's own changes only. |
| H-118's guard against H-110's code | No test module of H-110 starts Playwright at import. |
| Rule 14 | No MagicMock in the new tests or in my probe. |

**Rule 17.** Every run had `PYTHONDONTWRITEBYTECODE=1`, and each mutant was applied with `python -B`. `__pycache__` under `AutoGrader/` and `assignments/` was deleted before each baseline, before each mutant and after each restore (the logs show 0 directories each time). After each mutant the restored `assignments/pdf_renderer.py` matched the commit blob's sha256, and no tracked file was changed.

**The form of my logs.** Each of the five holds exactly one "Ran" line and exactly one OK or FAILED line. They are not the last lines. After them come the lines written to standard output, which reaches a file only at exit: Django's three ("Found ... test(s)", "Skipping setup of unused database(s)", "System check") and, in the three logs of the first runs, the lines my probes print. Then my footer: exit status, wall time, load and the restore checks. The SM accepted the two later logs in that shape (2026-10-06).

**Nothing left behind.** My probes only ever stop their own child by its handle, and the driver's tree by process id and start time together; no process of mine remained after any run.

**The credential check on my own files** (the widened form, values not printed): the record, the probe, the two mutants, the two notes written before the runs and the five logs hold no URL with anything in the password position, no assignment form and no percent-encoded form.

## Notes
**N1 (how the author's battery was counted; disclosed by ed, and true).**
- The battery judged each mutant by exit status. The rule that a kill needs the run's own "Ran" line and named failing tests was applied afterwards to what the battery had recorded, with no re-run; each mutant's log has its "Ran 27 tests" line and named failures. The runner was changed afterwards to judge the three ways itself.
- The failing tests expected for each mutant were not written down before the run.
- The battery, the reproduce-first step and the regression ran on the earlier base; only the short step was repeated on the final one. The base updates change none of the three files.
- One mutant log (M16) is not verbatim: a made-up database address in a failure message is replaced by a note, because no address with a password part is committed. The untouched copy is outside the repository.

**N2 (one bound in the reader is pinned only loosely by the author's tests).** The two tests that cover a line longer than the bound write 200,000 bytes. With the pipe read in full 64 KiB pieces the bound is passed only once in 200,000 bytes, so under Y13 they pass; they fail only when the reads fall smaller. In my run one of the two failed and the other passed. My C3 writes 400,000 bytes and fails under Y13 however the reads fall. The cost of a regression here is small (one extra 2000-character WARNING for every 64 to 128 KiB of a line that never ends), so I do not ask for a fold; raising the two tests to 400,000 bytes would pin it.

**N3 (load).** Every run started at a 1-minute load average under 4, read before the start. The load after the second Y12 run was 5.54: the run itself starts four browsers and keeps three children waiting. The kill cannot be an effect of load: a busy machine could make a healthy child miss its 30 seconds, but it cannot make a child that hangs at exit end. And the same tests passed unmutated two minutes before.

**N4 (for operators: what changes in the logs and under pressure).**
- The driver's stderr is now in the log as WARNING lines beginning `[PDF] Playwright driver stderr:`. In every run of the author's tests there were none; with Playwright's debug variable set there are over a hundred per render (A1), so that variable should not be set in a service.
- A line goes through H-89's scrubber like any log line. It can still carry page text and addresses of pages; the scrubber removes email addresses and credentials in URLs, not everything.
- The reader is not rate-limited (row H-126, LOW).
- The trade, in the SM's wording in the evidence: with a full log pipe a writer waits for the collector again instead of losing lines. That is how main behaves today.
- An ERROR "did not take the renderer's stderr pipe" after a Playwright upgrade means the old behaviour is back; the pin test fails first.

**N5 (what H-110 does not cover; in the evidence, or by my reading).**
- **A private Playwright function** is replaced for the length of one start. It is replaced for the whole process: another Playwright start in another thread during that window would be handed the renderer's pipe. No service code does that; the one other starter in the tree is the offline benchmark tool.
- **The offline benchmark tool** (`ai_processor/benchmark/render.py`) still starts a driver on its own stderr.
- **How the hosting platform wires stderr**, and whether its collector ever lets the pipe fill, is not measured; the SM has put it to the founder.
- **A failed start is cleaned up twice over** (the `finally` after the start, and the shutdown). My B1 and B2 show that nothing leaks; they do not show that each of the two would be enough alone.
- **The fresh-interpreter tests need a headless Chromium** and are skipped where there is none; they and one reader test read `/proc` (Linux only).

**N6 (rollback).** Code only; no step. After a rollback the driver shares the service's stderr again from the first render on.

Logs: `runs/h110_c10ee47d.log`, `runs/h110_mutant_Y12_c10ee47d.log`, `runs/h110_mutant_Y13_c10ee47d.log`, `runs/h110_delta_ba4c84e1.log`, `runs/h110_delta_mutant_Y12_ba4c84e1.log`. Probe: `h110_probe_tests_vf1a_h110_probe.py`. Mutants: `h110_mutant_Y12.py`, `h110_mutant_Y13.py`. Written before the runs: `h110_expected_kills.txt` (00:03:29), `h110_expected_kills_delta.txt` (10:38:52).
