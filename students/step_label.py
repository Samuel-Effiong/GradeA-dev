"""The words the label of an AI step can hold (AI-call record).

Beside `students/grading_label.py`, for the steps that are not the grading:
reading an assignment, generating one, reading a student's answers, the
blank-answer re-check, and the feedback wording. This module holds only the
words. It imports nothing from the project, so the models, their
migrations, the services and the audit code can all read it. The
AI-processor keeps its own copy of the words (`ai_processor/step_run.py`; it
must not import the students app) and a test compares the two.

A model column holds the provider's exact text, or one of the fixed words
below. A provider name that equals a fixed word is recorded as "unknown"
(`ai_processor.step_run.StepRun`), so a fixed word in a model column is
always ours.
"""

#: No label recorded: made before labels existed. The database default of
#: every label column, and a word no step ever writes.
UNLABELLED = "unlabelled"
#: The provider did not say which model answered. Never a guess.
MODEL_UNKNOWN = "unknown"
#: The step made no provider call (re-check: switched off, nothing blank,
#: over a cap, no page images, or refused before the call).
NOT_RUN = "not_run"

#: A call was made and gave nothing usable (re-check only).
FAILED = "failed"
#: The re-check's reply was read and no finding was applied.
CONFIRMED_BLANK = "confirmed_blank"
#: The re-check's reply was read and at least one finding was applied.
FOUND_WRITING = "found_writing"

#: `Assignment.content_source`.
SOURCE_EXTRACTED = "extracted"
SOURCE_GENERATED = "generated"

#: The words that may not stand for a provider's name in a model column.
FIXED_MODEL_WORDS = frozenset({UNLABELLED, MODEL_UNKNOWN, NOT_RUN})

#: `StudentSubmission.answers_recheck_result` (besides UNLABELLED).
RECHECK_RESULTS = (NOT_RUN, FAILED, CONFIRMED_BLANK, FOUND_WRITING)
#: `Assignment.content_source` (besides UNLABELLED).
SOURCES = (SOURCE_EXTRACTED, SOURCE_GENERATED)
