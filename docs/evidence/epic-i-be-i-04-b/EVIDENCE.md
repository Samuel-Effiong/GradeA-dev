# BE-I-04 slice B: when a saved AI answer may be reused

Author: the Next-stage Builder, 2026-10-06. Branch `task/epic-i-be-i-04-b`, first off slice A's
frozen tip 58326e45, then on `phase2/epic-a` cfe55a0f, which holds slice A as merged (base update
9827f87a, a clean merge; against cfe55a0f the branch differs outside this folder in seven files,
all under `ai_processor/`).
Phase 2 line only. Verifier: the Next Stage Checker, independent.
Design: `~/Documents/Projects/GAP-planning/BE-I-04-design-note.md`, with the SM's rulings.

## Order of work (SM ruling: red tests are their own commit first)

1. **This commit: the tests only.** `ai_processor/tests_grading_cache_key_v2.py`, 31 tests, and
   this file. No production code is changed by it.
2. Then the code, in later commits.
3. The red run of these tests against the code as at this commit is logged before the code's
   own gate, as that gate's first step.

## Expected at this commit, written before any run

The module loads (it imports only names that exist today). Of its 31 tests, 24 are expected to
fail or be in error and 7 to pass.

Expected to FAIL (24):

`WhatMustMatchTest` (10 of 12):
- `test_edited_teacher_instructions_are_a_fresh_grade`
- `test_teacher_instructions_added_where_there_were_none`
- `test_an_edited_assignment_title_is_a_fresh_grade`
- `test_edited_assignment_instructions_are_a_fresh_grade`
- `test_a_changed_additional_note_on_the_question_is_a_fresh_grade`
- `test_a_changed_blooms_level_on_the_question_is_a_fresh_grade`
- `test_a_changed_question_image_is_a_fresh_grade`
- `test_a_field_of_the_question_nobody_listed_is_part_of_the_match`
- `test_a_changed_grading_setting_is_a_fresh_grade`
- `test_a_changed_grading_prompt_is_a_fresh_grade`

`OneReadingPerRunTest` (2 of 3):
- `test_the_settings_are_read_exactly_once_for_a_run`
- `test_a_retried_run_still_reads_them_once`

`TheSavedAnswersEnvelopeTest` (5 of 5):
- `test_the_model_that_answered_is_stored_beside_the_evaluation`
- `test_a_reply_that_names_no_model_is_stored_with_none`
- `test_the_keys_are_version_two`
- `test_an_entry_that_is_not_an_envelope_is_a_miss`
- `test_a_reused_answer_is_marked_by_our_code_not_by_its_content`

`AMarkerInsideTheReplyIsNeverKeptTest` (3 of 3):
- `test_a_graded_by_in_the_reply_is_replaced_by_the_model_that_answered`
- `test_a_from_cache_in_the_reply_is_dropped_and_the_answer_is_stored`
- `test_no_place_in_the_grading_service_keeps_a_replys_own_graded_by`

`TheTemperatureIsPartOfTheSettingsVersionTest` (4 of 4):
- `test_it_is_a_named_constant_of_the_grading_service`
- `test_the_call_that_leaves_the_app_uses_the_constant`
- `test_it_is_in_the_settings_version`
- `test_changing_it_changes_the_version`

Expected to PASS today (7). They hold behaviour the change must keep, and each needs a mutant
later to show it has teeth:
- `WhatMustMatchTest.test_the_same_submission_again_is_reused`
- `WhatMustMatchTest.test_another_assignment_with_the_same_question_is_a_fresh_grade`
- `WhatMustNotBreakTheMatchTest.test_a_new_release_still_reuses_the_saved_answer`
- `WhatMustNotBreakTheMatchTest.test_teacher_instructions_that_are_switched_off_do_not_count`
- `WhatMustNotBreakTheMatchTest.test_whitespace_around_teacher_instructions_does_not_count`
- `WhatMustNotBreakTheMatchTest.test_the_other_questions_are_not_part_of_the_match` (the stated
  limit)
- `OneReadingPerRunTest.test_a_setting_changed_during_the_call_does_not_split_lookup_and_store`

These expectations come from reading today's code, with no run behind them. Where the red run
differs, the difference is reported as a difference.

## The code (commits after 17890114)

| Piece | File |
|---|---|
| The key, version two, and the envelope | `ai_processor/grading_cache.py` |
| What a run carries: one reading of the settings and the prompt version | `ai_processor/grading_run.py` (new) |
| The run started above the retry loop and passed down; the grader marker assigned, not defaulted; the temperature as a constant | `ai_processor/services.py` |
| The temperature in the settings version | `ai_processor/grading_config.py` |
| Older tests moved to the new signatures; the settings-version pin | `ai_processor/tests_grading_cache.py`, `ai_processor/tests_grading_config.py` |

- **The key** now holds: its version (`v2`), the intended model, the assignment's id, the prompt
  version, the settings version, the assignment's title and instructions, the teacher's extra
  instructions as spliced, the whole question as serialised into the prompt, and the answer with
  outer whitespace removed (as before).
- **Stated limit, also in the module:** the key does not hold the other questions, the other
  answers or the answer's place in a batch. A test pins this.
- **The release is not in the key,** so a deploy does not empty the store. A test pins this.
- **One reading per run.** `extract_grade_with_retry` starts a `GradingRun` above its retry loop.
  The lookup, the store, the teacher-instructions splice and every attempt use it. A direct
  caller of an inner function with no run gets one for that call.
- **The envelope.** A stored value is `{"evaluation": ..., "served_model": ...}`, written by our
  code. Anything else under a key is a miss. A reused answer's `graded_by` comes from the
  envelope.
- **The marker.** `graded_by` is assigned from the response our code read, and a `from_cache` in
  a fresh reply is dropped, at both places a reply is parsed (batch and single pass), before any
  later step reads them.
- **The temperature** is `AI_TEMPERATURE` in the grading service, used by the one provider call
  and read by `GradingConfig` (SM ruling). The pinned settings version changes from
  `cfg:c039947043de` to `cfg:d09ab0c0d559` for that reason. No grade carries a version yet, so no
  stored value stops matching.

## What an operator must know

**This slice empties the store of saved answers once, when it reaches a service.** Every key
changes (`v1` to `v2`), so nothing saved before is found again; old entries expire by their own
lifetime (three days by default). Until the store refills, more answers are sent to the AI, and
AI cost rises for a while. The founder's representative accepted this (decision 3). Its size
cannot be worked out without live figures. Nothing else changes for users or the frontend.

## Limits

- **A limit of release 1 (slices A and B without C), by the SM's ruling.** The saved-answer key
  comes from the run's one reading of the settings. Of the grading code itself, only the
  teacher-instructions switch reads that reading in this slice; the other fifteen places still
  read their setting live, and move to the reading in slice C. In a running service the two
  cannot differ: these settings are taken from the environment when the process starts and
  change only at a restart, so within one process the live value and the reading are the same.
  They can differ only where something changes a setting inside a living process, which the
  tests do on purpose and the service does not.
- The key's intended model is still the main model, not the one that answered. The envelope
  records the one that answered.
- A provider named `llm` could not be told from "not named". No such model name is known.
- The answer is matched with outer whitespace removed, as before; inner differences count.
- Nothing is labelled yet; the envelope's `served_model` is first read in slice C.

## One test added with the code

`TheSpliceUsesTheRunsReadingTest.test_a_switch_flipped_after_the_run_started_is_not_seen` was
not in the red commit. I found, while writing the mutants, that no test would catch the splice
reading the live switch. It is expected to be in error in the red run (it imports the new
module), so the red run should show **25** failing: the 24 named above and this one.

## Expected for the gate, written before any run

- Step 0, the red run (the test module against the code as at 17890114): non-zero exit, a "Ran"
  line, 32 tests, the 25 failing as above and the 7 passing as above.
- Step 1a: no model or migration is changed; makemigrations clean.
- Step 1: three changed test modules, thirteen near modules, 26 guard modules: OK.
- Step 2: 23 mutants; the failing test expected for each is `EXPECTED` in `mutate.py`.
- Step 3, its own grant: `ai_processor` and `students`, the two apps that call this code.
  **Replaced (SM ruling, 2026-10-06 17:04):** the grading tasks that call this code live in
  `assignments` too, so the regression is one full run by 0b on the frozen tip, as for slice A.
  Step 3 of the script was never run.

By the SM's ruling no gate run for this slice was asked for before the Checker's verdict on
slice A; that verdict came on 2026-10-06 and slice A was merged at 17:00.

The gate script is committed here as `run_be_i_04_b_gate.sh.txt`, a copy of
`~/Documents/Projects/GAP-builder-scripts/run_be_i_04_b_gate.sh` as it stands for the run.

This slice changes no model field, no migration and no line of `AutoGrader/settings.py`. It adds
one code constant (`AI_TEMPERATURE`) to the settings version.

## Runs

### The gate at e7d4b376: GREEN

One grant from 0b, 2026-10-06 17:15:49 to 17:21:43, step 1 of the committed gate script. One run,
all serial, 6G scope, rules 12, 13, 16, 17 and 18. Not stopped, not repeated, nothing of the
team's beside it. This was the first run of any test of this slice.

| Step | Result | Log |
|---|---|---|
| 0 the red run: the slice's test module against the code as at 17890114 | exit 1 as expected: Ran 32 tests in 0.615s, FAILED (failures=28, errors=1). **Exactly the 25 tests named beforehand** fail, compared by name by program (none missing, none unexpected); the other 7 pass | `red_run_code_as_at_red_commit.txt.gz` |
| 1a makemigrations --check | exit 0, No changes detected | `makemigrations_check.txt` |
| 1 three changed modules, thirteen near modules, 26 guards | exit 0: Ran 606 tests in 173.473s, OK. No skips | `modules_and_guards.txt.gz` |
| 2 the 23 mutants | 23 KILLED, 0 SURVIVED, 0 BROKEN | `mutation_log.txt`, `mutation_results.json`, `mutant_logs/` |

- Load average 2.64 4.27 3.90 at the start, 4.27 4.88 4.41 at the end. No test here asserts on
  the wall clock (0b read the one comparison in the benchmark module: a ratio of words).
- Step 0's 28 failures and 1 error are per sub-test: one of the 25 tests has five sub-cases. The
  one error is the test added with the code, which imports the new module.
- `modules_and_guards.txt`: "Ran" is line 10922 and "OK" line 10924 of 10925.
- 26 logs are gzipped, byte-exact, because a commit hook would alter them (trailing whitespace
  or a blank last line); each one's sha256, taken before gzipping, is in
  `gzipped_logs_sha256.txt`. `console.txt.gz` is the gate script's own output.
- After the run the source was as committed and the mutation database was dropped.
- Pattern check of the new files, by program, values never printed: no URL with a password
  part. Eighteen NAME=value matches, all in tracebacks of two files (the red run and mutant
  T1): the names are `token_count`, `node_tokens`, `single_pass_mode` and a token-budget
  constant, and each "value" is code text (letters, dots, brackets, one number). None is a
  credential.

How each mutant was judged: KILLED needs a non-zero exit, the inner run's own "Ran" line, no
test module that failed to load, and the expected test among the failing ones. All 23 inner runs
exited 1 with "Ran 73 tests"; none of the 23 logs holds a load failure. The expected names were
in the runner since 2401d461, before any run.

| Mutant | What is broken | Result | Expected test, found among the failing |
|---|---|---|---|
| K1 | the teacher's extra instructions are left out of the key | KILLED, Ran 73 tests | `test_edited_teacher_instructions_are_a_fresh_grade` |
| K2 | the assignment's title is left out of the key | KILLED, Ran 73 tests | `test_an_edited_assignment_title_is_a_fresh_grade` |
| K3 | the assignment's instructions are left out of the key | KILLED, Ran 73 tests | `test_edited_assignment_instructions_are_a_fresh_grade` |
| K4 | only the question's text is in the key, not the whole question | KILLED, Ran 73 tests | `test_a_changed_additional_note_on_the_question_is_a_fresh_grade` |
| K5 | the prompt version is left out of the key | KILLED, Ran 73 tests | `test_a_changed_grading_prompt_is_a_fresh_grade` |
| K6 | the settings version is left out of the key | KILLED, Ran 73 tests | `test_a_changed_grading_setting_is_a_fresh_grade` |
| K7 | the assignment is left out of the key | KILLED, Ran 73 tests | `test_another_assignment_with_the_same_question_is_a_fresh_grade` |
| K8 | the answer is left out of the key | KILLED, Ran 73 tests | `test_miss_on_different_answer` |
| K9 | the release is put into the key | KILLED, Ran 73 tests | `test_a_new_release_still_reuses_the_saved_answer` |
| K10 | the key takes the teacher's raw text, not the text as spliced | KILLED, Ran 73 tests | `test_teacher_instructions_that_are_switched_off_do_not_count` |
| K11 | the keys stay at version one | KILLED, Ran 73 tests | `test_the_keys_are_version_two` |
| R1 | the store takes a fresh reading of the settings | KILLED, Ran 73 tests | `test_a_setting_changed_during_the_call_does_not_split_lookup_and_store` |
| R2 | each attempt of the retry loop takes its own reading | KILLED, Ran 73 tests | `test_a_retried_run_still_reads_them_once` |
| R3 | the pipeline ignores the reading it is given | KILLED, Ran 73 tests | `test_the_settings_are_read_exactly_once_for_a_run` |
| S1 | the splice reads the live switch, not the run's reading | KILLED, Ran 73 tests | `test_a_switch_flipped_after_the_run_started_is_not_seen` |
| E1 | anything that is a dictionary is taken for an envelope | KILLED, Ran 73 tests | `test_an_entry_that_is_not_an_envelope_is_a_miss` |
| E2 | a reused answer keeps the marker inside its evaluation | KILLED, Ran 73 tests | `test_a_reused_answer_is_marked_by_our_code_not_by_its_content` |
| E3 | the envelope names the intended model, not the one that answered | KILLED, Ran 73 tests | `test_the_model_that_answered_is_stored_beside_the_evaluation` |
| E4 | a reply that names no model is stored as "llm" | KILLED, Ran 73 tests | `test_a_reply_that_names_no_model_is_stored_with_none` |
| B1 | a graded_by in the AI's own reply is kept | KILLED, Ran 73 tests | `test_a_graded_by_in_the_reply_is_replaced_by_the_model_that_answered` |
| B2 | a from_cache in the AI's own reply is kept | KILLED, Ran 73 tests | `test_a_from_cache_in_the_reply_is_dropped_and_the_answer_is_stored` |
| T1 | the provider call uses a literal temperature again | KILLED, Ran 73 tests | `test_the_call_that_leaves_the_app_uses_the_constant` |
| T2 | the temperature is left out of the settings version | KILLED, Ran 73 tests | `test_it_is_in_the_settings_version` |

## The delta after the Checker's reading (SM ruling, 2026-10-06 17:28)

**The Checker found this by reading, before any verdict.** The whole answer object is sent to
the AI (`json.dumps` of each answer: `ai_processor/services.py:2665`, `:2684`, `:3907-3917`), but
the key held only the answer's text. Two answers with the same text and a different
`answer_status` (blank against "not found in the document") or different `transcription_notes`
("partly illegible") shared a key, so the second student would get the first one's saved grade
although the AI would have been told something different. My own "the answer" in the key was the
text alone; the ruling that named "the whole question as sent and the answer" was only half
followed.

**Ruling:** the key also takes `answer_status` and `transcription_notes`. It does NOT take
`source_page`, `confidence` or the answer's own copy of `question_text`: they differ from student
to student for the same text, and matching on them would end all reuse. Those three are a stated
limit. What is sent to the AI does not change.

**Order of work, as for the slice:** the red tests are this commit, alone; then the code; then
mutants with their expected failing tests named first.

### The delta's red tests, and what is expected of them (written before any run)

`TheAnswerAsSentTest` in `ai_processor/tests_grading_cache_key_v2.py`, 12 tests. Against the code
as at 59990797:

Expected to FAIL (5):
- `test_the_same_text_with_a_different_status_is_a_fresh_grade`
- `test_the_same_text_with_different_notes_is_a_fresh_grade`
- `test_a_status_against_no_status_is_a_fresh_grade`
- `test_notes_against_no_notes_is_a_fresh_grade`
- `test_an_inner_difference_in_the_notes_is_a_fresh_grade`

Expected to PASS today (7), each needing a mutant later:
- `test_the_same_status_and_notes_are_reused`
- `test_a_different_page_and_confidence_do_not_break_the_match` (stated limit)
- `test_the_answers_own_copy_of_the_question_text_does_not_break_the_match` (stated limit)
- `test_no_status_is_one_thing_however_it_is_written`
- `test_no_notes_is_one_thing_however_it_is_written`
- `test_outer_whitespace_of_the_notes_is_not_compared`
- `test_a_status_that_is_not_text_does_not_crash_the_grading`

**How "nothing said" is handled, a decision of mine stated for the verifier:** a field that is
missing, `None`, empty or only whitespace is one and the same thing for the match. The text sent
to the AI does differ between those forms (a missing key, `null`, `""`), and I judge that none of
them tells the AI anything. Outer whitespace of the notes is not compared, as for the answer's
text; an inner difference is.

### Three more items in the same delta (SM, from the Checker's reading)

A second tests-only commit, before any code of the delta: `TheChunkedPathTest` (4 tests) and
`ThePartsOfTheKeyCannotRunTogetherTest` (5 tests) in the same module. I wrote these tests; the
Checker probes on its own. Expected against the code as at 59990797, written before any run:

Expected to FAIL (1), where the behaviour is new:
- `test_a_separator_inside_a_part_cannot_pass_for_a_boundary`: today each part is followed by a
  NUL byte, so a part ending in NUL beside an empty part gives the same bytes as the part beside
  a part that is one NUL.

**Corrected before any run (in the code commit, not the tests commit):** the tests commit
88884176 named a second expected failure,
`test_the_last_part_of_the_context_and_the_answer_cannot_run_together`. That was wrong, by my
own reading afterwards: the question lies between the teacher's instructions and the answer in
the key, so those two parts are not neighbours and the test passes on the old code. It is
expected to pass. So: 1 expected to fail, 8 to pass.

Expected to PASS today (8); the code already does it, and a mutant with its expected failing
test named first is the proof:
- `test_the_last_part_of_the_context_and_the_answer_cannot_run_together`
- `test_the_first_long_paper_is_marked_in_parts` (a guard on the fixture)
- `test_an_identical_second_long_paper_makes_no_provider_call`
- `test_a_graded_by_in_a_chunks_reply_is_replaced`
- `test_a_from_cache_in_a_chunks_reply_is_dropped_and_all_are_stored`
- `test_a_character_moved_across_a_boundary_changes_the_key`
- `test_the_model_and_the_assignment_cannot_run_together`
- `test_the_same_parts_give_the_same_key`

A NUL cannot be stored in a text column of this database, so the weakness the two red tests show
is not one a teacher could reach today; the key is made unambiguous all the same.

### The delta's code

`ai_processor/grading_cache.py`: the key takes `answer_status` and `transcription_notes`
(`_said`: missing, `None`, empty and whitespace-only are one thing; outer whitespace not
compared; a value that is not text is serialised, never refused), and its twelve parts are
hashed as one JSON list, so two neighbouring parts cannot run together and nothing inside a
part can pass for a boundary. The key version stays `v2`: no `v2` key has reached any service.
`ai_processor/services.py`: one helper, `_answer_as_said`, feeds both the lookup and the store.
`docs/phase2/architecture/03a_data_model.md`: a dated note on what the match holds and its
stated limits. The module's docstring states the same limits.

### The delta's mutants (expected failing test named before any run)

Eleven added to `mutate.py`, 34 in all: A1 status left out; A2 notes left out; A3 the answer's
page put in; A4 `None` not the same as empty; A5 outer whitespace compared; A6 a status that is
not text refused; J1 parts joined with nothing between; J2 parts joined by a character a part
can hold; C1 a long paper's answers not saved; C2 a chunk's reply keeps its own markers; C3 the
single-pass reply keeps its own markers. No mutant is offered for
`test_the_first_long_paper_is_marked_in_parts` (a guard on the fixture) or for
`test_the_same_parts_give_the_same_key`.

**All 34 are run again,** not only the new ones: the test module they are judged by changed
after the first battery (rule 17's addendum), and every earlier mutant on
`ai_processor/grading_cache.py` acts on a function this delta rewrote.

### Expected for the delta's gate, written before it

- Step 0, the red run: the test module (53 tests) against `services.py` and `grading_cache.py`
  as at 88884176: non-zero exit, a "Ran" line, **6 failing** (the five named under
  `TheAnswerAsSentTest` and the separator test) and 47 passing.
- Step 1a clean. Step 1 OK. Step 2: 34 KILLED.

The first gate's logs (at e7d4b376) are kept, moved into `gate1_e7d4b376/`.

### The delta's gate at c239131b: GREEN

One grant from 0b, 2026-10-06 17:34:26 to 17:42:22, the whole of step 1 again. One run, all
serial, 6G scope, rules 12, 13, 16, 17 and 18. Not stopped, not repeated, nothing of the team's
beside it. This was the first run of the delta's 21 tests and 11 mutants.

| Step | Result | Log |
|---|---|---|
| 0 the red run: the test module against `services.py` and `grading_cache.py` as at 88884176 | exit 1 as expected: Ran 53 tests in 1.895s, FAILED (failures=10). **Exactly the 6 tests named beforehand** fail, compared by name by program; 47 pass | `red_run_code_as_at_red_commit.txt.gz` |
| 1a makemigrations --check | exit 0, No changes detected | `makemigrations_check.txt` |
| 1 three changed modules, thirteen near modules, 26 guards | exit 0: Ran 627 tests in 169.047s, OK. No skips. 627 as predicted | `modules_and_guards.txt.gz` |
| 2 the 34 mutants | 34 KILLED, 0 SURVIVED, 0 BROKEN | `mutation_log.txt`, `mutation_results.json`, `mutant_logs/` |

- Load average 9.71 9.12 6.82 at the start (the other project's run), 4.06 6.56 6.87 at the
  end. No test here asserts on the wall clock.
- Step 0's 10 failures are per sub-case: the separator test has several. The second key-parts
  test passed on the old code, as corrected before the run.
- All 34 inner runs exited 1 with "Ran 94 tests"; none of the 34 logs holds a load failure; each
  expected test is among the failing ones. The 23 earlier mutants are killed again on the
  changed code and the changed test module.
- Logs a commit hook would alter are gzipped, byte-exact, with each one's sha256 taken before
  gzipping in `gzipped_logs_sha256.txt`. `console.txt.gz` is the gate script's own output.
- After the run the source was as committed and the mutation database was dropped.
- Pattern check of the new files, by program, values never printed: no URL with a password
  part; the word "secret" appears in none of them; the NAME=value matches are the same four
  code names as in the first gate (`token_count`, `node_tokens`, `single_pass_mode`, a
  token-budget constant), each with code text as its "value". None is a credential.

The eleven new mutants:

| Mutant | What is broken | Result | Expected test, found among the failing |
|---|---|---|---|
| A1 | the answer's status is left out of the key | KILLED, Ran 94 tests | `test_the_same_text_with_a_different_status_is_a_fresh_grade` |
| A2 | the answer's transcription notes are left out of the key | KILLED, Ran 94 tests | `test_the_same_text_with_different_notes_is_a_fresh_grade` |
| A3 | the answer's page is put into the key | KILLED, Ran 94 tests | `test_a_different_page_and_confidence_do_not_break_the_match` |
| A4 | a field that is None is not the same as an empty one | KILLED, Ran 94 tests | `test_no_status_is_one_thing_however_it_is_written` |
| A5 | outer whitespace of status and notes is compared | KILLED, Ran 94 tests | `test_outer_whitespace_of_the_notes_is_not_compared` |
| A6 | a status that is not text is refused | KILLED, Ran 94 tests | `test_a_status_that_is_not_text_does_not_crash_the_grading` |
| J1 | the parts of the key are joined with nothing between them | KILLED, Ran 94 tests | `test_a_character_moved_across_a_boundary_changes_the_key` |
| J2 | the parts of the key are joined by a character a part can hold | KILLED, Ran 94 tests | `test_a_separator_inside_a_part_cannot_pass_for_a_boundary` |
| C1 | a long paper's answers are not saved | KILLED, Ran 94 tests | `test_an_identical_second_long_paper_makes_no_provider_call` |
| C2 | a chunk's reply keeps its own markers | KILLED, Ran 94 tests | `test_a_graded_by_in_a_chunks_reply_is_replaced` |
| C3 | the single-pass reply keeps its own markers | KILLED, Ran 94 tests | `test_a_graded_by_in_the_reply_is_replaced_by_the_model_that_answered` |

### The regression: one full run by 0b at 6ca94c09: GREEN

By the SM's ruling the regression for this slice is one full run by 0b (the Release Engineer) on
the frozen tip, with its own script; step 3 of the author's script was replaced by it and never
run. The author did not touch the worktree while it ran.

| What | Result |
|---|---|
| Whole-repository mypy | Passed |
| `makemigrations --check` | No changes detected |
| Full suite, `--parallel 4` | exit 0: Ran 6496 tests in 454.078s, OK (skipped=30). No FAIL or ERROR header |

- Run by 0b: `gate10_slice_b.sh`, the Phase 2 Gate 10 script pointed at this worktree and branch
  (sha256 prefix e35656ac9ef99d3f; a copy is committed here as `gate10_slice_b.sh.txt`). 12G
  cap, the shared machine lock, the sleep inhibitor, a silence watchdog that never fired, output
  straight to a file.
- Times, 2026-10-06: script start 18:01:00 (load 2.49); suite 18:01:40 (load 2.53) to 18:09:47
  (load 6.32), 487 s on the wall. No suspend, no blocked outbound call.
- Per app: ai_processor 908, assignments 663, audit 367, AutoGrader 613, billing 2139, classrooms
  434, dashboard 270, students 404, users 698; sum 6496. That is 54 more than slice A's full run
  (6442): 53 in ai_processor (the slice's test module) and 1 in students (slice A's migration
  test).
- The 30 skips are the same opt-in kinds as in slice A's run, by 0b's reading.
- For the record, as 0b asked: two commit-hook runs by other sessions ended at 17:58:40 and
  17:59:31, both before the script's start. One of them was mine (slice C's commit df648e6c,
  17:58).
  **Correction (2026-10-06, from 0b):** the last sentence is wrong; I had guessed. The two hook
  runs 0b named were the Security Engineer's (about 17:58:20 to 17:58:40, stopped at a lint
  check, nothing committed) and the Hardening Engineer's (ended 17:59:31, failed on a type
  error, nothing committed). My commit df648e6c at 17:58:23 was a third. So: three hook runs by
  three sessions, all ended by 17:59:31, before the script's start at 18:01:00.
- The log is committed whole and byte-exact as `full_run_0b_6ca94c09.log.xz`: 8,677,854 bytes
  unpacked, sha256 `fdcc7d6dc898d1736f505d06815f4dfb5d02f6385e66be455337a3d9b4e60993`, the figure
  0b gave and the one I computed from 0b's file. "Ran" is line 113930 and "OK" line 113932. 0b's
  summary is `full_run_0b_6ca94c09.summary.txt`.
- Pattern check of that log, by program, values never printed: no URL with a password part.
  Nine NAME=value matches on names containing "token", each with code text as its value. Two
  lines (81995 and 82000) are a test's "blocked unsafe fetch" warning for a made-up host whose
  address carries the word "secret", the same test stand-in the SM accepted in slice A's log.
- The run was at 6ca94c09. This commit adds only files in this evidence folder on top of it, so
  the run stands for the new tip. By the SM's ruling it also stands as Gate 10 for release 1
  (slices A and B) if the merged epic tree equals this slice's tree at the merge.

## The Checker's verdict and the delta it requires

Verdict at 728491a2: **VERIFIED-WITH-NOTES**, with three items required before the merge. All
three are tests; none is a fault in the code the slice ships.

| # | Item | Done by |
|---|---|---|
| 1 | A test that fails when the chunk call's or the summary call's teacher-instructions splice stops using the run's reading | `ai_processor/tests_grading_cache_key_v2_checker.py`, `OneReadingPerRunOnALongPaperTest`; mutants P1 (the chunk call's site) and P2 (the summary call's site) |
| 2 | A test that the answer side's parts cannot run together | same module, `TheAnswerSidesPartsCannotRunTogetherTest`; mutants P3 (status and notes joined) and P4 (text and status joined) |
| 3 | `test_the_last_part_of_the_context_and_the_answer_cannot_run_together` cannot fail | **Removed.** The question lies between those two parts of the key, so the test could not fail whatever the key did. Item 2's tests hold the answer side; the context's parts are held by `test_a_character_moved_across_a_boundary_changes_the_key` and the separator test |

- **Both new classes are the Checker's.** By the SM's ruling the Checker handed over two probe
  classes with their helpers (both passed at 6ca94c09 in the Checker's own run). They are adopted
  as written; the module docstring and the two class names are mine. I read only that hand-over
  file and the record I am asked to commit.
- **Order kept:** this commit is tests only (the new module, the removal). The mutants and the
  document lines follow in the next commit.

### Expected of the delta's tests, written before any run

All three new tests are expected to PASS on the code as it stands (the Checker ran them green at
6ca94c09); the proof that each can fail is its mutant, with the expected failing test named
before the run:
- P1, the chunk call's splice reads the live switch: `test_the_run_keeps_its_starting_reading`
  (the second chunk's prompt is built after the switch flips).
- P2, the summary call's splice reads the live switch: the same test (the summary call's prompt).
  If P2 survives, the test does not look at the summary call and an assertion is owed; that
  would be reported, not explained away.
- P3, status and notes joined into one part: `test_status_and_notes_cannot_run_together`.
- P4, text and status joined into one part: `test_text_and_status_cannot_run_together`.

Test count of the slice's own module after the removal: 52; the new module: 3.

### The rest of the delta (the commit after the tests-only one)

- Four mutants added to `mutate.py` (P1 to P4), 38 in all; the Checker's module joins the test
  modules every mutant is judged by.
- **Document lines, no code line:** in the docstring of `build_cache_key` and in 03a, a new
  stated limit (a question's image is matched by its address, not its content) and the "nothing
  said" choice among the stated limits, accepted by the SM on 2026-10-06. `ai_processor/grading_cache.py`
  changes by docstring lines only; whether that needs anything beyond this delta's run is 0b's
  to say.
- The correction about the three commit-hook runs, above.
- The Checker's record at 728491a2 is committed byte-identical as
  `VERIFICATION_be_i_04_slice_b.md` (sha256
  `b49ba92c74eee1ce5ff1e05e33bab9b6fd9118017a53833bc7059891e59f8a4b`).
- **All 38 mutants are run again** in the delta's run, with the modules and the guards; no red
  run, since the delta's tests pass on the code as it stands. Expected: OK (627 before; one test
  removed and three added, so 629 if nothing else moved); 38 KILLED.

### Delta 2's run at 525fdf88: the modules green, TWO MUTANTS SURVIVED (disclosed)

One grant from 0b, 2026-10-06 18:31:14 to 18:40:10, mode `1d`. One run, serial, 6G scope, rules
12, 13, 16, 17 and 18. Not stopped, not repeated.

| Part | Result | Log |
|---|---|---|
| 1: four changed modules, thirteen near modules, 26 guards | exit 0: Ran 629 tests in 199.954s, OK. No skips. 629 as predicted | `modules_and_guards_525fdf88.txt.gz` |
| 2: all 38 mutants | **36 KILLED, 2 SURVIVED, 0 BROKEN** | `mutation_log_525fdf88.txt`, `mutation_results_525fdf88.json`, `mutant_logs_525fdf88/` |

- Load average 8.41 12.50 12.19 at the start, 11.34 10.85 11.19 at the end.
- The 34 earlier mutants were all killed again. P3 and P4 were killed by their named tests: the
  Checker's item 2 is met.
- **P1 and P2 SURVIVED** (exit 0, "Ran 96 tests", nothing failing): the chunk call's and the
  summary call's splice of the teacher's instructions can each read the live switch without any
  test failing. **The Checker's item 1 was NOT met by this delta.** Nothing was re-run.
- **Why.** The adopted test asserted that the teacher's text is in
  `json.dumps(kwargs, default=str)` for every provider call. Those keyword arguments include
  `assignment=<the assignment object>`, and the test's stand-in assignment is a plain namespace
  whose text form prints its `custom_ai_prompt`. So the text was in every dump whether or not it
  was spliced into the prompt: the assertion could not fail. It passed at 6ca94c09 in the
  Checker's run for the same reason.
- **My part.** I adopted the class without checking by reading that its assertion could fail
  under the mutants I had written for it, and spent a slot finding out.
- Logs a commit hook would alter are gzipped byte-exact; their hashes are in
  `gzipped_logs_sha256_525fdf88.txt`. `console_525fdf88.txt` is the gate script's own output.

### The corrected assertion, and what can fail (written before the next run)

By the SM's word I corrected the one assertion: it now looks at what was sent as the system
prompt (`kwargs["system_prompt"]`) of every provider call of the run, not at a dump of all the
keyword arguments. This changes the Checker's class; the module's docstring says so and why.
Nothing else in the module changed.

Checked by reading, as the SM asked, that each adopted or new assertion of this delta CAN fail,
with the mutant that fails it:

| Assertion | The mutant that fails it | How I know |
|---|---|---|
| `test_status_and_notes_cannot_run_together` (three inequalities) | P3 | killed in the run at 525fdf88 |
| `test_text_and_status_cannot_run_together` (two inequalities) | P4 | killed in the run at 525fdf88 |
| `test_the_run_keeps_its_starting_reading`: three provider calls on the first paper | none offered: a guard on the fixture (two chunks and a summary) | by reading |
| the same test: the teacher's text in the system prompt of every call | P1 for the second chunk's call, P2 for the summary call's | by reading only, no run yet: with P1 the second chunk's prompt is built after the switch flips and reads the live switch, so the text is absent from what is sent; with P2 the same for the summary call. The first chunk's prompt is built before the flip and carries the text either way |
| the same test: an identical second paper makes no provider call | R1 (the store takes a fresh reading) and S1 (the splice reads the live switch) | both killed in the run at 525fdf88 with this test among the failing ones |

### Expected for the next run, written before it

Mode `1d` again at the new tip: part 1 OK, 629 tests; 38 KILLED, with P1 and P2 killed by
`test_the_run_keeps_its_starting_reading`. If P2 still survives, the summary call does not carry
the teacher's instructions in its system prompt at all and that is reported as a finding.

### Delta 2's second run

None yet at this commit.
