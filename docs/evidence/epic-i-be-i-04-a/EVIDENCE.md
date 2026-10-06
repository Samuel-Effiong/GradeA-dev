# BE-I-04 slice A: the settings version and the six label columns

Author: the Next-stage Builder, 2026-10-06. Branch `task/epic-i-be-i-04-a`, off `phase2/epic-a`
9a581258. Phase 2 line only. Verifier: the second reserve engineer, independent.
Design: `~/Documents/Projects/GAP-planning/BE-I-04-design-note.md` (accepted by the SM,
2026-10-06). Plan and rulings: `Next-Stage-Implementation-Plan-BE-I-04.md`, Part 10, same folder.

## What this slice is

BE-I-04 makes every grade carry a label: what produced it. Slice A adds the place for the label
and the settings version. **Nothing writes the label yet.** After this slice every submission
reads `unlabelled` in all six columns; the label is written with the grade in slice C.

| Piece | File |
|---|---|
| The label's words and the founder's sentence | `students/grading_label.py` (new) |
| One reading of the grading settings, its version, the release beside it | `ai_processor/grading_config.py` (new) |
| Six columns on `StudentSubmission` | `students/models.py`, migration `students/0031_submission_grading_label.py` |
| The columns are read-only in the admin screen | `students/admin.py` |
| The release setting, empty by default | `AutoGrader/settings.py` (`GRADING_RELEASE_ID`) |
| Dated notes | `docs/phase2/architecture/03a_data_model.md` (§2.12, §4.4), `05_epics_b_to_i_roadmap.md` (§3.8), `07_epic_i1_implementation_plan.md` (header) |

### The columns

All six: `CharField`, NOT NULL, Django default and database default both `unlabelled`, no index.
Lengths: prompt version 128, settings version 128, strictness 32, model 255, backup used 16,
release 64. `unlabelled` means no label recorded: graded before labels existed, or not graded.

### The settings version

`GradingConfig.read()` reads 14 Django settings and 4 code constants once and freezes them;
`version` is `cfg:` plus the first 12 hex digits of a SHA-256 over the sorted names and values.
A colon, never `@`: audit metadata drops anything shaped like an email address.

- **Left out on purpose, and named in the module:** the saved-answer store's switch and
  lifetime, and the release setting.
- **The release** is read with the settings, kept beside the version and never inside it.
  Empty or missing gives the word `none`. Which host variable feeds `GRADING_RELEASE_ID` waits
  for the founder's fact 3; until it is set every grading will record `none`.

### The founder's condition

"First form of the grading record (BE-I-04). A later stage improves on it: a table of grading
runs, built beside re-grading or feedback editing and filled from these fields." This sentence
is in the help text of each of the six columns; the same statement is in a comment block at the
fields, in the docstrings of both new modules, and in the three architecture documents. Tests
hold each of those places.

## Differences from the accepted design note

1. **Temperature: to be added in slice B (SM ruling, 2026-10-06).** The note listed it and this
   slice does not have it. It is a literal inside a shared request builder
   (`ai_processor/services.py:790`), not a named constant, and this slice does not change the
   grading service. I first wrote that the release covers it; the SM did not accept that: a
   release changes on every deploy, so it cannot say that a grade moved because the temperature
   moved. In slice B it becomes a named constant that both the grading call and `GradingConfig`
   read. Until then a change of temperature does not move the settings version. The four named
   constants are in.
2. **Code and tests were written together, not tests first.** The red run is the new tests
   against the base's model, admin and settings without the migration. Accepted by the SM for
   slice A only; for slices B and C the red tests are their own commit first, with its red run
   logged.

## Limits

- No grade is labelled by this slice.
- The version covers a named list. The temperature is not in it until slice B. Instruction text
  outside the prompt file, the reply schemas and retry counts are not in it at all; only the
  release covers those, and the release is `none` until the host variable is known.
- Whether adding six defaulted columns is instant on the live database depends on its
  PostgreSQL version (the founder's fact 1). Not measured here.
- The pinned version (`cfg:c039947043de`) pins how a version is computed from a fixed reading.
  It was computed with the module's own code before any test run.

## Expected results, written before any run

### Step 0, reproduce-first

The two new test modules against `students/models.py`, `students/admin.py` and
`AutoGrader/settings.py` as at 9a581258, with migration 0031 moved away, on a database built
fresh. Expected: non-zero exit, a "Ran" line, and these twelve tests failing or in error.

`ai_processor.tests_grading_config`:
- `test_every_grading_setting_is_in_the_version_or_named_as_left_out`
- `test_every_listed_name_is_a_declared_setting`
- `test_it_fits_the_column`
- `test_a_long_release_is_cut_to_the_column`

`students.tests_grading_label_fields`:
- `test_each_is_a_not_null_text_column_of_the_designed_length`
- `test_each_has_the_placeholder_as_both_defaults`
- `test_a_new_unsaved_submission_reads_the_placeholder`
- `test_each_column_carries_the_sentence_in_its_help_text`
- `test_the_model_and_both_modules_say_it_too`
- `test_each_column_is_not_null_with_the_placeholder_as_its_default`
- `test_the_six_columns_are_read_only_there`
- `test_they_are_read_only_for_a_real_request_object_too`

Every other test of the two modules is expected to pass there.

### Step 1

`makemigrations --check`: no changes. The two new modules, one near module
(`students.tests_grading_duration_migration`) and 26 guard modules: OK. The guard list is 0b's
for the Epic A line: 0b added `AutoGrader.tests_codederror_serialization` and
`audit.tests_sweep_beat_lock` to the 24 first proposed.

### Step 2, the 27 mutants

The failing test expected for each is the `EXPECTED` dictionary in `mutate.py`, written before
any run of a mutant. G1 to G15 are on the settings version, L1 to L9 on the label's words, model,
admin screen, serializers and plan 07 (L9 was added after run 1, for the assertion that run
showed to be wrong), M1 to M3 on the migration (each of those three builds its own
database from the mutated migration).

## If a run is interrupted

Step 0 rewrites three tracked files and moves migration 0031 out of the worktree; the mutants
rewrite files in place. Three things put the tree back (the second and third at 0b's request):
- each mutant is restored in a `finally` block of `mutate.py`;
- `mutate.py` turns SIGTERM, which `timeout` sends, into an ordinary exit, so that block runs;
- the gate script has a trap on EXIT that moves the migration back from its hold file and
  checks out, as committed, every tracked file steps 0 and 2 rewrite.
After a SIGKILL of `mutate.py` itself only the trap acts. The next start of either script
refuses to run on a tree that is not as committed.

## Runs

### Run 1 at 80adc937: RED, stopped at step 1 (a failed run, disclosed)

One grant from 0b, 2026-10-06 12:06:21 to 12:14:57. The script stopped itself at "not green".
The mutants did not run. Nothing was re-run. Logs in `run1_red_80adc937/`, whole. Three are
gzipped, byte-exact, because they have trailing whitespace that a commit hook would otherwise
strip (it did so once in the working tree; the files were restored from the index and compared
with the original before gzipping). sha256 of each, unpacked:
- `console.txt.gz` (the script's own output): `d6d4315d2ffbbfc4b7bb368248f43819f4abbe5db61179dece1b98ad155f99f4`
- `modules_and_guards.txt.gz`: `e08bdbb877953e32b319f5fc71389ecee698e0577f27e64ff9dc79786ec539c4`
- `prefix_base_code_failing.txt.gz`: `a2b56577a3495a23c32f87d54f2366f6eb228383435f4a069ba2f3e60326199b`

| Step | Result |
|---|---|
| 0 reproduce-first | exit 1: Ran 42 tests in 0.686s, FAILED (failures=12, errors=26). **Thirteen** tests failing, not the twelve named beforehand |
| 1a makemigrations --check | exit 0, No changes detected |
| 1 two new modules, one near module, 26 guards | exit 1: Ran 400 tests in 343.739s, FAILED (failures=7). Two tests, both mine; every guard module passed. No skips |
| 2 mutants | not run |

Load average 6.61 6.52 6.72 at the start; the script stopped before its end-of-run reading, and
5.11 9.38 8.97 was read by hand at 12:16:46.

What failed and why. Both faults were mine, in a test and a document, not in the code the slice
ships:

1. `test_each_document_says_first_form_and_names_the_later_table`. My dated note in
   `05_epics_b_to_i_roadmap.md` did not contain the words "table of grading runs"; it said "the
   table is then filled". The test was right and the document was short of the founder's
   wording. **This is also the thirteenth failure of step 0, which my list missed:** the test
   reads documents, which step 0 does not take back to the base, so it failed there for the same
   reason. Fixed in the note's text. With the note fixed the test passes in step 0 as well, so
   the list of twelve stands as first written.
2. `test_each_has_the_placeholder_as_both_defaults`, six failures, one per column. The test read
   `field.db_default.value` and got `None`: in this Django a field's `db_default` is the plain
   value that was given (`django/db/models/fields/__init__.py:222`), not an expression. I wrote
   that line while clearing a type-check finding, with no run behind it. The database's own
   default was right all along: `test_each_column_is_not_null_with_the_placeholder_as_its_default`
   reads it from the database and passed, and `makemigrations --check` was clean. Fixed in that
   one line: `assertEqual(field.db_default, UNLABELLED)`.

What I re-read before asking for the next run (SM's condition):
- `students/tests_grading_label_fields.py`, the changed line (73 at 80adc937), against Django's
  source as above.
- Every other assertion of the two new modules ran in run 1 at the tip and passed: step 1's
  only failures are the two tests above. In step 0 each of the twelve named tests failed and the
  others passed, the thirteenth apart. So no other assertion of mine is now without a run.
- The 27 mutants' expected tests have had no run. I re-read each against what run 1 showed the
  tests do; none changed. A replay of the phrase checks in plain Python (not a test run) shows
  all three documents and all three code files now hold the phrases, and plan 07 loses the
  phrase under mutant L8.
- L9 is new: the model loses one column's database default. It gives the corrected assertion a
  mutant of its own.

### Run 2

None yet at this commit.
