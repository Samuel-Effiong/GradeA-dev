from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0030_usergooglecredentials_access_token_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="settings",
            name="notify_new_assignment_posted",
            field=models.BooleanField(default=False),
        ),
    ]
