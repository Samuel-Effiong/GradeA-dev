# Verification: H2, the grading benchmark's production guard (ed)

- **Branch:** task/h2-grading-benchmark-guard at **9fb597c4**, on task/beta-batch-5 f4e6e5d3. The code tip is 2fbfdb6f (92bfaa18's code plus the base update); 9fb597c4 adds one log.
- **Commits:** 3f08fc56 (the guard), 62844c67 (tests), e80ae11d (the history tests opt in; the weekly live-QA skip logs at INFO), 92bfaa18 (test-only), 268201a3 (docs).
- **Verifier:** v2 (independent), 2026-10-02.
- **Verdict:** **VERIFIED-WITH-NOTES**

## Static checks
| Check | Result |
|---|---|
| Base updates 52be4ec7, d7303f3b, 68f6464f, e782f70e, 2fbfdb6f: `git show --remerge-diff` | Empty on all five |
| Added-line survival (`vf_merge_survival.py`) | 0 lines lost on all five |
| The last base update, 83fe58ca..f4e6e5d3 | Brings H1's two files only; nothing under ai_processor/ |
| Where the guard sits | The first statement of `Command._resolve_user`, before any import or query |
| Who calls `_resolve_user` | The command's `handle` and `ai_processor.tasks._run` (both beat jobs). `_run` passes no flag, so the beat jobs depend on the setting alone |
| Other paths that create benchmark rows | None. `extraction_benchmark._resolve_user` creates nothing; the isolation harness builds its teacher inside `setup_databases` (a test database) |
| Weekly live job | `live_qa_enabled()` first, then the guard: it needs both switches |
| Rule 14 | No MagicMock. `runner.execute_benchmark` is replaced by a function that raises, so no model is called |
| F1's opt-in (SM ruling) | `CommandHistoryIntegrationTest._run` passes the explicit `--allow-non-debug` flag. It is not a DEBUG override |
| Docs (SM ruling) | `QA_SERVER_SETUP.md` section 10 and `docs/backend/ai-quality-harness.md` name the switch and the flag; "safe anywhere" is gone; both skip rows say INFO |

## The SM's three conditions for not repeating regression (b)
1. **The red log shows exactly the 7 errors and nothing else: holds.** Full log, sha256 prefix 2d66ad497fbd989f (v2 recomputed it; 34,329 lines): `Ran 1280 tests`, `FAILED (errors=7, skipped=6)`.
   - 7 ERROR headers, all `ai_processor.tests_benchmark_history.CommandHistoryIntegrationTest`, and all 7 tracebacks end in `BenchmarkRefused`. No FAIL header.
   - The 6 skips are in the opt-in real-provider and real-connection test modules.
   - The committed file is the full log's last 200 lines, apart from stripped trailing spaces.
2. **The re-run's module set matches the grep: holds.** v2 ran its own, wider grep at 9fb597c4 (the jobs, the command, `ai_processor.tasks`, `_resolve_user`, `BenchmarkRefused`, both switches).
   - It finds the same four modules as `grep_callers.txt`, plus matches that are comments only (`tests_benchmark_golden`, `tests_real_chunked_extraction`), billing's own `live_qa_enabled`, and H1's guard, which ed ran alone at 2fbfdb6f (4 OK).
   - The DEBUG → INFO line can only reach a test that calls the weekly job with live QA off. The only such test outside ed's module is `tests_grading_benchmark.ScheduledTaskTest`, which asserts on the result, not on logs; it passes at the tip (v2's baseline below). The four caller modules hold no `assertLogs` or `assertNoLogs` other than ed's new ones.
   - The four modules ran at 268201a3: **191 OK** with all beta-line guards.
3. **Mutants cover the new level and both skip lines: holds.** G9 and G11 (the live-QA skip at DEBUG, or gone), G10 and G12 (the refusal skip at DEBUG, or not naming the switch): each killed by a named test at 92bfaa18. G1–G12: 12 of 12.

Condition 4 (bundle 5's strict full run stays a hard gate) is 0b's and the SM's.

## The SM's beat-skip check
- A refused beat run writes one INFO line on `ai_processor.tasks` that names `ENABLE_GRADING_BENCHMARK`, and returns the string "Grading benchmark <mode> skipped: not enabled in this environment." Celery records success.
- The `ai_processor` logger's level is `GRADING_LOG_LEVEL`, default INFO, to the console.
- Beat health judges by `PeriodicTask.last_run_at` (Beat's dispatch), not by the task's result, so a skip is never overdue.

## v2 run (0b's grant, one 6G slot, rules 16, 13 and 12, scratch worktree at 9fb597c4, DB test_vf2_s1)
**Rule 17:** every test subprocess ran with `PYTHONDONTWRITEBYTECODE=1`. The `__pycache__` of the mutated modules' directories was deleted before the baseline, before each mutant and after each restore. Each restore was sha-checked against 9fb597c4.

**Baseline: 80 tests OK** (runs/h2_9fb597c4_baseline.log): ed's guard module (11), `tests_benchmark_history` (33), `tests_grading_benchmark` (28) and v2's probe (8).

**Probe (tests_vf2_h2_probe.py), 8 OK:**
- **P1:** with the benchmark teacher already present and no credits, a refused command, a refused nightly run and a refused weekly run each leave every row count unchanged: no plan, no subscription, no bucket. ed's tests start from an empty database.
- **P2:** each of the three skips writes exactly one record, at INFO, on `ai_processor.tasks`, naming the right switch in the line's own text, with no address in the formatted line. The task result is a success.
- **P3:** DEBUG=True does not stand in for `ENABLE_AI_LIVE_QA`: the weekly job still skips and writes nothing.
- **P4:** `--allow-non-debug` with `--teacher-email` runs as that teacher and creates no benchmark teacher or other row.

**Mutants (vf_h2_mutants.py), 7 of 7 killed** (runs/h2_9fb597c4_mutants.log), against ed's two modules and the probe:
| Mutant | Killed by ed's tests | Also by the probe |
|---|---|---|
| V1 guard moved below the named-teacher branch | `test_a_named_teacher_is_refused_too` | no |
| V2 guard moved after the teacher is created | 4 tests | no |
| V2b guard only before the top-up | 4 tests | P1 (3 tests) |
| V3 the beat jobs pass the flag | 2 tests | P1, P2 |
| V4 the weekly live-QA check dropped | `test_the_weekly_live_job_is_skipped_without_live_qa` | P2, P3 |
| V5 the guard reads the live-QA switch | 3 tests | P1, P2 |
| V6 the refusal returns instead of raising | 4 tests | P1, P2 |

Every mutant is killed by ed's own tests, so the probe adds no test ed must adopt.

## Notes (none blocks)
1. **No green ai_processor + AutoGrader regression on the final code.** This is the SM's ruling, on the conditions above. Since the red run, production changed by one line (the DEBUG → INFO log call). Bundle 5's strict full run is the gate.
2. **Deployment (founder, in the bundle 5 package):** outside DEBUG the nightly replay is skipped wherever `ENABLE_GRADING_BENCHMARK` is unset. The staging/QA worker needs it set, or the nightly grading check stops there. It must never be set on production.
3. **Existing production rows (founder):** H2 stops new writes only. Whether production already holds the benchmark teacher and its credits is unknown until the founder runs the read-only detection query.
4. **`--pdf` writes its files before the refusal.** Files only, no database rows. Disclosed in EVIDENCE.md.
5. **Reproduce-first is the weak form:** on the pre-fix code the test module fails at import (no `BenchmarkRefused`). ed says so; mutant G1 stands in for the pre-fix behaviour.
