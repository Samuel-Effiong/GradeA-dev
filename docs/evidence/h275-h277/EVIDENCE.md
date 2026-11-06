# H-275 + H-277 evidence

| Gate | Commit | Expected | Result |
|---|---|---|---|
| Red run | efa2fe46 | Ran 16, FAILED (failures=10, errors=5) | exact match (h275_red_efa2fe46.log) |
| Fix | 34129e2a | - | billing/invoice_wording.py + 4 stripe_service sites; logout ownership check |
| Mutation | 34129e2a | M-1..M-8 KILLED, restores OK | 8/8 KILLED, all restore sha256 OK (h275_mutation_34129e2a.log) |
| Near run (billing + users) | 34129e2a | Ran 3223, OK (skipped=4) | **Ran 3223, OK (skipped=4)** in 883s |

Near-run log archived outside the repo: ~/Documents/Projects/GAP-a0-runs/h275_near_34129e2a.log
sha256 62968260ff93d6cb350051e2578a049acfe9c8393638125b40c7786b63e86b36

Full suite: NOT run on this branch (owner instruction 2026-11-02: no full suites without say-so). The Batch 32
gate covers it; expected full count 8614 = 8598 + 16 (DiscoverRunner count).
Owner decision 2026-11-02: H-275 and H-277 ship in Batch 32.
Process miss recorded in NOTES.md (one targeted check ran before its expectation was committed).
