# Merge-down of beta batches 8 to 12b into phase2/epic-a: how each conflict was resolved

Author: the Release Engineer (0b), 2026-10-07 to 2026-10-08. Verifier: Verifier 2.

**What was merged:** origin/beta `035e0a07` (batch 12b as pushed) into `phase2/epic-a` `a29d8cb4`, merge base `63c3da22`. Git found 9 conflicts in 4 files and merged 15 further files that both sides changed. Every conflict was resolved by hand, hunk by hunk; no side was taken whole where both had added lines.

## The nine conflicts

| # | File, place | Epic side | Beta side | Resolution |
|---|---|---|---|---|
| 1 | `students/exceptions.py`, `SubmissionProcessingInProgressError` | `pass`, then the classes `InsufficientCreditsMidBatchError` and `CourseNotReachableError` | `code = "submission_busy"` (H-133) | beta's `code` line replaces `pass`; the epic's two classes follow unchanged |
| 2 | `students/views.py`, imports | `ensure_gradable` | `grading_result_for_formatter` | both imports kept |
| 3 | `students/views.py`, student upload answer | the audit event `SUBMISSION_UPLOAD` and a teacher-shaped serializer | `StudentUploadAnswerSerializer` with context (H-141) | the audit event kept, then beta's serializer (a student's upload is answered in the student's shape and still audited) |
| 4 | `students/services.py`, imports | `GradingRun`, `GRADING_ASSIGNMENT_PROMPT`, `SubmissionEmptyError` | `REPLY_CORRECTED`, `is_readable_answer` | all kept (one parenthesised import from `ai_processor.services`) |
| 5 | `students/services.py`, imports | `ensure_gradable`, `LABEL_FIELDS`, `UNLABELLED` | `grading_result_for_formatter` | all kept |
| 6 | `students/services.py`, before `grade_engine` | `emit_grading_completed` and the `ensure_gradable` rubric refusal | `_refuse_unreadable_answers` and the unreadable-answers check (H-165) | `emit_grading_completed` kept; beta's helper added after it; in `grade_engine` the rubric refusal comes first (it needs no read of the answers), then the unreadable-answers check, both before the claim and before any paid call |
| 7 | `students/services.py`, `upload_answers_engine` | the extraction inside `billing_refund_scope`, deeper indentation | the same code without the scope, with `is_a_list_of_objects` (H-165) and `told_to_student` (H-133) | the epic's structure kept; beta's two semantic changes applied by hand: `not is_a_list_of_objects(extracted_answers)` and `told_to_student=is_student_self_upload` on the `_check_submission_open` call |
| 8 | `ai_processor/services.py`, parts route (about :3030) | `self._stamp_graded_by(...)`, then `run.keep_second_opinion` / `run.keep_answers` | `self._stamp_as_a_models(...)` | epic's lines kept whole (requirement 1 and 2) |
| 9 | `ai_processor/services.py`, single-pass route (about :4294) | `self._stamp_graded_by(...)`, `run.keep_answers` | `self._stamp_as_a_models(...)` | epic's lines kept whole (requirement 1 and 2) |

## The Senior Manager's seven requirements, each answered

1. **One grader-marking function.** The epic's `_stamp_graded_by` survives and beta's `_stamp_as_a_models` is removed, with its two call sites. It uses `grading_cache.UNNAMED_MODEL`, not the literal "llm" that beta's copy wrote. The two functions did the same thing otherwise (pop `from_cache`, ASSIGN `graded_by`); beta's reasoning about the repeat rule (H-154) is carried into `_stamp_graded_by`'s docstring so nothing is lost. No test calls `_stamp_as_a_models` by name (grep of the merged tree: only the H-154 mutation runner under `docs/evidence`, which is history).
2. **Call sites.** Both sites in `ai_processor/services.py` keep slice C's `run.keep_answers` and `run.keep_second_opinion` lines (parts route and single-pass route); beta's side of those two hunks was not taken. To be shown red-capable by the four modules `ai_processor.tests_grading_run_label`, `tests_grading_run_pipeline`, `tests_grading_run_checker` and `students.tests_grading_label_end_to_end`, which the gate runs first.
3. **`students/services.py` `_populate_and_save_grade`.** It auto-merged without a conflict and has the epic's form (the run argument, the label, the ONE update over `GRADING_RESULT_FIELDS`) with beta's third review source `ai_reply_corrected` and the unreadable-answers reason both added before `if reasons:`. `GRADING_RESULT_FIELDS` is unchanged (no diff line against the epic).
4. **`kept_source_evaluations`.** It is used only by `_kept_among`. The five callers of `_finalize_grading_result` (five call sites in `ai_processor/services.py`; read one by one) pick named keys; none copies the whole dictionary into saved feedback, so the internal key does not reach a stored grade.
5. **The vote is not changed.** The diff of the merged `ai_processor/services.py` against the epic holds no line that mentions a vote; the stated limit stays; follow-up row H-160 closes it.
6. **The backup flag's meaning** is said in the architecture note 03a when H-160 is done (not in this merge).
7. **H-127's and H-130's student projections arrive in `assignments/serializers.py`.** They do: `student_safe_feedback` is imported from `students.feedback_projection` and used for the student's reading of `feedback` (H-127), and the H-130 comment and branch (the stored column is not read before release) are present at the same place. Checked by name in the merged file.

## Lines both sides added, counted

For each of the 15 files both sides changed besides the conflict ones, every non-blank line either side added is present in the merged file (a missing count of 0). The only added lines not present are explained: in `ai_processor/services.py`, beta's `_stamp_as_a_models` (its 14 lines and its two call sites) removed on purpose (requirement 1); in `students/services.py`, beta's import line (now a longer parenthesised import with the epic's names), and the lines of conflict 7 that exist at a deeper indentation inside the epic's refund scope; the epic's import line, its `isinstance` check and its one-line `_check_submission_open` call, each replaced by the merged form above.

## Not done in this document

The gate (makemigrations, the four named modules, the guards from both sides, the one full run) and Verifier 2's verification are recorded in their own files in this folder when they exist.

## What the quick module gate found on the merge commit `79ab843c` (2026-10-08 12:12 to 12:13 WAT), and the two follow-up changes

Ran 316 tests, FAILED (failures=2, errors=15); every other module green (the four named modules of requirement 2 among them). Both causes are interactions that neither line could show alone; no production code changes.
1. **Beta's H-165 tests meet the epic's rubric gate (16 results in `students.tests_answers_unreadable`).** The epic refuses to grade an assignment with no marking guide (F5, `students/grading_gates.py`: 409 `RubricMissingError`, before the claim, in the grade route and in `grade_engine`). The H-165 fixtures build an assignment with no questions at all, so on the merged line `grade_engine` stops at the rubric gate (409) before reaching H-165's unreadable-answers check (400). The order of the two refusals stays as resolved in conflict 6 (rubric first, then the unreadable answers: both before the claim and before any paid call). The change is in the TEST fixture: `AffectedRowCase.setUp` gives the base's assignment one question with a marking guide (`paper.essay(1)`); nothing else about the tests changes.
2. **Beta's feedback guard meets the epic's audit function (1 result, `AutoGrader.tests_student_feedback_guard`, rule 1).** `students/services.py: emit_grading_completed` (slice C) reads the saved feedback column raw, for the model name of the audit event, and the guard (batch 10) insists that every raw reader be named. The function is named in `RAW_FEEDBACK_READERS` with who it serves (staff audit event; returns nothing to anyone). It reads one key, `grading_model`, and the event is not a student's.
