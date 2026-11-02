# Beta Batch 32

Base: beta f5d2d577 (Batch 31, = origin/beta). Owner approved 2026-11-02: one full run on the batch,
plus a repeated run of H-274's concurrency tests, then push to origin + backup if green.

| Merge | Ticket(s) | Branch tip |
|---|---|---|
| 51e7393e | H-273 audit refusal detail + trace_id/target_id filters | 3f6e5420 |
| 50417207 | H-274 QA server messages (b, c, d, e.1, e.1a, e.2, e.3, f, g) | 01324874 |
| 54d6c3fd | H-276 per-device logout, H-275 invoice wording, H-277 logout owner check | cff1cbf8 (contains H-276 194b1a7b) |

All merges clean (auto-merged billing/stripe_service.py, users/serializers.py, users/views.py).
makemigrations --check: No changes detected. No duplicate migration numbers in users/billing/audit/classrooms/assignments/students.

Count (DiscoverRunner.build_suite, not run): 8737 = 8587 (B31) + 62 (H-273, branch 8649) + 61 (H-274, branch 8648)
+ 11 (H-276) + 16 (H-275/H-277).

Owner process change 2026-11-02: H-274's own Gate 10 (2 full runs at e60d3769) is replaced by this single batch run
plus 5 repeated runs of its concurrency module (the reason Gate 10 asked for two runs was intermittency).

## Expectations (committed before any run)
1. Concurrency repeat: `python manage.py test billing.tests_h274_checkout_stale_customer --settings=settings_worktree --noinput`
   five times in a row on this commit. Expected each time: Ran 20, OK.
2. Full run: `GAP-a0-runs/b32/strict_bundle.sh <this sha> b32 Grade-Automator-Plus-beta-batch-32 beta-batch-32`
   (--parallel 4, -v 2, b31 runner with only the folder path changed). Expected: **Ran 8737, OK (skipped=30)**, the same
   30 skip reasons as Batch 31.
Anything else = STOP, no push.
