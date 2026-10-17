# Verification: H-164 verify road, a never-verified account with admin power gets no activation code and cannot be verified or signed in by /auth/verify @ f12f12d0

**Verifier:** 1a. **Author:** ed. **Date:** 2026-10-08. Beta line, on top of my verified `42742c40` (N3). No migration, no model, no serializer.
**Tip:** `task/h164-reset-for-an-invited-student` @ **f12f12d05fff16007a3621c895a94fa71f49a0e2**, docs only over `f6ce7d90` (a tests-only commit; 0 files outside `docs/` differ from it, checked); production `users/views.py` last changed at `1a20074d`. This delta closes my N1 of the N3 record.

The code change over `42742c40` is `users/views.py` only (12 lines): in the `otp` VERIFY_EMAIL branch, a never-verified account with admin power (`_holds_admin_power`: `is_staff`, `is_superuser` or type SUPER_ADMIN) is skipped (no code made, nothing sent, the reply is the unknown address's 202); in `/auth/verify`, such an account is refused through `refuse("Invalid email or token.")` right after it is found and before any write, so the attempt is spent and the address can lock.

**I ran at f12f12d0** (2026-10-08, 19:50:33 to 19:53:47 WAT, one chain `h164v_run.sh all`, 0b's GRANT and GO), from my own detached scratch checkout, serial, each run once, inside one `systemd-inhibit`, the 6G scope with `MemorySwapMax=0`, `nice -n 10 timeout -k 60 1800`, `PYTHONDONTWRITEBYTECODE=1`, `python -B`, own settings and test databases, each run's output to its own file, stdin from `/dev/null`, load 1.54 before. Under rule 15 I cite ed's gates (step 0 Ran 50 with twelve red each for its written reason; modules and guards Ran 510 OK; 31 mutants, 31 killed after the added lock test) and repeat none. The users-app regression is ed's, once, after this verdict.

**Verdict: VERIFIED-WITH-NOTES.** The verify road I found (N1) is closed for a never-verified account with admin power, at both ends, and nothing else I looked at changed: an ordinary inactive user and a licence-invited teacher are still activated or verified by a code. My tests and mutants match the sets I wrote beforehand, and each red test carries its written reason (rule 22). The first note is the next road around the same refusal: Google sign-in.

## What I checked by reading
- **The two arms** are as the hand-over says. The request skip tests `user.email_verified_at or not _holds_admin_power(user)`; the verify arm tests `not user.email_verified_at and _holds_admin_power(user)` and calls `refuse`, which, once the budget is spent, locks the address (`lock_verify_address`) and then raises the same 400 as a wrong code.
- **ed's lock test** (`test_a_refused_verify_on_an_admin_power_account_locks_the_address_like_a_wrong_guess`): I agree with its reasoning. It reads the lock key itself after the second refusal; a bare `raise` never sets it ("unexpectedly None"). My x2 does the same with the real limit.
- **Other holders of an activation token:** a pending school admin (SCHOOL_ADMIN type, inactive, no staff or superuser flag) is not "admin power" by the helper, so its invitation flow is untouched; `classrooms/test_school_admin_otp_deadend` covers it and I did not run it. A verified admin gets "Email already verified." before the skip, unchanged. A verified AND switched-off account being re-activated by `verify` is H-202, not in this delta (ed says so; I did not look at it).
- **Google sign-in** (`google_auth`, the existing-account branch): an account that never verified is stamped verified and, if inactive, activated, then tokens are returned; there is no admin check. That is the note below.

## What I checked by running (at f12f12d0)
**My module** `users/tests_vf1a_h164_verify.py`, 7 tests (sha256 starts 5d4eb68f3fd167ca); written before the runs; expected sets and the per-test failure fragments (rule 22) in `h164v_expected_kills.txt` (c0b3716f22537b35). **x0** my `w1` kept as the "seen": a never-verified command-line superuser asks for a VERIFY_EMAIL code and sends it to `/auth/verify`, asserted to answer 202 with tokens (the old road), so it must now be RED; **x1a** the code request answers exactly like a real unknown address (status and bytes), stores no code, sends no mail; **x1b** a planted code is refused like a wrong code and nothing is written (email unstamped, still active, token unchanged); **x2** five refused verifies spend the budget and set the lock (read from the lock key) and the next is a 429; **x3** an ordinary inactive user still gets a code and is activated and signed in by it; **x4** a licence-invited teacher still gets a code and is verified by it; **x6** (a characterisation) Google sign-in for the same superuser address.

| Run | What | Result (the log's Ran line and named failures) | Written set |
|---|---|---|---|
| base | the tip | `Ran 7`, `FAILED (failures=1)`: x0 | x0 |
| old | `users/views.py` from `42742c40` (no verify-road change) | `Ran 7`, 3: x1a x1b x2 | x1a x1b x2 |
| Q1 | /auth/verify without the arm | `Ran 7`, 3: x0 x1b x2 | x0 x1b x2 |
| Q2 | the code request without its skip | `Ran 7`, 2: x0 x1a | x0 x1a |
| Q3 | the verify arm raises ParseError directly (no budget, no lock) | `Ran 7`, 2: x0 x2 | x0 x2 |
| Q5 | the verify arm refuses every never-verified account | `Ran 7`, 3: x0 x3 x4 | x0 x3 x4 |
| Q6 | the request skips every never-verified account | `Ran 7`, 3: x0 x3 x4 | x0 x3 x4 |
| Q7 | Google sign-in refuses a never-verified staff or superuser account | `Ran 7`, 2: x0 x6 | x0 x6 |
| hooks | `pre-commit run --from-ref 3f2ad13e --to-ref f12f12d0` | 18 passed, 0 failed | n/a |

- **Rule 22.** Before the runs I wrote the failure fragment each red test must carry in its own block. After the runs I read each failing test's own block in each log: all 19 fragments were present ("no code was issued" for x0 on the tip, Q1, Q3, Q5, Q6, Q7 and "the verify route answered 400" under Q2; "a code was stored" for x1a; "202 != 400" for x1b; "attempt 1" and "the address is not locked" for x2; "400 != 202" and "no code was issued" for x3 and x4; "google sign-in answered 401" for x6).
- **Every mutant was applied before its run** (abort 8 otherwise; "mutated sha differs: True" in each log) and **restored** from the commit's blob afterwards (compare True, 0 tracked changes); the old-code run restored too (its arm aborts if the verify arm is still present).
- **Rule 19.** Each test is shown able to fail and able to pass: x0 red on the tip (and green on the old code); x1a by the old code and Q2; x1b by the old code and Q1; x2 by the old code, Q1, Q3; x3 and x4 by Q5 and Q6; x6 by Q7 (and green on the tip, which is the finding below).
- **Where my written sets were wrong:** none in this chain; every set and every fragment matched on the first run.

## Notes
- **N1 (for the Senior Manager). Google sign-in still signs in a never-verified account with admin power. x6 is green on the tip:** a command-line superuser, asked for by its address through Google (Google says the email is verified), gets HTTP 200 with tokens, and the email is stamped verified. That is the same class as the verify road: a mailbox-holder gets in with no password. It is not in this diff (the Google branch is unchanged) and it was there before H-164. My Q7 shows a refusal there is a one-line sketch (not tested against the rest of the suite, not a proposal). Whether to close it is a decision for the Senior Manager and ed, in a row of its own, together with H-202 (verify re-activating a verified, switched-off account) if they like.
- **N2.** The refusal in `/auth/verify` for such an account is the same words as a wrong code; the code request answers the unknown address's 202. So neither step tells a caller the address has an admin-power account. The reset request still answers a 400 "Email not verified." for the same account (ed's Row A, H-198, known).
- **N3. Not shown:** production mail delivery, throttle keys behind a proxy; a pending school admin and a verified admin are covered by reading and by existing tests I did not run; the Django-admin screen for such accounts.
- **N4. Not done:** no run of ed's module, gate, mutants or regression (his, once); the users-app regression is his after this verdict; no whole-tree credential scan (one production file; the hooks' secrets check passed).

## Files of this check (sha256 prefixes; `~/Documents/Projects/GAP-1a-records/` unless noted)
- `h164v_probe_tests_vf1a_h164_verify.py` (5d4eb68f3fd167ca), `h164v_mutants_Q.py` (414cce709806705e), `h164v_expected_kills.txt` (c0b3716f22537b35), `h164v_run.sh.txt` (8e49dbdffbd03fad)
- `runs/h164v_f12f12d0.log` (be57475aa16dd019), `runs/h164v_old_code_f12f12d0.log` (5a5642ef9b902dc2), `runs/h164v_mutant_Q1,Q2,Q3,Q5,Q6,Q7_f12f12d0.log` (6 logs), `runs/h164v_hooks_f12f12d0.txt`
