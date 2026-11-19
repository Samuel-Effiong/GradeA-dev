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

The same recompute also runs once by itself (classrooms/missing_work_go_live.py,
owner 2026-11-19 "automatic"): the hourly Beat task does it the first time
the flag is on, and records it in a GoLiveRun row. This command reports that
row. A full --apply (no --course) that finds it not yet done takes the same
claim, so it never runs alongside the automatic run, and marks it done, so
the automatic run then skips. Once it is done, --apply still recomputes (a
repair tool; a second run changes nothing).
"""

from django.core.management.base import BaseCommand, CommandError

from classrooms import grade_service, missing_work_go_live
from classrooms.models import StudentCourse
from classrooms.signals import compute_final_grade

SOURCE = missing_work_go_live.SOURCE


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
        courses = missing_work_go_live.course_ids()
        if options["course"]:
            courses = courses.filter(pk=options["course"])

        mode = "APPLY" if apply else "DRY RUN (nothing written; pass --apply)"
        self.stdout.write(f"recalculate_missing_work: {mode}")
        done = self._report_marker()
        if apply and not options["course"] and not done:
            self._claimed_run()
            return
        checked = changed = 0
        for course_id in courses.iterator():
            checked += 1
            for student_id, old, new in self._course(course_id, apply):
                changed += 1
                self.stdout.write(f"{course_id}  {student_id}  {old} -> {new}")
        verb = "changed" if apply else "would change"
        self.stdout.write(f"courses checked {checked}, grades {verb} {changed}")

    def _report_marker(self):
        marker = missing_work_go_live.marker()
        if marker is not None and marker.completed_at is not None:
            self.stdout.write(
                f"automatic go-live: already ran at {marker.completed_at:%Y-%m-%d %H:%M} "
                f"(courses {marker.courses_checked}, grades changed "
                f"{marker.grades_changed}); a run now changes only what moved since"
            )
            return True
        self.stdout.write("automatic go-live: not completed yet")
        return False

    def _claimed_run(self):
        def show(course_id, student_id, old, new):
            self.stdout.write(f"{course_id}  {student_id}  {old} -> {new}")

        result = missing_work_go_live.run(on_change=show)
        status = result["status"]
        if status in (
            missing_work_go_live.HELD_ELSEWHERE,
            missing_work_go_live.LOST_CLAIM,
        ):
            raise CommandError(
                f"The automatic go-live run is in progress ({status}), "
                "so this run stopped. Try again when it has finished."
            )
        self.stdout.write(f"automatic go-live: {status}")
        self.stdout.write(
            f"courses checked {result['courses_checked']}, "
            f"grades changed {result['grades_changed']}"
        )

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
        return missing_work_go_live.recompute_course(course_id)
