"""A genuine score of 0 must produce a final grade of 0.00, not "no grade".

H-33: a student whose only graded submission scored 0/10 (two more
submitted but not yet graded) was shown a final grade of 10.00. Zero is
falsy in Python, so any `if score:` / `score or ...` / `if final_grade`
treats a real zero as missing. These tests put a genuine zero through every
path that writes a score or reads a final grade:

  * the AI grading persist step (`_populate_and_save_grade`);
  * the teacher's manual override (`PATCH .../update-grade`);
  * deleting a submission, which must recalculate down to the remaining 0;
  * the enrollment read API, including its `grade_letter`.

Scores are written the way production writes them, and `final_grade` is
never set by hand: the post_save receiver derives it.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import Course, Session, StudentCourse
from classrooms.services import enroll_student_by_email
from students.models import StudentSubmission
from students.services import _populate_and_save_grade
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def make_user(email, user_type):
    user = User.objects.create_user(email=email, password="password123")  # nosec
    user.user_type = user_type
    user.is_active = True
    user.first_name, user.last_name = "Test", user_type.title()
    user.save()
    return user


def grading_result(score, max_points):
    """The shape AIProcessor returns and `_populate_and_save_grade` persists."""
    return {
        "grading_summary": {
            "total_score": score,
            "max_total_points": max_points,
            "percentage": round(score / max_points * 100, 2) if max_points else 0,
        },
        "grading_confidence": 0.9,
    }


@override_settings(CACHES=LOCMEM)
class FinalGradeZeroScoreBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = make_user("zero-t@x.test", UserTypes.TEACHER)
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.course = Course.objects.create(
            name="English 7",
            teacher=self.teacher,
            session=Session.objects.create(name="Term", teacher=self.teacher),
        )
        self.student, _ = enroll_student_by_email(
            course=self.course, email="zero-s@x.test"
        )
        self.assignments = [
            Assignment.objects.create(
                title=title,
                course=self.course,
                teacher=self.teacher,
                status=AssignmentStatus.PUBLISHED,
                total_points=10,
            )
            for title in ("Practice Questions", "Essay", "Quiz")
        ]
        # Submitted, not yet graded: no score, no graded_at.
        self.ungraded = [
            StudentSubmission.objects.create(
                student=self.student, assignment=assignment, answers={}
            )
            for assignment in self.assignments[1:]
        ]
        self.submission = StudentSubmission.objects.create(
            student=self.student, assignment=self.assignments[0], answers={}
        )

    def enrollment(self):
        return StudentCourse.objects.get(student=self.student, course=self.course)

    def grade_by_ai(self, submission, score, max_points=10):
        submission.refresh_from_db()
        _populate_and_save_grade(submission, grading_result(score, max_points), None)

    def assert_final_grade(self, expected):
        self.assertEqual(self.enrollment().final_grade, Decimal(expected))


class AGenuineZeroIsAGradeNotAnAbsence(FinalGradeZeroScoreBase):
    def test_ungraded_submissions_alone_leave_no_final_grade(self):
        self.assertIsNone(self.enrollment().final_grade)

    def test_ai_graded_zero_out_of_ten_with_ungraded_siblings_is_zero(self):
        """The reported shape: one graded 0/10 plus two ungraded -> 0.00."""
        self.grade_by_ai(self.submission, 0)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.score, 0)
        self.assertEqual(self.submission.max_points, 10)
        self.assertIsNotNone(self.submission.graded_at)
        self.assert_final_grade("0.00")

    def test_teacher_override_to_zero_is_zero(self):
        self.grade_by_ai(self.submission, 7)
        self.assert_final_grade("70.00")

        self.client.force_authenticate(self.teacher)
        response = self.client.patch(
            reverse(
                "student-submission-update-grade", kwargs={"pk": self.submission.pk}
            ),
            {"score": 0},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assert_final_grade("0.00")

    def test_regrade_from_a_score_down_to_zero_is_zero(self):
        self.grade_by_ai(self.submission, 10)
        self.assert_final_grade("100.00")
        self.grade_by_ai(self.submission, 0)
        self.assert_final_grade("0.00")

    def test_deleting_the_other_graded_work_recalculates_down_to_zero(self):
        self.grade_by_ai(self.submission, 0)
        self.grade_by_ai(self.ungraded[0], 10)
        self.assert_final_grade("50.00")

        self.ungraded[0].delete()
        self.assert_final_grade("0.00")

    def test_zero_alongside_other_grades_is_counted(self):
        self.grade_by_ai(self.submission, 0)
        self.grade_by_ai(self.ungraded[0], 10)
        # (0 + 10) / (10 + 10); a dropped zero would read 100.00.
        self.assert_final_grade("50.00")


class TheApiReportsAZeroFinalGradeAsAGrade(FinalGradeZeroScoreBase):
    def fetch(self, user):
        cache.clear()
        self.client.force_authenticate(user)
        response = self.client.get(
            reverse("student-course-detail", kwargs={"pk": self.enrollment().pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def test_teacher_sees_zero_and_an_f(self):
        self.grade_by_ai(self.submission, 0)
        data = self.fetch(self.teacher)
        self.assertEqual(Decimal(data["final_grade"]), Decimal("0.00"))
        self.assertIsNotNone(
            data["grade_letter"], "a 0.00 final grade was reported as no grade"
        )
        self.assertEqual(data["grade_letter"]["letter_grade"], "F")

    def test_student_sees_zero_and_an_f(self):
        self.grade_by_ai(self.submission, 0)
        data = self.fetch(self.student)
        self.assertEqual(Decimal(data["final_grade"]), Decimal("0.00"))
        self.assertEqual(data["grade_letter"]["letter_grade"], "F")

    def test_no_graded_work_still_reports_no_grade(self):
        data = self.fetch(self.teacher)
        self.assertIsNone(data["final_grade"])
        self.assertIsNone(data["grade_letter"])
