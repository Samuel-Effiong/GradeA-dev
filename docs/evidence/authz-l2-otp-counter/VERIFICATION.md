# AUTHZ-L2 (reset-OTP guess budget) — Verification

Branch `task/authz-l2-otp-counter` @ 4630469, off beta 4b902fc.

## Re-run myself
`users/tests_reset_otp_budget.py` + `users/tests_throttling.py`, fresh test DB: **23/23 passed** (matches the claimed 15 + throttling-module tests).
Mutation log (`mutation_log.txt`) checked directly: 8 mutants (M1-M8, no M9 — numbering has a gap, not a missing mutant), all show `killed: True`, `SURVIVORS: []`. Confirmed, not just taken on faith.

## Code read against EVIDENCE.md's claims — all confirmed
- `register_failure()`: single `UPDATE ... SET attempts = F(attempts)+1`, then `refresh_from_db`, then locks (with a second `UPDATE`) only `if attempts >= MAX_ATTEMPTS and not self.is_locked()` — the `not is_locked()` guard stops a lock already in place from being re-stamped (and re-logged) on every subsequent guess. Matches the `register_failed_login` pattern cited as precedent.
- `generate_code()`: `select_for_update()` inside `transaction.atomic()`, early-`return None` while locked (verified this correctly exits the `with` block and commits — it's a read-only path when locked, nothing to roll back), refill only when `locked_until is not None` (an *expired* lock — the still-locked case already returned above) or `not row.is_valid()` (stale code). `created_at` is explicitly reassigned and saved on an existing row — confirmed via Django's actual `auto_now_add` semantics that this only auto-stamps on `add=True` (insert); an explicit value on an update-path save is honored, so the resend-refreshes-issue-time fix is real, not a no-op.
- `views.py`: the `/auth/otp` locked branch and the normal-send branch return the **exact same** status (202) and detail string (`"An OTP has been sent if an account with that email exists."`) — diffed both response bodies directly, byte for byte. `reset_password`'s `is_locked()` check already ran before the code comparison on beta; the pre-fix vulnerability was that `generate_code()` cleared the lock itself, not a missing gate — this diff's `views.py` changes (message wording, the locked branch) are correctly scoped to that.
- `throttling.py` docstring change (spam control, not the budget's guard) matches the design decision.
- `docs/HARDENING_BACKLOG.md` new entry: inserted cleanly, doesn't disturb surrounding sections, numbers match EVIDENCE.md (384/day, 0.04%).

## FYI, out of scope (pre-existing, not introduced by this branch)
`/auth/otp`'s "no such account" branch returns a *differently worded* 202 detail ("If an account with that email exists, an OTP has been sent.") than the send/locked branches ("An OTP has been sent if an account with that email exists."). Same status code, same semantic content, but a client that string-matches could still distinguish "no account" from "account, locked-or-sent" — a much weaker signal than the original finding, and untouched by this diff, so not counted against this verdict. Worth a one-line fix sometime (make the wording identical across all three branches).

## Verdict: VERIFIED
Fix matches its design doc in every particular I checked; both new tests plus the inverted `test_generate_code_does_not_clear_an_active_lockout` test correctly assert the fixed behavior; mutation coverage is real (checked the log, not just the summary line). No blocking findings.

Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.
