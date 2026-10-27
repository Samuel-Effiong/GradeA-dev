# Beta batch 30

## Bundle

Base: beta `fef4249d` (batch 29 evidence: Ran 8436, OK, skipped=30).

Three --no-ff merges, in order:

1. H-260 @ `d8ae6fa3` — assignment save error text
2. H-264 @ `8b84b4d0` — numbered off-topic answers
3. H-263 @ `aede70b4` — package upgrades (DRF 3.18.2, gunicorn 22, pillow 13, PyJWT 2.15, lxml 6.1, urllib3 2.8) + DRF UniqueConstraint survey

Bundle tip: `4b2500ec`.

Two backlog-table conflicts resolved (kept all rows sorted by H number,
took H-263's Built version over its Open version).

H-268 (overage cap wording) removed before the strict run: its
`OVERAGE_CAP_EXCEEDED` spec holds a lambda, so `CodedError` can't be
pickled. Lands in batch 31 with the fix.

## Expected results

Method: Django `DiscoverRunner` on beta `fef4249d` (8436 ids) and on
bundle `4b2500ec` (8472 ids); sorted id lists diffed with `comm`.

| | Count | IDs |
|---|---|---|
| Base (batch 29) | 8436 | — |
| Removed | 1 | `ai_processor.tests_blank_verification.BlankVerificationTest.test_too_many_blanks_skips_the_re_read` (renamed by H-264) |
| Added | 37 | 1 rename + 30 H-264 + 4 H-260 + 2 H-263 |
| **Expected Ran** | **8472** | 8436 − 1 + 37 |

Expected: **Ran 8472, OK, skipped=30**.

Chromium PDF tests must run (not skipped). The Playwright install is in
the private venv's environment; `PLAYWRIGHT_BROWSERS_PATH` points at the
shared browser cache.

## Strict run

Private venv: `AutoGrader_env_batch30` (built from the bundle's
`requirements.txt`, which carries H-263's upgraded pins).

Settings: `--settings=settings_worktree` (DB `test_beta_batch_30`).

Command:
```
systemd-run --scope -p MemoryMax=24G --user \
  python manage.py test --noinput --verbosity 2 \
    --settings=settings_worktree --parallel 4 \
    2>&1 | tee docs/evidence/beta-batch-30/full_run.txt
```
