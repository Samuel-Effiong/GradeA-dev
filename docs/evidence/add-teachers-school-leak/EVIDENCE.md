# Add teachers: no other school's name in the refusal (beta line)

Branch `task/add-teachers-school-name-leak`, cut from beta `abeda10`. This is a beta hotfix candidate for the next bundle (the SM will ask the founder whether it joins bundle 4). The verifier is 1a. No migration.

## The defect
A school admin's **Add teachers** (`POST license-subscriptions/{id}/add_teachers`) with the email of a teacher who already belongs to a **different** school answered, per teacher:

> Teacher '<email>' already belongs to school '<the other school's name>'. Cannot enroll under '<own school>'.

Any school admin could learn which school an arbitrary address belongs to: a **cross-tenant disclosure**. The same text also went to the log, with the teacher's email (`billing/license_service.py`, `_resolve_teacher_for_license`).

## The fix
- The refusal stays. Its text is now **"This teacher already belongs to another school."** (SM wording; the QA proposal's `TEACHER_IN_OTHER_SCHOOL` matches it).
- The log line carries **ids only**: teacher, their school, the licence's school.
- `add_teachers_batch`'s per-teacher "Skipped enrolling <email> in license …: <refusal>" log line no longer carries the email. It used to log the address next to the refusal that named the other school. The first gated run caught it through the test's log assertion. **Not changed here:** two other refusal texts, the business-email check and the individual-subscription one, still quote the address in that logged text. They are pre-existing, in H-23's grandfathered email-log territory.

## Checked for the same pattern (SM)
- **remove_teachers:** its messages name nothing of another tenant ("This teacher isn't an active teacher on this licence.", or the generic fallback).
- **Licence creation** (`validate_admin_user`: "… cannot manage licenses for school {name}"): super-admin only (`IsSuperAdmin` on create), and it names the requested school. Not a tenant disclosure.
- **The per-teacher seat-limit and "does not belong to school {name}" messages:** they name the licence's **own** school, the admin's own.
- **Roster import:** the other-school refusal is already generic ("This account cannot be added to this school…").
- **Invitation and overage emails:** they go to the right school's own people.
- **Related, raised with the SM:**
  - **Fixed here (SM ruling 1):** "Email X already belongs to a {STUDENT/SCHOOL_ADMIN/…} account, not a teacher." told any school admin the role of an arbitrary address. It is now **"This email can't be added as a teacher."**, and its log line carries the user id only: no email, no role. A test checks that no role word ("student", "school admin", "super") reaches the response or the log.
  - **Left for QA (SM ruling 2):** "Teacher X has an active individual subscription…" discloses the billing status of an arbitrary teacher. It is actionable for a legitimate admin, so the founder and QA decide it via the proposal's `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION`, which now notes the disclosure.

## Gates (rule 15 + addendum 2: changed modules incl. the repo-wide guards present on beta, mutation, ONE regression)
Run on **`f7260bd`**, one step at a time at 6G, beside Gate 10 in 0b's one slot.
- **History:** the first gated run (`e22eb1a` + `22b5d75`) stopped at the changed-module step. My two new tests caught the batch log line that still carried the email; fixed in `f7260bd`. No red mutation or regression was recorded.
- **On beta:** `AutoGrader.tests_reason_codes` and audit's route coverage are Phase 2 only, so the beta guard set is the three below.

| Gate | Result |
|---|---|
| Reproduce-first | `abeda10`'s `billing/license_service.py` against `billing.tests.test_add_teachers_other_school_not_disclosed` (`prefix_abeda10_failing.txt`): **3 tests, 2 failures**. The other school's name, and the role, are disclosed. The third test (the teacher is still refused) passes on beta, as it should: the refusal itself was never wrong. |
| Changed modules | the new test module; `billing.tests.test_license_service`, `test_license_teacher_changes_400`, `test_h38_teacher_removal`, `test_h38_part2_removed_teacher_routes`; the repo-wide guards present on beta: `AutoGrader.tests_no_wildcard_invalidation`, `tests_cache_invalidation_coverage`, `tests_migration_rollback_defaults`. **151 OK** (`changed_modules.txt`) |
| 2 Mutation | **5 mutants, 5 killed** (`mutation_log.txt`, `mutation_results.json`): N1 names the other school; N2 the log carries the email; N3 the teacher is enrolled anyway; N4 names the role; N5 the batch log carries the email. |
| 1 Regression (owning app) | `billing`: **1680 OK** (`regression_billing.txt`, trimmed; the full log is in GAP-evidence-logs) |
