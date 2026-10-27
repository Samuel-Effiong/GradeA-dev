# H-264 notes

Release Builder (a0), on `task/h264-numbered-offtopic-answers` from beta `b199eed6`. This first commit is the
tests and these notes; no production code yet.

## The case (Senior Manager, 2026-10-27; the user's beta submission 452f823f)

- **The assignment:** a 75-question true-or-false quiz on Dr. Martin Luther King Jr.
- **The upload:** a one-page 4th grade biomes lab report with six numbered answers ("Question 1 (5 marks) ..." to
  "Question 6 ...").
- **What the first reading returned:**
  - Question 1 was ANSWERED and OFF_TOPIC, and its `answer_html` held all six answers.
  - Questions 2-75 were NOT_FOUND_IN_DOCUMENT with flag NONE and confidence 100.
- **Why nothing recovered the answers:**
  - The re-check skipped the paper, because 74 blanks is above `ANSWER_BLANK_VERIFICATION_MAX_QUESTIONS` (12).
  - The second reading had no targets. Questions 2-75 had no re-check writing and no flag, and question 1 was
    ANSWERED.
- **The rule:** H-253's ruling (2026-10-22) says the student's writing always goes under its question.

## The mechanism I propose (A, B, C), with three changes to the brief marked

**A. A pile is split by the second reading** (`_retranscribe_found_answers`, deterministic, no extra call).

An entry is a *pile* when all of these hold:

- it is ANSWERED;
- **(change 1) it is flagged OFF_TOPIC or TYPE_MISMATCH;**
- its text holds the student's own numbered sections.

A section starts at the start of a paragraph, line or list item (`<p>`, `<br>`, `<li>`, `<div>`, a newline), in
one of these forms:

- `Question N`, `Q N`, `QN` or `Q.N`, followed by `:` `.` `)` `-`, a space or the end of the line, in any letter
  case;
- or a bare `N.` or `N)` not followed by a digit, so "3.5" is not a section.

The other conditions on the sections:

- N must be one of the assignment's question numbers, taken from the answers' own keys.
- At least two sections must be kept, in increasing order. Each kept number is greater than the one before.
- When two or more `Question N` / `Q N` sections are found, only those are kept. A bare `1.` inside a section is
  then a list item, not a section.

The pile **names** each kept number N other than its own question. The questions named that are NOT_FOUND with
flag NONE go to the second reading in the same call, with the pile's own question. A named question that is
already a target by H-253's route (flagged, or the re-check found writing) stays one as before. When no named
question qualifies, nothing is added.

The note gets one evidence line per question. The template (`RETRANSCRIPTION_INSTRUCTION`) is unchanged, so its
version is unchanged:

- for the pile's own question: `- Question 1: the first reading put the student's answers numbered 1, 2, 3, 4, 5,
  6 all under this question. Transcribe here only the answer to question 1; the others go under their own
  numbers.`
- for a named question: `- Question 2: the first reading put this answer under question 1. The student's answer
  numbered 2 begins: "<the start of that section, at most 80 characters>"`

The reply goes through H-253's recovery path. A named question it returns with writing replaces the NOT_FOUND,
keeps the reply's flag (else the first reading's, else READING_PROBLEM), is marked `retranscribed`, and gets
`RECOVERED_ANSWER_NOTE`.

**(Change 2) The pile is replaced only when the reply returns its own question AND every named question it
sent.** The brief says to replace it when question 1 comes back. But if question 1 came back and question 4 did
not, question 4's text would then be in no answer at all, and that breaks "never drop text". When the pile is
replaced, it is marked `retranscribed` and gets a new note (`SPLIT_ANSWER_NOTE`): "The first reading put several
of the student's numbered answers under this question; a second reading kept only this one's: check it against
the upload."

When the pile is kept, its text still holds the sections that were recovered elsewhere. That is a duplicate, not
a loss.

A named question the reply does not return stays NOT_FOUND. It has no re-check quote, so H-253's last resort does
not apply. I did not cut the pile's text into answers in code, because a regex split of model HTML is not a
transcription.

- **Why change 1:** without the flag condition, a correct reading is put at risk. For example: a numbered list that
  IS question 1's answer ("1. Rosa Parks 2. Selma"), with the later pages missing. The second reading would then be
  told "Do not return NOT_FOUND_IN_DOCUMENT or BLANK for these questions: the writing is there" about questions 2
  and 3, which invites it to move list items. The one-piece rule that makes a pile applies only to off-topic work,
  so the condition costs nothing on the beta case.
  `test_an_answer_on_topic_is_not_a_pile` pins it. If you rule it out, that test becomes a red and the condition
  goes.
- **Known gap:** a pile transcribed as `<ol><li>a</li><li>b</li></ol>`, with the student's numbers only in the
  list markup and not in the text, is not seen.

**B. The re-check cap** (`_verify_blank_answers`). Above the cap, the `cap` lowest-numbered blanks (by
`safe_sort_key`) are re-checked, not none. It logs "N blank(s) exceeds the cap of C - re-checking the
lowest-numbered C (questions [...]); M not checked." A finding for a question that was not asked about is not
applied; today every blank is asked about, so that never comes up. A cap of 0 still checks nothing
(`tests_reading_pipeline` "over the question cap" stays `not_run`). The page cap is unchanged. The comment on the
setting in `AutoGrader/settings.py` is rewritten. The setting's name and default are unchanged, so the reading
version (`rcfg:`) does not move; the release is the backstop, as for any code change.

- On the beta paper this re-checks questions 2-13. A model that sees "Question 2:" ... "Question 6:" on the page
  may find writing for 2-6, which makes them targets by H-253's route as well. A does not depend on that.

**C. The prompt.** These are the changes:

- **(Change 3) a new file:** I propose `ANSWERS_EXTRACTION_PROMPT_HTML_5.txt`, with
  `ANSWERS_EXTRACTION_PROMPT_HTML_4.txt` moved to `ai_processor/prompts_archive/`. That follows
  `docs/CODE_REVIEW_STANDARDS.md` §8: "versioned by suffix (`_2`, `_3`, …) rather than edited in place". It also
  means updating the live file in `services.py`, the table in `prompts_archive/README.md`, and the living docs
  that name the file (`docs/backend/ai-processor.md`, `docs/backend/grade-automator-backend-reference.html`,
  `ai_processor/extraction_schemas.py`'s comment). Historical evidence is left as written.
  - The other way the repo has done it: `_4` itself was edited in place three times (604f71a9, 452a35cd, and
    ecfacccc for H-242). Its recorded version `ANSWERS_EXTRACTION_PROMPT_HTML_4:<sha8>` changed each time on its
    own.
  - **Which one you mean is your call.** `test_the_prompt_is_version_5_and_version_4_is_archived` is written for
    `_5` and goes if you choose in place.
- **A rule sentence** after the one-piece rule: an upload with its own numbered answers is NOT one piece of work.
  Each numbered answer goes under the question with that number, flagged OFF_TOPIC, even when the paper is for
  another assignment and has far fewer answers than the assignment has questions.
- **Example 4: A Numbered Paper for Another Assignment Under a Longer Quiz.** It has a 75-question quiz and a
  numbered six-answer paper for another subject. The output shows answers 1-6 each under its own number, flagged
  OFF_TOPIC with a detail; question 7 as NOT_FOUND_IN_DOCUMENT; "Questions 7 to 75: one entry each, like question
  7."; and `"submission_matches_assignment": false` with a `mismatch_detail`.
  - The example's content is invented. It does not use the student's name or their answers: the prompt goes to
    every reading.

**The benchmark.** The answer benchmark (`ai_processor/benchmark/answers`) does not take a recorded model reply.
Its `FakeProvider` answers from each scenario's ground truth, and `ProviderBehaviour.raw_on` exists only to inject
a raw string per call for the failure suites. H-253's beta paper was recorded as a step-level test
(`TheBetaPaperTest` in `tests_h253_answers_never_empty`), not in the benchmark, and the H-242 evidence folder
holds live-run records, not replays. So this row follows H-253: the beta reply is a step-level fixture, and no
benchmark scenario is added. The grading and extraction recordings hold no answer-reading call (searched for the
prompt's title: 0 in both), so the prompt change cannot break a replay.

## Open question (not built)

If the beta reply said `submission_matches_assignment: true`, `upload_mismatch` stays null after this row. The
reader's verdict is trusted and only ever suppressed (`submission_flags`: a "not for this assignment" verdict is
dropped when an answer is on topic). Raising it in code when every answer found is OFF_TOPIC would change H-242's
rule. The tests check the flag with the verdict `false`, which is what the first reading's notes ("no MLK content
present") suggest it said.

## Expected reds against b199eed6 (written before any code)

`ai_processor.tests_h264_numbered_offtopic_answers`: 22 red (5 ERROR, 17 FAIL, counting subTests), 11 green
controls. `ai_processor.tests_blank_verification`: 1 red (the cap test, rewritten for B). Run at b199eed6:
`Ran 60 tests, FAILED (failures=23, errors=5)` for the two modules.

```
TheBetaPaperTest
  test_each_answer_goes_under_its_own_number                  FAIL  "0 != 1" (no second reading)
  test_the_flags_are_six_off_topic_and_the_upload_mismatch    FAIL  lists differ: ANSWER_NOT_FOUND where OFF_TOPIC
  test_only_the_pile_and_the_numbers_it_names_are_asked_about ERROR 'NoneType' object has no attribute 'kwargs'
  test_the_note_says_where_each_answer_was_put                ERROR 'NoneType' object has no attribute 'kwargs'
ThePileIsKeptUnlessEverySectionCameBackTest
  test_question_1_missing_from_the_reply                      FAIL  "'' != '<p>I studied the desert. ...'"
  test_some_sections_missing_from_the_reply                   FAIL  "'' != '<p>I studied the desert. ...'"
  test_a_failed_or_empty_second_reading_changes_nothing       FAIL  x2 subTests, "0 != 1"
WhatCountsAsAPileTest
  test_each_way_of_numbering_at_the_start_of_a_paragraph_or_line  FAIL x9 subTests, "0 != 1"
  test_the_numbers_need_not_start_at_1                        FAIL  "0 != 1"
  test_a_pile_in_the_wrong_form_counts                        FAIL  "0 != 1"
  test_a_pile_under_a_later_question                          ERROR 'NoneType' object has no attribute 'kwargs'
TheRecheckCapTest
  test_the_beta_paper_has_its_first_12_blanks_re_checked      FAIL  "0 != 1"
  test_the_rest_are_logged_as_not_checked                     FAIL  the log says "skipping the re-read"
  test_the_lowest_numbered_whatever_the_order                 ERROR 'NoneType' object has no attribute 'kwargs'
  test_a_finding_for_a_question_not_asked_about_is_not_applied ERROR KeyError: 'blank_verification'
TheWholeReadingTest
  test_the_beta_reply_ends_with_six_answers_and_the_upload_flag  FAIL "0 != 2"
ThePromptTest
  test_the_prompt_has_the_numbered_paper_example              FAIL  'Example 4: ...' not found
  test_the_prompt_is_version_5_and_version_4_is_archived      FAIL  "False is not true" (only if you rule _5)
tests_blank_verification.BlankVerificationTest
  test_too_many_blanks_re_reads_the_lowest_numbered_cap       FAIL  "0 != 1"
GREEN at b199eed6 and after (controls):
  test_a_reply_question_not_asked_about_is_ignored, test_writing_with_no_numbered_sections_changes_nothing,
  test_one_numbered_section_is_not_a_pile, test_numbers_out_of_order_are_not_a_pile,
  test_numbers_outside_the_assignment_are_not_counted, test_a_named_question_with_its_own_answer_is_not_touched,
  test_a_named_question_with_a_flag_is_left_to_h253, test_an_answer_on_topic_is_not_a_pile (change 1),
  test_a_cap_of_0_checks_nothing, test_at_or_under_the_cap_every_blank_is_re_checked,
  test_the_one_piece_rule_and_the_flag_rule_are_kept
```
