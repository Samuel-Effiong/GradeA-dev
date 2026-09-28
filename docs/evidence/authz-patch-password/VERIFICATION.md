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

## Part 2 re-verification (0a9313f..c12a8c1): REJECTED

**BLOCKING: 5 existing `users` tests fail.** The full `users` app in my own detached checkout of c12a8c1 gives 518 tests, **6 failures**. One is the known env `test_nothing_is_exempt_by_default` (this branch doesn't have H-39's hermetic fix yet; ignore it). The other five are real and caused by part 2:
- `tests_open_signup.SignupGuardsSurviveTests.test_school_admin_moving_to_a_personal_email_is_still_rejected`
- `tests_email_domain_rules.SerializerEnforcementTests.test_school_admin_may_not_move_to_a_disposable_email`
- `tests_email_domain_rules.SerializerEnforcementTests.test_school_admin_may_not_move_to_a_consumer_alias` (3 subtests: proton.me, googlemail.com, yahoo.co.uk)
They assert `"Personal emails are not allowed"`, but they now get `{'email': ["Email address can't be changed."]}`, because part 2's blanket refusal runs first. The security intent still holds (the change is refused on `email`), but the suite goes red. The "75/75 on touched modules" run didn't include these modules.
Consequence to handle in the fix: on an EXISTING account, the personal/disposable-email domain rule in `validate()` is now unreachable, since every email change is refused first. It still applies on the create path (a super admin POSTing /users with user_type=SCHOOL_ADMIN). Suggested fix: rewrite these tests to (a) assert the blanket refusal on update, and (b) re-pin the domain rule on the CREATE path, so its coverage isn't silently lost.

**Checked and fine:**
- The harness fix is real. The killers now match each mutant: E1 and E6 are killed only by email-refusal tests, E4 only by the super-admin-on-another-account test, and E5 by the creation tests. No password test kills an email mutant any more.
- An echoed `null`/blank email on @student.local rows fails field validation (`EmailField(unique=True)`, not null) before `validate()`. That's pre-existing, and part 2 adds nothing there. A stale `current_password` in a PATCH body is now silently ignored as an unknown field, so old frontends don't break. The remaining `current_password` references are all `/auth/change-password`.
- Baseline `tests_patch_password` + `tests_patch_email`: 25/25.

**Notes (non-blocking):**
1. **E3 is mislabelled and the property it names is unpinned.** It compares against `.upper()`, which makes every email differ, so it's a duplicate of E2. I ran the TRUE mutant (drop the stored side's `.lower().strip()`, i.e. `attrs["email"] != self.instance.email`): it **SURVIVES**, 25/25 OK. The existing case/whitespace test only varies the INCOMING value, which validate_email normalises anyway. The stored-side normalisation protects legacy mixed-case rows from a false 400 on an unchanged-email full-profile PATCH. Add a test that stores a mixed-case email via `.update()` and PATCHes it unchanged.
2. **E5** is killed through an AttributeError (None.email → 500) rather than by the guard refusing. It still proves the create path is exercised, but a test asserting create returns 201 with the email kept would be cleaner.

Verdict: REJECTED until the 5 tests are fixed (with the domain rule re-pinned on create). Once that's in, a quick re-check of the test diff plus a `users` app run should be enough.

Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.
