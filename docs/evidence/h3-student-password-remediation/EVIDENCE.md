# H-3 — `student123!` account remediation: evidence

Branch `task/h3-student-password-remediation`. Spec: `docs/HARDENING_BACKLOG.md` "# H-3".
Files: `users/management/commands/remediate_student123_passwords.py`, `users/tests_remediate_student123.py`.

## What it does
- Scans every user; targets **only** accounts where the literal `student123!` verifies.
- Dry-run by default (no DB writes, no report file). `--execute` writes.
- Per match: `password` column only, set to an unusable password (`make_password(None)`, i.e. `set_unusable_password()` semantics). Never deletes; no other column, enrollment or submission is touched.
- Each account is its own transaction, compare-and-set on the verified hash: a kill leaves each account fully reset or fully untouched; an account whose password changed since the scan (user set a real one) is skipped, not clobbered. Idempotent: after reset the literal no longer verifies.
- Uses `django.contrib.auth.hashers.check_password(..., setter=None)`, **not** `user.check_password`, because the latter re-hashes and saves an outdated hash, which would make the dry-run write. Pinned by a test and mutant M4.

## Audit record — which mechanism
The `audit` app is **not on beta** (only on `task/phase2-t1-audit-event`). So: an append-only **JSONL report** (`--report`, fsync per line, written after the row commits) plus a structured log on logger `users.remediation`. Each line: user_id, email, user_type, is_active, has_recorded_activity, last_login, previous hash *algorithm* only, new_state. No hashes, no literal. The report contains emails (PII): store it outside the repo, restricted. If the DB and report ever disagree (death between commit and write) the DB is the source of truth; re-run is safe. Swap to the audit emitter once it lands on beta.

## Gates
| Gate | Result |
|---|---|
| Functional | dry-run reports counts and writes nothing (rows or file); execute resets only matches; unaffected/already-unusable accounts untouched; enrollments, submissions and all non-password user columns byte-identical, no deletes; report has no secrets |
| Adversarial | real `auth/login` route: literal returns 200 before, **401 with no token after**; unaffected account still logs in; a reset account can set a new password and a re-run leaves it alone |
| Failure | KeyboardInterrupt on the 2nd account write: exactly 1 reset committed, other still literal-valid, 1 report line; re-run resets the remaining 1, ends with zero literal-valid accounts. Concurrent password change between scan and write is not clobbered |
| Mutation | 7 mutants, **7 killed, 0 survivors** (`mutation_results.json`, `mutation_log.txt`, `mutate.py`). M1 (remove the reset) fails 5 tests incl. `test_nobody_authenticates_with_the_literal_after_execute` and the login-endpoint test |
| Regression | full `users` app: 506 tests, 505 pass, **1 failure that is not from this change**: `tests_email_domain_rules.ExemptDomainTests.test_nothing_is_exempt_by_default` fails because the shared `.env` (symlinked into every worktree) sets `EXEMPT_EMAIL_DOMAINS` to include yopmail.com (QA-style env). This change only adds 2 new files (no existing file modified), so it cannot affect that test. Verification Engineer should confirm on a clean env. Trimmed output: `regression_users_app.txt` |
Feature tests: `python manage.py test users.tests_remediate_student123 --settings=settings_worktree` → 15 tests OK.
Not covered here: Gates 3/6/7/8 (single-pass admin command; no concurrency/scale surface beyond the CAS test). Real-infra: tests ran on real PostgreSQL.

## Production run (needs founder approval; I have not run anything against prod)
Run from the deployed release containing this commit, with prod settings:
```
python manage.py remediate_student123_passwords                       # 1. dry run
python manage.py remediate_student123_passwords --execute \
    --report /secure/path/h3_student123_remediation.jsonl             # 2. only after reviewing 1
python manage.py remediate_student123_passwords                       # 3. verify: 0 matches
```
Report handling (PROPOSED, founder to confirm owner and period): the `--report` file holds student emails. Write it to a directory readable only by the operator running the command (`chmod 600`, never inside the repo or a web-served path). Custodian: the founder (or the one operator they name). Retention: 90 days, then securely delete; record the deletion date. Do not attach it to tickets or chat.

Runtime note: it verifies PBKDF2 (1M iterations) against every user row, roughly 0.2–0.5 s each; budget minutes, not seconds, and run it off-peak.

### CORRECTION (Verification Engineer, adopted): "last_login IS NULL on all 116" proves nothing
This app never sets `last_login` (simplejwt `UPDATE_LAST_LOGIN` is off and no view updates it; a real 200 from `/auth/login` leaves it NULL). So the spec's "not one has ever signed in, therefore zero user impact" is **unproven**. The command now reports the real signal, `UserActivity` rows (written by the heartbeat middleware on authenticated requests), and warns if any matching account has activity or a `last_login`. Limit: a login with no follow-up request leaves no row. **Before `--execute`, the founder should also check auth/web logs for these accounts.**
Consequences to know: (1) 97 of the 116 are `@student.local` addresses, which are not mailboxes, so password reset cannot reach them; their recovery is a teacher/admin issuing a new generated password (re-invite path). Only the 19 real-email accounts can self-recover. (2) The command resets via `QuerySet.update()`, so it will not bump `token_epoch` if `authz-token-epoch` lands first; add `token_epoch=F("token_epoch") + 1` to the update once both are on beta (only matters if any of these accounts have live tokens, which is what the activity check is for). (3) The log line is emitted before the report-file write, so a disk-full failure after a commit still leaves the reset in the log; the DB remains the source of truth and a re-run is safe.

### Expected dry-run output (from the H-3 spec's prod findings)
```
Mode: DRY-RUN (no changes will be written)
Accounts scanned: <total user count>
Accounts with the literal password: 116
  by user_type: {"STUDENT": 116}
  active: 116
  with a non-@student.local email: 19
  with recorded sign-in activity (UserActivity rows): <must be 0 to proceed without review>
  with last_login set (not maintained by this app): 0
Dry run: would reset 116 account(s). Re-run with --execute to apply.
```
Stop conditions: matches ≠ 116, any user_type other than STUDENT, or the `WARNING ... show sign-in activity` line appears → the data has changed since the spec was written; do not `--execute` without review. Expected `--execute` tail: `Reset: 116`, `Skipped (password changed since scan): 0`, `Audit report: <path>`; step 3 must print `Accounts with the literal password: 0`.

## RESUME (shutdown checkpoint)
Current step: command + evidence, including the UserActivity-signal fix from Verification Engineer's notes, all committed (e275a10). Verification Engineer says a re-verify pass is still owed (told me not to treat their first pass as a verdict). Prod run needs founder approval, routed through Security Lead/Senior Manager; I have NOT run anything against prod.
Next exact command: none pending from me — wait for Verification Engineer's re-verify. If resuming cold: `cd Grade-Automator-Plus-h3-student-password-remediation && git log --oneline -5`; the exact prod dry-run command is in EVIDENCE.md's 'Production run' section.
