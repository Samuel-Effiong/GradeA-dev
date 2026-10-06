"""
H-127: every route that shows a student their graded work returns the
student projection of the saved feedback, never the saved column itself.

The student's own submission endpoint has done so since the second-opinion
work (students/tests_student_feedback_scoping.py). Two other student routes
returned `submission.feedback` as stored once the grade was published:

  * the assignment detail a student opens (`performance_summary`,
    assignments/serializers.py);
  * the student dashboard's assignment list (`feedback`,
    dashboard/views.py).

The stored feedback holds what is written for the teacher: the second
grader's marks and reasons, which model graded, the review flags, the
rationale for the level chosen, the evidence quotes, the advice to the
teacher, and the text of a failed second opinion.

Each route is pinned to the EXACT projection, so a key added to the grading
result later stays hidden from students until someone decides otherwise.

Run with:
    python manage.py test students.tests_student_feedback_routes
"""

import ast
import json

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

#: Words that appear only in the parts of FULL_FEEDBACK a student must not
#: be sent. Looked for in the whole response body, so a leak under another
#: key is caught too.
TEACHER_ONLY_MARKERS = (
    "second_opinion",
    "second-grader-model",
    "first-grader-model",
    "SECOND GRADER RATIONALE",
    "INTERNAL NOTE ON LEVEL",
    "evidence_quotes",
    "flag_for_review",
    "graded_by",
    "snapped_from",
    "evaluation_rationale",
    "for_teacher",
    "ADVICE TO THE TEACHER",
    "follow_up_actions",
    "RAW SECOND OPINION ERROR",
    "grading_model",
    "A KEY NOBODY HAS CLASSIFIED",
)

FULL_FEEDBACK = {
    "grading_summary": {
        "total_score": 8,
        "max_total_points": 10,
        "percentage": 80.0,
        "confidence_note": "INTERNAL NOTE ON LEVEL (summary)",
    },
    "question_evaluations": [
        {
            "question_number": 1,
            "question_text": "Q1?",
            "question_type": "SHORT-ANSWER",
            "max_points": 10,
            "student_answer": "An answer.",
            "model_answer": "The model answer.",
            "evidence_quotes": ["An answer."],
            "score_awarded": 8,
            "level_achieved": "good",
            "evaluation_rationale": "INTERNAL NOTE ON LEVEL selection.",
            "strengths": ["Clear reasoning."],
            "weaknesses": ["Missing a detail."],
            "improvement_suggestions": ["Add the missing detail."],
            "feedback_for_student": "Solid answer overall.",
            "flag_for_review": "check the rubric",
            "graded_by": "first-grader-model",
            "snapped_from": 8.4,
        }
    ],
    "overall_performance_analysis": {
        "score_breakdown": "Student scored 8 out of 10 points (80.00%)",
    },
    "grading_confidence": 92,
    "grading_model": "first-grader-model",
    "recommendations": {
        "for_student": ["Review the missing detail."],
        "for_teacher": ["ADVICE TO THE TEACHER"],
        "follow_up_actions": ["Flag for a rubric review."],
    },
    "second_opinion": {
        "model": "second-grader-model",
        "error": "RAW SECOND OPINION ERROR: you only have 12 credits",
        "disagreements": [
            {
                "question_number": 1,
                "a": {"score_awarded": 8},
                "b": {
                    "score_awarded": 10,
                    "evaluation_rationale": "SECOND GRADER RATIONALE",
                },
            }
        ],
    },
    "a_future_block": "A KEY NOBODY HAS CLASSIFIED",
}

#: What a student is sent for FULL_FEEDBACK: the same shape their own
#: submission endpoint returns.
STUDENT_FEEDBACK = {
    "grading_summary": {
        "total_score": 8,
        "max_total_points": 10,
        "percentage": 80.0,
    },
    "question_evaluations": [
        {
            "question_number": 1,
            "question_text": "Q1?",
            "question_type": "SHORT-ANSWER",
            "max_points": 10,
            "student_answer": "An answer.",
            "score_awarded": 8,
            "level_achieved": "good",
            "strengths": ["Clear reasoning."],
            "weaknesses": ["Missing a detail."],
            "improvement_suggestions": ["Add the missing detail."],
            "feedback_for_student": "Solid answer overall.",
        }
    ],
    "overall_performance_analysis": {
        "score_breakdown": "Student scored 8 out of 10 points (80.00%)",
    },
    "recommendations": {"for_student": ["Review the missing detail."]},
}


def as_plain(value):
    """A response value as plain dicts and lists, for an exact comparison."""
    return json.loads(json.dumps(value, default=str))


class StudentFeedbackRoutesBase(APITestCase):
    def setUp(self):
        stamp = timezone.now().timestamp()
        self.teacher = CustomUser.objects.create_user(
            email=f"h127-teacher-{stamp}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.student = CustomUser.objects.create_user(
            email=f"h127-student-{stamp}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(
            name="C", teacher=self.teacher, session=session, is_active=True
        )
        StudentCourse.objects.create(
            student=self.student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.assignment = Assignment.objects.create(
            title="A",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )
        self.set_grade(is_published=True)
        self.client.force_authenticate(user=self.student)

    def set_grade(self, **changes):
        """A queryset update, as the scoping test does: no signals, and the
        row holds exactly what the test names."""
        fields = {
            "graded_at": timezone.now(),
            "score": 8,
            "max_points": 10,
            "score_percentage": 80.0,
            "feedback": FULL_FEEDBACK,
        }
        fields.update(changes)
        StudentSubmission.objects.filter(pk=self.submission.pk).update(**fields)

    def assert_nothing_for_the_teacher_in(self, response):
        body = json.dumps(as_plain(response.data))
        for marker in TEACHER_ONLY_MARKERS:
            self.assertNotIn(marker, body)


class AssignmentDetailPerformanceSummaryTest(StudentFeedbackRoutesBase):
    """GET /assignments/<id>/ as the student: `performance_summary`."""

    def get_summary(self):
        response = self.client.get(
            reverse("assignment-detail", kwargs={"pk": self.assignment.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response

    def test_a_published_grade_is_shown_as_the_student_projection(self):
        response = self.get_summary()
        self.assertEqual(
            as_plain(response.data["performance_summary"]), STUDENT_FEEDBACK
        )
        self.assert_nothing_for_the_teacher_in(response)

    def test_the_older_ai_feedback_column_is_projected_too(self):
        """The route falls back to `ai_feedback` when `feedback` is empty;
        that column holds the same kind of result."""
        self.set_grade(feedback=None, ai_feedback=FULL_FEEDBACK)
        response = self.get_summary()
        self.assertEqual(
            as_plain(response.data["performance_summary"]), STUDENT_FEEDBACK
        )
        self.assert_nothing_for_the_teacher_in(response)

    def test_an_unpublished_grade_shows_nothing(self):
        self.set_grade(is_published=False)
        response = self.get_summary()
        self.assertIsNone(response.data["performance_summary"])
        self.assert_nothing_for_the_teacher_in(response)


class StudentDashboardAssignmentsFeedbackTest(StudentFeedbackRoutesBase):
    """GET /student-admin/dashboard/assignments/: each row's `feedback`.

    That field is declared as text (dashboard/serializers.py), so the row
    carries the Python text form of the dictionary, as it always has. H-127
    changes what is in it, not its type."""

    def get_row(self):
        response = self.client.get(reverse("student-assignments"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = [
            row
            for row in response.data["results"]
            if str(row["assignment_id"]) == str(self.assignment.pk)
        ]
        self.assertEqual(len(rows), 1)
        return response, rows[0]

    def test_a_published_grade_is_shown_as_the_student_projection(self):
        response, row = self.get_row()
        self.assertIsInstance(row["feedback"], str)
        self.assertEqual(ast.literal_eval(row["feedback"]), STUDENT_FEEDBACK)
        self.assert_nothing_for_the_teacher_in(response)

    def test_an_unpublished_grade_shows_nothing(self):
        self.set_grade(is_published=False)
        response, row = self.get_row()
        self.assertIsNone(row["feedback"])
        self.assert_nothing_for_the_teacher_in(response)


class StudentSubmissionListReviewFieldsTest(StudentFeedbackRoutesBase):
    """GET /submissions/ as the student: the teacher's review-queue fields.

    `review_reasons` holds both AI graders' marks for each disputed
    question. The list hid `score` until release and returned these fields
    as stored, released or not."""

    REVIEW_STATE = {
        "needs_review": True,
        "review_reasons": [
            {
                "type": "grader_disagreement",
                "question_number": 1,
                "a_score": 8,
                "b_score": 10,
                "tier": "critical",
                "gap_fraction": 0.2,
            }
        ],
        "review_severity": 2.2,
        "review_tier": "critical",
        "grading_confidence": 92,
    }

    def get_row(self, user=None):
        if user is not None:
            self.client.force_authenticate(user=user)
        response = self.client.get(reverse("student-submission-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = [
            row
            for row in response.data["results"]
            if str(row["id"]) == str(self.submission.pk)
        ]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def assert_no_review_fields(self, row):
        self.assertIs(row["needs_review"], False)
        self.assertIsNone(row["review_reasons"])
        self.assertIsNone(row["review_severity"])
        self.assertIsNone(row["review_tier"])
        self.assertIsNone(row["grading_confidence"])
        body = json.dumps(as_plain(row))
        for marker in ("a_score", "b_score", "grader_disagreement", "critical"):
            self.assertNotIn(marker, body)

    def test_an_unpublished_grade_shows_a_student_no_review_field(self):
        self.set_grade(is_published=False, **self.REVIEW_STATE)
        row = self.get_row()
        self.assert_no_review_fields(row)
        self.assertIsNone(row["graded_at"])
        self.assertIsNone(row["score"])

    def test_a_published_grade_shows_a_student_no_review_field(self):
        self.set_grade(is_published=True, **self.REVIEW_STATE)
        row = self.get_row()
        self.assert_no_review_fields(row)
        # A student may know when a released grade was made.
        self.assertIsNotNone(row["graded_at"])
        self.assertEqual(float(row["score"]), 8.0)

    def test_the_teacher_still_sees_the_review_queue_fields(self):
        self.set_grade(is_published=False, **self.REVIEW_STATE)
        row = self.get_row(user=self.teacher)
        self.assertIs(row["needs_review"], True)
        self.assertEqual(
            as_plain(row["review_reasons"]), self.REVIEW_STATE["review_reasons"]
        )
        self.assertEqual(row["review_severity"], 2.2)
        self.assertEqual(row["review_tier"], "critical")
        self.assertEqual(row["grading_confidence"], 92)
        self.assertIsNotNone(row["graded_at"])


class StudentSubmissionListReviewFiltersTest(StudentFeedbackRoutesBase):
    """The list's review-queue filters and ordering are the teacher's too.

    Hiding the fields is not enough while a student can ask the list for
    `?needs_review=true` or `?review_tier=critical` and see whether their
    own row comes back."""

    def setUp(self):
        super().setUp()
        self.set_grade(
            is_published=False,
            needs_review=True,
            review_tier="critical",
            review_severity=2.2,
        )
        self.url = reverse("student-submission-list")

    def ids(self, response):
        return {str(row["id"]) for row in response.data["results"]}

    def test_a_student_cannot_filter_on_the_review_queue(self):
        for query in (
            {"needs_review": "true"},
            {"needs_review": "false"},
            {"review_tier": "critical"},
            {"review_tier": "moderate"},
            {"ordering": "-review_severity"},
            {"ordering": "student__first_name,review_severity"},
        ):
            with self.subTest(query=query):
                response = self.client.get(self.url, query)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assertNotIn(
                    str(self.submission.pk), json.dumps(as_plain(response.data))
                )

    def test_a_student_can_still_list_and_use_the_other_filters(self):
        for query in (
            {},
            {"assignment": str(self.assignment.pk)},
            {"ordering": "student__first_name"},
        ):
            with self.subTest(query=query):
                response = self.client.get(self.url, query)
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(self.ids(response), {str(self.submission.pk)})

    def test_the_teacher_can_still_filter_and_order_the_review_queue(self):
        self.client.force_authenticate(user=self.teacher)
        for query, expected in (
            ({"needs_review": "true"}, {str(self.submission.pk)}),
            ({"needs_review": "false"}, set()),
            ({"review_tier": "critical"}, {str(self.submission.pk)}),
            ({"ordering": "-review_severity"}, {str(self.submission.pk)}),
        ):
            with self.subTest(query=query):
                response = self.client.get(self.url, query)
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(self.ids(response), expected)


class StudentSafeFeedbackFunctionTest(StudentFeedbackRoutesBase):
    """The shared projection itself, on values the routes rarely hold."""

    def test_a_value_that_is_not_a_dictionary_is_shown_as_nothing(self):
        from students.feedback_projection import student_safe_feedback

        for stored in (None, "RAW TEXT", ["RAW", "LIST"], 7):
            self.assertIsNone(student_safe_feedback(stored))

    def test_the_result_never_shares_the_top_level_with_what_is_stored(self):
        from students.feedback_projection import student_safe_feedback

        shown = student_safe_feedback(FULL_FEEDBACK)
        self.assertEqual(shown, STUDENT_FEEDBACK)
        self.assertIsNot(shown, FULL_FEEDBACK)


#: The formatter's output (ai_processor/GRADE_FORMATTER_2.txt). Its prompt
#: asks for advice to the teacher and for every review flag to be surfaced
#: there; the words in capitals stand for those.
FULL_FORMATTED_GRADE = {
    "overall_performance_summary": {
        "score_statement": "You scored 8 out of 10 points.",
        "performance_narrative": "You did well.",
        "grade_tier_context": "A good grasp.",
        "an_unknown_summary_key": "UNCLASSIFIED SUMMARY TEXT",
    },
    "strengths": ["Question 1: clear reasoning."],
    "areas_for_improvement": ["Question 1: add the missing detail."],
    "question_by_question_breakdown": [
        {
            "question_number": 1,
            "question_text": "Q1?",
            "max_score": 10,
            "score_awarded": 8,
            "narrative": "The response covered the main point.",
            "feedback_for_student": "Solid answer overall.",
            "strengths": ["Clear reasoning."],
            "weaknesses": ["Missing a detail."],
            "an_unknown_question_key": "UNCLASSIFIED QUESTION TEXT",
        }
    ],
    "final_recommendations": {
        "for_student": ["Review the missing detail."],
        "for_teacher": ["FLAG FOR THE TEACHER: the graders disagreed on Q1"],
        "follow_up_actions": ["FOLLOW-UP ACTION: schedule a session"],
    },
    "an_unknown_section": "UNCLASSIFIED SECTION TEXT",
}

STUDENT_FORMATTED_GRADE = {
    "overall_performance_summary": {
        "score_statement": "You scored 8 out of 10 points.",
        "performance_narrative": "You did well.",
        "grade_tier_context": "A good grasp.",
    },
    "strengths": ["Question 1: clear reasoning."],
    "areas_for_improvement": ["Question 1: add the missing detail."],
    "question_by_question_breakdown": [
        {
            "question_number": 1,
            "question_text": "Q1?",
            "max_score": 10,
            "score_awarded": 8,
            "narrative": "The response covered the main point.",
            "feedback_for_student": "Solid answer overall.",
            "strengths": ["Clear reasoning."],
            "weaknesses": ["Missing a detail."],
        }
    ],
    "final_recommendations": {"for_student": ["Review the missing detail."]},
}

FORMATTED_TEACHER_ONLY_MARKERS = (
    "for_teacher",
    "FLAG FOR THE TEACHER",
    "follow_up_actions",
    "FOLLOW-UP ACTION",
    "UNCLASSIFIED",
    "an_unknown",
)


class StudentFormattedGradeTest(StudentFeedbackRoutesBase):
    """GET /submissions/<id>/ as the student: `formatted_grade`.

    The column is text. The formatting task assigns it a dictionary, which
    the database stores in Python's text form; a student is sent the
    projection in the same text form the row holds. One GET per test: the
    route caches its response per caller."""

    def get_formatted_grade(self, user=None):
        if user is not None:
            self.client.force_authenticate(user=user)
        response = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": self.submission.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response, response.data["formatted_grade"]

    def assert_nothing_for_the_teacher(self, response):
        body = json.dumps(as_plain(response.data))
        for marker in FORMATTED_TEACHER_ONLY_MARKERS:
            self.assertNotIn(marker, body)

    def test_a_released_grade_stored_in_python_text_form(self):
        self.set_grade(is_published=True, formatted_grade=str(FULL_FORMATTED_GRADE))
        response, shown = self.get_formatted_grade()
        self.assertIsInstance(shown, str)
        self.assertEqual(ast.literal_eval(shown), STUDENT_FORMATTED_GRADE)
        self.assert_nothing_for_the_teacher(response)

    def test_a_released_grade_stored_as_json_text(self):
        self.set_grade(
            is_published=True, formatted_grade=json.dumps(FULL_FORMATTED_GRADE)
        )
        response, shown = self.get_formatted_grade()
        self.assertIsInstance(shown, str)
        self.assertEqual(json.loads(shown), STUDENT_FORMATTED_GRADE)
        self.assert_nothing_for_the_teacher(response)

    def test_text_that_is_not_a_dictionary_is_shown_as_nothing(self):
        self.set_grade(
            is_published=True,
            formatted_grade="FLAG FOR THE TEACHER: free text, no sections",
        )
        response, shown = self.get_formatted_grade()
        self.assertIsNone(shown)
        self.assert_nothing_for_the_teacher(response)

    def test_a_list_is_shown_as_nothing(self):
        self.set_grade(is_published=True, formatted_grade=str(["FLAG FOR THE TEACHER"]))
        response, shown = self.get_formatted_grade()
        self.assertIsNone(shown)
        self.assert_nothing_for_the_teacher(response)

    def test_an_unreleased_grade_shows_nothing(self):
        self.set_grade(is_published=False, formatted_grade=str(FULL_FORMATTED_GRADE))
        response, shown = self.get_formatted_grade()
        self.assertIsNone(shown)
        self.assert_nothing_for_the_teacher(response)

    def test_no_formatted_grade_yet_shows_nothing(self):
        self.set_grade(is_published=True, formatted_grade=None)
        _, shown = self.get_formatted_grade()
        self.assertIsNone(shown)

    def test_the_teacher_still_reads_it_whole(self):
        self.set_grade(is_published=True, formatted_grade=str(FULL_FORMATTED_GRADE))
        _, shown = self.get_formatted_grade(user=self.teacher)
        self.assertEqual(ast.literal_eval(shown), FULL_FORMATTED_GRADE)
