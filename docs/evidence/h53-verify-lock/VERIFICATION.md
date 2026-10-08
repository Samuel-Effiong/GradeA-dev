# Verification: H-53 /auth/verify per-address budget @ 7997dea

**Verifier:** Verification Engineer (1a, grade-automator-plus-77). **Author:** Security (ed).
**Base:** beta e7e4bdf (beta-bound; rebase onto beta after batch-2a). **Date:** 2026-09-30.

**Verdict: VERIFIED-WITH-NOTES.** The code is correct. One item, N1, is **required before merge**, and it is tests only: the two window lengths aren't pinned, and shortening the attempt window to 60 s goes unnoticed.

## What I checked
The checks ran in my own detached checkout of 7997dea with its own test database. Every run used `nice -n 10 timeout -k 60 1800` and `EXEMPT_EMAIL_DOMAINS=`.

| Check | Result |
|---|---|
| Scope | `users/throttling.py` (+105), `users/views.py` (`verify` and `otp`), 2 settings, 1 test file, and evidence. No migration. The WIP commit 100962e is superseded by 7997dea on the same branch. |
| Order | The lock check comes first. Then one attempt is reserved (`cache.add(key, 0)` + `cache.incr`), and only then is the code looked up. Attempts above 5 get 429 before the lookup. A failure on attempt 5 or later sets the lock (`cache.add`, first one wins). A success deletes the counter. |
| **Burst on the real django-redis cache** (my probe; ed's burst test uses the in-memory LocMem cache) | 20 threads held at a barrier right after the lock check, with 20 IPs: **exactly 5 × 400 and 15 × 429**. The counter reached 20 and the lock was set. The correct code is then refused (429) and the account stays inactive. Under tests only `clear()` and the key prefix differ from production; `incr` is the production code path. |
| Window lengths on Redis | After the burst, the counter and the lock both have `TTL = 1800`. |
| No account oracle (my probe) | 7 wrong attempts each for a known and an unknown address give **identical** (status, body, `Retry-After`) sequences, before and during the lock. |
| Address variants (my probe) | Five wrong attempts spread across `VF.KNOWN@…`, `" vf.known@EXAMPLE.com "`, `Vf.Known@Example.Com` and so on share one budget, so the correct code is then refused (429). The DB lookup stays exact (`email=`), so a variant never matches the account anyway. |
| Invitations | The lock never writes `activation_token`. School-admin invitation tokens are `secrets.token_urlsafe(32)`, which can't be guessed, and they aren't touched. For 6-digit codes: a sign-up code (15 min) is dead before a 30-min lock ends; a 24 h student invitation gets at most about 5 guesses per 30 min. |
| Resend while locked | `/auth/otp` VERIFY_EMAIL skips the send and gives the same 202. The lock key comes from the stored `user.email`, normalised the same way. |
| Input handling | `email` and `token` are `.strip()`ed before this code runs, as on beta. A non-string `email` fails the same way as before; no change. |
| `users` app regression | **Ran 605, OK (skipped=4)**, 106 s. That is ed's 600 plus the 2 final tests plus my 3 probes. |
| ed's tests | `users.tests_verify_email_budget`: **15 OK** |
| ed's mutants | 16/16 killed, from the author's log; not re-run. |

## My mutants (4)
| Mutant | ed's tests | My probes |
|---|---|---|
| N1: counter is read, then set (not an atomic INCR) | **killed** (`test_a_simultaneous_burst_gets_no_more_than_the_budget`) | killed (real Redis burst) |
| N2: attempt window 60 s instead of `VERIFY_EMAIL_LOCK_SECONDS` | **SURVIVED** | killed (TTL check) |
| N3: lock entry expires after 60 s though its stored end is 30 min away | **SURVIVED** | killed (TTL check) |
| N4: key not `.strip()`ed | survived | survived. **Equivalent**: the view strips first, and `otp` uses the stored address. |

All restores were sha256-checked against 7997dea. My first mutant pass was cut short by a session restart. The restart left N1 applied in my checkout; I restored it, verified its hash, and ran all four again.

## Notes
**N1 (REQUIRED before merge; tests only).** Nothing pins how long either window lasts.
- **N2 is a real weakening that no test catches.** With a 60 s attempt window, an attacker who spaces guesses at 4 a minute never reaches 5 in a window, so the address never locks. That is about 5,700 guesses a day against a 24 h 6-digit invitation, instead of about 240.
- N3 is milder (the 30-minute counter still refuses attempts after the lock entry expires), but it still shortens the lock and re-opens resends early.

Please add one test that pins both timeouts to `VERIFY_EMAIL_LOCK_SECONDS`. For example, patch `cache.add` and assert the `timeout` of the `attempts` and `locked` keys, or use a TTL check on Redis as my probe does. I'll re-verify by re-running N2 and N3.

**N2 (known residual, accepted in EVIDENCE).** If the counter expires between `add` and `incr`, the `ValueError` fallback `set(key, 1)` can lose a concurrent count. That allows at most about one extra attempt at a window's edge, the same as H-47.

**N3 (informational, pre-existing).** `/auth/otp` VERIFY_EMAIL still answers "Email already verified. Please login." for a verified account before the lock check. That existing oracle is unchanged by H-53.

**N4 (process).** H-53 is based on e7e4bdf. Batch-2a changes the same `users/throttling.py` and the `/auth/otp` area of `users/views.py` (L2, retire (A), wording). After the rebase onto beta, I'll re-verify the rebased delta with `range-diff` and patch-id, and re-run `users.tests_verify_email_budget`, my probes and the `users` app on the combined tree.

## Re-verification: N1 @ 438655e. Verification Engineer 1a, 2026-09-30
**Combined verdict for the tip 438655e: VERIFIED-WITH-NOTES.** Nothing is required. N4 (re-verify after the rebase onto beta) still applies before merge.

- **Delta:** 7997dea..438655e (fc8ac30, 438655e) is tests and evidence only. Under `users/` it touches only `users/tests_verify_email_budget.py`, adding `test_both_windows_last_the_whole_lock_period`. This record is committed verbatim.
- **Rule 14:**
  - the new test's `MagicMock(wraps=throttling.cache)` passes every call through to the real cache and returns real values, and nothing from it reaches a response;
  - the older fail-open test's `broken = MagicMock()` has every method the budget calls (`get`, `add`, `incr`, `set`, `delete`) raising, so it never returns a value.
  - Optional hardening: `MagicMock(spec=cache)`.
- **Runs** (my checkout, `systemd-run` MemoryMax=6G, nice, timeout):
  - `users.tests_verify_email_budget` plus my probes: **19 OK**;
  - the real-Redis burst is still exactly 5 × 400 and 15 × 429, with both TTLs at 1800.
- **My mutants:** **N2** (attempt window 60 s) and **N3** (lock entry 60 s) are now **killed by ed's `test_both_windows_last_the_whole_lock_period`**, as well as by my probe. All restores were sha-checked. **N1 is closed.**

## Re-verification: N4 (rebase) @ d883ce5. Verification Engineer 1a, 2026-09-30
**Verdict for d883ce5 (task/h53-verify-lock-2, on beta 755aa27): VERIFIED-WITH-NOTES.** Nothing is required. N4 is closed.

- **Rebase integrity:** d883ce5 is one commit on 755aa27. Its tree is **identical** to an independent 3-way apply of the verified range `e7e4bdf..2eb80cf` onto 755aa27 (`git merge-tree --merge-base=e7e4bdf 755aa27 2eb80cf`), except for the one conflict, in the `users/views.py` imports. ed resolved it by keeping both beta's `import math` and H-53's `import time`, which is correct. The squash dropped the superseded WIP history; no content is lost.
- **Combined tree** (`systemd-run` 6G, nice, timeout):
  - `users.tests_verify_email_budget` plus my probes: **19 OK**. The real-Redis burst still gives 5 × 400 and 15 × 429; both TTLs are 1800.
  - My N1 (non-atomic counter), N2 (60 s attempt window) and N3 (60 s lock entry) are all **killed** by ed's tests as well as my probes.
  - `users` app: **Ran 647, OK (skipped=4)**.
- **Composition with H-43 (bundle 3):** a trial merge of d883ce5 into bundle 3 (28c4b03) has **no conflict**. In `AuthViewSet.otp`, the locked `VERIFY_EMAIL` branch skips the send and falls through to the single `{"detail": OTP_SENT_DETAIL}` 202, so a locked address gets the same bytes as every other 202. I'll confirm this by test on the bundle tip.
