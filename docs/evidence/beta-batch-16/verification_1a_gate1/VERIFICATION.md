# Gate 1: beta batch 16 @ 1edd5c92ab3611f158bf7d854391b3250617cd46

**Verifier:** 1a. **Asked by:** 0b. **Date:** 2026-10-09, 19:28 to 19:32 WAT (clock read from `date`). Reads only; no test run, no slot used. The worktree was read in place (`Grade-Automator-Plus-beta-batch-16`, clean); nothing was written there.

**Verdict: VERIFIED-WITH-NOTES** (the package's last line, the rule 21 worktree line, is still to be read when the package arrives).

## Checked
- **Ancestry:** beta `b4fda750` (batch 15a) is an ancestor of `1edd5c92`. First-parent chain: `fb25d0e0` (H-211), `74f1ba09` (H-208), `7a8fe9c9` (H-203), `72d6a157` (H-209), `6dd72240` (H-209's own evidence), `6629f838` (docs), `1edd5c92` (run record).
- **Each merge tree equals `git merge-tree --write-tree` of its two parents:** fb25d0e0, 74f1ba09, 7a8fe9c9, 72d6a157, 6dd72240, all five EQUAL.
- **Each merged tip carries exactly its row's verified change.** Sorted +/- lines of (first parent to merge), non-docs, against (merge-base of the row tip and the first parent, to the row tip):
  - H-211, tip fc35d036 (merge-base 22090331): 190 lines each side, 4 files, IDENTICAL
  - H-208, tip 5b9e9956 (merge-base 8567a7a0): 520 lines, 7 files, IDENTICAL
  - H-203, tip 5fe68114 (merge-base e11a0083): 1058 lines, 9 files, IDENTICAL
  - H-209, tip a0c5af33 (merge-base 5b9e9956, i.e. stacked on H-208): 257 lines, 2 files, IDENTICAL
- **Non-docs diff against beta:** 20 files (2288 insertions, 72 deletions) at the merge tip `72d6a157`. The three commits after it (6dd72240, 6629f838, 1edd5c92) change 0 files outside `docs/`.
- **No migration, settings, requirements, Beat, yaml, toml, lock or Procfile file** in the non-docs diff. The only matches of a name search over the whole diff are three files under `docs/evidence/h203-google-road-admin-power/` (two makemigrations-check text files and V2's run script).
- **Evidence trees:** `docs/evidence/<row>` at 1edd5c92 equals the tree at the row's merged tip for all four rows (h211 at fc35d036, h208 at 5b9e9956, h203 at 5fe68114, h209 at 7d328eac).
- **Log:** `strict_b16.log.xz` unpacks to 8,085,413 bytes, sha256 `4f29b9a7b4758ef6...`, matching FULL_SUITE.md. One Ran line: `Ran 6521 tests in 531.460s`, `OK (skipped=28)`; 0 FAIL/ERROR headers; 28 skipped lines. I counted the per-app test lines in the raw log myself: ai_processor 861, assignments 721, AutoGrader 692, billing 2171, classrooms 496, dashboard 270, students 534, users 776, which sum to 6521 and equal the script's table. Against batch 15's 6455: +66, all in assignments +11, billing +13, classrooms +9, students +14, users +19; ai_processor, AutoGrader and dashboard unchanged (batch 15 counts are from my batch 15 record).
- **Run summary:** start 19:15:13, end 19:24:36, exit 0, watchdog 0, suspends 0, blocked outbound 0; mypy Passed and makemigrations clean by the script's pre-checks.
- **Hook table (rule 23):** `pre-commit run --all-files` at the code tip 72d6a157: 25 hooks, 24 Passed, 1 Skipped (broken symlinks, no files), none failed. The table is a hook table of the merged code tip, which is the code of the full run (the later commits are docs only).
- **Records:** my H-211 record and runner/console blobs inside `docs/evidence/h211-student-edit-credit-sentence/verification_1a/` are byte-identical to my originals where I checked by name (VERIFICATION_h211_student_edit_credit_sentence.md, h211_run.sh.txt, h211_all_console.txt: cmp OK); the whole h211 evidence tree equals the tip I cmp'd (11 of 11) at fc35d036. My batch 15 Gate 1 record, in `docs/evidence/beta-batch-15/verification_1a_gate1/` at 1edd5c92 (commit 7c58ab96, already on beta), is byte-identical to my original (sha256 starts 39d41c706b4d6998): cmp OK. The batch 14 record was cmp'd earlier.
- **V2's H-203 record** (`v2_record_eeea349d/`) and **d5's H-209 evidence** are present; they are checked here by tree equality with the row tips, not re-compared with the authors' originals.

## Notes
- N1. The package's last line (rule 21: worktrees of the merged rows named for removal by 0b) is not yet read; the package was not part of this hand-over. The rows' worktrees to be removed after the push: H-211, H-208 (and its H-209 stack), H-203, plus the H-211 verifier checkout `vf_h211` with databases `test_vf_h211` and `test_vf_h211_mut`.
- N2. 0b's message said my batch 15 record was not yet committed; it is on beta in 7c58ab96 and cmp'd above. The batch 16 record is to be committed by 0b byte for byte from `GAP-1a-records/VERIFICATION_beta_batch_16_gate1.md`.
- N3. The module gate's 14 modules and the one full run are 0b's; I repeated neither (rule 15). The hook table and the module gate were taken from the files, not re-run.
- N4. This machine's runs skip 28 tests while CI skipped 75 on batch 15a; the counts are not comparable (0b disclosed this).
- N5. The load average was 5.4 while I read (other sessions); I ran no test and no whole-tree scan.
- N6. For the later promotion: H-203's `DIRECT_REFUSALS` either-or in `classrooms/tests_h203_admin_power_routes.py` is tightened in the same bundle as H-214, as ed says; nothing in batch 16 depends on it.
