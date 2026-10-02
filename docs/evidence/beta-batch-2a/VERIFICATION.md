# Verification: beta-batch-2a, Gate 1 (batch level) @ ddfca77

**Verifier:** Verification Engineer (1a, grade-automator-plus-09). **Integrator:** Integration & Release (0b).
**Base:** beta 463e222. **Tested tip:** 3e34962 (ddfca77 adds only `docs/evidence/beta-batch-2a/FULL_SUITE.md`). **Date:** 2026-09-30.

**Verdict: VERIFIED.** The batch is exactly the verified items, merged cleanly, with nothing added in integration. Nothing is required before the push; the founder's confirmation of that specific push is still needed.

## What I checked (git and records; no test runs of my own)
| Check | Result |
|---|---|
| Ancestry | beta 463e222 and the tested tip 3e34962 are both ancestors of ddfca77. `3e34962..ddfca77` changes only `FULL_SUITE.md`. |
| Evil merges | For all 9 first-parent merges in `beta..ddfca77`, `git merge-tree --write-tree <p1> <p2>` equals the merge's own tree. **No merge adds or drops content.** |
| Direct (non-merge) commits | bda5927, 7a6a5d3 and 0af9b5b change only `docs/evidence/beta-batch-2/DEPLOYED_CHECK.md`. |
| Each item's merged side | Every commit after the verified SHA, up to the merged record commit, is docs only. The verified SHAs, all named in their records:<br>• AUTHZ-L2 at d7054a2 (record 048a3c9)<br>• H-3 token_epoch at 825ab86 (41d85e7)<br>• H-25 at 233ebe7, with the last code commit c5d1a6e (280352c)<br>• retire (A) at 44e2893 (bb7d226)<br>• email wording at f0e259f, with the last code commit 6b120f4 (8ee8f2e)<br>• H-25 fixtures at 877c900 (6dbf137)<br>• backlog and deployed-check draft a4f7924: docs only |
| Records | Each item's VERIFICATION.md is in the tree with its final verdict: VERIFIED, or VERIFIED-WITH-NOTES. retire (A)'s record keeps its two earlier REJECTED rounds above the final VERIFIED, as intended. The H-25 fixtures record matches my copy byte for byte. |
| Scope | 27 non-docs files in `beta..3e34962`. **Every one belongs to a verified item's own delta**; none is unattributed, and none from an item is missing. The files two items share (`users/services.py`: retire (A) + wording; `users/views.py`: L2 + retire (A) + wording) were checked together at the item stage: trial merges were clean and the combined tests gave 35 OK (the wording record). |
| Migrations | None in `beta..3e34962`. 0b's `makemigrations --check` is clean, and whole-repo mypy passed (FULL_SUITE.md). |
| Gate 10 | Two strict full runs at 3e34962 inside the flock, with no other runs on the machine: **Ran 4970, OK (skipped=28)** both times, 0 blocked outbound, no reruns. The run was at `RACE_COST_ENROLLMENTS=600 RACE_COST_ROWS=200` per my N1 on 877c900. The default 6000/2000 sizes passed in my own run at 877c900 (Ran 17, OK). The hotfix merged since (463e222) touches only billing and error mapping, not the cache paths those tests measure. CI on the pushed tip runs the defaults. |
| Rollback | Target beta 463e222. There are no migrations in range, so a code-only rollback is safe (rule 11). |

## Notes (not blocking)
**N1 (deploy time).** 2a ships `backfill_pending_student_invites` and `remediate_student123_passwords`. Running either against production is a production action: it needs founder approval, and the founder runs it via Railway. FULL_SUITE.md already says so; repeated here so it isn't missed at push time.

**N2 (forward, for H-53).** H-53 (7997dea) is based on beta e7e4bdf and changes `users/throttling.py` and `AuthViewSet.verify`/`otp` in `users/views.py`. 2a changes both files: L2's OTP budget, retire (A) and the wording all touch the `/auth/otp` area. When H-53 is rebased onto beta after 2a lands, I'll re-verify the rebased delta with `range-diff` and patch-id, and re-run its tests on the combined tree.

## Gate 1 refresh @ 755aa27 (for the beta push). Verification Engineer 1a, 2026-09-30
**Verdict for the tip 755aa27: VERIFIED.** Nothing is required. The push still needs the founder's confirmation of that specific push.

**What changed since my Gate 1 at ddfca77 (and the docs-only check at 0d8b095):**

| Check | Result |
|---|---|
| Ancestry | 0d8b095 is an ancestor of 755aa27. First-parent commits: 9dbb96e, d510786, 605510a, 3847540, 755aa27. |
| Evil merges | For all 3 merges (9dbb96e → 4ed4ee5, 605510a → fa2d351, 3847540 → 644588e), `git merge-tree --write-tree <p1> <p2>` equals the merge's own tree. |
| Code | The only non-docs files in 0d8b095..755aa27 are `users/views.py` and `AutoGrader/tests_cache_commit_race_cost.py`. At 755aa27 they are **identical** to the verified 4ed4ee5 (auth docs, VERIFIED) and fa2d351 (cost cap, VERIFIED-WITH-NOTES). |
| After 605510a | **Docs only:** FULL_SUITE.md, DEPLOYED_CHECK.md and the cost cap's VERIFICATION record. The confirmation run's tree (605510a) therefore equals the push tip's code. |
| Records | `docs/evidence/auth-otp-verify-docs/VERIFICATION.md` (including the D1-closed addendum) and `docs/evidence/cache_commit_race/VERIFICATION_h25_cost_cap_2a.md` match my copies **byte for byte**. |
| Migrations | None in beta 463e222..755aa27. |
| Confirmation run (0b, FULL_SUITE.md) | At 605510a, **no `RACE_COST_*` env** (this exercises the code defaults), under a 12G cap: **Ran 4973, OK (skipped=28)**, 0 blocked outbound, 562 s. That is 4970 plus the cap's 3 new tests (2 budget, 1 default pin), as expected. |

**Notes (not blocking):**
- **R1.** Whole-repo mypy was last recorded at d510786, before the cap merge. fa2d351 is test-only, and its author reports that the hooks passed. If mypy is a push-gate item, one run at 755aa27 would close it.
- **R2.** DEPLOYED_CHECK's "staging check reduced to web-only" is recorded as a **founder decision**. I haven't verified it and can't; it isn't a code change. The H-25 race replay is covered by `AutoGrader.tests_cache_commit_race`, which passed in the confirmation run.
- **R3 (carried from my Gate 1).** Running `backfill_pending_student_invites` or `remediate_student123_passwords` against production is a production action and needs founder approval. H-53 is **not** in 2a.
