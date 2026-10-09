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

## Taken over and re-checked by ed, 2026-10-09 19:4x WAT (nothing run yet)
- **Base update:** merged phase2/epic-a d2ad0405 into the branch (clean; merge-tree wrote a tree with no conflict): merge commit 2f228b0c. The branch's own files are unchanged by it; `audit/views.py` has no difference between 75d82620 and d2ad0405 (only audit/enums.py and audit/tests_history.py changed on the line). Hooks (flake8, black, isort, mypy, bandit, detect-secrets) pass on the four branch files at 2f228b0c.
- **Written reasons (rule 22), step 0 on the old `audit/views.py`:** `test_an_admin_with_no_school_is_shown_an_empty_list` fails with `!= []` (rows_of is not empty: three null-school events; the e-mail text in the body is the second assertion, never reached); `test_a_school_id_parameter_does_not_widen_it` fails with `!= []`. T3 and T4 are green on the old code, by design (each seen red by A3 and A4). Both reds assert on a deciding value: the control T3 (and `tests_query_api`) prove the route returns data for an admin with a school, so an empty list cannot come from a broken route.
- **Old behaviour grep:** the other users of `school-admin-audit-events` are audit/tests_query_api.py, audit/tests_state_change.py (admin_a) and students/tests_h38_n3_refusal_audit.py (admins made with a school, `make_user(..., SCHOOL_ADMIN, school)`); none uses an admin without a school, so none pins the old behaviour.
- **Mutant sets re-traced** (each against all four new tests and `audit.tests_query_api`): A1 (`if False:`) falls through to `filter(school_id=None)` = IS NULL: T1, T2 fail; A2 (`.all()`): T1, T2; A3 (`if True:`): every admin gets none, T3 fails and the `tests_query_api` tests of an admin with a school fail too (named as extras after the run); T1/T2 stay green; A4 (super-admin view filters out null school): T4 fails. `mutate.py --check`: 4 mutants, anchors unique, all parse.
- **Gate list for the slot:** step 0 (T1-T4 on the old views.py), makemigrations --check, `audit.tests_school_admin_without_a_school`, `audit.tests_query_api`, `audit.tests_state_change`, `students.tests_h38_n3_refusal_audit`, the repo-wide guard modules; mutants A1-A4; then the audit app plus users as the regression.
- **Part (b) of AUDIT-NULL-SCHOOL:** it was the read-only sweep of the other school routes (each checks `if not school` or `and user.school_id`), finding none with this shape; nothing open there. What stays open is the rule "a SCHOOL_ADMIN must have a school" in the user serializer: row H-193 (LOW-MEDIUM, a beta row, not started).

## Verifier 1's pre-read (2026-10-09), applied
No fault found. Expected set of **A3** stated before the run, with its extras: required T3; expected extras (real, they read an admin WITH a school): `audit.tests_query_api` `test_school_admin_sees_only_own_school` and `audit.tests_state_change` `test_school_admin_sees_attempts_on_its_own_accounts`; possible further extras among the h38 n3 tests that read the school admin's list. The kill rule (expected is a subset of failing) holds either way; the extras are named after the run.

## RESULTS (step 1 on 3dd1aebe, 2026-10-09 19:41:30-19:44:37 WAT, Release Engineer's grant 19:42, one systemd-inhibit; raw files beside this one)
- **Step 0 (base audit/views.py under the new module):** Ran 4, FAILED (failures=2): exactly the two written tests, each with its written reason `!= []` (runner step (r): "reason ok" twice). T3 and T4 green, as written. `prefix_base_production_failing.txt`.
- **makemigrations --check:** exit 0, "No changes detected".
- **New + related modules + repo-wide guards (`modules_and_guards.txt`):** Ran 479 in 112.8 s, OK; load 2.43 at start and end.
- **Mutants (`mutation_log.txt`, `mutation_results.json`, `mutant_logs/`): 4 of 4 KILLED, no survivor, none broken, "source clean after mutants".** A1 failed 2 (T1, T2), A2 failed 2 (T1, T2), A4 failed 1 (T4): as written. **A3 failed 4:** T3 as written, plus three extras I had named only partly: `tests_query_api` `SchoolAdminAuditEventPermissionTests.test_school_admin_sees_only_own_school` (named), and `SchoolAdminCrossTenantAdversarialTests.test_queryset_level_ignores_other_schools_id_filter` and `...test_request_response_level_school_id_param_is_ignored` (the second is the "request/response-level cross-tenant test" Verifier 1 predicted; the first I had not named). **One miss, disclosed:** I had listed `audit.tests_state_change` `test_school_admin_sees_attempts_on_its_own_accounts` as an expected extra; it did NOT fail, because the mutant runner's module list is only `tests_school_admin_without_a_school` and `tests_query_api` (that module was in step 1's related list, which is green on the real code). Not a survivor: A3 is killed by T3.
- Regression of the audit app and users: a later grant (step 3).
