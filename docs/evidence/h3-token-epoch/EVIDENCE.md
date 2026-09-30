# H-3 follow-up: the student123 reset revokes live tokens

Branch `task/h3-token-epoch` off beta `e7e4bdf`. Asked for by the SM on 2026-09-29, closing consequence (2) of `docs/evidence/h3-student-password-remediation/EVIDENCE.md`. Beta is live, and the founder's `--execute` waits for this fix to be live. The dry run is read-only and unaffected.

## The gap (reproduced on beta e7e4bdf, `prefix_beta_e7e4bdf_failing.txt`)
`remediate_student123_passwords --execute` reset the password with `QuerySet.update(password=unusable)`. That write doesn't go through `set_password`/`save`, so `token_epoch` was never bumped. Anyone who had signed in with the literal before `--execute` kept a working session:
- the access token was still accepted on `GET /users/<id>`;
- the refresh token still minted new access tokens on `POST /auth/refresh`, indefinitely.

Reproduce-first: 3 new tests failed on beta (4 FAIL lines, because the epoch test fails once per reset account).

## Fix
`users/management/commands/remediate_student123_passwords.py`: the compare-and-set becomes

```python
User.objects.filter(pk=pk, password=old_hash).update(
    password=unusable, token_epoch=F("token_epoch") + 1
)
```

- **One write.** The bump rides the same compare-and-set. A reset account's tokens die with its password. An account skipped because its password changed since the scan keeps its sessions (its owner set a real password).
- **Atomic.** `F()` increments in SQL, so a concurrent logout's bump isn't lost.
- **Idempotent.** A re-run finds no matches, so it bumps nothing.
- The docstring guarantee now names both columns.

## Tests (`users/tests_remediate_student123.py`, 20 tests; was 15)
New `TokenRevocationTests`. The tokens come from the real `/auth/login`, and the checks use the real routes:
- After `--execute`, a matched account's live **access** token gets 401 on `GET /users/<id>`. It returned 200 just before.
- After `--execute`, a matched account's live **refresh** token gets 401 on `/auth/refresh`, with no access token issued.
- An **unmatched** account's access and refresh tokens still work after `--execute`.
- The epoch is bumped **exactly once**, and for the reset accounts only. A re-run bumps nothing.

Changed tests:
- `test_account_whose_password_changed_since_scan_is_not_clobbered` now also asserts that the skipped account's epoch stays 0.
- `test_no_deletes_and_nothing_else_changes` now excludes `token_epoch` alongside `password`. The exact bump is pinned above, so that is not a loosening.

## Gates
| Gate | Result |
|---|---|
| 1 Reproduce-first | 3 new tests fail on beta e7e4bdf (`prefix_beta_e7e4bdf_failing.txt`). The unmatched-account and re-run tests pass there, as guards |
| 2 Mutation | **10 mutants, 10 killed, 0 survivors** (`mutate.py`, `mutation_log.txt`, `mutation_results.json`; every anchor asserted unique). E1 drops the bump (killed by the access, refresh and epoch tests). E2 bumps outside the compare-and-set (killed by the race test and the epoch test). E3 bumps every account (killed by the unmatched-tokens test and others). M1–M7 are the original H-3 mutants, re-anchored on the new call and all still killed |
| Regression | whole `users` app with `EXEMPT_EMAIL_DOMAINS=` → **592 OK** (skipped=4), `regression_users_app.txt` |
| mypy | whole-repo `pre-commit run mypy --all-files` → Passed |
| Full suite | not run per fix (0b's rule 2026-09-29); goes into the batch-2 run |

## Operator note
Accounts with `has_recorded_activity: true` in the report are exactly the ones whose sessions this now ends. Any real user among them is signed out and must use password reset. Password reset needs a real mailbox; the 97 `@student.local` accounts don't have one.

Mutation-run artefact: mutant M2 (the `--execute` flag ignored) makes the test dry-runs write a report at the default path. Two such `h3_student123_remediation_2026…jsonl` files from the test fixtures were left in the worktree root. They are untracked and not committed.
