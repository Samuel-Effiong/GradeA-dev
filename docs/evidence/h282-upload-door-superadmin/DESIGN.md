# H-282 DESIGN: the upload door's super admin check uses both flags

Branch `task/h282-upload-door-superadmin` from `162a43e66` (the beta-batch-33
tip under the B33 full run; SM, 2026-11-03). Owner go: 2026-11-03, Batch 34.

## Problem

`assignments/upload_door.py` skips the credit door for a super admin in both
of its functions, but checks `user_type` only:

- `upload_refusal_if_unaffordable`, line 87: `if request_user.user_type == UserTypes.SUPER_ADMIN: return None`
- `parked_files`, line 121: `if request_user.user_type == UserTypes.SUPER_ADMIN: return {}`

Everywhere else a super admin needs BOTH `user_type == SUPER_ADMIN` and
`is_superuser` (H-19). That includes `IsSuperAdmin`, `HasCreditBalance`,
`execute_graded_task` and `license_service._is_superadmin_actor`. A half-set-up
account (`is_superuser` unticked) passes the door and is then refused by the
task.

## Finding: today the branch is reached only by a direct call

Every route that calls the door refuses a super admin of either kind first:

| Route | Door function | Permission classes |
|---|---|---|
| `students/views.py` `upload_answers_async` | `upload_refusal_if_unaffordable` | `IsAuthenticated, IsStudent, HasCreditBalance` |
| `students/views.py` `batch_upload` | `parked_files` | `IsAuthenticated, IsTeacher, HasCreditBalance` |
| `assignments/views.py` `upload_assignment_async` | `parked_files` | `IsAuthenticated, IsTeacher, HasCreditBalance` |

`IsTeacher` and `IsStudent` check `user_type` exactly, so a `SUPER_ADMIN`
gets a 403 before the view runs. That makes the wrong check **latent**: no
HTTP request reaches it today. It is still fixed here, so that the door agrees
with the rest of the code if a route ever opens to super admins. The 403 at
the routes is pinned as a characterization test.

**Correction to H-281 DESIGN (d).** It said that at the upload door "the door
lets the account through and the task then refuses it with the 403 above".
That is true only for a direct call of the door function. Over HTTP, the
route's `IsTeacher`/`IsStudent` refuses first, with a 403 permission denial.
The H-281 characterization tests did not pin the door, so no H-281 test
changes. Part (c) of the ticket has nothing to update; see "Existing tests"
below.

Caller survey: `graphify explain` on both functions, then checked in the files.
It found the same three callers plus the test imports, with correct file:line.

## Helper

There is no public user-level predicate for this. The existing ones are:

- `classrooms.permissions.IsSuperAdmin` and `users.permissions.HasCreditBalance`
  are DRF permission classes, not user predicates;
- the three private copies are `billing.license_service.LicenseService._is_superadmin_actor`
  (staticmethod), `billing.qa_console._is_superadmin` and
  `audit.context._is_active_super_admin` (which also needs a saved, active row).

Decision, following the SM's option: one small public method,
`CustomUser.is_real_super_admin()` in `users/models.py`, next to
`is_student()` and `is_teacher()`. It returns
`bool(self.is_superuser and self.user_type == UserTypes.SUPER_ADMIN)`.

- The name says "real", the term the existing
  `ai_processor/tests_real_superadmin_unmetered.py` already uses, so it can't be
  read as a `user_type`-only check.
- It is a model method: no field, no migration.
- The three private copies could switch over to it later. **They are not
  touched in H-282.**

## Fix

Both early returns become `if request_user.is_real_super_admin():`. Nothing
else in the door changes.

A half-set-up super admin who calls the door is then treated like any other
non-student:
- `_billed_user` returns the caller;
- their own wallet (created by the user signal) is checked;
- the usual refusal applies.

They are not a `TEACHER`, so per H-281 the refusal is untagged and the message
is the school sentence (`INSUFFICIENT_CREDITS_MESSAGE`). **There is no new
wording.**

The module docstring item "a super admin are left to the task" stays true for
a real super admin. It is not reworded.

## Existing tests

- `students/tests_upload_credit_door.TheDoorFunctionTest.test_a_super_admin_is_never_refused_and_a_poor_teacher_is`
  already builds a both-flags admin (lines 377–379). It is unchanged, and stays
  green as the existing control.
- `billing/tests_h281_individual_credit_wording.AHalfSetUpSuperAdminAtTheCreditGate`
  pins `HasCreditBalance`, the 402 body and the AI gate, not the door. It is
  unchanged.
- No existing test is expected to change.

## Tests (red first), `students/tests_h282_upload_door_superadmin.py`

- **Predicate:** both flags gives True. A half-set-up account gives False. A
  teacher with `is_superuser` ticked gives False. A plain teacher gives False.
- **(a)** A half-set-up super admin with a short wallet and with an empty
  wallet is refused by `upload_refusal_if_unaffordable` (402, the school
  sentence) and by `parked_files` (file 0 parked, untagged).
- **(b) Control:** a real super admin with the same short wallet still gets
  `None` and `{}`.
- **Routes (characterization, green before and after):** a half-set-up super
  admin on the batch route and on the assignment route gets 403, and nothing is
  queued.

## Planted bugs (one per guard)

The predicate's two conditions, each door call reverted to a `user_type`-only
check, and each door's early return removed.

## Near run (held until the SM says B33 is done)

- the upload-door modules: `students.tests_upload_credit_door`,
  `students.tests_h223_batch_runs_what_fits`;
- the H-281 module and the H-282 module;
- the both-flags modules: `ai_processor.tests_real_superadmin_unmetered`,
  `users.tests_models_and_admin`, `classrooms.tests_my_students_course_scope`,
  `assignments.tests_course_ownership_concurrency`;
- the students upload tests.

No migration is expected. If one appears, the run adds
`AutoGrader.tests_migration_rollback_defaults`.
