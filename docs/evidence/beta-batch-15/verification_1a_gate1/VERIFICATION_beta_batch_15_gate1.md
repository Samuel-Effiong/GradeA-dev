# Gate 1: beta batch 15 @ 71cbb00cd42eb7f80ce89ade79840613ea8e6c47

**Verifier:** 1a. **Asked by:** 0b. **Date:** 2026-10-09, 13:23 to 13:24 WAT (clock read from `date`). Reads only; no test run, no slot used.

**Verdict: VERIFIED-WITH-NOTES** (the package's last line, the rule 21 worktree line, is still to be read when the package arrives).

## Checked
- **Ancestry:** beta 8567a7a0 is an ancestor of 71cbb00c. First-parent chain: 8567a7a0, d89bbf2d, f0b98ab7, ebb243d0, bff4a844, 8d09c754, 71cbb00c.
- **Each merge tree equals `git merge-tree --write-tree` of its two parents:** d89bbf2d, f0b98ab7, ebb243d0, bff4a844, all four EQUAL.
- **Each merged tip carries exactly its row's verified change.** Sorted +/- lines of (first parent to merge), non-docs, against (merge-base of the gated tip and the first parent, to the gated tip):
  - stack, gated c8ec622d: 1214 lines each side, 9 files, IDENTICAL (merge-base 035e0a07)
  - H-196, gated 2f9dfc1d: 256 lines, 5 files, IDENTICAL
  - H-180, gated 4216575f: 844 lines, 9 files, IDENTICAL
  - H-179, gated e61cbe5a: 141 lines, 2 files, IDENTICAL
  - H-196, H-180, H-179 tips (3f53bced, 22090331, c0294cc9) differ from their gated tips in docs only (0 non-docs files). The stack tip 099870f8 differs from c8ec622d in non-docs files only because it is the base update onto beta; the line-set test above covers it.
- **Non-docs diff against beta:** 22 files, the 22 named. Wording slip in the request: it says "five test modules" in students; there are seven (six score-printing/grade modules plus tests_upload_credit_door). The total 22 is right.
- **No migration, settings, requirements, Beat, command, script, yaml, toml or lock file** in the non-docs diff. 8d09c754 touches docs/HARDENING_BACKLOG.md only; 71cbb00c touches six files under docs/evidence/beta-batch-15 only.
- **Log:** strict_b15.log.xz unpacks to sha256 f93947789c65dd0f... (matches FULL_SUITE.md); one Ran line, `Ran 6455 tests`, `OK (skipped=28)`; 0 FAIL/ERROR headers. Per-app counts sum to 6455; against batch 14's 6356: ai_processor 852 to 861 (+9), classrooms 479 to 487 (+8), students 438 to 520 (+82), all others unchanged. Module gate: Ran 156, OK.
- **Records:** the evidence tree of each row at 71cbb00c equals the tree at the row's merged tip (stack, H-196, H-180, H-179). My H-180 record blob at 71cbb00c has sha 8414981f2fadc6ce, equal to my saved original. My batch 14 Gate 1 record at 8567a7a0 is byte-identical to my original (cmp).

## Notes
- N1. The request's "five test modules" should read seven (see above).
- N2. The stack's gated code is c8ec622d; the merged tip 099870f8 includes a later base update onto beta. By the line-set test it adds nothing else; the whole-suite run at 8d09c754 covers it.
- N3. Not done: the package's last line (worktrees removed under rule 21) is not yet read; V2's three records were checked by tree equality with their row tips, not re-compared with V2's originals (V2 states 35/35, 21/21, 16/16).
- N4. I used `/tmp` for two throw-away line-set files in the merge comparison (removed in the same command); nothing kept.
