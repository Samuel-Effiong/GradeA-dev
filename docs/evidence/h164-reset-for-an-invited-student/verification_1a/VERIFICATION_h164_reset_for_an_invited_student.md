# Verification: H-164, "forgot password" for an invited student (option A), with the guard added after my finding @ b6282c7f

**Verifier:** 1a. **Author:** ed. **Date:** 2026-10-08. Beta line, base beta `3f2ad13e`. No migration, no model, no serializer.
**Tip:** `task/h164-reset-for-an-invited-student` @ **b6282c7f7fd70ed8de24a15e046926b5fb269324**, the final tip: docs only over the gated `bbf20687` (no non-docs file differs, checked by `git diff --name-only`). History: first tip `1c970b9d` (docs over `c82882a4`), then my finding, then the guard in `bbf20687`.

The change over beta is `users/views.py` (28 lines in all) and ed's test module `users/tests_reset_for_an_invited_student.py`: (1) `POST /auth/otp` RESET_PASSWORD refuses a never-verified account only if it is also inactive; (2) a successful `reset_password` stamps `email_verified_at` (if empty) in the same `save()` as the new password, clears `must_change_password`, then stamps `last_login`; (3) NEW since my finding: `reset_password` refuses, with the generic "Invalid email, OTP code, or new password." 400, an account that is both never-verified and inactive, whatever code row exists, before the lock check.

**I ran at b6282c7f** (2026-10-08, 17:51:25 to 17:55:47 WAT, one chain `h164_run.sh all`, 0b's GRANT and GO), from my own detached scratch checkout, serial, each run once, inside one `systemd-inhibit` (idle, sleep, lid switch; the whole script, re-executed at its first line), the 6G scope with `MemorySwapMax=0`, `nice -n 10 timeout -k 60 1800`, `PYTHONDONTWRITEBYTECODE=1`, `python -B`, own settings and test databases, each run's output straight to its own file, stdin from `/dev/null`, load 2.40 before. Under rule 15 I cite ed's gate (step 0 Ran 15, three red; modules and guards Ran 475 OK; 13 of 13 mutants killed) and repeat none of it. Not run by anyone: the owning-app regression of the delta (ed's stated call), the whole users and classrooms app after the guard; the first tip's users+classrooms regression (Ran 1171 OK) was before the guard.

**Verdict: VERIFIED-WITH-NOTES.** The first tip was NOT verified: my test v6 found that the reset step had no guard where the request step has one, so a reset code that existed when an account was switched off still reset it and stamped its email (answer 200 with tokens). ed added the guard and his own tests first. At the final tip my nine tests pass, and each is shown able to fail (old code and mutants), and the guard is shown tight on both sides (a guard too weak or too wide is caught). The notes are limits; none asks for a change.

## The finding on the first tip (kept, because it is why the delta exists)
At `1c970b9d`: an invited student, a code made for them, `is_active` set False (email still unverified), reset called with the correct code: HTTP 200 with tokens, password changed, `email_verified_at` stamped. That leaves an inactive account with a verified email, the state Google sign-in reads as "deactivated on purpose" (users/views.py, the carve-out near the Google flow) and will not revive. The old code let the same reset run but did not stamp. Reach: only if a code row exists when the account is switched off (code good for 15 minutes). Graded LOW. Seen red twice on that tip: `runs/h164_base_first_1c970b9d.log`, `runs/h164_base_second_1c970b9d.log` (each `Ran 7`, v6 red with "reset answered 200"). In both runs my v2 was also red through my own parameter parsing (NULL literals and an expression placeholder shifted the numbering); those two logs are evidence of v6 only. The parser was rewritten and v2 is now green on the tip and red where it should be (below).

## What I checked by reading
- **The guard** sits in `reset_password` right after the user and the code row are found and before the lock check: `if not user.email_verified_at and not user.is_active: raise ParseError("Invalid email, OTP code, or new password.")`. It writes nothing, so a code that exists is left to expire (15 minutes); ed's reasoning is in EVIDENCE.md, "Delta".
- **Not a new oracle.** The refusal has the same words and status as an unknown address's reset answer (no code row: the generic 400 at the lookup). My v9 shows it for the case ed asked about: a LOCKED, switched-off, never-verified row answers its reset byte for byte like an unknown address, not with the lock message.
- **A switched-off VERIFIED account still resets** (v8, and ed's T15). That is today's behaviour, unchanged. The Google code comment says SimpleJWT rejects tokens of an inactive user on the next request, so the tokens it receives do not open the API; I did not test that.
- **An inactive row with no code row** gets the generic 400 at the lookup, unchanged.
- **Who is "active and never verified":** an invited student (the target), a licence-invited teacher (same shape), a superuser made by the command line (active, never verified). All now have the reset road and are stamped verified by a successful reset. Self-registered and old-scheme pending rows are inactive and stay refused at the request. The model default for `is_active` is False; the only creations as active are the invitation paths, the school-admin completion, Google sign-in and `create_superuser`.
- **The same-save claim** (ed said no test isolated it): my v2 reads the parameters of the one UPDATE that writes the new password and finds `email_verified_at` non-null and `must_change_password` false in the same statement.
- **Signals:** the user pre/post-save receivers read fields for cache invalidation; the reset's `save()` fired them before too. `email_verified_at` is in no cached payload that I found; I did not run the cache test (rule 20 does not apply: no serializer or cached route).

## What I checked by running (at b6282c7f)
**My module** `users/tests_vf1a_h164_reset.py`, 9 tests, a TestCase-level APITestCase using the real invitation path (`enroll_student_by_email`) (sha256 starts 321a031b9d6d6d4f); written before the runs; the expected sets are in `h164_expected_kills_bbf20687.txt` (543cb686e6258f41; the v9 addendum was appended, also before any run).
- **v1:** the reply to a reset request is the same (status and bytes) for an unknown address, an invited active student and an established one; the invited one is sent one mail to the right address and the unknown one none. **v2:** the one UPDATE that writes the password also carries a non-null `email_verified_at` and a false `must_change_password`. **v3:** five wrong codes lock; a correct code after the lock changes nothing. **v4:** an expired correct code stamps nothing. **v5:** end to end through the real login route: the new password logs in, the login answer says `must_change_password` false, the temporary password no longer logs in. **v6:** a switched-off, never-verified account is not reset or stamped by the reset. **v7:** an active never-verified teacher is sent a code with the same neutral 202. **v8:** a switched-off VERIFIED account can still reset. **v9:** a locked, switched-off, never-verified account answers its reset like an unknown address.

| Run | What | Result (the log's own Ran line and named failures) | Written set |
|---|---|---|---|
| base | the tip, unmutated | `Ran 9`, `OK` | all green |
| old | `users/views.py` from `3f2ad13e`, rest at the tip | `Ran 9`, `FAILED (failures=6)`: v1 v2 v5 v6 v7 v9 | v1 v2 v5 v6 v7 v9 |
| Y1 | the old refusal back at the request | `Ran 9`, 2: v1 v7 | v1 v7 |
| Y2 | the email stamp in a second UPDATE, not the save | `Ran 9`, 1: v2 | v2 |
| Y3 | stamp before the lock check | `Ran 9`, 2: v3 v4 | v3 v4 |
| Y4 | stamp in the expired-code branch | `Ran 9`, 1: v4 | v4 |
| Y5 | the flag kept | `Ran 9`, 2: v2 v5 | v2 v5 |
| Y6 | request answers 200 for an unverified account | `Ran 9`, 2: v1 v7 | v1 v7 |
| Y7 | the new guard removed | `Ran 9`, 2: v6 v9 | v6 v9 |
| Y8 | the guard weakened to "not email_verified_at" (refuses invited active) | `Ran 9`, 3: v2 v3 v5 | v2 v3 v5 |
| Y9 | the guard widened to "not is_active" (refuses switched-off verified) | `Ran 9`, 1: v8 | v8 |
| hooks | `pre-commit run --from-ref 3f2ad13e --to-ref b6282c7f` | 18 passed, 0 failed | n/a |

- **Every mutant was applied before its run** (abort 8 otherwise; "mutated sha differs: True" in every log) and **restored** from the commit's blob afterwards (the compare says True for all nine, 0 tracked changes after); the old-code run restored too. `PYTHONDONTWRITEBYTECODE=1` and the pycache cleared around each.
- **Rule 19.** Each of my nine tests is shown able to fail: v1 by old, Y1, Y6; v2 by old, Y2, Y5, Y8; v3 by Y3, Y8; v4 by Y3, Y4; v5 by old, Y5, Y8; v6 by old, Y7 (and on the first tip); v7 by old, Y1, Y6; v8 by Y9; v9 by old, Y7. v6 and v9 are also green on the tip, and v8 is green on the tip and the old code, so they can pass.
- **Where my written sets were wrong:** none in this chain; every set matched on the first run. (The slips were in the earlier baselines, named above.)

## Notes (not blocking)
- **N1. The request step still tells apart "inactive and never verified" (400 "Email not verified.") from every other address.** Unchanged by this row, now the only remaining difference there; ed and the Senior Manager keep it on purpose (the 400 hint to the student site).
- **N2. An already-issued code is left to expire.** The guard writes nothing; the code row stays for up to 15 minutes. A refusal each time it is used.
- **N3. More accounts have the reset road than "invited students":** a licence-invited teacher and a command-line superuser are active and never verified. They are stamped verified by a successful reset. The row's text says the first; the second is only by my reading of `create_user`/`create_superuser`, not by a test.
- **N4. A successful reset of a switched-off VERIFIED account still answers 200 with tokens** (v8 pins it; it was so before). The tokens are said to be rejected by the auth class for an inactive user; not tested by me.
- **N5. Not shown:** production mail delivery and the real throttle key; what the student site shows for the 400 on reset; timing of the guard against the unknown-address path (the two paths do different amounts of lookup; not measured); admin tools that switch accounts off.
- **N6. Not done:** no run of ed's module, gate, mutants or regression (his, once); no owning-app regression after the guard (ed's call, stated by him); no whole-tree credential scan (one production file; the hooks' secrets check passed).

## Files of this check (sha256 prefixes; `~/Documents/Projects/GAP-1a-records/` unless noted)
- `FINDING_h164_v6_switched_off_account_reset.md` (c2b118982ed451ac)
- `h164_probe_tests_vf1a_h164_reset.py` (321a031b9d6d6d4f), `h164_mutants_Y.py` (5b7a5ca4e5ccef23), `h164_expected_kills.txt` (ac374b1ca7b118cd, for the first tip), `h164_expected_kills_bbf20687.txt` (543cb686e6258f41)
- `runs/h164_b6282c7f.log` (34f4be341bf24593), `runs/h164_old_code_b6282c7f.log` (552a2175db3c5e97), `runs/h164_mutant_Y1..Y9_b6282c7f.log` (9 logs), `runs/h164_base_first_1c970b9d.log` (d6477bdf2d4d8256), `runs/h164_base_second_1c970b9d.log` (bd4a1b410c10d33f)
- `~/Documents/Projects/GAP-1a-scratch/h164_run.sh` (4f449bd0a5cf7553; copy as `h164_run.sh.txt`), `h164_hooks_b6282c7f.txt` (35b0330842d79652), `h164_all_console.txt`
