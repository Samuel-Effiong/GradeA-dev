# H-55: pin the token_epoch increment in the student123 remediation

Branch `task/h55-token-epoch-pin` off beta `abeda10`. The files it touches
are identical on `task/beta-batch-4` `8de3078`. Author: ed (Security).
Verifier: 1a. **Test only**: no production code changes.

## The gap

`remediate_student123_passwords --execute` revokes a reset account's live
tokens by bumping `token_epoch` (`F("token_epoch") + 1`) in the same
compare-and-set as the password. Every existing test starts from epoch 0,
where "+1" and "set to 1" give the same value. The Verification Engineer's
set-to-1 mutant therefore survived (H-3 record).

That mutant is a real regression. An account revoked twice (epoch 3) would
be put back to epoch 1, reviving any token still held from epoch 1.

## The pin

`TokenRevocationTests.test_the_epoch_is_incremented_not_set`:
1. Set bad1 to epoch 1, open a session with the literal (a token at epoch
   1), then set epoch 3.
2. Precondition: that token is rejected (401).
3. Run `--execute`.
4. Assert the epoch is exactly **4**, and that both the stale access token
   and the stale refresh token are still rejected (401).

## Gates

| Gate | Result | Log |
|---|---|---|
| Changed module: `users.tests_remediate_student123` | see log | `changed_modules.txt` |
| Mutation: M1 set-to-1 (the target), M2 +2, M3 no bump | see log | `mutation_log.txt`, `mutation_results.json` |
| Regression | see below | n/a |

Reproduce-first for a test-only change is the mutation run itself. M1 is
the exact code the old tests could not tell from the fix, so it must now
be killed. Since no production code changes, a regression run would repeat
beta's last green run of the same code. Whether one is still wanted is 0b's
call under rule 15.
