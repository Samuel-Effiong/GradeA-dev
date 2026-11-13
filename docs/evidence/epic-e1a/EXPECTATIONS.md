# Epic E1a: expectations, committed before any site moves

Base: staging 2f00c078 (beta 40f514f6 + B0). Plan:
docs/phase2/architecture/09_epic_e1_implementation_plan.md (task/plan-e1-c1
7c7e5c028), E1a steps 1-5. Owner decisions D1 (formulas unified in E2, not
here) and D2 (no background recalculation), 2026-11-06. Scope additions (a),
(b), (c) approved by the SM 2026-11-06: every added site frozen before it
moves.

## The tests

| File | What it pins | State on the base commit |
|---|---|---|
| classrooms/tests_e1_grade_parity.py | Frozen verbatim copies of every grade computation on 2f00c078 (the legacy oracle). `LiveSitesMatchLegacyTest`: `compute_final_grade` and `StudentRiskEvaluator` against it. `ServiceMatchesLegacy*Test`: `classrooms.grade_service` against it, 300 in-memory corpora and 40 saved corpora with every plan §4.1 edge row | live: green (2). service: red, ImportError (15) |
| classrooms/tests_e1_snapshots.py + classrooms/e1_snapshots/*.json | 39 golden outputs: every endpoint and service that carries a grade figure, on one fixed fixture under a frozen clock. Recorded on 2f00c078 | green (26 tests, 39 files) |
| classrooms/tests_e1_grade_guard.py | FR-E-06: no grade maths outside the service (rules G1-G6), with an allow-list that cannot go stale, and bypass self-tests | red: 45 hits outside the service; service missing |

Red run: `red-run.log` (full log in ~/Documents/Projects/GAP-a0-runs/e1a/red-run.log):
Ran 51, failures=2 (guard), errors=15 (service import). Every snapshot and
live-oracle test passes.

## Deviations from plan 09

- **Saved corpora: 40, not 300.** The plan's 300 corpora are run in memory
  (unsaved rows, every in-memory figure). The queryset figures (R1, rows,
  means) use 40 saved corpora of 1-3 courses, 1-8 students and 1-6
  assignments, built with bulk_create, to keep the run near 10 seconds. Any
  seed replays with `E1_SEED=<n>`.
- **Snapshots canonicalize list order.** Several endpoints break ties by
  UUID, which is random per run, so rows are sorted before comparison. Every
  value is still compared byte for byte; only row order between ties is not.
- **The guard has two more rules than §3.3**: G3 (the weight ternary) and G5
  (sum/len over score or percentage names), as agreed with the SM.
- **Two existing tests change their import line only**
  (classrooms/tests_student_final_grade_released_only.py, dashboard/tests.py)
  when classrooms/final_grade.py is deleted at the end of the slice.

## Proof each site is frozen

`coverage-probe-base.txt` (script: `coverage_probe.py.txt`): one
number-changing edit planted at each of 35 site locations on the base code,
the snapshot and live-oracle tests run, the file restored and its sha256
checked. 33 red. The 2 green are P3 and P5, which are never read (SITES.md
note 1). Full logs: ~/Documents/Projects/GAP-a0-runs/e1a/coverage-probe-base/.

The site-by-site table is SITES.md.
