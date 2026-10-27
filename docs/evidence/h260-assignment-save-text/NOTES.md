# H-260 notes

Security Engineer (db).

- **The row:** new in this pack, severity Low (information disclosure to the teacher who made the request: database
  error text, no credentials).
- **Source:** H-255's survey; the Senior Manager's follow-up assignment of 27 October 2026. It missed batch 29's
  cut-off (the Senior Manager, 27 October: batch 29 takes `2658ca13`, H-259 alone), so it lands in batch 30.
- **Branch:** `task/h259-h260-stripe-logs`, after H-259's gate-evidence commit `2658ca13`. Its commits are its own:
  tests `2174bd69`, code `cfff5b22`, then this docs commit.
- **Rulings (27 October):**
  1. A fixed sentence to the client; the class name to the server log at WARNING.
  2. The test route is the generated-draft save (below). The upload pipeline is in scope only if the quoted text
     reaches anything a client can read; if it stays in server logs, it is reported here and left.

## What the read found (at `2658ca13`)

`AssignmentSerializer.create` (`assignments/serializers.py:231`) wrapped its save in `except Exception as e` and
raised `serializers.ValidationError(f"Failed to create assignment and rubric: {e}")`. A database error's text names
tables, constraints and the row's values; the 400 carried it to the client as
`{"success": false, "message": "Failed to create assignment and rubric: duplicate key value violates unique
constraint …", "error": {}}`.

What reaches `create` (a `save()` with no instance):
- **The generated-draft save**, `POST assignments/generated-drafts/<message_id>/save`
  (`save_generated_assignment_draft`, `assignments/views.py:1716`): synchronous. The error becomes the 400 above.
  This is the leak, and the tests' route.
- **The upload pipeline**, `upload_assignment_file` (`assignments/file_uploads.py:150`), run by
  `upload_assignment_async`. The error is raised inside the task, and no client reads its text:
  - the tracked row stores `describe_background_task_error(e, fallback)` (`mark_processing_task_failure`), which
    passes through only the app's own user-facing errors; a `ValidationError` gets the infra sentence for its cause
    or the fallback;
  - the batch session's result stores the same (H-219);
  - the log line is `describe_error_for_log` (class and frames, H-208), and Celery's own failure line goes through
    H-218's filter.
  H-219's S1 test (`assignments/tests_h219_task_error_text.py`) already pins this for any error raised in that task.
  Report only, as ruled.

What does not reach it:
- The assignment create route (`POST assignments/`, the viewset at `assignments/views.py:315`): a POST there uses
  `AssignmentTextSerializer` (`get_serializer_class`), and the viewset's own `create` calls
  `Assignment.objects.create`, never `serializer.save()`.
- `assignments/services.py:1148` passes an instance (an update), and `views.py:1528` only validates.

## What changes

- `assignments/serializers.py`:
  - a module logger, `logging.getLogger(__name__)`;
  - `ASSIGNMENT_SAVE_FAILED`, "We couldn't save this assignment. Please try again, and contact support if the
    problem continues.";
  - in `create`'s except clause, `logger.warning("Assignment save failed: %s", type(e).__name__)`, then
    `raise serializers.ValidationError(ASSIGNMENT_SAVE_FAILED) from e`. The status (400), the rollback and the
    exception handler's own "API request refused: ValidationError (400)" line do not change.
- **Visible change:** a failed draft save now reads the fixed sentence where the database's text was.

## Not changed here (report only)

The same survey, by syntax tree, over every production module: a client answer (`Response`, `ValidationError`,
`ParseError` and the like) built inside an `except` clause from the caught exception, outside a `describe_*` or
coded-response helper. 25 sites at `cfff5b22`, each catching a narrow type:
- 5 catch the app's own refusals, written to be shown: `PlanAssignmentRefused` (`billing/serializers.py:329`),
  `LicenseRequestError` (`billing/serializers.py:2227`), `EnrollmentError` (`classrooms/serializers.py:762`,
  `classrooms/views.py:1435`, `:1625`).
- 20 catch `ValueError` and answer `str(exc)`: `billing/license_views.py` (10), `billing/views.py` (4),
  `billing/license_overage_offline_views.py` (2), `dashboard/views.py` (2), `billing/serializers.py:2178`,
  `billing/qa_time_travel.py:1018`, `billing/views_admin_credits.py:143`. Their services raise `ValueError` with
  sentences written for the user. A library's `ValueError` raised inside the same `try` (a parse or a conversion)
  would be quoted too; its text is usually the caller's own input. Not traced further.

## Tests

`assignments/tests_h260_assignment_create_text.py`, 4 tests, on `AIOutputAllowListFixture` with the generation
mocked (`GENERATE`). Each generates a draft, then saves it while `ModelSerializer.create` raises an `IntegrityError`
whose text names a constraint and a value (`h260_probe_constraint`, `h260-probe-title`); the save must answer 400.
- `test_the_400_carries_none_of_the_errors_text`: the body holds none of "duplicate key", the constraint, the value.
- `test_the_400_reads_the_fixed_sentence`: the body holds the sentence.
- `test_the_server_log_names_the_class_at_warning`: on `assignments.serializers`, in order: one record, WARNING,
  "IntegrityError" in its message, none of the three fragments, no exception attached.
- 1 control, `test_control_the_draft_stays_unsaved_and_saves_after`: no assignment is saved and the draft is not
  linked; the same draft then saves (201).

## Expected reds against `2658ca13` (written before the gate)

3 FAIL blocks, no ERROR, "Ran 4 tests". The control is green.
- `test_the_400_carries_none_of_the_errors_text`: "unexpectedly found in".
- `test_the_400_reads_the_fixed_sentence`: "not found in".
- `test_the_server_log_names_the_class_at_warning`: "no logs of level WARNING or higher triggered on
  assignments.serializers".

A development run on `2174bd69` (the tests) with the code at `2658ca13`: exactly that, "Ran 4 tests",
"FAILED (failures=3)". On `cfff5b22`: "Ran 4 tests", "OK"; and the whole `assignments` app with
`AutoGrader.tests_no_pii_in_logs`: "Ran 782 tests", "OK (skipped=13)".

## Mutants (expected sets traced against every assertion of the module)

8, in `mutate.py`, each run against the own module with `--keepdb` on `settings_worktree_mut`
(`test_h259_h260_stripe_logs_mut`). "LEAK" is "unexpectedly found in", "MISSING" is "not found in", "NO LOG" is
"no logs of level WARNING or higher triggered on assignments.serializers". The control passes under every mutant.

| Id | Break | Expected |
|---|---|---|
| A1 | the 400 quotes the error's text again (after the sentence) | the text test, LEAK |
| A2 | the 400 reads another sentence | the sentence test, MISSING |
| L1 | the log line names the text, not the class | the log test, MISSING |
| L2 | the log line names the class and the text | the log test, LEAK |
| L3 | the log line carries the traceback (`exc_info=True`) | the log test, "is not None" |
| L4 | the log line is `logger.exception` | the log test, "'ERROR' != 'WARNING'" |
| L5 | no log line | the log test, NO LOG |
| L6 | the log line drops to INFO | the log test, NO LOG |

`--check`: "8 mutants: every anchor found once, all parse".

## The seam

`seam_check.py`, by syntax tree, base `2658ca13`:
- the changed top-level units of `assignments/serializers.py` are exactly `import logging`, `logger`,
  `ASSIGNMENT_SAVE_FAILED` and `AssignmentSerializer.create`;
- `create` with its except clause's body blanked is unchanged;
- that clause is two statements: a `logger.warning` naming the error only as `type(e).__name__`, with no keyword
  (so no `exc_info`), then `raise serializers.ValidationError(ASSIGNMENT_SAVE_FAILED) from e`;
- `logger` is `logging.getLogger(__name__)`, and the sentence is one string constant.

It passes on the tip and stops on these bad inputs (temporary copies; the tree untouched):
- the base against itself;
- the log line naming `e`;
- the log line with `exc_info=True`;
- `logger.error` in place of `logger.warning`;
- the raise quoting `e` after the sentence;
- a change in `create` outside its except clause;
- the logger named otherwise;
- a change in `normalize`, a method not allowed to change.

A changed file other than the one is the gate script's changed-file check, not this one.

## Gate

- **Script:** `run_h260_gate.sh.txt`, H-259's form. The red run expects 3 blocks and "Ran 4 tests". The exit code is
  captured by the start command.
- **Before the test runs:** makemigrations, the changed-file list (only `assignments/serializers.py` outside `docs/`,
  the one test module, and `.secrets.baseline` when every changed line in it is a `line_number` or `generated_at`),
  then the seam check.
- **Own:** the one module.
- **Near (23 modules):** every test module that reaches `AssignmentSerializer`, the generated-draft save or the
  upload pipeline (`assignments.tests`, `tests_ai_output_allowlist`, `tests_question_numbers_in_order`,
  `tests_upload_pipeline`, `tests_upload_batch_billing`, `tests_h219_task_error_text` and others; the list is in the
  script), H-209's `tests_task_results_carry_no_text` and H-218's `tests_h218_celery_trace_filter`.
- **Guards:** the 20 of the H-259 gate, `tests_no_pii_in_logs`, `tests_log_scrubbing` and `tests_sentry_scrubbing`
  among them.
- **The mutants:** 8, with a 1320 s limit and the 300 s stall watchdog.
- The batch's full run is the real regression.
- **Base:** `2658ca13`.
