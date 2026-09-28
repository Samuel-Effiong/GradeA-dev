# AUTHZ-T1/T2 (token-epoch) — Verification

Branch `task/authz-token-epoch` @ cdbbe01, off beta 4b902fc.

## Re-run myself
`users/tests_token_revocation.py`, fresh test DB: **22/22 passed**. (Full 1,426-module regression and the 11/11-killed mutation battery were already run and recorded by the author/prior verifier in EVIDENCE.md; not re-executed here.)

## Code read against EVIDENCE.md's claims — all confirmed
- `CustomUser.token_epoch`: `PositiveIntegerField(default=0)`, migration 0039 is a plain `AddField` with a constant default (metadata-only on Postgres). Confirmed.
- All 5 `RefreshToken.for_user(user)` call sites in `users/views.py` converted to `EpochRefreshToken.for_user` (grepped: exactly 5, none left on bare `RefreshToken.for_user`). The one remaining plain `RefreshToken(refresh_token)` (line ~1076, logout) only *parses* an existing token to blacklist it — doesn't mint, doesn't need the subclass. Correct.
- `EpochRefreshToken.for_user` reads the epoch fresh from the DB via `.values_list(...).get(pk=...)`, not from the instance — correctly avoids handing out a token stamped with an unresolved `F()` expression or a stale value.
- All 5 `save(update_fields=["password", ...])` callers in the repo (classrooms/serializers.py, backfill_pending_student_invites.py, classrooms/services/enrollment.py, billing/license_service.py, users/views.py:1526) call `set_password`/`set_unusable_password` immediately before the save, and `CustomUser.save()`'s override correctly injects `token_epoch` into `update_fields` in every case. Confirmed by grep + read, matches the "5 callers" claim exactly.
- Refresh rotation: confirmed `ROTATE_REFRESH_TOKENS=True`, `BLACKLIST_AFTER_ROTATION=True` in settings. simplejwt's rotation mutates `jti`/`exp`/`iat` in place and copies all other claims (including `epoch`) onto the new token — not custom code, but I checked it's actually true of this simplejwt version's `RefreshToken.access_token`/rotation path, not just assumed.
- `check_password`'s `_rehashing` guard: correctly scoped with try/finally around `super().check_password()`, so a hasher-upgrade re-save during login can't leave the flag stuck true after an exception either.
- `EpochTokenRefreshSerializer.validate()` checks the epoch itself before calling `super().validate()` (which re-parses the same token) — a little redundant parsing but not a bug; a revoked refresh token gets a clean `InvalidToken` instead of a mint-then-401-on-first-use.

## New finding (non-blocking): "not bumped on user creation" isn't quite true for Google registration
`users/views.py`'s Google-registration branch does `user = serializer.save(...)` (already an INSERT — `user._state.adding` is `False` immediately after), then separately `user.set_unusable_password(); user.save(update_fields=["password","is_active"])`. Confirmed empirically (throwaway test, not committed): this bumps `token_epoch` 0→1 right at account creation, unlike the normal `/auth/register` path (`create_user()` calls `set_password()` *before* the first save, while `_state.adding` is still `True`, so the guard correctly skips it there — also confirmed empirically). No security impact: nothing holds a token for the brand-new row yet, so nothing is revoked, and the row is still internally consistent at whatever epoch it ends up at. Just means EVIDENCE.md's "Not bumped on user creation" line isn't literally true for this one path — worth either fixing the doc wording or reordering that branch to set the password before the first insert. Your call, doesn't block landing.

## Verdict: VERIFIED-WITH-NOTES
Fix is correct and matches its own design doc except the one narrow, harmless discrepancy above. Gates 1/2/4/9 as recorded stand; my own re-run of the targeted test module confirms Gate 1 for that module. Recommend: land as-is, fix the doc line (or the Google branch ordering) as a follow-up, no re-verification needed for that follow-up unless the fix changes bump semantics elsewhere.

Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.

## Typing fix for the django-stubs mypy check (d8b84e5): VERIFIED
- Diff: users/authentication.py only. `cast("CustomUser", user).token_epoch`, with CustomUser imported under `TYPE_CHECKING` only. At runtime `cast()` returns its argument unchanged and the import never executes, so behaviour and the epoch comparison are identical and there's no import cycle.
- Reproduced both ways in throwaway checkouts merged with mypy-django-stubs @9532373. On 0a2a5c3, `mypy users/authentication.py` (hook args) gives exactly `users/authentication.py:51: error: "AbstractBaseUser" has no attribute "token_epoch" [attr-defined]`, 1 error. On d8b84e5, the whole-repo `pre-commit run mypy --all-files` Passed.
- `users.tests_token_revocation` on d8b84e5: 22/22 OK.
Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.
