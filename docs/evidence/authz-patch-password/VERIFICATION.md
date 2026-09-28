# AUTHZ-PATCHPW — Verification (correctness only; fix 1 deploy held for frontend)

Branch `task/authz-patch-password` @ 199d928, off beta 4b902fc.

## Re-run myself
`users/tests_patch_password.py` + `users/tests_patch_email.py`, fresh test DB: **25/25 passed**.
`mutation_log.txt` checked directly: P1-P3 + E1-E9 = 12 mutants, all `killed: True`, `SURVIVORS: []`. Confirmed.

## Code read against EVIDENCE.md's claims — all confirmed
- `current_password` is a plain `CharField(write_only=True, required=False)` on the serializer, not a model field; traced it end to end — popped out of `attrs` early in `validate()` (`attrs.pop("current_password", None)`) and never referenced again, so it can't leak into `create()`/`update()`'s `setattr` loop or `create_user(**validated_data)`. Matches mutant E9's point exactly.
- Password block: `if "password" in attrs and self._is_acting_on_self()`. `_is_acting_on_self()` returns `False` whenever `self.instance is None` (account creation → routes through `create()`, untouched) or when acting user's pk differs from the target (super-admin on another account, untouched) — read both `create()` and `update()` to confirm neither path is affected for those two cases.
- Email guard fires only when `_is_acting_on_self() and "email" in attrs and attrs["email"] != self.instance.email` — confirmed a same-value email (including case/whitespace differences normalized upstream by `validate_email`) skips `_require_current_password` entirely (mutant E2 exists for exactly this).
- `_require_current_password` order: unusable-password check → already-locked check → missing-password check → `check_password` → on failure `register_failed_login()`, on success `reset_login_lockout()`. Confirmed these are the literal same fields/methods `/auth/login` uses (`MAX_LOGIN_ATTEMPTS=5`, `LOGIN_LOCKOUT_DURATION=15min`, same `CustomUser.is_account_locked/register_failed_login/reset_login_lockout`) — not a parallel counter, a shared one, exactly as claimed.
- Raising inside `validate()` means DRF never calls `.save()` — read `test_a_mixed_request_is_refused_whole_nothing_is_half_applied` and its mutant (P1/P3) to confirm this isn't just asserted, it's mutation-covered.
- `test_the_stolen_token_takeover_chain_is_closed` and `test_it_is_not_a_password_guessing_oracle_the_lockout_applies` are real, non-vacuous end-to-end tests (checked their bodies) — the former replays the exact 4-step chain from the "verified on beta HEAD" section and asserts each step now fails; the latter drives the shared lockout to its limit and confirms even the correct password is then refused.

## Verdict: VERIFIED (correctness)
Both commits (aa0de82 password, 566b447 email) match their design doc in every particular checked. No blocking findings. Deploy timing for fix 1 (frontend must confirm its password-change UI already posts to `/auth/change-password`) is a release-sequencing question, not a correctness one — outside this verdict's scope per the SM's instruction.

Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.
