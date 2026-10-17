# H-53: a per-address budget of attempts on POST /auth/verify

Branch `task/h53-verify-lock` off beta `e7e4bdf` (beta-bound; verifier 1a). Rebase onto the license-seat hotfix (`463e222`) once that is on beta. No migration.

## Defect
`/auth/verify` activates an account given its email and a 6-digit `activation_token`. The only limit was `VerifyEmailThrottle`: 5 an hour **per IP** (the DRF identity is X-Forwarded-For with `NUM_PROXIES=2`, else REMOTE_ADDR).

That lets someone who registered with another person's address guess that account's code from many IPs and activate it without ever seeing the email. The same applies to any account whose `activation_token` is still pending:
- a sign-up code (15 min, `users.services`);
- a student invitation code (24 h, `ACTIVATION_TOKEN_VALIDITY`).

**Reproduce-first** on beta `e7e4bdf`: 5 of the budget tests fail (`prefix_beta_e7e4bdf_failing.txt`).

## Fix
All of it is in `users/throttling.py` and `AuthViewSet.verify` / `otp` in `users/views.py`.

**Budget:**
- `VERIFY_EMAIL_MAX_FAILURES = 5` attempts per window, and `VERIFY_EMAIL_LOCK_SECONDS = 1800`. Both come from the environment.
- The attempts window starts at the first attempt. The lock lasts 1800 s from the attempt that spends the budget.

**What is counted:**
- the **address**, never the IP or the account: `sha256(email.strip().lower())[:32]` in the cache key, so the address itself is never stored;
- unknown addresses exactly like real ones, so a lock (a 400 ×5, then a 429) says nothing about whether an account exists.

**Order (the atomicity 1a asked about):**
1. The lock is checked.
2. **Then one attempt is reserved** (`reserve_verify_attempt`: cache `add` + `incr`). django-redis `INCR` is atomic, the same primitive as H-47's budget.
3. **Only then is the code checked.**

So however many requests arrive together, only 5 per window reach the code check. The 6th and later get 429, even with the correct code. My first draft counted failures *after* the check, which let a burst from many IPs pass the check before the lock existed. I caught that in self-review, before handoff.
- Tests: the deterministic `test_guesses_under_way_count_before_they_are_answered`, and the real-thread `VerifyEmailBurstTests` (20 simultaneous guesses → exactly 5 × 400 and 15 × 429).

**Spending the budget:**
- The attempt that reaches 5 without verifying (a wrong code **or** an expired one) sets the lock (`cache.add`, so the first one wins).
- While locked, `/auth/verify` answers 429 with `Retry-After`, a correct code included.
- `/auth/otp` VERIFY_EMAIL sends no new code, but gives the same 202 reply.

**Resets:**
- A successful verify refunds the budget (the counter is deleted).
- Otherwise the counter expires with its window. It always expires by the time the lock ends, because it started no later than the lock and lasts as long.
- Re-sending a code never resets anything.

**The stored code is NOT cleared on lock.** This differs from L2's reset budget:
- `activation_token` also holds student invitations (24 h) and school-admin invitations (7 d, `classrooms/serializers.py:829`). My first draft cleared it, which would have let anyone destroy an invitation with 5 wrong guesses from 5 IPs. I caught that in self-review.
- Without the clear, a 15-minute sign-up code is dead before the lock ends. `VerifyEmailLockDefaultTests` pins that the shipped lock (1800 s) outlasts it.
- A 24-hour invitation code gets at most 5 guesses per 30 minutes: 240 in its life against 10^6 codes.

**Fail-open:** if the cache errors, the budget logs at ERROR and lets the attempt through, so the per-IP throttle still applies. A cache outage costs the lock, never a sign-up.

**Logs:** the lock event is logged (`verify_email.locked`, with the limit) and contains no address, code or IP.

## Residual (accepted, stated)
- **An attacker can lock a real person's address** for 30 minutes at a time, with 5 requests from 5 IPs. They can repeat it. This is the same trade-off as L2: the lock is what bounds guessing. The invitation itself survives, and the register doors (`register/student`, `register/school-admin`) are not touched by this lock.
- **Window-boundary race:** if the counter expires between `add` and `incr`, `incr` raises ValueError and the fallback `set(key, 1)` can lose a concurrent count. At most one extra attempt at a window's edge, the same as H-47.

## Tests: `users/tests_verify_email_budget.py`
Every request comes from a different IP (the attack).
- 5 wrong codes lock the address; the correct code then gets 429 with the message and `Retry-After ≤ 1800`.
- The burst, both deterministic and with real threads.
- An expired code spends the budget too.
- A lock leaves the stored code (invitation) alone; the default lock outlasts a sign-up code.
- 4 wrong then the right code still verifies; success refunds the budget.
- An unknown address locks the same way.
- A re-sent code is neither sent nor usable while locked.
- After the lock, a new code verifies.
- One address's lock never affects another; address matching ignores case and whitespace.
- The budget fails open on a cache outage.
- A stored lock whose time has passed is no lock (`VerifyEmailLockDefaultTests`).

## Gates
| Gate | Result |
|---|---|
| Reproduce-first | beta e7e4bdf: 5 fail (`prefix_beta_e7e4bdf_failing.txt`) |
| 1 Regression | `users` app: **600 OK** (skipped=4), on the code before the last two tests (test-only additions) |
| 2 Mutation | `mutate.py`, 16 mutants, anchors asserted unique. First run **15/16**: M16 (`if until and until > time.time()` → `if until`) survived, because the locmem cache expires the key at the same moment. The check stays, since a Redis TTL is rounded to whole seconds and can outlive the recorded end; `test_a_lock_whose_time_has_passed_is_no_lock` now pins it. Re-run on the final code: **16/16 killed**, survivors `[]` (`mutation_log.txt`, `mutation_results.json`). M2, the first draft's count-after-check design, is killed by both the deterministic test and the real-thread burst test |
| mypy | whole-repo `pre-commit run mypy --all-files` → Passed |
| 3 Concurrency | real-thread burst (TransactionTestCase, 20 threads held at a barrier after the lock check): exactly 5 × 400 and 15 × 429; `users.tests_verify_email_budget` **15 OK** |
| 4 Adversarial | many IPs, case/space variants, unknown address, re-sent code, correct code while locked |
| 5 Failure | cache outage fails open (test) |

## N1 (1a's VERIFIED-WITH-NOTES at 7997dea; their record is `VERIFICATION.md`, committed verbatim)
**Required:** nothing pinned the two window lengths.
- 1a's mutant N2 (the attempt counter's `cache.add` timeout 60 s instead of the window) survived all 15 tests. At 4 guesses a minute an address would never lock: about 5,700 guesses a day against a 24 h invitation code.
- N3 (the lock entry's timeout 60 s) also survived.

**Added:** `test_both_windows_last_the_whole_lock_period`.
- It wraps the budget's own cache reference in a recording `MagicMock(wraps=cache)`, so every call still goes through to the real cache. That mock never reaches a response (rule 14).
- It spends the budget, then asserts that every `cache.add` for the `attempts` and `locked` keys used `timeout == VERIFY_EMAIL_LOCK_SECONDS` (1800).

**Harness:** 1a's N2 and N3 joined `mutate.py` as M17 (attempt window 60 s) and M18 (lock entry 60 s).

**N1 gates** (on fc8ac30, wrapped in `systemd-run MemoryMax=6G`, `nice -n 10`, a timeout, `--noinput`):

| Gate | Result |
|---|---|
| `users.tests_verify_email_budget` | **16 OK** |
| 2 Mutation | **18/18 killed**, survivors `[]`. M17 and M18 are both killed by the new test (`mutation_log.txt`) |

N1 is tests only, so the `users` regression (600 OK; 1a's run: 605 OK) is unchanged.

**Rebase (1a's N4):** onto beta after batch-2a lands, since 2a touches `users/throttling.py` and the `/auth/otp` area. The new SHA goes to 1a for a range-diff and re-run.
