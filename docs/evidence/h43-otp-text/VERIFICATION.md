# Verification: H-43 one /auth/otp 202 reply @ 3af18a1

**Verifier:** Verification Engineer (1a, grade-automator-plus-c2). **Author:** Security (ed).
**Base:** batch-2a 974aa59 (lands after 2a; bundle 3). **Date:** 2026-09-30.

**Verdict: VERIFIED-WITH-NOTES.** It closes my D2 (the 202 account-existence oracle). Nothing is required before merge.

## What I checked
My own detached checkout with its own test DB. Every run used `systemd-run` MemoryMax=6G, nice and timeout.

| Check | Result |
|---|---|
| Scope | `users/views.py` (one module constant plus 4 call sites plus the OpenAPI example), one new test file and evidence. 669fc73 is WIP, superseded. The base is correct: 974aa59 is 2a's tip, which already has L2's locked-reset 202 branch and the docs example. |
| Every 202 branch | The unknown address, the verification sent, the reset code sent and the reset locked (L2) all return `{"detail": OTP_SENT_DETAIL}` with status 202. The OpenAPI example uses the same constant. When H-53 lands, its "locked: send nothing" `VERIFY_EMAIL` branch falls through to the same final `return`, so it is covered too. |
| Rule 14 | The patched `send_user_activation_email` and `safe_delay` return values are never used, so nothing mock-shaped reaches a response. |
| `users.tests_otp_no_oracle` | **1 OK.** It compares the full rendered `response.content` of each branch **byte for byte** with the unknown-address reply, so the envelope is covered as well as `detail`. |
| `users` app | **Ran 629, OK (skipped=4)**, 237 MB peak |

## My mutants (3)
| Mutant | Result |
|---|---|
| O1: the unknown address gets the old text back | killed, 3 subtests fail |
| O2: the locked-reset reply's text changed by one character | killed |
| O3: the locked-reset reply's status 202 changed to 200 | killed |

All restores were sha-checked.

## Notes (not blocking)
**N1.** The frontend docs added in 4ed4ee5 say the 202 "wording can differ between cases". After H-43 it doesn't. The advice (never branch on `message`) is still right, so a one-line docs touch-up can ride along with H-53's docs update.

**N2 (scope, as agreed).** The account-state 400s ("Email already verified. Please login.", "Email not verified.") still reveal that an account exists. That's a product decision, recorded as H-53's N3. Response timing (a send versus no send) is not covered here either.
