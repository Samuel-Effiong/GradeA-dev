"""BE-I-04, slice A: the six columns of a grade's label.

Each is NOT NULL with a database default of "unlabelled" (rule 11, H-56).
On PostgreSQL 11 and later, adding a column with a constant default does
not rewrite the table: every existing row reads the default at once, and
older code that inserts without naming the columns still inserts. Nothing
is copied and nothing is indexed.

To undo: go back to the previous version of the code and leave the columns
in place, unused. Reversing this migration drops them and whatever labels
they hold.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0030_batch_session_credits_exhausted_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentsubmission",
            name="grading_prompt_version",
            field=models.CharField(
                db_default="unlabelled",
                default="unlabelled",
                help_text="Version of the grading instructions the AI was given: the prompt file's stem, a colon, and a hash of its text. First form of the grading record (BE-I-04). A later stage improves on it: a table of grading runs, built beside re-grading or feedback editing and filled from these fields.",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="studentsubmission",
            name="grading_config_version",
            field=models.CharField(
                db_default="unlabelled",
                default="unlabelled",
                help_text="Version of the grading settings in force, read once at the start of the run (ai_processor/grading_config.py). First form of the grading record (BE-I-04). A later stage improves on it: a table of grading runs, built beside re-grading or feedback editing and filled from these fields.",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="studentsubmission",
            name="grading_strictness",
            field=models.CharField(
                db_default="unlabelled",
                default="unlabelled",
                help_text="How strictly the work was marked. 'not_yet_set' until the strictness scale exists. First form of the grading record (BE-I-04). A later stage improves on it: a table of grading runs, built beside re-grading or feedback editing and filled from these fields.",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="studentsubmission",
            name="grading_model",
            field=models.CharField(
                db_default="unlabelled",
                default="unlabelled",
                help_text="The AI model that marked the most answers, as the provider named it; 'deterministic' when no AI was involved; 'unknown' when the provider did not say. First form of the grading record (BE-I-04). A later stage improves on it: a table of grading runs, built beside re-grading or feedback editing and filled from these fields.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="studentsubmission",
            name="grading_fallback_used",
            field=models.CharField(
                db_default="unlabelled",
                default="unlabelled",
                help_text="Whether a backup model produced any part of the grade: 'yes', 'no', 'unknown', or 'not_applicable' when no AI call was made and no saved answer was reused. First form of the grading record (BE-I-04). A later stage improves on it: a table of grading runs, built beside re-grading or feedback editing and filled from these fields.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="studentsubmission",
            name="grading_release",
            field=models.CharField(
                db_default="unlabelled",
                default="unlabelled",
                help_text="The release of the system that did the grading, or 'none' when the host does not say. Covers what the settings version cannot: text and rules written directly in the code. First form of the grading record (BE-I-04). A later stage improves on it: a table of grading runs, built beside re-grading or feedback editing and filled from these fields.",
                max_length=64,
            ),
        ),
    ]
