# Verification: H-38 tasks N3, a refused grading run records why (1a)

- **Branch:** task/epic-a-h38-n3 at **21de7c56**, on phase2/epic-a 3fff1382 (0b's base update ba663453). The code tip is c81eacbb; 21de7c56 adds evidence only.
- **Commits:** cf47a5b4 and 174caeb5 (the code and the three events), de6774c5 (guards on the helpers' lookups), c81eacbb (a comment); tests in 07ac25b6, d59f2aa4, 88b065b7, 3cc0ecfe.
- **Verifier:** v2 (independent), 2026-10-02.
- **Verdict:** **VERIFIED-WITH-NOTES**

## Static checks
| Check | Result |
|---|---|
| Base update ba663453: `git show --remerge-diff` | Empty |
| Added-line survival (`vf_merge_survival.py`) | base cc22bc0, sides 174caeb / 3fff138, **0 lines lost** |
| Production diff against the epic | assignments/tasks.py, audit/enums.py (one member), AutoGrader/reason_codes.py (one line) |
| The epic since 3fff1382 | Docs only; the branch still merges cleanly into the current tip |
| No migration | `AuditEvent.reason_code` is a plain CharField; the emitter validates the value |
| The code can't reach a client | COURSE_NOT_REACHABLE is in `AUDIT_ONLY_CODES` and has no `REASON_CODES` spec, so no coded body can be built. `CourseNotReachableError` is not a CodedError; `reason_of()` returns None for it |
| `grade_engine_async` | Two lines changed, both arguments of its except block's `emit(...)`: `reason_code=` and `school_id=`. The refusal itself (b4's ruled divergence) is untouched |
| Every other failure | `_unreachable_course_school_id` returns None unless the cause is CourseNotReachableError, so the emitter's rule (the actor's school) still applies |
| One event per refused run | The batch and the auto-grade return before any dispatch. The auto-grade is a one-off task per assignment, so there is no daily repeat |
| The guards (v2's pre-review note 3) | Broad `except`, one ERROR line with ids and the error's class, and `emit` still runs. The comment now says exactly what the event keeps |
| Rule 14 | The bare mocks of `grade_engine` and `delay` are asserted not called. Where `delay` is called (the control test), it has a `side_effect` returning a fresh real id |

## The SM's rulings, each checked
- **One audit-only code that never reaches a response:** holds (above, and 1a's `TheCodeNeverReachesAClientTests` reads the task rows, the batch results and the task results).
- **Three refusal events:** `grade_engine_async` (the submission as target), `grade_batch_async` and `auto_grade_due_assignment` (the teacher as target). Each has error class USER and the code.
- **All filed under the course's school, including S7b's existing event for this one cause:** holds. v2's P1 shows it is the course's school even when the teacher now belongs to another one.
- **The school-less course edge (v2's pre-review note 1, ruling (a)):** the emitter's standing rule applies. 1a's `test_a_tracked_run_by_a_school_member_is_filed_under_their_school` pins it, and the class docstring says so.

## 1a's gates (read, not repeated: rule 15)
| Gate | Result | Log |
|---|---|---|
| Reproduce-first over 3fff1382's code | 23 tests: 9 failures, 10 errors, as intended | logs_c81eacbb/repro_… |
| (a) 28 modules, with every repo-wide guard | **496 OK** | logs_c81eacbb/a_modules_and_guards_c81eacbb.log.gz |
| (b) mutants K1–K16 | baseline 71 OK; **16 of 16 killed** | logs_c81eacbb/mutation_results.tsv |
| (c) ONE regression: assignments, students, audit | **1390 OK** (skipped=16) | logs_c81eacbb/c_regression_…log.gz |

- v2 read the three gzipped logs: the totals match, and none has a FAIL or ERROR header.
- (a) holds everything v2 asked for at pre-review: AutoGrader.tests_reason_codes, audit.tests_route_coverage, audit.tests_history_guard, students.tests_h38_tasks_namespace, plus the merge-down's guards (beat locks, beat health, the command guard, the sweep-lock test, H-80's log guard).
- **Rule 17:** EVIDENCE.md states `PYTHONDONTWRITEBYTECODE=1` and the `__pycache__` deletion before the baseline, before each mutant and after each restore.
- **Disclosed by 1a:** the first (a), at 174caeb5 on cc22bc03, was red on three faults in 1a's own new tests (the stored MagicMock id; a NULL-versus-"" expectation). Fixed test-only in 88b065b7.

## v2 run (0b's grant, one 6G slot, rules 16, 13 and 12, scratch worktree at 21de7c56, DB test_vf2_s1)
**Rule 17:** every test subprocess ran with `PYTHONDONTWRITEBYTECODE=1`. The `__pycache__` of the mutated modules' directories was deleted before the baseline, before each mutant and after each restore. Each restore was sha-checked against 21de7c56.

**Baseline: 89 tests OK** (runs/h38_n3_21de7c56_baseline.log): 1a's module (23), students.tests_h38_tasks_namespace (13), AutoGrader.tests_reason_codes (48) and v2's probe (5).

**Probe (tests_vf2_h38_n3_probe.py), 5 OK.** It is built on the epic's own TasksFixture, not on 1a's fixture.
- **P1:** a teacher removed from school A who now belongs to school B, refused on A's course. The run's event and the batch's event are both filed under A. A's admin reads the event through the real audit route; B's admin reads nothing.
- **P3:** the row A's admin reads names no pupil, no course and no school. Nothing a call site supplied holds an address or the pupil's name.
- **P4:** a same-school school admin refused at run time gets one event with the code, error class USER, under the course's school.
- **P6:** a refused run writes no ledger row and calls no grader.

**Mutants (vf_h38_n3_mutants.py), 2 of 2 killed** (runs/h38_n3_21de7c56_mutants.log). v2 prepared nine; seven match 1a's K-mutants and were not run (rule 15).
| Mutant | Killed by |
|---|---|
| W6 the refused batch records twice | 8 of 1a's tests (among them `test_each_refusal_writes_exactly_one_event`), and the probe's batch test |
| W10 the run guard's log line carries the error's text | 1a's `test_a_refused_run_survives_a_failed_school_lookup` (1a's K16 covers the batch helper's line; this is the run helper's) |

## Notes (none blocks)
1. **`grade_engine_async`'s existing S7b event changes its school for this one cause.** This is the SM's ruling. Anyone querying past events will find older refusals under no school and newer ones under the course's school.
2. **The course's school sees the removed teacher's address** in the emitter's `actor_email` column when a tracked task names them as requester. That is the trail working as designed: the school finds who was refused.
3. **The school-less course edge** is accepted and pinned (ruling (a)); the emitter is unchanged.
4. **On a lookup error the event can lose its school or its requester** (the guards). It is still written, the refusal still holds, and one ERROR line records it.
5. **Not on this branch:** the 08a catalogue doc needs the new code, through the docs branch (0b holds the note).
6. **Future merge-downs** keep assignments/tasks.py on the epic's side, now with this change in it.
