# H-164 finding from Verifier 1 (1a): the reset stamps a switched-off, never-verified account @ 1c970b9d

Date 2026-10-08, 17:16 WAT. Tip 1c970b9d (docs over c82882a4). Interim finding, not the verdict: H-164 is not verified as it stands.

## What I found
`POST /auth/otp` (RESET_PASSWORD) refuses an INACTIVE never-verified account. `POST /auth/reset-password` has no matching guard. If a reset code row exists for the account when it is switched off, the reset runs: it answers 200 with tokens, changes the password, and (new in H-164) stamps `email_verified_at`. Result: an inactive account with a verified email, which is the state Google sign-in reads as "deactivated on purpose" (users/views.py, the carve-out near line 1979) and will not revive. The old code let the same reset run and change the password, but did not stamp.

## How it was seen (my test v6, run twice, `runs/h164_base_first_1c970b9d.log` and `runs/h164_base_second_1c970b9d.log`, each `Ran 7`)
v6: an invited student (real invitation path), a code made for them, `is_active` set to False, the reset called with the correct code. Both runs: v6 RED with "a switched-off, never-verified account was stamped verified (reset answered 200)". v6 is red on the tip itself; I have NOT yet run it green (Y7, the guard, is written but unrun) or on the old code.

## How far it reaches (by reading)
Needs a code row to exist when the account is switched off (code valid 15 minutes): an invited student asks for a code, then an admin deactivates the account within 15 minutes. Small. Grade: LOW. The fix asked for by the Senior Manager: the same guard in `reset_password` as in the request step (`not email_verified_at and not is_active` -> the neutral refusal), and v6 adopted only after it is seen red.

## Said plainly about my own tests
v2 (password and email stamp in one UPDATE) was red in both runs through my own parameter parsing (NULL literals and an expression placeholder shifted the numbering). Its output counts for nothing; the parser is rewritten (cfa4d49aad5f009e) and unrun. Only v6's lines are evidence here.
