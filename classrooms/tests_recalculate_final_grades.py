"""`recalculate_final_grades` repairs final grades left by an older formula.

Before 2026-08-06 (0252776) the post_save receiver stored
`Avg(score_percentage)` over every submission with a percentage - graded or
not - and skipped submissions with no percentage. The current receiver only
rewrites a row when one of the student's submissions in that course is saved
or deleted, so rows last written by the old code still show the old value.
H-33's student was one: stored 10.00, graded work 4/5 + 20/100 + 0/10 =
20.87.

Stale rows are produced here by running that old receiver's body verbatim
(`write_like_pre_0252776`), i.e. the exact write production made, rather
than by setting `final_grade` to an invented number.
"""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Avg
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, Session, StudentCourse
from classrooms.services import enroll_student_by_email
from students.models import StudentSubmission
from students.services import _populate_and_save_grade
from users.models import UserTypes

from .tests_final_grade_zero_score import LOCMEM, grading_result, make_user


def write_like_pre_0252776(student, course):
    """The receiver body as it was at 7e6603d, unchanged."""
    enrollment = StudentCourse.objects.get(student=student, course=course)
    avg_percentage = StudentSubmission.objects.filter(
        student=student, assignment__course=course, score_percentage__isnull=False
    ).aggregate(Avg("score_percentage"))["score_percentage__avg"]
    if avg_percentage is not None:
        # .update(), not .save(): the CURRENT receivers must not run and
        # "correct" the row, or it would not be stale.
        StudentCourse.objects.filter(pk=enrollment.pk).update(
            final_grade=avg_percentage
        )


@override_settings(CACHES=LOCMEM)
class RecalculateFinalGradesBase(APITestCase):
    def setUp(self):
        self.teacher = make_user("recalc-t@x.test", UserTypes.TEACHER)
        self.course = Course.objects.create(
            name="English 7",
            teacher=self.teacher,
            session=Session.objects.create(name="Term", teacher=self.teacher),
        )

    def assignment(self, title, total_points):
        return Assignment.objects.create(
            title=title,
            course=self.course,
            teacher=self.teacher,
            status=AssignmentStatus.PUBLISHED,
            total_points=total_points,
        )

    def the_h33_student(self, email="recalc-s@x.test"):
        """4/5 graded before max_points was stored, then 20/100 and 0/10,
        with the stored grade left by the old formula: 10.00."""
        student, _ = enroll_student_by_email(course=self.course, email=email)
        StudentSubmission.objects.create(
            student=student,
            assignment=self.assignment(f"{email} practice", 5),
            answers={},
            score=4,
            ai_score=4,
            graded_at=timezone.now(),
            max_points=None,
            score_percentage=None,
        )
        for title, points, score in (("essay", 100, 20), ("quiz", 10, 0)):
            submission = StudentSubmission.objects.create(
                student=student,
                assignment=self.assignment(f"{email} {title}", points),
                answers={},
            )
            _populate_and_save_grade(submission, grading_result(score, points), None)
        write_like_pre_0252776(student, self.course)
        return StudentCourse.objects.get(student=student, course=self.course)

    def make_ungraded_with_old_grade(self, email):
        """The old formula counted percentages on UNGRADED submissions; the
        current one does not, so such a grade has nothing behind it."""
        student, _ = enroll_student_by_email(course=self.course, email=email)
        StudentSubmission.objects.create(
            student=student,
            assignment=self.assignment(f"{email} ungraded", 10),
            answers={},
            score_percentage=40,
        )
        write_like_pre_0252776(student, self.course)
        enrollment = StudentCourse.objects.get(student=student, course=self.course)
        self.assertEqual(str(self.stored(enrollment)), "40.00")
        return enrollment

    def run_command(self, *args):
        out = StringIO()
        call_command("recalculate_final_grades", *args, stdout=out)
        return out.getvalue()

    def stored(self, enrollment):
        enrollment.refresh_from_db()
        return enrollment.final_grade


class DryRunIsTheDefault(RecalculateFinalGradesBase):
    def test_reproduces_the_reported_stale_value(self):
        enrollment = self.the_h33_student()
        self.assertEqual(str(self.stored(enrollment)), "10.00")

    def test_dry_run_reports_the_change_and_writes_nothing(self):
        enrollment = self.the_h33_student()
        out = self.run_command()
        self.assertIn("DRY RUN", out)
        self.assertIn(f"{enrollment.id}  10.00 -> 20.87", out)
        self.assertIn("would change 1, held for review 0", out)
        self.assertEqual(str(self.stored(enrollment)), "10.00")


class ApplyRepairsAndIsIdempotent(RecalculateFinalGradesBase):
    def test_apply_writes_the_current_formula(self):
        enrollment = self.the_h33_student()
        out = self.run_command("--apply")
        self.assertIn(f"{enrollment.id}  10.00 -> 20.87", out)
        self.assertEqual(str(self.stored(enrollment)), "20.87")

    def test_a_second_apply_changes_nothing(self):
        self.the_h33_student()
        self.run_command("--apply")
        out = self.run_command("--apply")
        self.assertIn("changed 0, held for review 0", out)

    def test_correct_rows_are_left_alone(self):
        student, _ = enroll_student_by_email(course=self.course, email="ok@x.test")
        submission = StudentSubmission.objects.create(
            student=student, assignment=self.assignment("ok", 10), answers={}
        )
        _populate_and_save_grade(submission, grading_result(7, 10), None)
        out = self.run_command("--apply")
        self.assertIn("changed 0, held for review 0", out)
        enrollment = StudentCourse.objects.get(student=student, course=self.course)
        self.assertEqual(str(self.stored(enrollment)), "70.00")

    def test_enrollment_filter_touches_only_that_row(self):
        first = self.the_h33_student("first@x.test")
        second = self.the_h33_student("second@x.test")
        self.run_command("--apply", "--enrollment", str(first.id))
        self.assertEqual(str(self.stored(first)), "20.87")
        self.assertEqual(str(self.stored(second)), "10.00")


class AGradeIsNeverClearedByABulkRun(RecalculateFinalGradesBase):
    """A grade someone has seen must not silently become "no grade"."""

    def test_bulk_apply_holds_the_row_for_review(self):
        enrollment = self.make_ungraded_with_old_grade("held@x.test")
        stale = self.the_h33_student()

        out = self.run_command("--apply")

        self.assertIn(f"{enrollment.id}  40.00 -> None  [NEEDS REVIEW", out)
        self.assertIn("changed 1, held for review 1", out)
        self.assertEqual(str(self.stored(enrollment)), "40.00")
        self.assertEqual(str(self.stored(stale)), "20.87")

    def test_dry_run_shows_the_same_hold(self):
        enrollment = self.make_ungraded_with_old_grade("held2@x.test")
        out = self.run_command()
        self.assertIn(f"{enrollment.id}  40.00 -> None  [NEEDS REVIEW", out)
        self.assertIn("held for review 1", out)

    def test_allow_clear_needs_a_single_enrollment(self):
        with self.assertRaises(CommandError):
            self.run_command("--apply", "--allow-clear")

    def test_a_reviewed_row_can_be_cleared_on_its_own(self):
        enrollment = self.make_ungraded_with_old_grade("reviewed@x.test")
        out = self.run_command(
            "--apply", "--allow-clear", "--enrollment", str(enrollment.id)
        )
        self.assertIn(f"{enrollment.id}  40.00 -> None", out)
        self.assertIsNone(self.stored(enrollment))

    def test_the_submission_receivers_still_clear_a_grade(self):
        """Deleting the only graded submission really does remove the grade;
        the hold is for the repair command only."""
        student, _ = enroll_student_by_email(course=self.course, email="d@x.test")
        submission = StudentSubmission.objects.create(
            student=student, assignment=self.assignment("only", 10), answers={}
        )
        _populate_and_save_grade(submission, grading_result(6, 10), None)
        enrollment = StudentCourse.objects.get(student=student, course=self.course)
        self.assertEqual(str(self.stored(enrollment)), "60.00")
        submission.delete()
        self.assertIsNone(self.stored(enrollment))
