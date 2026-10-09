# Gate 1: beta batch 14 at 93a45326 (Verifier 1, reads only, no test run)

Verifier 1a. Asked by 0b, clock 10:21 on 2026-10-09. Tip 93a45326aa162b19cd163562d4587888fea86dff on task/beta-batch-14, over beta 5e37ac2b.

Verdict: VERIFIED-WITH-NOTES.

## Checked
- Ancestry: 5e37ac2b is an ancestor of the tip. Working tree clean.
- Merges: all 9 merge commits in 5e37ac2b..tip have a tree equal to git merge-tree of their two parents (nothing on neither parent).
- Each merged tip against its verified tip, by the added and removed lines of the merge side against the row's own change from its merge-base (lines sorted and compared): H-181 cacc2141 (598 lines), H-178 Part A 12290a29 (290), H-164 f898bfe4 (919), H-182 ed213782 (506), H-202 9201dbc0 (576), H-202 403980bc (0): no line on either side only. Merge bases 035e0a07, 3f2ad13e, 3f2ad13e, 41a92162, e11a0083, eba65a42.
- My verified tips against what was merged, non-docs files: H-181 5b0ddded to 6fe3a08e: none. H-164 f12f12d0 to e11a0083: none (b6282c7f and 42742c40 differ from e11a0083 in users/views.py and the reset test module, as they should: they are earlier tips). H-182 046a90f6 to 44da6f58: none. H-202 5ad21e96 to eba65a42 and to 13e79e12: none.
- Non-docs diff against beta, 15 files: billing/licence_rollup.py, license_service.py, locks.py, models.py, services.py, stripe_service.py, five billing test modules, users/admin.py, users/views.py, two users test modules. No migration, settings, requirements, Beat, command, serializer or URL file.
- Log: strict_b14.log.xz unpacks to sha256 dcad3a9a...739b as stated; Ran 6356 tests, OK (skipped=28); 0 FAIL or ERROR headers; per-app counts sum to 6356 and, against 13h's summary, billing 2130 to 2158 (+28), users 681 to 757 (+76), the rest unchanged. Module gate: Ran 115, OK.
- Record commit 93a45326 adds files under docs/ only. 38195d20 changes docs/HARDENING_BACKLOG.md only.
- My six records (H-181, H-182, H-164 x3, H-202) are in the tree and cmp byte-identical to mine.

## Notes
- N1. FULL_SUITE.md has no worktree line (as asked), but it also ends with "After the run tip", not with a worktree section. Rule 21 asks the package's last line to name the worktrees removed or to be removed. The package for batch 14 is not yet in the tree; I check that line when it is written. The worktrees of mine are all released to 0b.
- N2. The record's section for H-164 names three verified tips; the merged code is the last (f12f12d0 equals e11a0083 in code).
- N3. mypy and makemigrations: only the script's own pre-checks (mypy Passed, makemigrations "No changes detected"); I ran nothing.
- N4. Open notes carried from my records, not batch blockers: H-203 (Google sign-in of a never-verified admin account), the old epoch of accounts switched off before H-202, H-198 (state oracle).
