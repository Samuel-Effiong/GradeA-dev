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
