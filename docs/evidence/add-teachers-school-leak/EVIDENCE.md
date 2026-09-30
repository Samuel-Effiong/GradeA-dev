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

## Gates
_pending_
