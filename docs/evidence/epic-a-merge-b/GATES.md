# Step (b): beta abeda10 merged down into phase2/epic-a at 6ae18c4, the gates

Recorded by Integration & Release (0b), 2026-09-30. 0b performed the merge (founder decision, confirmed in 0b's session). ed (Security) supplied the resolution; see `RESOLUTION_ed.md`, the README from `GAP-1a-records/epic-a-merge-b/`, whose SHA256SUMS were checked before applying.

## Merge
- `6ae18c4` = merge of beta `abeda10` into phase2/epic-a `19b2072`. The commit message lists the conflicts, the settings keys by side, and ed's patch.
- `makemigrations --check --dry-run`: no changes.
- `pre-commit run --files` over the 11 resolved or patched files: all hooks pass, black, isort, flake8 and mypy included (`precommit_touched_files.log`).

## Rule 15 runs (0b's slot; 6G memory cap, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST unset)
| Run | Start–end (WAT) | Result | Log |
|---|---|---|---|
| Changed modules: `users.tests_auth_audit_doors`, `audit.tests_failed_auth_cap`, `audit.tests_emitter`, `audit.tests_route_coverage`, `AutoGrader.tests_reason_codes`, `AutoGrader.tests_cache_invalidation_coverage`, `users.tests_verify_email_budget`, `users.tests_throttling`, `users.tests_auth_input_validation` | 16:36:01–16:36:30 | **Ran 209, OK** | `changed_modules.log` |
| ONE owning-app regression: `users` (`--parallel 2`) | 16:36:30–16:37:13 | **Ran 686, OK (skipped=4)** | `regression_users_summary.txt` (full log outside the repo, over the 500 KB hook limit) |

## Verification
- v2: the auth/audit part and the settings hunk (static review clean; probes and ed's 5 suggested mutants in v2's slot).
- 1a: the H-53 part of `users/views.py` `AuthViewSet.verify`.
- Their records are committed beside this file when they land.

## Next
ONE Gate 10 (strict full run, 12G) on the combined tree, then the staging package (staging `c909766` → the new tip, a fast-forward).
