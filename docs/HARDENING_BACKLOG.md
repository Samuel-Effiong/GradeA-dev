# Hardening Backlog

Tracked follow-up work carried out of the codebase audit
(`docs/CODEBASE_AUDIT_SECTIONS.md`). Everything here is **work to be done**,
not debt to be admired: each item carries a scope, acceptance criteria, and
the verification evidence required before it can be closed.

An item is closed only when its acceptance criteria are met **and** the
evidence below has been produced and recorded in the item's own notes.
"Tests pass" is not evidence on its own — the audit repeatedly found suites
that passed while the behaviour they claimed to cover was broken.

## Verification standard — the gate, not a wish-list

Set by the owner, 2026-09-09. **No item closes on "the tests pass."** Every
fix is treated as a production-ready change and must be battle-tested against
the actual stack and realistic failure conditions.

The required order is:

> **implement → verify on real infrastructure → stress → attack →
> mutation-test → failure-test → regression-test → measure →
> document evidence → close**

| # | Requirement | Means |
|---|---|---|
| 1 | **Real infrastructure** | Real PostgreSQL and real Redis. Mocks and LocMem are not evidence — a LocMem run of a `delete_pattern` test passes while proving nothing, because LocMem has no `delete_pattern`. |
| 2 | **Real service layer / live endpoints** | Exercised through the actual services and running API (gunicorn), not only the Django test client. |
| 3 | **Functional** | Normal paths, asserted on values and row identity — never on status codes alone. A leaking endpoint returns 200 like a correct one. |
| 4 | **Adversarial / security** | Written from the attacker's side; the test attempts the breach and asserts it failed. |
| 5 | **Concurrency & stress** | Realistic volumes, real threads, barrier-synchronised, multiple tenants operating simultaneously, including large imports. |
| 6 | **Mutation** | Revert or weaken each protection; the relevant tests must fail. Record the counts. An unproven fix is not a fix. |
| 7 | **Failure simulation** | Redis unavailable / slow / recovering, DB contention, retries, partial failures, interrupted operations. |
| 8 | **Integrity properties** | Transactional integrity, idempotency, race-condition safety, recovery behaviour. |
| 9 | **Measurement** | Before/after Redis commands and SCANs, DB queries, latency percentiles, memory, CPU, connection usage. Numbers, not adjectives. |
| 10 | **Targeted attacks** | Cross-tenant leakage, stale data, missed invalidation, collateral deletion, cache stampedes, duplicate operations, authorization bypasses. |
| 11 | **Production-scale cases** | Realistic volumes, not small fixtures. |
| 12 | **Regression + integrated run** | Relevant suites green, then a final full-stack verification. |
| 13 | **Documented evidence** | Acceptance criteria written down BEFORE the work; evidence recorded against them at close. |

Where a requirement genuinely does not apply to an item, the item must
**state so and why** — silence is not an exemption.

## Owners

Owners are assigned by **management**. The "Proposed" column is a suggestion
based on which section the work sits in, not an assignment — it is there to
speed that decision up, not to pre-empt it.

| ID | Item | Priority | Proposed owner | Status |
|---|---|---|---|---|
| H-1 | System-wide cache invalidation architecture | **Highest** | Backend/infra lead | **OPEN: step 4 (wildcard removal) in batch-2b verification** on `task/h1-step4-wildcard-removal` (`24d5ec0`), not landed (status refreshed 2026-09-30). Earlier: **Stage 2: COMPLETE (33/33 applicable migrated). Stage 3 item 7 (user-row fan-out): FIXED. Stage 3 item 2 (stampede protection): DECIDED 2026-09-15 — none for now, by measurement, with re-evaluation triggers. H-1 OVERALL: OPEN** — Stage 3 legacy wildcard removal in progress, under its own full gate. |
| H-2 | Full-suite exit code / test DB connection leaks | High | Whoever owns CI | **Closed** — fixed, verified with three consecutive clean full runs and failure/mutation simulation |
| H-3 | `student123!` account remediation | High | Product + backend | **Code live on beta; production run pending founder.** The command landed via batch-1 (`e7e4bdf`) and the token_epoch follow-up via batch-2a (`755aa27`). The founder hasn't run the dry-run/execute on any deployment; on main it needs the main release first (status refreshed 2026-09-30). |
| H-4 | Duplicated `delete_cache_patterns` implementations | Medium | Folds into H-1 | **FIXED on branch `task/h1-step4-wildcard-removal` (`8f950b7`), not landed**; rides batch-2b with H-1 step 4 (status refreshed 2026-09-30). |
| H-5 | `full_clean()` on the grading hot path | Medium | Section 7 (students) | **FIXED on branch `task/h5-full-clean-hot-path` (`1c0742f`; 1a VERIFIED-WITH-NOTES `dd9d875`), not landed**; not yet in any batch (status refreshed 2026-09-30). |
| H-6 | `CourseCategoryViewSet` — unrouted and broken | Medium | Section 3 (classrooms) | Not started |
| H-7 | `direct_add_student` response shape and status code | Low | Section 3 + frontend | Not started |
| H-8 | Test file naming / stray docs | Low | Section 3 | Not started |
| H-9 | Test suite shares one Redis DB (isolation) | High | Whoever owns CI | **CLOSED (owner, 2026-09-15)** — verified on `29cc1c7`: two full suites ran concurrently on the same Redis, 4,153 tests OK each, exit 0, clean teardown, both per-process namespaces live; old code collided 5/8, the fix 0/8. Evidence: `docs/evidence/H9_REDIS_ISOLATION_EVIDENCE.md`. Overlapping runs are safe only between trees that contain `29cc1c7` |
| H-10 | `super-admin/dashboard/students` 480-query N+1 | High | Section 8 (dashboard) | **CLOSED (2026-09-14)** — Section 8 remediation merged to beta `2715c64`; strict gate passed there (4,031 OK); query count flat |
| H-11 | Synchronous billed AI calls inside `students` request handlers (`upload`, `grade`, `PATCH raw_input`) | **High - release-blocking** | Section 7 (students) + frontend | **OPEN.** 2026-09-14: async edit path built and gated, V-2..V-4/V-6 closed, duplicate-request guards added; **remaining: client migration confirmed, then retire the three synchronous routes** (see item). Not a blocker for the Section 9 promotion to beta (owner, 2026-09-15); remains release-blocking for production |
| H-12 | Commented-out code (flake8 E800) burn-down | Low | Each file's section owner (history in H-12) | **CLOSED (2026-09-16)** — repository-wide, 0 files / 0 hits, `--per-file-ignores` removed, register deleted; landed on beta at `a7c81a4`; see `docs/evidence/ITEM9_E800_BURNDOWN_EVIDENCE.md` |
| H-13 | Uploads while grading is RUNNING | Medium | Product + Section 7 | **CLOSED: landed on beta with Section 7 (`ac731a9`, 2026-09-14)** (status refreshed 2026-09-30). Decision: refuse (409); see item. |
| H-14 | School-admin summary rebuild cost (cache family 23) | Medium | Section 8 (dashboard) | **CLOSED: landed on beta via batch-1 (`e7e4bdf`, 2026-09-29)**; see `docs/evidence/H14_SCHOOL_ADMIN_SUMMARY_EVIDENCE.md` (status refreshed 2026-09-30). Was — 1.4 s cold rebuild at 240 courses/school, growing with rows processed; performance issue, not a stampede justification (owner, 2026-09-15) |
| H-15 | `global`-scoped per-user cache families invalidate as a herd | Medium | Backend/infra lead (H-1 follow-up) | Open — one change anywhere expires every user's copy (my_courses, superadmin dashboards); 50-student herd p50 792 ms / p95 1,262 ms at realistic scale |
| H-16 | Teacher submission list issues 63 queries per page | Low | Section 7 (students) | **CLOSED: landed on beta (`ec26b2e`, 2026-09-15)** (status refreshed 2026-09-30) — `select_related` on the list queryset; 63/304 → flat 4; see item |
| H-17 | Course payload leaked draft assignments and classmates' real emails to student viewers | **High - security** | Section 3 (classrooms) | **CLOSED (2026-09-16)** — `CourseSerializer` served every assignment (draft/unpublished included) and every enrolled student's real email address to a student viewer, regardless of assignment status or whose row it was. Fixed: `get_assignments`/`get_assignment_count` filter to `PUBLISHED` for a student viewer; `get_students` nulls out `email` for every row but the viewer's own. 15 dedicated tests (`classrooms/tests_course_payload_student_exposure.py`), 2 mutation tests (both killed), 250-test `classrooms` regression clean, query counts flat across roster size (roster=2 and roster=6 both 7/8/7/7). Landed on beta `ee30f08` (merge of `task/course-detail-data-exposure` gated commit `1d920f8`). Teacher/other-viewer payloads unchanged. |
| H-18 | Assignment writes accepted any course, any topic, and any field the AI emitted | **High - security** | Section 4 (assignments) | **CLOSED: landed on beta via the fast-path batch (`6039b87`, 2026-09-21)** (status refreshed 2026-09-30) — `AssignmentTextSerializer.course` was an unscoped writable PK, so a teacher could create an assignment in another teacher's course or move their own into it, through THREE doors: create/create-async, PATCH, and PATCH update-async (which built the serializer with no request in context). Separately, AI extraction and generation output was saved through `AssignmentSerializer` whole, so injected text could write `status`, `teacher`, `course`, `topic`, `due_date` and more, at four sinks plus stored pre-fix draft snapshots. Fixed: `validate_course` (fail-closed), `update_async` passes context, `ai_assignment_content_only()` at three entry points, `teacher` read-only, and `TopicSerializer`/`CourseSerializer` validators fail closed. Gated on `6811527`; see `docs/evidence/H18_H19_ACCESS_CONTROL_EVIDENCE.md` |
| H-19 | Superadmin authority granted on a single flag in four places | **High - security** | Section 1 (users) + Section 3 (classrooms) + Section 5 (ai_processor) | **CLOSED: landed on beta via the fast-path batch (`6039b87`, 2026-09-21)** (status refreshed 2026-09-30) — `create_superuser()` leaves `user_type=TEACHER`, so `is_superuser` alone let a Django-admin account read and edit every user's Settings and any school's token usage; and `user_type=SUPER_ADMIN` alone let an account skip `HasCreditBalance` and take `execute_graded_task`'s unmetered branch - free, unlimited billed AI. All four now require both flags, as `IsSuperAdmin` does. The deny-side `or` in `license_service.py:319` and `users/serializers.py:175` is correct and unchanged. Gated on `6811527`; same evidence file |
| H-21 | Unrestricted discovery and activation of free/internal plans (unlimited free credits) | **High - security/billing** | Session `fix-free-plan` (task/free-plan-activation) | **LANDED on beta via the fast-path batch (`9a4629c`, 2026-09-21)**; the item's pre-production conditions (QA Gate 8, independent Gate-4 replay) are **pending confirmation** (status refreshed 2026-09-30) — any teacher could POST a free plan id to `/user-subscriptions` or `/subscription` repeatedly; each call replaced their subscription and granted a full monthly credit bucket (replay: 10 repeats = 100,000,000 raw credits spent). School admins could take TRIAL and the internal benchmark plan; `/subscription/plan` listed every plan to every non-student; inactive plans activated; a Stripe-billed subscriber could be moved to BETA in the app while Stripe kept billing; a licensed teacher could activate their school's license plan. Fixed by `billing/plan_policy.py` (explicit allow-lists for the self-service catalog and admin assignment, price and Stripe-price floors as extra refusals, never price as the eligibility rule), superadmin-only POST routes with a both-flags serializer check and a scoped plan lookup, and `SubscriptionService.activate_plan_without_payment` as the single no-payment path (active plans only; BETA teacher-only and once per user ever including pre-existing history; refused over a live Stripe subscription; license-track guard; all under a `CustomUser` row lock). Plan listings and `select-plan` now share one definition. Evidence: `docs/evidence/FREE_PLAN_ACTIVATION_EVIDENCE.md` — 97 dedicated tests, 26/26 mutants killed, 20 simultaneous requests x 10 rounds, injected DB/Redis/Stripe failures, real Stripe test-mode proof (app and Stripe state unchanged, 0 Stripe writes), replay 10/10 exploited on `b744c9f` and 10/10 refused on the fix, and an N+1 removal (809 -> 9 queries, 343 KB -> 2.7 KB at 808 plans). OPEN: **Gate 8 DEPLOYED-REAL on QA is required before any promotion to production** (billing tier: LOCAL-REAL + QA smoke is enough to land on beta, not enough for prod); independent Gate-4 replay by the red-team session; full-repository gate deferred to the batched integration gate; production plan configuration unverified (impact SQL in the evidence doc). |
| H-22 | Cross-teacher tenancy leaks: `my-students` served other teachers' course names, description, teacher name and grade; `/users/<id>` enrollment filters were a yes/no oracle on other tenants' enrollments | **Medium - security** | fix-tenant-leak (session 57) | **CLOSED: landed on beta via the fast-path batch (`0fe09a4`, 2026-09-21)**; Gate 4 PASS in `3180ec3` (status refreshed 2026-09-30) — both endpoints joined every enrollment a shared student had. Fixed by scoping the `my_students` prefetches to `course__teacher=user` plus a new `MyStudentsFilter`, and by replacing `CustomUserViewSet.filterset_fields` with a scoped `UserEnrollmentFilter`; the unrouted `StudentViewSet` copy was deleted (V-5, owner sign-off). 42 dedicated tests (22 fail on `b744c9f`), 16 mutants (15 killed, 2 equivalent), 20 threads x 10 rounds, 6,000-student scale with query counts flat at 5. Branch `task/my-students-prefetch-leak` tip `948d710`; Gates 4, 8 and 10 still open — see `docs/evidence/MY_STUDENTS_TENANCY_EVIDENCE.md`. |
| H-23 | ~95 pre-existing school-side (teacher/admin/subscription-owner, no students) email-logging call sites found across `billing/access_control.py`, `license_service.py`, `services.py`, `stripe_service.py`, `tasks.py`, `views.py`, `qa_time_travel.py`, `management/commands/backfill.py` and `users/signals.py`, mostly at INFO — same leaky-logging shape as the 11 confirmed student/user PII leaks fixed under Epic A's BE-A-04 cleanup, but not students, so out of that cleanup's scope | Medium — privacy/compliance, not tenancy | Proposed: whoever owns Epic A follow-up (audit-lead) or a dedicated session | **Not started.** Found and verified as true positives (not scanner noise) by privacy-guard (2026-09-22) during the Epic A BE-A-04 AST-based lint-rule build. Grandfathered into `scripts/pii_log_baseline.txt` (same convention as the H-12 E800 per-file burn-down) so the new PII-logging CI lint rule doesn't fail on pre-existing code — the lint rule catches any *new* instance of this pattern going forward; this item is the backlog for cleaning up the ~95 that already exist. |
| H-28 | P1b: irreversible Stripe mutations run inside `transaction.atomic` in the licence billing paths. If the database transaction rolls back after Stripe has already accepted the change (plan change, seat change, cancel), Stripe and our records disagree: the customer can be charged, or an invoice left open, for a change we do not record | Medium-High: billing divergence | Hardening Engineer (grade-automator-plus-d5) | **Parked, WIP (logged 2026-09-30 by the SM; revisit after batch-2b).** Branch `task/p1b-divergence`: Change 1 commit 1 has reproduce-first tests (18/18 fail on `b744c9f`); commit 2 adds the `LicenseStripeMutationIntent` model (no behaviour change); the SM's ruling on resolving ESCALATED intents is recorded (`7bd028a`); commit 3 is in progress, checkpointed as `cfd8d9e`. `cfd8d9e` was committed with --no-verify as a parked save-point; the next real commit must pass all hooks. Not verified. Its billing migration 0070 collides with H-56's and H-62's, so whichever lands later needs a merge migration. |
| H-38 | A teacher removed from a school keeps reading, writing and deleting that school's data: `remove_teachers` deactivates the credit allocation but never clears `user.school` and never touches the courses the teacher built in the school's sessions, and `course.teacher == user` (permanent) was the only ownership test at ~30 sites across assignments, classrooms, students, dashboard, users and billing | **High - security** | Hardening Engineer (grade-automator-plus-d5) | **CLOSED: landed on beta via batch-1 (`e7e4bdf`, 2026-09-29)**; the founder-run exposure query and follow-ups are in H-38-F1 (status refreshed 2026-09-30). Reproduce-first on beta: 33 probes, 23 open. One landing, as decided: part 1 clears the school link on removal; part 2 routes every teacher-scoped site through one shared rule (`teacher_course_access_q` / `reachable_courses` / `teacher_can_reach_course` in `classrooms/models.py`, which ANDs `course__teacher=user`, so it can only narrow earlier tenancy fixes), plus a static sweep test (`classrooms/tests_teacher_access_sweep.py`) that fails on any unlisted direct-owner scoping. The course owner stays in place (founder decision). Mutation: 20/20 killed on the original sites, then 8/8 on the rebase sites. **Two regressions/gaps caught before landing and recorded plainly:** (1) an earlier version of the fix put the helper's OR clause under `select_for_update()` in `save_generated_assignment_draft`, so Postgres rejected the outer join and AI draft saving 500'd for EVERY teacher; a loose test assertion hid it; fixed by an unlocked access check then a pk lock, with strict assertions and an active-teacher positive control now the pattern on every H-38 route (`docs/evidence/h38_part2/select_for_update_outer_join_regression.md`); (2) rebasing onto `4b902fc` brought in 5 new direct-owner sites (H-22's my_students prefetches, `MyStudentsFilter`, `UserEnrollmentFilter`, and H-18's `AssignmentTextSerializer.validate_course`), all reproduced as real removed-teacher leaks and fixed; 2 harness-only lines allowlisted with reasons; plus a 6th leak found in core review, `roster_import._find_existing_student_by_name` (a removed teacher's no-email roster row attached a same-named School A student), reproduced and fixed, its allowlist entry removed (`docs/evidence/h38_part2/rebase_sweep_hits.md`). Evidence: `docs/evidence/h38_teacher_removal/`, `docs/evidence/h38_part2/`. OPEN: Gate 10 full-suite run (slot from Integration & Release), independent verification, and the production exposure query (`docs/evidence/h38_part2/production_exposure.sql`, read-only, founder-run). Follow-ups in H-38-F1. |
| H-38-F1 | H-38 follow-ups: courses left with a removed owner, and removed-teacher paths still guarded only by the static sweep | Medium | Proposed: Hardening + product (reassignment) | **Not started (logged 2026-09-28).** (a) **Admin "reassign course" feature**: because the owner stays in place, a removed teacher's school courses become unreachable to every teacher; a school admin needs a way to reassign them to another teacher, and the dashboard should show orphaned courses. Product decision needed on who may reassign and what history moves. (b) **Async upload race (T1)**: the Celery task `upload_assignment_async` re-fetches the course through `reachable_courses`, but no test covers a teacher removed AFTER the request is accepted and BEFORE the task runs. Probe design: create the batch session through the real route as an active teacher, remove the teacher through `remove_teachers`, then run the task synchronously (`upload_assignment_async.apply(...)`) with the queued arguments, and assert it refuses, writes no Assignment, and marks the processing task failed rather than raising a 5xx-shaped error; add the active-teacher positive control (same sequence without removal writes the assignment). (c) **Other sweep-only sites** that still lack a behavioural removed-teacher probe: `CourseViewSet.remove_student`'s object-level guard in isolation, and the StudentCourse submissions prefetch; each needs a strict probe plus a positive control. (`batch_upload` got its dynamic probe in the H-38 landing.) (d) The unrouted `StudentViewSet` flagged by the mutation pass was already deleted on beta (`e0b1640`, V-5); nothing left to do. See `docs/evidence/h38_part2/mutation_results_final.md`. |
| H-41 | `students.tests_grading_redelivery_live.GradingRedeliveryLiveTest.test_5_concurrent_submissions_with_one_redelivery_each_grade_exactly_once` failed for real on a GitHub Actions CI run (`AssertionError: 'FAILED' != GradingState.DONE`) under `--parallel 4`, and the same failure was never seen locally, including 3 full-suite reproduction attempts on this box deliberately constrained to match or exceed CI's real CPU pressure (`taskset -c 0-3` x2, `taskset -c 0-1` x1 — all 4553 tests, all clean) | Medium — real but unreproduced; contained by H-39-adjacent tblib fix so it can no longer crash the whole run, but the underlying race is still open | Proposed: whoever owns the live-Celery redelivery test next | **Open: known flake, unreproduced** (status refreshed 2026-09-30). gate-runner (2026-09-23): CPU core count/oversubscription on this box is ruled out as the trigger (reproduction attempted up to 4x oversubscribed, never reproduced) - the remaining suspect is network-latency variance specific to GitHub Actions' Docker-networked Postgres/Redis service containers (reached over the docker bridge, not a true localhost socket the way this box's isolated env is), which this box cannot faithfully reproduce without artificial network jitter (e.g. `tc netem`) injected into the repro, not yet attempted. This is a genuinely timing-sensitive live-broker test (`WAIT = 45s`, real Celery worker, real Redis redelivery) - a single occurrence on one CI run is weak evidence of a reliable defect, but not zero. Until reproduced, treat as a flake candidate to watch for recurrence on real CI runs, not a proven bug in the `--parallel` prefix work (H-9's fork-prefix fixes were independently verified via `multiprocessing.get_context("fork")` probes and are not implicated by this failure's causal chain - see the tblib/pickle analysis this entry is filed alongside). |
| H-42 | `send_user_activation_email` (`users/services.py`) routes school admins to `FRONTEND_DOMAIN` (the teacher app) alongside teachers, same as the now-fixed school-admin invitation email was doing — but the school-admin frontend is a genuinely separate app that refuses other roles | Medium (wrong domain) escalated to **High — real dead-end account** once traced end to end | privacy-guard | **CLOSED (2026-09-23)** — investigation confirmed the wrong-domain bug was the smaller half: `POST /auth/otp` (`otp_type=VERIFY_EMAIL`) is `AllowAny`, takes only an email, and has no `user_type` restriction, so it was reachable for a pending school admin (`SchoolWithAdminSerializer`-created, `is_active=False`, no usable password, real 7-day `activation_token`). Hitting it overwrote that token with a 15-minute generic one and emailed a `/verify-email` link (wrong domain) whose completion endpoint (`/auth/verify`) has no password field at all — and once it set `is_active=True` and cleared the token, `/register/school-admin`'s `is_active=False` filter could never match again. Net effect: an active, verified account with an unusable password and no remaining path to ever set one. Reproduced end-to-end with a failing test against unfixed code first (`classrooms/test_school_admin_otp_deadend.py`), then fixed: `send_user_activation_email()` now recognizes `SCHOOL_ADMIN` and delegates to a new `resend_school_admin_invitation()` (`classrooms/serializers.py`), which reissues a fresh 7-day token and resends the real invitation email instead of ever building the generic, password-less activation email for this user_type — mirroring the existing precedent for invited teachers, who don't go through the generic flow either. 7 dedicated tests, full regression 4571/4571 (256.6s, `--parallel 4`), independently verified by the SM (own worktree, own full-regression run, matching numbers). Landed on beta by fast-forward (`2f2b9bc` → `2bad9c6`) and pushed to origin, user-approved.
| H-39 | No test-suite guard against real outbound network calls — a test that forgets to mock a third-party call (Stripe, etc.) silently succeeds locally against real credentials and only fails later, on CI, against fake ones | High — this exact gap cost a two-CI-run diagnosis | Proposed: whoever owns test infrastructure next (gate-runner nominated it) | **CLOSED: landed on beta (`be78221`, 2026-09-28)**; 1a VERIFIED-WITH-NOTES `3856e40` (status refreshed 2026-09-30). Reinforced-priority per gate-runner (2026-09-23), directly motivated by `task/flaky-stripe-timeout-diagnosis`: `billing.tests.test_free_plan_activation_security.ActivationFailureRecoveryTests.test_stripe_timeout_on_the_allowed_checkout_leaves_no_local_change` mocked `stripe.checkout.Session.create` but not `stripe.Customer.create`; the real call silently succeeded locally (`.env`'s `LOCAL_STRIPE_SECRET_KEY` is a real Stripe test-mode key) and deterministically failed on CI (fake placeholder key rejected by Stripe's own auth check). A guard that fails any test making a real outbound HTTP call (e.g. patching `socket.socket`/`urllib3` at the test-runner level with an allowlist for the local Postgres/Redis sockets) would have caught this on the very first local run instead of needing two failed CI pushes to diagnose. Complements, does not replace, `scripts/isolated-test-env.sh` (which gives CI-matching fake credentials but doesn't itself block a stray real call from a differently-named env var). |
| H-46 | 178 real (non-false-positive) mypy errors newly surfaced by wiring django-stubs/djangorestframework-stubs into the mypy pre-commit hook (`task/mypy-django-stubs`) — genuinely new `file:line` sites the plugin now understands well enough to flag, that had zero mypy complaint at all before (distinct from the 581 pre-plugin false positives the same change resolved) | Mixed — see triage in this cell; not blocking, ratcheted via `pyproject.toml` `[[tool.mypy.overrides]]` (`ignore_errors = true` per pre-existing-error module) so these don't block unrelated commits | Proposed: section owners of the files involved (mostly billing/, students/, assignments/, classrooms/) | **Not started — logging/triage only, per this branch's scope; no fix attempted.** Full `file:line` list and pattern breakdown: `docs/evidence/mypy_django_stubs/EVIDENCE.md` section 5 (and `newly_surfaced_errors.txt` in that directory). Triage by risk: **(a) Higher priority — likely real bugs:** nullable-field/FK access without a None-guard (`Item "None" of "X \| None" has no attribute "Y"`, largest cluster, concentrated in test files but flagging real optionality the production code paths share) and `Decimal \| None` passed to `float()` (~15 sites across `students/tests*.py`, `billing/tests/test_grading_refund_scope.py`) — a `None` slipping through either shape at runtime would raise, so these are worth a closer look even though most current sites are in tests. **(b) Lower priority — likely safe:** `request.user` (typed `CustomUser \| AnonymousUser`) passed into a typed FK lookup (~35 sites, mostly `classrooms/views.py`, `billing/views.py`, `assignments/views.py`, `students/views.py`) — these are almost all behind DRF permission classes that already guarantee an authenticated, non-anonymous user by the time the lookup runs, a runtime guarantee mypy has no way to see; likely a narrowing/cast cleanup, not a bug hunt. **(c) Needs a closer look — not yet triaged either way:** Stripe SDK argument-type mismatches (`str \| None` where the `stripe` stubs expect `str`, in `billing/stripe_service.py`, `billing/license_service.py`, `billing/live_qa/scenarios_license.py`) and the six `.annotate()`-result TypedDict `[union-attr]` sites in `classrooms/views.py` (mypy tracks the annotated fields as a TypedDict and doesn't yet see them dotted onto the base type the way the runtime code accesses them — could be a stubs-precision gap or a genuine access-pattern issue). |
| H-43 | `POST /auth/otp` (`users/views.py`) answers every call `202`, but the response `detail` string differs: `"If an account with that email exists, an OTP has been sent."` when no account matches, vs. `"An OTP has been sent if an account with that email exists."` when one does (either `otp_type`, including the H-22 locked-reset branch, which already shares the second wording). Same status, different text — an unauthenticated client that string-matches the body gets an account-existence oracle | Low — existence only, not credentials/PII; rides the existing `otp_request` throttle bucket, so rate-limited but not prevented | Security Engineer (grade-automator-plus-ed) | **IN BUNDLE 3 (`task/beta-batch-3`: `3af18a1`, record `7b2f816`), not yet on beta** (status refreshed 2026-09-30). Found 2026-09-28 by the Verification Engineer while verifying H-22 (`authz-l2-otp-counter`) — pre-existing on beta, not introduced or touched by that branch, not folded into it. Fix: make all three `detail` strings byte-identical; no behavior change, since an OTP is already only sent when an account exists. Deferred to its own branch after `task/verify-email-not-you` lands (SM decision, 2026-09-28). |
| H-45 | `AutoGrader.tests_redis_hygiene.SweepTests.test_delete_own_keys_takes_only_that_exact_prefix` fails intermittently (`AssertionError: 1 != 2`, one of the two expected keys not removed) under a full `--parallel 4` suite run, but not in isolation | Low — test-infrastructure flake, not a product defect; the test and the hygiene code it covers were untouched by the branch it surfaced on | Proposed: whoever owns test infrastructure next (H-39 track) | **Open: known flake** (status refreshed 2026-09-30). Found 2026-09-28 by the Verification Engineer verifying `task/h39-network-guard`: reproduced 0/3 alone and 0/2 under `AutoGrader.tests_redis_hygiene --parallel 4` in isolation, only seen once inside the full 4677-test suite. SM's suspect: key-prefix interference from another suite sharing the same Redis at the time — the machine-wide full-suite lock only serialises full runs against each other, targeted runs from other worktrees still overlap on the same real Redis instance. Not cross-checked against plain beta under equivalent load (would cost another full-suite run); accepted as noted rather than blocking on it. Worth a repro attempt (e.g. deliberately run a concurrent targeted Redis-touching suite alongside the full run) when someone picks it up. |
| H-51 | `POST assignments/upload-async` (`assignments/views.py`, `upload_async`) creates the `BatchUploadSession` and then checks each file's size **inside** the dispatch loop, after the earlier files' processing tasks have been created and launched. A later oversized file returns 413, but the earlier files are already queued: the client is told the upload failed while part of it runs, and the session is left half-queued (its `total_files` counts files that were never dispatched). `batch-upload` (`students/views.py`) already does it correctly: it validates every file up front, before any session or task exists | Medium: live on beta; inconsistent state and a misleading 413, no data leak | Hardening Engineer (grade-automator-plus-d5), batch-3 | **Not started (logged 2026-09-29).** Found during the Epic A S6/S7 route inventory (`docs/phase2/architecture/08a_epic_a_s6_s7_reason_codes_and_batch_design.md` §3), confirmed on beta `e7e4bdf` (session created, then size check inside the loop, then task launch). Fix: validate every file before creating the session, as `batch-upload` does. Acceptance: a test proving that an oversized **later** file produces a 413 with **no** `BatchUploadSession` and **no** processing tasks created or launched, plus a positive control where all files are within the limit. |
| H-52 | `AutoGrader/cache_generation.bump_generation(scope, entity_id)` bumps one counter directly and skips the on-commit re-bump that H-25 (`task/cache-commit-race`) adds to `bump_many`. Today that's safe, because no production code outside `cache_generation.py` calls it (0 callers on `e7e4bdf`, `168d57e`, `a86354b` and `bd2f016`), and its one internal caller is `_bump_now`'s non-pipelined fallback, which already runs under `bump_many`'s on-commit. A future direct caller inside a transaction would silently lose commit-race protection | Low: latent, 0 production callers | Hardening Engineer (grade-automator-plus-d5), batch-3 | **Not started (logged 2026-09-29).** Found by the Verification Engineer while pre-reading H-25. Kept out of H-25 by SM ruling, so its verified delta is unchanged. Fix: make it private (`_bump_generation`) and add a guard test that production code never calls it directly. Do **not** route it through `bump_many`: `_bump_now`'s fallback calls it, so that would recurse and queue duplicate on-commit bumps. |
| H-53 | `POST /auth/verify` (account activation) has no per-account failure budget. It has only the per-IP 5/hour `VerifyEmailThrottle`, against a 6-digit code valid for 15 minutes, so with enough IPs someone who registered with another person's address can guess the code and activate the account without the email link (same pattern as AUTHZ-L2) | Medium: security (account activation without mailbox control) | Security Engineer (grade-automator-plus-ed), batch-3 | **IN BUNDLE 3 (`task/beta-batch-3`: `d883ce5`, record `c45950e`), not yet on beta** (status refreshed 2026-09-30). Logged 2026-09-29. Fix: an L2-style per-account lock (5 wrong codes → the code is invalidated and must be re-sent), with a strict-code test and a mutation. |
| H-54 | L2 follow-up: the 429 message's `HH:MM UTC` is not pinned against the server time zone. The Verification Engineer's localtime mutant (formatting the lock expiry in local time instead of UTC) survives because it is **equivalent while `TIME_ZONE=UTC`**, so a later time-zone setting change would silently show the wrong time | Low: test gap, no defect today | Security Engineer (grade-automator-plus-ed) | **Not started; unblocked** (L2 landed on beta `755aa27`) (status refreshed 2026-09-30). Logged 2026-09-29. Fix: pin the message's `HH:MM UTC` in a test under `override_settings(TIME_ZONE='Africa/Lagos')`, which kills the localtime mutant. |
| H-55 | H-3 remediation follow-up: the `token_epoch` increment is not pinned. The Verification Engineer's set-to-1 mutant (the remediation sets the epoch to 1 instead of incrementing it) survives, because the test starts from an epoch where the two agree | Low: test gap, no defect today | Security Engineer (grade-automator-plus-ed), batch-3 | **Not started (logged 2026-09-29).** Fix: pre-set the epoch to 3, assert it is 4 after `--execute`, and assert that an epoch-1 token is still rejected. This kills the set-to-1 mutant. |
| H-56 | Nine NOT NULL columns, added by eight migrations newer than production (origin/main `9c21bee`), have only a Django-side default and no database default: `users_customuser` `failed_login_attempts` (0036), `must_change_password` (0037), `token_epoch` (0039); `billing_creditwallet` `dispute_deficit_credits` and `is_consumption_blocked` (0063), `refund_deficit_credits` (0064); `billing_stripeevent` `recovery_attempts` (0067); `assignments_assignment` `updated_at` (0038); `dashboard_studentriskalertstate` `alert_pending` (0003). Code older than a column's migration omits it from INSERT, and the INSERT fails with a NOT NULL violation: user creation, wallet `get_or_create`, every Stripe webhook, assignment creation, the daily risk task. **A code-only rollback is therefore not safe** | Medium-High: rollback safety for a live release | Hardening Engineer (grade-automator-plus-d5), batch-3, after H-1 step 4 | **IN BUNDLE 3 (`task/beta-batch-3`: `1f52219`, record `e4f3932`; 1a VERIFIED-WITH-NOTES), not yet on beta.** All 14 NOT NULL columns get DB-level defaults (users 0040, assignments 0040, billing 0070, dashboard 0004), plus the rollback guard test (status refreshed 2026-09-30). Logged 2026-09-29; scope decided by the SM the same day. Found by the Integration & Release Engineer, whose tested stop-gap SQL for a rollback is `~/Documents/Projects/GAP-rollback-set-defaults.sql` (outside the repo). Scope: `db_default` on **all 14** NOT NULL AddFields newer than `9c21bee`. That is the 9 above plus 5 on tables created in the same commit `df305cc`, which no rollback target can have without the column: `paymentdispute.deficit_by_wallet` (0066), `pricereconciliationresult` `previous_local_product`/`synced`/`synced_fields` and `pricereconciliationrun.synced_count` (0069). We run Django 5.2.6, so use model `db_default` (`assignments.updated_at`: `db_default=Now()`) plus the generated AlterField migrations. Guard test: scan AddField/AlterField (not CreateModel) in every migration after 9c21bee's per-app head (users 0035, billing 0058, assignments 0037, classrooms 0016, students 0025, dashboard 0002, ai_processor 0005). Fail on any NOT NULL field without `db_default`, with an **empty allow-list**. Reproduce-first raw INSERTs omitting the column, for the 9 rollback-relevant columns only: they fail before and succeed after. Verification by the Verification Engineer. *(An earlier count of 27 used a stale local `main`, de92b08, and is withdrawn.)* |
| H-57 | `PATCH /license-subscriptions/<id>` with `max_seats` returns 200 but silently ignores the field, so the caller believes the seat count changed when it did not | Low-Medium: silent no-op on a billing field | Security Engineer (grade-automator-plus-ed), batch-3 | **Not started (logged 2026-09-29).** Found by the Security Engineer during the licence-seat hotfix (`324164f`). Fix: either reject `max_seats` with a 400 that points to `update_seats`, or apply it through the same seat check as `update_seats`. Plus a strict-code test and a mutation. |
| H-58 | Licence creation counts a duplicated teacher email twice against `max_seats`: the same address listed twice in the create request uses two seats, so a school can be refused (or charged) for seats it does not need | Low-Medium: billing correctness | Security Engineer (grade-automator-plus-ed), batch-3 | **IN BUNDLE 3 (`task/beta-batch-3`: `39fee13`, record `28c4b03`), not yet on beta** (status refreshed 2026-09-30). Logged 2026-09-29. Found by the Verification Engineer while reviewing the licence-seat hotfix (`324164f`). Fix: de-duplicate teacher emails (case-insensitively, after normalisation) before the seat count, with a test that a duplicated address uses exactly one seat. |
| H-59 | The licence create serializer falls back to `max_seats = 0` when the field is omitted, while the model default is 1. On the STRIPE billing method that can create a checkout with quantity 0, and the payment webhook then refuses the licence **after the customer has paid** | Medium: billing (payment taken, licence refused) | Security Engineer (grade-automator-plus-ed), batch-3 | **IN BUNDLE 3 (`task/beta-batch-3`: `39fee13`, record `28c4b03`), not yet on beta** (status refreshed 2026-09-30). Logged 2026-09-29. Found by the Verification Engineer while reviewing the licence-seat hotfix (`324164f`). Fix: make the serializer fallback match the model default (1), or require `max_seats` and reject 0 with a 400 before any checkout is created, with a test that an omitted `max_seats` never reaches Stripe with quantity 0. |
| H-60 | `update_seats` returns Stripe's raw `ValueError` text in its 400 response body, so provider internals reach the client (the same shape as QA-ERR-03's "no raw library text") | Low: information exposure, no credentials | Security Engineer (grade-automator-plus-ed), batch-3 | **Not started (logged 2026-09-29).** Found by the Verification Engineer (note N3) while reviewing the licence-seat hotfix (`324164f`). Fix: map the failure to a plain, fixed client message and keep Stripe's text in the server logs only, with a test using a sentinel string in the raised error that asserts the sentinel never appears in the response. |
| H-61 | Test fixtures use addresses on a real mail domain: 39 `.py` files, almost all tests, hold 136 distinct `@gmail.com` literals (e.g. `users/tests_activity_middleware_load.py`). They are synthetic test strings, not real people's data, but they land in test logs (the app logs the address when a test user is created) and would reach a real inbox if a test ever reached a real mail backend | Low: latent only. Today the test network guard and the locmem email backend stop any send | Owner TBD | **Not started (logged 2026-09-30).** Found by the Hardening Engineer while trimming the H-1 step 4 per-module logs into evidence (`cc14bb0`); logged as H-61 by the SM. Fix: move test fixtures to `example.com` or the `.test` TLD, plus a guard test that rejects a real mail domain in test-fixture email literals (reserved domains only, with any exception allow-listed with a reason). Out of scope: the OpenAPI example in `billing/stripe_view_schemas.py` and one code comment in `users/serializers.py`, which never create a user, though moving them too costs nothing. |
| H-62 | Overage lock: failed paid overage purchases are replayed automatically, so a purchase that failed after payment is completed instead of lost | Medium: billing (paid purchase not delivered) | Hardening Engineer (grade-automator-plus-d5) | **Parked, near-complete, unverified (logged 2026-09-30 by the SM; revisit after batch-2b).** Branch `task/overage-lock` at `47a7c3d`: Gate 2 complete (56 of 56 mutants killed), the SM's Gate 4 ruling recorded (independent red team, not N/A), A01-A04 mutation results and `test_event_replay` coverage. Not merged; not verified. The branch's commits call this item "H-23"; that label is superseded, since H-23 is the email-logging item above. The branch's billing migration 0070 collides with H-56's (`0070_db_defaults_for_rollback`), so whichever lands second needs a merge migration. |
| H-63 | The student dashboard's per-row status and its Graded tile define "graded" differently: the per-row GRADED (`assignments/services.py` `get_student_assignment_status`) needs `graded_at` and `is_published`, while the Graded tile (`dashboard/views.py` `_assignment_status_counts`) needs `is_published` and a `score_percentage`. A manual score without `graded_at` would be a Graded tile but a SUBMITTED row | Low: display consistency | Hardening Engineer (d5) | **Not started (logged 2026-09-30 by the SM; found during the student-tiles partition, `2eb7c3e`).** Unreachable through today's write paths, checked at beta `463e222`: the grading service is the only production writer of `feedback` and sets `score_percentage`, `feedback` and `graded_at` together (`students/services.py` ~328-335), and `update_grade` refuses a submission without `feedback` ("Submission has not be graded yet", `students/views.py` ~995), so a manual score always lands on a submission that already has `graded_at`. Fix when next touched: one shared "grade released" definition used by both. |
| H-64 | A student's PENDING enrolments become ENROLLED at their first login through a bulk `QuerySet.update()`, which sends no signal and so bumps no cache generation. A classmate's cached course roster (the course list and detail keyed on the course's `crs` scope since the H-1 stage 3 rework) shows the old enrolment status for up to its 5-minute TTL | Low: brief staleness of a status shown to classmates | Hardening Engineer (d5) | **Not started (logged 2026-09-30 by the SM).** Found by the Verification Engineer while reviewing the batch-2b candidate; pre-existing (the activation predates the rework). Fix when next touching enrolment: bump each affected course's `crs` scope (and the student's own `usr`) after that update, with a freshness test through the real login. |

---

# H-1 — System-wide cache invalidation architecture

> ## STATUS, stated precisely
> **H-1 Stage 2: COMPLETE — 33/33 applicable families migrated.**
> **H-1 overall: OPEN — Stage 3 hardening outstanding.**
>
> The 100% migration figure does **not** mean H-1 is production-complete.
> Stage 3 contains substantive engineering, not paperwork:
>
> 1. dashboard-wide legacy-disabled verification (the dashboard as a system,
>    not 33 isolated family proofs);
> 2. selective stampede protection — jitter + single-flight for the measured
>    expensive families only, no locking on cheap ones;
> 3. **H-9** Redis test isolation;
> 4. **H-2** PostgreSQL test teardown / leaked connections;
> 5. clean cross-app regression from the repaired tree;
> 6. final repository-wide release gate from the committed tree.
>
> Only after all six does removing the legacy wildcards become reviewable.

**Priority: highest. Treat as an architecture task, not a tuning exercise.**

> **Architecture decided (owner, 2026-09-09): Option D — hybrid generation
> versioning.** The detailed design, the measured cache-stampede evaluation,
> and the acceptance/measurement plan are in
> **`docs/H1_CACHE_INVALIDATION_DESIGN.md`**. Its one blocking pre-check —
> production Redis's `maxmemory-policy` — is **RESOLVED: `volatile-lru`**,
> which is the favourable answer: counters written without a TTL are not
> eligible for eviction, so the stale-revival failure mode is structurally
> impossible. See §6 of that document.

## What is actually wrong

Measured against **real Redis** (not LocMem) with a realistic 10,000-key
cache and a real bulk import through the live service layer:

| Measurement | Value |
|---|---|
| SCAN commands per imported row | **29** |
| Total Redis commands per imported row | **432** |
| Cache keys destroyed by a **25-row** import | **10,000 — the entire keyspace** |
| Extrapolated to a 2,000-row import | **~58,000 SCANs, ~864,000 Redis commands** |

The earlier "~22,000 scans" figure quoted during the Section 3 audit was an
underestimate derived from signal counts. The measured figure is worse.

The headline is not the scan count. It is the third row: **a 25-row roster
import flushed every cached entry for every tenant in the system.** Patterns
like `*user*` and `courses:*` match every user's cached page, so invalidation
triggered by one teacher's course discards the cache of every unrelated
school. That converts a routine import into a system-wide cold cache.

### Contributing defects, each independently confirmed

1. **Wildcard patterns are unbounded by tenant.** `*user*`, `*school*`,
   `*course*` match across every user and school. Nothing scopes an
   invalidation to the rows that actually changed.

2. **Patterns overlap heavily.** Static analysis of the four signal modules
   found a single key matched by up to **four** distinct patterns:
   `studentcourses:user_id__1:query__abc` is matched by `*course*`,
   `*studentcourse*`, `*user*` and `studentcourses:*`. Each is a separate
   full keyspace SCAN deleting the same key.

3. **Per-row invalidation.** Every `StudentCourse` save fires a receiver
   clearing ~11 patterns. Bulk paths save row-by-row, so the cost is
   multiplied by the row count. `AutoGrader.cache_utils.batched_cache_invalidation`
   exists precisely to coalesce this and **no bulk path uses it**.

4. **Four separate implementations** of `delete_cache_patterns`
   (`AutoGrader/cache_utils.py`, `classrooms/signals.py`,
   `students/signals.py` via the shared helper, `assignments/signals.py`),
   which have already drifted: only some batch, only some catch Redis
   errors, only some warn on a backend without pattern support. See H-4.

5. **Wildcards can and do hit non-cache functionality.** `KEY_PREFIX="gaplus"`
   keeps Celery broker/result keys safe, but *anything written through the
   Django cache* is in range. This has already caused a live defect:
   `users/middleware.py` carries a 20-line comment explaining that the
   activity heartbeat and presence-set keys are **deliberately named to avoid
   the substring "user"**, because `clear_user_cache`'s `delete_pattern("*user*")`
   was wiping both on every unrelated user save — defeating a write throttle
   and silently zeroing the concurrent-user metric. The current mitigation is
   a naming convention with no enforcement: the next key containing "user"
   reintroduces it. DRF throttle keys (`throttle_<scope>_<ident>`) are in the
   same keyspace and are one scope-name away from the same collision.

6. **Cache stampede is unmitigated.** After a flush, every concurrent user
   misses simultaneously and rebuilds at once. The dashboard and course
   endpoints fan out into many sub-queries per page, so the rebuild is
   expensive precisely when everything requests it together.

7. **Cache and Celery share one Redis.** The same import run issued 25
   `lpush`/`subscribe`/`unsubscribe` pairs (task dispatch) interleaved with
   the 10,000 `del`s. Cache invalidation load and task dispatch contend for
   the same instance.

## Scope

Do the design work before the code work. The instruction stands: **first
establish what must actually be invalidated, then design the strategy.**

1. **Inventory** — every cache write site, key shape, TTL, and every
   invalidation call site with the patterns it clears and the model events
   that trigger it.
2. **Derive the true dependency map** — for each cached response, which
   model changes can actually invalidate it. Most current patterns are far
   wider than this map.
3. **Design** the replacement. Options to evaluate explicitly, with the
   trade-offs recorded:
   - **Key versioning / generation counters** (e.g. a per-user or per-course
     version integer folded into the key) — invalidation becomes an `INCR`,
     O(1), with no SCAN and no collateral damage. Stale entries expire by TTL.
   - **Tag-based invalidation** via sets of keys per entity.
   - **Targeted key deletion** where the key set is enumerable.
   - Keep `delete_pattern` only where a bounded, tenant-scoped prefix makes
     it cheap and safe.
4. **Namespace design** — cache keys must be structurally separated from
   throttles, locks, counters and presence data so that no invalidation can
   reach them by construction, replacing today's naming convention.
5. **Batching** — bulk import/create/update/delete paths must coalesce
   invalidation into one operation.
6. **Stampede control** — decide and implement (staggered TTL/jitter,
   lock-and-rebuild, or serve-stale-while-revalidate).
7. **Consolidate** to one implementation (subsumes H-4).

## Acceptance criteria

- A 2,000-row bulk import issues **O(1) invalidation operations, not O(rows)** —
  target: fewer than 100 Redis commands attributable to invalidation, down
  from ~864,000.
- Invalidation triggered by one tenant **cannot** evict another tenant's
  cached entries. Proven by assertion on surviving keys, not by inspection.
- No invalidation path can delete a throttle, lock, counter, presence or
  Celery key. Proven structurally (namespace separation), not by naming.
- A mutation that changes data **always** invalidates the responses that
  depend on it — no stale read survives a committed mutation.
- Redis unavailable or slow: every database operation still succeeds; the
  system degrades to stale reads, never to failed writes (the principle
  already established in `classrooms/signals.py`).
- Cache rebuild after a flush does not produce a thundering herd — bounded
  concurrent rebuilds under the load harness.
- Exactly one `delete_cache_patterns` implementation remains.

## Required evidence

- **Functional**: cached and uncached responses are byte-identical; every
  mutation type invalidates what it should.
- **Adversarial**: deliberately attempt stale reads, cross-tenant cache
  leakage, incorrect entries, missed invalidations, races between a mutation
  and a concurrent read, throttle/lock interference, and stampedes. Each
  attack asserts it failed.
- **Stress/concurrency**: real Postgres + real Redis; large bulk import
  concurrent with active readers; measured Redis command counts, CPU, memory,
  latency percentiles, and connection counts before and after.
- **Mutation**: removing batching, removing namespace separation, widening a
  pattern, and removing stampede control must each fail specific tests, with
  counts recorded.
- **Live stack**: measured through gunicorn against real Postgres/Redis, as
  the Section 3 load test was — not the Django test client alone.
- **Failure simulation**: Redis down during import; Redis recovering
  mid-import; Redis slow (latency injection); invalidation failing partway.
- **Regression**: full suite green, with before/after counts.

## Phase 1 progress — dependency map (started 2026-09-09)

The redesign cannot begin until it is known what must actually be
invalidated. This is that work; it is **partially complete**.

### Cache write sites, by app

| App | `cache.set` / `get_or_set` sites |
|---|---|
| `dashboard` | **24** |
| `users` | 4 |
| `billing` | 2 |
| `students`, `classrooms`, `assignments`, `ai_processor`, `AutoGrader` | 1 each |

`dashboard` holds the large majority of cached responses (TTLs of 5, 15 and
60 minutes) and is also where the heaviest aggregate queries live — so it is
both the biggest beneficiary of caching and the biggest victim of a flush.
The redesign must start from `dashboard`'s key shapes, not `classrooms`'.

### Key namespaces in use

`courses:`, `schooladmins:`, `studentadmins:`, `superadmins:`,
`teacheradmins:`, `studentsubmissions:`, `settings:`, `user:`, `image_url:`,
plus `UserCacheMixin`'s `<model>s:user_id__<id>:query__<md5>` shape.

Most are already `<entity>:user_id__<id>` — i.e. **the per-user scoping the
patterns then throw away** by matching `*user*` across all of them. A
per-user generation counter would fit the existing key shape with no
restructuring, which makes key-versioning the leading candidate.

### Non-cache keys sharing the keyspace — CONFIRMED BY TEST

`AutoGrader/tests_cache_collateral_damage.py` (9 tests, real Redis) seeds
every non-cache key shape the project writes through the Django cache, fires
each invalidation receiver, and asserts survival:

| Key | Purpose | Consequence if deleted |
|---|---|---|
| `billing:planchange:<user id>` | idempotency lock | a second concurrent billing mutation gets through |
| `billing:license_overage:<sub id>` | idempotency lock | duplicate overage grant |
| `presence:beat:<type>:<id>` | write throttle | activity rows written on every request |
| `presence:online` | presence set | concurrent-user metric silently zeroed |
| `throttle_<scope>_<ident>` | DRF rate limits | rate limit reset |
| `healthcheck` | liveness probe | health check flaps |

**Current result: no live collateral damage — all 9 pass.** The billing
locks and presence keys survive today only because none of their names
contain a substring any pattern matches. That is a naming convention with
nothing enforcing it, which is why these tests now enforce it.

Two findings from this:

* **Mutation-proved load-bearing**: adding a single `"*billing*"` pattern to
  `users/signals.py` destroys **both billing idempotency locks** and the
  suite fails. One careless pattern is all it takes to turn cache
  invalidation into a double-billing bug.
* **A latent defect is pinned**: `throttle_user_<pk>` — DRF's built-in
  `UserRateThrottle` scope — **is** matched by `*user*` and is deleted today.
  It is harmless only because that throttle class is not enabled. A test
  asserts this, so enabling `UserRateThrottle` fails loudly instead of
  silently resetting every user's rate limit on every user save.

### Coverage map — COMPLETE (measured, real Redis)

Every cache-key format the application writes, tested against every wildcard
pattern the signal receivers fire, with **Redis itself** doing the glob
matching (`AutoGrader/tests_cache_invalidation_coverage.py`, 5 tests).

| Metric | Value |
|---|---|
| Distinct cache-key formats | **35** |
| Never reached by any pattern | **6** |
| Reached by more than one pattern (redundant) | **27** |
| Total pattern-matches for 35 keys | **67** (~1.9 scans per key, per invalidation event) |
| Keys matched by `*user*` alone | **29 of 35** |

**The single most important number is the last one.** `*user*` matches 29 of
the 35 key families, so `users/signals.py::clear_user_cache` — which fires on
**every `CustomUser` and `Settings` save** — is a de-facto full cache flush.
User saves are among the commonest writes in the system (registration,
profile edit, settings change, and the `create_default_settings_and_wallet`
signal chain each trigger one). The 25-row-import result is not a bulk-import
quirk; it is what happens on ordinary traffic.

Worst redundancy, confirmed on a key the app really writes:
`studentcourses:user_id__<id>:query__<md5>` is matched by `*user*`,
`*course*`, `*studentcourse*` **and** `studentcourses:*` — four full keyspace
SCANs, three of them deleting a key an earlier one already deleted.

### UNDER-invalidation — the finding this sweep existed to catch

Six key formats are reached by **no pattern at all**. Two are fine; four are
a correctness bug.

**Genuine gaps — four `dashboard` responses that no mutation ever clears:**

| Key | TTL | Goes stale when |
|---|---|---|
| `teacher_performance_<school>_<page>_<size>` | 300s | any teacher's assignments/grades change |
| `teacher_detail_<school>_<teacher>` | 300s | that teacher's data changes |
| `assignment_activity_<school>_<year>` | 900s | any assignment is created/edited |
| `department_overview_<school>` | 300s | roster or course changes |

They break the `<entity>:user_id__<id>` naming convention every pattern is
written against, so nothing reaches them. A school admin can watch a teacher
publish an assignment and see the old numbers for up to 15 minutes.

Severity: **stale-within-tenant, not cross-tenant.** The keys are correctly
scoped by `school.id`, so one school cannot read another's numbers — this is
a freshness bug, not a disclosure bug. That distinction matters for
prioritisation and is asserted, not assumed.

**Not gaps — two caches with their own invalidation, recorded so the
distinction is not lost:**

* `assignmentpdf:<version>:<assignment id>:<view>:<stamp>` — `assignments/pdf_cache.py`
  clears its own prefix directly.
* `grading_answer_cache:<digest>` — content-addressed: the key *is* a digest
  of the input, so changed input means a different key and there is nothing
  to invalidate.

### Cross-tenant cached data — swept, no leak found

Every key format is scoped by `user_id` or by `school.id`. No cached response
is keyed only by a page number or a filter that two tenants could share, so
**no cached entry is readable across a tenant boundary**. The cross-tenant
exposure in this system is the opposite direction: one tenant's write
*destroys* another tenant's cache (availability/performance), rather than
exposing it (confidentiality). Asserted by
`test_over_invalidation_one_users_key_is_cleared_by_another`.

**Phase 1 is complete.** The two open pieces from the previous pass — the
dashboard map and the under-invalidation sweep — are done above.

---

## Design options — for review before implementation

Compared objectively; the recommendation is at the end, but the decision is
yours. "Ops" = Redis operations per invalidation event.

### Option A — per-entity generation counters (key versioning)

Fold a version integer into the key (`courses:v<n>:user_id__<id>:...`);
invalidate by `INCR`ing that entity's counter. Old keys are orphaned and
expire by TTL.

| Dimension | Assessment |
|---|---|
| Correctness | Strong. A bumped counter changes every dependent key atomically. |
| Tenant isolation | Strong — a counter is per user/school, so a bump cannot reach another tenant. |
| Redis ops | **O(1)**: one `INCR`, no SCAN. Best of the four. |
| Latency | One round trip per invalidation; reads need the counter, so +1 GET per read unless cached in-process. |
| Memory | Orphaned entries live until TTL — a transient increase after heavy churn. |
| Concurrency | `INCR` is atomic; no lost updates. |
| Bulk imports | Naturally O(1) — 2,000 rows still bump one counter per affected entity. |
| Stale risk | Low, provided every dependent key includes the counter. Missing one is a silent stale bug. |
| Stampede | Unchanged — a bump invalidates a whole family at once. Needs separate mitigation. |
| Complexity | Moderate: every read site must fetch and embed the counter. |
| Migration | Easy — new keys simply have a new shape; old ones expire. No flush needed. |
| Failure behaviour | Redis down: counter read fails → treat as cache miss → serve from DB. Degrades correctly. |

### Option B — targeted key deletion

Compute the exact affected keys and `DEL` them.

| Dimension | Assessment |
|---|---|
| Correctness | Only as good as the enumeration; anything forgotten goes stale. |
| Tenant isolation | Strong — you delete exactly what you name. |
| Redis ops | O(affected keys); a single `DEL` can take many keys, so usually small. |
| Latency | Very low when the set is small. |
| Memory | Best — no orphans. |
| Concurrency | Fine. |
| Bulk imports | **Poor unless batched** — the per-row problem returns as a per-row DEL list. |
| Stale risk | **Highest of the four.** The 35-key map above shows the enumeration is already non-obvious; the four dashboard keys were missed by exactly this kind of reasoning. |
| Stampede | Unchanged. |
| Complexity | High: every key format must be derivable from the mutation, including paginated and query-hashed variants — `UserCacheMixin` keys embed an **MD5 of the query params**, which is not enumerable at all. |
| Migration | Incremental. |
| Failure behaviour | Same as today. |

**The `UserCacheMixin` query hash is close to disqualifying for B on its
own**: you cannot enumerate keys you cannot predict.

### Option C — tag / set-based invalidation

Maintain a Redis SET per entity holding the keys that depend on it;
invalidate by reading the set and deleting its members.

| Dimension | Assessment |
|---|---|
| Correctness | Strong — solves B's enumeration problem by recording dependencies at write time. |
| Tenant isolation | Strong. |
| Redis ops | O(1) SMEMBERS + one DEL of N keys — no SCAN. |
| Latency | Two round trips; still far below today. |
| Memory | Extra: a set per entity, and sets need pruning or they grow unboundedly as keys expire underneath them. |
| Concurrency | Set writes race with key writes; a key can be cached but not yet tagged (small stale window) unless done in a transaction/pipeline. |
| Bulk imports | Good — one set read per entity, batchable. |
| Stale risk | Low-moderate: the race above, plus tag drift if a write path forgets to tag. |
| Stampede | Unchanged. |
| Complexity | **Highest** — every cache write must also tag, and tag GC must be built and operated. |
| Migration | Harder — needs backfill or a period where untagged keys are unreachable. |
| Failure behaviour | Redis down: tagging fails → untagged keys become un-invalidatable until TTL. Worst failure mode of the four. |

### Option D — hybrid: versioning for user/school families, dedicated prefixes elsewhere

A for the 29 `user_id`/`school`-scoped families; keep the two
self-managing caches (PDF, grading digest) as they are; give the four
orphaned dashboard keys the standard scoped shape so they join the scheme.

| Dimension | Assessment |
|---|---|
| Correctness | Strong, and it fixes the four under-invalidated keys as a side effect. |
| Tenant isolation | Strong. |
| Redis ops | O(1) for the common case; unchanged for the two specialised caches. |
| Complexity | Moderate — one mechanism plus two documented exceptions, rather than one mechanism forced everywhere. |
| Migration | Same as A. |
| Everything else | As A. |

### Recommendation, with the reasoning exposed

**D (hybrid, versioning-based)** — but on evidence, not convenience:

* 29 of 35 key families are already `<entity>:user_id__<id>`, so the scoping
  a counter needs **already exists in the key shape**; A/D fit the codebase
  as written rather than requiring it to be rewritten.
* B is undermined by the `UserCacheMixin` MD5 query hash — unpredictable keys
  cannot be enumerated for targeted deletion.
* C is the most powerful but has the worst failure behaviour (a Redis blip
  during tagging silently creates un-invalidatable keys) and the largest
  operational surface, for a system whose current problem is over-eager
  invalidation rather than under-reach.
* D handles the two self-managing caches honestly instead of forcing them
  into a scheme that buys them nothing.

**Not decided by this analysis, and needing your call:** cache stampede.
None of A–D addresses it — invalidating a family still expires everything at
once. It should be chosen separately (jitter, lock-and-rebuild, or
serve-stale-while-revalidate), and `dashboard`'s expensive aggregates are the
place it matters.

**Open risk for whichever option is chosen:** every read site must adopt the
new scheme. A single missed read site is a permanently stale response. The
35-key map above is the checklist that makes that auditable, and the coverage
test should be inverted after implementation to assert that every key format
is reachable by its owning mechanism.

## Baseline — preserve for the before/after comparison

These are the numbers the redesign must be measured against. **Do not
re-baseline after the change**; the point is to prove improvement, not to
move the goalposts.

| Measurement | Baseline (2026-09-08/09) |
|---|---|
| SCAN commands per imported row | 29 |
| Total Redis commands per imported row | 432 |
| Cache keys destroyed by a 25-row import | 10,000 (entire keyspace) |
| Extrapolated 2,000-row import | ~58,000 SCANs, ~864,000 commands |
| Distinct cache-key formats | 35 |
| Key families matched by `*user*` | 29 of 35 |
| Never-invalidated key formats | 6 (4 are gaps) |
| Redundantly-invalidated key formats | 27 |
| Total pattern-matches per invalidation sweep | 67 |

Post-implementation testing must additionally cover **realistic multi-tenant
scenarios and large bulk operations**, not the 25-row case that exposed the
problem: reproduce the original failure at scale first, then demonstrate the
redesign removes the flush/performance cliff.

## Reproduction

The measurements above came from a `TransactionTestCase` with
`override_settings(CACHES=...)` pointed at a dedicated Redis DB, seeding
10,000 realistic keys, calling `redis.config_resetstat()`, running
`import_roster` through the real service layer, then reading
`INFO commandstats` and `DBSIZE`. Rebuild this as a permanent, committed
benchmark so the improvement is measured rather than asserted.

---

# H-2 — Full-suite exit code / test DB connection leaks

**The suite passes and still exits non-zero, so CI would go red on a green
run.**

Observed: `classrooms students users assignments dashboard` →
**1,422 tests, OK, 0 failures**, then teardown fails with
`database "test_..." is being accessed by other users — There are 13 other
sessions using the database`, exit 1.

Isolated: **`classrooms` alone runs 235 tests and exits 0**, so this section
is not the source. The leak comes from threaded / `LiveServerTestCase`
suites elsewhere — `users/tests_activity_middleware_load.py`,
`users/tests_login_lockout.py`, `students/tests_grading_idempotency.py`,
`assignments/tests_load.py`, `assignments/tests_security.py`.

The pattern that fixes it is already in the tree:
`classrooms/tests_concurrency_and_resilience.ThreadSafeTransactionTestCase`
closes connections in `tearDown` as well as in each worker thread.

> **CORRECTION (2026-09-13): the diagnosis above was wrong.** The leak comes
> from one test, not five suites.
> `assignments/tests_security.py` `ConcurrentAccessRevocationTest` starts
> 13 threads and none of them closed its own connection. That is the
> "13 other sessions". Two earlier fixes were tried, disproven and reverted:
> `CONN_MAX_AGE=0` in test mode (still 13 sessions), and a shared
> `tearDown` mixin (`connections.close_all()` is thread-local, so it cannot
> close a worker thread's connection). The actual fix is
> `try/finally: connection.close()` inside each worker. Full evidence is in
> `docs/evidence/H2_TEST_TEARDOWN_EVIDENCE.md`.
>
> **Status: Closed — fixed, verified with three consecutive clean full runs
> and failure/mutation simulation.** (Owner sign-off 2026-09-13.)
> gate3/4/5: 1,804 tests OK each, exit 0, 0 leftover sessions and 0
> leftover DBs. Mutant with the two `close()` calls removed: all 58 tests
> still pass, exit 1 with "13 other sessions". Restore verified by checksum.
> The fingerprint helper skipped six untracked documentation files with
> spaces in their paths. The owner reviewed this and does not consider it
> grounds to invalidate the runs, since those files are not code. All
> future gates use the NUL-safe fingerprint.

**Scope**: ~~promote that base class to a shared location and adopt it in
every threaded/live-server suite~~ (superseded, see the correction); confirm
each worker closes its own connection.

**Acceptance**: the full suite exits **0**, repeatably, including after the
threaded suites run; no lingering `test_*` connections in `pg_stat_activity`
after a run.

**Evidence**: regression (three consecutive clean full runs); failure
simulation (a deliberately leaking test is detected rather than silently
tolerated). Adversarial/stress/live-stack: not applicable — record why.

---

# H-9 — the whole test suite shares one Redis DB (test isolation)

**Found 2026-09-12 while investigating a cross-app regression failure.**

> **REGRESSION (2026-09-14) — H-9 reopened.** The 2026-09-12 fix below
> isolated the project-wide test cache, but twelve modules that must run on
> real Redis wrote their own `CACHES` override. That override replaced the
> fix entirely. It used the unscoped `django_redis.cache.RedisCache`: eleven
> modules on a fixed database number (3–15) with the shared `gaplus` prefix,
> and `users/tests_activity_middleware_load` on the default database. Their
> `cache.clear()` is FLUSHDB, so two concurrent test runs wiped each other's
> cache entries and H-1 generation counters. It surfaced for real: a
> Section 9 mutation run overlapped the Section 8 strict gate on shared
> Redis for ~3 minutes, and Section 8 aborted and re-ran.
>
> The four original acceptance tests kept passing throughout, because they
> only exercised the default cache. A dedicated database number is not
> isolation: every run of the same module picks the same number.
>
> **Fix** (branch `task/h9-redis-db-isolation`):
> - `AutoGrader.test_cache.real_redis_caches(location)` returns a
>   real-Redis override with the prefix-scoped backend and the
>   per-process prefix. All twelve modules use it.
> - `tests_cache_generation` builds raw keys with `cache.make_key()`.
> - `tests_cache_superadmin_1522` scans only its own prefix.
> - `SuiteOverridesCannotBypassIsolationTests` fails the suite if any test
>   module configures the unscoped backend again, and proves two processes
>   sharing one fixed database cannot wipe each other.
>
> **Scope widened (owner, 2026-09-14, relayed via Section 9):** every
> session must be able to run full strict gates at the same time as other
> sessions without interference. So the Celery broker and result backend
> are namespaced too. Under `manage.py test` each process gets kombu
> `global_keyprefix` and `result_backend_transport_options.global_keyprefix`
> equal to its cache prefix. That isolates queues, exchange bindings and
> kombu's global `unacked` hash and index, which acks_late redelivery uses.
> Production `visibility_timeout` is unchanged. The real-worker tests
> delete their queues through kombu, not a raw client.
> `CeleryBrokerIsolationTests` checks the prefixes are applied, proves a
> same-named queue purged by another process leaves our messages intact,
> and fails if any test builds a raw client from `CELERY_BROKER_URL`.
> The settings branch covers every test run (main checkout, worktrees,
> CI, two runs in one worktree), so `scripts/task-worktree.sh` only
> documents it.
>
> **Acceptance proof (owner's):** two FULL strict test runs from the
> committed fix, overlapping in time on the same Redis, each on its own
> fresh PostgreSQL test DB. Both must pass with exit 0 and clean
> teardown, and mid-run sampling must show both process prefixes live at
> once.
>
> **Status: CLOSED (owner, 2026-09-15).** Every closure criterion below is
> met and independently verified on `29cc1c7`, including two concurrent full
> suites (4,153 tests each); see `docs/evidence/H9_REDIS_ISOLATION_EVIDENCE.md`
> §5. The
> serial-runs restriction below is lifted by the passing proof, but only for
> test runs from a tree that contains this fix. Branches that predate it still
> run the flushing suites and must merge beta before overlapping.
>
> _Previous status:_ IMPLEMENTED / MERGED / VERIFICATION PENDING (owner,
> 2026-09-14). The fix is `4820e33`, merged with current beta on
> `task/h9-redis-db-isolation`. The owner approves landing it on beta,
> subject to the verification below completing successfully.
>
> **Operational restriction, in force until the overlap proof passes:**
> full-suite gates, mutation runs and live-worker tests from different
> sessions must not overlap in time. Merging the fix does NOT lift it;
> only the proof does.
>
> **Order:** Section 9's host-quiet gate, then targeted H-9 verification
> on current beta, then the concurrent full-run proof, then the H-1
> stampede measurement on current beta, then the closure decision.
>
> **Moves to CLOSED only when ALL hold:**
> 1. the targeted tests pass (the changed modules plus both Celery
>    real-worker/broker modules);
> 2. two FULL test runs, running simultaneously, both pass;
> 3. each uses its own isolated, fresh PostgreSQL test DB;
> 4. real Redis is used throughout;
> 5. Redis sampling during the overlap shows both runs' independent
>    namespaces live at the same time;
> 6. no cross-run deletion or contamination occurs;
> 7. both tear down cleanly with zero leaked DB connections;
> 8. the final regression stays clean.
>
> H-9 closure is **not** blocked by the repository-wide Item 9 (E800)
> cleanup. The two only share files. Whichever lands on beta second
> rebases and preserves BOTH the H-9 and the H-12 backlog and pre-commit
> changes.

`REDIS_LOCAL_URL=redis://127.0.0.1:6379/0`, so **every pre-existing test
suite in the project shares Redis DB 0** with any other process using it -
including a second concurrent development session running its own tests.

## The failure it produces

A 6-app regression run reported one failure:
`assignments.tests_pdf_cache.InvalidationScopeTest.test_a_whole_course_of_saves_does_not_cool_one_warm_assignment`,
asserting `None != b'%PDF-warm'`.

Isolation results — the test is **not** broken:

| Scenario | Result |
|---|---|
| the test alone | OK |
| the whole `assignments` app (521 tests) | OK |
| `students` -> `tests_pdf_cache` | OK |
| H-1 cache suites -> `tests_pdf_cache` | OK |
| **6-app run (~30 min)** | **FAILS** |

Ruled out by A/B revert: reverting H-1's assignment generation bump does
**not** fix it, so H-1 is not the cause.

**Mechanism demonstrated, not hypothesised.** A `cache.clear()` issued
between `store_pdf` and `get_cached_pdf` reproduces the exact assertion:

```
DEMO no interference                 -> b'%PDF-warm'
DEMO after concurrent cache.clear()  -> None   (matches the observed failure)
```

Any concurrent process calling `cache.clear()` or a wildcard
`delete_pattern` on DB 0 can therefore fail an unrelated suite. A 30-minute
run gives a wide window; every short run passes, which is exactly the
observed pattern.

## Second, related finding

Redis DB 0 currently holds live `gaplus:1:cachegen:usr:*` keys left by an
earlier test run. Generation counters are written with **no TTL** by design
(so `volatile-lru` cannot evict them - see
`docs/H1_CACHE_INVALIDATION_DESIGN.md` §6), which in the test environment
means they **accumulate across runs and are never reclaimed**. Harmless
today because entity UUIDs differ per test database, but it is unbounded
litter in the shared dev instance and a source of cross-run state.

## FIXED (2026-09-12)

`AutoGrader/test_cache.py` + a settings branch under `manage.py test`:

* each test **process** gets its own `KEY_PREFIX` (`gaplus-t<pid>`);
* `PrefixScopedRedisCache.clear()` deletes only that prefix instead of
  issuing `FLUSHDB`.

**A prefix, not a Redis database slot.** Redis ships with 16 databases, so a
PID-modulo scheme collides at ~1/16 for two concurrent runs and cannot scale
to CI parallelism. A prefix has no ceiling.

**Why a test-only backend is acceptable**: only `clear()` differs, and no
production code path calls it (grep-verified - production invalidation goes
through `delete_pattern` or generation bumps). Every other operation,
including `delete_pattern` and the `incr`/`SET NX` behaviour H-1's counters
depend on, is the real django-redis implementation against real Redis.

### Evidence

* both acceptance tests flipped from `expectedFailure` to passing;
* **two genuinely concurrent runs** - `assignments.tests_pdf_cache` beside
  the H-1 cache suites - both returned OK, which is the failure this was
  diagnosed from;
* 4/4 tests in `tests_redis_test_isolation.py`.

### One correction worth recording

The acceptance test did not pass immediately after the fix, and the reason
was the test, not the fix: its helper subprocess ran as a plain script, so
`"test" in sys.argv` was false and it picked up the **production** backend,
whose `clear()` is still `FLUSHDB`. It was measuring "a non-test process
flushes us", not "a concurrent test session flushes us". The helper now sets
`sys.argv` to look like a test run, which is the scenario the requirement
actually describes.

### Residual exposure, out of scope by design

A non-test process - a `manage.py shell`, a stray script - calling
`cache.clear()` still issues `FLUSHDB` and would wipe a running suite. That
is a developer action against the development cache, not a concurrent test
session, and is documented in the test module rather than guarded.

## Original requirement (owner, 2026-09-12) — stronger than changing one URL

> **Concurrent test sessions must not be able to modify, flush, or
> invalidate one another's Redis-backed test state.**

The property to satisfy is **concurrent-process isolation**, not sequential
test isolation. It covers everything Redis-backed that a test run touches:

* the Django cache;
* H-1 generation counters (`cachegen:*`);
* cache-based locks (`billing:planchange:*`, `billing:license_overage:*`);
* throttle state (`throttle_*`);
* presence/heartbeat keys (`presence:*`);
* any Celery broker/result state sharing the instance.

## Scope

* give the test suite its own Redis DB, or better a per-run DB, the way the
  H-1 suites already do - they use dedicated DBs 6-12 and **none of them
  flaked** in the run that exposed this;
* flush the chosen DB at session start, never mid-run;
* verify the isolation holds with **multiple concurrent processes**, not one
  process running suites in sequence.

## Acceptance

* two concurrent full-suite runs on one machine do not interfere;
* the PDF invalidation-scope test passes in a 6-app run, three times running;
* `AutoGrader/tests_redis_test_isolation.py`'s two `expectedFailure` tests
  become **unexpected successes** - which turns the suite red and forces the
  markers to be removed. That is the signal the fix landed.

## The reproduction is retained as the regression test

`AutoGrader/tests_redis_test_isolation.py` holds the deterministic
demonstration, per the owner's instruction to keep it:

| Test | Today | Why |
|---|---|---|
| `test_the_mechanism_a_cache_clear_destroys_a_warm_entry` | passes | documents the mechanism in-process |
| `test_generation_counters_are_also_destroyed_by_a_flush` | passes | shows the blast radius reaches H-1's invariant |
| `test_a_concurrent_process_cannot_flush_our_cache` | **expectedFailure** | the acceptance test: a separate OS process, using the project's own settings, flushes our state |
| `test_a_concurrent_process_cannot_reset_our_generation_counters` | **expectedFailure** | same requirement for the H-1 counters |

The two acceptance tests spawn a **real second interpreter** and are
deterministic - the other process clears, then we read. No race, no sleep.
`expectedFailure` rather than `skip` so the fix cannot land silently.

## Generation-counter accumulation: do NOT add a TTL

Explicit owner instruction. The non-expiring counter is part of H-1's
correctness invariant: under `volatile-lru` a TTL'd counter becomes
evictable while the entries it guards survive, resetting the generation and
reviving stale data - the exact failure class H-1 was designed to remove
(`docs/H1_CACHE_INVALIDATION_DESIGN.md` §6).

Solve the accumulation through **isolated, disposable test Redis state**
plus teardown of the test instance after a run. For a long-lived development
Redis, track counter cardinality and memory as their own line item. Do not
change production semantics to tidy a shared development database.

---

# H-10 — `super-admin/dashboard/students` issues 487 queries

Found while measuring H-1 families 15-22. **Not a cache defect** — caching
only hides it on the requests that hit.

Measured at 10 schools / 240 courses / 17,464 submissions, through the live
endpoint on real Postgres:

| Metric | Value |
|---|---|
| Queries | **487** for a **413-byte** response |
| Wall | 1,938ms |
| SQL | 433ms |
| **Python** | **1,505ms** |
| Signature | `240x COUNT(*) assignments` + `240x COUNT(*) studentcourse` |

One pair of COUNT queries per course. It scales linearly with course count,
so a tenant with 10x the courses sees ~10x the queries — and the Python time
dominates, so it is not fixed by a faster database.

**Scope**: replace the per-course counts with annotated aggregates
(`Count(..., distinct=True)` on a single queryset), the same fix applied to
the classrooms N+1s in Section 3.

**Acceptance**: query count does not grow with course count (assert
flatness, not an absolute budget — an absolute number drifts and gets
bumped); response byte-identical before and after; wall time reduced.

**Evidence**: functional (identical payload), performance (before/after
query count and latency at the measured scale), mutation (reverting the
annotation fails the flatness test), regression. Adversarial/failure
classes: not applicable — record why.

## Owner decision (2026-09-14): the fix is the Section 8 remediation

H-10 points at the Section 8 dashboard remediation (session
grade-automator-plus-01), so there is no competing H-1 fix. Per that session,
the remediation covers three places:
- `_expected_submission_total(courses)` replaces the per-course
  `assignments.count() * enrollments.count()` in the super-admin and
  school-admin students views;
- `TeacherPerformanceStatsService().build(teachers)` replaces the
  per-teacher N+1 in teacher_performance, teacher_detail and the weekly
  digest;
- `dashboard/tests_dashboard_remediation.py` adds flatness, parity and
  tenant tests.

**Closed only when all three hold** (owner):
1. the dashboard fix's own tests pass;
2. the H-1 cache suites pass against the resulting code;
3. a fresh real measurement shows the **query count stays effectively flat
   as the dataset grows**. A single faster request is not evidence.

Recorded so far, not closing: on the uncommitted reconciled tree
(`1373eae` + Section 8 patch), the six H-1 cache suites passed (80 OK), and
an interim full suite passed (3,931 OK, 14 skipped). That full run used
`--keepdb`, so it is **not** final-gate evidence: `--keepdb` skips the
test-DB drop, which is the only point where a leaked connection shows up.

**Condition 3 — fresh real measurement (2026-09-14).** The same test was run
on `1373eae` (before) and `ec67363` (Section 8 remediation). Requests were
uncached, on real Postgres: one school, 2 courses per teacher, 3 students
per course.

| Endpoint | Queries at 2 / 6 / 18 teachers, `1373eae` | Queries at 2 / 6 / 18 teachers, `ec67363` |
|---|---|---|
| super-admin students | 14 / 30 / 78 | **6 / 6 / 6** |
| school-admin students | 13 / 29 / 77 | **5 / 5 / 5** |
| school-admin teacher_performance | 24 / 60 / 168 | **10 / 10 / 10** |
| super-admin teachers | 8 / 16 / 40 | **8 / 8 / 8** |
| school-admin teacher_detail | 19 / 19 / 19 | 14 / 14 / 14 |

The query count is flat on `ec67363` and grew linearly before it, so
condition 3 is met **for `ec67363`**. At 18 teachers, teacher_performance went
from 169.5ms to 20.9ms; latency is indicative only, as other runs were active.

## CLOSED (2026-09-14)

The owner told us to merge the dashboard work and close H-10 once the
resulting `beta` tree passed verification. Sections 7 and 8 were merged into
`beta` as `2715c642fc4b` (tree `2a68fe26…`), and the owner's strict gate
passed on that exact commit: **4,031 tests OK** (14 skipped), exit 0, fresh
DB with no `--keepdb`, DB dropped, 0 connections, machine kept awake, tree
unchanged.

All three conditions now hold **on `beta`**:
1. **Dashboard tests pass:** `tests_dashboard_remediation` 47,
   `tests_dashboard_audit_fixes` 23, `tests_rigor` 35, `dashboard.tests` 73,
   all ok.
2. **H-1 cache suites pass** inside the same gate: fan-out 29,
   dashboard-wide 11, wiring 15, plus the rest.
3. **Query count is flat** (table above). `dashboard/` on `beta` is
   byte-identical to the measured code.

Evidence: `docs/evidence/H10_INTEGRATION_GATE_EVIDENCE.md`.

---

# H-3 — `student123!` account remediation

Deferred by the owner as non-urgent and isolated; tracked here so it does not
vanish. **Data already gathered (production, read-only):**

- **116 of 122 student accounts authenticate with `student123!`** — verified
  by running `check_password` against every row, not inferred from the email
  pattern.
- All 116 are `is_active=True`, and login has **no email-verification gate**,
  so these are live credentials.
- **97** have generated `@student.local` addresses; **19 have real email
  addresses** — the exposed cohort, since an attacker needs no guessing.
- They hold **124 enrollments and 558 submissions**, so deletion is not an
  option.
- **`last_login IS NULL` on all 116** — not one has ever signed in.

That last fact is the whole plan: resetting all 116 to an unusable password
has **zero user impact**. The code fix is already shipped
(`set_unusable_password()`), so only existing rows are affected.

**Scope**: a management command, dry-run by default, requiring `--execute`,
reporting the affected count and writing an auditable record of what it
changed.

**Acceptance**: zero accounts authenticate with the literal afterwards; no
account is deleted; enrollments and submissions are untouched; the command is
idempotent.

**Evidence**: functional (dry-run reports without writing); adversarial (the
literal no longer authenticates on the live login endpoint); failure
simulation (interrupted mid-run leaves a consistent state and can be
re-run); regression. Mutation: removing the reset must fail the test that
asserts no account authenticates.

---

# H-4 — Duplicated `delete_cache_patterns` implementations

Four implementations exist and have already drifted: only some batch, only
some catch Redis connection errors, only some warn when the backend lacks
pattern support, and they differ in how broadly they catch exceptions
(`classrooms/signals.py` deliberately narrow so real bugs still surface;
`AutoGrader/cache_utils.py` catches bare `Exception`).

**Folds into H-1** — consolidation is part of that design, not a separate
change. Listed separately so it cannot be lost if H-1 is staged.

**Acceptance**: exactly one implementation; every caller uses it; the narrow
exception behaviour is preserved (a `TypeError` from a bad pattern must still
propagate, as pinned today).

---

# H-5 — `full_clean()` on the grading hot path

`StudentCourse.save()` calls `full_clean()` unconditionally, so every
`save(update_fields=["final_grade"])` from the grading signal runs full model
validation — including the name-conflict lookup — **inside a
`select_for_update()` block on the grading hot path.**

**Scope**: measure the cost under concurrent grading first, then decide
whether to scope validation to the fields being written or move it to the
serializer boundary. Do not change validation semantics blindly — the
name-conflict rule is load-bearing for roster integrity.

**Acceptance**: no reduction in validation coverage for user-supplied input;
measured reduction in queries and lock-hold time on the grading path.

**Evidence**: functional (name-conflict rules still enforced on every user
entry point); stress/concurrency (concurrent grade recalculation, real
threads, measuring lock contention); mutation (removing the remaining
validation fails roster-integrity tests); regression.

---

# H-6 — `CourseCategoryViewSet` is unrouted and broken

Not registered in any `urls.py`, and its `category_courses` action reads
`category.courses` — a relation that **does not exist**: `CourseCategory` has
no link to `Course` at all. It would raise `AttributeError` if ever routed.
Left untouched pending your decision on whether categories have a future.

**Scope**: decide first — build the relation and route it, or remove the
viewset and serializer. Both are small; the decision is the work.

**Acceptance**: either a working, tenant-scoped, tested endpoint, or the dead
code removed with the model's fate recorded (the `CourseCategory` model and
its admin registration also become questionable if the endpoint goes).

**Evidence**: if built — functional, adversarial (tenant scoping, as every
other classrooms endpoint now has), regression. If removed — regression plus
a grep-proof that nothing references it.

---

# H-7 — `direct_add_student` response shape and status code

Returns `{"message", "detail"}` where every sibling endpoint returns
`{"detail"}` alone, and returns **200 for a creation** where 201 is correct.
Untouched because response-shape changes are an API contract change.

**Scope**: confirm with the frontend which fields are consumed, then align.

**Acceptance**: consistent error envelope across classrooms endpoints; 201 on
creation; frontend updated in the same release.

**Evidence**: functional; regression; live-stack confirmation against the
real frontend. Adversarial/stress: not applicable — record why.

---

# H-8 — Test file naming and stray docs

`classrooms/` mixes `test_*.py` and `tests_*.py`; the standards doc
(`docs/CODE_REVIEW_STANDARDS.md` §9) specifies `tests.py` / `tests_<topic>.py`.
`classrooms/test_documentation.md` is a markdown doc living in an app
package and probably belongs under `docs/`.

**Scope**: rename with `git mv` so history follows; move the doc.

**Acceptance**: naming consistent across the app; the pre-commit
`name-tests-test` exclusion still correct; suite green.

**Evidence**: regression only — record that the other classes do not apply.

---

## Note on scope discipline

# H-11 — Synchronous billed AI calls inside `students` request handlers

**Found by the §7 review pass (2026-09-13) while auditing
`students/views.py`, which no audit section had listed.** Recorded here so
it cannot fall between sections again: it is release-blocking, it has an
owner, and it has its own gate.

Three actions on `StudentSubmissionViewSet` run the billed AI pipeline
inside the HTTP request, against standards §7 ("nothing in a
request/response cycle does synchronous work that belongs in a Celery
task - AI grading calls..."):

| Action | What runs in the request | Async twin that already exists |
|---|---|---|
| `POST submissions/<assignment>/upload` (`upload_answers`) | file → AI answer extraction → save | `upload-async` |
| `POST submissions/<pk>/grade` (`grade`) | the full grading pipeline (several sequential AI calls with retries, up to `GRADING_TASK_TIME_LIMIT_SECONDS`) | `grade-async` |
| `PATCH submissions/<pk>` (`partial_update`) | raw text → AI re-extraction → save | none |

Consequences today: a gunicorn worker is held for the whole AI run
(minutes for `grade`), the request can outlive the proxy timeout while
the charge has already been made, a client retry after a timeout is a
second billed run (the grading claim stops the double *grade*, but the
sync `grade` view then answers 409 for a run the client cannot poll), and
the sync `upload` path has no tracked task, so nothing the frontend can
poll records its failure.

Also recorded from the same audit, all in `students/views.py` /
`serializers.py`, to be resolved with this item because they share the
endpoints:

* **V-2** `upload_answers` and `partial_update` answer HTTP 500 for every
  failure, including user-caused ones (bad file, extraction returned no
  answers) - only the two post-grading closure errors now map to 409.
* **V-3** `get_permissions` routes `partial_update` (PATCH) to
  teacher-only while its docstring and the OpenAPI text describe it as the
  student's edit path. Either the docstring or the mapping is wrong;
  decide which before the endpoint moves async.
* **V-4** `partial_update` ends with a full-row `submission.save()` - the
  same stale-instance clobber class fixed in the service layer (F-4).
* **V-5** `StudentViewSet` is defined but not routed (`students/urls.py`
  registers only submissions); dead or missing, decide which.
  **DECIDED 2026-09-17 (owner): delete.** Deleted in
  `task/my-students-prefetch-leak` commit `e0b1640`; it also carried the
  unscoped cross-teacher `enrollments__course` pattern fixed there.
* **V-6** `teacher_feedback` declares `IsTeacherOrReadOnly` on the action
  but `get_permissions` overrides it to teacher+credits (already commented
  in code; the dead kwarg should go once V-3 is decided).

**Owner:** Section 7 (students) for the backend; the frontend owner for
the client switch-over. Proposed by the §7 reviewer; assignment is
management's.

**Scope:** make the three sync actions either (a) thin dispatchers that
create a tracked task and return 202 with a task id (the `-async` twins
already do this), or (b) removed once the frontend has switched - decided
with the frontend, since (a) changes their response contract. Map
user-caused failures to 4xx with the existing `describe_user_error` text.
Resolve V-2..V-6 in the same change.

**Acceptance criteria:**
1. No `students` view calls `ai_processor.*`, `grade_engine` or
   `upload_answers_engine` synchronously (static check: a test that greps
   `students/views.py` for those names, so it cannot regress silently).
2. Every submission-mutating action creates a `BackgroundProcessingTask`
   the frontend can poll, and its failure is recorded on that row with a
   user-safe message.
3. A client retry after a timeout cannot produce a second billed run
   (idempotency proven under redelivery, as for R-1).
4. V-2..V-6 each closed with a test.

**Required evidence (per the verification standard above):** functional
through the live API; adversarial (retry/replay after timeout, the same
tenancy probes as `students/tests_submission_tenancy.py`); concurrency
(N parallel clients, one billed run); failure simulation (broker down →
503, not 500; AI provider timeout → refund, task FAILURE); mutation;
regression; evidence recorded in `docs/evidence/`.

**Until closed:** the sync endpoints keep working exactly as today, with
the §7 pass's server-side rules applied to them (post-grading lock,
tenancy scoping, 409 for closure errors).

**Progress (2026-09-14, commit `0320c87`, gated in `ec28d90` — evidence
§13/§14):** scope narrowed by the owner to migration/retirement for
upload and grade (their async twins exist) plus wiring the existing
extraction task into an async edit path.

Done:
* `POST submissions/<pk>/update-async` — queues the rewired
  `extract_answer_background_task` as a tracked task, 202 + task id.
* One service (`update_submission_from_raw_text`) behind PATCH and the
  async route: closure rules before the billed call and again under the
  row lock, refund scope over extraction + persist, column-scoped save.
* Idempotency claim on the tracked row (`claim_processing_task_start`):
  a Redis redelivery of a running extraction skips instead of billing
  twice; a stale claim from a dead worker is taken over.
* Duplicate-request guards on `update-async` (submission row lock) and
  `upload-async` (student user row lock): a client retrying after a
  proxy timeout gets 409 instead of a second billed run.
* V-2 (400 for user-caused failures on the three sync routes), V-3 (PATCH
  follows its docstring: own student or course teacher, credit-checked),
  V-4 (column-scoped save), V-6 (dead permission kwarg removed).
* Real provider call for the edit path (OpenRouter, once): charged once.

Remaining (H-11 stays OPEN and release-blocking):
1. **Client dependency check** — this repository holds no frontend; the
   only references to the synchronous `upload`, `grade` and PATCH routes
   are in `docs/backend/`. The frontend owner must confirm the client
   uses `upload-async`, `grade-async`/`schedule-grade-async` and
   `update-async`, and that no other client calls the sync routes.
2. **Retire** the synchronous AI execution in `upload_answers`, `grade`
   and `partial_update` once (1) is confirmed — delete, or keep as thin
   dispatchers returning 202 if a compatibility window is needed.
3. **V-5** `StudentViewSet` unrouted: delete or route (file deletion needs
   sign-off). **Owner signed off on deletion 2026-09-17; deleted in
   `e0b1640`.**
4. The same tracked-row idempotency claim for `upload_answers_engine_async`
   (the upload task still marks started unconditionally; Section 9 is
   changing that task, so this is coordinated with it).
5. The retirement change goes through the full ten-state gate: real
   provider, proxy-timeout scenarios, duplicate/replayed requests,
   concurrency, credit charged exactly once, failure after charge, task
   retries, real HTTP end-to-end.

### Owner scope clarification (2026-09-14) and progress

The owner ruled: no new duplicate async implementations for upload and
grade - they already have `upload-async`, `grade-async` and
`schedule-grade-async`. H-11 is a **migration/retirement** task for those
two, plus wiring the existing `extract_answer_background_task` into an
async edit path. The synchronous routes are **not** to be removed until
the frontend/client dependency is confirmed.

**Done in the §7 branch (2026-09-14):**

* `POST submissions/<pk>/update-async` — queues the (previously unrouted
  and broken: it wrote to fields that do not exist) extraction task,
  rewired to the same service the synchronous PATCH now uses
  (`students.services.update_submission_from_raw_text`): closure rules
  checked before the billed call and again under the row lock, one refund
  scope over extraction + persistence, column-scoped save. `202 + task_id`.
* Duplicate-request guards (a client retrying after a proxy timeout must
  not queue a second billed run): `update-async` locks the submission row,
  `upload-async` locks the student's user row; both refuse (409) while an
  extraction task for the target is PENDING/STARTED.
* Task-level idempotency claim on the tracked row
  (`students.task_tracking.claim_processing_task_start`): a Redis
  redelivery of a running extraction skips; a stale STARTED claim (dead
  worker, older than `EXTRACTION_TASK_STALE_AFTER_SECONDS`) is taken over.
* V-2 closed: user-caused failures answer 400 with their own text (500
  only for genuine faults) on `upload`, `PATCH` and `grade`.
  V-3 decided: `PATCH`/`update-async` follow the docstring — the
  submission's own student and the course teacher, both queryset-scoped.
  V-4 closed by the shared service. V-6 closed (dead kwarg removed).
  V-5 (`StudentViewSet` unrouted) is a deletion and stays for sign-off
  (owner signed off 2026-09-17; deleted in `e0b1640`).
* Evidence: `students/tests_async_edit_path.py` — route, tenancy,
  duplicate guards, task success/refusal/retry/refund, redelivery on a
  real Celery worker, 12 concurrent live-HTTP clients → exactly one task,
  and an opt-in **real provider** call (`RUN_REAL_AI=1`, run once:
  answer extracted from the text, wallet charged exactly once). Mutation
  M25–M30 in `docs/evidence/SECTION_7_GATE_EVIDENCE.md` §13.

**Client dependency check (what could be done from this repository):**
no frontend code lives here; the only references to the synchronous
routes are the backend docs (`docs/backend/students-and-submissions.md`,
`BACKEND_REFERENCE.md`, which already lists them as finding P3) and this
app's own tests. **Confirmation from the frontend owner is still
required** before retirement.

**Remaining to close H-11:**
1. Frontend/client confirmation that `upload-async`, `grade-async` /
   `schedule-grade-async` and `update-async` are what clients call.
2. Retire `POST .../upload`, `POST .../grade` and `PATCH .../<pk>` (or
   turn them into thin dispatchers returning 202) — a response-contract
   change, done with the frontend.
3. Give `upload_answers_engine_async` the same tracked-row idempotency
   claim the edit task now has (the grading task has its own claim; the
   upload task still relies on the request-level guard alone). Section 9
   is changing that task concurrently; do this after their change lands.
4. Full ten-state gate on the retirement change, per the owner's list:
   real provider, proxy timeouts, duplicate/replayed requests, concurrent
   submissions, task tracking, credit charged exactly once, failure after
   charge, retries, and a timed-out client retry never creating a second
   billed run.

# H-12 — Commented-out code burn-down (flake8-eradicate E800)

`docs/CODE_REVIEW_STANDARDS.md` §2 lists flake8-eradicate as enforced, but
`.pre-commit-config.yaml` had `E800` in its `--ignore` list, so it never was.

**History:**

- **§7 pass:** turned the rule ON and carved out, by name, the files that
  still carried legacy commented-out blocks. That was 930 E800 hits
  repo-wide, 500+ of them in `dashboard/`.
- **§8 pass:** cleaned all of `dashboard/` (84 hits on the merged tree) and
  removed its six entries.
  - Four files were cleaned: `views.py`, `serializers.py`, `tests_rigor.py`,
    and `urls.py`, which was already clean.
  - Two entries were for files §8 deleted (`at_risk_improvements.py`,
    `AT_RISK_IMPLEMENTATION_GUIDE.py`), so that also settles the
    documentation-as-code question.
  - Every removal was proved comment-only by an AST comparison.

**Item 9 progress (repository-wide burn-down, owner decision 2026-09-14):**

- §10 templates: `templates/assignment_to_prosemirror.py` (2 hits) cleaned; AST-identical, E800 0
- §0 cross-cutting (AutoGrader/urls.py): `AutoGrader/urls.py` (6 hits) cleaned; AST-identical, E800 0
- §1 users: `users/models.py`, `users/serializers.py`, `users/services.py`, `users/tests_throttle_client_identity.py`, `users/views.py` (27 hits) cleaned; AST-identical, E800 0
- §3 classrooms: `classrooms/models.py`, `classrooms/serializers.py`, `classrooms/test_bulk_enrollment.py`, `classrooms/test_views.py`, `classrooms/views.py` (26 hits) cleaned; AST-identical, E800 0
- §4 assignments (admin, serializers, tests_rigor): `assignments/admin.py`, `assignments/serializers.py`, `assignments/tests_rigor.py` (17 hits) cleaned; AST-identical, E800 0
- §2 billing (licensing, Stripe and credit services): `billing/access_control.py`, `billing/license_service.py`, `billing/license_views.py`, `billing/models.py`, `billing/services.py`, `billing/stripe_service.py`, `billing/stripe_view_schemas.py`, `billing/tasks.py` (77 hits) cleaned; AST-identical, E800 0
- §2 billing (serializers, views, tests and tools): `billing/serializers.py`, `billing/views.py`, `billing/tests/tests.py`, `billing/live_qa/invariants_individual.py`, `billing/management/commands/backfill.py` (67 hits) cleaned; AST-identical, E800 0
- §0 cross-cutting (AutoGrader/settings.py): `AutoGrader/settings.py` (29 hits) cleaned; AST-identical, E800 0
- §4 assignments (tasks, views): `assignments/tasks.py`, `assignments/views.py` (47 hits) cleaned; AST-identical, E800 0
- §5 ai_processor: `ai_processor/services.py` (47 hits) cleaned; AST-identical, E800 0

**Owner decision (2026-09-14):** the rule covers the **whole repository**.
The carve-out list was a temporary register, not a policy, and is now
retired — see closure below.

**CLOSED (2026-09-16).** Item 9's repository-wide burn-down finished: all 32
files that ever carried an E800 exemption are cleaned, the last being
`ai_processor/services.py` (47 hits). Full detail, including the strict
final gate and the post-merge verification on `beta`, is in
`docs/evidence/ITEM9_E800_BURNDOWN_EVIDENCE.md`. Landed on `beta` at
`a7c81a4` (fast-forward from `53e31c3`).

**Acceptance, all met:**

- `--per-file-ignores` is empty and removed from `.pre-commit-config.yaml`;
- `flake8 --select=E800 .` is clean, with only migrations excluded;
- this register is deleted (the file/hits table and its staged-plan rules
  above; the history above it is kept as the record of how H-12 got here).

**The one directory-wide exclusion:** `exclude: (^|/)migrations/` on the
whole flake8 hook, not only E800. It predates H-12 and is unaffected by its
closure. Migrations are generated by `makemigrations`, and hand-editing them
to satisfy a linter risks changing schema history, so this exclusion is
justified and is **not** part of the burn-down.

# H-13 — Uploads while grading is RUNNING — DECIDED

**Owner decision (2026-09-14): refuse additional uploads while grading is
in progress.** No implicit replacement or re-grade. The same decision
extends the graded-row lock to **teacher proxy uploads**: a graded
submission is immutable through every ordinary upload path, because
"new answers + old grade" is not an acceptable production state. A
correction after grading needs a future explicit replace/re-grade
workflow with its own authorization, audit trail, credit behaviour and
concurrency rules.

**Implemented** (`students.services._check_submission_open`):
* graded (`graded_at` set) → `SubmissionAlreadyGradedError`, every path;
* live grading claim (RUNNING and younger than `GRADING_CLAIM_STALE_AFTER`,
  the same staleness rule the claim itself uses, so a dead worker's claim
  does not lock the row out) → `SubmissionBeingGradedError`, every path;
* attempt limit → students only.
Applied under the row lock for student and proxy uploads, pre-checked
before the billed extraction where the student is known (student paths),
and mapped to 409 at `upload`, `upload-async` and `PATCH raw_input`; the
batch task records it as a final, non-retried failure.

**Evidence:** `students/tests_post_grading_submission_lock.py` (service,
API, task, 8-thread proxy and student concurrency, grade-commit race,
stale-claim exception) and mutation checks M22-M24 in
`docs/evidence/SECTION_7_GATE_EVIDENCE.md` §11.

# H-14 — School-admin summary rebuild cost (cache family 23)

**Found 2026-09-15** by the H-1 post-H-10 stampede measurement
(`docs/evidence/H1_STAMPEDE_MEASUREMENT.md`).

The school-admin summary rebuilds in **1,429 ms** at a realistic large
school (240 courses/school, 6,000 students), up from 211 ms at 24
courses/school. Its query count is flat (17), so this is not an N+1: the
cost grows with the rows each query processes. The entry is invalidated by
any activity in the school (`sch`), so admins of busy schools will often
load it cold.

**Owner decision (2026-09-15):** tracked as a performance issue. It does
**not** by itself justify stampede protection, given the per-user cache
design and expected concurrency.

**Acceptance:** cold rebuild measured before and after at the same seeds,
identical payload, query count still flat, and the H-1 freshness tests for
family 23 still passing with legacy invalidation disabled.

---

# H-15 — `global`-scoped per-user cache families invalidate as a herd

**Found 2026-09-15** by the same measurement.

Student `my_courses` (family 11) and the superadmin dashboards (16, 21) are
cached per user but depend on the `global` generation. Any change anywhere
therefore expires every user's copy at once, and they rebuild independently.
Single-flight cannot help, because every key is different. Measured at
realistic scale: 50 students' `my_courses` after one `global` bump took
p50 792 ms / p95 1,262 ms (all 50 rebuilt).

**Scope:** narrow each family's dependency to what its payload actually
reads (e.g. family 11 on its courses' and teachers' generations instead of
`global`), derived from code, not names.

**Acceptance:** herd size and latency re-measured; freshness proved with
legacy invalidation disabled; no cross-tenant staleness.

---

# H-16 — Teacher submission list issues 63 queries per page — COMPLETE

**Found 2026-09-15** by the same measurement. **Assigned to Section 7 and
fixed 2026-09-15.**

The teacher's `student-submission-list` (UserCacheMixin) ran 63 queries per
cold build at every data size: a per-row query pattern. It was not slow at
the measured sizes (~47 ms) but grew with page size — up to 304 queries at
`page_size=100`.

Cause: `StudentSubmissionViewSet.get_queryset` returned a bare queryset for
the `list` action, and `StudentSubmissionListSerializer` reads each row's
`student`, `assignment` and `assignment.course` — three relations fetched
lazily, once per row.

Fix: `select_related("student", "assignment__course")`, added to
`get_queryset` for the `list` action only. Both relations are non-nullable
foreign keys, so the joins are `INNER JOIN` and cannot widen the tenant
filter already applied.

**Acceptance:** query count flat in page size (asserted, not budgeted),
identical payload, freshness unchanged. All three met.

**Evidence:** `docs/evidence/H16_SUBMISSION_LIST_QUERIES_EVIDENCE.md` —
query count 63/154/304 → flat 4 (5 with the assignment filter, itself
flat) across page sizes 1–100 and two data sizes; payload byte-identical
to the unoptimised serializer for every one of 32 request shapes; tenancy
unaffected (proven, not assumed); 3/3 mutants killed, restored by
checksum; regression 318 tests OK across `students` and every cache suite
touching this endpoint; scoped to `students/views.py` (14 insertions, 2
deletions) plus a new test module, nothing else.

---

---

# H-18 — assignment writes accepted any course, any topic, and any AI-emitted field

**Found 2026-09-17** by a cross-role data-leakage audit, then widened twice:
once by reading the views (a third entry point the audit missed), once by a
sweep of every writable relation (the AI-output sink).

**What was wrong.** `AssignmentTextSerializer.course` was a plain writable PK
field with no ownership check, while `get_queryset()` only scopes which
EXISTING assignment a teacher can reach. Knowing a course UUID was enough to
plant a PUBLISHED assignment in another teacher's course - visible at once to
that teacher and their students - or to move one's own assignment into it.
Three doors shared the serializer: create/create-async, PATCH, and
update-async, which built it without a request in context. Separately, the AI's
raw JSON was saved through `AssignmentSerializer`, whose writable fields
include `status`, `teacher`, `course`, `topic` and `due_date`, so text inside a
typed assignment or an uploaded document could set them.

**Scope of the fix.** `validate_course` on the serializer (fail-closed without
a request, so no view can forget it); `update_async` uses `get_serializer`;
`ai_assignment_content_only()` reduces AI output to the 9 content fields at
extraction, at generation, and again when a stored draft snapshot is saved;
`AssignmentSerializer.teacher` is read-only. Two fail-open ownership
validators in `classrooms/serializers.py` were hardened at the same time.

**Acceptance criteria and evidence.** All met; see
`docs/evidence/H18_H19_ACCESS_CONTROL_EVIDENCE.md`, which opens with the
10-gate table and the 8 completion answers:
- every entry point refuses a foreign course and a foreign topic, for a course
  in another school AND a same-school colleague's course;
- legitimate own-course and own-topic flows unchanged on every path;
- AI output cannot write any protected field at any sink, including pre-fix
  stored snapshots;
- 30/31 mutants killed, both survivors explained (one two-layer defence, one
  real test gap that was closed);
- 20 threads x 10 rounds; provider-failure and Celery-redelivery recovery;
  query counts flat to 6,000 students;
- independent HTTP replay by the red team.

**Open:** Gate 8 (deployed end-to-end) is PARTIAL - everything is LOCAL-REAL.

---

# H-19 — superadmin authority granted on a single flag in four places

**Found 2026-09-17**: two places by the audit, two more by this item's own
sweep of every superadmin check in the codebase.

**What was wrong.** `IsSuperAdmin` requires `is_superuser` AND
`user_type == SUPER_ADMIN`. Four checks did not:

| Where | Single flag | Effect |
|---|---|---|
| `users/views.py` `SettingsViewSet.get_queryset` | either | read and edit every user's Settings |
| `classrooms/views.py` `monthly_token_usage` | either | read any school's token usage |
| `users/permissions.py` `HasCreditBalance` | `user_type` | skip the credit-balance check |
| `ai_processor/services.py` `execute_graded_task` | `user_type` | unmetered, unbilled AI |

`CustomUserManager.create_superuser()` sets `is_superuser` but leaves
`user_type=TEACHER`, so an account made for Django admin reached the first
two. The reverse shape - `user_type=SUPER_ADMIN` without `is_superuser`,
produced by promoting a teacher through the users API or unticking the flag in
Django admin - reached the last two and ran billed AI for free through
background jobs that load `course.teacher`.

**Scope of the fix.** All four require both flags. A single-flag account is not
refused outright by the credit gate: it falls through to the ordinary wallet
check. The unmetered branch refuses it with `AIFeatureNotAvailableError`, a
user-facing refusal, rather than the `ValueError` that views report as a
server fault.

**Deliberately unchanged:** `billing/license_service.py:319` and
`users/serializers.py:175` use `or` on the deny side, where either flag is
stricter; `CustomUserViewSet.create`'s inner either-flag check is unreachable
behind `IsSuperAdmin` and is recorded as a consistency clean-up candidate.

**Acceptance criteria and evidence.** Same evidence file. All three attacker
shapes refused; true superadmin and school-admin flows unchanged; one real
billed provider call proves the unmetered path still works with zero billing
rows; 14 mutants across the four checks, all killed.

**Related, owned elsewhere:** making the newly-refused background paths record
their refusal cleanly (weekly-summary swallow, `error=None`, retry-3x) belongs
to the refusal-handling cluster, not to this item.

---

# AUTHZ-L2 follow-up — reset-code guess budget hardening (LOW; H-number for the SM to assign)

AUTHZ-L2 made the password-reset guess budget per account: at most 5 guesses
per 30-minute lock cycle (about 4 per 15-minute code-expiry window if the
attacker avoids the lock), so **~384 guesses/day** worst case, however many IPs
the attacker uses. Against a 6-digit code (1,000,000 values) that is under
0.04%/day per targeted account. That is fine for a drive-by attacker but a
patient one reaches meaningful odds over months against one high-value
account, and the same budget can be burned deliberately to keep a victim's
reset locked (recovery denial; login with the existing password is unaffected).

**Options (any subset):**
- An escalating lock (30 min, then hours, then a day after repeated lock
  cycles on one account), which bends the daily rate down to a handful.
- An 8-digit reset code (100x the space; changes email copy and the frontend
  input length).
- An audit event on lock. The audit app is not on `beta`, so the fix emits a
  structured `password_reset_otp_locked` warning on the `users.models` logger
  (user id and attempt count only). Replace or complement it with the audit
  emitter once that lands, and alert on repeated locks for one account.

**Acceptance:** worst-case guesses per account per day measurably lower than
384, or the code space larger; every lock visible to an operator. Evidence
as for AUTHZ-L2 (adversarial many-IP loop, mutation).

---

# H-41 — grading-redelivery concurrency test flakes under load

**Found**: seen failing once on CI, never reproduced locally until now.

`students.tests_grading_redelivery_live.GradingRedeliveryLiveTest.
test_5_concurrent_submissions_with_one_redelivery_each_grade_exactly_once`
failed during the staging@fc96d9a full-suite redo (2026-09-28, machine load
~30 from concurrent unrelated sessions): `'FAILED' != GradingState.DONE`.
Isolated rerun of the whole module immediately after, load ~20: 4/4 pass.

**Status**: load-induced, consistent with the CI sighting — treated as a
pre-existing flake, not a regression on whichever branch triggers it. No fix
scoped yet; recorded so a repeat sighting has a home instead of being
re-diagnosed from scratch each time.

**Acceptance**: TBD once the actual scheduling contention (if fixable) is
understood; at minimum, note here whether it reproduces isolated under
deliberately induced load.

---

# H-44 — `pdf_renderer` concurrent-render test is a wall-clock flake

**Found 2026-09-28** during authz-oauth-takeover Gate 10 verification.

`assignments.tests_pdf_renderer.ConcurrentRenderingTest.
test_one_slow_render_does_not_stall_the_others` asserts the slowest of 6
concurrent renders finishes under 4.0s. On this shared 4-physical-core
machine that fails whenever load is elevated — confirmed on plain
beta@4b902fc (3/3 failures, ~7s each) and on task/authz-oauth-takeover@27d36f0
(2/3 failures, same signature); `git diff --stat 4b902fc 27d36f0 --
assignments/` is empty, so it isn't branch-specific.

**Fix direction**: make the assertion independent of the wall clock — measure
relative ordering (the slow render finishes last; healthy ones finish close
together) or inject a fake clock — rather than raising the 4.0s threshold,
which only shifts where the flake reappears under heavier load.

**Priority**: low, behind the test-speed stream's current queue.

**Acceptance**: passes reliably at machine load comparable to a loaded
CI/dev box; a genuine stall (the behaviour this test guards against) must
still fail it.

---

# H-48 — thin-webhook signature tests made live Stripe calls and passed on a 500 — FIXED

**Found 2026-09-28** by the H-39 network guard on its first real CI run
(beta `be78221`, Tests run 36444904862): two blocked connections to
`api.stripe.com:443`, suite still green.

`ThinWebhookRealSignatureTests` inherited
`test_a_rolled_secret_still_verifies_while_both_are_live` and
`test_a_signature_just_inside_the_tolerance_is_accepted` without patching
`stripe.Event.retrieve`. The thin view verifies, then fetches the event from
Stripe; unmocked, that is a real network call. With the guard it fails and
the view returns 500; before the guard, CI called Stripe for real. Both
tests asserted only `!= 400`, so a 500 passed.

**Fix**: `retrieve` patched for the whole thin class in `setUp`; every
accepted-path assertion (fat and thin) is now `== 200`.
Evidence: `docs/evidence/h48_thin_webhook_mock/EVIDENCE.md`.

---

# H-49 — validation 400s log a full traceback at ERROR — LOW

**Found 2026-09-28** in the same CI run: `classrooms/views.py`
`_validate_uuid_query_param` (via `monthly_token_usage`) turns a bad
`school_id` into a `ValidationError` 400, and the request is logged as
"API Exception" with the full `badly formed hexadecimal UUID string`
traceback at ERROR. GitHub Actions surfaces those as error annotations on a
green run, and in production they would page as errors for ordinary client
mistakes.

**Scope**: client-error (4xx) responses should log at WARNING/INFO without a
traceback; genuine 5xx keep ERROR. Backlog only.

---

# H-50 — webhook cycle tests made live Stripe PaymentIntent calls — FIXED

**Found 2026-09-29** by the H-39 guard during the beta-batch-1 full run
(a112eda): two blocked connections to `api.stripe.com`
`/v1/payment_intents/pi_test_{1,2}?expand=latest_charge`, suite green.

`billing/tests/test_subscription_cycle_integrity.py`
`test_webhook_preserves_cycle_for_same_interval` and
`test_webhook_resets_cycle_for_interval_crossing` pass a real
`payment_intent` id into `_handle_individual_upgrade_checkout_completed`,
which reaches `resolve_stripe_receipt_url` →
`stripe.PaymentIntent.retrieve`. Only `stripe.Subscription`/`Invoice` were
patched, despite the module docstring claiming every Stripe call is mocked.
`resolve_stripe_receipt_url` swallows `StripeError` by design, so the blocked
(or, before H-39, real) call never failed the test. Present on beta before
the batch; not introduced by it.

**Fix**: `stripe.PaymentIntent` patched in both tests, and the lookup
asserted (`retrieve("pi_test_N", expand=["latest_charge"])`).
Evidence: `docs/evidence/h50_cycle_receipt_mock/EVIDENCE.md`.

---

Several of these were found during Section 3 but are **not** Section 3
changes — H-1 spans four apps, H-2 lives in `users`/`assignments`/`students`,
H-5 is Section 7. They were deliberately kept out of the security work so
that diff stayed reviewable. That decision is what this document exists to
make safe: the work was postponed, not dropped.
