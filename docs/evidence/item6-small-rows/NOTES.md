# Item 6 notes (Builder 2 · 4d)

Written 2026-10-25 before any run. Base beta `b4af529f`; branch `task/item6-small-rows`,
worktree `Grade-Automator-Plus-item6-small-rows`. Plan: `GAP-4d-runs/item6/PLAN.md`, approved by
the Senior Manager with rulings (H-217 reading stands, constraints only; H-195 closed with the
note; H-226 red at the tests commit; H-229 in place; one evidence directory).

## The commits (tests → code → docs per row, then this pack)

| commit | what |
|---|---|
| 5c28777b | `.gitignore`: `.mypy_cache/` |
| d66c7f6c | H-228 test: asserts `StudentEmptyWalletError` |
| 58cdfdb6 | H-226 tests: the two assertions, no stand-in (RED) |
| 0d30ea07 | H-226 fix: the stand-in for `requests.post` |
| a375d7bf | H-217 tests: `AConstraintChangedInOneStep` (RED) |
| e7952f2c | H-217 code: `AddConstraint`/`RemoveConstraint` reported; `docs/MIGRATIONS.md` |
| 5038d71d, 68d92340, 723b5c25, 75986547, 4a5828be | docs: rows H-228, H-226, H-229; H-195 closed; H-217 |
| (pack) | this file, the runner, `mutate.py`, the `.mypy_cache` proofs |

Production files changed against the base: `.gitignore`, `scripts/check_migration_safety.py`.
Test files changed: `AutoGrader/tests_migration_safety_check.py` (+5 tests, 19 → 24),
`users/tests_credit_balance_permission.py` (20 tests, one assertion tightened),
`users/tests_rate_limited_envelope.py` (11 → 12 tests). The rest is under `docs/`.
The seam (gate step 1b) checks exactly that list.

## Expected reds, written before the run

**0a H-226, `users.tests_rate_limited_envelope` at 58cdfdb6** (the assertions without the
stand-in; the module at that commit has 12 tests): exit 1, `Ran 12 tests`, no OK line, exactly
two FAIL blocks:
- `test_google_auth_throttle`: `'Google sign-in failed. Please try again.' != 'Google did not return an ID token'`
  (the view's fallback text for a failed exchange, `users/views.py:167`, against the no-id_token
  answer at :2218).
- `test_the_google_throttle_probe_never_calls_google`: `Unexpected logs found: ['ERROR:users.views:Google OAuth token exchange failed`
  (the fragment stops there on purpose: the record carries `exc_info`, so the captured entry
  continues with a newline and the traceback before the closing quote; the tests commit's
  message wrote the closing `']`, which will NOT be adjacent in the log).
This run makes the real outbound attempt to oauth2.googleapis.com that the H-39 guard blocks
(or a DNS failure offline): expected here and only here; the gate counts the address's lines.

**0b H-228, the gap seen**: at the tip with H-225's P1 applied
(`users/permissions.py:51` → `raise EmptyWalletError(...)`) and the base's
`users/tests_credit_balance_permission.py`: exit 0, `Ran 20 tests`, OK. The red half of the proof
is mutant M228-1 (below), which runs the tightened test against the same P1.

**0c H-229, the stdlib test against the OLD runner** (`row_chain_f3fe0bdd.py`): exit 1,
`Ran 6 tests`, exactly two FAIL blocks:
- `test_A_fragments_from_two_different_subtest_blocks_are_refused`: `Lists differ: [] != ["test_split: not together in ONE failure block`
- `test_C_the_subtest_label_can_be_a_fragment`: `not in its own failure block: ['[two]']`
B, D, E, F green (controls).

**0d H-217, `AutoGrader.tests_migration_safety_check` at a375d7bf** (the five new tests against
the old check): exit 1, `Ran 24 tests`, exactly four FAIL blocks, no ERROR:
- `test_a_check_constraint_added_in_one_step_is_reported`: `Lists differ: [] != ['AddConstraint']`
- `test_a_unique_constraint_added_in_one_step_is_reported`: `Lists differ: [] != ['AddConstraint']`
- `test_a_constraint_dropped_in_one_step_is_reported`: `Lists differ: [] != ['RemoveConstraint']`
- `test_the_finding_names_the_constraint_and_the_two_step_way`: `0 != 1`
- control `test_the_two_step_way_itself_is_not_reported`: green before and after.

## Green steps on the tip
- 1a `makemigrations --check --dry-run`: no changes.
- 1b the seam as above.
- 1 own + near + the 21 guards (24 labels after de-duplication: `AutoGrader.tests_migration_safety_check`
  is both near and a guard), one run, OK; and the log holds NO `oauth2.googleapis.com` line
  (batches 17–26 held two attempts each).
- 1c `python scripts/test_strict_gate.py` (the Senior Manager's NEAR for H-217; `scripts/` has no
  `__init__.py`, the file says it is run as a script, outside Django's discovery): OK.
- 1d the H-229 test against the NEW runner (sha a92519b9…): `Ran 6 tests`, OK.

## Mutants (step 2), expected sets written before the run
In-repo, `mutate.py` in the throwaway tree `item6-mut` (one baseline, then each with `--keepdb`):
- M228-1 (`users/permissions.py`, P1 again) → `users.tests_credit_balance_permission` +
  `students.tests_h225_student_empty_wallet`: `test_student_is_refused_when_their_teacher_has_none`
  for `is not an instance of <class 'billing.errors.StudentEmptyWalletError'>`; and H-225's
  s1–s5, each block holding both `There aren't enough AI credits available for this.` and
  `Your answers were not submitted.` (H-225's chain config, P1: each first fails at its
  error-text assertion). c1, c2 and the other 19 unit tests green.
- M226-1 (`users/tests_rate_limited_envelope.py`, the stand-in on `requests.put`) →
  `test_google_auth_throttle` for `Expected 'put' to have been called once. Called 0 times.`
  (the call-count assertion comes right after the envelope check; the real exchange ran and the
  guard blocked it) and `test_the_google_throttle_probe_never_calls_google` for the
  `Unexpected logs found: [...]` fragment of 0a. (Not taken: leaving `.json()` unset — a MagicMock
  would be handed to Google's real verifier; unpredictable and pointless.)
- M217-1 (AddConstraint rule off) → the two `added` tests (`Lists differ: [] != ['AddConstraint']`)
  and `names_the_constraint` (`0 != 1`).
- M217-2 (RemoveConstraint rule off) → `dropped` (`Lists differ: [] != ['RemoveConstraint']`).
- M217-3 (a RunSQL judged by its state operations) → the control
  `test_the_two_step_way_itself_is_not_reported` (`Lists differ: ['AddConstraint'] != []`); the
  existing `test_raw_sql_and_data_migrations_are_not_judged` stays green (its RunSQL carries no
  state operation), and the pinned real-migration tables hold no constraint operation.
- M217-4 (the finding without the two-step words) → `names_the_constraint`
  (`'NOT VALID' not found in`).
Outside the repo, copies of the new runner (never the shared file):
- R1 (`all(` → `any(` per fragment) → test A red for its 0c fragment.
- R2 (the old BLOCK capture) → test C red for its 0c fragment.
Battery limit 90 s × 6 + 600 s with the 300 s stall watchdog; `.mypy_cache`, H-195 have no mutant
(nothing to mutate; said in the plan).

## H-120's harness (`docs/evidence/h120-migration-check-before-state/run_mutants.py`)
Still LIVE: its 13 anchors (K1–K13) each match today's `scripts/check_migration_safety.py`
exactly once, before and after the H-217 edit (checked by loading its MUTANTS table). It makes
its own worktree `Grade-Automator-Plus-h120-mut` and database `test_h120_mut`, runs
`AutoGrader.tests_migration_safety_check` per mutant. The gate runs it as step 2b when started
with `--h120` (the Senior Manager's call; about 13 × one module run).

## `.mypy_cache/`
`git check-ignore -v .mypy_cache/` (trailing slash: the folder does not exist in this worktree,
and a `dir/` pattern matches only a directory) at the base: exit 1; after the line: `.gitignore:6:.mypy_cache/`,
exit 0. Both outputs are in `mypy/`. The folder hid before only through mypy's own `.gitignore`
(`*`) written inside it. No test, no mutant.

## Disclosures
- The H-226 tests-commit message writes the assertNoLogs fragment with a closing `']`; the
  written reason used by the gate stops before it (above), because the entry is multi-line.
- My first docs chain stopped itself: the per-file scan read the whole 1,400-line backlog and
  tripped on six pre-existing fixture lines; the chain was restarted scanning the added lines
  only. The H-229 diff file was then rewritten by the whitespace hook (blank context lines carry
  a trailing space), so it is committed gzipped (`row_chain_h229.diff.gz`, round-trip checked);
  the three affected docs commits were rebuilt row by row and the rebuilt backlog compared
  equal to the first pass.
- H-217's row text is rebuilt (the original was never written down); the row and the commit
  messages say so.
- A candidate for the user, not taken (Senior Manager's ruling): `AddIndex` without
  CONCURRENTLY (and `RemoveIndex`, `RenameIndex`, `AlterModelTable`) are still passed in
  silence by the check; a non-concurrent index build takes a SHARE lock that blocks writes for
  its duration on a large table.
- Not shown by these tests: the migration check against a real new migration in CI (none is in
  the final batch; `scripts/strict_gate.py:875–885` and `.github/workflows/migration-safety.yml:66`
  both pass `--base`, so the change judges nothing today).
- No run of any kind has happened for this pack; every count above is from reading.

## Correction after the first gate run (2026-10-25 18:5x)
The first gate on 5dd35903 stopped at step 0c: test A was red with the right set (exit 1, Ran 6,
A and C), but my written fragment used `['test_split` where Python reprs a string that itself
holds single quotes with DOUBLE quotes: `["test_split: not together in ONE failure block: ...`.
Corrected here, in `gate.py` (RED_229, used by R1 too) and in the test's docstring; the test and
the runner change are untouched. Steps 0a and 0b of that run were as written (0a: Ran 12, both
reds for their reasons, the address named 8 times; 0b: Ran 20, OK). The whole gate is re-run from
0a on the corrected pack after a new GRANT. Lesson: reproduce each list-compare message in plain
Python before writing its fragment.
