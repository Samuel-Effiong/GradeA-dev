# Merge-down b16: gates and records

Branch `task/epic-a-merge-down-b16`: the Phase 2 line `d2ad0405` plus beta's batches 15, 15a and 16 (up to `42ff4b1e`), the gates 1 to 4 text, tests-only adaptations (`72bce4fd`, `52941a6f`), ed's roads-pin fix (`dfee4d19`), H-222 (`3b5f9fb5`, wallet lock first), H-192 (`3dd1aebe`) and H-194 (`f5eedce0`).

- Gate 1 `607e41b2`, gate 2 `72bce4fd`, gate 3 `52941a6f`: red (causes and fixes in the commit messages); consoles and gzipped module logs here.
- **Gate 4 on `918eb2aa` (the code of everything above): GREEN.** `pre-commit run --all-files` exit 0, 26 hooks: 25 Passed, 1 Skipped (check for broken symlinks: no files), none Failed (`hook_table_918eb2aa.txt`, rule 23); makemigrations: no changes detected; 52 modules + the audit app Ran 1022 tests, OK (skipped=5). Console `gate4_console.txt`, raw log `gate4_modules_918eb2aa.log.gz`.
- Verifier 2: the merge `86dcfdc6` VERIFIED (`verification_2/`), H-222 at `f46a3097` VERIFIED (`verification_2_h222/`). Verifier 1: H-192 and H-194 (records in the rows' own evidence folders).
- The promotion's one full run follows (`FULL_SUITE.md`), the regression of H-222, H-192 and H-194 too (rule 15).
- Migration safety by hand over the range against `origin/beta`: `migration_safety_vs_beta.txt` (5 migrations, additive only).
