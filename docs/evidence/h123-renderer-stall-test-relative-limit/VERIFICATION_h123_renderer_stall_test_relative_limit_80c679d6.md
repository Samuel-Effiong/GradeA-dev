# Verification: H-123, the renderer's stall test takes its limit from the machine it runs on (d5)

- **Branch:** task/h123-renderer-stall-test-relative-limit at **80c679d6**, on task/beta-batch-9 cc9682b8 (H-110 merged). For batch 9. The code tip is d9396620; fe41c8d9 and 80c679d6 add only docs/evidence/h123-renderer-stall-test-relative-limit/.
- **The change:** test-only. `assignments/tests_pdf_renderer.py` (+176/-25) and a mutation runner in the evidence folder. No production code, no migration, no settings change.
- **Verifier:** v2 (independent), 2026-10-06. One slot from 0b, 12:27:40 to 12:29:39 WAT, load 3.40 at the start and 2.65 at the end (1-minute).
- **Verdict:** **VERIFIED-WITH-NOTES**

## The change, as read
- **Before:** the hung render got a 5-second timeout and the six healthy renders beside it had to finish within 4.0 seconds, on any machine.
- **After:** the test first renders the same six healthy documents alone and takes the slowest as its baseline. The hung render's timeout is the larger of 5 seconds and four times the baseline; the limit for the healthy renders is 0.8 of that timeout. Over a 30-second timeout (a baseline over 7.5 s) the test fails with "too loaded to judge". It does not skip and it does not pass.
- **Why a stretched limit cannot pass a stall:** a render stuck behind the hung one waits until the hung one gives up, which is its whole timeout, and the limit is always under that timeout. v2 read the renderer's recycle branch to check this: with the stall put back, the fourth render to arrive waits under the swap lock until nothing is in flight.
- **Two additions after v2's pre-read (d0beb906):** the test asserts that the hung render ran and gave up; the "too loaded" wiring test needs no browser.
- **Rule 14:** the one mock returns real numbers and an empty list.

## d5's gates, read by v2 from the raw logs (not repeated, rule 15)
| Gate at d9396620 | The raw log, as committed |
|---|---|
| (a) the changed module, H-118's guard, H-110's module, two renderer modules, the repo-wide guard list; serial | Ran 353 tests in 236.082s, OK, exit=0. No test skipped for want of Chromium |
| (b) 8 mutants | baseline Ran 15 tests, OK; 8 of 8 killed. Every inner run has its own "Ran" line; each failing set equals the list written beforehand |
| (c) the owning app, assignments, `--parallel 2` | Ran 663 tests in 138.918s, OK (skipped=13), exit=0; the watchdog saw no stall |

- **The list of expected failing tests** in the evidence is byte-identical to the file in d5's run folder, whose clock is 2026-10-06 00:00, before any run of H-123.
- **The battery is on the final test module:** blob f08e88cc at d0beb906, d9396620 and 80c679d6.
- **Nothing but evidence after the code tip:** d9396620..80c679d6 changes no file outside the evidence folder. v2's run was at fe41c8d9; 80c679d6 adds the second load run's evidence only, so the run stands for it.
- **Credential shapes** in the branch's files, archives opened: none. Two prose lines hold the words "passed:".

## A real stall is still caught
- **d5's S1** puts the stall back in the renderer. v2 read its raw output: two healthy renders at 1.05 s and four at 7.69 to 8.03 s, against a limit of 5.08 s and a hung timeout of 6.35 s. The four stalled renders each waited out the whole timeout, as the design assumes.
- **v2's W4** tries the same on a slow machine, with real timing: the stall put back and the baseline forced to 2.5 s, so a 10-second timeout and an 8-second limit. The stall test fails: two healthy renders at 1.35 s and four at 11.66 to 12.01 s, each over the 8-second limit after waiting out the 10-second timeout.
- **v2's W5** is the control: the same forced numbers and no stall. The stall test passes, as v2 predicted in writing: giving the hung render longer does not slow the healthy ones.

## v2's run (12:27:40 to 12:29:39, at fe41c8d9)
Expected results were written in the runner by test name before any run; 0b read the runner and the script before the grant.

| Step | Result |
|---|---|
| Baseline: the stall test, the wiring test, the six arithmetic tests | Ran 8 tests in 9.729s, OK. The stall test printed: baseline 1.58 s, hung timeout 6.31 s, limit 5.05 s |
| W1 the hung document finishes | **KILLED** by the stall test, on its new assertion ('finished' is not 'gave up'). Ran 1 test in 3.185s, FAILED (failures=1) |
| W2 the measured baseline is not used | **KILLED** by the wiring test (`test_a_machine_too_loaded_to_judge_fails_the_stall_test_and_says_why`). Ran 1 test in 0.021s, FAILED (failures=1) |
| W3 the hung render gets the default timeout (predicted to survive) | **SURVIVED**, as predicted. Ran 1 test in 33.107s, OK |
| W4 the stall put back, on a slow machine | **KILLED** by the stall test: 12.01 s against the 8.0 s limit. Ran 1 test in 13.969s, FAILED (failures=1) |
| W5 a slow machine and no stall (predicted to survive) | **SURVIVED**, as predicted. Ran 1 test in 13.057s, OK |

- **Rules 16, 13, 12** wrap both steps. **Rule 17:** `PYTHONDONTWRITEBYTECODE=1` and `python -B`; `__pycache__` of both mutated modules' directories deleted before the baseline, before each mutant and after each restore; every restore equals the commit's blob by sha256. **Rule 18:** every inner run wrote straight to its own file with stdin from the null device.
- **Load:** 1-minute load 3.40 at the baseline's start, 4.54 at its end and at the mutants' start, 2.65 at the end. The script would not have started over 4. Nothing else of the team's ran beside it; the other project's test seats were paused.

## What the evidence shows, and what it does not
1. **A real stall is still caught: shown.** S1 at the 5-second floor and W4 at a stretched limit.
2. **No false red under load: shown up to a load of about 10 to 13, in one run.** (c) passed at a load of 3 to 5 with a baseline of 2.29 s and a limit of 7.32 s. The second deliberate-load run (8 busy loops at the test's own niceness) passed at a 1-minute load of 10.43 at the start and 13.32 at the end: Ran 1 test in 20.693s, OK, with a baseline of 3.46 s, a 13.85-second timeout and an 11.08-second limit. v2 read the raw log and the load file. d5's expected outcome was written first (file clock 12:07:33, before the grant of 12:29:52) and held, except that the load was higher than the 7 to 9 d5 aimed for. It is one run, and a passing run prints its limits, not how long the healthy renders took beside the hung one.
3. **The first deliberate-load run** (load 11 to 14, the loops ranked above the test) ended red with "too loaded to judge" at a baseline of 11.5 s. It shows the ceiling at work.
4. **On this machine the limit was always looser than the old one.** The baseline was 1.30 to 2.29 s in every run, never 1.25 s or under, so the limit was 4.15 to 7.32 s, not 4.0 s. The reason is that the baseline is six renders at once on a fresh worker and includes the browser's start. d5 states this in the evidence.

## Notes
- **N1 (for the package).** Point 4 is a cost of the design, not a defect: healthy renders that get slower beside a hung one, without being stuck behind it, have more room to pass than before. The stall itself is caught at any limit.
- **N2.** A real stall on a machine too loaded to judge reads as "too loaded", not as "stall". It is still a red test that says why.
- **N3 (W3).** Nothing checks which timeout the hung render is given: with the default 30 seconds in place of the worked-out one the test still passes. This is the harmless direction. The limit stays the worked-out one, so a stall would still be far over it. No action asked.
- **N4 (not new with H-123).** The hung render is started first, but nothing proves it is in flight before the fourth healthy render arrives. If it were late, a stall could pass. S1 failing in d5's run and W4 in mine show the order held here.
- **N5.** The baseline and the judged round are separate measurements; a burst of load between them can still fail the test without a stall. The margin is 3.2 times the baseline.
- **N6.** d5's battery leaves the database `test_h123_mut` on the local server (`--keepdb`); 0b drops it.

Files: `~/Documents/Projects/GAP-v2-handover/` `vf_h123_mutants.py`, `vf_h123_run.sh`, `runs/h123_fe41c8d9_baseline.log`, `runs/h123_fe41c8d9_mutants.log`, `runs/h123_fe41c8d9.status`, `runs/h123_fe41c8d9_mutant_logs.tar.gz` (each inner run's whole output).
