# Verification: H-55, pin the token_epoch increment in the student123 remediation @ b5f9695

**Verifier:** 1a. **Author:** ed. **Date:** 2026-09-30.
**Branch:** `task/h55-token-epoch-pin` @ **b5f9695**, off beta `abeda10`. The test is `b6fd5f8`; the evidence is `b5f9695` (`docs/evidence/h55-token-epoch-pin/`). **Test and docs only:** no production file changed. It closes my surviving set-to-1 mutant from the H-3 record.

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`) in 0b's slot, from a detached scratch checkout at b5f9695 with its own test DB (`test_vf_h55`). No regression (test-only; 0b's rule-15 call).

**Verdict: VERIFIED.**

## Static
- The command's bump is `User.objects.filter(pk=pk, password=old_hash).update(password=unusable, token_epoch=F("token_epoch") + 1)`: a compare-and-set on the verified hash, with an atomic in-database increment.
- `test_the_epoch_is_incremented_not_set` checks the right thing:
  - It opens a session at epoch **1**, then moves the account to epoch **3**.
  - It asserts, as a precondition, that the epoch-1 token is already refused.
  - After `--execute` it asserts the epoch is exactly **4**, and that both the stale access and refresh tokens stay **401**.
  - "Set to 1" would revive the epoch-1 token, which is the disclosure the test exists to catch. From epoch 0 the existing tests can't tell "+1" and "set to 1" apart; this test can.

## Evidence
| Check | Result |
|---|---|
| `users.tests_remediate_student123` @ b5f9695 | **21 OK** |
| **Mutant (mine), Y1:** `token_epoch=Greatest(F("token_epoch"), 1)`, which agrees with "+1" from epoch 0 (so the old tests pass) and leaves epoch 3 at 3 | **KILLED**, only by `test_the_epoch_is_incremented_not_set`; sha-checked restore |
| ed's M1 (set to 1), M2 (+2), M3 (no bump) | all killed per `mutation_log.txt`; M1 only by the new test |
| Hooks | `pre-commit run --from-ref abeda10 --to-ref b5f9695` passes, and each of the 2 commits passes |
| Bundle 4 | `git merge-tree --write-tree 8de3078 b5f9695` is **clean** |

Logs: `runs/h55_baseline_b5f9695.log`, `runs/h55_mutant_Y1_greatest.log`.
