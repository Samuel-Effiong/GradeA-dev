# Verification: Epic A S1b follow-up @ db61be7

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed). **Date:** 2026-09-30.
**Branch:** task/epic-a-s1b @ db61be7 (e1e0374 + gates, on 0b77547 over 65aab2e). It covers v2's S1b notes N1–N3.

Every run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST 600/200) in 0b's slot, from a scratch worktree detached at db61be7.

**Verdict: VERIFIED.**

| Check | Result |
|---|---|
| Delta 65aab2e → db61be7 | tests, evidence and the `failed_auth_cap.py` **docstring** only. No code line changed |
| `audit.tests_failed_auth_cap` (with the adopted `LockThenSprayThroughTheLoginTests`) + v2's S1b probe | **21 OK** |
| v2's B1 (summary `school_id` → None), against **ed's labels only** (without v2's probe) | **KILLED** by `test_a_locked_account_sprayed_from_many_ips_is_bounded_and_school_scoped`. N1 is closed |
| v2's B2 (a DENIED summary recorded as FAILURE), ed's labels only | **KILLED** |
| N2: the docstring | now says DENIED shares the per-target counter (floor, then the target cap, no global cap), crashes sit under the global cap, and summaries keep the outcome, error class and school |
| N3 | stated in EVIDENCE ("Follow-up") |
| ed's gates | 17 OK; 16/16 mutants (committed) |

ed flagged, as out of S1b's scope, that a DENIED with target None always writes. It is raised for the (b) merge-down (H-53's locked unknown address on /auth/verify), where the SM ruled no-account DENIED goes under the per-target and global caps with no floor. v2 checks that in the (b) verification.

Logs: `runs/s1bf_run1.log`, `runs/s1bf_run2_mutants.log`.
