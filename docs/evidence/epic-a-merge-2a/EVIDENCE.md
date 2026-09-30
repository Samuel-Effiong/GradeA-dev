# Merge: batch-2a (755aa27) down into Epic A (phase2/epic-a 3bbafdd)

Branch `task/epic-a-merge-2a`, merge commit `e549c9d`. Phase 2 takes beta, never the reverse (founder standing rule). 0b found the conflict while refreshing staging; the SM ruled on the resolution. The verifier is v2; 1a reads the L2 behaviour.

## The one conflict: `AuthViewSet.reset_password` (`users/views.py`)
- **Batch-2a (AUTHZ-L2):** a locked reset answers `_reset_locked_response` (429 `RESET_LOCKED`), and the wrong guess that spends the budget gets that 429 at once.
- **Epic A S1:** records an AUTH_LOGIN event on every door attempt, and answered a locked reset with a 400.

**Resolution (SM rulings).**
- Behaviour is L2's: the 429 in both places. S1's 400 for a locked reset is gone.
- Audit is one event per attempt, recording what happened to that attempt:

| Attempt | Response | Event |
|---|---|---|
| While already locked | 429 `RESET_LOCKED` | DENIED, reason `RESET_LOCKED` |
| The wrong guess that spends the budget | 429 `RESET_LOCKED` | FAILURE, reason `INVALID_CODE`, `metadata.lock_triggered: true` |
| An ordinary wrong guess | 400 | FAILURE, reason `INVALID_CODE` (unchanged) |

**Supporting changes.**
- `users.auth_audit.sign_in_failed` gains `extra_metadata`.
- `lock_triggered` (a boolean) is added to the global `ALLOWED_KEYS` and to `AUTH_LOGIN`'s allow-list.

Every other file merged cleanly.

## Tests
- `users/tests_auth_audit_doors.py`:
  - `test_locked_is_denied` now pins the 429; it asserted no status before.
  - New `test_the_guess_that_spends_the_budget_is_a_failure_that_set_the_lock`: MAX_ATTEMPTS wrong codes answer 400 … 400, 429. Each records exactly one FAILURE `INVALID_CODE`, and only the last carries `lock_triggered: true`.
- S1's invariant tests (`audit.tests_state_change`) and batch-2a's L2 tests (`users.tests_reset_otp_budget`) run unchanged.
- Events are ordered `occurred_at, pk` (1a's note), so equal timestamps can't flake it. `users.tests_auth_audit_doors` re-run after that change: **20 OK**.

## Gates
Every run was wrapped in `systemd-run MemoryMax=6G`, `nice -n 10`, a timeout, RACE_COST 600/200, `EXEMPT_EMAIL_DOMAINS=` and `--noinput`, one at a time.

| Gate | Result |
|---|---|
| Doors + L2 + S1 | `users.tests_auth_audit_doors users.tests_reset_otp_budget users.tests_auth_audit_events audit.tests_state_change`: **88 OK** |
| Regression | `users audit AutoGrader`: **1261 OK** (skipped=4); `billing classrooms`: **2033 OK** |
| mypy | whole-repo `pre-commit run mypy --all-files`: **Passed** |
| Migrations | `makemigrations --check --dry-run`: **No changes detected** |
| Full suite | 0b's Gate 10 on phase2/epic-a after the merge |
