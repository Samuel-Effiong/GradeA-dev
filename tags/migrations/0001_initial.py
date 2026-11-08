# Epic C1 (plan 11 §3.1): two new tables, additive only.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("assignments", "0041_topic_set_null"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Tag",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "tag_type",
                    models.CharField(
                        choices=[("SUBJECT", "Subject"), ("UNIT", "Unit")],
                        max_length=16,
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("normalized_name", models.CharField(max_length=100)),
                (
                    "owner_type",
                    models.CharField(
                        choices=[("TEACHER", "Teacher")],
                        default="TEACHER",
                        max_length=16,
                    ),
                ),
                ("created_by_id", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "teacher",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tags",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                # Inside CreateModel, not AddConstraint: the table is new and
                # empty, so there is no lock on existing rows (H-217 is about
                # constraints added to a table that already has rows).
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("owner_type", "TEACHER")),
                        fields=("teacher", "tag_type", "normalized_name"),
                        name="tags_tag_teacher_type_name_uniq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("owner_type", "TEACHER"), ("teacher__isnull", False)
                        ),
                        name="tags_tag_teacher_owner_ck",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("tag_type__in", ["SUBJECT", "UNIT"])),
                        name="tags_tag_type_ck",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("normalized_name", ""), _negated=True),
                        name="tags_tag_normalized_name_not_empty_ck",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="AssignmentTag",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("USER", "User"),
                            ("TOPIC_BACKFILL", "Topic backfill"),
                        ],
                        default="USER",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tag_links",
                        to="assignments.assignment",
                    ),
                ),
                (
                    "tag",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assignment_links",
                        to="tags.tag",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("assignment", "tag"), name="tags_assignmenttag_uniq"
                    ),
                ],
            },
        ),
    ]
