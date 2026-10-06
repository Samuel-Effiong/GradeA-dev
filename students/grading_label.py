"""The words a grade's label can hold (BE-I-04).

Every graded submission records what produced its grade: the grading
prompt's version, the grading settings' version, the strictness level, the
model that answered, whether a backup model answered, and the release that
was running. They are six columns on StudentSubmission, written by the same
UPDATE as the score, so a grade and its label cannot disagree and a grade
cannot be missing its label.

FIRST FORM OF THE GRADING RECORD. A later stage improves on it: a table of
grading runs, built beside re-grading or feedback editing and filled from
these fields. Until that table exists a re-grade overwrites the label with
the newer run's, as it overwrites the score.

This module holds only the words. It imports nothing from the project, so
the model, its migration, the grading code and the audit code can all read
it. What writes the label is students.services._populate_and_save_grade.
"""

#: The founder's condition (2026-10-06), carried in the help text of each
#: label column and tested there.
FIRST_FORM_NOTE = (
    "First form of the grading record (BE-I-04). A later stage improves on "
    "it: a table of grading runs, built beside re-grading or feedback "
    "editing and filled from these fields."
)

#: No label recorded: graded before labels existed, or not graded. The
#: database default of all six columns, and a word no grading ever writes.
UNLABELLED = "unlabelled"

#: The strictness scale does not exist yet (Epic I-2). A neutral word, so
#: today's marking is not claimed to be any level of that scale.
STRICTNESS_NOT_YET_SET = "not_yet_set"

#: grading_model when no AI produced any part of the grade.
MODEL_DETERMINISTIC = "deterministic"
#: grading_model, and an item of the audit lists, when the provider did not
#: say which model answered. Never a guess.
MODEL_UNKNOWN = "unknown"

FALLBACK_YES = "yes"
FALLBACK_NO = "no"
FALLBACK_UNKNOWN = "unknown"
#: The grading made no AI call and reused no saved answer.
FALLBACK_NOT_APPLICABLE = "not_applicable"

#: grading_release when the host does not tell the application which
#: release is running.
RELEASE_NONE = "none"

#: The six columns, in the order the model declares them.
LABEL_FIELDS = (
    "grading_prompt_version",
    "grading_config_version",
    "grading_strictness",
    "grading_model",
    "grading_fallback_used",
    "grading_release",
)
