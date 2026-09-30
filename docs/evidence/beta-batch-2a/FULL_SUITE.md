# beta-batch-2a: batch full suite (Gate 10)

Recorded by Integration & Release (0b, grade-automator-plus-83), 2026-09-30.

## Tested tip
`task/beta-batch-2a` @ **3e34962**, which is beta 463e222 plus these items, each merged with its verification record:
- AUTHZ-L2 429 (048a3c9)
- H-3 token_epoch (41d85e7)
- H-25 cache-commit-race (280352c), with the fixture fix 877c900 (VERIFIED-WITH-NOTES) and its record 6dbf137
- retire (A) (bb7d226)
- email warning wording (8ee8f2e)
- batch-2 backlog rows H-51..H-60, plus the deployed-check draft (docs)

H-1 stage 3 and step 4 are NOT in 2a. They go to batch-2b.

## Two strict runs
Both runs used `nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput` in worktree `Grade-Automator-Plus-beta-batch-2a`, with no other test runs on the machine: a quiet window agreed with Vezi, and team targeted runs held.

**Environment:** `RACE_COST_ENROLLMENTS=600 RACE_COST_ROWS=200`, per the SM on 2026-09-30.
- Why: 1a's note N1 on 877c900 found that H-25's `SingleTransactionRosterCostTests` at its 6000 default holds one worker for about 20 min.
- The 6000-row measurement is already recorded in H-25's evidence.
- 600/200 becomes the code default in batch-2b (d5, bd95ccb).
- CI on the pushed tip runs with the code defaults (6000/2000).

| Run | Start (WAT) | End | Wall | Result | "Blocked real outbound" |
|---|---|---|---|---|---|
| 1 | 11:26:00 | 11:33:19 | 439 s | Ran 4970 tests in 394.1 s, **OK (skipped=28)**, exit 0 | 0 |
| 2 | 11:33:19 | 11:39:00 | 341 s | Ran 4970 tests in 316.8 s, **OK (skipped=28)**, exit 0 | 0 |

No failures, errors or flakes, and no reruns.

## Other checks at 3e34962
- `makemigrations --check --dry-run`: No changes detected.
- Whole-repo `pre-commit run mypy --all-files`: Passed.
- Migrations in 463e222..3e34962: **none**.

## Rollback
- **Target:** beta 463e222.
- **Code-only rollback is safe:** there are no migrations between the target and the tip, so no NOT NULL column without a DB default lies in that range (team brief rule 11).

## Deploy-time notes (not covered by this gate)
2a adds two management commands:
- `classrooms/management/commands/backfill_pending_student_invites.py`
- `users/management/commands/remediate_student123_passwords.py`

Running either against production is a production action. It needs founder approval, and the founder runs it via Railway.
