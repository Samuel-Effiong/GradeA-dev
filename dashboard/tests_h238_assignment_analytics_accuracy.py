"""H-238: the teacher-admin per-assignment analytics endpoint
(`teacher-admin/dashboard/assignments/<id>`) was leaking withdrawn
students into its numbers, computing `average_grade` with an ad hoc
`Avg("score_percentage")` instead of the one documented final-grade
formula, and averaging AI confidence over ungraded (default-0, never
null) submissions. Also split `ai_extraction_confidence` (now a real
per-submission average) from the new `question_extraction_confidence`
(the one-time value stamped when the teacher's own question paper was
parsed - unrelated to how well the AI read any particular student's
work).
"""

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import EnrollmentStatusType
from dashboard.tests_teacher_all_students import Builder
from students.models import StudentSubmission
from users.models import UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class AssignmentAnalyticsAccuracyTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self.user(UserTypes.TEACHER)
        self.term = self.session(self.teacher)
        self.course_obj = self.course(self.teacher, self.term)
        self.client.force_authenticate(self.teacher)

    def _url(self, assignment):
        return reverse(
            "teacher-admin-assignments", kwargs={"assignment_id": assignment.id}
        )

    def _get(self, assignment):
        cache.clear()
        response = self.client.get(self._url(assignment))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def _submit(
        self,
        assignment,
        student,
        *,
        score,
        max_points,
        grading_confidence=0,
        extraction_confidence=0,
        graded=True,
    ):
        return StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers={"q1": "a"},
            score=score,
            score_percentage=(
                (score / max_points * 100) if score is not None and max_points else None
            ),
            max_points=max_points,
            graded_at=timezone.now() if graded else None,
            is_published=True,
            grading_confidence=grading_confidence,
            extraction_confidence=extraction_confidence,
        )

    # -- #1: withdrawn students excluded ---------------------------------

    def test_withdrawn_students_submission_excluded_from_totals(self):
        assignment = self.assignment(self.course_obj)
        enrolled = self.user(UserTypes.STUDENT, first="Enrolled")
        self.enrol(enrolled, self.course_obj)
        self._submit(
            assignment, enrolled, score=80, max_points=100, grading_confidence=90
        )

        withdrawn = self.user(UserTypes.STUDENT, first="Withdrawn")
        self.enrol(withdrawn, self.course_obj, status_=EnrollmentStatusType.WITHDRAWN)
        self._submit(
            assignment, withdrawn, score=0, max_points=100, grading_confidence=0
        )

        data = self._get(assignment)

        self.assertEqual(data["total_submissions"], 1)
        self.assertEqual(data["average_grade"], 80.0)
        self.assertEqual(data["ai_grading_confidence"], 90.0)

    def test_withdrawn_students_submission_would_have_changed_the_old_numbers(self):
        """Proves the filter is load-bearing: without it, this withdrawn
        student's 0-scored, 0-confidence row drags both averages down."""
        assignment = self.assignment(self.course_obj)
        enrolled = self.user(UserTypes.STUDENT, first="Enrolled")
        self.enrol(enrolled, self.course_obj)
        self._submit(
            assignment, enrolled, score=100, max_points=100, grading_confidence=100
        )

        withdrawn = self.user(UserTypes.STUDENT, first="Withdrawn2")
        self.enrol(withdrawn, self.course_obj, status_=EnrollmentStatusType.WITHDRAWN)
        self._submit(
            assignment, withdrawn, score=0, max_points=100, grading_confidence=0
        )

        data = self._get(assignment)

        # If the withdrawn row counted, average_grade would be 50.0 and
        # ai_grading_confidence would be 50.0 instead.
        self.assertEqual(data["total_submissions"], 1)
        self.assertEqual(data["average_grade"], 100.0)
        self.assertEqual(data["ai_grading_confidence"], 100.0)

    # -- #2: average_grade via final_grade_from ---------------------------

    def test_average_grade_is_points_weighted_not_a_plain_mean_of_percentages(self):
        assignment = self.assignment(self.course_obj)
        a = self.user(UserTypes.STUDENT, first="A")
        self.enrol(a, self.course_obj)
        b = self.user(UserTypes.STUDENT, first="B")
        self.enrol(b, self.course_obj)

        # A 100-point submission at 50% and a 5-point submission at 100%.
        # Plain mean of percentages: (50 + 100) / 2 = 75.0.
        # Points-weighted: (50 + 5) / (100 + 5) * 100 = 52.38.
        self._submit(assignment, a, score=50, max_points=100)
        self._submit(assignment, b, score=5, max_points=5)

        data = self._get(assignment)

        self.assertEqual(data["average_grade"], 52.38)

    def test_average_grade_falls_back_to_assignment_total_points_when_unset(self):
        assignment = self.assignment(self.course_obj)
        assignment.total_points = 100
        assignment.save(update_fields=["total_points"])

        student = self.user(UserTypes.STUDENT, first="Fallback")
        self.enrol(student, self.course_obj)
        self._submit(assignment, student, score=40, max_points=None)

        data = self._get(assignment)

        self.assertEqual(data["average_grade"], 40.0)

    def test_average_grade_zero_when_no_graded_submissions(self):
        assignment = self.assignment(self.course_obj)
        student = self.user(UserTypes.STUDENT, first="Ungraded")
        self.enrol(student, self.course_obj)
        self._submit(assignment, student, score=None, max_points=100, graded=False)

        data = self._get(assignment)

        self.assertEqual(data["total_submissions"], 1)
        self.assertEqual(data["average_grade"], 0)

    def test_total_submissions_zero_when_no_submissions_at_all(self):
        assignment = self.assignment(self.course_obj)

        data = self._get(assignment)

        self.assertEqual(data["total_submissions"], 0)
        self.assertEqual(data["average_grade"], 0)
        self.assertEqual(data["ai_grading_confidence"], 0)
        self.assertEqual(data["ai_extraction_confidence"], 0)

    # -- #3: confidence averages only over graded submissions --------------

    def test_grading_confidence_ignores_the_default_zero_on_an_ungraded_submission(
        self,
    ):
        assignment = self.assignment(self.course_obj)
        graded_student = self.user(UserTypes.STUDENT, first="Graded")
        self.enrol(graded_student, self.course_obj)
        self._submit(
            assignment,
            graded_student,
            score=90,
            max_points=100,
            grading_confidence=95,
        )

        ungraded_student = self.user(UserTypes.STUDENT, first="Ungraded2")
        self.enrol(ungraded_student, self.course_obj)
        self._submit(
            assignment,
            ungraded_student,
            score=None,
            max_points=100,
            grading_confidence=0,
            graded=False,
        )

        data = self._get(assignment)

        # If the ungraded 0 counted: (95 + 0) / 2 = 47.5.
        self.assertEqual(data["ai_grading_confidence"], 95.0)

    def test_grading_confidence_none_when_nothing_graded(self):
        assignment = self.assignment(self.course_obj)
        student = self.user(UserTypes.STUDENT, first="Nope")
        self.enrol(student, self.course_obj)
        self._submit(assignment, student, score=None, max_points=100, graded=False)

        data = self._get(assignment)

        self.assertEqual(data["ai_grading_confidence"], 0)

    # -- #4: extraction confidence split ----------------------------------

    def test_extraction_confidence_split_question_vs_submission(self):
        assignment = self.assignment(self.course_obj)
        assignment.extraction_confidence = 70
        assignment.save(update_fields=["extraction_confidence"])

        student = self.user(UserTypes.STUDENT, first="Extract")
        self.enrol(student, self.course_obj)
        self._submit(
            assignment, student, score=80, max_points=100, extraction_confidence=99
        )

        data = self._get(assignment)

        # The question-paper figure and the per-submission average must
        # not be the same number here, proving they come from two
        # different sources.
        self.assertEqual(data["question_extraction_confidence"], 70)
        self.assertEqual(data["ai_extraction_confidence"], 99.0)

    def test_submission_extraction_confidence_averages_graded_submissions_only(self):
        assignment = self.assignment(self.course_obj)

        a = self.user(UserTypes.STUDENT, first="ExtractA")
        self.enrol(a, self.course_obj)
        self._submit(assignment, a, score=80, max_points=100, extraction_confidence=100)

        b = self.user(UserTypes.STUDENT, first="ExtractB")
        self.enrol(b, self.course_obj)
        self._submit(
            assignment,
            b,
            score=None,
            max_points=100,
            extraction_confidence=0,
            graded=False,
        )

        data = self._get(assignment)

        # If the ungraded row's default-0 counted: (100 + 0) / 2 = 50.0.
        self.assertEqual(data["ai_extraction_confidence"], 100.0)
