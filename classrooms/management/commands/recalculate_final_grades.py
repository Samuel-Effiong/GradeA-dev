"""Bring stored `StudentCourse.final_grade` values up to the current formula.

`final_grade` is only rewritten when one of the student's submissions in
that course is saved or deleted (classrooms.signals). Rows whose last such
write predates a formula change keep the old value indefinitely - H-33 found
a student shown 10.00 (the pre-2026-08-06 `Avg(score_percentage)`) whose
graded work implies 20.87.

    # DEFAULT: dry run. Reads only; prints every row that would change.
    manage.py recalculate_final_grades

    # narrow it down
    manage.py recalculate_final_grades --course <uuid>
    manage.py recalculate_final_grades --enrollment <uuid>

    # write the changes (each row under the same lock the signal uses)
    manage.py recalculate_final_grades --apply

Every changed row is printed as `<enrollment id>  <old> -> <new>`, so the
output of a run is also the record needed to reverse it. Running --apply
twice changes nothing the second time.

A row whose grade would be CLEARED - a stored grade with no gradable work
behind it under today's rules - is NEVER changed by a bulk run. It is listed
as NEEDS REVIEW and left as it is: a grade a student and teacher have
already seen must not silently turn into "no grade". Once a person has
looked at that row and decided, it can be cleared on its own:

    manage.py recalculate_final_grades --apply --allow-clear --enrollment <uuid>
"""

from django.core.management.base import BaseCommand, CommandError

from classrooms.models import StudentCourse
from classrooms.signals import _recalculate_final_grade, compute_final_grade


class Command(BaseCommand):
    help = (
        "Recompute stored final grades with the current formula. Dry run "
        "unless --apply is given."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the new values. Without it nothing is written.",
        )
        parser.add_argument("--course", help="Only enrollments in this course id.")
        parser.add_argument("--enrollment", help="Only this enrollment id.")
        parser.add_argument(
            "--allow-clear",
            action="store_true",
            help=(
                "Also clear a grade that has no gradable work behind it. "
                "Only accepted together with --enrollment, after review."
            ),
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        allow_clear = options["allow_clear"]
        if allow_clear and not options["enrollment"]:
            raise CommandError(
                "--allow-clear clears a grade someone has already seen; it is "
                "only accepted for a single reviewed row (--enrollment <uuid>)."
            )

        enrollments = StudentCourse.objects.order_by("created_at", "id")
        if options["course"]:
            enrollments = enrollments.filter(course_id=options["course"])
        if options["enrollment"]:
            enrollments = enrollments.filter(id=options["enrollment"])

        mode = "APPLY" if apply else "DRY RUN (nothing written; pass --apply)"
        self.stdout.write(f"recalculate_final_grades: {mode}")

        checked = changed = held = 0
        for enrollment in enrollments.values(
            "id", "student_id", "course_id", "final_grade"
        ).iterator():
            checked += 1
            if apply:
                # Re-reads under the row lock and writes via the same path
                # as the signal, so a submission graded mid-run can't be
                # overwritten with a value computed before it landed.
                result = _recalculate_final_grade(
                    enrollment["student_id"],
                    enrollment["course_id"],
                    allow_clear=allow_clear,
                )
                if result is None:
                    continue
                old, new, written = result
            else:
                old = enrollment["final_grade"]
                new = compute_final_grade(
                    enrollment["student_id"], enrollment["course_id"]
                )
                if old == new:
                    continue
                written = not (new is None and old is not None and not allow_clear)

            if written:
                changed += 1
                self.stdout.write(f"{enrollment['id']}  {old} -> {new}")
            else:
                held += 1
                self.stdout.write(
                    f"{enrollment['id']}  {old} -> {new}  "
                    "[NEEDS REVIEW: would clear a grade; left unchanged]"
                )

        verb = "changed" if apply else "would change"
        self.stdout.write(
            f"checked {checked}, {verb} {changed}, held for review {held}"
        )
