"""Recompute the denormalized rigor_* columns on Assignment.

Migration 0036 does this once at deploy time. This command exists for the
cases a migration cannot cover:

  * the scoring rules in assignments/rigor.py change and every row needs
    rescoring without inventing a no-op schema migration;
  * a write path is found that bypassed the pre_save hook (a raw UPDATE, a
    bulk_update, a fixture load) and rows have drifted;
  * an operator wants to check for drift before trusting the dashboard.

Idempotent: running it twice in a row produces the same values, and --dry-run
reports what would change without writing.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from assignments.models import Assignment
from assignments.rigor import score_assignment

RIGOR_FIELDS = ["rigor_demand", "rigor_standards", "rigor_blooms_coverage"]


def _differs(a, b):
    """Float-tolerant comparison that also treats None correctly."""
    if a is None or b is None:
        return (a is None) != (b is None)
    return abs(a - b) > 1e-9


class Command(BaseCommand):
    help = "Recompute Assignment.rigor_* from each assignment's questions payload."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Rows to load and write per batch (default: 500).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )
        parser.add_argument(
            "--school",
            dest="school_id",
            default=None,
            help="Limit to assignments belonging to one school (by school id).",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]
        school_id = options["school_id"]

        if batch_size < 1:
            self.stderr.write(self.style.ERROR("--batch-size must be at least 1"))
            return

        queryset = Assignment.objects.all().only("id", "questions", *RIGOR_FIELDS)
        if school_id:
            queryset = queryset.filter(course__teacher__school_id=school_id)

        scanned = 0
        changed = 0
        scored = 0
        batch = []

        for assignment in queryset.iterator(chunk_size=batch_size):
            scanned += 1
            demand, standards, coverage = score_assignment(assignment.questions)

            if demand is not None:
                scored += 1

            if (
                _differs(assignment.rigor_demand, demand)
                or _differs(assignment.rigor_standards, standards)
                or _differs(assignment.rigor_blooms_coverage, coverage)
            ):
                changed += 1
                assignment.rigor_demand = demand
                assignment.rigor_standards = standards
                assignment.rigor_blooms_coverage = coverage
                batch.append(assignment)

            if not dry_run and len(batch) >= batch_size:
                self._flush(batch)
                batch = []

        if not dry_run and batch:
            self._flush(batch)

        coverage_pct = (scored / scanned * 100) if scanned else 0.0
        verb = "would update" if dry_run else "updated"

        self.stdout.write(f"scanned      : {scanned}")
        self.stdout.write(f"{verb:<13}: {changed}")
        self.stdout.write(
            f"scoreable    : {scored} ({coverage_pct:.1f}% have usable Bloom's data)"
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("dry run - nothing was written"))
        else:
            self.stdout.write(self.style.SUCCESS("done"))

    def _flush(self, batch):
        with transaction.atomic():
            Assignment.objects.bulk_update(batch, RIGOR_FIELDS)
