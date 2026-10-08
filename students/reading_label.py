"""The label of how a paper was read (AI-call record, slice 2).

Six columns on StudentSubmission say which prompt, settings and release read
the answers, which model supplied them, and what became of the blank-answer
re-check. This module is the ONLY writer of those columns: both routes that
save a reading (students.services.upload_answers_engine and
update_submission_from_raw_text) call `apply_reading_label`, which sets the
six from the reading's run on the instance BEFORE the save, and the save's
update_fields list carries READING_LABEL_FIELDS, so the label and the answers
are one UPDATE (or one INSERT). A static test lists every writer of `answers`
and of these names.

The words are in students/step_label.py. A re-read replaces the label; a
teacher's edit of the typed text is a re-read.
"""

READING_LABEL_FIELDS = (
    "answers_model",
    "answers_prompt_version",
    "answers_config_version",
    "answers_release",
    "answers_recheck_model",
    "answers_recheck_result",
)


def apply_reading_label(submission, run, answers):
    """Set the six columns on `submission` from the reading's `run`, for the
    answers that are about to be saved. Returns the names set.

    A run whose extraction never took the settings (a test that replaces the
    whole extraction) has nothing to say: the columns are left as they are."""
    if run.config is None:
        return ()
    fields = run.label_fields(answers)
    for name in READING_LABEL_FIELDS:
        setattr(submission, name, fields[name])
    return READING_LABEL_FIELDS
