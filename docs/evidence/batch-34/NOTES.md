# Beta Batch 34

Base: origin/beta e71e710b (Batch 33). Owner 2026-11-03: "run it now" (full run on H-282 alone; push if green).

| Merge | Ticket | Branch tip |
|---|---|---|
| b6b947659 | H-282 upload door uses is_real_super_admin() (both flags) | 2708d73c (6/6 mutants, near run Ran 220 OK) |

Clean merge. makemigrations --check: "No changes detected". Co-Authored-By lines in the batch: 0.

## B34-E1 expectation (committed before the run)
- Discovered count 8906 = 8894 + 12 (H-282).
- `GAP-a0-runs/b34/strict_bundle.sh <this sha> b34 Grade-Automator-Plus-beta-batch-34 beta-batch-34`: Ran 8906, OK (skipped=30), skip reasons identical to B33-E5.
- Anything else means STOP, no push.
