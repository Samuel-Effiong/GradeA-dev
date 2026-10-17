# Epic A completion S4: before and after values

Branch `task/epic-a-s4`. It was cut from phase2/epic-a `19b2072`, then 0b merged in the epic tip twice: `c7e25b0` (S3 in) and `ddfede3` (the audit.E001 floor/limit check). Phase 2 only. The verifier is v2. No migration: `AuditEvent.action` has no choices, and `before`/`after` already exist.

Plan: `08_epic_a_completion_plan.md` §5. The design, the survey and rulings R1–R6 are in `DESIGN.md`. The SM's rulings are summarised under each part below.

## 1. What it does
- **`audit/history.py`: one registry.** It maps each tracked model to its fields and action. Everything recorded is IDs, statuses, flags, numbers and times:

  | Model | Fields | Action |
  |---|---|---|
  | `StudentSubmission` | score, score_percentage, max_points, graded_at, is_published, needs_review (R1) | GRADE_CHANGE (new, STUDENT_RECORD) |
  | `StudentCourse` | enrollment_status, course_id | ROSTER_CHANGE |
  | `CustomUser` | user_type, is_active, is_staff, is_superuser, school_id | PERMISSION_CHANGE |
  | `UserSubscription` | plan_id, is_active, is_trial, stripe_status, billing_cycle_end, cancelled_at, auto_renew | SUBSCRIPTION_CHANGE (new) |
  | `LicenseSubscription` | plan_id, is_active, max_seats, stripe_status, billing_cycle_end, auto_renew | SUBSCRIPTION_CHANGE |
  | `SchoolCreditAllocation` (a seat) | is_active, license_subscription_id (as `license_id`) | SUBSCRIPTION_CHANGE (R4) |

  Never tracked:
  - answers, raw_input, feedback, ai_feedback, formatted_grade and review_reasons;
  - `StudentCourse.ai_summary`;
  - email, names, password, activation token;
  - **token_epoch** (SM note 1).
- **One event per changed record** (D3), with `before`/`after` holding **only the fields that changed**. A create has `before=None`; a delete has `after=None` (R5). `metadata` carries `changed_fields`, `source` (save, create, delete or bulk) and the record's ids.
- **Instance saves and deletes** go through model signals:
  - `pre_save` reads the stored values, one query and only when the save can write a tracked field;
  - `post_save` diffs;
  - `pre_delete` records the row before it goes.
- **Queryset writes** go through `record_bulk(queryset, *, actor=..., **changes)`:
  - it reads the pks, locks them by pk on the base manager in pk order, and reads `before`;
  - it updates by pk, with the caller's queryset as a subquery, so a claim filter such as `is_published=False` is re-checked after the lock;
  - it writes one event per row that changed.

  It accepts DISTINCT and aggregate-annotated querysets (v2's early warning; Postgres refuses FOR UPDATE on those).
- **A per-action `before`/`after` allow-list** (`audit.metadata.BEFORE_AFTER_ALLOWLIST`). The emitter now narrows `before`/`after` per action, as it does `metadata`, so an action without an entry keeps none. `registry_problems()` (asserted) keeps the registry within the allow-list.
- **The actor** follows S3's rule: the request's signed-in user, or SYSTEM. Where the request has no user yet, the code names the account it just authenticated, never a value from request input (SM rulings):
  - `record_bulk(..., actor=...)` (keyword-only) for the enrolment activation at sign-in (R3);
  - `history.acting_as(user)` around the account's own activation at `/auth/verify`, at Google sign-in, and at both invitation completions (student, school admin).
- **History events carry the request** (source address, trace), as an explicit emit does.
- **Creates of users are recorded only for privileged accounts:** staff, superuser, SCHOOL_ADMIN or SUPER_ADMIN (SM ruling 4). Ordinary teacher and student accounts are covered by ACCOUNT_REGISTER and the roster flows.

## 2. Call sites
| Route or path | Now |
|---|---|
| update-grade (`students/views.py`) | instance save, so the signal writes 1 GRADE_CHANGE |
| publish (`students/views.py`) | `record_bulk` (claim `is_published=False`) |
| publish-all-grades (`assignments/views.py`) | `record_bulk`: one GRADE_CHANGE per submission it actually publishes |
| mark-reviewed (`students/views.py`) | `record_bulk`: needs_review true→false (R1) |
| AI grading save (`students/services.py`) | inside `history.suppressed()`. Its before/after go on the one GRADING_COMPLETED (`emit_grading_completed`), in the columns, on **both** the Celery task and the synchronous `grade` route (R2). The sync route's event names the requester, so it replaces S1's generic event. |
| sign-in activation (`classrooms/services/enrollment.py`; Google in `users/views.py`) | `record_bulk(..., actor=<the authenticated student>)`: one ROSTER_CHANGE per activated enrolment beside AUTH_LOGIN, and no generic event (R3, as confirmed) |
| remove-student (`classrooms/views.py`) | the explicit course-level emit is gone. The enrolment's history delete event is its one ROSTER_CHANGE (SM ruling). Nothing filtered ROSTER_CHANGE by target = course: the query API has no target filter. |
| bulk-add-students | N per-record history creates, plus the one aggregate ROSTER_CHANGE, the only record of failed rows (SM ruling: N + 1) |
| admin activate/deactivate users (`users/admin.py`) | `record_bulk`: one PERMISSION_CHANGE per user changed, naming the admin |
| `SubscriptionService.activate_subscription` (`billing/services.py`) | `record_bulk` for the subscriptions it switches off |
| download-pdf (`assignments/views.py`) | **DATA_EXPORT** with `file_count` and `file_size_bytes`. It renders questions and rubric only; **no route exports student data yet.** |

**The S4 guard** (`audit/tests_history_guard.py`, in the H-38 style) is an AST sweep of production code:
- every `.update()`/`bulk_update()` naming a tracked field (either spelling), or with fields it can't read, must go through `record_bulk` or sit in `ALLOWED` with a reason;
- its own tests cover stale entries, reasons and the scanner itself.

Today five allow-listed sites remain, none of them on a tracked model: `record_bulk` itself, the append-only queryset guard, SubscriptionPlan price sync, and two Assignment backfill commands.

**Consequences worth knowing:**
- `/auth/verify` success, Google resurrection and the two invitation completions each also write a PERMISSION_CHANGE (`is_active` false→true) naming the account itself. That's once per account.
- A no-op second publish or mark-reviewed changes nothing, so it gets S1's generic event, like any other state-changing request with no named event.

**`history.suppressed()` is an audit off-switch (SM condition).** Its production call sites are named in `SUPPRESSION_ALLOWED`, and the guard fails on any other:
1. `audit/history.py` `record_bulk`: around its own update, because it writes one event per changed row itself, so the signals must not write a second.
2. `students/services.py` `_populate_and_save_grade`: the AI grading save (SM note 2). Its before/after go on GRADING_COMPLETED.
3. `audit/bench_history.py`: the Gate 6 benchmark's capture-off baseline. It runs by label only, never in the suite and never in production.

There is no migration, backfill or command use. Tests use it only to create privileged or enrolment fixtures whose own history isn't what the test is about.

## 3. Tests updated on purpose
- `classrooms/tests_epic_a_roster_audit.py`: bulk-add is N + 1, and remove-student's one event is the enrolment's history delete.
- `audit/tests_emitter.py`: before/after follow the action's own allow-list.
- `users/tests_auth_audit_doors.py`: `only_event()` ignores a fixture's privileged-account create event. It uses JSON containment, because a key lookup is NULL on events without the key.

- `audit/tests_state_change.py`: a superadmin's write compares only its request's events. The school-admin scoping tests filter `?action=AUTH_LOGIN`, because a privileged fixture account's creation is now a PERMISSION_CHANGE in its school.
- `audit/tests_query_api.py`, `audit/tests_admin_action.py`: the privileged fixture accounts are created inside `history.suppressed()`. Those tests are about query scoping and admin coverage, not account creation.
- `audit/tests_license_admin_attribution.py` (S3's SM pin): add-teachers now records the credit GRANT, the **seat** taken (SUBSCRIPTION_CHANGE on the allocation, R4) and the teacher's **school** set (PERMISSION_CHANGE). Remove-teachers records the EXPIRE, the seat released and the school cleared. All of them name the admin, so there is still no STATE_CHANGE.
- `students/tests_epic_a_submission_upload_audit.py`: its fixture enrolment is created under `history.suppressed()`.
- `audit/tests_enums.py`: GRADE_CHANGE joins the three-year (STUDENT_RECORD) actions (D6).

## 4. Changed-module map (0b: every touched production file under a label)
| Production file | Labels |
|---|---|
| `audit/history.py`, `metadata.py`, `emitter.py`, `enums.py`, `context.py`, `apps.py` | the whole `audit` app, plus students.tests_epic_a_submission_upload_audit and assignments.tests_epic_a_crud_audit (event-count pins elsewhere) |
| `students/services.py` | audit.tests_history (AI grading), assignments.tests_grading_audit_events, students.tests_second_opinion_queue |
| `students/views.py` | audit.tests_history, students.tests_grading_review_fixes, students.tests_submission_update_freshness, students.tests_second_opinion_queue |
| `assignments/tasks.py` | assignments.tests_grading_audit_events, audit.tests_history |
| `assignments/views.py` | audit.tests_history (publish-all, DATA_EXPORT), assignments.tests_download_pdf, assignments.tests_cache_matrix_g3 |
| `classrooms/services/enrollment.py` | classrooms.tests.ActivatePendingEnrollmentsOnLoginTest, audit.tests_history |
| `classrooms/views.py` | classrooms.tests_epic_a_roster_audit, classrooms.tests_concurrency_and_resilience |
| `users/views.py`, `users/admin.py` | users.tests_auth_audit_doors, audit.tests_history, plus the `users` regression |
| `billing/services.py` | billing.tests.test_free_plan_activation_security |

## Gates (rule 15: changed modules + mutation + ONE owning-app regression; logs committed)
Everything below ran on **`f8267bc`** (0b's merge `3a46781` of phase2/epic-a `be1147a`, with S6b, plus test/doc commits). Every run was wrapped in `systemd-run MemoryMax=6G`, `nice -n 10`, a timeout, RACE_COST 600/200 and `--noinput`, one step at a time in 0b's slot.

**History:** three earlier gated runs stopped at the changed-module step. Each time the script halts before the prefix when that step isn't green, so no red mutation or regression was ever recorded:
1. `tests_state_change`: privileged fixture accounts.
2. Widened to the whole `audit` app: the admin-action, query-API, S3 licence-pin and enums tests.
3. The upload-audit tests: a fixture enrolment.

All three were fixture or pin updates, listed in §3. None were code defects.

| Gate | Result |
|---|---|
| Reproduce-first | `be1147a`'s call sites and emitter (`audit/apps.py`, `emitter.py`, and the call sites in students, assignments, classrooms, users and billing), with the new history module kept so the tests import. Against `audit.tests_history`, `audit.tests_history_guard` and `classrooms.tests_epic_a_roster_audit` (`prefix_be1147a_failing.txt`): **50 tests, 17 failures, 8 errors**. All are behavioural: no events written, no before/after narrowing, the guard sees the unrouted bulk writes, and the roster shapes differ. |
| Changed modules | the whole `audit` app + the 13 mapped labels (§4): **530 OK** (`changed_modules.txt`, trimmed; full log in GAP-evidence-logs) |
| 2 Mutation | `mutate.py`, **17 mutants, 17 killed**, anchors asserted unique (`mutation_log.txt`, `mutation_results.json`). They cover: registry (H1), diffing (H2), actor rule and `acting_as` (H3, H4), privileged-only creates (H5), deletes (H6), `record_bulk` (H7), school scope (H8), request fields (H9), per-action narrowing (B1), AI suppression and GRADING_COMPLETED before/after (G1, G2), the sign-in actor (R1), the verify actor (P1), publish-all routing (P2, also killed by the guard), an unlisted `suppressed()` (S1, killed by the new guard) and DATA_EXPORT (X1). |
| 1 Regression (one app) | `users`: **686 OK** (skipped=4) (`regression_users.txt`, trimmed; full log in GAP-evidence-logs). Chosen over `audit` because the audit app is already in the changed set and users has the widest behaviour change (0b agreed). |
| mypy | whole-repo `pre-commit run mypy --all-files`: **Passed** on `f8267bc` |
| Migrations | `makemigrations --check --dry-run`: **No changes detected** |
| 3 Concurrency | `ConcurrentGradeWritesTests` (TransactionTestCase, real threads): a publish (`record_bulk`, row lock) racing a score save. Each records its own change with matching before/after. |
| 5 Failure | `test_the_grade_is_saved_when_the_audit_store_is_down`: the audit store is down, the grade is still saved and the route answers 200 (FR-A-11) |
| 6 Stress (SM note 4) | `audit/bench_history.py` on the test DB (`gate6_benchmark.txt`): see below |

**Gate 6: what capture costs** (40 grade saves; 10 imports of 30 students through the real bulk-add route):

| Measure | Capture off (`suppressed()`) | Capture on |
|---|---|---|
| one grade save, p50 / p95 | 2.29 / 3.13 ms | 5.68 / 7.06 ms |
| roster import of 30, p50 / p95 | 869 / 1098 ms | 941 / 1209 ms |

A tracked save costs about **+4 ms p95**. It pays for the `pre_save` read of the stored values, the `post_save` read-back and the event insert. A roster import of 30 costs about **+10%** (30 enrolment creates + 1 aggregate). Saves that write no tracked field (`update_fields` without one) skip both reads. If the +4 ms matters, the `post_save` read-back could use the instance's saved values instead; it doesn't look worth it now.
