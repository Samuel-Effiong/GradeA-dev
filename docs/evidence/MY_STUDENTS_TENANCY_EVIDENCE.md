# Cross-teacher tenancy leaks in `my-students` and `/users/<id>` — evidence

Branch `task/my-students-prefetch-leak`, based on beta `b744c9f`.
**Gated tip: `948d710`** (code identical to `5ed650a`; `948d710` adds docs only).
Logs: `docs/evidence/my_students_tenancy/`, checksums in `SHA256SUMS.txt`.
All figures below are copied from those logs.

## The 10 gates

| Gate | Status | Evidence |
|---|---|---|
| 1 — Baseline / regression | **PASS** | Pre-fix reproduction on `b744c9f`: 22 of 42 fail (`1-prefix-repro-b744c9f.log.gz`). Post-fix on `948d710`: `classrooms students dashboard users assignments` = **1,759 tests, OK, 17 skipped, exit 0** (`3-targeted-948d710.log.gz`). Skips unchanged from beta. No existing test was modified or weakened. |
| 2 — Mutation | **PASS** | 16 mutants, one per changed guard, in disposable worktrees at the commit. On `948d710`: **15 KILLED, 1 SURVIVED (M7, equivalent)**; M14 re-run alone also SURVIVED (equivalent) after its first run was invalidated by Postgres connection exhaustion. Every restore sha256-verified, every worker removed (`5-mutation-logs-948d710.tar.gz`, `7-mutation-m14-rerun.tar.gz`, harness `battery.py`). |
| 3 — Concurrency | **PASS** | `classrooms/tests_my_students_concurrency.py`: **20 threads x 10 rounds** on real Postgres, teacher B's roster enrolled/removed through the production services while teacher A reads. 500 reader rows checked, **0 violations**, both write directions exercised, every thread `is_alive()`-asserted after join. |
| 4 — Adversarial | **PARTIAL (this session)** | The attacks are reproduced as tests from the attacker's side, and each one fails on `b744c9f` (Gate 1 log). Independent replay by `grade-automator-plus-04` (Red Team Lead) and `grade-automator-plus-25` (red-team-tenancy) is **in progress**; their result is not in this document. |
| 5 — Failure / recovery | **PASS** | `my-students` is not cached: `UserCacheMixin` caches `list`/`retrieve` only, and `my_students` is a separate action. A test patches both cache modules and asserts **no cache call happens**. With Redis unreachable (`redis://127.0.0.1:1/0`) the endpoint still answers 200 with only the requester's own data; alternating teachers on real Redis each get their own payload. The endpoint performs no writes and calls no external service. |
| 6 — Stress / scale | **PASS** | `classrooms/scale_my_students.py`, local DB: 600 students / 2,400 enrollments / 9,600 submissions / 50 courses, then **6,000 / 24,000 / 96,000 / 500**. Measured teacher's roster 60 → 600 (exactly 10x). **Queries constant at 5** for every request shape at both sizes. Numbers below (`6-scale-6000-students.log.gz`). |
| 7 — Real infrastructure | **PASS (LOCAL-REAL)** | Every run above used the real local PostgreSQL (127.0.0.1:5432) and, where cache behaviour was under test, the real local Redis (127.0.0.1:6379). No mocked DB or cache. The deployed beta's database and Redis were never contacted. |
| 8 — Live / end-to-end | **PARTIAL (LOCAL-REAL)** | Full request path exercised (client → auth → permissions → filter backends → queryset → serializer → response) against real local services. Per the coordinator's tiering for this logic-only change, Gate 8 is LOCAL-REAL plus the post-landing QA-beta smoke; **no DEPLOYED-REAL replay has been run**. |
| 9 — Security / isolation | **PASS** | Both directions probed for teacher↔teacher (school-less pair and same-school pair), school-admin↔outside-school, student, both-flag superadmin, single-flag superuser and unauthenticated. Whole-payload assertions. Permanent regression tests for both vulnerabilities. Details below. |
| 10 — Final production gate | **NOT RUN** | By design: this branch joins one integration commit with `task/free-plan-activation`, gated once by the integrator (`3e`). No per-branch full gate was run, and `948d710` has had no full-suite run. |

**Doctrine note (Part II H1.3):** this is a security/isolation change, so Gates 4, 8 and 10 being short of PASS blocks landing until they are completed (4 and 10) or the user signs off in writing on the Gate 8 tiering. Nothing here should be read as "ready to land".

## The 8 completion answers

1. **What changed.** Three code changes. (a) `StudentCourseViewSet.my_students` now filters both prefetches by `course__teacher=user`, and a new `classrooms/filters.py::MyStudentsFilter` replaces `filterset_fields` so `?enrollments__course=` / `?enrollments__course__session=` match only through the requester's own enrollments. (b) `CustomUserViewSet` now uses `users/filters.py::UserEnrollmentFilter` instead of `filterset_fields`, so every `enrollments__*` lookup passes through `visible_enrollments(user)`. (c) The unrouted `StudentViewSet` in `students/views.py` is deleted (backlog V-5, owner sign-off 2026-09-17).
2. **Why it was necessary.** A student is routinely enrolled with several unrelated teachers (student accounts have `school_id` NULL, and a school-less account may join any individual teacher's course). Both endpoints joined *every* enrollment such a student had. `my-students` therefore printed other teachers' course names, and with `?enrollments__course=<their course>` served that course's description, its teacher's full name and the student's grade in it. `/users/<id>` became a yes/no oracle about other teachers' enrollments and withdrawals, on GET and on PATCH.
3. **What was tested.** 42 dedicated tests across three new modules, plus the existing query-budget and penetration suites, the concurrency module, the scale harness and the affected-app suites (1,759 tests).
4. **Which gates passed.** 1, 2, 3, 5, 6, 7, 9.
5. **Which gates are incomplete.** 4 (independent replay running elsewhere), 8 (LOCAL-REAL only, no deployed replay), 10 (deferred to the integration commit).
6. **What risks remain.** (i) The behaviour change in 7 below. (ii) `/users/<id>`'s retrieve cache key ignores query parameters, so a warm cache can answer 200 for a filter that would 404 cold — pre-existing, unchanged, and not cross-tenant (the cached body is one the requester may already see), but red-team-tenancy has been asked to attack that reasoning. (iii) Gate 8 is not deployed-real. (iv) The sweep found no other live instance of this pattern, but it was a read of the code, not an exhaustive proof.
7. **Which exact commit contains the verified implementation.** `948d710` on `task/my-students-prefetch-leak`.
8. **Is the verified commit the one intended for release?** No. `948d710` is intended to be **merged into an integration commit** with `task/free-plan-activation` and gated there. That integration commit is a new commit and must be gated itself.

## Behaviour changes (Gate 1 requirement)

| | Previous | New | Why correct | Proved by |
|---|---|---|---|---|
| `my-students?enrollments__course=<unknown uuid>` | 400 "select a valid choice" (the filter validated the id against every course) | 200 with 0 rows | A 400-vs-404-vs-200 split told the caller whether a course id exists and whether their student is in it. A foreign id must be indistinguishable from a meaningless one. | `test_course_filter_naming_another_teachers_course_returns_no_rows`, `assert_same_as_unknown_id` |
| `my-students?enrollments__course=<other teacher's course>` | the shared student, described by *that* course | 0 rows | The teacher may not learn that their student is also in another teacher's course. | same |
| `/users/<id>?enrollments__*` | matched through every enrollment | matches only through enrollments the requester may see | Removes the oracle; own-scope filtering is unchanged. | `users/tests_user_enrollment_filter_oracle.py` |
| malformed uuid / unknown status | 400 | 400 (unchanged) | Input validation is not an oracle. | `test_malformed_course_id_is_still_rejected`, `test_malformed_values_are_still_rejected` |

## Gate 2 — mutants, one per guard

Run on `948d710`, `K=4` disposable worktrees, each restored from `git show <commit>:<path>` and sha256-verified.

| Mutant | Guard it removes | Verdict | Killed by (first) |
|---|---|---|---|
| M1 | `enrollments` prefetch `course__teacher=user` | KILLED (10 tests) | `test_teacher_a_sees_only_their_own_course_names` |
| M2 | `submissions` prefetch `assignment__course__teacher=user` | KILLED | `test_prefetch_caches_hold_only_the_teachers_own_rows` |
| M3 | `MyStudentsFilter` → old `filterset_fields` | KILLED (4) | `test_course_filter_naming_another_teachers_course_returns_no_rows` |
| M4 | `MyStudentsFilter` `course__teacher=request.user` | KILLED (4) | same |
| M5 | my-students session filter ignores the id | KILLED (3) | `test_my_students_session_filter_cannot_reach_another_teachers_session` |
| M6 | my-students course filter ignores the id | KILLED (3) | `test_my_students_course_filter_cannot_reach_another_teachers_course` |
| M7 | `_resolve_relevant_course` own-teacher fallback | **SURVIVED — equivalent** | see below |
| M8 | `visible_enrollments` teacher scope | KILLED (6) | `test_course_filter_on_retrieve` |
| M9 | `visible_enrollments` school-admin scope | KILLED (2) | `test_outside_teachers_course_is_invisible` |
| M10 | `UserEnrollmentFilter` → old `filterset_fields` (covers the PATCH path) | KILLED (8) | `test_course_filter_on_patch` |
| M11 | all `/users` lookups drop `visible_enrollments` | KILLED (8) | same |
| M12 | `status__in` bypasses the scoped helper | KILLED | `test_withdrawn_status_elsewhere_is_invisible` |
| M13 | `isnull` filter ignores its value | KILLED | `test_isnull_true_means_no_visible_enrollment` |
| M14 | `visible_enrollments` own-rows fallback | **SURVIVED — equivalent** | see below |
| M15 | superadmin branch `and` → `or` | KILLED | `test_single_flag_superuser_is_scoped_like_a_teacher` |
| M16 | superadmin loses platform-wide scope | KILLED | `test_superadmin_filters_remain_platform_wide` |

**Equivalence arguments.**
*M7:* with M1's guard in place (and M1 is killed), every enrollment in the prefetch cache satisfies `course.teacher_id == request.user.id`. The fallback loop therefore returns the first cached enrollment's course, which is exactly what the mutant returns via `enrollments[0]`. The `?enrollments__course=` branch above it is untouched. No input can distinguish them.
*M14:* `Q(student=user)` is reached only by accounts whose `get_queryset()` is `filter(pk=user.pk)` — students, school admins without a school, and `SUPER_ADMIN`-typed accounts without `is_superuser`. The `Exists` subquery is correlated on `student=OuterRef("pk")`, and the only candidate row is the requester's own, so `Q(student=user)` and `Q()` select identically.

**Invalidated run, recorded for honesty.** The first pass on `948d710` reported M14 KILLED. Its only failing test was the 20-thread concurrency module, and the log shows `FATAL: sorry, too many clients already` — Postgres `max_connections=100` was exhausted by four parallel battery workers (20 connections each) plus other sessions' runs. That is an environment failure, not a mutant kill, so the verdict was discarded and M14 re-run alone: SURVIVED. Nine of the sixteen logs contain that error; every other mutant's verdict rests on at least one non-concurrency test, so only M14 needed re-running. A separate bug in the harness's verdict classifier (the substring `ImportError` matching `RosterImportError`) mislabelled the first battery's verdicts; it was fixed and the verdicts recomputed from the unchanged logs.

## Gate 6 — scale

Local Postgres, single process. Build: 18 s to 600 students, 161 s to 6,000.

| Shape | Queries 600 → 6,000 | p50 ms | p95 ms | Peak alloc |
|---|---|---|---|---|
| default page (20 rows) | 7 → **5** | 22.7 → 23.8 | 25.9 → 28.1 | 793 → 772 KB |
| `page_size=100` | 5 → **5** | 41.1 → 53.7 | 140.3 → 164.3 | 2,088 → 3,391 KB |
| last page, `page_size=100` | 5 → **5** | 61.3 → 74.2 | 176.3 → 199.4 | 2,097 → 3,404 KB |
| `?enrollments__course=` | 5 → **5** | 25.1 → 27.4 | 31.1 → 29.5 | 780 → 802 KB |
| `?enrollments__course__session=` | 5 → **5** | 25.0 → 68.0 | 31.0 → 81.8 | 785 → 789 KB |
| `?search=` | 5 → **5** | 19.4 → 51.9 | 25.2 → 69.3 | 495 → 786 KB |

Query count is flat across a genuine 10x (the roster itself grew 60 → 600). Latency grows sub-linearly; the session filter and search shapes grow most, both bounded by index selectivity on a 10x table, not by row fan-out. Memory tracks rows per page, not table size.

## Gate 9 — isolation matrix

| Actor | Probe | Result |
|---|---|---|
| Teacher A (individual), student shared with teacher B | `my-students`, all shapes | Only A's courses; no B name, description, grade or submission count anywhere in the payload |
| Teacher B | mirror image | Only B's course; nothing of A |
| Same-school teachers A and C sharing a pupil | both directions | Each sees only their own course |
| Teacher A | `?enrollments__course=`/`__session=` naming B's or C's row | 0 rows, body byte-identical to an unknown UUID |
| Student | `my-students` | 403 |
| School admin | `my-students` | 403 |
| Superadmin (both flags) | `my-students` | 403 |
| Single-flag superuser (`is_superuser`, `user_type=TEACHER`) | `my-students` | 200, own (empty) roster |
| Unauthenticated | `my-students` | 401/403 |
| Teacher A | `/users/<S>` with B's course, B's session, `WITHDRAWN`, `__in`, on GET and PATCH | Indistinguishable from a no-match; PATCH changes nothing |
| School admin | `/users/<S>` naming an outside teacher's course or withdrawal | Indistinguishable from a no-match |
| Superadmin (both flags) | `/users/<S>` and `/users` list with any course | Still platform-wide, unchanged |
| Single-flag superuser reaching S through their own course | `/users/<S>` with B's course | 404, same as a no-match; own course still 200 |
| Student | `/users/<self>` filtered by their own enrollment | 200 — their own data |

## Sweep for the same pattern elsewhere

Every `Prefetch`/`prefetch_related`, every serializer method walking a related manager, every `filterset_fields`/`filterset_class`/`search_fields`/`ordering_fields`, and every caller of the "pick a relevant course" helpers in `classrooms`, `students`, `dashboard`, `assignments`, `users` and `billing` were read. Verdicts:

* **Fixed here:** `classrooms/views.py` `my_students` prefetches and filters; `users/views.py` `CustomUserViewSet` filters.
* **Deleted:** `students/views.py` `StudentViewSet` — unrouted, and an exact copy of the pre-fix pattern (V-5, owner sign-off).
* **Safe, checked:** `SchoolViewSet.school_admins` and `billing/views_admin_credits.py` (both `IsSuperAdmin`, platform-wide by design); `CourseViewSet`'s `assignments__submissions` and `active_enrollments` prefetches (single course, and a student viewer already has emails blanked and drafts hidden — H-17); `StudentCourseViewSet`'s non-`my_students` branch (submissions already scoped to the teacher's courses); `dashboard/views.py` overview and `students` actions and `dashboard/services.py` (all keyed on one course or on `course__teacher__school`); `assignments/serializers.py` submission counts and rosters (assignment's own course); `students/serializers.py` `get_enrollment_status`; `classrooms/services/enrollment.py::schools_associated_with` (deliberately global, answers only the generic rejection message); `ai_processor` and dashboard helpers (own course or own student).
* **Over-fetch only, no leak:** the non-`my_students` `student__submissions` prefetch loads a student viewer's own drafts and discards them in Python.
* **Noted for other owners, not in this diff:** `classrooms/views.py::monthly_token_usage` and `users/views.py::CustomUserViewSet.create` accept **either** superadmin flag rather than both (H-19 territory, `fix-idor`).
* **Quirk, unchanged:** `enrolled_courses` includes the teacher's own courses the student has withdrawn from. That is the teacher's own data; flagged rather than changed, because changing it is a product decision.

## Reproduction (Gate 1)

```
# pre-fix, worktree detached at b744c9f with only the two test modules copied in
python manage.py test classrooms.tests_my_students_course_scope \
    users.tests_user_enrollment_filter_oracle --settings=settings_worktree --noinput
Ran 42 tests — FAILED (failures=22)

# post-fix, on 948d710
python manage.py test classrooms students dashboard users assignments \
    --settings=settings_worktree --keepdb --noinput
Ran 1759 tests — OK (skipped=17), exit 0
```

## Files

| Path | What |
|---|---|
| `classrooms/views.py` | scoped prefetches, `filterset_class` |
| `classrooms/filters.py` | new — `MyStudentsFilter` |
| `users/views.py` | `filterset_class` |
| `users/filters.py` | new — `UserEnrollmentFilter`, `visible_enrollments` |
| `students/views.py` | `StudentViewSet` deleted |
| `classrooms/tests_my_students_course_scope.py` | 15 tests — leak, filters, roles, cache |
| `classrooms/tests_my_students_concurrency.py` | 1 test — 20 threads x 10 rounds |
| `users/tests_user_enrollment_filter_oracle.py` | 19 tests — the `/users/<id>` oracles |
| `classrooms/scale_my_students.py` | Gate 6 harness, not discovered by the suite |
