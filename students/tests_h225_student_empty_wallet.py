"""H-225: a student whose teacher's wallet is EMPTY reads the student sentence.

H-216 gave a student's in-view credit refusal (a wallet above 0 but below the
call's estimate) the one fixed student sentence and no remediation. At
exactly 0 the request never reaches the view: HasCreditBalance refuses it
first with EmptyWalletError, and users.exceptions answers that with the
generic credit sentence ("The credit wallet needs to be topped up ...") and
the remediation "Top up credits, ...", so a student is told about their
teacher's wallet on every student submission route.

For a STUDENT the body keeps the code (INSUFFICIENT_CREDITS, 402, the legacy
`code`), reads STUDENT_UPLOAD_NOT_PROCESSED in `error` and carries a null
`remediation`, on each route kind: the synchronous upload, the asynchronous
upload, the edit (PATCH) and the asynchronous edit. Nothing is queued and the
provider is never reached. A teacher at 0 keeps the generic body, and a
student's refusal that is not about credits is unchanged (the controls).
"""

import io
import json
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from ai_processor.services import AIProcessor
from assignments.models import Assignment, AssignmentStatus
from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES
from billing.errors import INSUFFICIENT_CREDITS_MESSAGE
from billing.models import CreditWallet, PlanType
from billing.tests.test_execute_graded_task import ExecuteGradedTaskTestBase
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.exceptions import STUDENT_UPLOAD_NOT_PROCESSED
from students.models import BackgroundProcessingTask, StudentSubmission
from users.models import UserTypes

MONEY_WORDS = ("credit", "wallet", "balance", "refill", "top up", "topped")
EMPTY = 0
RICH = 200_000
CREDITS_SPEC = REASON_CODES[ReasonCode.INSUFFICIENT_CREDITS]
NO_FILES = "No files uploaded. Please try again."


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _png()


class Base(ExecuteGradedTaskTestBase):
    def setUp(self):
        super().setUp()
        provider = patch.object(
            AIProcessor,
            "_AIProcessor__ai_model",
            side_effect=AssertionError("the provider was reached"),
        )
        self.provider = provider.start()
        self.addCleanup(provider.stop)
        launch = patch(
            "students.views.launch_processing_task",
            side_effect=AssertionError("a task was queued"),
        )
        self.launch = launch.start()
        self.addCleanup(launch.stop)

    def _classroom(self, teacher_credits, student_credits=0):
        plan = self._make_plan(PlanType.PRO)
        # An active subscription and a wallet of `teacher_credits`: at 0 the
        # permission refuses on the empty wallet, before any view code.
        teacher = self._make_teacher_with_credits(plan=plan, credits=teacher_credits)
        teacher.email_verified_at = timezone.now()
        teacher.save(update_fields=["email_verified_at"])
        student = self._make_user(UserTypes.STUDENT, f"s-{uuid4().hex[:8]}@example.com")
        student.email_verified_at = timezone.now()
        student.save(update_fields=["email_verified_at"])
        if student_credits:
            self._give_credits(student, student_credits)
        session = Session.objects.create(name="S", teacher=teacher)
        course = Course.objects.create(name="C", teacher=teacher, session=session)
        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        assignment = Assignment.objects.create(
            title="A",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )
        return teacher, student, assignment, submission

    def _api(self, user):
        api = APIClient()
        api.force_authenticate(user=user)
        return api

    def _file(self):
        return SimpleUploadedFile("a.png", PNG, content_type="image/png")

    def upload(self, who, assignment):
        return self._api(who).post(
            reverse(
                "student-submission-upload-answers",
                kwargs={"assignment_id": assignment.pk},
            ),
            {"answer": self._file()},
            format="multipart",
        )

    def upload_async(self, who, assignment, with_file=True):
        return self._api(who).post(
            reverse(
                "student-submission-upload-async",
                kwargs={"assignment_id": assignment.pk},
            ),
            {"answer": self._file()} if with_file else {},
            format="multipart",
        )

    def edit(self, who, submission):
        return self._api(who).patch(
            reverse("student-submission-detail", kwargs={"pk": submission.pk}),
            {"raw_input": "An edited answer."},
            format="json",
        )

    def edit_async(self, who, submission):
        return self._api(who).post(
            reverse("student-submission-update-async", kwargs={"pk": submission.pk}),
            {"raw_input": "An edited answer."},
            format="json",
        )

    def assertStudentSentence(self, response):
        self.assertEqual(
            response.status_code, status.HTTP_402_PAYMENT_REQUIRED, response.data
        )
        self.assertEqual(response.data["reason_code"], "INSUFFICIENT_CREDITS")
        self.assertEqual(response.data["code"], "insufficient_credits")
        self.assertTrue(response.data["error"])
        self.assertEqual(response.data["error"], STUDENT_UPLOAD_NOT_PROCESSED)
        self.assertIn("remediation", response.data)
        self.assertIsNone(response.data["remediation"])
        rendered = json.loads(response.content.decode())
        self.assertEqual(rendered["message"], STUDENT_UPLOAD_NOT_PROCESSED)
        for text in (response.data["error"], rendered["message"]):
            for word in MONEY_WORDS:
                self.assertNotIn(word, text.lower())

    def assertNothingHappened(self, teacher, student, submission, before):
        self.provider.assert_not_called()
        self.launch.assert_not_called()
        self.assertEqual(BackgroundProcessingTask.objects.count(), 0)
        self.assertEqual(StudentSubmission.objects.filter(student=student).count(), 1)
        submission.refresh_from_db()
        self.assertEqual(submission.answers[0]["answer_html"], "An answer.")
        self.assertEqual(self._balances(teacher, student), before)

    def _balances(self, teacher, student):
        def balance(user):
            wallet = CreditWallet.objects.filter(user=user).first()
            return wallet.total_remaining_credits() if wallet else None

        return balance(teacher), balance(student)


class AStudentAtAnEmptyWalletReadsTheSentence(Base):
    def test_s1_the_synchronous_upload(self):
        teacher, student, assignment, submission = self._classroom(EMPTY)
        before = self._balances(teacher, student)

        response = self.upload(student, assignment)

        self.assertStudentSentence(response)
        self.assertNothingHappened(teacher, student, submission, before)

    def test_s2_the_asynchronous_upload(self):
        teacher, student, assignment, submission = self._classroom(EMPTY)
        before = self._balances(teacher, student)

        response = self.upload_async(student, assignment)

        self.assertStudentSentence(response)
        self.assertNothingHappened(teacher, student, submission, before)

    def test_s3_the_edit(self):
        teacher, student, assignment, submission = self._classroom(EMPTY)
        before = self._balances(teacher, student)

        response = self.edit(student, submission)

        self.assertStudentSentence(response)
        self.assertNothingHappened(teacher, student, submission, before)

    def test_s4_the_asynchronous_edit(self):
        teacher, student, assignment, submission = self._classroom(EMPTY)
        before = self._balances(teacher, student)

        response = self.edit_async(student, submission)

        self.assertStudentSentence(response)
        self.assertNothingHappened(teacher, student, submission, before)

    def test_s5_the_students_own_funded_wallet_changes_nothing(self):
        teacher, student, assignment, submission = self._classroom(
            EMPTY, student_credits=RICH
        )
        before = self._balances(teacher, student)
        self.assertEqual(before, (0, RICH))

        response = self.edit(student, submission)

        self.assertStudentSentence(response)
        self.assertNothingHappened(teacher, student, submission, before)


class EveryOtherAnswerIsUnchanged(Base):
    """Controls, green before and after."""

    def test_c1_a_teacher_at_an_empty_wallet_keeps_the_generic_body(self):
        teacher, student, assignment, submission = self._classroom(EMPTY)

        response = self.edit(teacher, submission)

        self.assertEqual(
            response.status_code, status.HTTP_402_PAYMENT_REQUIRED, response.data
        )
        self.assertEqual(response.data["reason_code"], "INSUFFICIENT_CREDITS")
        self.assertEqual(response.data["code"], "insufficient_credits")
        self.assertTrue(response.data["error"])
        self.assertEqual(response.data["error"], INSUFFICIENT_CREDITS_MESSAGE)
        self.assertTrue(CREDITS_SPEC.remediation)
        self.assertEqual(response.data["remediation"], CREDITS_SPEC.remediation)
        self.provider.assert_not_called()

    def test_c2_a_students_non_credit_refusal_is_unchanged(self):
        # A funded teacher: the permission lets the request in and the view
        # refuses it for a missing file (a 400 with DRF's own body).
        teacher, student, assignment, submission = self._classroom(RICH)

        response = self.upload_async(student, assignment, with_file=False)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIsInstance(response.data, dict)
        self.assertEqual(str(response.data["detail"]), NO_FILES)
        self.assertNotIn("reason_code", response.data)
        self.launch.assert_not_called()
