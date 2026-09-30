# H-43: /auth/otp's 202 is one reply, whether or not the account exists

Branch `task/h43-otp-text` off `task/beta-batch-2a` `974aa59` (beta-bound, batch-3; verifier 1a).

**Base.** Based on batch-2a, not beta, because batch-2a changes the same view:
- AUTHZ-L2 adds the locked-reset 202 branch;
- the auth-docs change adds the OpenAPI example.
Based on beta, this would conflict with 2a and miss the locked branch. It lands after 2a.

## Defect (1a's D2; `docs/HARDENING_BACKLOG.md` H-43)
Every `/auth/otp` call answers 202, but the text differed:
- an unknown address got `"If an account with that email exists, an OTP has been sent."`;
- a real account got `"An OTP has been sent if an account with that email exists."`.

Same status, different words, so the body alone was an account-existence oracle.

## Fix
`users/views.py` gains `OTP_SENT_DETAIL`, the one reply for every 202 branch. The branches are:
- unknown address;
- verification sent;
- reset code sent;
- reset locked.

The OpenAPI 202 example uses the same constant, so the docs can't drift from the code.

Out of scope (pre-existing and documented; 1a's N3): the 400s "Email already verified. Please login." and "Email not verified." answer about a known account's state.

## Test
`users/tests_otp_no_oracle.py`: the unknown-address reply is **byte-identical** (`response.content`) to each of the other 4 branches:
- unknown, reset;
- verification sent;
- reset code sent;
- reset locked.

The sends are patched, and their return values never reach a response (rule 14).

## Gates
_pending_ (runs through 0b, rules 12–14).
