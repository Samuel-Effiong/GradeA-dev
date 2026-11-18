# expand-contract-step: expand
"""Epic E2: grade categories (plan docs/evidence/epic-e2/STEP0_PLAN.md §2).

* `Course.missing_counts_as_zero` (Q2), with a database default.
* `CourseCategory` repaired (03a §2.8, H-6): course, weight, position,
  created at, each with a database default or nullable, so code older than
  this migration can still insert a row (H-56).
* The rows that existed before E2 are deleted (owner, Q7). The table was
  never routed, so they belonged to no course: every one of them has the
  new `course` column NULL. No production count was available; the owner
  ruled to delete them all.
* Then the two constraints on `classrooms_coursecategory` (the marker on
  line 1 acknowledges them): UNIQUE(course, name) and weight 0-100. The
  table is empty at that moment, so neither scans or can refuse a row, and
  rows older code may still write (a name only) satisfy both: `course` NULL
  is distinct under UNIQUE, and the weight's database default is 0.
* `CategoryTemplate` (03a §2.7), a new table with its constraints.

`course` stays nullable in the database (a NOT NULL foreign key has no
database default to give older code); every E2 code path requires it.

To undo: reverse the migration (the deleted rows are not restored; they were
linked to nothing), or roll the code back and leave the columns in place.
"""

import uuid
from decimal import Decimal

import django.db.models.deletion
import django.db.models.functions.datetime
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

DELETE_ORPHANS = 'DELETE FROM "classrooms_coursecategory" WHERE "course_id" IS NULL'


class Migration(migrations.Migration):

    dependencies = [
        ("classrooms", "0018_alter_session_is_active"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="course",
            name="missing_counts_as_zero",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AlterModelOptions(
            name="coursecategory",
            options={"ordering": ["position", "name"]},
        ),
        migrations.AddField(
            model_name="coursecategory",
            name="course",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="grade_categories",
                to="classrooms.course",
            ),
        ),
        migrations.AddField(
            model_name="coursecategory",
            name="created_at",
            field=models.DateTimeField(
                db_default=django.db.models.functions.datetime.Now(),
                default=django.utils.timezone.now,
            ),
        ),
        migrations.AddField(
            model_name="coursecategory",
            name="position",
            field=models.IntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="coursecategory",
            name="weight_percent",
            field=models.DecimalField(
                db_default=Decimal("0.00"),
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=5,
            ),
        ),
        migrations.RunSQL(sql=DELETE_ORPHANS, reverse_sql=migrations.RunSQL.noop),
        migrations.AddConstraint(
            model_name="coursecategory",
            constraint=models.UniqueConstraint(
                fields=("course", "name"), name="unique_category_name_per_course"
            ),
        ),
        migrations.AddConstraint(
            model_name="coursecategory",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("weight_percent__gte", 0), ("weight_percent__lte", 100)
                ),
                name="category_weight_percent_0_100",
            ),
        ),
        migrations.CreateModel(
            name="CategoryTemplate",
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
                ("name", models.CharField(max_length=100)),
                (
                    "owner_type",
                    models.CharField(
                        choices=[("TEACHER", "Teacher"), ("SCHOOL", "School")],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("items", models.JSONField()),
                ("created_by_id", models.UUIDField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "school",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="category_templates",
                        to="classrooms.school",
                    ),
                ),
                (
                    "teacher",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="category_templates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["name", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("owner_type", "TEACHER")),
                        fields=("teacher", "name"),
                        name="unique_teacher_category_template_name",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("owner_type", "SCHOOL")),
                        fields=("school", "name"),
                        name="unique_school_category_template_name",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("owner_type", "TEACHER"),
                                ("school__isnull", True),
                                ("teacher__isnull", False),
                            ),
                            models.Q(
                                ("owner_type", "SCHOOL"),
                                ("school__isnull", False),
                                ("teacher__isnull", True),
                            ),
                            _connector="OR",
                        ),
                        name="category_template_one_owner",
                    ),
                ],
            },
        ),
    ]
