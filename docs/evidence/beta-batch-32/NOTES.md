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

## Results (on e12a2397)
1. Concurrency repeat: 5/5 runs `Ran 20, OK` (log GAP-a0-runs/b32/concurrency_5x_e12a2397.log,
   sha256 aa9d38821592eb8cbd9470408f730598d394fc8359c23fd1c9e43b190a823a73).
2. Full run (strict_bundle.sh, --parallel 4, -v 2): **Ran 8737, OK (skipped=30)**, rc=0, wall 1139 s, watchdog never
   fired, memory guard never fired, 0 FAIL/ERROR headers. The 30 skip reasons are identical to Batch 31's (diff empty).
   mypy Passed, makemigrations clean (runner pre-checks). Summary: full_run_summary_e12a2397.txt.
   Log archived outside the repo (5.9 MB): GAP-a0-runs/b32/strict_b32.log,
   sha256 4251722e72ab55b12a361cf03a6525ff4d67f2533040a944a97abac60a16eda7.
   First runner attempt exited 4 before any pre-check ("another GAP test run is active": the H-278 worker's
   targeted run); nothing ran. Retried when clear.
Matches the committed expectation exactly. Owner approved the push on green (2026-11-02).

## Co-author lines stripped before push (owner decision 2026-11-02)
53 commit messages (from the H-273 and H-274 branches) carried `Co-Authored-By: Claude ...` lines, against the
standing repo rule. Owner chose "strip the lines, then push". Done with
`git filter-branch --msg-filter 'sed "/^Co-Authored-By: Claude/d" | git stripspace' -- f5d2d577..beta-batch-32`.
Messages only: all 72 commits keep their exact tree (verified pairwise, same order), author and date.
The tested commit e12a2397 is now ce1a0d70 (same tree). Every old -> new id: strip_sha_map.txt.
SHAs quoted earlier in this NOTES and in the ticket evidence refer to the pre-strip ids; use the map.
