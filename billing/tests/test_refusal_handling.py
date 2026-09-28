"""
Refusal handling: a PERMANENT refusal of an AI request is surfaced cleanly and
never retried. See docs/evidence/REFUSAL_HANDLING_EVIDENCE.md.

PERMANENT = AIFeatureNotAvailableError (403, "ai_feature_not_available") and
InsufficientCreditsError (402, "insufficient_credits"). Everything else is
TRANSIENT and keeps today's retry behaviour.

The refusals here are real: no test mocks a refusal into existence. A teacher
whose paid subscription Stripe cancelled (credits left in the wallet) is
refused by execute_graded_task's own access gate; a teacher whose plan grants
fewer credits than the task's estimate is refused by its own balance check; a
teacher with an empty wallet is refused by the HasCreditBalance permission;
the superadmin assistant is refused by its own kill switch. Only the model
provider is stubbed, and every test asserts it was never reached. The gate
itself is spied (wraps=) so a retry shows up as a second gate call.

Run with:
    python manage.py test billing.tests.test_refusal_handling --settings=settings_worktree
"""

import json
import logging
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.models import ChatMessage as DashboardChatMessage
from ai_processor.services import AIProcessor, ai_processor
from assignments.models import Assignment, AssignmentStatus
from assignments.tasks import (
    extract_answer_background_task,
    grade_engine_async,
    upload_answers_engine_async,
)
from assignments.tests_upload_batch_billing import png
from assignments.tests_upload_task_retry_policy import payload
from AutoGrader.error_messages import (
    describe_background_task_error,
    describe_user_error,
)
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import InsufficientCreditsError
from billing.models import (
    BillingInterval,
    CreditWallet,
    PlanCategory,
    PlanTier,
    SubscriptionPlan,
)
from billing.services import SubscriptionService
from billing.stripe_service import StripeWebhookHandler
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    StudentSubmission,
)
from students.task_tracking import mark_processing_task_failure
from users.models import CustomUser, UserTypes

# The contract. Literal on purpose: these are what clients see.
FORBIDDEN, FEATURE_CODE = status.HTTP_403_FORBIDDEN, "ai_feature_not_available"
PAYMENT_REQUIRED, CREDITS_CODE = (
    status.HTTP_402_PAYMENT_REQUIRED,
    "insufficient_credits",
)
GENERIC_CREDITS_MESSAGE = (
    "There aren't enough AI credits available for this. The credit wallet "
    "needs to be topped up before it can run."
)

# Fragments of every InsufficientCreditsError text on b744c9f that carry
# internal billing state. None may reach a client (Senior Manager ruling Q2).
CREDIT_DETAIL_FRAGMENTS = (
    "Task requires",
    "you only have",
    "Requested:",
    "Available:",
    "chargeback",
    "deficit",
    "reversed payment",
)

PROMPT_TEXT = "Question 1: What is 2 + 2?"


# ---------------------------------------------------------------- fixtures


def _user(tag, user_type, **extra):
    return CustomUser.objects.create_user(
        email=f"{tag}-{uuid.uuid4().hex[:8]}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=user_type,
        first_name=tag.replace("-", " ").title(),
        last_name="Refusal",
        is_active=True,
        **extra,
    )


def _plan(monthly_credits):
    return SubscriptionPlan.objects.create(
        name=f"refusal-{uuid.uuid4().hex[:8]}",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        interval=BillingInterval.MONTHLY,
        monthly_credits=monthly_credits,
        carry_over_percent=0,
        is_active=True,
    )


def unsubscribed_teacher(tag):
    """Never subscribed: an empty wallet (HasCreditBalance refuses), and the
    access gate answers "No active subscription"."""
    return _user(tag, UserTypes.TEACHER)


def cancelled_teacher(tag):
    """Trial -> paid through checkout, then Stripe's
    customer.subscription.deleted webhook: the subscription is inactive but
    the paid credits stay in the wallet. HasCreditBalance lets the request
    through; the access gate answers "No active subscription" ->
    AIFeatureNotAvailableError."""
    teacher = _user(tag, UserTypes.TEACHER)
    plan = _plan(monthly_credits=5_000_000)
    trial = SubscriptionService.activate_free_trial(teacher, plan)
    stripe_id = f"sub_refusal_{uuid.uuid4().hex[:12]}"
    SubscriptionService.finalize_trial_to_paid_conversion(trial, plan, stripe_id)
    StripeWebhookHandler.handle_subscription_deleted({"id": stripe_id})
    assert CreditWallet.objects.get(user=teacher).total_remaining_credits() > 0
    return teacher


def underfunded_teacher(tag):
    """Subscribed through the production activation path to a plan whose
    grant is below execute_graded_task's fixed ~20k estimate overhead: the
    balance check answers "Task requires ~N credits, but you only have M"
    -> InsufficientCreditsError."""
    teacher = _user(tag, UserTypes.TEACHER)
    SubscriptionService.activate_subscription(teacher, _plan(monthly_credits=1000))
    return teacher


REFUSED_TEACHERS = (
    ("feature", cancelled_teacher, AIFeatureNotAvailableError),
    ("credits", underfunded_teacher, InsufficientCreditsError),
)


def classroom(teacher):
    session = Session.objects.create(name="S", teacher=teacher)
    course = Course.objects.create(name="C", teacher=teacher, session=session)
    student = _user("student", UserTypes.STUDENT)
    StudentCourse.objects.create(
        student=student, course=course, enrollment_status=EnrollmentStatusType.ENROLLED
    )
    assignment = Assignment.objects.create(
        title="A",
        course=course,
        status=AssignmentStatus.PUBLISHED,
        questions=[{"question_number": 1, "question_text": "2 + 2?", "points": 10}],
    )
    return course, student, assignment


@contextmanager
def spied_gate():
    """Count execute_graded_task calls (the entitlement/balance gate) and
    fail loudly if anything ever reaches the model provider."""
    with patch.object(
        AIProcessor,
        "execute_graded_task",
        autospec=True,
        side_effect=AIProcessor.execute_graded_task,
    ) as gate, patch.object(
        AIProcessor,
        "_AIProcessor__ai_model",
        side_effect=AssertionError("a refused request reached the provider"),
    ) as provider:
        yield gate
        provider.assert_not_called()


class RefusalAssertions(unittest.TestCase):
    def assertCleanRefusalResponse(self, response, expected_status, expected_code):
        self.assertEqual(response.status_code, expected_status, response.content)
        self.assertEqual(response.data.get("code"), expected_code, response.data)
        self.assertTrue(response.data.get("error"), response.data)
        body = response.content.decode()
        self.assertNoCreditDetail(body)
        self.assertNotIn("<b>", body)
        # What the frontend shows is the rendered top-level message: exactly
        # the error text, never "1. Error: ... 2. Code: ...".
        self.assertEqual(json.loads(body)["message"], response.data["error"])
        if expected_code == CREDITS_CODE:
            self.assertEqual(response.data["error"], GENERIC_CREDITS_MESSAGE)

    def assertNoCreditDetail(self, text):
        for fragment in CREDIT_DETAIL_FRAGMENTS:
            self.assertNotIn(fragment, text or "")
        for fragment in ("Traceback", 'File "', "Error during AI model"):
            self.assertNotIn(fragment, text or "")


# ------------------------------------------------- D1: rewrap -> retried 3x


class D1ExtractionRewrapTest(RefusalAssertions, TestCase):
    """extract_assignment_image rewrapped every exception as a bare
    Exception, so extract_assignment_with_retry's refusal passthrough never
    matched: the refusal was retried 3x and lost its type."""

    def _raised(self, call):
        try:
            call()
        except Exception as exc:  # noqa: BLE001 - the type is what's asserted
            return exc
        self.fail("a refused extraction returned normally")

    def test_small_document_refusal_keeps_its_type_and_is_checked_once(self):
        for label, make_teacher, refusal in REFUSED_TEACHERS:
            with self.subTest(label):
                teacher = make_teacher(f"d1-{label}")
                with spied_gate() as gate:
                    raised = self._raised(
                        lambda teacher=teacher: ai_processor.extract_assignment_with_retry(
                            teacher, [{"type": "text", "text": PROMPT_TEXT}]
                        )
                    )
                self.assertEqual(gate.call_count, 1, "a permanent refusal was retried")
                self.assertIs(type(raised), refusal)

    def test_extract_assignment_text_path_keeps_the_refusal_type(self):
        for label, make_teacher, refusal in REFUSED_TEACHERS:
            with self.subTest(label):
                teacher = make_teacher(f"d1-text-{label}")
                with spied_gate() as gate:
                    raised = self._raised(
                        lambda teacher=teacher: ai_processor.extract_assignment(
                            teacher, PROMPT_TEXT
                        )
                    )
                self.assertEqual(gate.call_count, 1)
                self.assertIs(type(raised), refusal)


# --------------------------------------- D2 / D3: dashboard + billing chat


class D2D3AssistantChatRefusalTest(RefusalAssertions, APITestCase):
    """Every dashboard/analytics AI chat endpoint answered a refusal with 500."""

    def _post(self, url_name, user):
        self.client.force_authenticate(user=user)
        return self.client.post(
            reverse(url_name), {"prompt": "How are my classes doing?"}, format="json"
        )

    def test_teacher_chat_refusals_are_402_or_403_never_500(self):
        for label, make_teacher, refusal in REFUSED_TEACHERS:
            with self.subTest(label):
                teacher = make_teacher(f"d2-{label}")
                before = DashboardChatMessage.objects.count()
                with spied_gate() as gate:
                    response = self._post("teacher-admin-custom-ai-prompt", teacher)
                expected = (
                    (FORBIDDEN, FEATURE_CODE)
                    if refusal is AIFeatureNotAvailableError
                    else (PAYMENT_REQUIRED, CREDITS_CODE)
                )
                self.assertCleanRefusalResponse(response, *expected)
                self.assertEqual(gate.call_count, 1)
                # A refused turn leaves no orphaned chat message behind.
                self.assertEqual(DashboardChatMessage.objects.count(), before)

    def test_school_admin_chat_refusal_is_403(self):
        admin = _user("d2-admin", UserTypes.SCHOOL_ADMIN)
        with spied_gate() as gate:
            response = self._post("school-admin-custom-ai-prompt", admin)
        self.assertCleanRefusalResponse(response, FORBIDDEN, FEATURE_CODE)
        self.assertEqual(gate.call_count, 1)

    @override_settings(DASHBOARD_CUSTOM_AI_PROMPT_ENABLED=False)
    def test_superadmin_assistants_refused_by_kill_switch_are_403(self):
        superadmin = CustomUser.objects.create_superuser(
            email=f"d3-super-{uuid.uuid4().hex[:8]}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        for url_name in ("dashboard-custom-ai-prompt", "analytics-custom-ai-prompt"):
            with self.subTest(url_name):
                before = DashboardChatMessage.objects.count()
                with spied_gate():
                    response = self._post(url_name, superadmin)
                self.assertCleanRefusalResponse(response, FORBIDDEN, FEATURE_CODE)
                self.assertEqual(DashboardChatMessage.objects.count(), before)


# ------------------------------- D4: DRF handler turns uncaught refusal 500


class D4UncaughtRefusalTest(RefusalAssertions, APITestCase):
    """The synchronous assignment create/update views don't catch refusals;
    the project's DRF exception handler turned them into a bare 500."""

    def test_sync_assignment_create_refusal_is_402_or_403(self):
        for label, make_teacher, refusal in REFUSED_TEACHERS:
            with self.subTest(label):
                teacher = make_teacher(f"d4-create-{label}")
                course, _, _ = classroom(teacher)
                self.client.force_authenticate(user=teacher)
                with spied_gate() as gate:
                    response = self.client.post(
                        reverse("assignment-list"),
                        {
                            "title": "Refused",
                            "course": str(course.id),
                            "raw_input": PROMPT_TEXT,
                        },
                        format="json",
                    )
                expected = (
                    (FORBIDDEN, FEATURE_CODE)
                    if refusal is AIFeatureNotAvailableError
                    else (PAYMENT_REQUIRED, CREDITS_CODE)
                )
                self.assertCleanRefusalResponse(response, *expected)
                self.assertEqual(gate.call_count, 1)
                # @transaction.atomic on create: nothing half-created.
                self.assertFalse(
                    Assignment.objects.filter(course=course, title="Refused").exists()
                )


# ------------------------- D5: Celery tasks self.retry a permanent refusal


class D5TaskRetryTest(RefusalAssertions, TestCase):
    """UPLOAD_REFUSALS lacked both refusal types, so the two retrying answer
    tasks re-ran a refused extraction 3 more times before recording it."""

    def _tracked(self, requested_by, assignment, task_type, **extra):
        return BackgroundProcessingTask.objects.create(
            requested_by=requested_by,
            task_type=task_type,
            assignment=assignment,
            **extra,
        )

    def assertTerminalRefusal(self, tracked, outcome, gate, refusal):
        self.assertEqual(gate.call_count, 1, "a permanent refusal was retried")
        # A refusal is a handled, recorded outcome - not a task crash.
        self.assertFalse(outcome.failed(), repr(outcome.result))
        result = outcome.result
        self.assertEqual(result["status"], "FAILURE")
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertTrue(tracked.error)
        self.assertNotIn("Retrying", json.dumps(tracked.meta))
        self.assertNoCreditDetail(tracked.error)
        self.assertNoCreditDetail(result["message"])
        if refusal is InsufficientCreditsError:
            self.assertEqual(tracked.error, GENERIC_CREDITS_MESSAGE)

    def test_extract_answer_background_task_does_not_retry_a_refusal(self):
        for label, make_teacher, refusal in REFUSED_TEACHERS:
            with self.subTest(label):
                teacher = make_teacher(f"d5-edit-{label}")
                _, student, assignment = classroom(teacher)
                submission = StudentSubmission.objects.create(
                    assignment=assignment,
                    student=student,
                    answers=[{"question_number": 1, "answer_html": "original"}],
                    attempt_count=1,
                )
                tracked = self._tracked(
                    student,
                    assignment,
                    BackgroundTaskType.ANSWER_EXTRACTION,
                    submission=submission,
                )
                with spied_gate() as gate:
                    outcome = extract_answer_background_task.apply(
                        args=(str(submission.id), PROMPT_TEXT, str(student.id)),
                        kwargs={"processing_task_id": str(tracked.id)},
                    )
                self.assertTerminalRefusal(tracked, outcome, gate, refusal)

    def test_upload_answers_engine_async_does_not_retry_a_refusal(self):
        for label, make_teacher, refusal in REFUSED_TEACHERS:
            with self.subTest(label):
                teacher = make_teacher(f"d5-upload-{label}")
                _, student, assignment = classroom(teacher)
                tracked = self._tracked(
                    student, assignment, BackgroundTaskType.ANSWER_EXTRACTION
                )
                with spied_gate() as gate:
                    outcome = upload_answers_engine_async.apply(
                        args=(
                            str(assignment.id),
                            payload("answers.png", png(201), "image/png"),
                            "prompt",
                            str(student.id),
                        ),
                        kwargs={"processing_task_id": str(tracked.id)},
                    )
                self.assertTerminalRefusal(tracked, outcome, gate, refusal)


# ------------------------------------- D6: batch result records error=None


class D6BatchResultErrorTest(RefusalAssertions, TestCase):
    """grade_engine_async with a batch but no tracked task (the auto-grade
    and grade_batch_async fan-out) recorded the failure with error=None."""

    def test_refused_batch_grade_records_the_refusal_message(self):
        for label, make_teacher, refusal in REFUSED_TEACHERS:
            with self.subTest(label):
                teacher = make_teacher(f"d6-{label}")
                _, student, assignment = classroom(teacher)
                submission = StudentSubmission.objects.create(
                    assignment=assignment,
                    student=student,
                    answers=[{"question_number": 1, "answer_html": "4"}],
                    attempt_count=1,
                )
                session = BatchUploadSession.objects.create(
                    teacher=teacher,
                    course=assignment.course,
                    task_type=BatchUploadType.GRADE,
                    total_files=1,
                )
                with spied_gate():
                    outcome = grade_engine_async.apply(
                        args=(str(teacher.id), str(submission.id)),
                        kwargs={"batch_id": str(session.id)},
                    )
                self.assertTrue(outcome.failed())
                self.assertIsInstance(outcome.result, refusal)
                session.refresh_from_db()
                [entry] = session.results
                self.assertEqual(entry["status"], "FAILED")
                self.assertTrue(entry["error"], "batch failure recorded error=None")
                self.assertNoCreditDetail(entry["error"])


# ------------------------------ D7: weekly summaries log refusal as ERROR


class D7WeeklySummaryRefusalTest(RefusalAssertions, TestCase):
    """A refused narrative is an expected business outcome: the email still
    goes without it, the refusal is counted, and nothing logs at ERROR."""

    def _run_capturing(self, task):
        with patch("dashboard.tasks.send_email_task.delay") as send, self.assertLogs(
            "dashboard.tasks", level=logging.DEBUG
        ) as logs:
            logging.getLogger("dashboard.tasks").debug("capture start")
            with spied_gate():
                result = task.apply().get()
        return result, send, logs

    def assertRefusalNotAnError(self, logs):
        errors = [r for r in logs.records if r.levelno >= logging.ERROR]
        self.assertEqual(errors, [], [r.getMessage() for r in errors])
        self.assertTrue(
            any(r.levelno == logging.WARNING for r in logs.records),
            "the refusal was not logged at WARNING",
        )

    def test_course_summary_refusal_sends_email_counts_and_warns(self):
        from dashboard.tasks import send_weekly_course_summaries

        teacher = unsubscribed_teacher("d7-course")
        teacher.settings.notify_weekly_summary = True
        teacher.settings.save(update_fields=["notify_weekly_summary"])
        classroom(teacher)

        result, send, logs = self._run_capturing(send_weekly_course_summaries)

        send.assert_called_once()
        self.assertIn("AI narration refused for 1", result)
        self.assertRefusalNotAnError(logs)

    def test_school_admin_summary_refusal_sends_email_counts_and_warns(self):
        from classrooms.models import School
        from dashboard.tasks import send_weekly_school_admin_summaries

        school = School.objects.create(name="Refusal School")
        admin = _user("d7-admin", UserTypes.SCHOOL_ADMIN, school=school)
        admin.settings.notify_weekly_summary = True
        admin.settings.save(update_fields=["notify_weekly_summary"])

        result, send, logs = self._run_capturing(send_weekly_school_admin_summaries)

        send.assert_called_once()
        self.assertIn("AI narration refused for 1", result)
        self.assertRefusalNotAnError(logs)


# ------------------------- D8: task failure recorder logs refusal as ERROR


class D8TaskFailureLoggingTest(TestCase):
    def test_refusal_is_logged_at_warning_without_a_stack(self):
        for refusal in (
            AIFeatureNotAvailableError("AI access denied: No active subscription"),
            InsufficientCreditsError("Refill your wallet to continue"),
        ):
            with self.subTest(type(refusal).__name__):
                with self.assertLogs("students.task_tracking", level="DEBUG") as logs:
                    mark_processing_task_failure(str(uuid.uuid4()), refusal)
                [record] = logs.records
                self.assertEqual(record.levelno, logging.WARNING)
                self.assertIsNone(record.exc_info)

    def test_transient_failure_still_logs_error_with_stack(self):
        with self.assertLogs("students.task_tracking", level="DEBUG") as logs:
            mark_processing_task_failure(str(uuid.uuid4()), TimeoutError("slow"))
        [record] = logs.records
        self.assertEqual(record.levelno, logging.ERROR)
        self.assertIsNotNone(record.exc_info)


# ---------------------- D9: student submission views answer refusal 400


class D9StudentSubmissionRefusalStatusTest(RefusalAssertions, APITestCase):
    """FRONTEND-VISIBLE CHANGE (Senior Manager ruling Q1): was 400."""

    def test_sync_submission_edit_refusal_is_402_or_403(self):
        for label, make_teacher, refusal in REFUSED_TEACHERS:
            with self.subTest(label):
                teacher = make_teacher(f"d9-{label}")
                _, student, assignment = classroom(teacher)
                submission = StudentSubmission.objects.create(
                    assignment=assignment,
                    student=student,
                    answers=[{"question_number": 1, "answer_html": "original"}],
                    attempt_count=1,
                )
                self.client.force_authenticate(user=student)
                with spied_gate() as gate:
                    response = self.client.patch(
                        reverse(
                            "student-submission-detail", kwargs={"pk": submission.pk}
                        ),
                        {"raw_input": PROMPT_TEXT},
                        format="json",
                    )
                expected = (
                    (FORBIDDEN, FEATURE_CODE)
                    if refusal is AIFeatureNotAvailableError
                    else (PAYMENT_REQUIRED, CREDITS_CODE)
                )
                self.assertCleanRefusalResponse(response, *expected)
                self.assertEqual(gate.call_count, 1)
                submission.refresh_from_db()
                self.assertEqual(submission.answers[0]["answer_html"], "original")


# ------------------------ D10: credit refusal text never reaches a client


class D10CreditDetailNeverSurfacesTest(RefusalAssertions, TestCase):
    """Every InsufficientCreditsError text on b744c9f, verbatim."""

    ORIGINAL_TEXTS = (
        "Credit consumption is blocked on this account: a reversed payment left "
        "an unsettled deficit of 700 credits (500 from chargebacks, 200 from "
        "refunds).",
        "Insufficient credits. Requested: 25000, Available: 1000",
        "Task requires ~21000 credits, but you only have 1000 credits. "
        "Please refill your wallet to continue",
        "Refill your wallet to continue",
    )

    def test_every_describer_returns_the_generic_message(self):
        from AutoGrader.error_messages import describe_stripe_error

        for text in self.ORIGINAL_TEXTS:
            exc = InsufficientCreditsError(text)
            for describe in (
                describe_user_error,
                describe_background_task_error,
                describe_stripe_error,
            ):
                with self.subTest(describe=describe.__name__, text=text[:30]):
                    self.assertEqual(describe(exc), GENERIC_CREDITS_MESSAGE)

    def test_feature_refusal_text_still_passes_through(self):
        exc = AIFeatureNotAvailableError("AI access denied: No active subscription")
        self.assertEqual(describe_user_error(exc), str(exc))


# ------------- D11: HasCreditBalance answered 400 with HTML markup


class D11EmptyWalletPermissionTest(RefusalAssertions, APITestCase):
    """FRONTEND-VISIBLE CHANGE (Senior Manager ruling D11): every endpoint
    guarded by HasCreditBalance answered an empty wallet with a 400
    ParseError whose message was HTML ("<b>Insufficient Credits:</b> ...").
    Now 402 "insufficient_credits" with the generic message, before any
    work, dispatch or AI call happens."""

    def setUp(self):
        self.teacher = unsubscribed_teacher("d11-teacher")
        self.course, self.student, self.assignment = classroom(self.teacher)
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "original"}],
            attempt_count=1,
        )

    def endpoints(self):
        a, s, c = str(self.assignment.id), str(self.submission.id), str(self.course.id)
        teacher, student = self.teacher, self.student
        text = {"raw_input": PROMPT_TEXT}
        return [
            (teacher, "patch", reverse("assignment-detail", args=[a]), text),
            (teacher, "patch", reverse("assignment-update-async", args=[a]), text),
            (teacher, "post", reverse("assignment-upload-async"), {}),
            (teacher, "post", reverse("assignment-grade-all", args=[a]), {}),
            (
                teacher,
                "post",
                reverse("assignment-schedule-grade-all-submission", args=[a]),
                {},
            ),
            (teacher, "get", reverse("course-student-summary", args=[c]), None),
            (teacher, "post", reverse("student-submission-batch-upload", args=[a]), {}),
            (teacher, "post", reverse("student-submission-grade", args=[s]), {}),
            (teacher, "post", reverse("student-submission-grade-async", args=[s]), {}),
            (
                teacher,
                "post",
                reverse("student-submission-schedule-grade-async", args=[s]),
                {},
            ),
            (
                teacher,
                "get",
                reverse("student-submission-teacher-feedback", args=[s]),
                None,
            ),
            (
                teacher,
                "patch",
                reverse("student-submission-update-grade", args=[s]),
                {},
            ),
            (student, "post", reverse("student-submission-list"), {"assignment": a}),
            (
                student,
                "post",
                reverse("student-submission-upload-answers", args=[a]),
                {},
            ),
            (
                student,
                "post",
                reverse("student-submission-upload-async", args=[a]),
                {},
            ),
            (student, "put", reverse("student-submission-detail", args=[s]), text),
            (student, "patch", reverse("student-submission-detail", args=[s]), text),
            (
                student,
                "post",
                reverse("student-submission-update-async", args=[s]),
                text,
            ),
        ]

    def test_every_credit_guarded_endpoint_answers_402_with_a_code(self):
        endpoints = self.endpoints()
        self.assertEqual(len(endpoints), 18)
        for user, method, url, data in endpoints:
            with self.subTest(f"{user.user_type} {method.upper()} {url}"):
                self.client.force_authenticate(user=user)
                tasks_before = BackgroundProcessingTask.objects.count()
                with spied_gate() as gate:
                    response = getattr(self.client, method)(url, data, format="json")
                self.assertCleanRefusalResponse(
                    response, PAYMENT_REQUIRED, CREDITS_CODE
                )
                self.assertEqual(gate.call_count, 0, "work ran past the permission")
                self.assertEqual(BackgroundProcessingTask.objects.count(), tasks_before)

    def test_browsable_api_renders_the_refusal_instead_of_crashing(self):
        """An HTML request to a credit-guarded endpoint: the 402 page is
        rendered by BrowsableAPIRenderer, which re-runs the SAME action's
        permission check to decide which forms to draw and catches only
        APIException (rest_framework.renderers.show_form_for_method). That is
        why EmptyWalletError is an APIException: without it the refusal
        escapes the renderer and the user gets a 500 instead of the 402."""
        self.client.force_authenticate(user=self.student)
        response = self.client.patch(
            reverse("student-submission-detail", args=[str(self.submission.id)]),
            {"raw_input": PROMPT_TEXT},
            format="json",
            HTTP_ACCEPT="text/html",
        )
        self.assertEqual(response.status_code, PAYMENT_REQUIRED, response.content)
        self.assertIn("text/html", response["Content-Type"])
        response.render()
        self.assertNotIn("<b>Insufficient Credits", response.content.decode())
