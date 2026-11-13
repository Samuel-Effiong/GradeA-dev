# Epic B0 landing on staging (written before the runs)

Owner 2026-11-05: "push b0 so that it can unlock others". Phase 2 work goes
to staging without permission (owner rule 2026-11-05). This lands B0 alone,
ahead of B1 (owner relaxed "all of Epic B together" by this instruction).

Branch task/epic-b0-land = beta 40f514f6 (Batch 36) + merge of
task/epic-b0-r2 (B0 at d808a9ce, plus B1 plan and B1a NOTES - docs only).
Merge had no conflicts.

- E1 B0's own tests (ai_processor.tests_metering_chokepoint,
  billing.tests.test_zero_credit_scope, billing.tests.test_zero_credit_registry)
  all OK on the merged tree.
- E2 Mutant battery as recorded in docs/evidence/epic-b0/NOTES.md (M1-M6,
  M7b, M8-M12; M7 withdrawn): each killed by its named test, with the same
  fragment. Guard lines are unchanged in place (services.py:5502,
  models.py:925, immutable.py:145/202, zero_credit.py:107/119/167,
  models.py:1699). M9/M10 insert new methods, positions may differ.
- E3 makemigrations --check: no changes.
- E4 Near run billing ai_processor assignments (B0's own near labels) plus
  users classrooms students AutoGrader audit dashboard (Batch 36 apps):
  OK. Exact Ran recorded from the run.
- Then: merge into staging (refresh 17), push origin + backup staging.

## Results (2026-11-06)
- E1: Ran 43, OK (b0-tests.txt) - same 43 as B0's original green run.
- E2: all 12 mutants killed, every fragment matched, every file restored
  (sha256 match) (mutants.txt). Script: b0_mutants.py.txt.
- E3: No changes detected (migrations.txt).
- E4: near run on 9 apps Ran 9029, OK (skipped=30) (near.txt; full log
  GAP-a0-runs/b0-land-near.log).
