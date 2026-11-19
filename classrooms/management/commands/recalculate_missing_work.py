"""Epic E2 go-live: count missing work in the course grades already stored.

Missing work counts as 0 by default (owner 2026-11-19). A stored final grade
is only rewritten by an event: a submission, an assignment or course change,
or the clocked due-date task, which is made only for due dates still ahead
while GRADE_CATEGORIES_ENABLED is on. So when the flag goes on, a due date
that passed earlier has no task, and the grades stored before then do not
count its missing work yet. Run this once right after switching the flag on:

    # DEFAULT: dry run. Reads only; prints every grade that would change.
    manage.py recalculate_missing_work

    # one course only
    manage.py recalculate_missing_work --course <uuid>

    # write the changes
    manage.py recalculate_missing_work --apply

For every course that counts missing work, --apply makes the due-date tasks
for due dates still ahead and recomputes the course under its lock, the same
recompute as switching the course setting (a GRADE_CHANGE audit entry only
for each grade that moved, source "missing_work_go_live"). No credits are
spent. Each changed grade is printed as `<course id>  <student id>  <old> ->
<new>`. A second run changes nothing.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from classrooms import grade_categories, grade_service
from classrooms.models import Course, StudentCourse
from classrooms.signals import compute_final_grade

SOURCE = "missing_work_go_live"


class Command(BaseCommand):
    help = (
        "E2 go-live: recompute stored course grades so missing work counts. "
        "Dry run unless --apply is given."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the new values. Without it nothing is written.",
        )
        parser.add_argument("--course", help="Only this course id.")

    def handle(self, *args, **options):
        if not grade_service.categories_enabled():
            raise CommandError(
                "GRADE_CATEGORIES_ENABLED is off: missing work is not read, "
                "so there is nothing to recompute. Switch the flag on first."
            )
        apply = options["apply"]
        courses = Course.objects.filter(missing_counts_as_zero=True).order_by(
            "created_at", "id"
        )
        if options["course"]:
            courses = courses.filter(pk=options["course"])

        mode = "APPLY" if apply else "DRY RUN (nothing written; pass --apply)"
        self.stdout.write(f"recalculate_missing_work: {mode}")
        checked = changed = 0
        for course_id in courses.values_list("id", flat=True).iterator():
            checked += 1
            for student_id, old, new in self._course(course_id, apply):
                changed += 1
                self.stdout.write(f"{course_id}  {student_id}  {old} -> {new}")
        verb = "changed" if apply else "would change"
        self.stdout.write(f"courses checked {checked}, grades {verb} {changed}")

    def _course(self, course_id, apply):
        if not apply:
            found = []
            for student_id, old in StudentCourse.objects.filter(
                course_id=course_id
            ).values_list("student_id", "final_grade"):
                new = compute_final_grade(student_id, course_id)
                if new != old:
                    found.append((student_id, old, new))
            return found
        with transaction.atomic():
            course = Course.objects.select_for_update().get(pk=course_id)
            grade_categories.sync_course_missing_work_tasks(course)
            return grade_categories.recalculate_course(course_id, source=SOURCE)
