# H-273 evidence: audit refusal detail

**Final tip:** this commit (code identical to d8b78e86). Built by the previous worker
(steps 1-3b), finished by the SM after the 2026-11-02 takeover.

| Gate | Result |
|---|---|
| Targeted (step 3b tip 2031117c) | 474 tests, **4 failures**: tests_middleware still on 32-char ids. Fixed in 8f92f2b7. |
| SM review findings | (1) the 4 stale tests; (2) step 1 had no tests (added 30, audit/tests_h273_request_detail.py); (3) enveloped/throttled refusals lost their reason_code on the audit row (fixed, red-checked 4 tests). |
| Mutation | 27 mutants (mutants.py). First pass on ff2cba75: 25 killed; M-16 and M-18 survived = test gaps, fixed in d8b78e86, re-run: both killed. **27/27 killed, every restore sha256 OK.** Logs beside this file. |
| makemigrations --check | No changes detected |
| Near run, 7 apps (audit users classrooms billing students assignments AutoGrader) | Expectation committed at 55021b61: Ran 6752, OK, skipped=20. **Result: Ran 6752 tests in 1546.377s, OK (skipped=20). Matches.** Log archive (too large for the repo): ~/Documents/Projects/GAP-a0-runs/h273/near_run_final_55021b61.log, sha256 3753c133a9138d8072b1f213fc2122d06d396eb351b720fbfe2d6560efd68bcd |

Behaviour change to flag for the frontend: X-Request-ID / `reference` values are now dashed
UUIDs (36 chars), not 32-char hex (NOTES §4b). `metadata.route` is the URL pattern on
ADMIN_ACTION rows and the URL name on STATE_CHANGE rows (NOTES §1i).

Remaining before landing: batch build with H-274, full-suite gate on the batch SHA.
