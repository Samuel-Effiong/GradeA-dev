"""
H-180: an upload the teacher's wallet cannot pay for is refused at the door.

Until now the three upload-async routes (a student's answer file, a teacher's
batch of answer files, a teacher's assignment files) were guarded only by
HasCreditBalance, which refuses a wallet at 0 or below. The queued task then
met the billing gate in AIProcessor.execute_graded_task, which refuses a
balance below the call's ESTIMATE (the file's tokens plus a flat 20,000), as
a final refusal: the student was told "Answer Extraction Started" and their
file was gone.

The door asks the SAME method the gate asks (AIProcessor.
estimate_messages_cost) about the same content (what the task will send for
the file), against the balance the gate reads (the wallet's
total_remaining_credits). The door's number is a lower bound of the task's (the
task adds its system prompt, the questions and the roster), so the door only
turns away an upload the task would certainly refuse. The gate stays the
authority.

Run with:
    python manage.py test students.tests_upload_credit_door
"""

import base64
import io
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.services import AIProcessor
from assignments.models import Assignment, AssignmentStatus
from assignments.tasks import upload_answers_engine_async
from assignments.upload_door import upload_refusal_if_unaffordable
from billing.errors import INSUFFICIENT_CREDITS_MESSAGE, InsufficientCreditsError
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from billing.tests.test_execute_graded_task import ExecuteGradedTaskTestBase
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
)
from users.models import CustomUser, UserTypes

# The sentence a STUDENT reads, written out here on purpose: the code's constant
# may not drift from what the Senior Manager ruled (2026-10-07).
STUDENT_SENTENCE = (
    "Your answers were not submitted. Your teacher's account can't process "
    "uploads right now. Please keep your file and try again later, or let "
    "your teacher know."
)
# What the gate's own refusal text looks like: it carries the balance and the
# estimate, which no student may read.
GATE_TEXT = (
    "Task requires ~25000 credits, but you only have 5000 credits. "
    "Please refill your wallet to continue"
)
# A wallet that passes HasCreditBalance (> 0) and is far below the flat 20,000
# of any estimate; and one far above any small file's estimate.
# The name of the one method both the door and the gate ask. Named by a variable so
# this file loads (and is type-checked) before the method exists.
SHARED_ESTIMATE = "estimate_messages_cost"
POOR = 5_000
RICH = 100_000


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _png()


def _user(tag, user_type):
    return CustomUser.objects.create_user(
        email=f"{tag}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=user_type,
        first_name=tag.replace("-", " ").title(),
        last_name="Door",
        is_active=True,
        email_verified_at=timezone.now(),
    )


def _fund(teacher, credits):
    wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=credits,
        used_credits=0,
        expires_at=timezone.now() + timedelta(days=30),
    )


def _classroom(tag, credits):
    """A teacher with a wallet of `credits`, an enrolled student, a published
    assignment. Each test builds its own: the route refuses a second queued
    extraction of the same student and assignment."""
    teacher = _user(f"{tag}-teacher", UserTypes.TEACHER)
    if credits:
        _fund(teacher, credits)
    student = _user(f"{tag}-student", UserTypes.STUDENT)
    session = Session.objects.create(name="S", teacher=teacher)
    course = Course.objects.create(name="C", teacher=teacher, session=session)
    StudentCourse.objects.create(
        student=student, course=course, enrollment_status=EnrollmentStatusType.ENROLLED
    )
    assignment = Assignment.objects.create(
        title="A",
        course=course,
        status=AssignmentStatus.PUBLISHED,
        questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
    )
    return teacher, student, course, assignment


def _file(name="a.png", data=PNG, content_type="image/png"):
    return SimpleUploadedFile(name, data, content_type=content_type)


class _Spy:
    """The estimates the door (or the gate) was given back, in order, by the
    real method."""

    def __init__(self):
        self.values = []
        real = getattr(AIProcessor, SHARED_ESTIMATE)
        spy = self

        def wrapped(processor, *args, **kwargs):
            value = real(processor, *args, **kwargs)
            spy.values.append(value)
            return value

        self.patcher = patch.object(AIProcessor, "estimate_messages_cost", wrapped)

    def __enter__(self):
        self.patcher.start()
        return self

    def __exit__(self, *exc):
        self.patcher.stop()


class StudentUploadDoorTest(APITestCase):
    def _upload(self, student, assignment, **file_kwargs):
        self.client.force_authenticate(user=student)
        url = reverse(
            "student-submission-upload-async", kwargs={"assignment_id": assignment.pk}
        )
        return self.client.post(
            url, {"answer": _file(**file_kwargs)}, format="multipart"
        )

    @patch("students.views.launch_processing_task")
    def test_a_wallet_below_the_estimate_is_refused_before_anything_is_queued(
        self, launch
    ):
        _, student, _, assignment = _classroom("poor", POOR)

        response = self._upload(student, assignment)

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(response.data["code"], "insufficient_credits")
        self.assertEqual(response.data["error"], STUDENT_SENTENCE)
        launch.assert_not_called()
        self.assertEqual(BackgroundProcessingTask.objects.count(), 0)

    @patch("students.views.launch_processing_task")
    def test_the_sentence_a_student_reads_names_no_money(self, launch):
        _, student, _, assignment = _classroom("words", POOR)

        response = self._upload(student, assignment)

        message = response.data["error"]
        self.assertTrue(message)
        for word in ("credit", "wallet", "balance", "refill", "top up", "5000"):
            self.assertNotIn(word, message.lower())
        self.assertEqual(sorted(response.data), ["code", "error"])

    @patch("students.views.launch_processing_task")
    def test_a_wallet_above_the_estimate_is_queued_as_before(self, launch):
        _, student, _, assignment = _classroom("rich", RICH)
        launch.return_value.id = "task-1"

        response = self._upload(student, assignment)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["task_id"], "task-1")
        launch.assert_called_once()
        # The door read the file; the task must still be handed all of it.
        payload = launch.call_args.args[3]
        self.assertEqual(base64.b64decode(payload["content_b64"]), PNG)

    @patch("students.views.launch_processing_task")
    def test_the_line_is_the_estimate_itself_one_below_refuses_the_estimate_passes(
        self, launch
    ):
        # Learn the estimate the door computes for this very file, from a
        # request that passes, then put a wallet exactly on each side of it.
        launch.return_value.id = "task-1"
        _, student, _, assignment = _classroom("learn", RICH)
        with _Spy() as spy:
            self.assertEqual(
                self._upload(student, assignment).status_code, status.HTTP_200_OK
            )
        self.assertEqual(len(spy.values), 1)
        estimate = spy.values[0]
        self.assertGreater(estimate, 20_000)

        _, below_student, _, below_assignment = _classroom("below", estimate - 1)
        _, exact_student, _, exact_assignment = _classroom("exact", estimate)

        below = self._upload(below_student, below_assignment)
        exact = self._upload(exact_student, exact_assignment)

        self.assertEqual(below.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(exact.status_code, status.HTTP_200_OK)

    @patch("students.views.launch_processing_task")
    def test_the_door_asks_the_shared_estimate_method_and_nothing_else(self, launch):
        # A huge answer from the shared method refuses a rich wallet; a zero
        # answer lets a one-credit wallet through. A copy of the arithmetic in
        # the door would do neither.
        launch.return_value.id = "task-1"
        _, rich_student, _, rich_assignment = _classroom("huge", RICH)
        _, tiny_student, _, tiny_assignment = _classroom("zero", 1)

        with patch.object(AIProcessor, "estimate_messages_cost", return_value=10**9):
            refused = self._upload(rich_student, rich_assignment)
        with patch.object(AIProcessor, "estimate_messages_cost", return_value=0):
            passed = self._upload(tiny_student, tiny_assignment)

        self.assertEqual(refused.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(passed.status_code, status.HTTP_200_OK)

    @patch("students.views.launch_processing_task")
    def test_a_file_the_door_cannot_read_is_left_to_the_task(self, launch):
        # Cannot estimate means cannot say "certainly refused": the task still
        # turns a bad file into its own final refusal.
        launch.return_value.id = "task-1"
        _, student, _, assignment = _classroom("garbled", POOR)

        response = self._upload(student, assignment, data=b"not an image")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        launch.assert_called_once()

    @patch("students.views.launch_processing_task")
    def test_a_teacher_without_a_wallet_is_left_to_the_permission(self, launch):
        # The permission class is the one that refuses a missing wallet; the
        # door must neither crash nor refuse on its own account.
        launch.return_value.id = "task-1"
        _, student, _, assignment = _classroom("nowallet", 0)

        with patch(
            "users.permissions.HasCreditBalance.has_permission", return_value=True
        ):
            response = self._upload(student, assignment)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        launch.assert_called_once()


class TeacherBatchDoorTest(APITestCase):
    def _batch(self, teacher, assignment, count=1):
        self.client.force_authenticate(user=teacher)
        url = reverse(
            "student-submission-batch-upload", kwargs={"assignment_id": assignment.pk}
        )
        files = [_file(name=f"s{n}.png") for n in range(count)]
        return self.client.post(url, {"answers": files}, format="multipart")

    @patch("students.views.launch_processing_task")
    def test_a_poor_teacher_is_refused_and_nothing_is_queued(self, launch):
        teacher, _, _, assignment = _classroom("batch-poor", POOR)

        response = self._batch(teacher, assignment, count=2)

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(response.data["code"], "insufficient_credits")
        self.assertEqual(response.data["error"], INSUFFICIENT_CREDITS_MESSAGE)
        launch.assert_not_called()
        self.assertEqual(BatchUploadSession.objects.count(), 0)
        self.assertEqual(BackgroundProcessingTask.objects.count(), 0)

    @patch("students.views.launch_processing_task")
    def test_a_funded_teacher_is_queued_as_before(self, launch):
        launch.return_value.id = "task-1"
        teacher, _, _, assignment = _classroom("batch-rich", RICH)

        response = self._batch(teacher, assignment, count=2)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(len(response.data["tasks"]), 2)
        self.assertEqual(launch.call_count, 2)
        for call in launch.call_args_list:
            payload = call.args[3]
            self.assertEqual(base64.b64decode(payload["content_b64"]), PNG)


class AssignmentUploadDoorTest(APITestCase):
    def _upload(self, teacher, course):
        self.client.force_authenticate(user=teacher)
        return self.client.post(
            reverse("assignment-upload-async"),
            {"course": str(course.id), "assignments": [_file(name="paper.png")]},
            format="multipart",
        )

    @patch("assignments.views.launch_processing_task")
    def test_a_poor_teacher_is_refused_and_nothing_is_queued(self, launch):
        teacher, _, course, _ = _classroom("assign-poor", POOR)

        response = self._upload(teacher, course)

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(response.data["code"], "insufficient_credits")
        self.assertEqual(response.data["error"], INSUFFICIENT_CREDITS_MESSAGE)
        launch.assert_not_called()
        self.assertEqual(BatchUploadSession.objects.count(), 0)

    @patch("assignments.views.launch_processing_task")
    def test_a_funded_teacher_is_queued_as_before(self, launch):
        launch.return_value.id = "task-1"
        teacher, _, course, _ = _classroom("assign-rich", RICH)

        response = self._upload(teacher, course)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        launch.assert_called_once()
        payload = launch.call_args.kwargs["file_payload"]
        self.assertEqual(base64.b64decode(payload["content_b64"]), PNG)


class TheDoorFunctionTest(TestCase):
    def test_a_super_admin_is_never_refused_and_a_poor_teacher_is(self):
        # The control comes first: with the same poor wallet and file, the
        # function DOES refuse a teacher, so the None for the super admin
        # below is a decision, not an absence of one.
        teacher, _, _, _ = _classroom("door-fn", POOR)
        admin = _user("door-admin", UserTypes.SUPER_ADMIN)
        admin.is_superuser = True
        admin.save(update_fields=["is_superuser"])
        _fund(admin, POOR)

        refused = upload_refusal_if_unaffordable(teacher, None, [_file()], "p")
        passed = upload_refusal_if_unaffordable(admin, None, [_file()], "p")

        self.assertIsNotNone(refused)
        self.assertEqual(refused.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertIsNone(passed)


class TheGateAsksTheSameMethodTest(ExecuteGradedTaskTestBase):
    """The door and the gate must not be able to differ: the gate reads its
    estimate from the shared method too."""

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_the_gate_refuses_what_the_shared_method_says_is_too_dear(self, ai_model):
        teacher = self._make_teacher_with_credits(credits=100_000)

        with patch.object(AIProcessor, "estimate_messages_cost", return_value=10**9):
            with self.assertRaises(InsufficientCreditsError):
                self.processor.execute_graded_task(
                    user=teacher,
                    feature="Grading Assignment",
                    task_type="grade_assignment",
                    user_prompt="prompt",
                )

        ai_model.assert_not_called()

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_the_gate_lets_through_what_the_shared_method_says_is_cheap(self, ai_model):
        from billing.tests.test_execute_graded_task import make_ai_response

        ai_model.return_value = make_ai_response(tokens=10)
        teacher = self._make_teacher_with_credits(credits=100)

        with patch.object(AIProcessor, "estimate_messages_cost", return_value=1):
            self.processor.execute_graded_task(
                user=teacher,
                feature="Grading Assignment",
                task_type="grade_assignment",
                user_prompt="prompt",
            )

        ai_model.assert_called_once()

    def test_the_method_counts_the_text_the_images_and_the_flat_part(self):
        # What it was inline in the gate: the text, then every image or pdf
        # the messages carry, then 20,000.
        with patch.object(
            AIProcessor, "estimate_total_token", return_value=777
        ) as total:
            value = getattr(self.processor, SHARED_ESTIMATE)(
                "user text",
                [{"type": "text", "text": " system text"}],
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": " message text"},
                            {"type": "image_url", "bytes": b"img"},
                            {"type": "pdf_url", "bytes": b"pdf"},
                        ],
                    }
                ],
            )

        self.assertEqual(value, 777)
        total.assert_called_once_with(
            "user text system text message text", [b"img"], [b"pdf"]
        )


class WhatAStudentReadsWhenTheTaskIsRefusedTest(TestCase):
    """The failed-task text on the polled status route. A student reads the
    fixed sentence; a teacher keeps the old generic text. Neither reads the
    gate's own text (the balance and the estimate)."""

    def _refused_task(self, requested_by, assignment):
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=requested_by,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=assignment,
            celery_task_id=str(uuid4()),
        )
        payload = {
            "name": "a.png",
            "content_type": "image/png",
            "content_b64": base64.b64encode(PNG).decode("ascii"),
        }
        with patch(
            "assignments.tasks.upload_answers_engine",
            side_effect=InsufficientCreditsError(GATE_TEXT),
        ):
            upload_answers_engine_async.apply(
                args=(
                    str(assignment.id),
                    payload,
                    "prompt",
                    str(requested_by.id),
                ),
                kwargs={"processing_task_id": tracked.id},
            )
        tracked.refresh_from_db()
        return tracked

    def _status(self, user, tracked):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=user)
        return client.get(f"/api/v1/tasks/status/{tracked.celery_task_id}")

    def test_a_student_reads_the_fixed_sentence(self):
        _, student, _, assignment = _classroom("status-student", POOR)
        tracked = self._refused_task(student, assignment)
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)

        response = self._status(student, tracked)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        text = response.data["meta"]
        self.assertTrue(text)
        self.assertIn(STUDENT_SENTENCE, text)
        self.assertEqual(tracked.error, STUDENT_SENTENCE)
        for fragment in ("5000", "25000", "wallet", "refill"):
            self.assertNotIn(fragment, text.lower())

    def test_a_teacher_keeps_the_generic_text(self):
        teacher, _, _, assignment = _classroom("status-teacher", POOR)
        tracked = self._refused_task(teacher, assignment)
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)

        response = self._status(teacher, tracked)

        text = response.data["meta"]
        self.assertTrue(text)
        self.assertIn(INSUFFICIENT_CREDITS_MESSAGE, text)
        self.assertEqual(tracked.error, INSUFFICIENT_CREDITS_MESSAGE)
        self.assertNotIn(STUDENT_SENTENCE, text)
        self.assertNotIn("25000", text)
