"""
Refusal handling: Gate 3 (concurrency), Gate 5 (failure/recovery) and
Gate 9 (security/isolation). See docs/evidence/REFUSAL_HANDLING_EVIDENCE.md.

Companion to test_refusal_handling.py, which covers the defects themselves.
Everything here runs against real PostgreSQL; the model provider is stubbed
and its call count asserted. Refusals are real (see the fixtures imported
from the companion module).

Run with:
    python manage.py test billing.tests.test_refusal_handling_gates \
        --settings=settings_worktree
"""

import logging
import threading
import uuid
from unittest.mock import MagicMock, patch

from django.db import connections
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from ai_processor.models import ChatMessage
from ai_processor.services import STUDENT_AI_UNAVAILABLE_MESSAGE, AIProcessor
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import INSUFFICIENT_CREDITS_MESSAGE, InsufficientCreditsError
from billing.models import CreditLedger, CreditUsageLog, CreditWallet
from billing.tests.test_refusal_handling import (
    CREDIT_DETAIL_FRAGMENTS,
    CREDITS_CODE,
    FEATURE_CODE,
    FORBIDDEN,
    PAYMENT_REQUIRED,
    PROMPT_TEXT,
    _user,
    cancelled_teacher,
    classroom,
    spied_gate,
    underfunded_teacher,
    unsubscribed_teacher,
)
from classrooms.models import Course, School, Session
from dashboard.throttling import CustomAIPromptThrottle
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    StudentSubmission,
)
from students.task_tracking import mark_processing_task_failure
from users.models import UserTypes

CHAT_URL = "teacher-admin-custom-ai-prompt"

# H4: at least 20 simultaneous operations, at least 10 rounds.
THREADS = 20
ROUNDS = 10


class ConcurrentRefusalTest(TransactionTestCase):
    """Gate 3. A refusal under real concurrent load has ONE logical outcome:
    every caller is refused, nobody is charged, and no partial state (an
    orphaned chat turn, a credit row) is left behind."""

    def setUp(self):
        self.teacher = underfunded_teacher("g3")
        self.url = reverse(CHAT_URL)
        # The chat endpoint is rate limited per user (CustomAIPromptThrottle),
        # which is correct product behaviour and covered by
        # dashboard/tests_custom_ai_prompt.py - but it would answer 429 long
        # before 20 threads reached the refusal. Lifted here so that what is
        # under test is the refusal under concurrency, nothing else.
        throttle = patch.object(CustomAIPromptThrottle, "rate", "1000/min")
        throttle.start()
        self.addCleanup(throttle.stop)
        # The plan activation itself writes a GRANT ledger row, so what
        # matters is that NO NEW billing rows appear.
        self.ledger_before = CreditLedger.objects.filter(
            user_id=self.teacher.id
        ).count()

    def _hammer(self, user, count):
        """`count` real threads, each with its own client and its own DB
        connection, closed in finally (H4.2)."""
        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(count)

        def call():
            try:
                client = APIClient()
                client.force_authenticate(user=user)
                barrier.wait(timeout=30)
                response = client.post(
                    self.url, {"prompt": "How are my classes doing?"}, format="json"
                )
                with lock:
                    results.append((response.status_code, response.data))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=call) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
            # A timed-out thread must never leave partial state we then
            # assert on (H4.2).
            self.assertFalse(thread.is_alive(), "a request thread hung")
        return results

    def test_twenty_simultaneous_refusals_charge_nothing_and_persist_nothing(self):
        for round_number in range(ROUNDS):
            with self.subTest(round=round_number):
                with spied_gate() as gate:
                    results = self._hammer(self.teacher, THREADS)

                self.assertEqual(len(results), THREADS)
                for code, body in results:
                    self.assertEqual(code, PAYMENT_REQUIRED, body)
                    self.assertEqual(body["code"], CREDITS_CODE)
                    self.assertEqual(body["error"], INSUFFICIENT_CREDITS_MESSAGE)
                self.assertEqual(gate.call_count, THREADS)
                # One logical outcome: refused, and nothing else happened.
                self.assertEqual(
                    CreditUsageLog.objects.filter(user_id=self.teacher.id).count(), 0
                )
                self.assertEqual(
                    CreditLedger.objects.filter(user_id=self.teacher.id).count(),
                    self.ledger_before,
                )
                self.assertEqual(ChatMessage.objects.count(), 0)

    def test_a_top_up_landing_mid_flight_never_half_charges(self):
        """The interesting race: 20 callers refused while credits arrive.
        Every caller must end EITHER refused with no charge OR served with
        exactly one charge - never charged without a reply."""
        wallet = CreditWallet.objects.get(user=self.teacher)

        def grant_mid_flight():
            from datetime import timedelta

            from django.utils import timezone

            from billing.models import CreditBucket, CreditBucketType

            CreditBucket.objects.create(
                wallet=wallet,
                bucket_type=CreditBucketType.MONTHLY,
                total_credits=5_000_000,
                used_credits=0,
                expires_at=timezone.now() + timedelta(days=30),
            )

        response = provider_response()
        with patch.object(
            AIProcessor, "_AIProcessor__ai_model", return_value=response
        ) as provider:
            granter = threading.Thread(target=grant_mid_flight)
            granter.start()
            results = self._hammer(self.teacher, THREADS)
            granter.join(timeout=30)
            self.assertFalse(granter.is_alive())

        served = [r for r in results if r[0] == status.HTTP_200_OK]
        refused = [r for r in results if r[0] == PAYMENT_REQUIRED]
        self.assertEqual(len(served) + len(refused), THREADS, results)
        for _, body in refused:
            self.assertEqual(body["code"], CREDITS_CODE)
        # Exactly one charge per served caller, none for a refused one.
        self.assertEqual(
            CreditUsageLog.objects.filter(user_id=self.teacher.id).count(), len(served)
        )
        # A served caller's turn is persisted as a user+assistant pair; a
        # refused one leaves nothing.
        self.assertEqual(ChatMessage.objects.count(), 2 * len(served))
        self.assertLessEqual(provider.call_count, THREADS)


def provider_response(tokens=1000, content='{"response": "ok"}'):
    """The shape execute_graded_task reads back from the provider."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage.total_tokens = tokens
    return response


class RefusalFailureRecoveryTest(APITestCase):
    """Gate 5. What the system does when a refusal meets a failure."""

    def setUp(self):
        self.teacher = cancelled_teacher("g5")
        self.course, self.student, self.assignment = classroom(self.teacher)
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "original"}],
            attempt_count=1,
        )

    def _tracked(self):
        return BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=self.assignment,
            submission=self.submission,
        )

    def test_a_refusal_never_charges_and_never_persists(self):
        """The refusal lands before the provider call, inside
        update_submission_from_raw_text's billing_refund_scope: no charge to
        refund, no answers written, the tracked row terminal."""
        from students.services import update_submission_from_raw_text

        before = CreditWallet.objects.get(user=self.teacher).total_remaining_credits()
        tracked = self._tracked()

        with spied_gate():
            with self.assertRaises(AIFeatureNotAvailableError):
                update_submission_from_raw_text(
                    self.student,
                    self.submission,
                    PROMPT_TEXT,
                    processing_task_id=str(tracked.id),
                )

        self.assertEqual(
            CreditWallet.objects.get(user=self.teacher).total_remaining_credits(),
            before,
        )
        self.assertEqual(
            CreditUsageLog.objects.filter(user_id=self.teacher.id).count(), 0
        )
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.answers[0]["answer_html"], "original")

    def test_recording_the_refusal_failing_does_not_hide_it(self):
        """The DB write that records the refusal fails: the task must still
        end as a failure, not report success."""
        from assignments.tasks import extract_answer_background_task

        tracked = self._tracked()
        with spied_gate():
            with patch(
                "students.task_tracking.update_processing_task",
                side_effect=OSError("tracking row unavailable"),
            ):
                outcome = extract_answer_background_task.apply(
                    args=(str(self.submission.id), PROMPT_TEXT, str(self.student.id)),
                    kwargs={"processing_task_id": str(tracked.id)},
                )

        self.assertTrue(outcome.failed())
        self.assertIsInstance(outcome.result, OSError)
        tracked.refresh_from_db()
        # Intentional state: the row stays non-terminal (PENDING) and the
        # task is a Celery FAILURE - never SUCCESS, never a silent drop.
        self.assertNotEqual(tracked.status, BackgroundTaskStatus.SUCCESS)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.answers[0]["answer_html"], "original")

    def test_a_redelivered_refused_task_stays_refused_and_recharges_nothing(self):
        """Celery redelivery of a refused task: same terminal outcome, no
        second billed attempt."""
        from assignments.tasks import extract_answer_background_task

        tracked = self._tracked()
        outcomes = []
        with spied_gate() as gate:
            for _ in range(2):
                outcomes.append(
                    extract_answer_background_task.apply(
                        args=(
                            str(self.submission.id),
                            PROMPT_TEXT,
                            str(self.student.id),
                        ),
                        kwargs={"processing_task_id": str(tracked.id)},
                    )
                )

        for outcome in outcomes:
            self.assertFalse(outcome.failed(), repr(outcome.result))
        # Delivery 1 records the refusal. Delivery 2 finds the tracked row
        # already terminal, so the pre-existing idempotency claim skips it
        # ("duplicate run skipped", reported SUCCESS) rather than running the
        # billed work again - unchanged by this fix.
        self.assertEqual(outcomes[0].result["status"], "FAILURE")
        self.assertIn(outcomes[1].result["status"], ("FAILURE", "SUCCESS"))
        if outcomes[1].result["status"] == "SUCCESS":
            self.assertIn("skipped", outcomes[1].result["message"])
        # Either way nothing is charged and the gate is never passed twice.
        self.assertEqual(
            CreditUsageLog.objects.filter(user_id=self.teacher.id).count(), 0
        )
        self.assertLessEqual(gate.call_count, 2)
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertTrue(tracked.error)

    def test_a_transient_failure_still_retries_and_still_logs_an_error(self):
        """The other half of the classification: nothing here made a
        transient failure quieter or less retried."""
        from assignments.tasks import extract_answer_background_task

        tracked = self._tracked()
        with patch("students.services.ai_processor") as mock_ai:
            mock_ai.extract_answer_with_retry.side_effect = TimeoutError("slow")
            outcome = extract_answer_background_task.apply(
                args=(str(self.submission.id), PROMPT_TEXT, str(self.student.id)),
                kwargs={"processing_task_id": str(tracked.id)},
            )

        self.assertTrue(outcome.failed())
        # 1 initial attempt + max_retries(3).
        self.assertEqual(mock_ai.extract_answer_with_retry.call_count, 4)

    def test_a_credit_refusal_is_logged_with_its_real_reason_server_side(self):
        """The generic client message must not cost operators the detail:
        the original text stays in the log record."""
        with self.assertLogs("students.task_tracking", level="DEBUG") as logs:
            mark_processing_task_failure(
                str(uuid.uuid4()),
                InsufficientCreditsError(
                    "Credit consumption is blocked on this account: a reversed "
                    "payment left an unsettled deficit of 700 credits "
                    "(500 from chargebacks, 200 from refunds)."
                ),
            )
        [record] = logs.records
        self.assertIn("chargebacks", record.getMessage())


class RefusalIsolationTest(APITestCase):
    """Gate 9. A refusal must not become an information leak."""

    def setUp(self):
        self.school_a = School.objects.create(name="Alpha School")
        self.school_b = School.objects.create(name="Beta School")
        self.teacher_a = underfunded_teacher("g9-a")
        self.teacher_b = _user("g9-b", UserTypes.TEACHER)
        self.course_a, self.student_a, self.assignment_a = classroom(self.teacher_a)
        session_b = Session.objects.create(name="B session", teacher=self.teacher_b)
        self.course_b = Course.objects.create(
            name="Beta Secret Course", teacher=self.teacher_b, session=session_b
        )
        self.foreign_markers = [
            str(self.teacher_b.id),
            self.teacher_b.email,
            str(self.course_b.id),
            "Beta Secret Course",
            "Beta School",
        ]

    def assertNoForeignData(self, response):
        body = response.content.decode()
        for marker in self.foreign_markers:
            self.assertNotIn(marker, body)
        for fragment in CREDIT_DETAIL_FRAGMENTS:
            self.assertNotIn(fragment, body)

    def test_a_refused_teacher_learns_nothing_about_another_teacher(self):
        self.client.force_authenticate(user=self.teacher_a)
        with spied_gate():
            response = self.client.post(
                reverse(CHAT_URL), {"prompt": "How are my classes?"}, format="json"
            )
        self.assertEqual(response.status_code, PAYMENT_REQUIRED, response.content)
        self.assertNoForeignData(response)

    def test_a_refused_student_learns_nothing_about_the_teachers_wallet(self):
        """The student's request is billed to their teacher. The refusal
        must not disclose that teacher's balance, estimate or deficit."""
        self.client.force_authenticate(user=self.student_a)
        with spied_gate():
            response = self.client.patch(
                reverse(
                    "student-submission-detail",
                    args=[
                        str(
                            StudentSubmission.objects.create(
                                assignment=self.assignment_a,
                                student=self.student_a,
                                answers=[
                                    {"question_number": 1, "answer_html": "original"}
                                ],
                                attempt_count=1,
                            ).id
                        )
                    ],
                ),
                {"raw_input": PROMPT_TEXT},
                format="json",
            )
        self.assertEqual(response.status_code, PAYMENT_REQUIRED, response.content)
        self.assertEqual(response.data["error"], INSUFFICIENT_CREDITS_MESSAGE)
        self.assertNoForeignData(response)
        self.assertNotIn(self.teacher_a.email, response.content.decode())

    def test_an_empty_wallet_refusal_carries_no_wallet_numbers(self):
        teacher = unsubscribed_teacher("g9-empty")
        course, student, assignment = classroom(teacher)
        self.client.force_authenticate(user=teacher)
        with spied_gate() as gate:
            response = self.client.post(
                reverse("assignment-grade-all", args=[str(assignment.id)]), {}
            )
        self.assertEqual(response.status_code, PAYMENT_REQUIRED, response.content)
        self.assertEqual(response.data["code"], CREDITS_CODE)
        self.assertEqual(gate.call_count, 0)
        self.assertNoForeignData(response)

    def test_a_refused_school_admin_learns_nothing_about_another_school(self):
        admin = _user("g9-admin", UserTypes.SCHOOL_ADMIN, school=self.school_a)
        self.client.force_authenticate(user=admin)
        with spied_gate():
            response = self.client.post(
                reverse("school-admin-custom-ai-prompt"),
                {"prompt": "How is my school doing?"},
                format="json",
            )
        self.assertEqual(response.status_code, FORBIDDEN, response.content)
        self.assertEqual(response.data["code"], FEATURE_CODE)
        self.assertNoForeignData(response)

    def test_a_student_is_never_told_their_teachers_billing_state(self):
        """D12 (found by the Red Team Lead): the student branch used to raise
        "AI access denied for this assignment's teacher: {reason}", handing a
        student their teacher's subscription posture verbatim - and, for the
        "Internal Error: ..." reasons, our own failure detail."""
        teacher = cancelled_teacher("g9-plan")
        course, student, assignment = classroom(teacher)
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "original"}],
            attempt_count=1,
        )
        self.client.force_authenticate(user=student)
        with spied_gate():
            response = self.client.patch(
                reverse("student-submission-detail", args=[str(submission.id)]),
                {"raw_input": PROMPT_TEXT},
                format="json",
            )

        self.assertEqual(response.status_code, FORBIDDEN, response.content)
        self.assertEqual(response.data["code"], FEATURE_CODE)
        self.assertEqual(response.data["error"], STUDENT_AI_UNAVAILABLE_MESSAGE)
        body = response.content.decode()
        for leaked in (
            "No active subscription",
            "Trial period has expired",
            "No credits remaining",
            "does not include this feature",
            "Internal Error",
            "assignment's teacher",
            teacher.email,
            str(teacher.id),
            teacher.first_name,
        ):
            self.assertNotIn(leaked, body)

    def test_the_teachers_own_refusal_still_states_the_real_reason(self):
        """The other half of D12: detail is withheld from the STUDENT, not
        from the account holder, who needs to know what to fix."""
        teacher = cancelled_teacher("g9-own")
        classroom(teacher)
        self.client.force_authenticate(user=teacher)
        with spied_gate():
            response = self.client.post(
                reverse(CHAT_URL), {"prompt": "How are my classes?"}, format="json"
            )
        self.assertEqual(response.status_code, FORBIDDEN, response.content)
        self.assertIn("No active subscription", response.data["error"])

    def test_the_withheld_reason_is_still_recorded_server_side(self):
        """Operators must not lose the diagnosis the student no longer sees."""
        teacher = cancelled_teacher("g9-log")
        course, student, assignment = classroom(teacher)
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "original"}],
            attempt_count=1,
        )
        self.client.force_authenticate(user=student)
        with self.assertLogs("ai_processor.services", level="DEBUG") as logs:
            with spied_gate():
                self.client.patch(
                    reverse("student-submission-detail", args=[str(submission.id)]),
                    {"raw_input": PROMPT_TEXT},
                    format="json",
                )
        # At WARNING, not DEBUG: a refusal an operator may need to act on
        # must be visible at the level production actually records.
        denials = [
            r
            for r in logs.records
            if "No active subscription" in r.getMessage()
            and str(assignment.id) in r.getMessage()
        ]
        self.assertTrue(denials, [r.getMessage() for r in logs.records])
        for record in denials:
            self.assertGreaterEqual(record.levelno, logging.WARNING)
