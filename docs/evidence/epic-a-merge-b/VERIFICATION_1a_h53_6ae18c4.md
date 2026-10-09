# Verification: the H-53 part of the Epic A merge-down (b) @ 6ae18c4

**Verifier:** Verification Engineer (1a, grade-automator-plus-c2). **Integrator:** Integration & Release (0b). **Resolution:** Security (ed).
**Merge:** `6ae18c4` = beta `abeda10` into phase2/epic-a `19b2072` (beta into Phase 2 only; nothing goes the other way). **Date:** 2026-09-30.
**Scope:** H-53's behaviour in `users/views.py` `AuthViewSet.verify` after the merge. v2 covers the auth/audit part and the settings hunk.

**Verdict: VERIFIED.** H-53's behaviour is unchanged by the merge. Nothing is required before Gate 10 or staging.

## What I checked
| Check | Result |
|---|---|
| Deliverables | ed's `SHA256SUMS` passes for `files/users/views.py` and `patches/merge-b.patch`. |
| The merge is exactly the declared resolution | I rebuilt it independently: git's automatic merge of `19b2072` and `abeda10` (`merge-tree`), plus 0b's three unions, ed's `users/views.py` and `patch -p1` applying `merge-b.patch`. The result is **identical to `6ae18c4`'s tree** (`diff -r`), so the merge holds nothing undeclared. |
| 0b's unions | `AutoGrader/settings.py`, `classrooms/views.py` and `docs/HARDENING_BACKLOG.md`: every line exists in one parent, and every line either side added is kept. Pure unions. |
| H-53's own code and tests | Byte-identical to beta `abeda10`: `users/throttling.py`, `users/tests_verify_email_budget.py`, `users/tests_throttling.py` and `users/tests_auth_input_validation.py`. |
| `verify()`, beta against the merge | **H-53's control flow is unchanged.** The only additions are 4 audit calls: `sign_in_failed(CODE_MISSING)` before the 400; `sign_in_failed(VERIFY_LOCKED, denied=True)` before the 429 (`Retry-After` and the same wait calculation); inside `refuse()`, after the budget check and `lock_verify_address` and before the 400, with `lock_triggered` in the metadata; and `sign_in_succeeded` after activation. The stored code is still kept on lock (invitations survive), and `clear_verify_failures` still runs on success. The response bodies and statuses are unchanged, so there is no new account-state oracle in the answers. |
| Can the audit calls change H-53's answers? | No. `emit` catches validation errors (unless `strict=True`, which no call site passes) and every storage error, so it never raises into the view. `account_for_email` uses `.filter(email__iexact).first()`, so it never raises on duplicates. `ATOMIC_REQUESTS` is off (`billing/checks.py` enforces that), so DRF's `set_rollback()` on the 400 and 429 doesn't undo the audit rows. |
| Rule 15 runs | Relied on 0b's committed gates at `6ae18c4` (`docs/evidence/epic-a-merge-b/GATES.md`, `2c950aa`): the changed modules (which include `users.tests_verify_email_budget`, `tests_throttling` and `tests_auth_input_validation`) **Ran 209, OK**; the owning app `users` **Ran 686, OK (skipped 4)**. The other `/auth/verify` caller outside `users` (`classrooms.test_school_admin_otp_deadend`) is identical on both parents and falls to Gate 10's full run. |

## My mutants on the merged `verify()` (modules: `users.tests_verify_email_budget`, `users.tests_auth_audit_doors.VerifyEmailDoorTests`)
| Mutant | Result |
|---|---|
| W1: the budget-spending guess no longer locks the address | **killed** (3): `test_a_resent_code_neither_arrives_nor_unlocks`, `test_an_expired_code_spends_the_budget_too`, `test_both_windows_last_the_whole_lock_period` |
| W2: the merge's reshaped `refuse()` drops the budget check (`lock_triggered = False`) | **killed** (7), H-53's and the doors tests together, including `test_a_locked_attempt_is_denied_even_with_the_right_code` |
| W3: an over-budget attempt in a race is served normally (only a stored lock refuses) | **killed** (2): `test_a_simultaneous_burst_gets_no_more_than_the_budget`, `test_guesses_under_way_count_before_they_are_answered` |
| W4: success no longer clears the address's failures | **killed** (1): `test_a_successful_verify_refunds_the_budget` |

My own detached checkout at `6ae18c4`, its own test DB, `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`, and 0b's slot (16:43–16:44). Every restore was sha-checked against the commit's blob.

## Notes (not blocking)
**N1.** Each failed or locked attempt now costs one extra query (`account_for_email`) plus an audit write, which S1b's caps bound. The time taken might differ slightly between known and unknown addresses. That's v2's scope and was already present in Epic A's other doors.
