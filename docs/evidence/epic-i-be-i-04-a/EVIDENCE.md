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

1. **The temperature is not in the settings version.** The note listed it. It is a literal
   inside a shared request builder (`ai_processor/services.py:790`), not a named constant, and
   this slice does not change the grading service. It is covered by the release, as the plan's
   step 1 says for things written directly in the code. The four named constants are in.
2. **Code and tests were written together, not tests first.** The red run below is the new
   tests against the base's model, admin and settings without the migration.

## Limits

- No grade is labelled by this slice.
- The version covers a named list. Instruction text outside the prompt file, the reply schemas,
  the temperature and retry counts are not in it; only the release covers them, and the release
  is `none` until the host variable is known.
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
(`students.tests_grading_duration_migration`) and 24 guard modules: OK.

### Step 2, the 26 mutants

The failing test expected for each is the `EXPECTED` dictionary in `mutate.py`, written before
any run. G1 to G15 are on the settings version, L1 to L8 on the label's words, model, admin
screen, serializers and plan 07, M1 to M3 on the migration (each of those three builds its own
database from the mutated migration).

## Runs

None yet at this commit.
