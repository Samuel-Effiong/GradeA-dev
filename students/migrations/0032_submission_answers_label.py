"""AI-call record, slice 2: the six columns that say how a paper was read.

Each is NOT NULL with a database default of "unlabelled" (rule 11, H-56).
On PostgreSQL 11 and later, adding a column with a constant default does
not rewrite the table: every existing row reads the default at once, and
older code that inserts without naming the columns still inserts. Nothing
is copied and nothing is indexed. Pure AddField; the check constraint on the
re-check's pair is the next migration, on its own.

To undo: go back to the previous version of the code and leave the columns
in place, unused. Reversing this migration drops them and whatever labels
they hold.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0031_submission_grading_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentsubmission",
            name="answers_model",
            field=models.CharField(
                db_default="unlabelled",
                default="unlabelled",
                help_text="The AI model that supplied most of the saved answers, as the provider named it; 'unknown' when the provider did not say.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="studentsubmission",
            name="answers_prompt_version",
            field=models.CharField(
                db_default="unlabelled",
                default="unlabelled",
                help_text="Version of the instructions the reading was given.",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="studentsubmission",
            name="answers_config_version",
            field=models.CharField(
                db_default="unlabelled",
                default="unlabelled",
                help_text="Version of the settings that shape a reading, taken once at the start of the extraction (ai_processor/reading_config.py).",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="studentsubmission",
            name="answers_release",
            field=models.CharField(
                db_default="unlabelled",
                default="unlabelled",
                help_text="The release that did the reading, or 'none'.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="studentsubmission",
            name="answers_recheck_model",
            field=models.CharField(
                db_default="unlabelled",
                default="unlabelled",
                help_text="The model that answered the blank-answer re-check, as the provider named it; 'unknown'; or 'not_run' when no call was made.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="studentsubmission",
            name="answers_recheck_result",
            field=models.CharField(
                db_default="unlabelled",
                default="unlabelled",
                help_text="What became of the blank-answer re-check: 'not_run', 'failed', 'confirmed_blank' or 'found_writing'.",
                max_length=32,
            ),
        ),
    ]
