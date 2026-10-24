# Verification: beta-batch-3 (with batch-2b), Gate 1 (batch level) @ ecbbf82

**Verifier:** Verification Engineer (1a, grade-automator-plus-c2). **Integrator:** Integration & Release (0b).
**Base:** beta 755aa27 (pushed). **Tip:** ecbbf82. **Code tip:** 0c95d4e; everything after it is docs. **Date:** 2026-09-30.

**Verdict: VERIFIED.** Bundle 3 (with batch-2b folded in, per the SM) is exactly the verified items, merged cleanly. Nothing is required before the push. The founder still has to confirm that specific push.

## What I checked (git and records)
| Check | Result |
|---|---|
| Ancestry | beta 755aa27, then the first draft tip f9ad0dc, are ancestors of ecbbf82. |
| Evil merges | For all **13** first-parent merges in `755aa27..ecbbf82`, `git merge-tree --write-tree <p1> <p2>` equals the merge's own tree. The merges are 4264679, 55b969f, e73c3ba, e679031, e25d97c, 5621bc3, ed63df2, f9ad0dc, b8ffc23, cacabc4, cd25b96, 6212ce9 and 0c95d4e. **No merge adds or drops content.** The direct commits are docs only: 60a8d0b, 28c4b03 and ecbbf82. |
| Each item is its verified SHA plus docs only | • H-43: 3af18a1 → 7b2f816<br>• student tiles: f2510cc → 0da2166<br>• H-58/59: 39fee13<br>• H-53: d883ce5 → c45950e<br>• H-56: 1f52219 → e4f3932, then **the fix 2247007** → 6eca384<br>• **batch-2b: 24d5ec0** → 534c36e<br>• **allow-list: f9f3d94** (0c95d4e's tree is identical to f9f3d94's)<br>Each has **0** non-docs changes after its verified SHA. The backlog rows 8205361 and 926a43b are docs only. |
| Records | My VERIFICATION.md files are in the tree **byte for byte**, re-verification sections included: h43-otp-text, student-tiles-partition, license-seat-counting, h53-verify-lock, h56-db-defaults (including the correction and the fix), and batch-2b (`docs/evidence/batch-2b-candidate-per-module-8b1c0cf/VERIFICATION.md`). |
| Code attribution | Every non-docs file in `755aa27..0c95d4e` belongs to a verified item's own delta. Files changed on both sides, and how each was checked: `users/views.py` `AuthViewSet.otp` (H-43 + H-53: tested together, **18 OK**; a locked `VERIFY_EMAIL` gets 202 bytes identical to an unknown address's, and nothing is sent); `AutoGrader/settings.py` (H-53 settings vs batch-2b comment edits, independent); `dashboard/views.py` (the tiles' counts vs batch-2b's student-dashboard cache keys: separate hunks, and the merged `summary` still computes the partition and completion rate between its cache read and write). |
| Migrations | 4, all from H-56. Each is an AlterField adding a DB default: users 0040 → 0039, billing 0070 → 0069, assignments 0040 → 0039, dashboard 0004 → 0003. **Each depends on its app's beta head**, with no duplicate numbers. batch-2b adds none. |
| Rollback | Target beta 755aa27. H-56's migrations only add column defaults, so 755aa27's code runs unchanged against them, and a code-only rollback is safe. Once they have run, the stop-gap SET DEFAULT SQL for the 9 columns is no longer needed on that deployment. |

## Gate 10 (0b, FULL_SUITE.md; one strict run per rule 15, and each failure fixed and re-run)
| Tip | Result | Cause | Fix (verified by 1a) |
|---|---|---|---|
| f9ad0dc | Ran 5014, **errors=1** | H-56 `Assignment.updated_at` `DatabaseDefault` sentinel in `pdf_cache.build_cache_key` (my miss at H-56; see that record's correction) | 2247007 (code 7c28742) |
| 6212ce9 (+2b, +the H-56 fix) | Ran 5094, **failures=1**, 0 blocked outbound, mypy passed | batch-2b's raw-cache-write guard allowed 2 writes in `users/throttling.py`; H-53 adds 3 | f9f3d94, test-only (2 → 5) |
| 0c95d4e | `AutoGrader.tests_cache_invalidation_coverage` **Ran 10, OK** | | |

**Stated plainly:** no single full run passed on the exact final code tree. 0c95d4e differs from 6212ce9 only by f9f3d94, one test file's allow-list constant (no production code). At 6212ce9 every one of the other 5093 tests passed, and the one failing module passes at 0c95d4e. Under rule 15 (after a test-only commit, run only the touched modules) this is sufficient, and I accept it. Both failures were integration gaps between items verified separately, which is what Gate 10 exists to catch. Neither was a flake.

## Notes (not blocking)
- **N1 (frontend).** The student tiles change is a breaking value change: `assignment(s)_submitted` now excludes graded work. The field names are unchanged. The frontend must know before this reaches users.
- **N2.** H-58/59 refuses `max_seats = 0` on create and on checkout. Stored legacy licences with `max_seats = 0` still behave as "unlimited". The "0 = unlimited" help texts are docs drift (backlog).
- **N3 (carried from batch-2b).** The no-wildcard guard's alias gap (L1 and L2) and H-64 (login activation leaves classmates' rosters stale until the TTL).
- **N4 (deploy).** H-56's migrations are AlterFields of defaults only, with no data change, and run in the normal release migrate.
