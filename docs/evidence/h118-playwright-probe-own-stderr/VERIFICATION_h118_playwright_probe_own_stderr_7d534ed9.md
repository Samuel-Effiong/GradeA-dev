# Verification: H-118, the renderer tests' Chromium probe runs in a child with its own streams (d5)

- **Branch:** task/h118-playwright-probe-own-stderr at **7d534ed9**, off beta 63c3da22. For batch 8. The code tip is 7c2a55f3; 5f2e45df and 7d534ed9 add only docs/evidence/h118-playwright-probe-own-stderr/.
- **The change:** test-only. `assignments/tests_pdf_renderer.py` (+28/-7), the new guard module `AutoGrader/tests_no_playwright_at_import.py` (332 lines), and a mutation runner in the evidence folder.
- **Verifier:** v2 (independent), 2026-10-05. One slot from 0b, 23:53:46 to 23:54:44 WAT, load 8.76 at the start and 8.41 at the end (not timing-sensitive).
- **Verdict:** **VERIFIED-WITH-NOTES**

## The change, as read
- **Before:** importing the module launched Playwright's driver and a browser in the importing process, on that process's stderr.
- **After:** the same launch runs in a child interpreter with stdin, stdout and stderr on the null device; the answer is the exit code. A child that cannot be run or does not end in 120 seconds means "not available".
- **Same answers as before in every case v2 could think of:** no Playwright installed (the child's import fails, non-zero), no browser (the launch fails, non-zero), a working browser (zero).
- **What uses the answer is unchanged:** the diff touches only the probe and two imports.

## A defect in the evidence, found and fixed (docs only)
- **At 5f2e45df** the two committed logs of the owning-app runs were the timestamped copies and were cut short: 12,069 of 12,406 lines and 12,166 of 12,408. Neither held its "Ran 628 tests" line or its result. The red one held no failure block, so the assertion (7.2 s against 4.0 s) was in no committed file.
- **Nothing claimed was false.** v2 read d5's raw files outside the repository and they matched every number in the evidence.
- **At 7d534ed9** the raw logs are committed gzipped. v2 unpacked both blobs: byte-identical to d5's raw files, and each ends with its "Ran" line, result and exit line.

| Run | Raw log, as committed |
|---|---|
| First (c), 17:01 to 17:10, loaded machine | Ran 628 tests in 372.875s, FAILED (failures=1, skipped=13), exit=1. The failure is `test_one_slow_render_does_not_stall_the_others`: 7.20 s against a 4.0 s limit |
| Second (c), 17:49 to 17:52, quiet machine | Ran 628 tests in 150.397s, OK (skipped=13), exit=0 |

- The SM has since made it a rule: the raw log is the evidence, the stamped copy a convenience.

## The red run, weighed
v2 accepts it as load and asked for no third run. Three grounds:
1. The same test passed on the same tip in d5's serial gate (a) at about 16:08, and in the quiet second run.
2. The red run took 2.9 times its usual time on a machine at load 19 to 22 on 8 cores; the second started at load 3.80 and took the time d5 had predicted in writing beforehand.
3. The change moves the import-time probe into a child. It touches no renderer code, and the probe runs once, before any test.

- What stays true, as d5 says: the first run alone cannot tell load from the defect that test guards. The test's fixed 4-second limit is row H-123.

## The source rule's blind spots (v2, on sample strings, no test run)
v2 called `playwright_started_at_import` on thirteen sample sources in plain Python.

| Shape, run at import | The rule |
|---|---|
| A starter imported under another name, then called | **misses** |
| A starter bound to a name by assignment, then called | **misses** |
| A static or class method that reaches a starter, called at import | **misses** |
| `getattr(module, "sync_playwright")()` | **misses** |
| A nested function; `functools.partial`; a lambda called at import; a comprehension; a `try` block at module level; a parenthesised import | catches |
| A helper whose body binds a local named like a starter, called at import | flags (false alarm, safe side) |
| A method sharing its name with a module function that reaches a starter | flags (false alarm, safe side) |
| A helper that patches a starter by a string name | does not flag (right) |

- The fresh-interpreter test would catch all four misses in `tests_pdf_renderer`, as it catches d5's P7. In any other test module nothing would.
- d5 confirmed the six and named them in the evidence at 7d534ed9.

## v2's mutants (beyond d5's eleven)
d5's P1 to P8 do not touch the command the probe runs. Both of v2's do. Each ran against `AutoGrader.tests_no_playwright_at_import`; the expected test was written in the runner before the run.

| Mutant | Result |
|---|---|
| V1: the child is started as some other Python, not this interpreter | KILLED. Ran 11 tests in 9.733s, FAILED (failures=1); the one failing test is the expected one |
| V2: the child's program launches and closes no browser | KILLED. Ran 11 tests in 8.557s, FAILED (failures=1); the one failing test is the expected one |

- The expected test for both: `test_the_child_is_this_interpreter_running_the_launch`. 0b read the runner (sha256 prefix aa08472b4c648129) at the grant; it was unchanged at the run.

- Baseline, the guard module alone: Ran 11 tests in 10.438s, OK.
- **Rules 17 and 18:** `PYTHONDONTWRITEBYTECODE=1`; `__pycache__` of the mutated module's directory deleted before each mutant and after each restore; every restore equal to the commit's blob by sha256. Each inner run wrote straight to its own file, stdin from the null device.
- Run on 7c2a55f3; no code or test file differs at 7d534ed9.

## d5's gates (read, not repeated: rule 15)
| Gate | v2 read in the committed logs |
|---|---|
| Repro at 3d2e6b50 | Ran 11, FAILED (failures=4, errors=2) |
| (a) 25 labels, serial | Ran 340, OK |
| (b) 11 mutants | 11 of 11 KILLED, restores verified; expected sets matched |
| (c) the owning app, two runs | As in the table above |

- **Rule 17 addendum:** the battery ran on the final test modules. Neither has changed since 48cdcbe8.
- v2 cannot confirm from the repository that d5's expected sets were written before the runs: the file first appears in the evidence commit. 0b checked it at the grant.

## The credential pattern (SM rulings of 2026-10-05)
- The evidence folder at 5f2e45df, 21 files, gzip and tar opened to every level: 0 URLs with anything in the password position; 0 literal assignments to a PASS-like name.
- The two raw logs added at 7d534ed9: 0 URLs.
- This record and v2's runner: 0.

## Notes (none blocks)
1. **A row is needed for the rule's four misses** (LOW): follow aliases, methods and `getattr`, with the four shapes as its tests. d5 will propose it. Not folded in here: a change to the test module now would stale the battery for little gain.
2. **The fresh-interpreter test covers one module** and depends on how the installed Playwright starts its driver (`asyncio.create_subprocess_exec`). An upgrade that starts it another way would make that test pass without seeing a start. d5 states this.
3. **The probe's child is stopped after 120 seconds, but its own children are not.** If a launch hangs, the driver and browser it started could outlive the probe. The old probe had no time limit at all, so this is not a regression.
4. **"No red in CI where there is no browser" is by reading** (d5's limit, H-121); v2 read the same and agrees, and did not run it.
5. **Not repeated by v2:** (a), d5's battery, and (c).

## Files
- Runner: GAP-v2-handover/vf_h118_mutants.py. Logs: GAP-v2-handover/runs/h118_7c2a55f3_baseline.log, h118_7c2a55f3_mutants.log, h118_7c2a55f3_mutant_logs/.
