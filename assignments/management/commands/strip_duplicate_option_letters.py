"""One-time repair for MCQ options that already contain a doubled letter
marker in the database (e.g. "A. A) $x=5$" instead of "$x=5$").

Root cause (see assignments/services.py _strip_leading_option_letter): the
AI extraction prompts used to instruct the model to bake a letter marker
("A) ...") into option text, while the app also renders its own letter from
the option's position. The render-time cleanup only stripped one marker per
pass, so an assignment edited more than once could accumulate multiple
markers directly in Assignment.questions[].options[] and model_answer -
not just in the rendered HTML.

The prompts and the render-time strip are both fixed to be idempotent going
forward; this command repairs data written before that fix. It strips every
leading marker from each option string (and, if present, from
question_type == "OBJECTIVE" model_answer strings) using the same
_strip_leading_option_letter helper the renderer now uses, so the stored
text matches exactly what would be re-derived on the next render.

Conservative by design:
  * only touches OBJECTIVE questions' `options` and `model_answer` fields;
  * only rewrites a question if stripping actually changes something;
  * writes with bulk_update, not save(), so no unrelated signal/cascade
    fires for what is a silent data repair.

Run --dry-run first. Safe to re-run: once cleaned, a second pass reports
zero changes since the strip is idempotent.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from assignments.models import Assignment
from assignments.services import _strip_leading_option_letter


def repair_questions(assignment):
    """Return (repaired_questions, options_changed) or (None, 0) if nothing to do."""
    questions = assignment.questions
    if not isinstance(questions, list):
        return None, 0

    repaired = []
    changed_count = 0
    any_changed = False

    for question in questions:
        if (
            not isinstance(question, dict)
            or question.get("question_type") != "OBJECTIVE"
        ):
            repaired.append(question)
            continue

        question_changed = False
        new_question = dict(question)

        options = question.get("options")
        if isinstance(options, list):
            new_options = []
            for option in options:
                cleaned = _strip_leading_option_letter(option)
                if cleaned != option:
                    question_changed = True
                new_options.append(cleaned)
            new_question["options"] = new_options

        model_answer = question.get("model_answer")
        if isinstance(model_answer, str):
            cleaned_answer = _strip_leading_option_letter(model_answer)
            if cleaned_answer != model_answer:
                question_changed = True
            new_question["model_answer"] = cleaned_answer

        if question_changed:
            any_changed = True
            changed_count += 1
            repaired.append(new_question)
        else:
            repaired.append(question)

    if not any_changed:
        return None, 0

    return repaired, changed_count


class Command(BaseCommand):
    help = (
        "Strip doubled/leftover letter markers ('A. A) ...') baked directly "
        "into Assignment.questions options/model_answer text."
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

        queryset = Assignment.objects.exclude(questions=None)
        if school_id:
            queryset = queryset.filter(course__teacher__school_id=school_id)

        scanned = 0
        repaired_assignments = 0
        repaired_questions = 0
        batch = []

        for assignment in queryset.iterator(chunk_size=batch_size):
            scanned += 1
            questions, changed_count = repair_questions(assignment)
            if not changed_count:
                continue

            assignment.questions = questions
            repaired_assignments += 1
            repaired_questions += changed_count
            batch.append(assignment)

            if not dry_run and len(batch) >= batch_size:
                self._flush(batch)
                batch = []

        if not dry_run and batch:
            self._flush(batch)

        verb = "would repair" if dry_run else "repaired"
        self.stdout.write(f"scanned            : {scanned}")
        self.stdout.write(f"{verb:<19}: {repaired_assignments} assignment(s)")
        self.stdout.write(f"questions cleaned  : {repaired_questions}")
        if dry_run:
            self.stdout.write(self.style.WARNING("dry run - nothing was written"))
        else:
            self.stdout.write(self.style.SUCCESS("done"))

    def _flush(self, batch):
        with transaction.atomic():
            Assignment.objects.bulk_update(batch, ["questions"])
