# AI-call record, slice 0: the shared parts (evidence)

Branch `task/epic-i-ai-call-record-0`, base `75d82620` (the merged line), tests first (see the commit list in section 4).
Builder: Next-stage Builder. Design: `AI-call-record-design-note.md` revision 6 and `-rev7.md`
(accepted by the Senior Manager, 8 October 2026). Nothing was run when this file was written;
the results are added by the gate (section 4).

## 1. What the slice does

Nothing a user can see. Four parts:

1. `ai_processor/vote.py`: `majority_model(votes, main)`, BE-I-04's vote rule (most votes; a tie
   goes to the main model, else the first by alphabet; an unnamed model loses ties), moved out of
   `GradingRun._grading_model`. `GradingRun` calls it; its answers are unchanged.
2. `ai_processor/step_run.py`: `StepRun`, what one non-grading step gathers (kept replies by
   model and the version of the prompt it answered, the two marks recorded at the provider call), the scope that carries it to
   `__ai_model` (a context variable), `require_step_scope` (a loud failure for a task type that
   needs a run when none is open) and the words. `ENFORCED_STEP_TASK_TYPES` is empty: no task
   type is enforced yet, so nothing changes.
3. `students/step_label.py`: the same words for the students app (no project imports).
4. Nothing else. `AIProcessor.execute_graded_task` and `__ai_model` are untouched (the gate checks
   `git diff` of `ai_processor/services.py` against the base is empty), and so are slice C's test
   modules.

## 2. Rules carried, and where they are held

| Rule | Held by |
|---|---|
| The vote moved with no change of answer | `tests_vote_helper`: a frozen copy of the old rule against the grading run over 400 seeded random patterns; slice C's `tests_grading_run_label` and `_pipeline` unchanged and green |
| One home for the rule | the grading run is shown to call the helper (a spy) |
| A provider name equal to a fixed word reads "unknown" | one test per word (`tests_step_run`) |
| No reply holds an item: the step reads "unknown" (Senior Manager, 8 October 2026, 5b) | `test_a_kept_reply_with_no_item_reads_unknown_even_when_it_is_named` |
| The scope is restored after any exception; no leak between two steps, two threads, two contexts | `tests_step_run_scope` |
| A call needing a run with none open fails loudly | `test_a_task_type_that_needs_a_scope_raises_when_none_is_open` |
| The words equal between the two apps; the words module imports nothing from the project | `students/tests_step_label_words`, `tests_step_run` |

## 3. Mutants (33), the failing test named before any run

| Mutant | File | What is broken | Test that must fail |
|---|---|---|---|
| V1 | ai_processor/vote.py | a tie goes to the first by alphabet, not to the main model | `test_a_two_way_tie_goes_to_the_main_model` |
| V2 | ai_processor/vote.py | a tie goes to the LAST by alphabet | `test_a_tie_without_the_main_model_goes_to_the_first_by_alphabet` |
| V3 | ai_processor/vote.py | an unnamed model wins a tie against a named one | `test_an_unnamed_model_loses_a_tie_to_a_named_one` |
| V4 | ai_processor/vote.py | a model with zero votes can lead | `test_only_zero_votes_give_none` |
| V5 | ai_processor/vote.py | the model with the FEWEST votes wins | `test_the_model_with_most_votes_wins` |
| V6 | ai_processor/vote.py | the helper changes the votes it was given | `test_the_input_is_not_changed` |
| V7 | ai_processor/grading_run.py | the grading run keeps its own copy of the rule instead of calling the helper | `test_the_run_calls_the_helper_with_the_counts_and_the_main_model` |
| V8 | ai_processor/grading_run.py | the grading run lets a helper answer of None through | `test_a_helper_answer_of_none_reads_unknown` |
| V9 | ai_processor/grading_run.py | the grading run gives the helper no main model | `test_the_run_calls_the_helper_with_the_counts_and_the_main_model` |
| V10 | ai_processor/grading_run.py | the grading run forgets the reused answers' votes | `test_the_same_model_over_many_random_patterns` |
| S1 | ai_processor/step_run.py | a provider name equal to a fixed word is kept as a name | `test_unlabelled_as_a_providers_name_reads_unknown` |
| S2 | ai_processor/step_run.py | not_run is not among the fixed words | `test_not_run_as_a_providers_name_reads_unknown` |
| S3 | ai_processor/step_run.py | a new attempt keeps the votes of the one before | `test_a_new_attempt_starts_from_nothing` |
| S4 | ai_processor/step_run.py | a new attempt keeps the marks of the call before | `test_a_new_attempt_starts_from_nothing` |
| S5 | ai_processor/step_run.py | a negative count votes | `test_a_negative_count_votes_for_nobody` |
| S6 | ai_processor/step_run.py | a reply that supplied no item is not a kept reply | `test_a_reply_that_supplied_no_item_is_kept_but_does_not_vote` |
| S7 | ai_processor/step_run.py | nothing kept reads unknown instead of None | `test_nothing_kept_gives_none_so_the_caller_chooses_its_word` |
| S8 | ai_processor/step_run.py | only unnamed replies give None instead of unknown | `test_only_unnamed_replies_read_unknown` |
| S9 | ai_processor/step_run.py | a reply can be marked received before the call left | `test_a_reply_cannot_be_marked_before_the_call_left` |
| S10 | ai_processor/step_run.py | the reply's name is recorded raw, a fixed word included | `test_a_reply_named_with_a_fixed_word_records_unknown` |
| S11 | ai_processor/step_run.py | the scope is not restored after an exception | `test_the_scope_is_restored_after_an_exception` |
| S12 | ai_processor/step_run.py | leaving a scope clears the variable, not restoring the outer scope | `test_a_nested_scope_restores_the_outer_one` |
| S13 | ai_processor/step_run.py | a call with no scope never fails | `test_a_task_type_that_needs_a_scope_raises_when_none_is_open` |
| S14 | ai_processor/step_run.py | a task type that is not enforced fails without a scope | `test_a_task_type_that_is_not_enforced_passes_without_a_scope` |
| S15 | ai_processor/step_run.py | a task type is enforced already in slice 0 | `test_in_slice_0_nothing_is_enforced_yet` |
| S16 | ai_processor/step_run.py | grading is counted among the step task types | `test_grading_is_not_a_step_task_type` |
| P1 | ai_processor/step_run.py | a kept reply does not record its prompt version | `test_one_reply_with_a_version_gives_that_version` |
| P2 | ai_processor/step_run.py | a new attempt keeps the prompt versions of the one before | `test_a_new_attempt_forgets_the_prompt_versions` |
| P3 | ai_processor/step_run.py | the prompt version of the last reply wins, not the most used | `test_the_version_most_replies_used_wins` |
| P4 | ai_processor/step_run.py | a reply votes for its prompt version once per item | `test_a_reply_votes_once_however_many_items_it_supplied` |
| L1 | students/step_label.py | a word of the students app differs from the AI-processor's | `test_the_words` |
| L2 | students/step_label.py | the words module imports from the project | `test_no_project_import` |
| L3 | students/step_label.py | the failed result is missing from the re-check's results | `test_the_recheck_results_are_these_four_and_unlabelled` |

## 4. Results

(To be filled by the gate: the red run, the modules and guards run, the mutants, the seam check.)

## 5. Stated limits

* The red run shows the four new modules failing to LOAD against the code as at the tests-only
  commit (the modules they import did not exist). Per-test red is shown by the 33 mutants, each
  of which must fail the named test (rule 19).
* `GradingRun` does not adopt the fixed-word rule for a provider name ("unknown" for a name that
  equals "unlabelled", "unknown" or "not_run"): that would change slice C's behaviour. Only
  `StepRun` has it. (Design note section 2; BE-I-04 has no such rule today.)
* A future caller of `__ai_model` that forgets the scope loses its marks silently, until its task
  type is in `ENFORCED_STEP_TASK_TYPES`; each later slice enforces its own.
* `StepRun` takes the main model as an argument of `model()`; where the main model comes from for
  a step is decided by that step's slice.
