from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    # B1b-4 (08b §1.3 item 2): CREATE INDEX CONCURRENTLY cannot run inside a
    # transaction, and a plain AddIndex would block writes to the call
    # record table for the whole build (the 0051/0061 house pattern). One
    # index per migration.
    atomic = False

    dependencies = [
        ("ai_processor", "0007_aicallrecord_assignment_created_index"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="aicallrecord",
            index=models.Index(
                fields=["billed_user_id", "created_at"],
                name="aicall_billed_user_created",
            ),
        ),
    ]
