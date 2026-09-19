# H-38 reproduce-first: removing a teacher from a school license

**Commit reproduced on:** beta `fba1294` (fba129402f5d5262e13652f12f04aa555d84ce29)
**Test:** `billing/tests/test_h38_teacher_removal.py`
**Log:** `reproduce_first_fba1294_run1.log` (unfiltered `-v 2` output; sha256 recorded from the committed blob, see below)
**Result:** 11 tests, 10 fail. Every failure message was checked against the defect it names. One test passes, and it is a real negative result, not a harness artefact.
**Severity (Senior Manager, 2026-09-19):** HIGH tenant-isolation security finding. It affects no real users today because no schools are in production, but it must land before the first school onboards.

## How the scenario is built

Every row the bug depends on is written through production endpoints, using real signed JWTs checked by the real authenticator:

1. The School A admin adds an existing individual-track teacher by email: `POST /license-subscriptions/{id}/add_teachers`. This is the path that sets `user.school`.
2. The admin creates a school session: `POST /sessions`.
3. The teacher creates a course in that session: `POST /course`.
4. The teacher enrols a student: `POST /course/{id}/students`.
5. The admin removes the teacher: `POST /license-subscriptions/{id}/remove_teachers`. The test asserts what the endpoint does do: the allocation becomes inactive and `is_under_license()` returns False.

Created directly: the School, the admin users and the `LicenseSubscription` row. Production creates these through the superadmin flow and Stripe checkout, which are not under test.

## What the failures show

### The removed teacher keeps access to School A's data

| Probe | Expected after removal | Observed on fba1294 |
|---|---|---|
| `GET /course/{school_course}` | 404 | **200**, full course |
| `GET /course` | school course absent | **listed** |
| `GET /users/{school_student}` | 404 | **200**, student profile |
| `PATCH /course/{school_course}` | 403/404, unchanged | **200**, renamed "Taken over" |
| `POST /course/{school_course}/students` | 403/404, no new user | **200**, new student created |

Root cause: `CourseViewSet.get_queryset` scopes a teacher by `course.teacher=user` alone (`classrooms/views.py:1261`). Removal never touches course ownership.

### School A keeps a hold on the teacher's private data

After removal the teacher is individual-track again, creates a private session and course, and enrols a private student.

| Probe | Expected | Observed |
|---|---|---|
| private student's `school_id` | not School A | **School A** |
| School A admin `GET /users/{private_student}` | 404 | **200** |
| School A admin `GET /school-admin/dashboard/students` | private student absent | absent: **passes** |

Root causes: `_create_pending_student` (`classrooms/services/enrollment.py`) copies `course.teacher.school` onto a new student, so the stale link spreads. `CustomUserViewSet.get_queryset` then gives a school admin every user with `school_id` equal to theirs (`users/views.py:283-287`).

### Display and re-onboarding (the originally reported symptom)

| Probe | Expected | Observed |
|---|---|---|
| teacher's `school_id` | None | **School A** |
| School A `GET /school-admin/dashboard/teachers` | teacher absent | **listed** |
| School B `add_teachers` for the same teacher | successful: 1 | **failed**: "already belongs to school 'School A H38'" |

## Not probed yet

- Assignment creation: it is AI-billed and credit-gated, and the removed teacher's buckets are expired, so a probe would measure the credit gate, not access.
- Assignments, submissions and grades reads on the old course.
- Bulk and async variants, and the licence-cancellation path, which has the same stale-link shape.

These follow before the design proposal. The independent attacker replay is red-team-tenancy's (H5.1).

## Harness corrections made on the way (earlier runs discarded)

- The routers use `trailing_slash=False`, so URLs with a trailing slash returned 404 during setup.
- The API renderer wraps JSON, so the test reads `response.data`.
- The `/users` list is superadmin-only. The admin roster is read from the school-admin dashboard instead.
- The login throttle tripped across tests, so JWTs are minted with `RefreshToken.for_user`. Real signed tokens still pass through the real authenticator.

None of these runs counts as evidence. Only the committed log does.
