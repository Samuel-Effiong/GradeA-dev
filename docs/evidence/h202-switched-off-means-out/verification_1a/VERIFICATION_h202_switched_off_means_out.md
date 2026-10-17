# Verification: H-202, switched off means out (SECURITY) @ 5ad21e96

**Verifier:** 1a. **Author:** ed. **Date:** 2026-10-08. Beta line; H-164's final tip (`e11a0083`) is merged into the branch. No migration, no model, no serializer.
**Tip:** `task/h202-switched-off-means-out` @ **5ad21e962e1ccd5156bd369c9cf3470759ec40e9**, docs only over `529dc53d` (docs) over `ecf65160` (the added lock test, tests only) over the gated code `24388a48`. Production files: `users/views.py` (+28 lines) and `users/admin.py`, last changed at `24388a48` and `fc02d3f2`.

The change: (1) a VERIFIED account that is switched off (`email_verified_at` set, `is_active` False) is refused on four doors. The VERIFY_EMAIL code request answers the unknown address's neutral 202 and makes and sends nothing. `/auth/verify` refuses through `refuse()` (the wrong-code answer, the attempt spent, nothing written). The RESET code request answers the neutral 202 before any code row is made. The reset step refuses with the generic answer, spending an attempt unless the row is already locked. (2) In the Django admin, the bulk "mark inactive" action is one UPDATE with a Case/When (typed `IntegerField`) that raises the token epoch only of rows that are active now; the edit form revokes sessions when it takes `is_active` from True to False.

**I ran at 5ad21e96** (2026-10-08, 20:31:50 to 20:37:42 WAT, one chain `h202_run.sh all`, 0b's GRANT and GO), from my own detached scratch checkout, serial, each run once, inside one `systemd-inhibit`, the 6G scope with `MemorySwapMax=0`, `nice -n 10 timeout -k 60 1800`, `PYTHONDONTWRITEBYTECODE=1`, `python -B`, own settings and test databases, each run's output to its own file, stdin from `/dev/null`, load 4.77 before. Under rule 15 I cite ed's gates (step 0 Ran 75 with the thirteen written red, each for its written rule 22 reason; modules and guards Ran 535 OK; 17 mutants, 17 killed after the added lock test) and repeat none. The users-app regression is ed's, once, after this verdict.

**Verdict: VERIFIED-WITH-NOTES.** By my own tests, which use real POSTs of the admin pages, bytes against a real unknown address, and a different mechanism from the author's, a verified switched-off account cannot be switched back on, signed in, or given a password by any of the four doors; the answers do not tell it from an unknown address; the budgets and the lock are spent; the admin raises only the right rows' epoch; tokens from before a switch-off stay dead after a switch-on. Each of my tests is shown red by the old code or a mutant of mine, for its written reason. The notes are limits; none asks for a change.

## What I checked by reading
- **The four arms** in `users/views.py` are as the hand-over says; each sits where the diff shows (the reset-step arm after the N3 guard and before the lock check, so a locked, switched-off account answers generic, never the lock message). In `/auth/verify` the new arm follows the never-verified admin-power arm; neither can fire for an account the other covers.
- **The bulk UPDATE:** the Case reads `is_active` from the row as it was before the statement (an UPDATE's right-hand sides see the old row), so setting `is_active=False` in the same statement does not change which rows get the bump; `output_field=IntegerField()` is needed because the two branches would otherwise mix a PositiveIntegerField and an IntegerField, which Django refuses. The first run of the module at the tip ran this against the real database through the real admin POST: no field-mix error.
- **Other ways to switch a user off:** I searched the non-test code for assignments of `is_active` to False. The only user-level one is the Django admin; `school.is_active = False` (classrooms/views.py) is a school flag, billing's `is_active=False` are subscriptions. So "deactivation by anything other than the Django admin does not exist today" matches my reading. The other `is_active` writes in `users/views.py` set it True (verify, school-admin completion, Google, the Google resurrect).
- **Registration against a switched-off address:** the self-registration serializer relies on the model's unique email, so a second register for the same address is refused by validation; I did not test it.

## What I checked by running (at 5ad21e96)
**My module** `users/tests_vf1a_h202.py`, 11 tests (sha256 starts 1b767f991bde69f9); written before the runs; expected sets with a failure fragment per test (rule 22) in `h202_expected_kills.txt` (670fc19f5d290d35). **y1** the VERIFY_EMAIL code request is the unknown address's answer in bytes and stores/sends nothing; **y2a** `/auth/verify` with a planted code answers like a wrong code and writes nothing; **y2b** five such refusals spend the budget AND set the address lock, the next is a 429; **y3** the RESET code request is the unknown address's answer in bytes and makes no code row; **y4** a reset with an existing code gets the unknown address's refusal (same bytes), the password is unchanged and the account stays off; **y5** five refused resets lock the row and the answer after the lock is still the generic one; **y6** one real admin POST of the bulk switch-off over a mix (two active rows, one already off): the active rows' epoch rises by one, the inactive row's does not; **y7** tokens issued before a bulk switch-off (access and refresh) stay dead after a bulk switch-on; **y8** a real POST of the admin change form, built from the page's own form: a name edit leaves the epoch, switching off raises it by one, switching on leaves it; **c1** an active verified account is still sent a reset code; **c2** an inactive never-verified user is still activated by a code (202).

| Run | What | Result (the log's Ran line and named failures) | Written set |
|---|---|---|---|
| base | the tip | `Ran 11`, `OK` | all green |
| old | `users/views.py` and `users/admin.py` from `e11a0083` | `Ran 11`, `FAILED (failures=9)`: y1 y2a y2b y3 y4 y5 y6 y7 y8 | y1..y8 |
| P1 | the VERIFY_EMAIL request arm removed | 1: y1 | y1 |
| P2 | the `/auth/verify` arm removed | 2: y2a y2b | y2a y2b |
| P3 | the verify arm raises directly (no budget, no lock) | 1: y2b | y2b |
| P4 | the RESET request arm removed | 1: y3 | y3 |
| P5 | the reset-step arm removed | 2: y4 y5 | y4 y5 |
| P6 | the reset-step arm does not spend the attempt | 1: y5 | y5 |
| P8 | the bulk raises every row's epoch | 1: y6 | y6 |
| P9 | the bulk raises no epoch | 2: y6 y7 | y6 y7 |
| P10 | the edit form's revoke removed | 1: y8 | y8 |
| P11 | the RESET request arm refuses every account | 1: c1 | c1 |
| P12 | the verify arm refuses every inactive account | 1: c2 | c2 |
| P13 | the edit form revokes on every edit | 1: y8 | y8 |
| hooks | `pre-commit run --from-ref 3f2ad13e --to-ref 5ad21e96` | 18 passed, 0 failed | n/a |

- **Rule 22.** I wrote the failure fragment each red test must carry in its own block before the runs. After the runs I read each failing test's own block in each log: all 24 fragments are present (for example "a code was stored" for y1, "202 != 400" for y2a, "the address is not locked" for y2b under P3, "the budget was not spent" for y5 under P6, "an already inactive row's epoch moved" for y6 under P8, "the epoch did not rise" for y8 under P10, "an edit of the name moved the epoch" under P13).
- **Every mutant was applied before its run** (abort 8 otherwise) and **restored** from the commit's blob afterwards (the compare says True for all twelve, 0 tracked changes); the old-code run restored both files.
- **Rule 19.** y1 by the old code and P1; y2a by the old code and P2; y2b by the old code, P2, P3; y3 by the old code and P4; y4 by the old code and P5; y5 by the old code, P5, P6; y6 by the old code, P8, P9; y7 by the old code and P9; y8 by the old code, P10, P13; c1 by P11; c2 by P12. All eleven are green on the tip, so each can pass and fail.
- **y8 was the risk I named before the run** (a real POST of the admin form that I could not try beforehand). It passed on the unmutated tip, so the form data was right.
- **Where my written sets were wrong:** none in this chain; every set and every fragment matched on the first run.

## Notes (not blocking)
- **N1. An account switched off BEFORE this ships keeps its old token epoch.** The revocation happens at the switch-off; for an account the admin already switched off earlier, tokens issued before that switch-off come back to life on a later switch-on, until they expire (the module docstring says access 1 day, refresh 2 days). Switching such an account off again does not help (it is already inactive, so the bump does not fire); only a manual epoch bump before the switch-on would. Not tested by me (I made the state with a normal switch-off at test time).
- **N2. The reset request still tells apart a NEVER-VERIFIED inactive account (400 "Email not verified.") from a verified switched-off one (neutral 202).** That is ed's Row A (H-198), known and kept on purpose; this delta did not widen it.
- **N3. Google sign-in** for a verified, switched-off account was already refused ("deactivated"); for a never-verified admin-power account it is still open (H-203, ed says, not here).
- **N4. Not shown:** production mail and throttle keys behind a proxy; behaviour under a connection pooler; timing of the refusals against the unknown-address path (not measured); the admin screens as a person sees them (my POSTs are the forms' data, not a browser).
- **N5. Not done:** no run of ed's module, gate, mutants or regression (his, once); the users-app regression is his after this verdict; no whole-tree credential scan (two production files; the hooks' secrets check passed).

## Files of this check (sha256 prefixes; `~/Documents/Projects/GAP-1a-records/` unless noted)
- `h202_probe_tests_vf1a_h202.py` (1b767f991bde69f9), `h202_mutants_P.py` (ccb20c5d3e386067), `h202_expected_kills.txt` (670fc19f5d290d35), `h202_run.sh.txt` (2cb59c49d1f386a8)
- `runs/h202_5ad21e96.log` (00078c54eff8fa29), `runs/h202_old_code_5ad21e96.log` (15b1a204cc2e9333), `runs/h202_mutant_P1,P2,P3,P4,P5,P6,P8,P9,P10,P11,P12,P13_5ad21e96.log` (12 logs), `runs/h202_hooks_5ad21e96.txt`
