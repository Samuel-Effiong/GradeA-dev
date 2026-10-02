# H2: the grading benchmark's production guard — evidence

Author: ed (Security). Branch `task/h2-grading-benchmark-guard`, on `task/beta-batch-5` 83fe58ca.
Source finding: `docs/evidence/h69-command-audit-survey/SURVEY.md`, H2.

## The problem

`grading_benchmark`'s `Command._resolve_user` creates, in whatever database it runs against:

- an active TEACHER `grading-benchmark@benchmark.local`;
- a `SubscriptionPlan` "Grading Benchmark Plan" (active, 5,000,000 monthly credits);
- an active `UserSubscription` and a `CreditWallet`;
- a 5,000,000-credit `CreditBucket` whenever the balance is under 500k.

Nothing refused a production database. The command was not the only path: the nightly beat job
`ai_processor.tasks.nightly_grading_benchmark_replay` calls `_resolve_user` too, and so does
`weekly_grading_benchmark_live` when `ENABLE_AI_LIVE_QA` is set. So the rows are created in
production by the schedule, without anyone running the command.

## The fix (SM approved: one guard for both paths)

| Commit | What |
|---|---|
| 3f08fc56 | `ensure_benchmark_allowed(allow_non_debug=False)` at the top of `_resolve_user`; raises `BenchmarkRefused(CommandError)` unless `settings.DEBUG`, `--allow-non-debug`, or `settings.ENABLE_GRADING_BENCHMARK`. New setting `ENABLE_GRADING_BENCHMARK` (env, default False). The nightly and weekly jobs catch `BenchmarkRefused`, log one INFO line and return "Grading benchmark {mode} skipped: not enabled in this environment." |
| 62844c67 | `ai_processor/tests_h2_grading_benchmark_guard.py`: 11 tests (command, nightly, weekly, the default). `runner.execute_benchmark` is replaced by a function that raises, so no model is called. |
| e80ae11d | Round 2 (v2's F1 and SM rulings). `tests_benchmark_history`'s `CommandHistoryIntegrationTest` passes `--allow-non-debug` (test-only). `weekly_grading_benchmark_live`'s `ENABLE_AI_LIVE_QA` skip is raised from DEBUG to INFO (one production line). The three skip tests assert exactly one INFO line that names the switch. |
| 92bfaa18 | Round 3, test-only: the skip-line helper reads the line's own text (`record.msg`), which kills G12. |
| 268201a3 | Docs: `QA_SERVER_SETUP.md` section 10 and `docs/backend/ai-quality-harness.md` name `ENABLE_GRADING_BENCHMARK` and `--allow-non-debug`; "safe anywhere" is gone. |
| 4355bfa8, 6e71498f | `mutate.py` (G1–G12) and `grep_callers.txt`. |

Behaviour change to note for the package:

- Outside DEBUG the nightly replay is now **skipped by default**, with one INFO line. An
  environment that wants it (staging) must set `ENABLE_GRADING_BENCHMARK=true`. That is a
  founder env change; it is not done here.
- The weekly live job needs both `ENABLE_AI_LIVE_QA` and `ENABLE_GRADING_BENCHMARK`. Its
  live-QA skip now logs at INFO, not DEBUG.
- Rows already created in production are not touched by this change. A read-only detection
  query (counts, ids, timestamps; no email column selected) was drafted for the founder to run
  via Railway; any cleanup is the founder's action.
- `--pdf` writes its files before the refusal (files only, no rows) (v2's N4).
- Rollback: revert the merge; no migration, no data change.

## Which tests reach the changed entry points

`grep_callers.txt` records the greps (SM condition 2). Four test modules reference the command,
the two beat jobs or `_resolve_user`: `tests_h2_grading_benchmark_guard`,
`tests_benchmark_history`, `tests_grading_benchmark` and `tests_extraction_benchmark_golden`
(a docstring mention only; included anyway). No other test asserts on the `ai_processor.tasks`
logger. Round 1's gate (a) ran only the first and third, which is how F1 was missed.

## Runs

All runs: rules 12, 13 and 16 (`systemd-inhibit` → `systemd-run` MemoryMax scope → `nice` →
`timeout -k 60`), `--settings=settings_worktree`, slots granted by 0b. Mutation runs (rule 17):
the test subprocess ran with `PYTHONDONTWRITEBYTECODE=1`, and the `__pycache__` of each mutated
module's directory was deleted before each mutant and after each restore. Mutants ran on their
own database (`test_h2_grading_benchmark_guard_mut`, dropped afterwards). Every anchor is
asserted unique and every mutant passes `ast.parse`.

### The runs that count

| Run | Tip | Result | Log |
|---|---|---|---|
| Reproduce-first: the H2 tests on 83fe58ca's production files | 268201a3 | FAILED (errors=1): `ImportError: cannot import name 'BenchmarkRefused'` | `prefix_83fe58ca_failing.txt` |
| The four caller modules + all beta-line guards | 268201a3 | 191 tests OK | `modules_and_guards.txt` |
| The touched module after the round 3 test fix (rule 15.4) | 92bfaa18 | 11 tests OK | `r3_module.txt` |
| Mutants G1–G12 | 92bfaa18 | 12 of 12 killed, no survivors | `mutation_log.txt`, `mutation_results.json` |

The prefix is the weak form: the pre-fix code has no `BenchmarkRefused`, so the module fails at
import rather than test by test. Mutant G1 (the guard call removed, everything else in place) is
the pre-fix behaviour with the module importable.

| Mutant | Killed by |
|---|---|
| G1 no guard in resolve user | `test_a_named_teacher_is_refused_too`; `test_refused_outside_debug_and_nothing_is_created`; `test_the_nightly_replay_is_skipped_and_creates_nothing`; `test_the_weekly_live_job_is_skipped_without_the_benchmark_switch` |
| G2 debug does not allow | `test_debug_lets_it_run` |
| G3 the flag does not allow | `test_the_flag_lets_it_run`; the 7 `CommandHistoryIntegrationTest` tests |
| G4 the setting does not allow | `test_the_nightly_replay_runs_when_enabled`; `test_the_setting_lets_it_run`; `test_the_weekly_live_job_runs_with_both_switches` |
| G5 handle drops the flag | `test_the_flag_lets_it_run`; the 7 `CommandHistoryIntegrationTest` tests |
| G6 nightly does not skip on refusal | `test_the_nightly_replay_is_skipped_and_creates_nothing` |
| G7 weekly does not skip on refusal | `test_the_weekly_live_job_is_skipped_without_the_benchmark_switch` |
| G8 the switch defaults on | `test_enable_grading_benchmark_defaults_to_false` |
| G9 the live qa skip logs at debug | `test_the_weekly_live_job_is_skipped_without_live_qa` |
| G10 the refusal skip logs at debug | `test_the_nightly_replay_is_skipped_and_creates_nothing`; `test_the_weekly_live_job_is_skipped_without_the_benchmark_switch` |
| G11 the live qa skip logs nothing | `test_the_weekly_live_job_is_skipped_without_live_qa` |
| G12 the refusal skip names no switch | `test_the_nightly_replay_is_skipped_and_creates_nothing`; `test_the_weekly_live_job_is_skipped_without_the_benchmark_switch` |

### Regression (b): RED at 68f6464f, not repeated (SM ruling)

`manage.py test ai_processor AutoGrader` at 68f6464f (12G, flock, timeout 3600, `RACE_COST_*` /
`AUDIT_BENCH*` unset), 2026-10-02 11:17–11:30 WAT: **Ran 1280 tests, FAILED (errors=7,
skipped=6)**. No failures. All 7 errors are `BenchmarkRefused` in
`ai_processor.tests_benchmark_history.CommandHistoryIntegrationTest`:

1. `test_archive_preparation_failure_does_not_break_the_run`
2. `test_database_mirror_failure_does_not_break_the_run`
3. `test_history_is_written_and_report_is_unaffected`
4. `test_history_write_failure_does_not_break_the_run`
5. `test_no_history_flag_writes_nothing`
6. `test_replay_run_is_recorded_but_not_archived`
7. `test_report_is_identical_with_and_without_history`

Log: `run1_failed_68f6464f_regression_ai_processor_autograder.txt` (last 200 lines: all 7 ERROR
headers and the totals line). Full log sha256 prefix `2d66ad497fbd989f`, kept at
`~/Documents/Projects/GAP-evidence-logs/h2_run1_failed_68f6464f_regression_ai_processor_autograder.txt`.

v2 predicted this from a read before the run ended (F1). The fix is test-only. The SM ruled
that the regression is not repeated, on four conditions: (1) this log shows exactly those 7 and
nothing else; (2) the re-run includes every test module that references the benchmark jobs or
asserts on the `ai_processor` logger, with the grep recorded; (3) mutants cover the new log
level and both skip lines; (4) bundle 5's strict full run at the tip stays a hard gate, and H2
comes out of the batch if it shows anything in `ai_processor` or `AutoGrader`.

So the three modules other than the 7-test class's passed in the regression at 68f6464f, the
7 tests pass at 268201a3, and there is **no green ai_processor + AutoGrader regression on the
final code**. The one production line changed after 68f6464f is the DEBUG → INFO log call.

## Disclosed: earlier and failed runs

| Run | What happened | Files |
|---|---|---|
| Gate at 52be4ec7 (on 499a3950), 2026-10-01 02:00 | Cut off when the sessions stopped. The prefix completed (errors=1); step 1 stopped mid-run with no summary line, so it is not a result; the mutants never started. | `prefix_499a3950_failing.txt`, `run1_cutoff_52be4ec7_modules_and_guards.txt` |
| Gate (a) at dbb6f778 (on d97b7e7c), 2026-10-02 11:07 | Green (prefix errors=1, 154 OK, G1–G8 killed), but its module set missed `tests_benchmark_history`. Superseded by the runs above; its logs are in history at d066558b. | `prefix_d97b7e7c_failing.txt` |
| Regression (b) at 68f6464f | RED, errors=7 (above). | `run1_failed_68f6464f_regression_ai_processor_autograder.txt` |
| Mutants at 268201a3 | G1–G11 killed; **G12 survived** (the refusal skip line no longer names the switch). The test read the formatted message, and the appended exception text names the switch too. Fixed test-only at 92bfaa18. | `run2_survivor_268201a3_mutation_log.txt`, `run2_survivor_268201a3_mutation_results.json` |

## Not verified here

- No run against a real production or staging database; the guard is tested with
  `override_settings`.
- No green owning-app regression on the final code (see regression (b)); bundle 5's strict
  full run is the gate for that.
- Whether production already holds benchmark rows is unknown until the founder runs the
  detection query.
- The author does not verify their own work: a verifier checks this.
