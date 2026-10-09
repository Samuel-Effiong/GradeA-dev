"""Populate the rigor_* columns for assignments that predate them.

The scoring rules live in assignments/rigor.py as pure functions over the
questions payload, with no model or settings imports, so calling them from a
migration is safe -- there is no historical-model mismatch to worry about.

Batched with an explicit iterator + bulk_update because a school's assignment
table can be large and every row carries a full questions JSON blob; loading
them all at once would spike memory on the deploy host.
"""

from django.db import migrations

BATCH_SIZE = 500


def backfill_rigor(apps, schema_editor):
    from assignments.rigor import score_assignment

    Assignment = apps.get_model("assignments", "Assignment")

    batch = []
    queryset = (
        Assignment.objects.all().only("id", "questions").iterator(chunk_size=BATCH_SIZE)
    )

    for assignment in queryset:
        demand, standards, coverage = score_assignment(assignment.questions)
        assignment.rigor_demand = demand
        assignment.rigor_standards = standards
        assignment.rigor_blooms_coverage = coverage
        batch.append(assignment)

        if len(batch) >= BATCH_SIZE:
            Assignment.objects.bulk_update(
                batch,
                ["rigor_demand", "rigor_standards", "rigor_blooms_coverage"],
            )
            batch = []

    if batch:
        Assignment.objects.bulk_update(
            batch,
            ["rigor_demand", "rigor_standards", "rigor_blooms_coverage"],
        )


def clear_rigor(apps, schema_editor):
    """Reverse by nulling the columns. The source data (`questions`) is
    untouched, so this is fully recoverable by re-running the forward pass."""
    Assignment = apps.get_model("assignments", "Assignment")
    Assignment.objects.update(
        rigor_demand=None, rigor_standards=None, rigor_blooms_coverage=None
    )


class Migration(migrations.Migration):

    dependencies = [
        ("assignments", "0035_assignment_rigor_blooms_coverage_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_rigor, clear_rigor),
    ]
