# H-34: lockout extension (DESIGN ONLY, awaiting the owner's approval)

Branch `task/h34-lockout-extension`, made off origin/beta bcc50d83c. Worktree Grade-Automator-Plus-h34, test DB test_h34_lockout_extension.
This commit is NOTES only: no product edit, no test edit. Nothing is merged or pushed.

Owner directive: continued wrong passwords on a locked account extend the lock, escalating to a cap.
The risk the owner must see: anyone who knows an email can keep that account locked.

## 1. Survey (origin/beta bcc50d83c)

### Lock state and where it is read or written

| Place | What it does |
|---|---|
| users/models.py:94-95 | `MAX_LOGIN_ATTEMPTS = 5`, `LOGIN_LOCKOUT_DURATION = 15 min`. |
| users/models.py:146-147 | Fields `failed_login_attempts` (db_default 0) and `locked_until` (nullable). |
| users/models.py:297 | `is_account_locked()` returns `locked_until > now`. |
| users/models.py:355-375 | `register_failed_login()`: a single UPDATE with F()+1, then refresh. If count >= 5 and not locked, it sets `locked_until = now + 15 min`. |
| users/models.py:377-385 | `reset_login_lockout()`: sets count 0 and lock None. Called only on a successful password login. |
| users/serializers.py:511-533 | Login. A locked account is refused (401 `account_locked` / ACCOUNT_LOCKED, audit DENIED) **before** any password check, and nothing is counted. |
| users/serializers.py:536-540 | A wrong password (and only a wrong password: the inactive-account case is excluded) calls `register_failed_login()`. |
| users/serializers.py:562 | Success calls `reset_login_lockout()`. |
| users/views.py:2525-2531 | `TokenObtainPairView`, `throttle_classes=[LoginThrottle]`. |
| AutoGrader/reason_codes.py:368-374 | ACCOUNT_LOCKED spec. Message "Too many failed sign-in attempts. Please try again later." Remediation "Wait a few minutes and try again, or reset your password." |
| docs/GRADE_A_PLUS_REASON_CODES.md:404-414, 812, 877 | Developer docs. They state "Five wrong passwords lock the account for 15 minutes". |
| users/admin.py | Does not expose either field. **No operator unlock exists** apart from a shell. |

No other product code reads or writes these two fields. That covers google_auth, verify, reset, change-password and admin; the other hits are tests and docs/evidence plants.

### Rates (AutoGrader/settings.py:1267-1275, all per IP via AnonRateThrottle)

| Scope | Rate |
|---|---|
| login | 10/min (14,400/day per IP) |
| otp_request | 5/hour |
| password_reset | 10/hour |
| google_auth | 20/hour |

### Password reset and OTP

- POST /auth/reset-password (users/views.py:1446-1560) needs a `PasswordResetOTP` row. The code is 6 digits and valid for 15 min. The account has a budget of 5 wrong codes, then a 30-min reset lock (AUTHZ-L2). Requesting a new code does not refill the budget while the reset is locked.
- On success it sets the new password, does `user.save()` (a **full** save), deletes the OTP, blacklists old tokens and **issues new tokens**: "You are now logged in".
- **It does not clear the login lock.** After a reset the user is signed in on that device. A later password login is still refused until `locked_until` passes, and because the count stays >= 5, one typo after that locks again.
- change_password (authenticated) and invited-admin activation set passwords. Neither touches the lock.
- google_auth (users/views.py:2191-2480) issues tokens without checking or clearing the login lock.

### What happens today (not obvious from the 15-minute figure)

- The count never decays. It is cleared only by a successful password login.
- After the first lock expires, the next wrong password (count 6, 7, ...) immediately locks again for 15 min.
- So today **one request per 15 min (96/day) from one IP already keeps an account locked indefinitely**. The DoS the owner is asking about exists now; the extension changes its price, not its existence.
- Guesses sent during a lock are refused unchecked. The only guesses that are ever evaluated are the first 5, plus one at each lock expiry.

### Existing tests

| Module | Count | Covers |
|---|---|---|
| users.tests_login_lockout | 13 | Threshold, correct password refused while locked, expiry, inactive, scoping, F() concurrency, live server burst (BurstLiveServerThread). |
| users.tests_models_and_admin | 36 | Lockout model units at :255-277. |
| users.tests_auth_lock_envelope | 7 | Lock envelope fields. |
| users.tests_h274_sign_in_message | 6 | Locked message differs from the wrong-password message. |
| users.tests_auth_audit_events | 9 | :103 pins that a locked account emits one DENIED event and **never checks the password**. |
| users.tests_throttling | 8 | OTP lock. |
| AutoGrader.tests_reason_codes | 61 | Catalogue. |
| AutoGrader.tests_live_server_backlog_guard (label 67) | 2 | Every LiveServerTestCase uses the backlog-128 thread. |

All counts are from build_suite().countTestCases(). The live server test bounds the count as `>= 5` and `<= WORKERS`, so it holds under every option below as long as one request adds at most one.

### Frontend-facing codes

- Login 401 body: `detail`, `code: "account_locked"`, envelope (reason_code ACCOUNT_LOCKED, error_class, remediation, retryable, params, reference).
- No `locked_until` and no Retry-After are sent; RESET_LOCKED does send them.
- I found no reference to `account_locked` in the frontend checkouts on this machine (CRM-frontend, vezi-frontend-wt). The real frontend may live elsewhere.

### Findings outside H-34's directive (for the SM to file or rule)

- **F1** A completed reset signs the user in but leaves the login lock and count in place (above).
- **F2** reset_password's full `user.save()` writes back the `failed_login_attempts` and `locked_until` it read earlier. A wrong guess that lands between the read and the save loses its increment.
- **F3** Google sign-in ignores the login lock. That is arguably correct, since Google proved identity, but a success there does not clear the lock either.
- **F4** No operator unlock in admin.
- **F5** The locked message tells an attacker the account exists. This is deliberate (H-274), but anyone can trigger it: 5 wrong passwords, then the 6th answer says locked, which means the address has an account. A longer lock does not change this.
- **F6** Reset recovery denial (AUTHZ-L2, existing). Every 30 min: request a code, which emails the victim, then 5 wrong codes. That is 288 requests/day (10/hour of password_reset fits one IP) and 48 emails/day to the victim. The victim cannot win the race, because the 5 wrong codes land within milliseconds of the code being issued.

## 2. Options

Shared rules for every option:

- A request on a locked account is still refused **before** the password is checked. This is already pinned by tests_auth_audit_events:103. Checking it would give a guesser an oracle: whether a lock got extended reveals whether the guess was right.
- The correct password sent during a lock is therefore treated like any other attempt.
- Every write stays a single UPDATE: F() for the count, and Greatest() so a lock never shortens under concurrency.
- Steps are counted in lock "levels". `level = max(0, failed_login_attempts - 5)`, which needs no new column and no migration.

### Option A: slide only (no escalation)

- **Lock rule:** each attempt during a lock sets `locked_until = max(locked_until, now + 15 min)`. The count is unchanged.
- **Reset rule:** a successful login or a completed reset clears it (see section 3).
- **Attacker who knows the email, 1 IP:** 96 requests/day keeps the account locked, the same as today. Evaluated guesses: 101 on day 1, about 35,000/year, the same as today.
- **Attacker spread across many IPs:** no gain against one account (the counter is per account). Coverage grows linearly: one IP can keep 150 accounts locked (14,400 ÷ 96).
- **Real owner:** must stop trying for 15 min. With retries, it is 15 min after the last one.
- **Verdict:** meets "extend" but not "escalating".

### Option B: per-attempt doubling, cap 24 h (the literal reading)

- **Lock rule:** every attempt during a lock adds 1 to the count and sets `locked_until = max(locked_until, now + min(15 × 2^level, 24 h))`. Steps: 15 m, 30 m, 1 h, 2 h, 4 h, 8 h, 16 h, then 24 h (cap).
- **Reset rule:** success or a completed reset clears it. A quiet decay must be longer than the cap (for example, 7 days without a lock); otherwise waiting out the cap would refill 5 guesses.
- **Attacker, 1 IP:** reaches the cap in 8 requests (seconds), then **1 request/day keeps the account locked**. Evaluated guesses: 11 on day 1, about 375/year.
- **Attacker, many IPs:** no gain against one account. **One IP can keep 14,400 accounts locked**, which is every school's users.
- **Real owner:** 5 typos plus 2 impatient retries is a 1-hour lock; plus 7 retries is 24 h. Their way back is a reset.
- **Verdict:** strongest against guessing, worst for DoS reach and for the honest user.

### Option C: per-lock doubling with slide, cap 4 h (RECOMMENDED; cap is the owner's pick)

- **Lock rule:** attempts during a lock only slide, `locked_until = max(locked_until, now + step(level))`, and do not count. Each new lock after an expiry steps up a level: 15 m, 30 m, 1 h, 2 h, then 4 h (cap).
- **Reset rule:** success or a completed reset clears it. Quiet decay: if `locked_until` is more than 24 h in the past, the next failure starts again from 0. Every evaluated wrong guess after the first lock re-locks, so a stale lock means 24 h with no failure. Counts of 1-4 below the threshold behave as today.
- **Attacker, 1 IP:** 6 requests/day keeps the account locked. Evaluated guesses: 14 on day 1, about 2,200/year. With decay, waiting 24 h only buys 5 guesses, fewer than the 6 the cap already allows.
- **Attacker, many IPs:** no gain against one account. One IP can keep 2,400 accounts locked.
- **Real owner:** at most 4 h per lock, and retries only push the current step. A reset clears it at once.
- **Other caps:** with a 1 h cap: 24 requests/day, 30 guesses on day 1, about 8,800/year, 600 accounts per IP. With a 24 h cap the numbers equal B's for guessing, but retries do not escalate.

### Numbers side by side (scratch lockmath.py; per account; login throttle 10/min per IP)

| Option | Guesses, day 1 | Guesses/year | Requests/day to hold locked | Accounts one IP can hold |
|---|---:|---:|---:|---:|
| today | 101 | 35,045 | 96 | 150 |
| A | 101 | 35,045 | 96 | 150 |
| B (24 h) | 11 | 375 | 1 | 14,400 |
| C (4 h) | 14 | 2,198 | 6 | 2,400 |
| C (1 h) | 30 | 8,766 | 24 | 600 |

Passwords pass Django's default validators: length >= 8, the 20k common-password list, not all-numeric, not similar to the user's attributes.

### The real owner's way back (all options)

A **completed password reset clears the login lock and count** in the same save that sets the new password. This fixes F1 and F2.

Why this is not a hole:

- The reset needs the 6-digit code mailed to the account's address, so the caller must control that mailbox.
- The code has its own per-account budget (5 wrong codes, then 30 min), and a new code request does not refill it.
- The reset already issues tokens today, so clearing the login lock grants no access the reset did not already grant. It only stops a later password login from being refused.

Optional, as questions for the owner:

- A successful change_password and a successful Google sign-in also clear the lock.

Residual risk the owner must see:

- An attacker can also deny the reset (F6), at 288 requests/day, and each cycle emails the victim a code.
- Someone doing both locks out a password-only user completely. Their only ways back are Google sign-in (if the address is a Google account) or an operator with a shell (F4).
- Options to close that are separate decisions, not in H-34: an admin "unlock" action, or a per-account cap on reset-code requests that emails the owner.

## 3. User-facing wording (questions; nothing will be added without approval)

- **W1** The ACCOUNT_LOCKED remediation "Wait a few minutes and try again, or reset your password." becomes untrue once a lock can last 1 h, 4 h or 24 h. Does the owner want new text? I am not proposing any text without a request.
- **W2** The lock message "Too many failed sign-in attempts. Please try again later." stays true. Proposal: keep it unchanged.
- **W3** docs/GRADE_A_PLUS_REASON_CODES.md (developer docs) says 15 minutes in three places, and its message line says "login" where the code says "sign-in" (existing drift). Is a docs update allowed in the same change?
- **W4** Not wording, but frontend-facing: should the login lock send `locked_until` / Retry-After like RESET_LOCKED? Proposal: **no**, because it tells a guesser exactly when the next evaluated guess lands.

## 4. Count pins, tests-first plan, plants, size

### Count-pin survey (assertions that pin numbers these changes could move)

- tests_login_lockout :149 (count == 5 after 5 wrong) and :188 (expiry clears): unchanged under A and C. Under B they are also unchanged, because the assertions come before any locked attempt.
- Concurrency and live server (`>= 5`, `<= WORKERS`): hold under every option.
- tests_auth_audit_events :103 (one DENIED event, no password check, 30-min lock pre-set): Greatest keeps the 30 min. Unchanged.
- tests_h274 :76 sets `locked_until` with count 0. The level must clamp at 0 (`max(0, ...)`), or the step goes negative. This will be pinned by a test.
- AutoGrader.tests_cache_user_fanout :450/:552 pin that lock-field writes do not fan out. Every write is a queryset `.update()`, so no save signal fires. Unchanged.
- audit.tests_history_guard bans `.update(**…)`, so static keywords only.
- AutoGrader.tests_reason_codes (61) moves only if W1 is approved.
- No migration, so tests_migration_rollback_defaults and the migration-safety check are unaffected.

### Tests first (new module users/tests_h34_lockout_extension.py; time mocked; red before the fix)

| Test | Checks | Before the fix |
|---|---|---|
| T1 | An attempt 5 min into a lock moves `locked_until` to now + step. | red |
| T2 | Successive re-locks give 15, 30, 60, 120, 240, 240 min (C). | red |
| T3 | The cap holds after many cycles. | red |
| T4 | The correct password while locked is refused, `check_password` is never called, and the lock slides. | slide part red |
| T5 | A completed reset clears the count and lock, and an immediate password login with the new password succeeds. | red |
| T6 | Decay: `locked_until` more than 24 h old, then a wrong guess gives count 1 and no lock; at 23 h 59 m a wrong guess re-locks at the next level. | red |
| T7 | Level clamp: count 0 with a lock set slides by 15 min. | red |
| T8 | Concurrent burst on a locked account: `locked_until` never decreases, and the count is unchanged (C). | red on slide |
| T9 | The locked 401 body is unchanged, with no `locked_until` key (pins W4). | green pin |

### Plants (one per run, with the kill declared first; restore from a saved copy and check the sha256)

| Plant | Mutation | Killed by |
|---|---|---|
| P1 | Greatest() replaced by a plain set | T8 |
| P2 | Cap removed | T3 |
| P3 | Reset no longer clears | T5 |
| P4 | Slide from `locked_until` instead of now (compounding) | T1 |
| P5 | `check_password` called in the locked branch | T4 and tests_auth_audit_events:103 |
| P6 | Decay boundary 24 h changed to 0 | T6 |
| P7 | Level clamp removed | T7 |

### Near (beta)

- `users` (982 before new tests) plus the 2 classrooms guard labels.
- Then the 56-label serial sweep (1023).
- Beta has 60 of the 67 guard labels; 7 are staging/B1b-only.
- Exact counts will be declared at build.

### Size

- Product: about 40-60 lines across users/models.py (constants, a step function, register_failed_login, a new slide method), users/serializers.py (the locked branch calls slide) and users/views.py (reset clears two fields).
- Tests: about 200-250 lines in one new module.
- Docs: 3 lines if W3 is allowed; plus 1 line and a catalogue test if W1 is approved.
- No migration.
- About one working day including the plants, the near run and the beta→staging merge.

---

## Owner ruling (2026-11-11, relayed by the SM grade-automator-plus-d6; recorded unamended)

> 1. NO lockout extension. Keep today's behavior (5 tries, 15 min) and today's messages unchanged. Options A/B/C are dropped. Do not build F1/F2/F3 either; they are not approved. Note them as open findings.
> 2. Internal notes: approved (W3). Update the developer docs to match today's behavior exactly, including fixing the login/sign-in drift you found. Docs only, no user-facing text.
> 3. NEW, design first: an admin UNLOCK and an admin LOCK for an account. The owner wants:
>    - unlock: clears the failed count and the login lock;
>    - lock: an admin stops an account from signing in, and the user sees a message explaining it. I am drafting that message for the owner now; do not invent any wording.

### Effect on this file

- Sections 2 and 4 above (options A/B/C, T1-T9, P1-P7) are **dropped**. They stay above as the record of what was proposed.
- **Open findings, not approved and not built:**
  - F1: a reset does not clear the login lock.
  - F2: the reset's full save can lose a concurrent failure.
  - F3: Google sign-in neither checks nor clears the lock.
- **Settled by item 3:**
  - F4 (no operator unlock) is covered by the admin UNLOCK.
- **Still open:**
  - F5 (existence oracle, deliberate under H-274).
  - F6 (reset recovery denial).

## Item 2: developer docs (W3, approved)

docs/GRADE_A_PLUS_REASON_CODES.md now describes today's behavior exactly. It is docs only; no product file or user-facing text changed, and no test reads this file.

- **Line 410:** the message is now "Too many failed sign-in attempts. Please try again later.", the exact text in reason_codes.py:371 and serializers.py:490. It said "login" before.
- **Line 414 notes:**
  - The count is per account.
  - During a lock, every attempt gets this answer, even with the correct password. The password is not checked, the attempt is not counted and the lock is not extended.
  - Only a successful password sign-in clears the count. The lock running out, a password reset and Google sign-in leave it in place, so the next wrong password after a lock runs out locks the account again.
- **Line 812:** "15 minutes from the fifth wrong password; attempts during the lock do not extend it."
- **Line 877:** the count stays until a successful password sign-in.

## Item 3: admin LOCK / UNLOCK (DESIGN ONLY, no product edit)

### 3.1 What exists (origin/beta bcc50d83c)

**Back office (Django admin), users/admin.py:**

- `activate_users` (:117) is `history.record_bulk(queryset, is_active=True)` and writes one PERMISSION_CHANGE per changed user, naming the admin.
- `deactivate_users` (:126) is a single record_bulk that sets `is_active=False` and, only for rows active at that moment, raises `token_epoch` (H-202, which revokes every session). Both actions call `invalidate_user_caches`.
- `save_model` (:146) revokes sessions when the edit form switches is_active off.
- The admin does not show `failed_login_attempts` or `locked_until`. **Nothing can clear a lock** except a successful password login or a shell.

**What a deactivated user sees today:**

| Door | Answer |
|---|---|
| Password login | The generic 401 `invalid_credentials` "That email and password don't match. ...". This is the same as a wrong password, and the correct password is not counted as a failure (tests_login_lockout:217). There is no deactivation message. |
| Google sign-in, verified account (users/views.py:2370) | 401 `account_deactivated` "This account has been deactivated. Please contact support.", audit ACCOUNT_DEACTIVATED. |
| Google sign-in, never-verified inactive account | **Switched ON**. Google proves the mailbox, which is the H-164/H-203 completion road. |
| Password reset (views.py:1484) | Generic 400, the attempt spent, audit ACCOUNT_DEACTIVATED denied. |
| OTP request | Neutral. |
| Per-request auth (users/authentication.py:60) | Rejects a token whose epoch is stale, and SimpleJWT rejects an inactive user. |

**`is_active` already carries three meanings:**

- a self-registered account not yet verified;
- an invited school admin who has not activated yet;
- an account deliberately switched off (verified + inactive).

Two mailbox roads switch the first two ON: /auth/verify and Google. It is also shown on other users' views (rosters, the school summary; see the comment at admin.py:111) and used by roster import (ROW_ACCOUNT_DISABLED).

**Super-admin API:**

- `CustomUserViewSet` (users/views.py:280): list/create/destroy need `IsSuperAdmin` (classrooms/permissions.py:69, which needs both is_superuser and user_type SUPER_ADMIN). IsSuperAdmin is also the automatic ADMIN_ACTION audit hook: a denial is emitted at once, and a grant is finished by AdminActionAuditMiddleware as exactly one event naming the requester ("its named one, or this").
- PATCH lets a super admin edit anyone. `is_active` is read-only for everyone (serializers.py extra_kwargs). **There is no API that activates, deactivates or unlocks a user.**

**School admins today:**

- They can **see** their own school's users plus students enrolled under their school's teachers (get_queryset).
- They **cannot edit** anyone but themselves (partial_update, "You can only modify your own account.").
- Their only management power over people is the license: add/remove teachers (billing/license_views.py:396), which moves a seat and does not stop sign-in.
- Students have `school = NULL` and are reached only through enrollments, so one student can be under several schools.

### 3.2 Design: a NEW state, not a reuse of is_active (recommended)

**New column:** `CustomUser.admin_locked_at`, a nullable DateTimeField. "Locked" means it is not null.

- No locked-by column: who did it is in the audit event, and leaving it out avoids an FK index build on users_customuser.
- One nullable AddField is an expand-safe migration, needs no db_default and does not touch the rollback-defaults list.

**Why not is_active:**

1. **Mailbox roads would undo it.** /auth/verify and Google switch a never-verified inactive account ON (views.py:2350-2370). A lock stored in is_active on such an account is undone by anyone who controls the mailbox, which is exactly the person being locked out.
2. **The owner wants a message.** Deactivated accounts deliberately get the generic "don't match" answer at password login. Showing a message through is_active would change the answer for every pending and switched-off account too.
3. **is_active has side effects a lock should not have.** It changes how the user shows on rosters and the school summary, and roster import refuses them (ROW_ACCOUNT_DISABLED). A lock should stop signing in and nothing else; the student's work, grades and enrollments stay as they are.
4. **Unlock is exact.** Clearing `admin_locked_at` cannot switch on a pending registration by accident.

**Behavior of the lock:**

- **Locking:** one UPDATE sets `admin_locked_at = now` and `token_epoch = F()+1`, which revokes every live session and refresh token at once. The epoch is checked on every request (authentication.py:60) and on refresh (tokens.py:48). Then `invalidate_user_caches`.
- **Indefinite:** it lasts until an admin unlocks. There is no expiry.
- **Every door refuses a locked account.** Today's token-issuing roads (the FOR_USER sites pinned by users.tests_roads_that_sign_in_or_activate) are listed below. Each would be a separate decision for the owner:

| Door | Proposed answer |
|---|---|
| POST auth/login (serializers.py:511, beside today's ACCOUNT_LOCKED check) | Refused **before** the password is checked, with the owner's message and a new code. The password is not checked and nothing is counted. |
| Google sign-in (views.py:2476) | Refused with the same code and message. It does not switch the account on. |
| /auth/verify (views.py:1106) and invited-admin activation (views.py:2104) | Refused, with the generic answer each already gives. |
| POST auth/otp, RESET_PASSWORD | Neutral reply, **no code mailed**. |
| POST auth/reset-password (views.py:1547) | Refused with the generic 400, no password set, the lock unchanged. |
| change-password (views.py:1701) | Needs a live session, which the epoch bump already killed. |
| Refresh | Dead (epoch). |
| Per-request auth (MustChangePasswordJWTAuthentication) | **Also rejects a locked user**, as a backstop, so a token from any road added later is useless. The user row is already loaded there, so the check costs nothing extra. |

**Why the login check comes before the password check:** this matches today's ACCOUNT_LOCKED.

- If the message came only after a correct password, the message itself would confirm a guessed password.
- Before the password check, the message tells anyone who types the address that the account exists and is locked. That is the same exposure ACCOUNT_LOCKED already has (F5, deliberate under H-274).
- **Question Q1 for the owner:** the alternative is the generic "don't match" answer for a wrong password and the message only for the correct one. That is safer for existence but confirms passwords.

**UNLOCK clears, in one UPDATE:** `admin_locked_at = None`, `failed_login_attempts = 0`, `locked_until = None`.

- Unlock is the owner's way out for both kinds of lock.
- It does not touch is_active, so it never activates a pending or deactivated account.
- It does not reset the epoch; the old sessions stay dead.

### 3.3 Who may lock or unlock

Rules for both options:

- **Nobody may lock themselves.**
- **Nobody may lock a super admin** (is_superuser or user_type SUPER_ADMIN), so a super admin cannot lock another super admin either.
- Unlock follows the same scope as lock, so self-unlock is impossible anyway: a locked user has no session.

**(a) Super admin only (RECOMMENDED for the first build):**

- Two back-office actions on CustomUserAdmin, "Lock sign-in" and "Unlock sign-in". Their labels are internal back-office text; I will ask whether they count as wording.
- Two API routes on CustomUserViewSet: `POST users/{id}/lock/` and `POST users/{id}/unlock/`, permission `IsAuthenticated, IsSuperAdmin`. IsSuperAdmin gives the ADMIN_ACTION hook and the denied event for free.
- A read-only field on the user serializer, shown to super admins only: `sign_in_locked` (bool), plus perhaps `login_locked_until` so support can see a 5-try lock. Q3.
- Cost: about 150-200 product lines and about 350 test lines.

**(b) Also school admins, for their own school's teachers and students.** Extra cost:

- **Scope.** Teachers are easy (`school_id == admin.school_id`). Students carry no school; they are reached only through enrollments under the school's teachers.
- **Multi-school students.** A student enrolled under two schools would be locked out of **both** by one school's admin. This needs a rule, either:
  - refuse when the student has an enrollment under another school; or
  - allow it and tell the other school.

  This is a new owner decision.
- **Peer locks.** A school admin must not lock another school admin. Proposal: teachers and students only.
- **More surface.** The same two routes with a second permission path (IsSchoolAdmin plus an object scope check), new denial tests for every role and cross-school pair, the school's activity log showing the event (`school_id` on the event), and new frontend screens for school admins.
- **Interaction with the license.** A locked teacher still holds a seat. Does the school admin expect locking to free it? Q4.
- Cost: about 60-80 more product lines and 150-200 more test lines.

**Recommendation:** build (a) now; (b) can follow as its own step once the multi-school rule is decided.

### 3.4 Audit, reason code, envelope, frontend

**Audit (recommended):** two new AuditAction values, `ACCOUNT_LOCK` and `ACCOUNT_UNLOCK`.

- `actor` = the admin, `target` = the user, `school_id` = the user's school.
- Metadata on unlock: `{had_admin_lock, had_login_lock, cleared_failed_attempts}`.
- `AuditEvent.action` is a free CharField with no choices, so this needs no migration.
- On the API route the named event is the one event naming the requester, and ADMIN_ACTION steps aside, as the middleware already does ("its named one, or this").
- Why not reuse PERMISSION_CHANGE by adding `admin_locked_at` to the CustomUser Tracked fields:
  - unlocking a 5-try lock alone changes no tracked field, so it would leave no event;
  - counters are excluded from history by design (history.py:124).
- The back-office actions emit the same two events. activate/deactivate keep their PERMISSION_CHANGE.

**Locked-login refusal:**

- AUTH_LOGIN, outcome DENIED, actor anonymous (as today's failure_actor), target the user.
- A new reason code. Suggested internal name `ACCOUNT_SUSPENDED`, or `ACCOUNT_LOCKED_BY_ADMIN`; the name is a frontend contract, so this is Q2.

**Envelope:**

- HTTP 401 via `EnvelopedAuthenticationFailed`, like ACCOUNT_LOCKED: `detail` plus the envelope (reason_code, error_class USER, remediation, **retryable false**, params {}, reference).
- `code` equals the reason code, with no legacy lower-case value, since this code is new.
- The message and remediation are **the owner's text, being drafted by the SM**. No wording is in this design.
- A new ReasonSpec in AutoGrader/reason_codes.py.
- A new section in docs/GRADE_A_PLUS_REASON_CODES.md.

**Frontend needs (no frontend work by us):**

1. The sign-in screen and the Google sign-in callback: handle the new reason code on 401, show `message`, and do not offer "try again later".
2. Super-admin user screens: `POST users/{id}/lock/` and `POST users/{id}/unlock/` (200 with the updated user, 403 for others, 400 for self or super-admin targets with the reason code from Q5), and the read-only `sign_in_locked` field.
3. Nothing changes for deactivate.

### 3.5 Questions for the owner (nothing will be built or worded without answers)

- **Q1** Show the lock message before the password check (an existence leak, the same as today's ACCOUNT_LOCKED), or only after a correct password (confirms passwords)? Recommend: before.
- **Q2** The reason code name, and the message and remediation text (SM drafting).
- **Q3** Show super admins `sign_in_locked` and the 5-try `login_locked_until`?
- **Q4** (b) only: multi-school students, and whether a lock frees a license seat.
- **Q5** The refusal texts for "you can't lock yourself" and "you can't lock a super admin" are new wording. Use the existing generic PermissionDenied texts instead?
- **Q6** Do the back-office action labels ("Lock sign-in" / "Unlock sign-in") need approval?
- **Q7** Reset and Google answers for a locked account: the generic reset answer, and the lock message on Google (proposed)?

### 3.6 Tests first (new module users/tests_admin_sign_in_lock.py), plants, pins, size

**Red-first tests (scope (a)):**

| Test | Checks |
|---|---|
| L1 | Lock sets `admin_locked_at` and raises token_epoch once; an access token from before the lock now gets 401 on users/me; its refresh token is refused. |
| L2 | Locked login, correct and wrong password: the new code, `check_password` never called, `failed_login_attempts` unchanged, one AUTH_LOGIN DENIED event. |
| L3 | Google sign-in on a locked verified account and a locked never-verified one: refused, and the account is **not** switched on. |
| L4 | The RESET_PASSWORD OTP request is neutral and mails nothing; reset-password with a valid existing code is refused, sets no password and keeps the lock. |
| L5 | /auth/verify and invited-admin activation are refused for a locked account. |
| L6 | Backstop: a token minted directly for a locked user (EpochRefreshToken.for_user) is refused by the per-request auth class. |
| L7 | Unlock clears `admin_locked_at`, `failed_login_attempts` and `locked_until`; login works at once; `is_active` is untouched (a pending account stays inactive). |
| L8 | Unlock of a 5-try lock alone (no admin lock) clears it and writes ACCOUNT_UNLOCK with `had_login_lock`. |
| L9 | Rules: lock self gives 400; lock a super admin gives 400; school admin, teacher and student callers get 403 with the IsSuperAdmin denied event; unknown id gives 404. |
| L10 | Audit: exactly one ACCOUNT_LOCK / ACCOUNT_UNLOCK naming the requester, with the right target and school_id, and no extra ADMIN_ACTION. |
| L11 | Back-office actions: same effects and events; the lock never applies to super admins in the selection or to the acting admin; caches invalidated. |
| L12 | Pin, green before and after: a deactivated user's login answer is unchanged (generic invalid_credentials). |
| L13 | Pin: the lock does not change roster or enrollment state. |

**Plants (one per run, kill declared first):**

| Plant | Mutation | Killed by |
|---|---|---|
| Q-P1 | No epoch bump | L1 |
| Q-P2 | Lock check after authenticate | L2 |
| Q-P3 | No check on Google | L3 |
| Q-P4 | No check on reset | L4 |
| Q-P5 | No backstop in the auth class | L6 |
| Q-P6 | Unlock leaves the failed count | L7 |
| Q-P7 | Super-admin target allowed | L9 |
| Q-P8 | Self-lock allowed | L9 |
| Q-P9 | Unlock sets `is_active=True` | L7 |

**Count pins and guards that will move or must be satisfied:**

- users.tests_roads_that_sign_in_or_activate: pins every FOR_USER / ACTIVE_TRUE / SET_PASSWORD site with a count. The design adds no such site; unlock writes no is_active. The guard must stay green unchanged.
- audit.tests_route_coverage: the two new write routes must each leave exactly one event naming the requester in its superadmin sweep. They are covered by the named events, with no registry entry needed unless the sweep's empty-body call returns before the event; checked at build.
- AutoGrader.tests_reason_codes (61): a new ReasonCode and spec. The catalogue tests will count it; its docs row too.
- audit.tests_history_guard: no `.update(**…)`; static keywords only.
- AutoGrader.tests_cache_user_fanout: the lock changes nothing other users display unless Q3 shows `sign_in_locked` to admins; then the caches are invalidated as activate does.
- AutoGrader.tests_migration_rollback_defaults (12): unaffected (nullable column).
- scripts/check_migration_safety.py: a nullable AddField. Expected to pass with no acknowledgement; run before and after as in H-287.
- Existing lock tests (tests_login_lockout 13, tests_auth_lock_envelope 7, tests_h274 6, tests_auth_audit_events 9): unchanged, with L12 as an extra pin.

**Near (beta):**

- `users` (982 now) + AutoGrader.tests_migration_rollback_defaults + `audit` route coverage + AutoGrader.tests_reason_codes + the 2 classrooms guard labels.
- Then the 56-label serial sweep (1023).
- New counts will be declared at build.

**Size:**

- (a): about 150-200 product lines (model field and migration, `lock_sign_in`/`unlock_sign_in` on the model, 2 admin actions, 2 API actions, checks at login, Google, verify, activation, OTP and reset, the auth-class backstop, 2 audit actions, 1 reason code and docs).
- Tests: about 350-400 lines.
- About 1.5-2 working days including the plants and both nears.
- (b) adds about 1 day.

## SM rulings on the admin LOCK/UNLOCK design (2026-11-11, grade-automator-plus-d6; recorded unamended)

> H-34 admin lock/unlock design accepted in shape (a4ca07996; docs fix 17f608a4c stays on this branch, approved under W3). Keep HOLDING: the owner still has to answer who gets the buttons, the message, and the back-office labels.
>
> SM rulings (technical):
> - Design: NEW state admin_locked_at (nullable, expand-safe), not is_active. Approved for your reasons a-d.
> - Q1: the lock answer comes BEFORE the password check, consistent with ACCOUNT_LOCKED today. No count is kept during the lock.
> - Q2: reason code ACCOUNT_LOCKED_BY_ADMIN. The message text is the owner's (pending).
> - Q3: yes. Read-only sign_in_locked and login_locked_until for super admins (back office and the user detail API). Field names are not user wording.
> - Q5: no new wording. Self and super-admin targets get the existing generic PermissionDenied answer.
> - Q7: reset stays generic. Google sign-in for a locked account gets the same lock answer as password login (consistent with account_deactivated there).
> - Audit: new ACCOUNT_LOCK and ACCOUNT_UNLOCK actions, as proposed.
> - Plan, plants Q-P1..9, guards and near as proposed. Count-pin survey before tests-first.
> Waiting on the owner for: (a) super admin only vs (b) also school admins (and, if b, the multi-school and seat rules), the two lock messages, and the labels "Lock sign-in" / "Unlock sign-in". I'll relay. No product edit until then.

### Still open (owner)

- Scope (a) or (b). If (b), the rule for multi-school students and for license seats.
- The lock message and remediation text.
- The back-office labels.

State: HOLDING. No product edit.

## Owner answers (2026-11-11, relayed by SM grade-automator-plus-d6; recorded unamended)

> SM: owner answers on H-34 (2026-11-11). Record them unamended.
> - Scope (a): super admin only. The back-office actions plus POST users/{id}/lock/ and /unlock/ (IsSuperAdmin). There is no school-admin route.
> - Both lock messages APPROVED, verbatim, from team/approved_wording_h34_admin_lock_2026-11-11.md (main checkout):
>   1 (user with a school): "An administrator has locked your account. Please contact your school administrator to unlock it."
>   2 (no school): "An administrator has locked your account. Please contact the Grade A Plus team to unlock it."
>   Students have no school field. In your survey, propose which message a student gets (for example, by whether any enrollment exists) and I will rule. No third text.
> - Labels "Lock sign-in" / "Unlock sign-in": still pending with the owner. Build with them, but STOP before the product commit if they are not confirmed by then.
> GO: count-pin survey, then tests-first (L1-L13), red, product, green, plants Q-P1..9, migration safety before and after, near as declared. Checkpoint to grade-automator-plus-d6 at the survey (with the student-message proposal) and at the end. No push. It goes to beta with the owner's go.

Wording file read: the main checkout's team/approved_wording_h34_admin_lock_2026-11-11.md (owner quote there: "Only super admin get the locks" / "I approve").

State: GO for the survey. Labels still pending, so STOP before the product commit unless they are confirmed by then.

## Labels approved (2026-11-11, SM relay; recorded unamended)

> SM: the owner APPROVED the Django admin action labels "Lock sign-in" and "Unlock sign-in" (2026-11-11). They are added as texts #3 and #4 in team/approved_wording_h34_admin_lock_2026-11-11.md. The label STOP before the product commit is lifted. Use them verbatim. Carry on as briefed: survey checkpoint (with the student-message proposal) first.

## Count-pin survey (before tests-first)

What the change will move, and which guard forces each move:

| # | Pin | Today | After | Forced by |
|---|---|---|---|---|
| C1 | `audit.enums.AuditAction` | - | +2: ACCOUNT_LOCK, ACCOUNT_UNLOCK | SM ruling; `tests_enums` (value == name, <= 64 chars) |
| C2 | `METADATA_ALLOWLIST` | - | +2 entries (one per new action) | `audit.tests_metadata.PerActionAllowListTest.test_every_action_has_an_entry` |
| C3 | `ALLOWED_KEYS` | - | +3: had_admin_lock, had_login_lock, cleared_failed_attempts (unlock); lock uses the existing `source` | `test_every_per_action_key_is_also_in_the_shared_pool` |
| C4 | `audit.enums.ReasonCode` | - | +1: ACCOUNT_LOCKED_BY_ADMIN | emitter refuses unknown codes; `AutoGrader.tests_reason_codes` emitted-literal scan |
| C5 | `REASON_CODES` | - | +1 user-facing spec (not audit-only) | `test_every_code_is_user_facing_or_audit_only_and_not_both` |
| C6 | users migrations | leaf 0041_revokedsession | 0042, AddField admin_locked_at NULL | nullable, so no db_default needed (`tests_migration_rollback_defaults` checks NOT NULL only); nothing pins the leaf name |
| C7 | write routes | - | +2: `user-lock`, `user-unlock` (router basename "user") | `audit.tests_route_coverage`: anonymous gets 401; a super admin with a made-up id gets 404, so one ADMIN_ACTION; no registry entry needed |

Checked, expected NOT to move:

- `users.tests_roads_that_sign_in_or_activate`: no new FOR_USER, ACTIVE_TRUE or SET_PASSWORD site. Unlock never touches is_active; the checks only refuse.
- `audit.tests_history_guard`: the lock UPDATE uses explicit keywords, not `.update(**...)`.
- `STUDENT_RECORD_ACTIONS` (pinned exactly): unchanged. Lock and unlock are GENERAL.
- `audit.E001` lock thresholds: the admin lock has no attempt threshold, so it is not added.
- `assertNumQueries`: none on login or the auth class in users/audit. admin_locked_at is read from the row already loaded, so 0 extra queries; the student-message lookup costs 1 query, on the locked branch only.
- `tests_volume_report` and the metadata loops iterate `AuditAction`, so they scale on their own.
- `tests_cache_user_fanout`: the lock writes by UPDATE (no save signal) and calls `invalidate_user_caches` explicitly.
- `CustomUserSerializer` for non-super-admins: no new keys. The two read-only fields appear only to super admins.
- ACCOUNT_LOCKED behavior and its pins (`tests_auth_lock_envelope`, `tests_auth_audit_events:103`, `tests_login_lockout`): unchanged.

### Points that need an SM ruling

- R1, the student message. Students have no school. Proposal: message 1 (school administrator) if the student has a non-WITHDRAWN enrollment in a course whose teacher has a school (`StudentCourse` with `course__teacher__school__isnull=False`, `enrollment_status != WITHDRAWN`), otherwise message 2 (Grade A Plus team). Reason: "any enrollment" would send a student of an independent teacher, or one who has only been withdrawn, to a school administrator who does not exist. Teachers and school admins: message 1 if school_id is set, else message 2. Super admins cannot be locked.
- R2, the remediation. To keep to no third text, the spec's remediation is "" (the body carries `remediation: null`, the existing "nothing to do" form). Both approved sentences already say what to do. The two messages are the spec's `message` (1, school) plus `alternative_messages={"no_school": <2>}`. The login and Google doors pick the variant themselves (no new param).
- R3, one event per request. IsSuperAdmin tags every granted request, and the middleware then ALWAYS writes ADMIN_ACTION (`audit/admin_action.py:92`). If the API view also writes ACCOUNT_LOCK with the admin as actor, a successful lock leaves TWO events naming the requester, which breaks the "exactly one event naming the requester" rule. No existing IsSuperAdmin view writes its own named event, so there is no precedent. Proposal: after a successful lock or unlock, the view writes ACCOUNT_LOCK/ACCOUNT_UNLOCK and removes the request's ADMIN_ACTION tag (a small helper in audit/admin_action.py), so the named event is the one. A refused or failed call (404, self, super-admin target) keeps today's ADMIN_ACTION. In the back office, each locked user gets one ACCOUNT_LOCK naming the admin, and the generic STATE_CHANGE is then skipped by the existing rule, as `deactivate_users` already does with PERMISSION_CHANGE.

## SM rulings on the survey (2026-11-11, grade-automator-plus-d6; recorded unamended)

> SM rulings on the H-34 survey (e01d76cda). Record them unamended. GO for tests-first.
>
> R1 APPROVED: message 1 if the user has a school (teachers and school admins), or, for a student, a non-WITHDRAWN enrollment in a course whose teacher has a school; otherwise message 2. Add tests for: a student with only an independent teacher; a student who was only ever withdrawn; a student with both kinds of enrollment (gets message 1).
>
> R2 APPROVED: remediation "" giving remediation null; message 2 as the spec's no_school alternative; the doors pick between the two. Pin both texts byte-for-byte against team/approved_wording_h34_admin_lock_2026-11-11.md (record the sha).
>
> R3 APPROVED (one named event, the ADMIN_ACTION tag dropped on success), with these conditions:
> - The drop helper may be called only AFTER the named ACCOUNT_LOCK/ACCOUNT_UNLOCK event is written in the same request. Structure it so the helper itself writes the named event and then drops the tag, never the drop alone.
> - A guard test: the helper's only callers are the two lock/unlock views (a static scan, like the history guard).
> - Route-coverage asserts exactly ONE event naming the requester on success, and today's ADMIN_ACTION on 404, self and super-admin targets.
> - Plants: (i) the tag drop removed, so two events, caught; (ii) the named event removed but the drop kept, so zero events, caught. The second matters most.
> - Back office: one ACCOUNT_LOCK per user changed, naming the admin. Users already locked or unlocked are not counted (match deactivate's "only rows that change").
>
> Then red, product, green, plants Q-P1..9 plus the two R3 plants, migration safety (vs origin/beta and origin/main), near as declared, with every declaration stating its env. Checkpoint to grade-automator-plus-d6. No push.

Wording source: the main checkout's team/approved_wording_h34_admin_lock_2026-11-11.md, sha256 d3d1a12e7b02be9dda85771b3cdd3a505ca2712b5e616c5b983bf9081a64e229 (read 2026-11-11; texts #1-#4). team/ is excluded locally (.git/info/exclude), so it is not in the worktree. The tests pin the texts as literals copied from that file, and the test module names this sha.

## Tests first (red), declared before the run

New tests, counted with build_suite().countTestCases():

- `users.tests_admin_sign_in_lock`: 57 (L1-L13, R1 sentence cases, R2 spec pins, R3 caller guard, back office, read-only fields).
- `audit.tests_route_coverage.AdminSignInLockRouteTests`: 3. This is R3's route-coverage condition; the label goes from 22 to 25.

Rulings applied in the tests:

- Self and super-admin targets get 403 with DRF's generic "You do not have permission to perform this action." This is SM Q5; the design's L9 said 400, and the ruling overrides it.
- Back office: no success message after the actions, so no new wording is needed. Only the approved labels are shown.

**Run R1 (red).**

- Env: worktree Grade-Automator-Plus-h34 at the commit holding these tests; venv ~/Documents/Virtualenvs/h284_deps_env; `--settings=settings_worktree --keepdb` (test DB test_h34_lockout_extension); under `flock ~/.machine-fullsuite.lock`.
- Labels: `users.tests_admin_sign_in_lock audit.tests_route_coverage.AdminSignInLockRouteTests`, 60 tests.

Prediction:

- 4 pass:
  - L6 `test_control_the_same_token_works_for_an_unlocked_account`;
  - ReadOnlyFields `test_nobody_else_sees_them`;
  - ReadOnlyFields `test_the_sign_in_payload_does_not_carry_them`;
  - L12 `test_a_deactivated_account_still_gets_invalid_credentials`.
- The other 56 fail or error. Causes: no `admin_locked_at` field (FieldError/AttributeError), no `user-lock`/`user-unlock` route (NoReverseMatch), no ACCOUNT_LOCKED_BY_ADMIN code (ValueError), no back-office actions, no helper.
- Nothing outside these labels is run.

**R1 result (red):** Ran 60. FAILED (failures=4, errors=60). Subtests are counted one by one: the 3 callers in `test_no_other_role_may_lock_or_unlock` and the 6 refusal cases in `test_refusals_leave_todays_admin_action`. By test, 57 did not pass and 3 passed: L6 control, ReadOnlyFields `test_nobody_else_sees_them`, and L12.

Prediction wrong/incomplete:

- `test_the_sign_in_payload_does_not_carry_them` was predicted to pass but errored with KeyError 'user'.
- Cause, a test defect: the renderer (users/renderers.py `api_response`) wraps success bodies as `{"success", "message", "data"}`. The test read `.json()`, where the existing tests read `response.data`.
- The same defect made `test_nobody_else_sees_them` pass vacuously; the keys were never in `.json()`'s top level.

Test-only fix, before any product edit:

- the success-path reads use `response.data` (3 places);
- the 4 `assertNotIn("access", ...)` checks read `response.data`;
- the two read-only-field tests now also assert `email` is present, so they cannot pass on an empty body.

**Run R1b (red, after the test fix f2d8c85f6).**

- Env and labels: the same as R1, 60 tests.
- Prediction, by test: 4 pass (L6 control; ReadOnlyFields `test_nobody_else_sees_them` and `test_the_sign_in_payload_does_not_carry_them`; L12). The other 56 fail or error, for the same causes as R1.

**R1b result (red):** Ran 60. FAILED (failures=4, errors=59; subtests counted one by one). By test, 56 did not pass and 4 passed: exactly the 4 predicted. Matches the prediction. Logs: 01_red.log.gz (R1) and 01b_red.log.gz (R1b).

## Product (uncommitted at declaration) and run R2 (green)

What was built:

- Model field `admin_locked_at` and migration 0042 (nullable AddField).
- `users/sign_in_lock.py`: `lock_users`, `unlock_users`, `may_lock`, `lock_message` (R1).
- Audit: ACCOUNT_LOCK / ACCOUNT_UNLOCK, plus the three unlock keys.
- Reason code ACCOUNT_LOCKED_BY_ADMIN, with its spec (R2).
- `audit.admin_action.emit_named_admin_event` (R3).
- The checks at login, Google, verify, the code request, reset and invited-admin activation.
- The auth-class backstop. It reuses the existing REVOKED_MESSAGE, so there is no new text.
- POST users/{id}/lock/ and /unlock/ (IsSuperAdmin).
- The back-office actions "Lock sign-in" / "Unlock sign-in", with no success message (no new text). The two lock columns are shown read-only.
- `sign_in_locked` / `login_locked_until` for super admins.
- The reason-code doc entry and the login endpoint's API description.

Audit reasons at the generic doors (internal, not shown):

- verify: ACCOUNT_LOCKED_BY_ADMIN failure, through its usual `refuse`;
- reset and invited-admin activation: ACCOUNT_LOCKED_BY_ADMIN, denied;
- Google: ACCOUNT_LOCKED_BY_ADMIN, denied.

Known limit, not built (F2 stays open per the owner): reset-password's full `user.save()` can write back a stale `admin_locked_at=None` if a lock lands between its read and its save, a window of milliseconds. The backstop does not cover that case. Recorded, not fixed.

**Run R2 (green).**

- Env: as R1, on the working tree (product uncommitted).
- Labels (counts from build_suite), 251 in total:

| Label | Count |
|---|---:|
| users.tests_admin_sign_in_lock | 57 |
| audit.tests_route_coverage | 25 |
| AutoGrader.tests_reason_codes | 61 |
| audit.tests_metadata | 28 |
| audit.tests_enums | 3 |
| users.tests_roads_that_sign_in_or_activate | 10 |
| audit.tests_history_guard | 13 |
| users.tests_auth_lock_envelope | 7 |
| users.tests_login_lockout | 13 |
| users.tests_switched_off_means_out | 25 |
| users.tests_auth_audit_events | 9 |

Prediction: Ran 251, OK.

**R2 result:** Ran 251. FAILED (failures=4): L10 `test_a_lock_leaves_one_account_lock_event_and_no_admin_action`, L8, and both L11 lock and unlock tests.

Prediction wrong/incomplete:

- Every failure was a test defect: `AuditEvent.target_id`, `actor_id` and `school_id` are UUIDs, and the tests compared them with `str(pk)`. Each failure shows the same UUID on both sides.
- The same defect made L2 `assertNotEqual(event.actor_id, str(target.pk))` vacuous; it could never fail.
- No product change. Test fix: the 7 comparisons use the pk itself. Database filters such as `target_id=str(...)` were already correct.
- Log: 02_green.log.gz.

**Run R2b (green).** Same env and labels as R2, 251 tests. Prediction: Ran 251, OK.

**R2b result:** Ran 251, OK. Matches the prediction. Log: 02b_green.log.gz.

- The run was on the working tree that became product commit 583859a30.
- The pre-commit hooks then only reformatted users/admin.py (black) and reordered imports in users/admin.py and users/views.py (isort). The plant runs below are on 583859a30 itself.

## Plants (declared together, before the first plant runs)

- One plant per run.
- Env: as R1, at product commit 583859a30.
- Labels: `users.tests_admin_sign_in_lock audit.tests_route_coverage.AdminSignInLockRouteTests` (60 tests).
- Each plant file is copied first, mutated, its sha256 shown changed, run, restored from the copy, and its sha256 checked equal to the committed file.
- "Killed by" lists the tests predicted to fail; every other test is predicted to pass.

| Plant | File | Mutation | Killed by (predicted) |
|---|---|---|---|
| Q-P1 | users/sign_in_lock.py | lock_users drops `token_epoch=F("token_epoch") + 1` | L1 `test_lock_stamps_the_account_and_raises_the_epoch_once`; L1 `test_its_refresh_token_is_refused`; L7 `test_unlock_does_not_revive_sessions_from_before_the_lock`; L11 `test_lock_changes_only_lockable_unlocked_rows_one_event_each`. Survives: L1 `test_an_access_token_from_before_the_lock_is_refused`, because the backstop still refuses it. |
| Q-P2 | users/serializers.py | the admin-lock block moves from before the 5-try check to after a successful password check | L2 `test_a_wrong_password_gets_the_same_answer_and_is_not_counted`; L2 `test_the_password_is_never_checked`; L2 `test_the_admin_lock_answers_even_during_a_five_try_lock` |
| Q-P3 | users/views.py | the Google admin-lock block is removed | L3 `test_a_locked_verified_account_gets_the_lock_answer`; L3 `test_a_locked_never_verified_account_is_not_switched_on` |
| Q-P4 | users/views.py | the reset-password admin-lock block is removed | L4 `test_a_valid_code_from_before_the_lock_sets_no_password` |
| Q-P5 | users/authentication.py | the backstop block is removed | L6 `test_a_token_minted_after_the_lock_is_refused` |
| Q-P6 | users/sign_in_lock.py | unlock_users drops `failed_login_attempts=0` | L7 `test_unlock_clears_both_locks_and_the_count`; L8; L11 `test_unlock_counts_only_rows_with_something_to_clear` |
| Q-P7 | users/sign_in_lock.py | may_lock and _lockable no longer exclude super admins | L9 `test_a_super_admin_cannot_lock_another_super_admin`, `test_a_superuser_flag_alone_makes_a_target_unlockable`, `test_a_super_admin_type_alone_makes_a_target_unlockable`; L11 `test_lock_changes_only_lockable_unlocked_rows_one_event_each`; route `test_refusals_leave_todays_admin_action` (super-admin subtests) |
| Q-P8 | users/sign_in_lock.py | may_lock and _lockable no longer exclude the actor | L9 `test_a_super_admin_cannot_lock_themselves`; L11 `test_lock_changes_only_lockable_unlocked_rows_one_event_each`; route `test_refusals_leave_todays_admin_action` (self subtests) |
| Q-P9 | users/sign_in_lock.py | unlock_users also sets `is_active=True` | L7 `test_unlock_never_switches_on_a_pending_account` |
| R3-i | audit/admin_action.py | the helper no longer drops the tag (two events) | L10 `test_a_lock_leaves_one_account_lock_event_and_no_admin_action`, `test_an_unlock_leaves_one_account_unlock_event_and_no_admin_action`; route `test_a_successful_lock_leaves_one_named_event`, `test_a_successful_unlock_leaves_one_named_event` |
| R3-ii | audit/admin_action.py | the helper writes no named event but still drops the tag (zero events) | the 4 R3-i tests, plus L8 (`AuditEvent.objects.get(action="ACCOUNT_UNLOCK")` finds none) |

### Plant results (at 583859a30; harness and logs in 03_plants.tar.gz)

Every file was restored, with its sha256 equal to HEAD after each run (the harness asserts this). `git status` was clean afterwards.

| Plant | Result | Failed | Matches? |
|---|---|---|---|
| Q-P1 | FAILED (failures=4) | the 4 predicted; L1 access test survived, as predicted | yes |
| Q-P2 | FAILED (failures=4) | the 3 predicted + L2 `test_one_denied_sign_in_event_names_the_account_as_target` | **Prediction wrong/incomplete**: the plant's moved block writes no AUTH_LOGIN event, so the audit test also fails. Killed. |
| Q-P3 | FAILED (failures=2) | the 2 predicted | yes |
| Q-P4 | FAILED (failures=1) | the 1 predicted | yes |
| Q-P5 | FAILED (failures=1) | the 1 predicted | yes |
| Q-P6 | FAILED (failures=3) | the 3 predicted | yes |
| Q-P7 | FAILED (failures=6) | the 4 predicted + 2 route super-admin subtests (lock and unlock) | yes |
| Q-P8 | **OK, survived** | none | **Prediction wrong.** See below. |
| Q-P9 | FAILED (failures=1) | the 1 predicted | yes |
| R3-i | FAILED (failures=4) | the 4 predicted | yes |
| R3-ii | FAILED (failures=4, errors=1) | the 4 predicted + L8 (error: DoesNotExist), as predicted | yes |

Q-P8 survivor explained:

- Under scope (a) the self rule cannot be reached. Every requester is a super admin (IsSuperAdmin on the routes; the back office is super-admin only), and every super-admin target is already refused by `is_super_admin_account`. So "self" is always refused by the super-admin rule first, and removing the self check changes no behavior.
- The self check stays as defense in depth (and for a scope (b) later).
- Option for the SM: a direct unit test of `may_lock` and `lock_users` with a non-super-admin actor would kill Q-P8. It is not added without a ruling, because it tests a case the routes cannot produce today.
