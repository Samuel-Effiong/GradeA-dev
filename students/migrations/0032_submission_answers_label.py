"""AI-call record, slice 2: the six columns that say how a paper was read.

Each is NOT NULL with a database default of "unlabelled" (rule 11, H-56).
On PostgreSQL 11 and later, adding a column with a constant default does
not rewrite the table: every existing row reads the default at once, and
older code that inserts without naming the columns still inserts. Nothing
is copied and nothing is indexed. The check constraint on the re-check's
pair is the next migration, on its own.

The first operation is SET LOCAL lock_timeout = '5s' (it lasts for this
migration's transaction only), as in 0033: each ADD COLUMN takes an ACCESS
EXCLUSIVE lock on students_studentsubmission and waits behind any open
transaction on the table while every new query on the table queues behind
it; with the timeout it gives up after five seconds with an error instead
of stalling the table. The deploy step then fails cleanly, the old version
keeps running, and the deploy is simply run again (SM ruling 2026-10-14).

To undo: go back to the previous version of the code and leave the columns
in place, unused. Reversing this migration drops them and whatever labels
they hold.
"""

from django.db import migrations, models

SET_LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0031_submission_grading_label"),
    ]

    operations = [
        migrations.RunSQL(SET_LOCK_TIMEOUT, reverse_sql=migrations.RunSQL.noop),
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
