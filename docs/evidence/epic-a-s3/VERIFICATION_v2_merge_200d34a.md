# Verification: the S3 merge @ 200d34a

**Verifier:** Verification Engineer 2 (v2). **Resolution author:** ed; the merge was performed by 0b. **Date:** 2026-09-30.
**Commit:** task/epic-a-s3 **200d34a** = phase2/epic-a 5811e15 (the tip on staging: S1, S2 R1, S1b and its follow-up, S5 R3, S6a, and the (b) merge-down) merged into S3 2011641 (v2's VERIFIED-WITH-NOTES at 11a2e16, plus its record and EVIDENCE notes).

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST 600/200) in 0b's slot, from a scratch worktree detached at 200d34a. Rule 15: v2's probes and the merge's static checks. 0b runs the 11 changed-module labels and the billing regression.

**Verdict: VERIFIED.** S3 N1 (the conflicts would drop a side) is resolved with every side kept.

## Static
- `git show --remerge-diff 200d34a`: hand edits in only the three conflicted files.
  - `audit/context.py`: `__slots__ = ("stored_event_ids", "suppressed", "request")`, and `__init__` sets all three. `request_audit_state(request)`, `current_request_actor`, `record_stored_event`, `record_suppressed_event`, `a_stored_event_survives` and `a_surviving_event_names` are all present.
  - `audit/enums.py`: S3's `AUDIT_RETENTION_SWEEP` is kept beside the epic side.
  - `audit/metadata.py`: S1b's multi-line `STATE_CHANGE` entry (with the summary keys) and S3's `AUDIT_RETENTION_SWEEP` entry.
- The auto-merged `audit/middleware.py` has `request_audit_state(request)` (S3), S1's generic event, and S2's `emit_anonymous_refusal` behind S1b's `not state.suppressed` guard.
- AST union check against both parents: no enum member, no `METADATA_ALLOWLIST` action and no metadata key literal from either side is missing.

## Probes (all v2 probes on the merged tree): **24 OK**
| Probe | Result on 200d34a |
|---|---|
| S1 V1 (`tests_vf2_s1_attribution`) | add_teachers and remove_teachers each leave a CREDIT_TRANSACTION naming the SCHOOL_ADMIN (S3); the plain write still gets STATE_CHANGE |
| S1 R1 (`tests_vf_s1_probe`) | the rollback cases hold |
| S2 R1 | register-500 → one ACCOUNT_REGISTER FAILURE SYSTEM SERVER_ERROR; the unnamed route is caught; the non-door crash → STATE_CHANGE SERVER_ERROR |
| S1b | lock-then-spray: 4 DENIED + DENIED summaries at 1 and 10 with the school |
| S3 | P1 GRANT names the admin; P2: 2 EXPIRE rows + 2 CREDIT_TRANSACTIONs naming the admin, balance 0, no email in the logs; P3: no double expiry |
| (b) merge-down | the locked unknown-address spray is bounded by the global cap, with no email stored |
| S5, S6a | the trace join; unknown code → generic fallback; braces render literally |

Log: `runs/s3merge_run1.log`.
