# S3 brought up to phase2/epic-a 5811e15: the merge's gates

Recorded by Integration & Release (0b), 2026-09-30. 0b performed the merge (founder decision, confirmed in 0b's session). ed (Security) supplied the resolution; see `RESOLUTION_ed.md`, whose SHA256SUMS were checked before applying.

## Merge
- `200d34a` = merge of phase2/epic-a `5811e15` (the tip staging carries as `c76af56`) into `task/epic-a-s3` `2011641` (= `11a2e16`, v2 VERIFIED-WITH-NOTES, plus docs).
- The 3 conflicts are unions, taken whole from ed:
  - `audit/context.py`
  - `audit/enums.py`
  - `audit/metadata.py`

  An AST check finds every `METADATA_ALLOWLIST` key of both sides present.
- `makemigrations --check --dry-run`: no changes.
- `pre-commit run --files` over the 3 resolved files plus the auto-merged `audit/middleware.py`, `billing/models.py` and `billing/license_service.py`: all hooks pass, mypy included (`precommit_touched_files.log`).

## Rule 15 runs (0b's slot; 6G memory cap, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST unset)
| Run | Start–end (WAT) | Result | Log |
|---|---|---|---|
| Changed modules (ed's 11 labels): `audit.tests_background_attribution`, `billing.tests.test_credit_transaction_audit`, `audit.tests_license_admin_attribution`, `audit.tests_state_change`, `audit.tests_failed_auth_cap`, `audit.tests_route_coverage`, `users.tests_auth_audit_doors`, `audit.tests_emitter`, `audit.tests_metadata`, `AutoGrader.tests_reason_codes`, `AutoGrader.tests_cache_invalidation_coverage` | 16:50:27–16:51:01 | **Ran 255, OK** | `changed_modules.log` |
| ONE regression: `billing` (`--parallel 2`); it exercises the billing auto-merge (S3's clawback and actor rule beside beta's H-58/H-59) | 16:51:01–16:52:59 | **Ran 1682, OK** | `regression_billing_summary.txt` (the full log is outside the repo) |

## Verification
v2: probes and remerge-diff at `200d34a`. The record is committed beside this file when it lands.
