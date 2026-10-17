# H-18 / H-19: assignment course IDOR, AI-output field writes, and superadmin single-flag gates

Backlog items: `docs/HARDENING_BACKLOG.md` H-18 and H-19. Found by a
cross-role data-leakage audit on 2026-09-17, reproduced before any fix, and
fixed the same day under the 10-gate doctrine
(`~/.claude/senior-manager/grade-automator-plus/DOCTRINE.md`).

| | |
|---|---|
| Branch | `task/verify-idor-superadmin-audit` |
| Base | beta `b744c9f` |
| Commit intended for release | `af23012a8dc5dc357614e531e46fa4730bef91e7` — **NOT yet gated** |
| Gate status | Attempt 1 on `6811527` FAILED (6 errors, all caused by this change); fixed in `af23012`; two clean runs still owed (§10) |
| Predecessors | `25613d3` (first fix), `d2a004f` (widened fix), `6811527` (tests), `af23012` (three suites updated for the new superadmin rule) |
| Source diff vs `b744c9f` | 8 files |
| Test modules added | 8 |

## 1. The 10 gates

| Gate | Status | Evidence |
|---|---|---|
| 1 Baseline / Regression | PARTIAL — full suite not yet clean | Reproduced on `b744c9f` first (§3, §8 M00: 54 failures + 1 error of 61 tests). Affected apps 1,274 OK; nearest suites 245 OK; full suite §10. No assertion weakened; 2 new skips, both opt-in and accepted (§12) |
| 2 Mutation | PASS | 31 cases, 30 killed. 1 survivor (M18) proven to be a two-layer defence by M19. M21 exposed a REAL test gap, closed in `6811527`, then killed. Restores verified by git blob hash, 31/31 (§8) |
| 3 Concurrency | PASS | 20 threads × 10 rounds, both scenarios: 200 foreign-course writes all refused; 200 type-only AI calls, 0 reached the provider, 0 billing rows (§6) |
| 4 Adversarial | PARTIAL | Own adversarial suite passes (§5). Independent replay: #10 by red-team-aifile on `d2a004f` — contract met, no bypass. T2 (course IDOR) and T4 (superadmin) replays on `6811527` were still PENDING when this was written (§9) |
| 5 Failure / Recovery | PASS | Provider timeout and rejection, Celery redelivery, failed-then-retried delivery, DB error in the credit gate (§7). Redis NOT APPLICABLE with reason (§7) |
| 6 Stress / Scale | PASS | Query counts identical at 60, 600 and 6,000 students (6 / 4 / 22). 6,000-student school p50/p95 and peak memory recorded (§6) |
| 7 Real Infrastructure | PASS | LOCAL-REAL throughout: real PostgreSQL 18.6 and Redis 8.0.5. One real billed provider call: `x-ai/grok-4.3`, 1,126 tokens, `finish_reason=stop`, 0 billing rows (§6) |
| 8 Live / End-to-End | PARTIAL | LOCAL-REAL only: every test drives the real router, permissions, serializers, services, Celery task bodies and DB. No DEPLOYED-REAL run. Per doctrine H8.1 this caps Gate 8 at PARTIAL; the deployed check is to be coordinated with the Integrator and Red Team Lead. **Needs the user's written sign-off before landing (H1.3)** |
| 9 Security / Isolation | PASS | Both directions, same-school and different-school, all five role shapes, full-payload assertions (§5) |
| 10 Final Production Gate | **NOT PASSED** | Attempt 1 r1 on `6811527` FAILED with 6 errors caused by this change; r2 killed and discarded. Two consecutive clean runs on `af23012` are owed (§10) |

**Labels (H8.1):** every result here is MOCKED, LOCAL-REAL or DEPLOYED-REAL.
Only the AI provider call and the flaky billing test touch an external
network; everything else is LOCAL-REAL. Nothing is DEPLOYED-REAL.

## 2. The 8 completion answers

1. **What changed.** Four guards, in 8 files: (a) `AssignmentTextSerializer.validate_course` rejects a course the requester doesn't own and fails closed without a request, and `update_async` passes the request context; (b) the Settings queryset, the monthly-token-usage check, `HasCreditBalance`, and `execute_graded_task`'s unmetered branch all require BOTH superadmin flags; (c) AI output is reduced to 9 content fields before it is saved, at three entry points, and `AssignmentSerializer.teacher` is read-only; (d) `TopicSerializer.validate_course` and `CourseSerializer.validate_session` fail closed.
2. **Why it was necessary.** Each was reproduced first (§3): a teacher could plant or move assignments in another teacher's course; a `createsuperuser` account could read and edit any user's settings and any school's token usage; an account with `user_type=SUPER_ADMIN` alone got free unlimited billed AI; and text inside an assignment or uploaded file could make the model write `status`, `teacher` and other protected fields.
3. **What was tested.** 8 new modules, 70 tests: adversarial, legitimate-flow, serializer-boundary, concurrency, failure/recovery, scale, and one real-provider call. Plus the existing suites nearest the change.
4. **Which gates passed.** 2, 3, 5, 6, 7, 9 (§1). Gate 1 is PARTIAL: the affected-app and nearest-suite runs are clean, but no full suite has passed yet.
5. **Which gates remain incomplete.** Gate 10 (NOT PASSED — see §10), Gate 1 (PARTIAL for the same reason), Gate 8 (PARTIAL, nothing DEPLOYED-REAL), and Gate 4 (PARTIAL: T2 and #10 are clean on `6811527`, T4 still in flight, and all three need re-confirming on `af23012` if its guards ever change — they do not today, `af23012` is tests-only).
6. **What risks remain.** §12.
7. **Which commit contains the verified implementation.** `af23012a8dc5dc357614e531e46fa4730bef91e7` — verified for gates 2-9 but NOT by a clean full suite.
8. **Is the verified commit the one intended for release.** `af23012` is the intended release commit, but it is NOT yet gated, so **nothing is landable today**. The guards in `af23012` are byte-identical to `d2a004f` and `6811527`; it differs only in test files.

## 3. What was wrong, and the reproduction of each (Gate 1, H2.1)

### H-18a Course IDOR — three entry points, not two

`AssignmentTextSerializer.course` was a plain writable PK field with no
ownership check. `get_queryset()` scopes which EXISTING assignment a teacher
can reach (`course__teacher=user`), never the course a new or edited one
points at. The audit reported two doors; reading the views found a third.

| # | Entry point | How the course was written |
|---|---|---|
| 1 | `POST assignments/`, `POST assignments/create-async/` | `Assignment.objects.create(course=validated_data["course"])` |
| 2 | `PATCH assignments/<pk>/` | `instance.course = validated_data.get("course", …)` |
| 3 | `PATCH assignments/<pk>/update-async/` | same, **and** it built the serializer with no request in context |

**Reproduced on `b744c9f`** through the real router, permissions and
serializers, before any fix:

- Teacher B `POST create-async` with teacher A's course UUID and
  `status=PUBLISHED` → **202**; the row was saved in course A and appeared in
  the assignment list of teacher A **and of teacher A's enrolled student**.
- Teacher B `PATCH` of their own assignment with A's course → **200**, the row
  moved, and A's student could see it.

The file-upload actions were never affected (`get_object_or_404(Course,
id=…, teacher=request.user)`).

### H-18b AI output could write protected fields (finding #10)

`ai_processor` returns `json.loads(...)` unfiltered for extraction (free-form
`json_object`, no response schema), and that dict was passed whole into
`AssignmentSerializer`, whose writable fields include `status`, `teacher`,
`course`, `topic`, `due_date` and `auto_grade_on_due_date`. Text inside a
typed assignment or an uploaded document could therefore steer the model into
emitting those keys.

Reproduced independently by red-team-aifile on `b744c9f`
(`assignments/tests_redteam_aifile_field_write.py`, evidence
`docs/evidence/security_replay/aifile/FINDING_10_ai_field_write.md`): the
upload-create and re-extract-mutate exploits both succeeded, writing
`status=PUBLISHED` and `teacher=<other user>` verbatim.

Sinks: `file_uploads.py` (upload create), `services.py` (re-extract, mutating
an existing row), the generate view, and the draft-save view. A fifth case
found while fixing: **drafts generated before the fix keep raw AI keys in
their stored snapshot**, so filtering only at generation would leave them
exploitable on save.

### H-19a Two superadmin gates accepted either flag

`SettingsViewSet.get_queryset` and `SchoolViewSet.monthly_token_usage` used
`is_superuser OR user_type == SUPER_ADMIN`, while `IsSuperAdmin` and every
other gate requires both. `create_superuser()` sets `is_superuser` but leaves
`user_type` at `TEACHER`.

**Reproduced on `b744c9f`:** a `create_superuser()` account got 200 reading
another teacher's Settings, 200 on PATCH of them, and 200 on another school's
monthly token usage, while a plain teacher got 404/403 and the Settings
`list` action (which uses `IsSuperAdmin`) refused the same account with 403.

### H-19b Single-flag credit and AI bypass

`HasCreditBalance` returned True for `user_type == SUPER_ADMIN` alone, and
`execute_graded_task` took its "unmetered, unrestricted" branch on the same
single flag — no tier gate, no credit consumption. An account with
`user_type=SUPER_ADMIN` and `is_superuser=False` therefore ran billed AI free.
Found by the Gate-3 sweep of this file (§4), not by the original audit.

**Reachability.** No HTTP route reaches the unmetered branch with such an
account: every AI endpoint has a role gate first (§4 table). Background work
that loads `course.teacher` does — weekly course summaries
(`dashboard/tasks.py:47`, no type filter), `auto_grade_due_assignment`
(`tasks.py:1070`), the scheduled grading jobs, and the `student_summary_async`
follow-up. Such an account is produced by a real superadmin PATCHing a
school-less teacher to `user_type=SUPER_ADMIN` (`is_superuser` untouched, and
the account keeps its courses), or by unticking `is_superuser` in Django
admin.

### H-18c Fail-open ownership validators (hardening)

`TopicSerializer.validate_course` and `CourseSerializer.validate_session`
returned the value unchecked when the serializer had no authenticated request
in context. Not exploitable today — every data-bound caller passes context —
but `update_async` shows how easily a view forgets, and there the same
pass-through was a live bypass. Both now refuse.

## 4. The fixes, and the two sweeps

### Fixes

| Guard | Where | Rule |
|---|---|---|
| Course ownership | `assignments/serializers.py` `AssignmentTextSerializer.validate_course` | Refuses unless `course.teacher_id == user.id`; a both-flag superadmin passes; **refuses when there is no authenticated request** (deliberately stricter than `TopicSerializer`, because `update_async` built the serializer without context — mutant M04 proves that combination is a live bypass) |
| Request context | `assignments/views.py` `update_async` | `self.get_serializer(...)` instead of a bare serializer |
| AI-output allow-list | `assignments/services.py` `AI_ASSIGNMENT_CONTENT_FIELDS` + `ai_assignment_content_only()`, applied in `extract_assignment_data`, `_build_generated_assignment_draft`, and `save_generated_assignment_draft` | Exactly the 9 content keys of both extraction prompts and the generation schema. Server values are added after the filter |
| Teacher read-only | `assignments/serializers.py` `AssignmentSerializer.Meta.read_only_fields` | Second layer; no production path writes `teacher` |
| Settings gate | `users/views.py` | Both flags |
| Token-usage gate | `classrooms/views.py` | Both flags, and `UserTypes.SUPER_ADMIN` instead of the string literal |
| Credit gate | `users/permissions.py` `HasCreditBalance` | Both flags; a single-flag account falls through to the ordinary wallet check, so it is not refused outright if it genuinely has credit |
| Unmetered AI | `ai_processor/services.py` `execute_graded_task` | Both flags; a type-only account raises `AIFeatureNotAvailableError` (a user-facing refusal), not the `ValueError` further down, which views report as a server fault |
| Fail-closed validators | `classrooms/serializers.py` | Both refuse without an authenticated requester |

Not changed, deliberately: `billing/license_service.py:319` and
`users/serializers.py:175` use `or` on the **deny** side (classifying a
*target* account as platform staff), where either flag is stricter.

### Sweep 1 — every writable relation and request-supplied id (doctrine item 2)

47 write paths across `users`, `billing`, `classrooms`, `assignments`,
`students`, `dashboard`, `ai_processor` and `AutoGrader` were read in context.
Result: **CHECKED 26, SERVER-SET 5, SUPERADMIN-ONLY 7, NOT-REACHABLE 7,
SUSPECT 2.** The two suspects:

| Suspect | Verdict |
|---|---|
| `billing/serializers.py:180-185` `UserSubscriptionSerializer.plan` resolved by an unscoped `SubscriptionPlan.objects.get`; `validate()` only rejects `price_cents > 0` — no category, `is_active` or TRIAL/BETA/CUSTOM filter (compare `SelectIndividualPlanSerializer.validate_plan_id`). Reachable by a teacher via `POST /user-subscriptions` and by a teacher or school admin via `POST /subscription` | **Routed to fix-free-plan (7e)** by the Senior Manager; outside this diff |
| `assignments/serializers.py` `AssignmentSerializer.teacher` writable, fed by AI output | **Fixed here** (allow-list + read-only) |

Hardening notes, not exploitable today: `TopicSerializer.validate_course` and
`CourseSerializer.validate_session` fail-open without context (fixed here);
`license_views.py:380` `remove_teachers` uses an unscoped user lookup but the
service raises without a matching allocation.
**Out of scope for this sweep, as agreed with the Senior Manager:** the Django
admin (superuser-only) and the Stripe webhooks (signature-verified,
server-set metadata).

### Sweep 2 — every superadmin check (doctrine item 3)

Every non-test `is_superuser` and `user_type == SUPER_ADMIN` use was read in
context (most span several lines, so a one-line grep is not enough).

| Location | Verdict |
|---|---|
| `classrooms/permissions.py` `IsSuperAdmin`, `billing/views.py` ×5, `billing/license_views.py` ×5, `billing/serializers.py:211`, `billing/qa_console.py:109`, `billing/license_service.py:129`, `classrooms/serializers.py:113`, `classrooms/views.py:1857/1923`, `users/views.py:280/308`, `billing/billing_transaction_views.py:57` | Both flags — correct |
| `billing/license_service.py:319`, `users/serializers.py:175` | `or` on the deny side — correct, left unchanged |
| `users/views.py` Settings queryset, `classrooms/views.py` monthly token usage, `users/permissions.py` `HasCreditBalance`, `ai_processor/services.py` unmetered branch | **The four bugs — fixed here** |
| `users/views.py` `CustomUserViewSet.create` inner `not is_superuser and user_type != SUPER_ADMIN` | Either-flag shaped but **unreachable**: `get_permissions()` puts `IsSuperAdmin` (both flags) on the `create` action and DRF evaluates it before the handler. Left unchanged, recorded as a consistency clean-up candidate |
| `billing/views.py:2457`, `dashboard/views.py:1187`, `ai_processor/services.py:4614` | `SUPER_ADMIN` passed as a prompt ROLE inside already-gated views, not a gate |
| `billing/license_service.py:3064` | Selects notification recipients with both flags |

### AI entry points reached by the new refusal (doctrine requirement)

All 16 `execute_graded_task` call sites were traced to their HTTP and Celery
entry points. **No HTTP route admits a type-only SUPER_ADMIN**; each is
stopped earlier by `IsTeacher`, `IsTeacherOrReadOnly`, `IsStudent`,
`IsSuperAdmin` (both flags) or a `teacher=request.user` lookup, with 403/404.
The paths the change actually affects are background ones that load
`course.teacher` or a stored user id:

| Path | Before | After |
|---|---|---|
| `dashboard/tasks.py:47` weekly course summaries | ran unmetered, free | refusal, currently swallowed by its `except Exception` (the email still sends without the narrative) |
| `assignments/tasks.py:1070` `auto_grade_due_assignment` → `grade_engine_async` | ran unmetered | task FAILURE, re-raised, no retry; the batch entry records `error=None` |
| Scheduled grading (`#9`, `#16`) and the `student_summary_async` follow-up | ran unmetered | task FAILURE with the refusal text, re-raised, no retry |

Making those recordings cleaner (the swallow, `error=None`, and the
retry-3× paths) was assigned by the Senior Manager to **fix-refusal-handling
(04)**, together with the pre-existing cluster it owns: `dashboard/views.py`
and `billing/views.py` turning any AI refusal into a 500, and
`ai_processor/services.py:767-768/820-821` re-wrapping refusals as a plain
`Exception`. Agreed file carve: this branch touches only
`ai_processor/services.py` ~4424 and `users/permissions.py:17-19` of that
area. My `CeleryPathBothFlagsTest` is the contract 04 codes against.

## 5. Regression tests (Gates 1, 4, 9)

70 tests in 8 modules. Every attack asserts on the database and on the full
response payload, not on the status code alone; every refusal has a matching
legitimate-flow test, so a fix cannot pass by refusing everything.

| Module | Tests | Proves |
|---|---|---|
| `assignments/tests_course_ownership_idor.py` | 30 | create, create-async, PATCH and update-async all refuse a foreign course, against a course in **another school AND a same-school colleague's course**; a nonexistent course answers identically (no ownership oracle); foreign topics on every path; draft save with a foreign topic and with another teacher's draft; serializer boundary (no context, foreign teacher, same-school colleague, owner, true superadmin, `createsuperuser`); and the legitimate own-course/own-topic flows on every path |
| `assignments/tests_ai_output_allowlist.py` | 9 | create, re-extract, upload, generate, save, and a pre-fix poisoned snapshot all write content only, when the provider returns `teacher`, `status=PUBLISHED`, foreign `course`/`topic`, `due_date`, `auto_grade_on_due_date`, `ai_generated`, `ai_generated_at`, `custom_ai_prompt` and `id`; teacher-supplied save fields still apply; the serializer's `teacher` read-only layer on its own; the allow-list contract |
| `ai_processor/tests_superadmin_unmetered_both_flags.py` | 9 | both type-only shapes are refused by the credit gate and before any provider call, over HTTP and through `student_summary_async`; a `createsuperuser` account is judged on its wallet; a true superadmin still bypasses the gate and runs unmetered with 0 billing rows |
| `users/tests_superadmin_both_flags.py` | 10 | Settings and monthly token usage: both half-superadmin shapes refused (404/403, row unchanged), own settings still reachable, true superadmin and school-admin flows intact |
| `classrooms/tests_fail_closed_ownership_validators.py` | 6 | both validators refuse without an authenticated requester, accept the owner, refuse another teacher (same school and other school) |
| `assignments/tests_course_ownership_concurrency.py` | 2 | §6 |
| `assignments/tests_course_ownership_failure.py` | 5 | §7 |
| `assignments/tests_course_ownership_scale.py` | 2 (1 opt-in) | §6 |
| `ai_processor/tests_real_superadmin_unmetered.py` | 1 (opt-in) | §6 real provider |

**Isolation probes (H9.1), both directions.** Teacher→teacher within one
school and across schools; student views of both victims' lists; the five role
shapes (STUDENT, TEACHER, SCHOOL_ADMIN, SUPER_ADMIN, Django-admin-only); the
attacker's own data still reachable throughout. **Full-payload assertions
(H9.2):** responses are checked for the victim's course id and name, topic id
and name, teacher id and email — not just the field under test.

## 6. Concurrency, scale and real infrastructure (Gates 3, 6, 7)

**Gate 3.** `APITransactionTestCase`, real threads, real PostgreSQL, 20
threads released at a barrier, 10 rounds, each thread closing its own
connection in `finally` and every `join` followed by an `is_alive()`
assertion (H4.2):

| Scenario | Result |
|---|---|
| 10 threads create into a foreign course + 10 re-parent their own assignment, ×10 rounds | 200/200 refused with 400; no row in either victim course; the attacker's assignment never moved; the task launcher never called; neither victim's student saw anything |
| 20 concurrent AI calls, 16 type-only + 4 real superadmin, ×10 rounds | every type-only call refused; the provider saw exactly the 4 real-superadmin calls per round and no others; 0 `CreditUsageLog` and 0 `CreditLedger` rows for all three accounts |

**Gate 6.** Query counts are asserted EQUAL, not budgeted
(`docs/evidence/h18_h19/g6_scale_6k_run2.log.gz`):

| Request | 60 students | 600 students | 6,000 students |
|---|---|---|---|
| foreign create refused | 6 | 6 | 6 |
| foreign re-parent refused | 4 | 4 | 4 |
| own create through the allow-list | 22 | 22 | 22 |

6,000-student school (120 teachers, 240 courses, 5 assignments per course,
6,000 enrollments; students created through the manager with an unusable
password, as the direct-add flow does): p50/p95 **7.48/8.46 ms** (refused
create), **17.35/22.19 ms** (refused re-parent), **84.51/112.81 ms** (own
create, provider stubbed). Peak traced memory 2.3 MB. Build 791 s.

**Gate 7, LOCAL-REAL.** PostgreSQL 18.6, Redis 8.0.5, Python 3.12.10,
Django 5.2.6. One real billed provider call (H8.3), opt-in `RUN_REAL_AI=1`
(`docs/evidence/h18_h19/g7_real_provider_call.log.gz`):

| Field | Value |
|---|---|
| model | `x-ai/grok-4.3` |
| response id | `gen-1789663456-AW5EWLM9LsS2G8jRy701` |
| finish_reason | `stop` |
| tokens | 551 prompt + 575 completion = 1,126 |
| billing rows for the both-flag superadmin | 0 usage, 0 ledger |
| provider calls by type-only accounts | 0 |

## 7. Failure and recovery (Gate 5)

| Injected failure | Resulting state |
|---|---|
| Provider timeout during create | Transaction rolls back; no half-created row; nothing in the victim's course |
| Provider rejection during create | Same |
| Provider timeout / rejection during re-extraction | The existing assignment is byte-identical afterwards (title, status, course, teacher, questions, raw_input) |
| Celery redelivery of an extraction task with forbidden keys | Both deliveries write identical, filtered content; status stays DRAFT, course unchanged, teacher None |
| Failed delivery, then a successful retry | The failure leaves the row untouched; the retry writes filtered content only |
| DB error while the credit gate reads the wallet | The request is never admitted (500, not the queryset's 404) — fail closed, never "assume credit" |

**NOT APPLICABLE — Redis.** None of the changed code reads or writes the
cache: `validate_course` and the two classroom validators compare attributes
of already-loaded rows, the allow-list is a dict filter, and both both-flags
checks read user attributes. A Redis outage cannot change their decision.
(The surrounding endpoints' cache behaviour belongs to H-1.)

## 8. Mutation (Gate 2)

Parallel disposable battery (`docs/evidence/h18_h19/battery.py`): K=2 detached
worktrees at the commit, each with its own test DB, mutants distributed
across them. After every mutant each touched file is restored from
`git show <commit>:<path>` — never `git checkout` — and its **git blob hash is
compared with the commit's blob**. 31/31 restores matched; both workers ended
clean.

| Guard | Mutants | Result |
|---|---|---|
| (baseline) | M00 original `b744c9f` source, all 8 files | KILLED — 54 failures + 1 error of 61 tests. This is the Gate-1 reproduction |
| `validate_course` | M01 body removed, M02 owner comparison removed, M03 fail-open without request, M04 M03 + `update_async` without context, M05 superadmin bypass loosened to `or`, M06 refuse every course | 6/6 KILLED |
| `update_async` context | M07 bare serializer | KILLED |
| topic in course | M16 check removed, M17 partial fallback dropped | 2/2 KILLED |
| draft-save topic | M18 draft serializer check removed | **SURVIVED** |
| draft-save topic, both layers | M19 M18 + `AssignmentSerializer.validate`'s topic check | KILLED |
| Settings gate | M08 `and`→`or`, M09 `is_superuser` only, M10 `user_type` only, M11 never true | 4/4 KILLED |
| Token-usage gate | M12 `and`→`or`, M13 `is_superuser` only, M14 `user_type` only, M15 never true | 4/4 KILLED |
| Credit gate | M20 `user_type` only, M21 `is_superuser` only | M20 KILLED; **M21 SURVIVED at `d2a004f`**, KILLED at `6811527` |
| Unmetered AI | M22 refusal branch removed, M23 condition inverted | 2/2 KILLED |
| Allow-list | M24 extraction filter removed, M25 generate filter removed, M26 draft-save re-filter removed, M27 allow-list widened with `status`/`teacher` | 4/4 KILLED |
| Teacher read-only | M28 removed | KILLED |
| Fail-closed validators | M29 Topic fail-open, M30 Course fail-open | 2/2 KILLED |

**Survivor 1 — M18, equivalent by two-layer defence.** Removing only
`SaveGeneratedAssignmentDraftSerializer`'s topic check leaves
`AssignmentSerializer.validate`'s own `topic.course != course` check, which
refuses the same request. M19 removes both and is killed, so the behaviour is
guarded; no single test is missing. Accepted by the Senior Manager on that
basis.

**Survivor 2 — M21, a REAL gap, now closed.** `HasCreditBalance` bypassing on
`is_superuser` alone survived, because the credit-gate tests attacked only
with `user_type=SUPER_ADMIN` accounts and never with the opposite shape (a
`createsuperuser` account: `is_superuser` True, `user_type` TEACHER). Closed
in `6811527` with a test asserting such an account with an empty wallet is
refused with 400 "Insufficient Credits" and writes no billing rows. M20–M23
were re-run on `6811527`: 4/4 killed, restores verified, workers clean.

**Not re-run after `6811527`:** no guard changed (`6811527` is tests only), per
H3.4. `CourseSerializer.validate_session` has no no-context mutant because
`CurrentUserDefault` raises `KeyError` without a request, making such a mutant
equivalent; the reachable form (an unauthenticated request in context) is
covered by M30. Accepted by the Senior Manager.

Logs: `docs/evidence/h18_h19/g2_battery_d2a004f/` (results.json,
battery_run.log, per_mutant_logs.tar.gz) and
`docs/evidence/h18_h19/g2_rerun_6811527/`.

## 9. Independent adversarial replay (Gate 4, H5.1)

| Front | Session | Commit | Result |
|---|---|---|---|
| #10 AI-output field writes | red-team-aifile (22) | **`6811527`** (the landing commit) | **Contract met on the landed SHA, all four sinks.** 10-test battery: the 4 exploits that PASS on `b744c9f` now FAIL — upload create, re-extract MUTATE, poisoned-snapshot save, and the async Celery worker path (`upload_assignment_async.apply()` end to end), with `teacher` staying None and `status` staying DRAFT. Control, 2 concurrency and 3 bypass tests pass: case and alias variants (`Status`, `STATUS`, `Teacher`, `teacher_id`, `" status "`), extra privileged keys (`id` hijack, `course` retarget, `ai_generated`, `was_overridden`, `is_superuser`), and `teacher` via the serializer. 22 independently confirmed `git diff d2a004f 6811527` is empty for all four source files. Evidence: branch `task/redteam-aifile` `b5b9df8`, `docs/evidence/security_replay/aifile/replay_10_on_landing_6811527.log` (earlier passes: `818dbe1`, and `0d42b6e` on `d2a004f`) |
| H-18 course IDOR (T2) | red-team-tenancy (25) | `b744c9f` and **`6811527`** | **CLEAN on the landing commit: 0 of 7 core attacks and 0 of 3 supplemental attacks succeed**, each answered 400 "Course: You do not have access to this course.", with no DB change: X1 sync create, X2 create-async, X3 PATCH, X4 update-async (attacker holding credits, so it blocks on the course and not on credit), X5 create-async with a foreign course and topic, X6 own course with a foreign topic (400, unchanged), X7/D1 another teacher's draft (404), D2 own draft with a foreign topic (400). Positive control on the same commit: own-tenant session, course, topic and draft creation and own-course create-async all still succeed (202). Evidence: `task/redteam-tenancy`, `docs/evidence/security_replay/tenancy/logs/idor_assignment_course_6811527_20260917T190745Z.json` and `idor_supplemental_6811527_20260917T190800Z.json`. **Pre-fix proof:** on `b744c9f`, HTTP with real JWT, these all LAND a cross-tenant write into the victim's course — create-async, PATCH `partial_update`, PATCH `update-async`, and create-async with a foreign topic. Sync `POST /assignments` is the same unguarded write but rolled back on an AI denial in their environment, recorded as AI-gated rather than ownership-defended. Draft-save was already tenancy-safe pre-fix (foreign draft 404, foreign topic 400); the allow-list there is #10 scope. Evidence: `idor_assignment_course_b744c9f_20260917T175734Z.json`, `idor_supplemental_b744c9f_20260917T181652Z.json`. **Scope note from 25:** its proof used two individual teachers. `validate_course` compares `teacher_id` only, so cross-school and same-school-colleague take the identical code path; both are covered by my own suite over HTTP (§5), and 25 is adding explicit school-fixture cases as an addendum |
| H-19 superadmin (T4) | red-team-authz (38) | `b744c9f` done, `6811527` in flight | **Pre-fix proof done:** on `b744c9f` a single-flag SUPER_ADMIN bypasses `HasCreditBalance` — a control TEACHER with 0 credits gets 400 "Insufficient Credits" while the single-flag account gets 404, i.e. the gate was skipped and the handler ran. Independently matches §3 H-19b. Evidence: `docs/evidence/security_replay/authz/logs/surface1_credit_gate_b744c9f.json`. The `6811527` replay was PENDING at the time of writing |

Gate 4 is **PARTIAL** until the T2 and T4 replays return clean on `6811527`.
All replays are LOCAL-REAL (real JWT through the full view/serializer stack);
the deployed HTTP replay belongs to Gate 8 and is blocked on the sandbox being
redeployed to current code.

## 10. Final production gate (Gate 10)

**NOT PASSED as of 2026-09-17 21:40. The commit that will be gated is
`af23012`, not `6811527`.**

### Attempt 1 — `6811527`, run r1: FAILED

| Step | Result |
|---|---|
| Isolation | dedicated detached worktree `../Grade-Automator-Plus-h18h19-gate-6811527-r1`, fresh DB `test_h18h19_gate_6811527_r1`, no `--keepdb`, `systemd-inhibit` held |
| Infrastructure | PostgreSQL 18.6, Redis 8.0.5, Python 3.12.10, Django 5.2.6 |
| `pre-commit run --all-files` | exit 0 |
| `scripts/check_migration_safety.py --base beta` | exit 0 |
| `makemigrations --check --dry-run` | exit 0 |
| `manage.py check` | exit 0 |
| Full suite | **exit 1: Ran 4306 tests in 6128 s, FAILED (errors=6, skipped=24)** |
| Teardown | test DB destroyed, 0 "other sessions" lines, `pg_database` 0 afterwards |
| Fingerprint | identical before, after the static checks and after the suite |

**The 6 errors were caused by this change, and were not flakes.** Three
suites built a `SUPER_ADMIN` user *without* `is_superuser` and expected the
old single-flag behaviour — precisely the account shape H-19 closes:

| Test | Expected (pre-H-19) |
|---|---|
| `ai_processor.tests_grading_hardening.GradingFallbackModelRestrictionTest` ×3 | a `user_type`-only SUPER_ADMIN reaches `__ai_model` so fallback routing can be observed |
| `billing.tests.test_execute_graded_task.UserTypeDispatchTests` ×2 | a `user_type`-only SUPER_ADMIN takes the unmetered branch |
| `users.tests_credit_balance_permission.HasCreditBalanceTests.test_super_admin_is_allowed_with_no_wallet` | a `user_type`-only SUPER_ADMIN passes the credit gate with no wallet |

All six now raise `AIFeatureNotAvailableError` or `ParseError`, which is the
intended new behaviour. **Why my earlier runs missed them:** the interim runs
covered `assignments`, `users` and `classrooms` — not `billing` or
`ai_processor`. The full suite is what caught it, which is the argument for
Gate 10 existing at all.

Fixed in `af23012` with the Part I Gate-1 four-point record in the commit
message: the three suites' own superadmin fixtures now hold both flags, and
two tests were ADDED (`test_single_flag_super_admin_is_not_unmetered`,
`test_createsuperuser_account_is_not_unmetered`) so those modules encode the
new rule where they live. No assertion was weakened, and no test was changed
merely to make it pass. Verified locally: those three modules, 57 tests, OK.

Log: `docs/evidence/h18_h19/g10_attempt1_6811527_r1_FAILED_full_suite.log.gz`.

### Attempt 1 — run r2: KILLED, not evidence

r2 started automatically after r1 and was killed deliberately ~2 minutes in,
once r1's failures were understood, so it would not consume a quiet host
reproducing a known failure. Its orphaned test database was confirmed dropped
(`pg_database` 0, 0 connections). **A truncated run is not a gate result and
is not counted.**

### What must still happen

Two consecutive clean strict full runs on **`af23012`** (H10.2), neither yet
run. Both were impossible before the machine was powered off for the night;
they are the first action at 09:00.

## 11. Diff scope (doctrine: no unnecessary scope)

Source: `ai_processor/services.py` (+11 lines, one branch),
`assignments/serializers.py`, `assignments/services.py`,
`assignments/views.py`, `classrooms/serializers.py`, `classrooms/views.py`,
`users/permissions.py`, `users/views.py`. Tests: 8 new modules. No model,
migration, dependency or unrelated behaviour changed;
`makemigrations --check` reports no changes.

Agreed file carve with the two sessions sharing these files:
**fix-refusal-handling (04)** owns `dashboard/tasks.py`,
`assignments/tasks.py`, `dashboard/views.py`, `billing/views.py`,
`users/exceptions.py` and `ai_processor/services.py:765-768/818-821`;
**fix-tenant-leak (57)** owns `CustomUserViewSet` in `users/views.py` and
`StudentCourseViewSet` in `classrooms/views.py`. This branch touches none of
those ranges.

## 12. What remains open

1. **Gate 8 is PARTIAL — needs the user's written sign-off (H1.3).** Everything here is LOCAL-REAL. No request was made against the deployed beta, and no deployed configuration, worker or webhook routing was exercised.
2. **Gate 4 is PARTIAL** until red-team-tenancy (25) and red-team-authz (38) report clean on `6811527`.
3. **Two new skips, both opt-in and accepted by the Senior Manager (H2.3):** `SixThousandStudentSchoolTest` (`RUN_LOAD_TESTS=1`, 13 minutes to build the school) and `RealProviderSuperadminBothFlagsTest` (`RUN_REAL_AI=1`, billed). Both follow the existing pattern of `assignments/tests_load.py` and `assignments/tests_real_extraction.py`, and both were run for this evidence.
4. **Frontend:** a client that sends a course the teacher does not own now gets 400 `course: "You do not have access to this course."`. Legitimate clients only send their own courses, but this was not checked against the frontend code.
5. **Not fixed here, owned elsewhere:** the refusal-handling cluster (04); the free-plan activation suspect (7e); `CustomUserViewSet.create`'s unreachable either-flag check; and `CustomUserManager.create_superuser()` still leaving `user_type=TEACHER`, which is what made H-19 reachable.
6. **Landing:** `6811527` is not merged. Landing needs the Senior Manager's review and the user's approval, by `merge --ff-only` after building and verifying the merge in a temp worktree.
