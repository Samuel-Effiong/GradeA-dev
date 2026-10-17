# H-99: a student's placeholder address never attaches an existing account — evidence

Author: ed (Security). Branch `task/h99-placeholder-email`, on `task/beta-batch-6` 76cc9b97
(bundle 6; SM order: before H-91). HIGH. Verifier: v2.

## How it was found

The resolve-intent slice's regression on Epic A (84aeb2f1, 2026-10-02) had one failure in 2438
tests: `billing.tests.test_h38_part2_removed_teacher_routes.RemovedTeacherRosterNameMatchTests`
`.test_import_into_an_individual_course_creates_a_new_student`. It looked like the H-38 name
match seeing a course the teacher could no longer reach. It was not. The trace ran through the
create-a-new-student branch (`roster_import._import_row_without_email` → `serializer.save()` →
the attach gate), which is only reached after the name match returned nothing.

## The defect

`DirectAddStudentSerializer.create` (also used by a no-email bulk row) gave a student with no
email the address `first.last<N>@student.local`, `N = secrets.randbelow(10000)`, and then
looked that address up. A new student who drew a suffix already in use by a same-named account
was given the EXISTING account:

| The existing account is tied to | What happened |
|---|---|
| a different school | refused by `check_existing_account_may_join`; the teacher saw a generic failure (what the test hit). Failed closed. |
| the same school | attached: the second course got the first pupil's account, no new student. |
| no school (individual-track teachers) | attached, across two unrelated teachers. |

Odds: k in 10,000 per add, k = the number of existing placeholder accounts with that first and
last name. On beta and on Epic A.

Diagnosis probe (run on the Epic A tip 7522707c; logs
`diagnosis_probe_pinned_suffix_7522707c.txt`, `diagnosis_class_untouched_7522707c.txt`): with
`secrets.randbelow` pinned to one value both tests of that class fail, each with the red run's
server line "Refused to enroll student … account is associated with school(s) …"; the untouched
class passes.

Not the cause, checked by reading (SM's four questions): no unordered pick decides it; the
reachability rule compares no timestamps; nothing on the path reads a cache; the tests share no
fixture rows.

## The fix (SM rulings, 2026-10-02)

| Commit | What |
|---|---|
| c4e008ff | (a) `new_placeholder_email`: `first.last.<16 hex>@student.local` (64 random bits; name parts cut to 20 characters). `_create_placeholder_student` only creates: an address already in use is skipped, never attached, and an insert the unique `email` column refuses is retried with a new address (5 attempts). `create` looks up an account only for an address the CALLER supplied. (b) `is_placeholder_email`; a caller-supplied address in the placeholder domain is refused with H-71's "This email can't be added as a student." by `AddStudentToCourseSerializer`, by `DirectAddStudentSerializer` and by `enroll_student_by_email` (so: single add, direct add, a bulk row), in any letter case and with surrounding spaces. One ids-only log line. |
| c2a98fd1 | `classrooms/tests_h99_placeholder_email.py`: 16 test methods. |
| 7b127ba3, 80267b89 | `mutate.py` (9 mutants), the diagnosis logs, `grep_callers.txt`. |
| dbc65451 | 0b's base update onto 76cc9b97 (clean; no shared file). |

### Behaviour changes, for the package

- A student added with no email gets a longer address. Nothing parses the old form: every
  other use is "ends with @student.local".
- **An address in @student.local can no longer be supplied on add.** Before, typing a
  roster-only pupil's placeholder address attached that pupil. The API returns `email: null`
  for these accounts (and the flag `is_system_generated_email`), so no client can know such an
  address, and there is no roster export. Frontend note: "the API no longer accepts an
  @student.local address on add; it never showed one." (v2's N1; the SM confirms with QA that
  no client sends one. Limit: only the backend was read.)
- A teacher re-adding their own roster-only student to another course is unchanged: the bulk
  import's name match does it; the direct-add form always makes a new account.
- No new user-facing wording. If all five generated addresses were taken (several 64-bit
  collisions in a row) the add fails with the views' existing fallback sentence.
- Rollback: revert the merge; no migration. Accounts created meanwhile keep their longer
  addresses, which the old code handles (it only checks the domain).

### The concurrent double-add (SM's question)

Two requests adding a same-named student at once each generate their own address, so they end
as two students. If both drew the same token, the first insert wins and the unique `email`
column refuses the second; that request catches the error in a savepoint and takes a new
address. Tested by making the lookup blind, so the insert is what meets the taken address
(`test_two_requests_picking_one_token_end_as_two_students`); not tested with real threads by
me. v2's probe C1 runs two real threads racing for one generated address.

### Examined, not changed (for the founder; SM ruling)

- **The free-join rule**: an account tied to no school may join any course by its (real)
  address. That is `check_existing_account_may_join`'s documented rule and is untouched.
- **`schools_associated_with`** derives a student's schools from the CURRENT school of each
  course's teacher, so a pupil counts as associated with a school their teacher later joined
  (seen in the probe's School B case, where the refusal listed two schools).
- Backlog rows raised on the way: H-100 (two unordered `.first()` calls), H-102 (the direct-add
  view answers a save-time refusal with a 500).
- v2, outside H-99 and older: the API's masking of placeholder addresses uses a case-sensitive
  `endswith("@student.local")`, so a legacy address stored with capitals would be shown, not
  masked. With the SM.
- v2's N3 (outside H-99): `users/serializers.py` skips the domain rules for any address ending
  @student.local whatever the user type; with the SM.

### Production detection (the founder's action)

`~/Documents/Projects/GAP-detect-shared-placeholder-students.sql`, read-only, ids and counts
only: placeholder accounts enrolled in courses of more than one teacher. Approved by the SM
and handed to the founder. It cannot show two same-named pupils of ONE teacher merged into one
record.

### Merge-down note (Epic A)

- On the epic a refused placeholder bulk row will carry `ROW_STAFF_EMAIL` (S7d maps
  `NOT_A_STUDENT_MESSAGE` to it) (v2's N2).
- `classrooms/serializers.py` and the roster import differ on the epic (S7d's coded rows); the
  hunks here are in `create`, the two `validate_email` methods and `enrollment.py`.
- H-91 (after this in bundle 6) adds a repo-wide PII-in-logs guard; this change's one log line
  passes a course id only.

## Which tests reach the changed entry points

`grep_callers.txt`. 21 caller modules outside the new one: classrooms 17, billing 2 (the H-38
modules), users 2, dashboard 1; none under `students/`. No existing test passes a
@student.local address into an add route, a form or `enroll_student_by_email` (the two hits
only read a generated address back).

## Runs

All runs: rules 12, 13 and 16 (the `idle:sleep:handle-lid-switch` prefix),
`--settings=settings_worktree`, slots granted by 0b. Mutation run (rule 17):
`PYTHONDONTWRITEBYTECODE=1`, the `__pycache__` of each mutated module's directory deleted before
each mutant and after each restore, own database (`test_h99_placeholder_email_mut`, dropped
afterwards), every anchor asserted unique and every mutant parsed.

### Gate (a), 2026-10-02 16:44–16:49 WAT, at 80267b89 (6G): green on the first pass

| Step | Result | Log |
|---|---|---|
| 0. Reproduce-first: the new module on 76cc9b97's three production files | FAILED (errors=1): `ImportError: cannot import name 'PLACEHOLDER_EMAIL_ATTEMPTS'` | `prefix_76cc9b97_failing.txt` |
| 1. The new module + the 21 caller modules + the batch's guards (the nine beta-line guards, `tests_beat_locks`, `tests_management_commands_are_commands`, `tests_redis_hygiene`, H-73's raw-client guard, H-56's migration defaults, H-80's log guard) | 602 tests OK | `modules_and_guards.txt` (last 200 lines; full log sha256 prefix `317374e7b53b3430`) |
| 2. Mutants (9) | 9 of 9 killed, no survivors | `mutation_log.txt`, `mutation_results.json` |

**The reproduce-first step is the weak form**: the test module imports names the fix adds, so
on the pre-fix code it fails at import, not test by test. **The strong reproduction is the
pinned-suffix probe** (above: the pre-fix code with the suffix pinned fails both tests of the
H-38 roster class with the red run's trace) **and mutant P1** (a taken generated address
attaches the account), which is the pre-fix behaviour with the module importable: three tests
fail on it.

| Mutant | Killed by |
|---|---|
| P1 a taken generated address attaches the account | `test_a_bulk_row_makes_a_new_student`; `test_direct_add_makes_a_new_student` (+2 more) |
| P3 a refused insert is not retried | `test_two_requests_picking_one_token_end_as_two_students` |
| P4 only one address is tried | `test_a_bulk_row_makes_a_new_student`; `test_direct_add_makes_a_new_student` (+3 more) |
| P5 the suffix is four digits again | `test_it_is_in_the_placeholder_domain_with_a_long_random_token`; `test_the_name_parts_are_folded_and_kept_short` |
| P6 the domain match is case sensitive and untrimmed | `test_is_placeholder_email_ignores_case_and_surrounding_spaces` |
| P7 the shared service accepts a placeholder address | `test_a_bulk_row_refuses_it`; `test_the_shared_service_refuses_it_and_logs_ids_only` |
| P8 the single add form accepts a placeholder address | `test_each_form_refuses_it_before_any_lookup_or_save` |
| P9 the direct add form accepts a placeholder address | `test_direct_add_refuses_it`; `test_each_form_refuses_it_before_any_lookup_or_save` |
| P10 the name parts are not cut | `test_the_name_parts_are_folded_and_kept_short` |

### Gate (b), 2026-10-02 16:50–16:59 WAT, at 4ed59060 (= 80267b89 plus gate (a)'s logs)

12G, flock, timeout 3600, serial, `--verbosity 2`, `RACE_COST_*` / `AUDIT_BENCH*` /
`ENABLE_GRADING_BENCHMARK` unset:

| Run | Result | Log |
|---|---|---|
| `manage.py test classrooms users dashboard AutoGrader` (the SM's scope) | **Ran 1794 tests in 499.3s, OK (skipped=6)**. Per app: users 654, AutoGrader 469, classrooms 401, dashboard 270. | `regression_classrooms_users_dashboard_autograder.txt` (last 200 lines; full log sha256 prefix `d1c5711ade21d3ec`, kept in `~/Documents/Projects/GAP-evidence-logs/`) |

Billing whole is not in this run (SM): its two H-38 modules ran in gate (a), and the bundle's
strict full run covers the rest.

## Not verified here

- No real concurrent requests; no run against production data.
- Only the backend was read for "does a client ever send a placeholder address".
- Not mutated: the free-address check before the insert. It is redundant with the unique
  column in every case these tests can build; it is kept for legacy rows stored with capitals,
  which the case-insensitive lookup sees and the case-sensitive column would not.
- The author does not verify their own work: a verifier checks this.
