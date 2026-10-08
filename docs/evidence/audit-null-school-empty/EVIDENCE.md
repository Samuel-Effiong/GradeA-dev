# H-192 (AUDIT-NULL-SCHOOL (a)): a School Admin with no school is shown an empty activity log

(Row numbers from the Release Engineer, 2026-10-08: H-192 this row, on the next-stage line; H-193 the rule "a SCHOOL_ADMIN must have a school", a beta row; H-194 AUDIT-ANON-FLOOD.)

Branch `task/audit-null-school-empty`, base phase2/epic-a 75d826202249 (next-stage line, local; not pushed). Written by ed (Security Engineer), 2026-10-08. Severity MEDIUM (Senior Manager; low likelihood, cross-tenant privacy).
**Nothing has been run on this branch yet.** Marks: READ = read in the code; NOT RUN = reasoning.

## The fault (READ; NOT RUN)

`SchoolAdminAuditEventListView.get_queryset` (audit/views.py) filtered `school_id = request.user.school_id`. A user's school is nullable (`users/models.py`, SET_NULL), and in Django `filter(school_id=None)` is `IS NULL`: a SCHOOL_ADMIN with no school was shown every audit event
that belongs to no school: individual teachers' events, system events, anonymous ones, **failed sign-ins with the address and the IP**. `IsSchoolAdmin` (classrooms/permissions.py) checks only the role. A school-less admin can exist by a super admin's PATCH (`user_type`, or `school: null` on
an existing admin: no serializer rule forbids it), the Django admin, or a hard delete of a School; not by self-service. This is the only route on beta/main/next-stage with that shape that I found (the others check `if not school` or `and user.school_id`; reading (b) of 2026-10-08).

## The change (READ; NOT RUN), `audit/views.py` only

`get_queryset` returns `AuditEvent.objects.none()` when the admin's `school_id` is None (an empty list, status 200: nothing leaks, the screen still renders), otherwise the same filter as before. No migration. The rule "a SCHOOL_ADMIN must have a school" in the user serializer is a SEPARATE beta row (LOW-MEDIUM, defence in depth).

## Tests (written first, 6a459129; NOT RUN)

`audit/tests_school_admin_without_a_school.py`, on the existing two-school fixture: an admin with no school gets 200 and an empty list, with none of the null-school events' address in the body; a `school_id` parameter does not widen it; an admin with a school still sees only that school (and none of the null-school events); the super admin still sees the null-school events.

## Written expectations, before any run

- **Step 0 (reproduce-first)**: `audit/views.py` as at 75d82620 under the new module: **Ran 4, TWO red**: `test_an_admin_with_no_school_is_shown_an_empty_list` and `test_a_school_id_parameter_does_not_widen_it`. **Green on the old code, by design:** `test_an_admin_with_a_school_still_sees_only_that_school` and `test_the_super_admin_still_sees_the_null_school_events`; each is seen red by a mutant (A3, A4).
- **Step 1**: `makemigrations --check` no changes; the new module (Ran 4, OK), `audit.tests_query_api` and the repo-wide guard modules that exist on the next-stage line: OK, no FAIL or ERROR line.
- **Step 2, mutants (4)**, each fails exactly the tests named: A1 (the None check removed): the empty-list test and the parameter test; A2 (an admin with no school is shown everything): the same two; A3 (every school admin is shown nothing): the still-sees-own-school test; A4 (the super admin loses the null-school events): the super-admin test.
- **Step 3**: the audit app, one serial run: OK.
- Nothing is re-run without the Release Engineer's word.

## Known limits

- This hides the leak for a school-less admin; the rule that stops such an admin from existing is the beta row.
- The dashboards and other school routes were READ (reading (b)): each guards a missing school; not retested here.
- The deployed throttle value and the audit flood (AUDIT-ANON-FLOOD) are another row; **the 60-a-minute bound there is UNVERIFIED in deployment** (NUM_PROXIES), said in that row's evidence too.

## Not shown by any run so far

Everything: nothing has been run.
