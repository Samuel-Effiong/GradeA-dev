# Epic E1a evidence: one grade service, no number moved

Branch `task/epic-e1a`, base staging **2f00c078** (beta 40f514f6 + B0).
Plan: docs/phase2/architecture/09_epic_e1_implementation_plan.md
(task/plan-e1-c1 7c7e5c028), E1a steps 1-5. Owner decisions D1 and D2,
2026-11-06. Scope additions and the P3/P5 deletion approved by the SM,
2026-11-06. Not pushed. No migration. No new user-facing wording.

## Result in one paragraph

Every grade figure in the product (the stored and released final grades,
the cross-course and assignment grades, the at-risk inputs, every plain
mean of percentages, every mean of final grades) now goes through
`classrooms/grade_service.py`, and `classrooms/final_grade.py` is gone.
No number changed: the frozen legacy oracle, 39 byte-for-byte response
snapshots recorded before any move, and the parity command all agree with
the old code, and the query count of every snapshotted endpoint is exactly
what it was. A guard test fails if grade maths is written anywhere else.

## Doctrine, step by step

| Step | Evidence | Result |
|---|---|---|
| Expectations committed first | 5d6f246d5; EXPECTATIONS.md | oracle, snapshots, guard, on the base |
| Red | red-run.log | 51 tests: 33 ok (every snapshot and live-oracle test), 15 errors (no service yet), 2 failures (guard: 45 hits) |
| Each site frozen before it moved | coverage-probe-base.txt, coverage_probe.py.txt | 33/35 planted edits red on the base; the 2 green were P3/P5, never read |
| Service | 502c422e4 | the 15 service tests green |
| One commit per site | commits.txt, move-gates.txt | 34 site commits after the service, the gate run before each commit and green apart from the guard, which fell 45 -> 0 |
| Green | move-gates.txt, last line | 51/51 at 113d57f95, guard 0 hits outside its allow-list |
| Mutations | mutants.txt, mutants.py.txt, mutant-MQB-query-budget.txt | 13/13 killed (M1-M8, X1-X4) plus MQB (query budget), each restored and checked by sha256 |
| Query counts | qcount-base-5d6f246d5.json, qcount-head-1e28a9952.json | 26 of 26 equal, none up; committed as classrooms/tests_e1_query_budget.py |
| Parity run (§4.2) | parity-report-test-db.txt, parity-report-planted-difference.txt | test DB: 8 families, 452 figures compared, 0 differences; a planted difference is found (31 of 72) |
| Near run | near-run.txt, near-run.log.gz | classrooms students dashboard assignments, --parallel 4, at 1e28a9952: **Ran 2954, OK (skipped=16)** |

## The tests added

| File | What |
|---|---|
| classrooms/tests_e1_grade_parity.py | Frozen oracle (verbatim copies from 2f00c078), 300 in-memory and 40 saved seeded corpora; `E1_SEED=<n>` replays a seed |
| classrooms/tests_e1_snapshots.py + classrooms/e1_snapshots/ | 39 golden outputs, frozen clock, UUIDs labeled, row order canonical |
| classrooms/tests_e1_grade_guard.py | FR-E-06: rules G1-G6, allow-list that cannot go stale, bypass self-tests |
| classrooms/tests_e1_query_budget.py | every snapshot test held to its pre-move query count |
| classrooms/tests_e1_grade_parity_report.py | the parity command: all match, finds a planted difference, writes nothing |

## The parity command (plan 09 §4.2), for the production-shaped run

    python manage.py grade_parity_report               # whole database
    python manage.py grade_parity_report --course <id> # one course
    python manage.py grade_parity_report --show 50     # more differences listed

It is read-only (everything runs inside a transaction that is rolled back).
For each figure family it prints "N compared, M differences", the first 10
differences with their ids, and a final RESULT line. Exit status 0 means
every figure matches; 1 means differences were found. Run on my test
database only; **the production-shaped run is left to the SM and owner**.

## Mutations (mutants.txt)

| Id | Planted | Killed by (among others) |
|---|---|---|
| M1 | a second `Avg("submissions__score_percentage")` in a view | guard |
| M2 | the weight Coalesce inlined at a new site | guard |
| M3 | a hand-rolled `total_score / total_points` | guard |
| M4a | `weight_of` loses the total_points fallback (H-33) | parity weight/R2/R3; snapshots |
| M4b | the SQL weight loses the fallback (H-33) | parity R1 (live and service), rows, risk rows; snapshots |
| M5 | released-only drops `is_published` (H-130) | parity R2; 9 existing H-130 tests; snapshots |
| M6 | ROUND_HALF_UP -> ROUND_HALF_EVEN | parity edges (0.125), R1-R4 |
| M7 | clamp removed | parity edges, R1-R4 |
| M8 | the plain mean becomes points-weighted (D1 "fixed" by accident) | parity mean; 9 snapshots incl. teacher course analytics |
| X1 | mean_of_values rounds to 1dp | parity; 4 snapshots |
| X2 | mean_of_values ignores `count` (student overview avg_score) | parity unrounded-means test (the snapshot fixture has no released row with a percentage but no score, so only the parity corpora carry that edge) |
| X3 | risk_score_row drops the weight | parity risk row; 6 snapshots |
| X4 | graded_rows drops `points > 0` | parity rows test |
| MQB | one extra query in teacher assignment analytics | query budget (72 > 66) |

Note on mutants.txt: the first summary misread 3 kills as survivals,
because a test that logs while running splits its "... FAIL" line. The
summary was regenerated from the SAME logs using the `FAIL:`/`ERROR:`
headers (the parser fix is in mutants.py.txt); no mutant was re-run.

## Deviations and findings for the SM

1. **P3/P5 were dead** (SITES.md note 1). Deleted per SM decision. The
   student "average grade" on those two endpoints is
   `StudentRiskEvaluator.average_grade`, which is points-weighted. Effect:
   the same number of queries, one aggregate fewer in each endpoint's SQL.
2. **13 sites beyond the plan** (5 weight ternaries, 2 extra final-grade
   means, 7 Python plain means), all frozen before moving (SITES.md).
3. **Saved corpora: 40, not 300** (EXPECTATIONS.md).
4. **Query counts were not captured before the moves as the plan asked.**
   I captured them afterward from 5d6f246d5 (the expectations commit, before
   any move) in a separate worktree, and compared them with HEAD: all 26
   are equal. That baseline is what the committed budget test enforces.
5. **Allow-listed, staying put**: ai_processor/services.py:5970 (Lane 1,
   reclassify when the Fixes Worker's B1a lands) and :3363 (Lane 1),
   students/views.py:1248, grading_eval.py:61, assignments/serializers.py:575
   (display maximum, not the weight rule), and the parity command's old side.
6. Unchanged and recorded for E2 (plan §3.4): changing an assignment's
   total_points does not recalculate the stored grades that rely on it.

Full logs: ~/Documents/Projects/GAP-a0-runs/e1a/ (red-run.log,
coverage-probe-base/, moves/, mutants/, near-run-1e28a9952.log).
