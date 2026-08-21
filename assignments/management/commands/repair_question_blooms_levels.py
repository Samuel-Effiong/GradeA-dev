"""Recover per-question blooms_level lost to the QuestionSerializer bug.

QuestionSerializer.validate_blooms_level raised on invalid input but forgot to
return valid input, so DRF wrote None for every *correctly* labelled question.
Every assignment saved through that path lost its cognitive-demand data, which
is what rigor scoring (assignments/rigor.py) is built on.

The data is not gone: Assignment.ai_raw_payload holds the untouched AI
response, and the extraction schema (ai_processor/services.py) requires
blooms_level on every question. This command copies the level back into
`questions` for any question that is missing one, matching on question_number
and falling back to position when the numbers do not line up.

Conservative by design:
  * only fills a level that is missing or blank -- never overwrites one that
    is already set, since a teacher may have corrected it by hand;
  * only accepts levels in the recognised taxonomy;
  * writes with bulk_update rather than save(), so the assignment post_save
    cascade (cache purges, periodic-task sync, "new assignment posted"
    notifications) does not fire for what is a silent data repair. The rigor
    columns are recomputed inline instead, exactly as the pre_save hook would.

Run --dry-run first. Safe to re-run: once a question has a level it is left
alone, so a second pass reports zero changes.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from assignments.models import Assignment
from assignments.rigor import BLOOMS_SCALE, score_assignment

RIGOR_FIELDS = ["rigor_demand", "rigor_standards", "rigor_blooms_coverage"]


def _valid_level(value):
    """Return the level if it is one the taxonomy recognises, else None."""
    if isinstance(value, str) and value.strip().lower() in BLOOMS_SCALE:
        return value.strip()
    return None


def _payload_questions(assignment):
    """Pull the question list out of the stored AI response, if there is one."""
    payload = assignment.ai_raw_payload
    if not isinstance(payload, dict):
        return []
    questions = payload.get("questions")
    return questions if isinstance(questions, list) else []


def _index_payload(payload_questions):
    """Map question_number -> level, plus the positional list as a fallback."""
    by_number = {}
    positional = []

    for entry in payload_questions:
        level = (
            _valid_level(entry.get("blooms_level")) if isinstance(entry, dict) else None
        )
        positional.append(level)
        if isinstance(entry, dict):
            number = entry.get("question_number")
            if isinstance(number, int) and level:
                by_number[number] = level

    return by_number, positional


def repair_questions(assignment):
    """Return (repaired_questions, filled_count) or (None, 0) if nothing to do."""
    questions = assignment.questions
    if not isinstance(questions, list):
        return None, 0

    payload_questions = _payload_questions(assignment)
    if not payload_questions:
        return None, 0

    by_number, positional = _index_payload(payload_questions)
    if not by_number and not any(positional):
        return None, 0

    repaired = []
    filled = 0

    for position, question in enumerate(questions):
        if not isinstance(question, dict):
            repaired.append(question)
            continue

        if _valid_level(question.get("blooms_level")):
            repaired.append(question)  # already good, leave it alone
            continue

        number = question.get("question_number")
        level = by_number.get(number) if isinstance(number, int) else None
        if level is None and position < len(positional):
            level = positional[position]

        if level is None:
            repaired.append(question)
            continue

        question = dict(question)
        question["blooms_level"] = level
        repaired.append(question)
        filled += 1

    if not filled:
        return None, 0

    return repaired, filled


class Command(BaseCommand):
    help = (
        "Restore per-question blooms_level from ai_raw_payload where the "
        "serializer bug nulled it, and rescore the affected assignments."
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

        queryset = Assignment.objects.exclude(questions=None).exclude(
            ai_raw_payload=None
        )
        if school_id:
            queryset = queryset.filter(course__teacher__school_id=school_id)

        scanned = 0
        repaired_assignments = 0
        repaired_questions = 0
        newly_scoreable = 0
        batch = []

        for assignment in queryset.iterator(chunk_size=batch_size):
            scanned += 1
            questions, filled = repair_questions(assignment)
            if not filled:
                continue

            was_scoreable = assignment.rigor_demand is not None

            assignment.questions = questions
            demand, standards, coverage = score_assignment(questions)
            assignment.rigor_demand = demand
            assignment.rigor_standards = standards
            assignment.rigor_blooms_coverage = coverage

            repaired_assignments += 1
            repaired_questions += filled
            if demand is not None and not was_scoreable:
                newly_scoreable += 1

            batch.append(assignment)

            if not dry_run and len(batch) >= batch_size:
                self._flush(batch)
                batch = []

        if not dry_run and batch:
            self._flush(batch)

        verb = "would repair" if dry_run else "repaired"
        self.stdout.write(f"scanned            : {scanned}")
        self.stdout.write(f"{verb:<19}: {repaired_assignments} assignment(s)")
        self.stdout.write(f"questions filled   : {repaired_questions}")
        self.stdout.write(f"newly scoreable    : {newly_scoreable}")
        if dry_run:
            self.stdout.write(self.style.WARNING("dry run - nothing was written"))
        else:
            self.stdout.write(self.style.SUCCESS("done"))

    def _flush(self, batch):
        with transaction.atomic():
            Assignment.objects.bulk_update(batch, ["questions", *RIGOR_FIELDS])
