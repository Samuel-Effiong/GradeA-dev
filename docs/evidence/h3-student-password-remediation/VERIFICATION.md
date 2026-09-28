# H-3 student123! remediation — Re-verification (of the UserActivity-signal fix)

Branch `task/h3-student-password-remediation` @ 72274cb. Prior verdict VERIFIED-WITH-NOTES @5ac3ba5 (my earlier pass) flagged: "116 accounts, last_login NULL on all, therefore zero impact" is unproven because this app never sets `last_login`. Re-verifying the fix for that note (85c5ecc, e275a10) plus the shutdown-checkpoint commit (72274cb, docs-only — confirmed via `git show --stat`, no code).

## Re-run myself
`users/tests_remediate_student123.py`, fresh test DB: **15/15 passed**.
`mutation_log.txt` checked directly: 7 mutants (M1-M7), all `killed: True`, `SURVIVORS: []` — including the new `M7_activity_signal_ignored`, killed by the two new/renamed activity tests.

## Code read against the fix's claims
- `active_ids` now comes from `UserActivity.objects.filter(user_id__in=...)`, computed unconditionally before the `if not execute: return`, so it's in scope for both the dry-run report and the `--execute` write path — no `NameError` risk, checked the full function body.
- `seen_ids = active_ids | {last_login-having ids}` drives the WARNING and the two new console lines; confirmed the dry-run console output now matches EVIDENCE.md's documented expected-output block exactly (separate "recorded sign-in activity" and "last_login set" lines).
- Tests are real, not vacuous: `test_warns_when_a_matching_account_has_recorded_activity` actually logs in, makes one authenticated request (so the heartbeat middleware writes the `UserActivity` row), and asserts the count and warning — not a fabricated row. `test_no_warning_when_nobody_has_signed_in` is a genuine negative control. `test_last_login_alone_also_triggers_the_warning` covers the union's other half.
- Reset mechanism unchanged from the already-verified 5ac3ba5 state (CAS via `filter(pk=pk, password=old_hash).update(...)`, log-before-file-write reordering also present and matches the stated disk-full rationale).

## Minor finding (non-blocking)
The per-reset audit report's `"has_recorded_activity"` field is set from `pk in active_ids` only, not the broader `seen_ids` (which also OR's in `last_login`). Since this app never actually sets `last_login` in practice, this has no real-world effect today, but it's a small inconsistency between the aggregate WARNING (which uses `seen_ids`) and the per-record audit trail (which doesn't). Trivial one-line fix if you want it (`pk in seen_ids`); not blocking.

## Verdict: VERIFIED
The specific gap from my prior pass is closed and mutation-covered. No new blocking findings. Still not run against production (needs founder approval, as documented).

Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.
