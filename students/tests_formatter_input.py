"""
H-127: the feedback formatter is never sent the second-opinion block.

The formatter is an AI call that turns the saved grading result into the
wording a student reads. Three places build its prompt from the saved
result:

  * the automatic follow-up after a grading (students/services.py);
  * the teacher's "teacher feedback" route, when no formatted grade exists
    yet (students/views.py);
  * the teacher's "update grade" route (students/views.py).

Each one placed the whole saved result in the prompt. The second-opinion
block in it is the second grader's marks and reasons and, when the second
opinion failed, the text of the failure: written for the teacher's review
queue. Sent to the formatter it could be restated in what the student
reads. Each site is tested by the prompt it actually dispatches.

Run with:
    python manage.py test students.tests_formatter_input
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment
from classrooms.models import Course, Session
from students.feedback_projection import grading_result_for_formatter
from students.models import StudentSubmission
from students.services import grade_engine
from users.models import CustomUser, UserTypes

SECOND_OPINION_MARKERS = (
    "second_opinion",
    "second-grader-model",
    "SECOND GRADER RATIONALE",
    "RAW SECOND OPINION ERROR",
)


def grading_result():
    """A grading result with a second-opinion block; a new one each time,
    because a grading run changes the result it is given."""
    return {
        "grading_summary": {
            "total_score": 8,
            "max_total_points": 10,
            "percentage": 80.0,
        },
        "grading_confidence": 90,
        "grading_model": "first-grader-model",
        "question_evaluations": [
            {
                "question_number": 1,
                "max_points": 10,
                "score_awarded": 8,
                "feedback_for_student": "WORDS FOR THE STUDENT",
            }
        ],
        "second_opinion": {
            "model": "second-grader-model",
            "error": "RAW SECOND OPINION ERROR: you only have 12 credits",
            "selected": {"1": ["high_stakes"]},
            "agreements": [],
            "disagreements": [],
            "note": "SECOND GRADER RATIONALE",
        },
    }


def dispatched_prompt(delay_mock):
    """The formatter prompt among the arguments of the one dispatch."""
    delay_mock.assert_called_once()
    args, kwargs = delay_mock.call_args
    prompts = [
        value
        for value in list(args) + list(kwargs.values())
        if isinstance(value, str) and "Grading Result:" in value
    ]
    assert len(prompts) == 1, prompts
    return prompts[0]


def assert_prompt_has_the_result_without_the_second_opinion(test, prompt):
    # The rest of the result still reaches the formatter...
    test.assertIn("grading_summary", prompt)
    test.assertIn("WORDS FOR THE STUDENT", prompt)
    test.assertIn("first-grader-model", prompt)
    # ...and nothing of the second opinion does.
    for marker in SECOND_OPINION_MARKERS:
        test.assertNotIn(marker, prompt)


def make_people_and_submission(**submission_fields):
    """A teacher and one submission of one of their students."""
    stamp = timezone.now().timestamp()
    teacher = CustomUser.objects.create_user(
        email=f"h127-formatter-teacher-{stamp}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
    )
    student = CustomUser.objects.create_user(
        email=f"h127-formatter-student-{stamp}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.STUDENT,
    )
    session = Session.objects.create(name="S", teacher=teacher)
    course = Course.objects.create(name="C", teacher=teacher, session=session)
    assignment = Assignment.objects.create(
        title="A",
        course=course,
        total_points=10,
        questions=[{"question_number": 1, "points": 10}],
    )
    submission = StudentSubmission.objects.create(
        assignment=assignment,
        student=student,
        answers=[{"question_number": 1, "answer_html": "An answer."}],
        **submission_fields,
    )
    return teacher, submission


class GradingResultForFormatterTest(SimpleTestCase):
    def test_only_the_second_opinion_block_is_left_out(self):
        result = grading_result()
        expected = {k: v for k, v in grading_result().items() if k != "second_opinion"}
        self.assertEqual(grading_result_for_formatter(result), expected)

    def test_the_saved_result_itself_is_not_changed(self):
        result = grading_result()
        grading_result_for_formatter(result)
        self.assertEqual(result, grading_result())

    def test_a_value_that_is_not_a_dictionary_is_passed_as_it_is(self):
        for value in (None, "TEXT", ["a"]):
            self.assertEqual(grading_result_for_formatter(value), value)


class AutomaticFollowUpFormatterPromptTest(TransactionTestCase):
    """The follow-up after a grading. TransactionTestCase, because the
    dispatch waits for the grade's commit (see
    students/tests_grading_followup_dispatch.py)."""

    def setUp(self):
        self.teacher, self.submission = make_people_and_submission()

    @patch("students.services._formatted_grade_task")
    @patch("students.services.student_summary_async")
    @patch("students.services.ai_processor")
    def test_the_prompt_dispatched_after_a_grading(
        self, mock_ai, mock_summary, mock_formatted
    ):
        mock_ai.extract_grade_with_retry.return_value = grading_result()
        mock_formatted.return_value.delay.return_value.id = "fake-task-id"

        grade_engine(self.teacher, self.submission)

        # The saved result keeps its second opinion: it is the teacher's.
        self.submission.refresh_from_db()
        self.assertIn("second_opinion", self.submission.feedback)
        assert_prompt_has_the_result_without_the_second_opinion(
            self, dispatched_prompt(mock_formatted.return_value.delay)
        )


class TeacherRoutesFormatterPromptTest(APITestCase):
    def setUp(self):
        self.teacher, self.submission = make_people_and_submission(
            score=8,
            max_points=10,
            score_percentage=80,
            feedback=grading_result(),
        )
        from billing.models import CreditBucket, CreditBucketType, CreditWallet

        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.client.force_authenticate(user=self.teacher)

    @patch("students.views.formatted_grade_async")
    def test_the_prompt_dispatched_by_teacher_feedback(self, mock_formatted):
        mock_formatted.delay.return_value.id = "fake-task-id"
        response = self.client.get(
            reverse(
                "student-submission-teacher-feedback",
                kwargs={"pk": self.submission.pk},
            )
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assert_prompt_has_the_result_without_the_second_opinion(
            self, dispatched_prompt(mock_formatted.delay)
        )

    @patch("students.views.formatted_grade_async")
    def test_the_prompt_dispatched_by_update_grade(self, mock_formatted):
        mock_formatted.delay.return_value.id = "fake-task-id"
        response = self.client.patch(
            reverse(
                "student-submission-update-grade",
                kwargs={"pk": self.submission.pk},
            ),
            {"score": 7},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.submission.refresh_from_db()
        self.assertIn("second_opinion", self.submission.feedback)
        assert_prompt_has_the_result_without_the_second_opinion(
            self, dispatched_prompt(mock_formatted.delay)
        )
