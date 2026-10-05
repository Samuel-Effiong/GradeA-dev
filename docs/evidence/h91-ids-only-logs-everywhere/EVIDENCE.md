# H-91: no log or print call passes an address or a person's name

**Author:** d5. **Branch:** `task/h91-ids-only-logs-everywhere`, off beta
`74bfc8d3`; base-updated by 0b onto `task/beta-batch-6` `76cc9b97`
(`2c336a50`) and then onto beta `141c8031` (`b6fbdbea`, the tip the
regression ran on). Bundle 7.

## The change (4 points)
1. **Before:** 64 logger and print calls in 11 production files passed a
   user's email address or a person's name: 55 in six billing files, 9 in
   five files elsewhere. H-80 had cleaned `billing/license_service.py` and
   `users/signals.py` only.
2. **After:** each of those calls passes an id instead.
   - billing (56 arguments in 55 calls): `access_control.py` 7,
     `qa_time_travel.py` 2, `services.py` 18, `stripe_service.py` 16,
     `tasks.py` 11, `views.py` 2. `X.email` becomes `X.id`;
     `X.user.email` becomes `X.user_id`.
   - `assignments/tasks.py`: a print of a student's name prints the
     submission id.
   - `classrooms/serializers.py`: two school-admin invitation lines name the
     user by id.
   - `classrooms/services/roster_import.py`: a failed bulk-add row is logged
     as "parsed row N of the import for course <id>", not by the student's
     first and last name. N counts the parsed rows from 1; it is not the
     spreadsheet's line number (a header or blank lines shift it).
   - `scripts/one_off_backfill_stripe_schedules.py`: four prints name the
     user by id.
   - `users/mailerlite_service.py`: two lines name the user by id.
   - Three variables that held an address, outside the rule below, are
     fixed too without enforcing that shape: the QA time-travel Test Clock
     line drops the address; the live-QA cleanup line and one invitation
     line name the user by id.
   - `ai_processor` had no such call.
   No message level, control flow or returned value changes.
3. **Reach:** log and print output only. Anyone who searched the logs by
   address searches by user id.
4. **The guard:** `AutoGrader/tests_no_pii_in_logs.py`
   (`NoPiiInAnyLogCallTest`), a repository-wide guard, so it lives under
   AutoGrader/ with the others (brief, 16:50 update).

## The rule is Epic A's hook's, exactly
Epic A has a pre-commit hook, `scripts/check_no_pii_in_logs.py`, with a
per-file baseline. The SM ruled that beta's guard must define "an address
in a log" the same way, so that a file which passes here can leave the
epic's baseline. The guard is that rule written as a test (no code was
copied from the epic line):
- a call to `print()`, or to `.debug/.info/.warning/.warn/.error/
  .exception/.critical/.log` on any receiver;
- with `.email`, `.first_name`, `.last_name` or `.get_full_name` anywhere
  in a positional or keyword argument (inside an f-string, `%`, `+` or
  `.format` too);
- in every `.py` file that is not a test module or a migration, `scripts/`
  included.

Checked two ways, without a test run: my scan with the guard's functions,
and 1a's with the hook's own code and no baseline. Both find 64 calls in 11
files at beta `74bfc8d3` and 0 on this branch.

**Outside the rule, on purpose (SM ruling):** exception text and messages
that are not plain literals. They are leaks only when the text happens to
hold an address, and H-89's log scrubber covers that at output time.
`~/Documents/Projects/GAP-d5-runs/h91/h89_corpus.tsv` lists the 216 logger
calls that pass exception text or attach a traceback, as H-89's corpus.
`billing/license_service.py` and `users/signals.py` keep H-80's stricter
rule (`billing/tests/test_logs_carry_no_email.py`).

**Does beta still need the hook?** The test enforces the rule on every run
of the suite, not at commit time. Whether beta also gets the hook and a
baseline is the SM's call; with this branch the baseline would be empty.

## For the next merge-down: the epic's baseline
`scripts/pii_log_baseline.txt` on `phase2/epic-a` (`ce0fd315`) has seven
entries. Each has the same violating calls on the epic as on beta, and none
on this branch:

| File | Calls on the epic | On beta | On this branch | Can leave the baseline |
|---|---|---|---|---|
| `billing/access_control.py` | 7 | 7 | 0 | yes |
| `billing/qa_time_travel.py` | 2 | 2 | 0 | yes |
| `billing/services.py` | 17 | 17 | 0 | yes |
| `billing/stripe_service.py` | 16 | 16 | 0 | yes |
| `billing/tasks.py` | 11 | 11 | 0 | yes |
| `billing/views.py` | 2 | 2 | 0 | yes |
| `scripts/one_off_backfill_stripe_schedules.py` | 3 | 4 | 0 | yes |

"Yes" assumes the merge-down takes this branch's lines; the epic's own
lines in those files must be re-checked by the hook after the merge. The
script's prints on lines 92, 113, 120 and 127 (beta `74bfc8d3`) are the
ones changed.

| Commit | What |
|---|---|
| `becbd499` | the guard, over billing (red: 55 calls) |
| `0e123246` | billing's 55 calls; two address-holding variables; the price-drift test expects the id |
| `f1ce00c6` | the guard, over the whole repository (red: 9 calls) |
| `f45dd0ef` | the 9 calls outside billing |
| `6a82ef41` | the guard moves to `AutoGrader/tests_no_pii_in_logs.py` (1a's N1, SM ruling); "parsed row N" |
| `2c336a50` | base update onto `task/beta-batch-6` `76cc9b97` (0b) |
| `b6fbdbea` | base update onto beta `141c8031` (0b) |

One existing test asserted an address in a log line
(`test_price_drift_reconciliation`, the PRICE DRIFT line); it now expects
the user's id and no address. A grep of the tests found no other.

## Gates
Under 0b's grants. Status lines: `run_status.txt`.

| Gate | On | Result | Log |
|---|---|---|---|
| Repro: the tip's guard and price-drift test over `76cc9b97`'s code (h78-repro worktree) | `2c336a50`, 2026-10-02 | RED as expected: 16 tests, failures=3 | `repro_2c336a50_tests_over_76cc9b97_code.log` |
| (a) modules | `2c336a50`, 2026-10-02 | GREEN: 146 tests, OK | `a_modules_2c336a50.log` |
| (b) battery (`test_h91_mut`), 14 mutants | `2c336a50`, 2026-10-02 | 14/14 killed, restore verified | `b_mutation_battery_2c336a50.log`, `logs/`, `results.tsv` |
| After the second base update: the guard, H-80's guard and the classrooms modules | `b6fbdbea`, 2026-10-02 | GREEN: 49 tests, OK | `a2_classrooms_guard_b6fbdbea.log` |
| (c) billing + users + classrooms + assignments + eight AutoGrader guard modules + the four sweep/schema guards, `--parallel 2`, output to a file | `b6fbdbea`, 2026-10-05 13:26 to 13:37 | GREEN: 3878 tests, OK (skipped=19) | `c_apps_guards_b6fbdbea_file.log.gz` |

**Two bases.** The repro, (a) and (b) ran on `2c336a50`. 0b then merged
beta `141c8031` (bundle 6 had landed) to give `b6fbdbea`. Of the files
this branch changes, that merge changed one: `classrooms/serializers.py`
(H-99's hunks; this branch's are in the school-admin invitation
functions). So after it the guard, H-80's guard and the classrooms
modules were run again on `b6fbdbea` (the fourth row), and (c) ran there.
The battery was not repeated on `b6fbdbea`: none of its 14 mutants is in
`classrooms/serializers.py`'s merged hunks, and every mutant is killed by
the guard, which the fourth row and (c) both run on `b6fbdbea`.

(c)'s guard modules: `tests_beat_locks`,
`tests_management_commands_are_commands`, `tests_no_pii_in_logs` (this
branch's), `tests_no_wildcard_invalidation`,
`tests_cache_invalidation_coverage`, `tests_migration_rollback_defaults`,
`tests_redis_test_isolation`, `tests_beat_health`, and
`classrooms.tests_teacher_access_sweep`,
`classrooms.tests_course_roster_scope_sweep`,
`assignments.tests_schema_extension`, `users.tests_schema_extension`.
`tests_redis_hygiene_databases` is H-97's and is not on this base; bundle
7's strict full run covers it on the merged tree.

**How the runs were made.** Every run used `--settings=settings_worktree`
and an empty `EXEMPT_EMAIL_DOMAINS`, wrapped as `systemd-inhibit
--what=idle:sleep:handle-lid-switch … --mode=block systemd-run --user
--scope -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800`
(rules 12, 13, 16). (c) ran with `--parallel 2 --verbosity 2` under `flock
~/.machine-fullsuite.lock`, with `PYTHONFAULTHANDLER=1` and a watchdog
(five minutes of silence; it did not fire). The tracked tree was clean at
`b6fbdbea`; two untracked evidence paths were present (the battery's
`logs/` and `results.tsv`, committed here).

**Rule 17.** The battery and its baseline ran with
`PYTHONDONTWRITEBYTECODE=1`, and the runner deleted `__pycache__` in each
mutated module's package before the baseline, before each mutant and after
each restore.

## (c) stalled four times before it passed, and why (H-107)
(c) in this form is the run that led to H-107. It is green here because of
one change in how it was launched, not in what it runs.

| When | How the run's output left the test process | Result |
|---|---|---|
| 2026-10-02 17:41 | a pipe to a timestamper | stalled; ended by hand, exit 1 |
| 2026-10-02 17:45 | a pipe to a timestamper | stalled; ended by the abort, exit 134 |
| 2026-10-05 12:32 | a pipe to a timestamper | stalled at 12:43:33; ended by the watchdog |
| 2026-10-05 13:04 (diagnostic, observer on; not evidence for this branch) | a pipe to a timestamper | stalled at 13:14:48; ended by the watchdog |
| 2026-10-05 13:26 | **straight to a file** | **3878 OK** |

In every stall the tests that had reported were all ok (3745 to 3782 of
3878, 0 failures), and then nothing more was printed: no failure, no
summary.

**What happened, in two parts** (the full record, with each statement
marked observed, demonstrated or inferred, is `stalls/H107_RECORD.md`):
- **B, why the parent stopped.** Observed in the 13:04 run: the test
  runner's parent left Django's parallel result loop with `BlockingIOError:
  write could not complete without blocking`, while printing a test's name
  (`stalls/D4_full_p2_observe.observe`). The run's output pipe was shared
  by the parent, the workers and their children. Demonstrated outside the
  suite: Playwright's Node driver, which inherits the process's stderr,
  leaves that pipe in non-blocking mode for every holder while it is alive
  (`stalls/node_child_leaves_shared_pipe_nonblocking.py.txt`). With the
  reader behind and the pipe full, the parent's write raised, and its
  traceback could not be printed to the same pipe. Not observed: the
  pipe's mode inside a real run.
- **A, why that became a hang.** Observed in three stalls: the parent in
  interpreter exit, waiting to join pool workers that were alive and idle.
  Demonstrated outside the suite: a worker inherits the runner's SIGTERM
  handler, and a terminate that arrives in the middle of a test is recorded
  as a test error and survived
  (`stalls/sigterm_mid_test_is_swallowed.py.txt`).

**Why this (c) is a fair regression for H-91.** The 13:26 run is the same
tip, the same 16 labels and the same worker count as the stalled ones, on
the same machine within the hour. The only difference is that the test
process's stdout and stderr were a regular file and its stdin `/dev/null`,
which I read from the running parent's file descriptors. Nothing inside
the test process differs. The log is unstamped at its source; the
timestamps in `c_apps_guards_b6fbdbea_file.log.gz` were added from the
side by a reader of that file (same 60,431 lines). The run found a test
database left by the stalled 13:04 run and replaced it (the log's first
lines).

**None of this is H-91's code.** H-91 changes log and print arguments. The
stalls need a real Chromium test, a piped output and a slow reader; they
are fixed in the test runner (H-107, `task/h107-pool-worker-sigterm`) and
at the cause in the renderer (H-110). From now a gate's output goes
straight to a file (SM, 2026-10-05).

Kept in `stalls/`: the logs of the two stalls of 2026-10-02 and of the
12:32 stall, the watchdog's process tree, the first hang record of
2026-10-02 (`HANG_RECORD.md`, whose SIGTERM "lead" for the parent was
wrong) with its tracebacks, the observer's file, `H107_RECORD.md` and the
three demonstration scripts.
(The commit hooks trimmed trailing whitespace and the final newline in
two of these captures, `D4_full_p2_observe.observe` and
`hang2_tracebacks.txt`; nothing else in them differs from the originals
in `GAP-d5-runs/`.)

## Mutants
Each puts one leak back and must be killed by the guard; P9 also breaks a
line a behaviour test reads. Before the gate, each mutant was confirmed
statically to be flagged by the guard's functions.

| Id | Guards | Result |
|---|---|---|
| P1 | billing/access_control.py: a user by id | KILLED, `FAILED (failures=1)` |
| P2 | billing/services.py: a user by id | KILLED, `FAILED (failures=1)` |
| P3 | billing/stripe_service.py: a subscription's user by id | KILLED, `FAILED (failures=1)` |
| P4 | billing/views.py: the requesting user by id | KILLED, `FAILED (failures=1)` |
| P5 | billing/tasks.py: a subscription's user by id | KILLED, `FAILED (failures=1)` |
| P6 | the rule covers a name, not only an address | KILLED, `FAILED (failures=1)` |
| P7 | the rule covers print() | KILLED, `FAILED (failures=1)` |
| P8 | the rule covers a keyword argument and any receiver | KILLED, `FAILED (failures=1)` |
| P9 | the price-drift line names the user by id (read by a behaviour test) | KILLED, `FAILED (failures=3)` |
| Q1 | users/mailerlite_service.py: the user by id | KILLED, `FAILED (failures=1)` |
| Q2 | classrooms/services/roster_import.py: a failed row by position, not by name | KILLED, `FAILED (failures=1)` |
| Q3 | assignments/tasks.py: the print names the submission, not the student | KILLED, `FAILED (failures=1)` |
| Q4 | scripts/: a one-off script's print names the user by id | KILLED, `FAILED (failures=1)` |
| Q5 | classrooms/serializers.py: the admin by id | KILLED, `FAILED (failures=1)` |

## For the verifier
- 1a's static pre-review at `f45dd0ef` was clean; its one note (N1, where
  the repository-wide guard lives) is `6a82ef41`.
- The two lines 1a pointed out are as intended: the roster import's "parsed
  row N" (the wording now says what N is) and the QA tool's Test Clock line
  (the clock id only).
- This branch and H-99 (ed) both edit `classrooms/serializers.py` and
  `classrooms/services/roster_import.py`; this branch's hunks are in the
  school-admin invitation functions and in `import_roster`'s loop header
  and except block, not in the placeholder-address code.
- The logs' addresses are test fixtures only.
- `assignments/tasks.py` line 1115 (1a's read for the SM, 2026-10-05): beta
  printed the bound method `submission.student.get_full_name`, whose repr
  carries the pupil's name; this branch prints `submission.id`, and the
  guard's shapes test has that row.
- Rule 15: (c) above is the regression; please don't repeat it. If you run
  anything with real Chromium tests, send its output to a file.
