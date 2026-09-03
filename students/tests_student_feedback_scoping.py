"""
Regression coverage: a student reading their own published submission must
never see the second-opinion block.

StudentSubmissionDetailStudentVersionSerializer.get_feedback used to return
`obj.feedback` verbatim once a submission was published. That blob can
carry `feedback["second_opinion"]` — a second grader's dissenting score
and rationale, generated for the teacher's review queue. A student who saw
it could read "the other grader would have given me full marks" and use it
to dispute a grade the teacher never saw disputed.

The fix (students/serializers.py::_student_safe_feedback) builds an
explicit whitelist projection of the feedback blob instead of returning it
raw — second_opinion, flag_for_review, graded_by provenance, and
evaluation_rationale (a teacher-directed note) are all stripped by
construction, since the whitelist only copies known-safe keys.

Run with:
    python manage.py test students.tests_student_feedback_scoping
"""

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, Session
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes


class StudentFeedbackScopingTest(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email=f"feedback-teacher-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.student = CustomUser.objects.create_user(
            email=f"feedback-student-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(name="C", teacher=self.teacher, session=session)
        self.assignment = Assignment.objects.create(
            title="A",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        self.full_feedback = {
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
                    "model_answer": "The model answer.",
                    "evidence_quotes": ["An answer."],
                    "score_awarded": 8,
                    "level_achieved": "good",
                    "evaluation_rationale": "Internal note on level selection.",
                    "strengths": ["Clear reasoning."],
                    "weaknesses": ["Missing a detail."],
                    "improvement_suggestions": ["Add the missing detail."],
                    "feedback_for_student": "Solid answer overall.",
                    "flag_for_review": None,
                    "graded_by": "x-ai/grok-4.3",
                    "snapped_from": 8.4,
                }
            ],
            "overall_performance_analysis": {
                "score_breakdown": "Student scored 8 out of 10 points (80.00%)",
                "strengths_summary": {
                    "overall_strengths": ["Good grasp of the topic."]
                },
                "areas_for_improvement": {"overall_weaknesses": ["Needs more detail."]},
            },
            "grading_confidence": 92,
            "recommendations": {
                "for_student": ["Review the missing detail."],
                "for_teacher": ["Consider clarifying the rubric wording."],
                "follow_up_actions": ["Flag for a rubric review."],
            },
            "second_opinion": {
                "ran": True,
                "model": "deepseek/deepseek-v4-pro",
                "disagreements": [
                    {
                        "question_number": 1,
                        "a": {"score_awarded": 8, "evaluation_rationale": "..."},
                        "b": {"score_awarded": 10, "evaluation_rationale": "..."},
                        "severity": {"tier": "moderate"},
                    }
                ],
            },
        }
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            graded_at=timezone.now(),
            score=8,
            max_points=10,
            score_percentage=80.0,
            is_published=True,
            feedback=self.full_feedback,
        )
        self.url = reverse(
            "student-submission-detail", kwargs={"pk": self.submission.pk}
        )

    def test_second_opinion_is_never_visible_to_the_student(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        feedback = response.data["feedback"]
        self.assertNotIn("second_opinion", feedback)
        self.assertNotIn(
            "second_opinion", str(feedback), "leaked outside the top-level key too"
        )

    def test_internal_and_teacher_only_fields_are_stripped(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)

        feedback = response.data["feedback"]
        evaluation = feedback["question_evaluations"][0]
        self.assertNotIn("flag_for_review", evaluation)
        self.assertNotIn("graded_by", evaluation)
        self.assertNotIn("snapped_from", evaluation)
        self.assertNotIn("evaluation_rationale", evaluation)
        self.assertNotIn("grading_confidence", feedback)
        self.assertNotIn("for_teacher", feedback.get("recommendations", {}))
        self.assertNotIn("follow_up_actions", feedback.get("recommendations", {}))

    def test_student_safe_fields_still_reach_the_student(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)

        feedback = response.data["feedback"]
        self.assertEqual(feedback["grading_summary"]["total_score"], 8)
        evaluation = feedback["question_evaluations"][0]
        self.assertEqual(evaluation["score_awarded"], 8)
        self.assertEqual(evaluation["level_achieved"], "good")
        self.assertEqual(evaluation["feedback_for_student"], "Solid answer overall.")
        self.assertEqual(
            feedback["recommendations"]["for_student"], ["Review the missing detail."]
        )

    def test_teacher_view_is_unaffected_and_still_sees_second_opinion(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("second_opinion", response.data)
        self.assertTrue(response.data["second_opinion"]["ran"])
        # The full per-question breakdown is teacher-only, same reasoning
        # as second_opinion: it carries evaluation_rationale/graded_by/
        # snapped_from, none of which are in the student-safe whitelist.
        [entry] = response.data["question_breakdown"]
        self.assertEqual(
            entry["evaluation_rationale"], "Internal note on level selection."
        )
        self.assertEqual(entry["graded_by"], "x-ai/grok-4.3")

    def test_question_breakdown_is_never_visible_to_the_student(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("question_breakdown", response.data)

    def test_unpublished_submission_still_returns_no_feedback_at_all(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            is_published=False
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)

        self.assertIsNone(response.data["feedback"])
