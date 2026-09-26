# AUTHZ-OAUTH — Google sign-in left a pre-registered password usable (account takeover)

Branch `task/authz-oauth-takeover` · fix commit `db87b85` (+ evidence/test commit that carries this file) · written by the Security Lead.
Finding origin: `task/redteam-authz` `732b182` (reproduced there on `b744c9f`); re-verified here on beta `0efba21` and again on `ea7183b`.

**Status: FIXED on the branch. Local gates pass. NOT landed, NOT deployed. Independent verification and the full-suite gate are still owed (see "Open").**

## 1. The bug

`POST /auth/google-auth`, existing-account branch (`users/views.py`, "resurrection"): if a row exists for the Google-verified address and is `is_active=False` with `email_verified_at IS NULL`, the view sets both fields and issues tokens. It never touched the password.

`POST /auth/register` (`CustomUserSerializer.create` -> `create_user`) creates exactly such a row, with the **caller-chosen, usable** password. So:

1. Attacker registers `victim@gmail.com` with the attacker's password. Row is dormant (login -> 401).
2. Victim later signs in with Google. The row is activated and verified.
3. The attacker's password is still valid: `POST /auth/login` -> 200. Full takeover of an account the victim believes is Google-only.

Precondition: attacker registers the address before the victim's first sign-in, and the victim then uses Google. No throttle dependency, no action beyond ordinary sign-in from the victim. Severity: **HIGH**.

## 2. The fix

In the resurrection branch, when `is_active` actually flips from False to True: `user.set_unusable_password()` and `"password"` added to `update_fields`.

Two details that matter (both have a test that fails if they are got wrong):
- `update_fields`: without `"password"` Django silently drops the change (mutant M2).
- Gate on `is_active` flipping, not on "anything resurrected": a license-invited teacher is created `is_active=True`, `email_verified_at=NULL` with a temporary password, and their first Google sign-in only stamps `email_verified_at`. Wiping their password there would be an unrelated behaviour change (mutant M3).

The new-account branch already did this (`set_unusable_password()`); the control in the replay confirms it.

## 3. Gate results

| Gate | Result | Evidence |
|---|---|---|
| 1 Baseline / regression | Pristine beta `views.py`: attacker's password logs in (`200 != 401`), `has_usable_password` stays True. Fixed: all pass. `users.tests_google_auth` 45 tests OK (3 opt-in live-Google tests skipped, as before). Full suite: **pending slot** | `gate1_unfixed_beta_0efba21.log`, `gate1_fixed.log`, `gate2_mutation.log` (M0 and control sections) |
| 2 Mutation | M0 (no fix) 7 failures; M1 drop `set_unusable_password` 7 failures; M2 omit `"password"` from `update_fields` 7 failures; M3 gate on any resurrection 1 failure (the already-active control). **3/3 mutants killed** | `gate2_mutation.log` |
| 3 Concurrency | Not run as a threaded test. Reason: both racers compute the same end state (`is_active=True`, unusable password) inside `transaction.atomic`, and repeated sign-ins are covered by `test_repeated_google_sign_ins_on_a_dormant_row_are_stable` | — |
| 4 Adversarial | The **original red-team exploit script** (copied verbatim from `task/redteam-authz` `732b182`; the committed copy differs only by black formatting from the pre-commit hook, the run used the verbatim file), replayed over real HTTP against two running apps on separate Postgres DBs. Pristine beta `ea7183b`: `vuln_pre_registration_takeover: true` (login 401 -> Google 200 -> attacker login **200**, usable password True). Fix `db87b85`: **false** (attacker login **401**, usable password False). Control (fresh Google-only account) unchanged on both | `gate4_replay.log`, `logs/oauth_ea7183b.json`, `logs/oauth_db87b85.json`, `replay_scripts/`, `settings_redteam.py` |
| 5 Failure | Not applicable: the change adds no I/O; it runs inside the existing transaction, so a failure rolls back activation and the password change together | — |
| 6 Stress | Not applicable: one extra column in an existing `UPDATE` on a once-per-account path | — |
| 7 Real infrastructure | Real PostgreSQL for the unit tests and both replay apps. Redis key prefix per lab app. Only the external Google IdP boundary is stubbed (same as the red-team harness; the real-Google failure contract tests remain opt-in) | `settings_redteam.py` |
| 8 Live / end-to-end | LOCAL-REAL only (running app, real HTTP register/login, in-process real `google_auth` body with Google stubbed). **Not run on a deployed QA environment** | — |
| 9 Security / isolation | Role matrix: a dormant row of every `UserTypes` value (STUDENT, TEACHER, SCHOOL_ADMIN, SUPER_ADMIN) loses its password on activation. Deactivated (previously verified) accounts are still refused with the row untouched, password included. Already-active rows keep their password | `test_every_role_...`, `test_a_deactivated_account_is_refused_...`, `test_an_active_but_unverified_account_keeps_its_password` |
| 10 Full-repository | **Pending**: needs a `--parallel 4` slot from the Integration & Release Lead | — |

Lab notes: apps ran from a detached scratch worktree at beta `ea7183b` (baseline) and from this branch (fix), DBs `authz_oauth_base` / `authz_oauth_fix`, Redis db 14 with unique key prefixes (nothing flushed), auth throttles disabled in the lab only (throttling is not what this replays). Servers stopped and lab DBs dropped after the run.

## 4. Other credentials on the same row

| Credential | Same flaw? | Reasoning |
|---|---|---|
| Password | Yes (fixed) | Above. |
| `activation_token` / `activation_expires` | Not a login credential; left as-is | Mailed to the address owner; only usable at `/auth/verify` (section 6). The resurrection branch never cleared it, which is what makes Google-activated rows identifiable (section 7). |
| `PasswordResetOTP` rows | No | `RESET_PASSWORD` requires `email_verified_at`, which a dormant row lacks, so no reset OTP can exist for it; codes go to the mailbox owner anyway. |
| JWTs / refresh tokens | No | A dormant row cannot log in (asserted in the replay: pre-Google login is 401), so no tokens exist to revoke. Blacklisting here would be dead code. |
| Google credentials row | n/a | Created by this same sign-in for the person who authenticated with Google. |

## 5. Every path that flips `is_active` on a CustomUser row

| Path | Same flaw? |
|---|---|
| `google_auth` new-account branch | No — created active with unusable password. |
| `google_auth` resurrection branch | **Yes — fixed here.** |
| `POST /auth/verify` (`AuthViewSet.verify`) | **Yes, different door, not changed here** — section 6. |
| `register_student` (`/auth/register/student`) | No — sets a fresh password in the same save that activates. |
| `register_school_admin` | No — same shape. |
| `resend_school_admin_invitation` (H-42) | Does not touch `is_active`; only reissues the invitation token. Not routed through `google_auth`. |
| License-invited teacher (create / resend) | No — created active, generated temporary password set in the same transaction. Interacts with the fix only via the first-Google-sign-in case, which is covered by the gating and a test. |
| Direct-add student, enrollment service, roster import | No — active from creation with unusable/generated password, or empty (unusable) password. |
| Django admin `activate_users` bulk action | Shares the pattern but is staff-only, not attacker-reachable. |

Invited teachers and school admins are not routed through the changed branch, so their flows are unaffected.

## 6. Separate finding: `/auth/verify` (NOT changed in this task; product decision)

Per the Senior Manager's ruling, `/auth/verify` is the normal self-registration flow and stays as-is here.

**Current behaviour (pinned by `users/tests_verify_email_preset_password_characterization.py`, 3 tests):** register with a password -> dormant row + emailed 6-digit token. Anyone submitting the token to `/auth/verify` activates the row (answers 202 with tokens) and the **registrant's** password then logs in. The token goes to the address owner's mailbox, so an attacker who pre-registered someone else's address needs that person to click a link for an account they never created.

**Assessment:** MEDIUM (proposed). Needs the victim to act on an unexpected verification email, so it is a social-engineering variant, versus the Google path which needs nothing from the victim. Mitigating detail: a victim who cannot log in will usually use "forgot password", and `reset_password` sets a new password and blacklists outstanding tokens, ending the attacker's access. The window is until that happens.

**Fix options and cost:**

| Option | Closes it? | UX cost |
|---|---|---|
| A. Set (or re-enter) the password at verify time: `/auth/verify` takes a password and overwrites the row's | Yes: the mailbox holder chooses the password | One extra field on the verify screen; frontend change; API change to `/auth/verify` |
| B. A second `/auth/register` for the same unverified address replaces the pending row and its password | Partly. The attacker can re-register after the victim and replace the victim's password before they click | None visible, but it makes the race symmetric rather than closing it |
| C. Email wording plus a "not you?" link that deletes the pending row and token | Only if the victim notices | New link/endpoint; relies on vigilance |
| D. Register without a password, choose it after verifying | Yes | Larger flow change than A |

**Recommendation:** A (D if the frontend team prefers a cleaner flow), with C as a cheap interim. B alone is not a fix. Founder decision.

## 7. Legitimate users, and production exposure

**UX impact of the fix:** someone who registered with their own password, never verified, then used Google, now has an unusable password. They can still sign in with Google, and can set a new password with "forgot password" because Google activation stamps `email_verified_at`, which the reset flow requires. Covered by `test_the_rightful_owner_can_still_set_a_password_by_reset_after_google`; a later Google sign-in does not wipe the reset password (`test_a_later_google_sign_in_does_not_wipe_a_password_set_by_reset`). The row cannot distinguish that person from an attacker, which is why the password is invalidated in both cases.

**Exposure query:** `prod_exposure_query.sql` — a single read-only `SELECT`. **Not run against production.** Fingerprint: `registration_method='EMAIL'`, has a Google credentials row, `activation_token IS NOT NULL` (the verify path clears it; the resurrection branch did not), usable password, verified and active. Validated on the local lab: returns the exploited baseline victim row, returns 0 rows on the fixed app.
- Every hit is a suspect. Legitimate users who verified by Google and later reset their password also match (their password is usable again).
- The database cannot show whether the password was used afterwards: the JWT login path does not update `last_login` (lab: takeover login returned 200, `last_login` stayed NULL) and there is no per-login audit table on beta. Triage needs web/auth logs for `POST /auth/login` 200 on each suspect address after `activated_at`.
- Rows a suspect owner has since reset or that the owner has since verified by another route may drop out of the fingerprint; a clean result is not proof of no takeover.

## 8. Open

1. Full-repository gate (Gate 10): requested a slot from the Integration & Release Lead.
2. Independent verification by the Verification Engineer.
3. Gate 8 on a deployed QA environment before any production promotion (security tier).
4. Founder decisions: production exposure query; `/auth/verify` option.
5. Branch is based on beta `0efba21`; beta is now `ea7183b`. The change is confined to one branch of `google_auth` and its test module; expect a clean merge but re-run the Google module after merging.
