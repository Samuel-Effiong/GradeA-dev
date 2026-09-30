# Verification: Epic A S3 @ 11a2e16

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed). **Date:** 2026-09-30.
**Branch:** task/epic-a-s3 @ 11a2e16 (cut from d7f2737, merged with 75bf91a = S6a; the test-only fix 6e28c43 is included). Plan 08 §4 (G5/G6/G7, D4/D6).

Every run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST 600/200) in 0b's slot, from a scratch worktree detached at 11a2e16. Rule 15: v2's probes, mutants and the changed modules. ed's billing regression (1670 OK) is cited, not repeated.

**Verdict: VERIFIED-WITH-NOTES.** N1 is a merge requirement for 0b.

## Evidence
| Check | Result |
|---|---|
| v2 probe (`tests_vf2_s3_probe.py`) + `audit.tests_background_attribution`, `audit.tests_license_admin_attribution`, `billing.tests.test_credit_transaction_audit`, the S1 probes, `audit.tests_state_change` | **66 OK** |
| P1: school admin `add_teachers` (real JWT) | exactly one event naming the admin: `CREDIT_TRANSACTION` GRANT, target = the teacher. No STATE_CHANGE (the SM pin) |
| P2: `remove_teachers` with 2 live buckets (licence 20000 + monthly 777/77 used) | **2** EXPIRE ledger rows and **2** CREDIT_TRANSACTIONs (700 and 20000), each actor = admin, target = teacher. Balance afterwards **0**. The teacher's email appears in **no** log line |
| P3: re-expiring the same buckets afterwards (as a racing Beat cleanup would) | EXPIRE rows 2 → **2**; the `is_processed` re-check under the lock holds |
| The S1 V1 probe on this tree | add_teachers and remove_teachers each leave a CREDIT_TRANSACTION naming the SCHOOL_ADMIN; the plain-write control is still STATE_CHANGE |
| v2 mutants (`vf_s3_mutants.py`) | **2/2 KILLED**: an anonymous request's user taken as the actor (should be SYSTEM); the clawback skipping the bucket's `expires_at` update |
| ed's gates | reproduce-first 5F/8E on 75bf91a; 11/11 mutants; billing 1670 OK (ab98771); mypy passed; no migration (committed) |

**Checked and sound:**
- `current_request_actor()` returns only an authenticated request user, otherwise None (SYSTEM).
- The ledger event targets the wallet owner, is scoped to the owner's school, and carries `ledger_id` (allow-listed).
- The retention sweeps self-record, zero-count runs included (ed's tests).
- `expire_bucket(reference=…)` keeps the automatic text as its default.

## Notes
- **N1 (merge, REQUIRED).** S3 does not contain S2 or S1b, which are now in phase2/epic-a 8de4376. Merging will conflict in `audit/context.py`: `RequestAuditState.__slots__` has `suppressed` (S1b) and `request` (S3), and `request_audit_state(request)`. It will also conflict in `audit/middleware.py`, where S2's `emit_anonymous_refusal` and S1b's `not state.suppressed` guard meet S3's `request_audit_state(request)`. The resolution must keep all three. After the merge, run the S1/S1b/S2/S3 probes in `GAP-v2-handover/` plus `audit.tests_failed_auth_cap` and `audit.tests_route_coverage`, and check `git show --remerge-diff`.
- **N2.** Per plan §4, user-initiated work that runs in Celery (e.g. a grading run's credit CONSUME) is now actor SYSTEM, with the teacher as target. The initiator is still recoverable through the trace id and the request's GRADING_REQUESTED event, but it is no longer on the CREDIT_TRANSACTION row itself. State this in EVIDENCE so an auditor knows where to look.
- **N3.** A teacher is no longer the *actor* of grants or expiries made by an admin or by the system; they are the *target*. Any consumer that lists "my events" by `actor_id` must also match `target_id` for credit transactions.

Logs: `runs/s3_run1_probes_changed.log`, `runs/s3_run2_mutants.log`.
