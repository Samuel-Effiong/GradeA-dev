"""One-time repair for Assignment.title rows written before the pre_save
sanitizer (assignments.signals.sanitize_assignment_title) existed.

Root cause (see assignments/signals.py sanitize_assignment_title and
assignments/services.py _strip_html_from_title): AI extraction has always
wrapped the title in heading/paragraph tags meant for the rich editor/PDF
body, but nothing stripped that markup before it was saved to
Assignment.title - a field read verbatim in plain-text contexts
(notification emails, PDF headers/filenames, list views). Those tags leaked
into emails and PDFs as literal text (e.g. "<p>Matrices Exam</p>").

The signal now sanitizes every new write going forward; this command
repairs rows written before that fix.

Conservative by design:
  * only rewrites an assignment if stripping actually changes `title`;
  * writes with bulk_update, not save(), so no unrelated signal/cascade
    fires for what is a silent data repair.

Run --dry-run first. Safe to re-run: once cleaned, a second pass reports
zero changes since the strip is idempotent.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from assignments.models import Assignment
from assignments.services import _strip_html_from_title


class Command(BaseCommand):
    help = (
        "Strip HTML tags baked directly into Assignment.title (e.g. "
        "'<p>Exam</p>') left over from before title sanitization existed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=200,
            help="Assignments to write per batch (default: 200).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be repaired without writing.",
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

        queryset = Assignment.objects.exclude(title=None)
        if school_id:
            queryset = queryset.filter(course__teacher__school_id=school_id)

        scanned = 0
        repaired = 0
        batch = []

        for assignment in queryset.iterator(chunk_size=batch_size):
            scanned += 1
            cleaned = _strip_html_from_title(assignment.title)
            if cleaned == assignment.title:
                continue

            assignment.title = cleaned
            repaired += 1
            batch.append(assignment)

            if not dry_run and len(batch) >= batch_size:
                self._flush(batch)
                batch = []

        if not dry_run and batch:
            self._flush(batch)

        verb = "would repair" if dry_run else "repaired"
        self.stdout.write(f"scanned  : {scanned}")
        self.stdout.write(f"{verb:<9}: {repaired} assignment(s)")
        if dry_run:
            self.stdout.write(self.style.WARNING("dry run - nothing was written"))
        else:
            self.stdout.write(self.style.SUCCESS("done"))

    def _flush(self, batch):
        with transaction.atomic():
            Assignment.objects.bulk_update(batch, ["title"])
