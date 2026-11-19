# F1 build interface (agreed between the lead builder and the Fixer, for the adversarial tests)

Source of truth is STEP0_PLAN.md (e1238f983 plus the small fixes in this commit). The Fixer writes the tenancy matrix (M1-M20), the differential D0 and the plants against **these names only**, without reading the implementation. If a name here has to change, the lead says so in a message before the change merges.

## Module and app
- App `departments`, models in `departments/models.py`; tests in flat modules `departments/tests_*.py` (a pre-commit hook rejects non-`test*` names inside a `tests/` folder).
- Flag: `settings.DEPARTMENTS_ENABLED` (default `not _RUNNING_TESTS`). Tests turn it on with `override_settings(DEPARTMENTS_ENABLED=True)`; flag-off tests set it False.

## Models (fields the tests may create directly)
- `Department(id uuid, school FK classrooms.School, name, name_key, description, created_by_id, created_at, updated_at)`
- `DepartmentMembership(id uuid, department FK, teacher FK CustomUser, can_edit bool default True, added_by_id, created_at, updated_at)`. `save()` validates teacher school == department school.
- Factory helpers in `departments/tests_factories.py` (slice 1, landed): `make_school()`, `make_admin(school)`, `make_user(user_type, school=None)`, `make_license(school, admin, is_active=True)`, `make_licensed_teacher(school, license)` (a real allocation through the license service), `make_school_with_license(teachers=2)` -> `(school, admin, license, [teachers])`, `make_department(school, name=None)`, `add_member(department, teacher, can_edit=True, added_by=None)`.

## Access functions (`departments/access.py`; the only authorization code)
- `is_licensed_teacher_of(user, school_id) -> bool` (the liveness predicate: active TEACHER, `user.school_id == school_id`, school active, active non-admin allocation on the school's newest license, active or not)
- `visible_departments(user) -> QuerySet[Department]` (admins: own school's; live teachers: departments they belong to; everyone else `.none()`)
- `member_department_ids(user) -> QuerySet` of department ids the user is a live member of
- `live_memberships(user) -> QuerySet[DepartmentMembership]`
- `can_member_edit(user, department, surface, lock=False) -> bool` (`surface` in `"lessons"`, `"library"`; both read `can_edit` today; never checks the admin role literal)

## Service functions (`departments/service.py`)
- `end_memberships(teacher, new_school_id, new_user_type, *, source, actor=None) -> int` (deletes memberships invalid after the change; idempotent; one MEMBER_REMOVE event each; fails closed)
- `create_department(actor, name, description="")`, `rename_department(actor, department, ...)`, `delete_department(actor, department, content=None, target=None)`, `add_members(actor, department, teacher_ids) -> {successful, failed, added, errors}`, `remove_member(actor, membership)`, `set_member_edit(actor, membership, can_edit)`
- Content registry: `DEPARTMENT_SCOPED_RESOURCES` in `departments/registry.py`; each entry `(model, visible_for, reassign, delete, count)`.

## Routes (prefix is the site API root; all behind the flag, all 404 when the flag is off)
Admin (`SCHOOL_ADMIN` only; teachers 403, anonymous 401):
- `GET, POST /school-admin/departments/`
- `GET, PATCH, DELETE /school-admin/departments/{id}/` (`DELETE ?content=refuse|delete|reassign&target=<id>`)
- `POST /school-admin/departments/{id}/members/` body `{teacher_ids: [...]}` (max 50)
- `PATCH, DELETE /school-admin/departments/{id}/members/{membership_id}/` (PATCH body `{can_edit}`)
Teacher:
- `GET /departments/` -> `[{id, name, can_edit}]`
- `GET /departments/{id}/roster/` -> `[{name, role}]` (role: "School admin" or "Teacher")
URL names (for the route-enumeration test): `departments-admin-list`, `departments-admin-detail`, `departments-admin-members`, `departments-admin-member-detail`, `departments-mine`, `departments-roster`.

## Statuses by role (pinned)
Student: 403 on both prefixes. SCHOOL_ADMIN on the teacher prefix (`/departments/` and roster): 403 (admins use their own prefix; no view branches on role). SUPER_ADMIN: 403 on both, with one DENIED event on admin write routes. Anonymous: 401 on both. Individual teacher: 200 `[]` on the list, the house 404 on the roster. Teacher on an admin route: 403.

## Roster contents
`[{name, role}]`: every live member as "Teacher", plus every active SCHOOL_ADMIN of the department's school as "School admin" (owner Q3). Admins are not membership rows; the roster view adds them. Order: admins first, then teachers, each by name. The "School admin" and "Teacher" texts are W9 on the owner sheet.

## Route kwargs and GET names
URL kwargs are `department_id` and `membership_id`. Readable (GET) routes: `departments-admin-list`, `departments-admin-detail`, `departments-mine`, `departments-roster`. `departments-admin-members` is POST only and `departments-admin-member-detail` is PATCH and DELETE only. Any later readable route must be added to this list and to the Fixer's pinned route list in the same change.

## Test setup in scope
The matrix fixture may use existing project code for everything that is not a department: the classrooms e2 fixtures helpers, billing models and helpers, and `LicenseSubscriptionService` (create the license, `add_teachers`, `remove_teacher_from_license`, and the replacement path at `license_service.py:1123`). Department rows come only from `make_department` and `add_member`, or the routes.

## Branches
After slice 1 (models, factories, URL names) lands on `task/epic-f1-build`, the Fixer works on `task/epic-f1-matrix` cut from it, touching only `departments/tests_matrix_fixture.py`, `tests_matrix_m.py`, `tests_matrix_t.py`, `tests_differential.py` and `plants/*.patch`. The lead never edits those files; the SM merges.

## Error contract
- Missing or out-of-scope department, individual teacher on detail/roster, admin with no school: **404** with the house body (`Resource not found`), byte-identical.
- Teacher on an admin route: 403 (house `IsSchoolAdmin` message).
- Per-teacher add failures are coded: `TEACHER_NOT_ON_LICENCE` (same body for unknown, student, admin, foreign-school, deactivated), `DEPARTMENT_ALREADY_MEMBER`.
- More than 50 `teacher_ids`: 400 with code `DEPARTMENT_BATCH_TOO_LARGE` and message W13 ("You can add up to 50 teachers at a time. Add the rest in another step."), nothing added.
- Other codes: `DEPARTMENT_NAME_TAKEN`, `DEPARTMENT_NAME_INVALID`, `DEPARTMENT_CONTENT_CHOICE_REQUIRED` (409 with counts), `DEPARTMENT_REASSIGN_TARGET_INVALID`, `DEPARTMENT_TOO_MANY_TEACHERS`.

## Audit
Existing actions only: `DEPARTMENT_CREATE/UPDATE/DELETE`, `DEPARTMENT_MEMBER_ADD/REMOVE`, `PERMISSION_CHANGE` (flag), all with `school_id` and `department_id`; `target_type` values `Department` and `DepartmentMembership`. One event per write; delete is one event with `member_count`, capped `member_ids`, `item_count`, `delete_choice`. Member-remove `source` in `admin`, `left_license`, `account_change`.

## Emails
`departments/notifications.py`: `send_added_to_department_email(teacher, department)`, `send_removed_from_department_email(teacher, department)`; queued after commit through `send_email_task`; tests patch `AutoGrader.tasks.send_email_task.delay` (or `departments.notifications.safe_delay`).

## What the Fixer owns
M1-M20, D0 (route enumeration, flush between snapshots, other people's ids, positive control, pinned route list), plants as patches that must turn named tests red (P1 tenancy: `visible_departments` drops the filter; P2 liveness: `is_licensed_teacher_of` ignores the newest-license check; P3 reap: `end_memberships` no-op inside `remove_teacher_from_license`), and T3-T7, T13, T15, T17-T21. The lead owns T1, T2, T8-T12, T14, T16, T22-T27 (implementation-coupled). The matrix fixture is the Fixer's (`departments/tests_matrix_fixture.py`) and must use only the factories and routes above.
