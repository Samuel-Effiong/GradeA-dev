# H-71: the student-add routes name no role

Branch `task/h71-student-add-role` off beta `abeda10`. Author: ed (Security).
Verifier: 1a. Scope, including the two widenings, was approved by the SM.

## The hole

A teacher adding a student by email was told the role of any address that
belonged to a non-student account. That made the form a staff directory:

| Route | Site (abeda10) | Said |
|---|---|---|
| Single add (`course-students`) | `classrooms/serializers.py:492-501` | "This email belongs to a teacher account…" / "…cannot be added as a school admin." |
| Bulk import (`course-bulk-add-students`) | `classrooms/services/enrollment.py:133-136` (per-row error) | "This email belongs to a {role} account…" |
| Direct add (`course-direct-add-student`) | `classrooms/serializers.py:531-537` | "This email belongs to a teacher account…" (teachers only) |

Direct add refused only teachers at validation. An admin address passed
validation, was refused later by the enrolment rule inside `create()`, and
came back as a **500**. So even with the wording fixed, the status code
would still have told a teacher address from an admin one.

## The fix

- `NOT_A_STUDENT_MESSAGE = "This email can't be added as a student."` in
  `classrooms/services/enrollment.py`, exported from `classrooms.services`.
  It is the only answer, on all three routes, for every non-student role.
- Direct add refuses every non-student role at validation, the same way
  single add does, so every role gets the same 400 on every route.
- The enrolment rule logs `Refused to enrol non-student account <id>
  (<user_type>) in course <id>`, with ids only and no email, so an admin keeps
  the detail.
- The QA catalogue proposal's `ROW_STAFF_EMAIL` is now neutral too, with no
  `account_type` param (task/qa-catalogue-proposal 0ecad75).

## Tests: `classrooms/tests_h71_student_add_role.py`

For each of teacher, school admin and super admin, on each of the three
routes:
- No role word appears anywhere in the response body (teacher, admin,
  administrator, super, staff).
- No enrolment is created.
- Single add returns 400 with the neutral message, and the bulk row error is
  the neutral message.
- Every role gets the same (status, body) on every route, so the answer
  cannot tell the roles apart.

Also:
- Control: a real student address is still added.
- Service rule: `check_existing_account_may_join` raises the neutral
  message, and its log line carries the account id and not the email.

## Gates

| Gate | Result | Log |
|---|---|---|
| Reproduce-first on abeda10 (serializers + enrollment reverted; only the unused constant appended so the tests import) | 15 FAIL of 5 tests (subtests): the role is named on every route for every role (except school/super admin on direct add, which instead differ from the teacher answer by status: a 500). The student control passes on the prefix. | `prefix_abeda10_failing.txt` |
| Changed modules, the enrolment/add suites and all repo-wide guards present on beta | 167 OK (run 2, 3fec037). Run 1 at 98e5090 stopped here on one test that pinned the role word; see the behaviour change below. Log: `changed_modules_run1_stopped_98e5090.txt` | `changed_modules.txt` |
| Mutation (M1–M5) | 5/5 killed, 0 survivors, source clean after | `mutation_log.txt`, `mutation_results.json` |
| ONE owning-app regression: classrooms | 383 OK | `regression_classrooms.txt` (trimmed; full log in GAP-evidence-logs) |
| `pre-commit run mypy --all-files`, `makemigrations --check` | pass (at 5a5e4c3) | n/a |

The changed set follows rule 15 addendum 2, since this adds a test module:
- `AutoGrader.tests_no_wildcard_invalidation`
- `AutoGrader.tests_cache_invalidation_coverage`
- `AutoGrader.tests_migration_rollback_defaults`
- `classrooms.tests_teacher_access_sweep`
- `classrooms.tests_course_roster_scope_sweep`

`tests_reason_codes` does not exist on beta.

### Mutants

| Mutant | Change |
|---|---|
| M1 | the enrolment rule names the role again (bulk import, and direct add's create path) |
| M2 | single add names the role again |
| M3 | direct add refuses only teachers again (admins reach the 500 path) |
| M4 | direct add names the role again |
| M5 | the refusal log drops the account id |

## Behaviour change: an existing test's expected message

| | |
|---|---|
| Test | `classrooms.tests_security_penetration.BulkAndUploadAbuseAttacks.test_bulk_import_cannot_hijack_an_existing_teacher_account` |
| Old expectation | the bulk row error contains "teacher" (the message was "This email belongs to a teacher account and cannot be added as a student.") |
| New expectation | the bulk row error equals `NOT_A_STUDENT_MESSAGE`, "This email can't be added as a student." (03bebb3) |
| Why | the old assertion pinned the very role disclosure H-71 removes |
| What did NOT change | the hijack is still refused exactly as before: the row still fails (`failure_count == 1`), the teacher is still a TEACHER, and no enrolment is created. The test's other three assertions are untouched. Only the wording changed. |

The single-add refusal keeps its 400. Direct add now refuses every
non-student with a 400 at validation. Before, school admins and super admins
got a 500 from the create path, and teachers got a 400. That status change
is the intended fix (widening b, SM-approved), and it is pinned by
`test_every_role_gets_the_same_answer`.
