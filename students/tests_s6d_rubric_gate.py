"""
Epic A S6d, FR-A-06 #7 RUBRIC_MISSING (08a §1, §5; F5 in §6.0).

The founder's definition (F5): the assignment has no questions, or any
question has no marking guide at all. A marking guide is a rubric with at
least one level, or a model answer (an objective question's answer key is
its model answer). A one-level rubric is a marking guide: not refused.

Every grading entry point refuses before any work: no AI call, no ledger
row, no grading claim, nothing queued or scheduled.

  H1 POST submissions/<pk>/grade                 409, sync
  H2 POST submissions/<pk>/grade-async           409, nothing queued
  H3 POST submissions/<pk>/schedule-grade-async  409, nothing scheduled
  H4 POST assignments/<pk>/grade-all             409, nothing queued
  H5 POST assignments/<pk>/schedule_grade_all_submission  409, nothing scheduled
  T1 auto_grade_due_assignment (beat)            refused when it runs
  T2 grade_batch_async (a scheduled batch)       refused when it runs
  T3 grade_engine_async (a scheduled or queued single grade)  refused when it runs

T1-T3 are the time-of-use checks: the rubric was there when grading was
scheduled and is gone when it runs.
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_celery_beat.models import PeriodicTask
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from assignments.tests_cache_matrix_g3 import make_active_user, question
from audit.enums import AuditAction, ErrorClass, ReasonCode
from audit.models import AuditEvent
from audit.tests_state_change import LOCMEM_CACHE
from billing.models import CreditLedger
from billing.tests.test_h38_part2_removed_teacher_routes import fund_wallet
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.exceptions import RubricMissingError
from students.grading_gates import rubric_missing
from students.models import BatchUploadSession, GradingState, StudentSubmission
from users.models import UserTypes

AI = "ai_processor.services.AIProcessor._AIProcessor__ai_model"
DISPATCH = "celery.app.task.Task.apply_async"

NO_GUIDE = {
    "question_number": 1,
    "question_text": "Q1",
    "question_type": "ESSAY",
    "points": 10,
    "rubric": [],
    "model_answer": "  ",
}
ONE_LEVEL_RUBRIC = {
    "question_number": 1,
    "question_text": "Q1",
    "question_type": "ESSAY",
    "points": 10,
    "rubric": [{"level": "Full", "description": "Complete", "points": 10}],
    "model_answer": "",
}


class RubricMissingDefinitionTests(SimpleTestCase):
    """F5, exactly as the founder set it."""

    def test_no_questions_is_missing(self):
        for questions in (None, [], (), "Q1", {"question_number": 1}):
            with self.subTest(questions=questions):
                self.assertTrue(rubric_missing(questions))

    def test_a_question_with_no_marking_guide_is_missing(self):
        for q in (
            NO_GUIDE,
            {**NO_GUIDE, "rubric": None, "model_answer": None},
            {"question_number": 1, "question_text": "Q1"},
            {**NO_GUIDE, "rubric": ["not a level"]},
            "a bare string",
        ):
            with self.subTest(question=q):
                self.assertTrue(rubric_missing([q]))

    def test_one_question_without_a_guide_among_many_is_missing(self):
        self.assertTrue(
            rubric_missing([question(1), {**NO_GUIDE, "question_number": 2}])
        )

    def test_a_one_level_rubric_is_not_missing(self):
        self.assertFalse(rubric_missing([ONE_LEVEL_RUBRIC]))

    def test_a_model_answer_alone_is_a_marking_guide(self):
        """An objective question's answer key is its model answer; without
        this every answer-key question would be refused."""
        self.assertFalse(rubric_missing([question()]))
        self.assertFalse(
            rubric_missing([{**NO_GUIDE, "model_answer": "Photosynthesis"}])
        )


@override_settings(CACHES=LOCMEM_CACHE)
class RubricGateTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.teacher = make_active_user("s6d-t@rubric.test", UserTypes.TEACHER)
        fund_wallet(self.teacher)
        self.student = make_active_user(
            "s6d-s@rubric.test", UserTypes.STUDENT, first_name="Sixd"
        )
        session = Session.objects.create(name="S6d", teacher=self.teacher)
        self.course = Course.objects.create(
            name="S6d", teacher=self.teacher, session=session
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(user=self.teacher)

    def world(self, questions, **assignment_fields):
        assignment = Assignment.objects.create(
            title="S6d",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=questions,
            **assignment_fields,
        )
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "x"}],
        )
        return assignment, submission

    def assert_nothing_happened(self, submission, ledger_before):
        submission.refresh_from_db()
        self.assertIsNone(submission.graded_at)
        self.assertEqual(submission.grading_state, GradingState.IDLE, "claimed")
        self.assertEqual(CreditLedger.objects.count(), ledger_before)


class RubricGateRouteTests(RubricGateTestCase):
    def routes(self, assignment, submission):
        later = (timezone.now() + timedelta(hours=1)).isoformat()
        return {
            "H1 grade": (
                reverse("student-submission-grade", args=[submission.pk]),
                {},
            ),
            "H2 grade-async": (
                reverse("student-submission-grade-async", args=[submission.pk]),
                {},
            ),
            "H3 schedule-grade-async": (
                reverse(
                    "student-submission-schedule-grade-async", args=[submission.pk]
                ),
                {"schedule_time": later},
            ),
            "H4 grade-all": (
                reverse("assignment-grade-all", args=[assignment.pk]),
                {},
            ),
            "H5 schedule-grade-all": (
                reverse(
                    "assignment-schedule-grade-all-submission", args=[assignment.pk]
                ),
                {"schedule_time": later},
            ),
        }

    def assert_refused_everywhere(self, questions, case):
        assignment, submission = self.world(questions)
        ledger = CreditLedger.objects.count()
        for route, (url, body) in self.routes(assignment, submission).items():
            with self.subTest(case=case, route=route), patch(AI) as ai, patch(
                DISPATCH
            ) as dispatch:
                response = self.client.post(url, body, format="json")

                self.assertEqual(response.status_code, 409, response.content)
                envelope = response.json()["error"]["field_errors"]
                self.assertEqual(envelope["reason_code"], "RUBRIC_MISSING")
                self.assertEqual(envelope["error_class"], ErrorClass.VALIDATION)
                self.assertIn("No credits were used", response.json()["message"])
                ai.assert_not_called()
                dispatch.assert_not_called()
                self.assertFalse(PeriodicTask.objects.exists(), "scheduled")
                self.assert_nothing_happened(submission, ledger)

    def test_r1_no_questions(self):
        self.assert_refused_everywhere(None, "R1 None")

    def test_r2_empty_questions(self):
        self.assert_refused_everywhere([], "R2 []")

    def test_r3_a_question_with_no_marking_guide(self):
        self.assert_refused_everywhere(
            [question(1), {**NO_GUIDE, "question_number": 2}], "R3"
        )

    def test_r4_a_one_level_rubric_is_accepted(self):
        assignment, submission = self.world([ONE_LEVEL_RUBRIC])
        with patch(DISPATCH):
            response = self.client.post(
                reverse("student-submission-grade-async", args=[submission.pk]),
                {},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.content)


class RubricGateAtRunTimeTests(RubricGateTestCase):
    """The rubric was there when grading was scheduled, and is gone when it
    runs: refused then, with nothing dispatched, claimed or charged."""

    def refusal_events(self, submission):
        return AuditEvent.objects.filter(
            action=AuditAction.GRADING_FAILED,
            target_id=submission.id,
            reason_code=ReasonCode.RUBRIC_MISSING,
            error_class=ErrorClass.VALIDATION,
        )

    def remove_the_rubric(self, assignment):
        Assignment.objects.filter(pk=assignment.pk).update(questions=[])

    def test_t1_auto_grade_refuses_when_it_runs(self):
        from assignments.tasks import auto_grade_due_assignment

        assignment, submission = self.world([question()], auto_grade_on_due_date=True)
        self.remove_the_rubric(assignment)
        ledger = CreditLedger.objects.count()

        with patch(AI) as ai, patch(
            "assignments.tasks.grade_engine_async.delay"
        ) as dispatch:
            result = auto_grade_due_assignment(str(assignment.pk))

        self.assertEqual(result, "Refused: RUBRIC_MISSING")
        ai.assert_not_called()
        dispatch.assert_not_called()
        self.assert_nothing_happened(submission, ledger)
        [session] = BatchUploadSession.objects.filter(course=self.course)
        [entry] = session.results
        self.assertEqual(entry["status"], "FAILED")
        self.assertEqual(entry["error"], str(RubricMissingError()))
        self.assertEqual(self.refusal_events(submission).count(), 1)

    def test_t2_a_scheduled_batch_refuses_when_it_runs(self):
        from assignments.tasks import grade_batch_async

        assignment, submission = self.world([question()])
        self.remove_the_rubric(assignment)
        ledger = CreditLedger.objects.count()

        with patch(AI) as ai, patch(
            "assignments.tasks.grade_engine_async.delay"
        ) as dispatch:
            result = grade_batch_async.run(str(self.teacher.id), str(assignment.pk))

        self.assertEqual(result, "Refused: RUBRIC_MISSING")
        ai.assert_not_called()
        dispatch.assert_not_called()
        self.assert_nothing_happened(submission, ledger)
        [session] = BatchUploadSession.objects.filter(course=self.course)
        self.assertEqual([e["status"] for e in session.results], ["FAILED"])
        self.assertEqual(self.refusal_events(submission).count(), 1)

    def test_t3_a_scheduled_single_grade_refuses_when_it_runs(self):
        """schedule-grade-async fires grade_engine_async; grade_engine
        refuses before the claim."""
        from assignments.tasks import grade_engine_async

        assignment, submission = self.world([question()])
        self.remove_the_rubric(assignment)
        ledger = CreditLedger.objects.count()

        with patch(AI) as ai:
            outcome = grade_engine_async.apply(
                args=(str(self.teacher.id), str(submission.id))
            )

        self.assertTrue(outcome.failed())
        # The real error, not Celery's UnpickleableExceptionWrapper.
        self.assertIsInstance(outcome.result, RubricMissingError)
        ai.assert_not_called()
        self.assert_nothing_happened(submission, ledger)
        self.assertEqual(self.refusal_events(submission).count(), 1)
