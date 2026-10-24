# Verification: add_teachers cross-tenant disclosure fix @ 152a8da

**Verifier:** 1a. **Author:** ed. **Date:** 2026-09-30.
**Branch:** `task/add-teachers-school-name-leak` @ **152a8da**. The code is at `f7260bd`, off beta `abeda10`, and the only code file is `billing/license_service.py`. The evidence is `docs/evidence/add-teachers-school-leak/`.

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`) in 0b's slot, from a detached scratch checkout at 152a8da with its own test DB (`test_vf_atl`). Under rule 15, ed's billing regression (1680 OK) is cited, not repeated.

**Verdict: VERIFIED-WITH-NOTES.** The fix does what it says. None of the notes block it: N1 is a sibling path for the SM to rule on, and N2 and N3 are low or by design.

## Static
- `_get_or_invite_teacher` changes two refusals.
  - **Another school:** the refusal is "This teacher already belongs to another school." The log line is `Teacher <id> belongs to school <id>, not <id>`: ids only, with no name or email.
  - **Non-teacher:** the refusal is "This email can't be added as a teacher." The log line is `User <id> is not a teacher`, with no role and no email. The log now fires on the `raise_on_conflict` path too; before, that path had no log.
- `_invite_and_enroll_one_teacher`'s skip log line no longer carries the email.
- **Order unchanged:** business email → role → individual subscription → school. The refusal still happens, and nothing is attached, enrolled or emailed.
- **Deliberately unchanged (ed, SM ruling 2):**
  - The individual-subscription refusal still quotes the address. That is QA's `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION` decision.
  - The business-email refusal and the "Unexpected error" log still quote the address (H-23 territory).
  - None of these names another school or a role.
- **Hooks:** `pre-commit run --from-ref abeda10 --to-ref 152a8da` passes, and so does each of the 5 commits on its own.

## Evidence
My probes are in `add_teachers_leak_probe_test_vf_atl_probe.py`. They go through the real `license-subscription-add-teachers` / `-remove-teachers` routes, as school A's admin, against school B's accounts.

The log oracle is stronger than ed's. `Logger.isEnabledFor` is forced to True and `Logger.handle` is captured, so every record from every logger at every level is checked, including loggers with `propagate=False`. It checks for no B name, no B email, and no role word (student, school admin, super admin).

| Check | Result |
|---|---|
| **Baseline** @ 152a8da: my 5 probes + ed's 3 tests | **8 OK** |
| **Reproduce-first:** abeda10's `license_service.py` swapped in | **5 FAIL**: P1, P2, P3 and ed's two leak tests. The live leak, through the real route: `Teacher '<b teacher>' already belongs to school 'Zanzibar Vfprobe Hidden Academy'. Cannot enroll under 'Alpha Own School'.`, `… already belongs to a STUDENT account …` and `… a SUPER_ADMIN account …` |
| P1: B's teacher, as sent and case/space-varied (`"  TB.Hidden@Zanzibar-VF.edu "`) | Generic text. No B name or role in the response or in any log. The log carries the teacher's id. B's teacher keeps school B. No allocation. No email sent |
| P2: B's student, B's school admin, a super admin | One identical text for all three, so the roles can't be told apart. No role word anywhere. The accounts are unchanged. No email sent |
| P3: the legitimate admin can still act (mixed batch: B teacher, B student, a new own-school address) | `successful 1, failed 2`. Each refusal is keyed by the `teacher_email` the admin sent, so the admin knows which address to fix. The new teacher is created in school A with an active allocation |
| P4: `remove_teachers` with B's teacher, student and admin ids, plus a random uuid | `0/4`. The messages are "This teacher isn't an active teacher on this licence." ×3 and the fallback ×1. No B name, email or role in the response or logs. B's teacher is untouched |
| P5: B's admin against A's licence | 403/404. No account is created |
| **Mutants (mine):** `add_teachers_leak_mutants_vf_atl_run.py`, sha-checked restore | **3/3 KILLED** |
| M1: the log line names B's school (`user.school.name`) | Killed by P1, P3 and ed's `…does_not_name_the_other_school` |
| M2: the non-teacher log line names the role | Killed by P2, P3 and ed's `…without_naming_its_role` |
| M3: a non-teacher is not refused at the role check | Killed by P2, P3 and ed's role test (error) |

These are separate from ed's N1–N5.

## Other paths checked for the same pattern
- **remove_teachers:** clean (P4).
- **Licence creation / `validate_admin_user`:** super-admin only.
- **Seat-limit and "does not belong to school {name}" texts:** these name the licence's own school.
- **Roster import cross-school refusal:** already generic (`CROSS_SCHOOL_REJECTION_MESSAGE`), with ids-only logs.
- ed's pattern check agrees with all of the above.

## Notes
- **N1 (for the SM: the same role disclosure on a teacher-facing path; not in this fix).** Adding a student to a course names the role of an arbitrary existing address:
  - `classrooms/serializers.py:497-501` (`AddStudentToCourseSerializer`): "…cannot be added as a {school admin / super admin}";
  - `classrooms/services/enrollment.py:133-136` (`check_existing_account_may_join`, used by single add, bulk import and direct add): "This email belongs to a {role} account…";
  - "This email belongs to a teacher account…" in both serializers.
  These paths are for a teacher, not a school admin, but the disclosure is the same kind as SM ruling 1: the role of any address on the platform. If the ruling should extend to them, it needs a separate ticket.
- **N2 (low: an id oracle).** `remove_teachers` answers "isn't an active teacher on this licence" for any existing account id, of any tenant, and the generic fallback for an unknown id. That tells whether a UUID is an account. UUIDs can't be enumerated, and nothing else leaks. Pre-existing.
- **N3 (by design, recorded).** The generic texts still tell an admin that the address is a teacher at some other school, or is a non-teacher account. The alternative is an invitation to an unknown address. Adding by email inherently reveals whether an account exists; the SM's wording accepts this.

## Bundle 4 fold-in (F6 option b)
- **Ready.** `abeda10` is an ancestor of bundle 4's `d28d4de`.
- `git merge-tree --write-tree d28d4de 152a8da` is **clean**. Bundle 4 rewrites much of `license_service.py` (+611/−259) but does not touch these hunks.
- On the merged tree, all four fixed lines are present, and `user.school.name` appears 0 times.
- Bundle 4 at d28d4de **still carries the leak**: lines 768, 1162 and 1187 are the old texts.
- If the founder picks (b), it needs one strict full re-run and my Gate 1 refresh on the merged tip, as 0b said.

Logs: `runs/add_teachers_leak_summary.log` and `runs/add_teachers_leak_{baseline,prefix,mutants}.full.log`. The addresses in the logs are test fixtures.
