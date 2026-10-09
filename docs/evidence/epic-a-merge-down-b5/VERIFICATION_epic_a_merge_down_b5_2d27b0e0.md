# Verification: bundle 5 → Epic A merge-down (0b merge, ed resolution and follow-ups)

- **Branch:** task/epic-a-merge-down-b5 at **2d27b0e0**, off phase2/epic-a cc22bc03, merging beta 74bfc8d3 (merge base 67a06817). The code tip is 878ddf58; 2d27b0e0 adds evidence only.
- **Commits:**
  - 05808f8a: one PII-log baseline line, before the merge (0b);
  - 5554811a: the merge (0b commits, ed resolved);
  - 518771e2 + 804258f0: the two audit sweeps take the Beat lock (ed);
  - 88d3f29d, fc70ea6f: baseline lines removed (ed);
  - 9f87eedf, 16027188, 21645f8c: test-only (ed).
- **Verifier:** v2 (independent), 2026-10-02. **No v2 test run and no slot used** (rule 15): static checks, two plain-Python sweeps over git objects, and ed's logs.
- **Verdict:** **VERIFIED-WITH-NOTES**

## The merge, 5554811a (static)
- **`git show --remerge-diff`:** exactly four files, five hunks, each as the SM ruled:
  - billing/license_service.py: the epic's log string ("... license %s. Expired %d credit buckets."), ids-only arguments;
  - billing/tasks.py: both sides' imports (`audit_metrics`, `beat_locks`, `single_instance`);
  - classrooms/tests_security_penetration.py: S7d's form (`ROW_STAFF_EMAIL`, "Row 1: …");
  - users/signals.py: beta's `type(exc).__name__`, in both hunks.
- **Added-line survival (`vf_merge_survival.py`):** 4 lines absent, all accounted for.
  - Two are beta's lines in tests_security_penetration.py, replaced by S7d's form (ruled).
  - Two come from H1's rename of the backfill script: the merged `scripts/one_off_backfill_stripe_schedules.py` keeps the epic's ids-only `user_sub.user_id` print, and beta's `user_sub.user.email` print is the line it replaced. Git carried the epic's edit through the rename. That is the right result; ed's record now says so.
- **The pinned files:** assignments/tasks.py and students/tests_h38_tasks_namespace.py are blob-identical to cc22bc03, at the merge and at the tip. Beta changed neither since the merge base.

## The eight auto-merged files changed on both sides (SM point 3)
v2 read each one in the merge result against both parents. No clash.
| File | Result |
|---|---|
| AutoGrader/settings.py | Beta adds `ENABLE_GRADING_BENCHMARK` (7 lines); the epic's audit settings and beat entries are untouched |
| AutoGrader/tests_cache_invalidation_coverage.py | H-73's guard arrives; the epic's `audit/failed_auth_cap.py` entry is kept |
| billing/license_views.py | Beta adds H-60's `_not_recorded_response` and four `except` clauses; nothing of the epic's is displaced |
| billing/services.py | Beta's H-76 and H-82 changes and the epic's `history.record_bulk`, `expire_bucket(reference)` and anomaly metrics sit in different functions |
| billing/tests/test_other_school_before_subscription.py | Identical on both sides (the H-78 fold) |
| classrooms/serializers.py | Beta's H-71 form-path refusal arrives; the epic's lines are kept |
| classrooms/services/__init__.py | Identical on both sides |
| classrooms/services/enrollment.py | Equals the epic's file (S7d already carried H-71's hunks) |

## The follow-ups (ed authored them; v2 verifies)
- **804258f0, audit/tasks.py (SM point 4):** `@single_instance(max_hold=beat_locks.DAILY)` on `sweep_audit_retention` and `sweep_audit_pii_short_retention`, plus two imports. Nothing else in production differs from the merge.
  - v2's own static sweep at the tip: 25 beat tasks, **none neither locked nor exempt**.
  - **Redis unavailable:** the sweep is skipped with an ERROR log naming the task; nothing is deleted or blanked, and no `AUDIT_RETENTION_SWEEP` event is written. Both sweeps select by cutoff, so the next run that gets the lock does the skipped work. A skip costs one day and no more (`test_the_next_run_does_the_skipped_runs_work`). This matches the SM's H-65 ruling: fail closed is right for a task the next run catches up.
- **518771e2, audit/tests_sweep_beat_lock.py:** 7 tests. No MagicMock: the lock store is made unavailable by `patch.object(..., side_effect=RedisConnectionError)` (rule 14).
- **9f87eedf (test-only, after round 1's red):**
  - audit/tests_retention_sweep.py: the two concurrent-sweep tests sum the totals over the runs that did the work; every other result must be the lock's skip summary. "Each row processed once" is unchanged.
  - **classrooms/tests_h71_student_add_role.py (SM point 2): H-71's point is still pinned.** `test_no_route_names_the_role_of_a_staff_address` is unchanged. `test_every_role_gets_the_same_answer` still compares status and whole body across roles, with only the per-response `reference` value masked. The bulk assertion takes S7d's row form, with the same neutral text. Round 2 passing proves the bodies differ in the reference alone.
- **16027188 (test-only):** the guarded-task pin names the two epic-only tasks and keeps beta's count of 21 over the rest, so a guarded task added on beta fails here at the next merge-down.
- **21645f8c (test-only, H-95; SM point 1):** see below.
- **Baseline (05808f8a, 88d3f29d, fc70ea6f):** the moved script's new path is listed and the old one removed; license_service.py and users/signals.py leave the baseline, so the hook guards both. S7d's note 1 (the teacher's address in five success-path log lines) is closed on this tree by H-80.
- **v2's command-module sweep at the tip:** 24 modules, every one with a `Command` class.

## The audit volume-report failure (SM point 1)
- **Not caused by the merge.** Under audit/ the branch differs from cc22bc03 only in tasks.py (the two decorators) and three test modules. No model, index, migration or command changed.
- **The failure:** on a table of four rows the planner showed a full scan of `audit_retention_ix` with `action` as a filter. That costs the same as the pinned scan, so the choice is a tie.
- **The fix is test-only:** the test first inserts 60 rows per action and runs `ANALYZE`, so the pinned scan is the clear winner when its index exists.
- **The fixed test still fails without each index.** ed's deliberate-red log shows two failures, one per dropped index, each from the check's own `Index Cond` assertion: without `audit_action_time_ix` the plan has no pinned `action`; without `audit_retention_ix` it is a bitmap scan with no pinned `retention_class`. `test_the_index_check_fails_when_an_index_it_protects_is_missing` keeps that proof in the suite.

## ed's gates (read, not repeated)
| Gate | Tip | Result | Log |
|---|---|---|---|
| Reproduce-first: beta's beat guard and the sweep test on the merge's audit/tasks.py | 3cfc40f8 | Red, as expected | prefix_sweeps_unlocked_failing.txt |
| Changed modules + both sides' guards | 3cfc40f8 | **RED**: 745 run, 6 failures + 2 errors | run1_failed_3cfc40f8_modules_and_guards.txt |
| The three touched modules + the sweep test | 798c06e2 | **49 OK** | r2_touched_modules.txt |
| Mutants R1–R6 | 798c06e2 | **6 of 6 killed** | mutation_log.txt |
| Seven-app regression | c32de6aa | **5135 run, 1 failure** (H-95) | run1_failed_c32de6aa_regression_seven_apps.txt |
| audit.tests_volume_report after the fix | 878ddf58 | **10 OK**, three times serially and once with `--parallel 2` | volume_report_after_fix.txt |

- **Full logs, hashes recomputed by v2:** round 1 `4ef376a310656971`; the regression `d06175d7f617abed`.
- **Round 1's eight headers** are exactly the tests ed lists: 2 errors in the concurrent-sweep tests, 1 failure on the count of 21, 5 failures in tests_h71_student_add_role. All collide with intended behaviour; the fix changed no production line.
- **The regression's one failure** is `test_every_windowed_count_has_an_index_path`. No other FAIL or ERROR header.
- **Both sides' guards passed in that regression,** each with 0 failures: tests_reason_codes, tests_error_messages, audit.tests_route_coverage, audit.tests_history_guard, tests_beat_locks, the H1 command guard, tests_cache_invalidation_coverage, tests_no_wildcard_invalidation, billing's test_logs_carry_no_email, H2's guard module, tests_security_penetration and tests_h71_student_add_role.
- **Rule 17:** mutate.py sets `PYTHONDONTWRITEBYTECODE=1` and deletes `__pycache__` before each mutant and after each restore; the record states both.
- **The regression's app set** covers every app whose code the merge changes. Outside those seven apps the branch changes only QA_SERVER_SETUP.md and two files under scripts/.

## Notes (none blocks)
1. **No single green seven-app run on the final tip** (SM ruling). After c32de6aa only audit/tests_volume_report.py changed, and it ran green four times at 878ddf58. Gate 10 on the merged epic tip is the full green run.
2. **5554811a alone fails beta's H-65 guard** on the two audit sweeps. Only the branch tip is merged, with `--no-ff`, so the epic's first-parent history has no red state.
3. **The concurrent-sweep tests no longer prove the sweeps' own row-level safety under real overlap,** because the lock usually skips the second run. With the lock in place two sweeps overlap only if the lock lapses. Worth a backlog line if that safety net should stay tested (call the undecorated function in the test).
4. **Divergences future merge-downs must keep** (the epic side of each): tests_beat_locks.py's named tasks, tests_h71_student_add_role.py's two assertions and helper, tests_security_penetration.py's S7d line, and b4's `grade_engine_async` and the students H-38 test. RESOLUTION_ed.md lists them; 0b's merge checklist should carry them.
5. **billing/services.py still logs a user's address** in places (ed counts 17 flagged calls; the file stays baseline-listed; H-23 / H-91). Unchanged by this merge.
