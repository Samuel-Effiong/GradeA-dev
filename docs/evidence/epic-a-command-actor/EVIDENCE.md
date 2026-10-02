# command_actor (H-69 plumbing on Epic A): evidence

Author: ed (Security). Branch `task/epic-a-command-actor`, on `phase2/epic-a` 3fff1382 (after
the bundle 5 merge-down). Source: `docs/evidence/h69-command-audit-survey/SURVEY.md`, "the main
recommendation".

## The problem

Outside a request every audit event is SYSTEM (S3's rule). So where a management command's
writes are recorded at all, the record does not say which super admin ran the command.

## What was built

`audit.context.command_actor(user, *, command)`, a context manager. Inside the block:

- an event that would have been SYSTEM is recorded as `user`. The rule lives in one place,
  `audit/emitter.py` `_build`: the history signals and `record_bulk` pass their actor to
  `emit`, so they are covered without touching `audit/history.py`;
- every stored event carries `metadata["command"]`, the command's name.

It raises `ValueError` before the block runs unless `user` is a saved, active SUPER_ADMIN with
`is_superuser` (the same rule as `resolve_licence_stripe_intent`'s `--by`) and `command` is a
plain module name that Django lists as a management command. `command` is in `ALLOWED_KEYS`
but in no action's `METADATA_ALLOWLIST`: the emitter adds it after the allow-list, so a call
site cannot supply it.

No command uses it yet. The first user is the resolve-intent audit emit (next slice).

### SM rulings recorded here (2026-10-02; they correct an earlier wording)

- `source` is **not touched**. It keeps its meaning: how the row was written (`create`,
  `save`, `bulk`, `delete` for history; the view class for `ADMIN_ACTION`). An earlier ruling
  spoke of a closed `source` set (command / beat / request); the code has no such set, and the
  SM withdrew that wording.
- A command's events are identified by `metadata["command"]` plus the operator as actor. That
  is the whole contract.
- Two cases that may surprise, each in the docstring and each with a test: a request's
  signed-in user is kept (the command key is still added), and an actor a call site passes to
  `emit()` is kept.

| Commit | What |
|---|---|
| 09141be9 | The code (first form: emitter and history both edited). |
| a8b2cbdd | `audit/tests_command_actor.py`. |
| 0124cc68 | Round 1's fix: `audit/history.py` back to its base byte for byte (the edit was redundant); the docstring's two cases; one isolating test for the name-shape check. |
| 3555050b, 8c7f1ed6 | `mutate.py` (15 mutants, then 13). |
| 5e4e1c5d | 0b's base update onto 3fff1382 (clean; no shared file). |
| 94064627, ada31929 | Round 3, test-only: the two tests v2's mutants needed; C16 and C17 in the harness. |

## Runs

All runs: rules 12, 13 and 16, `--settings=settings_worktree`, slots granted by 0b. Mutation
runs (rule 17): `PYTHONDONTWRITEBYTECODE=1`, the `__pycache__` of each mutated module's
directory deleted before each mutant and after each restore, own database
(`test_epic_a_command_actor_mut`, dropped afterwards), every anchor asserted unique and every
mutant parsed.

### The runs that count (round 2, at 5e4e1c5d)

2026-10-02 15:07–15:13 WAT, 6G.

| Step | Result | Log |
|---|---|---|
| 0. Reproduce-first: the new tests on 3fff1382's production files | FAILED (errors=1): `ImportError: cannot import name 'command_actor'` (the weak form: the module cannot be imported before the fix) | `prefix_3fff1382_failing.txt` |
| 1. The new module, every caller module from the grep, the Epic A guards and the guards that arrived with the merge-down (beat locks, commands, reason codes, redis hygiene, H-80's log guard, the sweep-lock test, the retention sweeps, the volume report) | 584 tests OK | `modules_and_guards.txt` (last 200 lines; full log sha256 prefix `2140b9cbbd9b8508`) |
| 2. Mutants C3–C15 | 13 of 13 killed, no survivors | `mutation_log.txt`, `mutation_results.json` |

| Mutant | Killed by |
|---|---|
| C3 emit ignores the command actor | `test_a_save_is_recorded_as_the_operator`; `test_an_explicit_emit_with_no_actor_is_recorded_as_the_operator`; `test_record_bulk_is_recorded_as_the_operator` |
| C4 emit overrides an explicit actor | `test_a_requests_signed_in_user_outranks_the_command_in_history`; `test_an_explicit_actor_is_kept_and_the_command_is_still_named` |
| C5 no command key in metadata | `test_a_requests_signed_in_user_outranks_the_command_in_history`; `test_a_save_is_recorded_as_the_operator`; `test_an_explicit_actor_is_kept_and_the_command_is_still_named` (+2 more) |
| C6 a call site can supply command | `test_a_call_site_cannot_supply_the_command_key` |
| C7 any user may be named | `test_a_school_admin_is_refused`; `test_a_super_admin_type_without_the_superuser_flag_is_refused`; `test_a_superuser_flag_without_the_super_admin_type_is_refused` (+3 more) |
| C8 an inactive super admin may be named | `test_an_inactive_super_admin_is_refused` |
| C9 the superuser flag is not needed | `test_a_super_admin_type_without_the_superuser_flag_is_refused` |
| C10 the super admin type is not needed | `test_a_superuser_flag_without_the_super_admin_type_is_refused` |
| C11 an unsaved user may be named | `test_an_unsaved_user_is_refused` |
| C12 any command name is accepted | `test_a_command_that_does_not_exist_is_refused`; `test_a_listed_command_with_a_free_text_name_is_refused`; `test_free_text_is_refused_as_a_command` |
| C13 a command need not exist | `test_a_command_that_does_not_exist_is_refused` |
| C14 the name shape is not checked | `test_a_listed_command_with_a_free_text_name_is_refused` |
| C15 the context is not reset | `test_a_command_that_does_not_exist_is_refused`; `test_a_listed_command_with_a_free_text_name_is_refused`; `test_a_school_admin_is_refused` (+9 more) |

### Round 3 (rule 15.4), at ada31929: v2's two test gaps closed

v2's verdict at 9a71836e (VERIFIED-WITH-NOTES, `VERIFICATION_epic_a_command_actor_9a71836e.md`)
found the production code right and two of v2's own mutants surviving my 21 tests:

- **Y2**: inside a block, a call site's own `metadata["command"]` value replaces the block's
  checked name. My test for a supplied `command` ran outside a block, where that branch is not
  taken. This is the one that matters: free text, even an address, into the trail past the
  allow-list.
- **Y1**: a nested block's exit clears the outer block's context. No test nested two blocks.

SM ruling: both tests go in before the merge, test-only. 94064627 adds
`TheBlocksNameCannotBeReplacedTests` (four supplied values, an address among them) and
`NestedBlocksTests` (the inner block ends, raises, or is refused). The two mutants joined my
harness as **C16** (Y2) and **C17** (Y1).

2026-10-02 15:50–15:52 WAT, 6G:

| Step | Result | Log |
|---|---|---|
| The touched module `audit.tests_command_actor` | 25 tests OK | `r3_module.txt` |
| Mutants C3–C17 (15) | 15 of 15 killed. C16 by `test_a_call_sites_command_value_never_reaches_the_event`; C17 by the two nested-block tests | `mutation_log.txt`, `mutation_results.json` (these now hold round 3; round 2's 13-mutant results are in history at a75c4cf7) |

The regression below was not repeated: only the test module changed after it.

### Regression (b)

2026-10-02 15:26–15:35 WAT, at a75c4cf7 (= 5e4e1c5d plus the round 2 logs; no code change),
12G, flock, timeout 3600, `--verbosity 2`, `RACE_COST_*` / `AUDIT_BENCH*` /
`ENABLE_GRADING_BENCHMARK` unset:

| Run | Result | Log |
|---|---|---|
| `manage.py test audit ai_processor students billing` | **Ran 3603 tests in 492.9s, OK (skipped=9)**. Per app: billing 2048, ai_processor 830, audit 363, students 362. | `regression_audit_ai_students_billing.txt` (last 200 lines; full log sha256 prefix `10025fbf33c9715f`, kept in `~/Documents/Projects/GAP-evidence-logs/`) |

`users`, `classrooms`, `assignments`, `dashboard` and `AutoGrader` are not in this run (SM
ruling); the next Gate 10 covers them and stays a hard gate before staging.

`audit/history.py` at the tip is the same blob as 3fff1382's (`4204766ceaf9…`), so the SM's
scope applies: audit + ai_processor + students + billing, without users.

## Disclosed: round 1 (at 3555050b, on cc22bc03), two survivors

2026-10-02 12:15–12:46 WAT. Prefix failed at import (errors=1); the new module, its callers
and the guards: 445 tests OK; mutants: 13 of 15 killed, **C1 and C14 survived**.

- **C1** (history ignores the command actor) survived because the emitter already turns a
  SYSTEM actor into the operator: my edit to `audit/history.py` was redundant. It was removed,
  not tested around.
- **C14** (the name-shape check) survived because every free-text sample was also not a
  listed command. `test_a_listed_command_with_a_free_text_name_is_refused` now isolates it.

The module step shows 27 minutes of wall time for 309 s of tests: the laptop suspended twice
on a lid close (12:21–12:26 and 12:26–12:40; rule 16's old prefix did not cover the lid). Not
a stall.

Files: `run1_3555050b_prefix_cc22bc03_failing.txt`, `run1_3555050b_modules_and_guards.txt`
(last 200 lines; full log sha256 prefix `4ce358d8a08e85c9`, kept in
`~/Documents/Projects/GAP-evidence-logs/`), `run1_survivors_3555050b_mutation_log.txt`,
`run1_survivors_3555050b_mutation_results.json`.

## Not verified here

- No management command uses `command_actor` yet, so no end-to-end command run is tested.
- Events written by code that passes an explicit actor (for example one that passes a request
  user) keep that actor inside a command block; that is the ruled behaviour, not an oversight.
- A task or thread started inside a block stays SYSTEM: the context does not cross into a
  Celery task or a new thread (v2's note 3). A command that wants the operator recorded must
  do its writes in-process.
- The author does not verify their own work: a verifier checks this.
