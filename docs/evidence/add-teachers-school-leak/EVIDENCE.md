# Add teachers: no other school's name in the refusal (beta line)

Branch `task/add-teachers-school-name-leak`, cut from beta `abeda10`. This is a beta hotfix candidate for the next bundle (the SM will ask the founder whether it joins bundle 4). The verifier is 1a. No migration.

## The defect
A school admin's **Add teachers** (`POST license-subscriptions/{id}/add_teachers`) with the email of a teacher who already belongs to a **different** school answered, per teacher:

> Teacher '<email>' already belongs to school '<the other school's name>'. Cannot enroll under '<own school>'.

Any school admin could learn which school an arbitrary address belongs to: a **cross-tenant disclosure**. The same text also went to the log, with the teacher's email (`billing/license_service.py`, `_resolve_teacher_for_license`).

## The fix
- The refusal stays. Its text is now **"This teacher already belongs to another school."** (SM wording; the QA proposal's `TEACHER_IN_OTHER_SCHOOL` matches it).
- The log line carries **ids only**: teacher, their school, the licence's school.

## Checked for the same pattern (SM)
- **remove_teachers:** its messages name nothing of another tenant ("This teacher isn't an active teacher on this licence.", or the generic fallback).
- **Licence creation** (`validate_admin_user`: "… cannot manage licenses for school {name}"): super-admin only (`IsSuperAdmin` on create), and it names the requested school. Not a tenant disclosure.
- **The per-teacher seat-limit and "does not belong to school {name}" messages:** they name the licence's **own** school, the admin's own.
- **Roster import:** the other-school refusal is already generic ("This account cannot be added to this school…").
- **Invitation and overage emails:** they go to the right school's own people.
- **Related, not fixed here (raised with the SM):** two other add_teachers per-teacher errors are weaker cross-tenant disclosures about an arbitrary address:
  - "already belongs to a {role} account, not a teacher" reveals the account's role;
  - "has an active individual subscription" reveals its billing status.

## Gates
_pending_
