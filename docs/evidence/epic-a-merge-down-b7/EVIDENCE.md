# Bundle 7 merge-down into Epic A

Author: ed (Security), 2026-10-05. Branch `task/epic-a-merge-down-b7`, from `phase2/epic-a`
db6f5155; merges beta 63c3da22 (bundle 7 as pushed). Verifier: v2. Then Gate 10 and staging
refresh 10 (0b). Phase 2 stays off beta: this goes one way only, beta into the epic.

## What arrives

H-89 (addresses scrubbed from log text and from what Sentry is sent), H-91 (ids only in every
log and print call, with a repository-wide guard test), H-97 and H-109 (the test runner's Redis
hygiene), H-107 (the parallel test runner's SIGTERM and blocked-write fixes), H-108 (a comment),
their evidence, and bundle 7's documents. H-110 and H-98 are not in it.

## The merge (335e6c81)

Six conflicts.

| File | Side kept | Why |
|---|---|---|
| `AutoGrader/sentry_scrubbing.py` | beta | SM ruling: H-89's scrubber replaces the epic's BE-A-04 scrubber. Byte-identical to beta's. |
| `AutoGrader/tests_sentry_scrubbing.py` | beta | Same ruling. Byte-identical to beta's. |
| `AutoGrader/settings.py` (2 blocks) | beta | Same ruling: the hooks import outside the `try`, and the four hooks passed to Sentry. The Sentry block is identical to beta's. |
| `assignments/tasks.py` | epic | Beta's only change was the print in the legacy loop of `grade_batch_async`. The epic replaced that loop with `_dispatch_tracked_grading` (S7a), whose log line names the submission id. Identical to db6f5155. |
| `classrooms/serializers.py` (2 blocks) | epic | The invitation line already logged the user id, and the school id where beta logs the school name. Identical to db6f5155. |
| `classrooms/services/roster_import.py` | epic | `_import_one` already logs a failed row by row number and course id. The auto-merged `for position, row in enumerate(...)` is put back to `for row in rows`: nothing on the epic uses the position. Identical to db6f5155. |

The epic's old hook `scrub_pii_before_send` is gone with its five tests. Nothing else in the
code used it (grep over `*.py`). `docs/evidence/epic-a-pii-cleanup/EVIDENCE.md` still names it;
that record is left as written.

The divergences recorded by earlier merge-downs are unchanged from db6f5155:
`assignments/tasks.py`, `students/tests_h38_tasks_namespace.py`,
`classrooms/tests_h71_student_add_role.py`, `classrooms/tests_security_penetration.py`,
`classrooms/tests_h99_placeholder_email.py`, `AutoGrader/tests_beat_locks.py`,
`audit/tests_retention_sweep.py`, `audit/tasks.py`.

## Two changes after the merge

- 77f4dc2b, comment only: the epic's own second copy of the ids-only catcher comment, in
  `add_teachers_batch` (`billing/license_service.py`), said the individual-subscription refusal
  carries the address. Since H-85 it is one neutral sentence. Same correction as beta's H-108
  (c4aed446), which arrived with the merge for the first copy.
- 6029963f: `scripts/pii_log_baseline.txt` has no entry left. The epic's pre-commit checker
  (`scripts/check_no_pii_in_logs.py`) excused seven files. H-91 cleaned all seven:
  `find_violations()` returns nothing for each on the merged tree, and the whole-repository
  check passes with the list empty. Checked file by file, not by count. (This is a static
  script, run without a grant; it is not a test run.)

## Cross-side checks before the gates (static)

- Beta's new repository-wide guard against the epic's code: the epic's checker has the same
  rule and passes on the merged tree with an empty baseline. v2's independent scan with the
  guard's own rule found 58 hits on db6f5155 alone and 0 on the merged tree. The guard itself
  runs in step 1.
- The epic's guards against beta's new code: beta adds no reason code, no audit action, no
  route, no beat entry and no management command (`AutoGrader/celery.py`,
  `AutoGrader/reason_codes.py`, `audit/enums.py` and every `urls.py` are untouched by the
  merge). The guards run in step 1 all the same.
- Symbols the merge deletes: `scrub_pii_before_send`; no test or code on the merged tree names
  it.

## Mutants

`mutate.py`, 22, on the EPIC's versions of the files, rules 17 and 18, own test database.

- P1 to P9, Q1, Q4, Q5: twelve of H-91's fourteen, read from d5's script as merged. Their
  anchors exist on the epic.
- H-91's Q2 and Q3 have no anchor here (they are in two of the files where the epic's side was
  kept). E1 and E2 put a leak back on the epic's lines instead. E3 does it on the invitation
  line, E4 on the epic-only reason-code line in `billing/license_service.py`.
- S11, S16, Y7, Y8, Y9, Y12: H-89's six on `AutoGrader/settings.py`, the one H-89 file that is
  not byte-identical to beta's.
- H-89's other mutants are on files identical to beta's and are not repeated. H-97's, H-107's
  and H-109's are on test-runner files that the epic had not changed; not repeated either.

Judged three ways (SURVIVED, KILLED, BROKEN) as the script's docstring says. The failing test
expected for each mutant is written in the script (`EXPECTED`), and was written before the
first run: for d5's mutants from d5's battery logs on beta, for E1 to E4 the guard's one
repository-wide test.

## Limits

- Step 0 shows the guard red on the epic's seven files as they were at db6f5155. It does not
  show each of the 58 lines separately.
- Only `AutoGrader` and `billing` are regressed here (the apps whose production code the merge
  changes on the epic). The other apps are covered by 0b's Gate 10 full run, not by this record.
- The log scrubber is off under the test runner by design (H-89), so no regression here proves
  it scrubs in a running service; H-89's own tests switch it on for that.

## Runs

Frozen tip af350044, one grant from 0b, 2026-10-05 15:55:14 to 16:04:27, one process at a time,
6G scope, rules 12, 13, 16, 17 and 18 (every run wrote straight to a file, stdin from the null
device, nothing piped). No run was stopped, repeated or failed outside what step 0 expects.
Before this grant nothing of the merge-down ran except commit hooks, the static checker and
`mutate.py --check`.

| Step | What | Result | Log |
|---|---|---|---|
| 0 | Reproduce-first: beta's guard `AutoGrader.tests_no_pii_in_logs` with the seven baselined files put back as at db6f5155, own DB | exit 1 as expected: Ran 4, FAILED (failures=1), `test_no_log_or_print_call_passes_an_address_or_a_name` | `prefix_guard_on_epic_base_files_failing.txt` |
| 1a | `makemigrations --check --dry-run` | exit 0, No changes detected | `makemigrations_check.txt` |
| 1 | 14 changed test modules and 18 guard modules of both sides | exit 0: Ran 426 in 231 s, OK, no skips | `modules_and_guards.txt` |
| 2 | 22 mutants on the epic's files, own DB (dropped afterwards) | 22 KILLED, 0 SURVIVED, 0 BROKEN | `mutation_log.txt`, `mutation_results.json`, `mutant_logs/` |

Mutants: every inner run exited 1 with its own "Ran" line ("Ran 16 tests" for the sixteen P, Q
and E mutants, "Ran 56 tests" for the six on `settings.py`), no test module failed to load, and
the test named in `EXPECTED` is among the failing tests of each. `mutation_results.json` holds
status, exit, the "Ran" line, the expected test and the failing tests per mutant.

`EXPECTED` first appears in commit af350044 (committed 15:53:53), which is the frozen tip the
battery ran on: the names were in the tree before the grant (15:55) and before any run. The
script's earlier commit dd17b2f3 had no expected names and judged by exit status; it was never
run.

Still owed: the one regression (`AutoGrader` and `billing`), under its own grant.
