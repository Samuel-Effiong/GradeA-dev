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
| Reproduce-first on abeda10 (serializers + enrollment reverted; only the unused constant appended so the tests import) | see log | `prefix_abeda10_failing.txt` |
| Changed modules, the enrolment/add suites and all repo-wide guards present on beta | see log | `changed_modules.txt` |
| Mutation (M1–M5) | see log | `mutation_log.txt`, `mutation_results.json` |
| ONE owning-app regression: classrooms | see log | `regression_classrooms.txt` |
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
