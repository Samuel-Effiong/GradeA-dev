"""Epic B1a step 1: the index on BackgroundProcessingTask.job_id.

Class: ADDITIVE (docs/MIGRATIONS.md §2: a new index, concurrently). CREATE
INDEX CONCURRENTLY cannot run inside a transaction, so atomic = False
(precedent billing/0051, 0061); it does not block writes to the table while
it builds.

To undo: reverse this migration (DROP INDEX CONCURRENTLY).
"""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("students", "0038_validate_backgroundprocessingtask_job_fk"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="backgroundprocessingtask",
            index=models.Index(fields=["job"], name="students_bpt_job_id_ix"),
        ),
    ]
