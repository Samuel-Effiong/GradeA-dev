from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("audit", "0002_alter_auditevent_actor_role"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="auditevent",
            index=models.Index(
                fields=["target_id", "-occurred_at"],
                name="audit_target_time_ix",
                condition=models.Q(target_id__isnull=False),
            ),
        ),
    ]
