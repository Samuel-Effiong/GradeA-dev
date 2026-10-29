# beta-batch-1: Verification

## d52364c: exclude docs/ from the whole-repo mypy hook, VERIFIED-WITH-NOTES
- Diff: .pre-commit-config.yaml only, +3 lines (`exclude: ^docs/` plus a comment).
- Reproduced both ways in a throwaway detached checkout of task/beta-batch-1 @3fa6062:
  - with d52364c properly reverted, `pre-commit run mypy --all-files` gives `docs/evidence/authz-token-epoch/mutate.py: error: Duplicate module named "mutate" (also at "docs/evidence/authz-patch-password/mutate.py")` and "errors prevented further checking". So whole-repo mypy checks nothing.
  - at the tip, with d52364c: 734 files checked, **51 errors in 2 files, all h14's** (dashboard/tests_h14_at_risk_equivalence.py 48, dashboard/services.py 3). No other file errors. users/authentication.py is clean thanks to d8b84e5.
- The docs/ scripts are evidence harnesses, not app code, and nothing imports them, so excluding them loses no app coverage.

Notes (non-blocking):
1. **The batch is NOT mypy-green until d5 fixes h14's 51 errors.** tests.yml's new mypy step would fail. Don't land the batch before then.
2. The exclude only covers the pre-commit hook. A developer running plain `mypy .` still hits the duplicate-module stop; adding `exclude = ["^docs/"]` under `[tool.mypy]` in pyproject.toml would give parity. The three bare-name docs overrides in the pyproject ratchet (`attack_replay`, `stripe_refusal_real`, `test_refusal_handling_scale`) are now dead config and can be removed when convenient.

Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.

## a58ad90: Tests job timeout 20 → 30 min, VERIFIED
A single value, justified by the first real run (job 12m55s) plus about 2 min of cold mypy, and documented inline.
Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-29.

## Batch full suite (Gate 10) on a112eda
4871 tests OK, 28 skipped, 0 failures (376.8s test time, 16m16s wall incl. lock wait), under `flock ~/.machine-fullsuite.lock`, `--parallel 4`. Whole-repo `pre-commit run mypy --all-files`: Passed. `makemigrations --check`: no changes. Commits after a112eda are docs-only (VERIFICATION.md records).
The H-39 guard logged 2 blocked calls to api.stripe.com `/v1/payment_intents/pi_test_{1,2}?expand=latest_charge` from `billing/tests/test_subscription_cycle_integrity.py` (webhook cycle tests → `resolve_stripe_receipt_url`, which swallows StripeError by design). Pre-existing on beta; no batch branch touches those files. Logged for the backlog, not blocking.
