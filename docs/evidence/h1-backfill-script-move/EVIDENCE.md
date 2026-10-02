# H1: the one-off Stripe schedule backfill is no longer a management command — evidence

Author: ed (Security). Branch `task/h1-backfill-script-move`, on `task/beta-batch-5` 83fe58ca.
Source finding: `docs/evidence/h69-command-audit-survey/SURVEY.md`, H1.

## The problem

`billing/management/commands/backfill.py` had no `Command` class. It was a module-level script,
so `python manage.py backfill` imported it and ran its loop: one live Stripe subscription
schedule per candidate row, plus a local `stripe_schedule_id` write, with no dry run and no
confirmation.

## The fix (SM ruling: move it, with a docstring saying it is never imported)

| Commit | What |
|---|---|
| 82a2c79a | `git mv` to `scripts/one_off_backfill_stripe_schedules.py` (`scripts/` is not a package). The docstring gains a "never imported, not a management command" section and the run line names the new path. The script's code is unchanged. |
| a7a07983 | `AutoGrader/tests_management_commands_are_commands.py`: 4 tests. Each project module under `management/commands/` is parsed (never imported) and must define `Command`; `backfill` is not in `get_commands()`; `scripts/` has no `__init__.py`. |
| 4f096a62 | `mutate.py` (B1–B3, file-add mutants). |
| 88f019d1 | Docs: `BACKEND_REFERENCE.md` and `billing-core.md` no longer list `backfill` as a command (v2's pre-review note, SM ruling); the test docstring states the guard's limit. |

Limit of the guard, stated in the test: a module that has a `Command` class and also
module-level side effects still passes. It only catches a script with no `Command` at all.

Behaviour change: `manage.py backfill` now fails with "Unknown command". The script still runs,
deliberately, via `manage.py shell < scripts/one_off_backfill_stripe_schedules.py`. It still has
no dry run; that is unchanged and belongs to H-69's "dry run as the default" rows.
Rollback: revert the merge; no migration, no data change.

## Runs

All runs: rules 12, 13 and 16, `--settings=settings_worktree`, slots granted by 0b.

### Gate (a), 2026-10-02 11:31–11:33 WAT, at 88f019d1 (6G)

| Step | Result | Log |
|---|---|---|
| 0. Reproduce-first: `backfill.py` back in `management/commands/` (83fe58ca's layout) | FAILED (failures=2 of 4): the sweep test and `test_backfill_is_not_a_management_command` | `prefix_83fe58ca_failing.txt` |
| 1. The module + all beta-line guards (addendum 2: a module moves) | 119 tests OK | `modules_and_guards.txt` |
| 2. Mutants B1–B3 | 3 of 3 killed, no survivors | `mutation_log.txt`, `mutation_results.json` |

In the prefix the real pre-fix file is checked out into the tree. The tests only parse it and
list command names; nothing imports it, so it never ran. It was removed again before step 1.

Mutation run (rule 17): the test subprocess ran with `PYTHONDONTWRITEBYTECODE=1`, and the
`__pycache__` of each touched directory was deleted before each mutant and after each restore.
Mutants ran on their own database (`test_h1_backfill_script_move_mut`, dropped afterwards). The
mutants add files, because the fix is a move; the files added hold a harmless stand-in, never
the live script. Each is asserted absent beforehand and deleted afterwards; the tree was clean
after the run.

| Mutant | Killed by |
|---|---|
| B1 `backfill.py` is back in `billing/management/commands/` | the sweep test; `test_backfill_is_not_a_management_command` |
| B2 another script with no `Command` in a commands dir (`ai_processor`) | the sweep test only |
| B3 `scripts/` becomes a package | `test_the_moved_script_cannot_be_imported_as_a_module` only |

### Gate (b), owning-app regression (12G, flock, timeout 3600)

2026-10-02 11:33–11:45 WAT, at 7616bbe9 (= 88f019d1 plus the gate (a) logs; no code change), with
`RACE_COST_*` / `AUDIT_BENCH*` unset:

| Run | Result | Log |
|---|---|---|
| `manage.py test billing AutoGrader` | Ran 2489 tests in 698.2s, OK | `regression_billing_autograder.txt` (last 200 lines; full log sha256 prefix `85b516df8db72ab1`, kept at `~/Documents/Projects/GAP-evidence-logs/h1_7616bbe9_regression_billing_autograder.txt`) |

## Import check

Nothing imports the old or the new module: no `commands.backfill`, no `call_command("backfill")`,
and the generic loaders (`load_command_class`, `get_commands`, `find_commands`, `import_module`,
`__import__`, `runpy`) do not reach it (survey, H1). Django's test discovery matches `test*.py`
only, so the script in `scripts/` is not collected.

## Not verified here

- The script itself was not run, here or anywhere: no test executes it, by design.
- Whether anyone has the old `manage.py backfill` in a runbook outside this repo is unknown.
- The author does not verify their own work: a verifier checks this.
