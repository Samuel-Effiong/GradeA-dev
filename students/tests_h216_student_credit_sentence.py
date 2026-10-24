"""H-216: the SYNCHRONOUS student routes read the fixed student sentence.

H-180 and H-211 made a student's refused upload and a student's refused edit,
on the asynchronous routes, read one fixed sentence that names no balance, no
amount, no "credit" and no "wallet". Two routes of the student submission
viewset still run the same billed extraction inside the request and answer a
credit refusal through `_failure_response`, now on the coded road
(`coded_response`): the edit (PATCH, kept until H-11 retires it) and the
answer upload (POST .../upload). A student who uses them was told about the
teacher's wallet TWICE: in `error` (the generic credit sentence) and in
`remediation` ("Top up credits, ...").

For a student the body keeps the code (INSUFFICIENT_CREDITS, 402, the legacy
`code`), reads STUDENT_UPLOAD_NOT_PROCESSED in `error`, and carries a null
`remediation` (SM ruling 2026-10-14: the sentence already says what to do).

Every test goes through the REAL route and the REAL billing gate: the
teacher's wallet is below every estimate (the gate adds a flat 20,000), only
the provider is replaced, and it must not be reached. A teacher who asks for
the same edit keeps the generic text and remediation, and a student's
refusal that is not about credits keeps its own body (the controls).
"""

import io
import json
import re
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from ai_processor.exceptions import NOT_CHARGED, ProviderFailureError
from ai_processor.services import STUDENT_AI_UNAVAILABLE_MESSAGE, AIProcessor
from assignments.models import Assignment, AssignmentStatus
from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES
from billing.errors import INSUFFICIENT_CREDITS_MESSAGE
from billing.models import CreditWallet, PlanType
from billing.tests.test_execute_graded_task import ExecuteGradedTaskTestBase
from billing.tests.test_refusal_handling import cancelled_teacher
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.exceptions import STUDENT_UPLOAD_NOT_PROCESSED
from students.models import StudentSubmission
from users.models import UserTypes

MONEY_WORDS = ("credit", "wallet", "balance", "refill", "top up", "topped")
POOR = 5_000
RICH = 200_000
UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
CREDITS_SPEC = REASON_CODES[ReasonCode.INSUFFICIENT_CREDITS]
PLAN_SPEC = REASON_CODES[ReasonCode.AI_FEATURE_NOT_AVAILABLE]
PROVIDER_SPEC = REASON_CODES[ReasonCode.PROVIDER_FAILURE]


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _png()


class Base(ExecuteGradedTaskTestBase):
    def _classroom(self, teacher_credits, student_credits=0, teacher=None):
        if teacher is None:
            plan = self._make_plan(PlanType.PRO)
            # a teacher with an active subscription and a wallet of
            # `teacher_credits` (the gate then refuses on credits, not the plan)
            teacher = self._make_teacher_with_credits(
                plan=plan, credits=teacher_credits
            )
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
            questions=[
                {"question_number": 1, "question_text": "Q1?", "points": 10},
                {"question_number": 2, "question_text": "Q2?", "points": 10},
            ],
        )
        return teacher, student, assignment

    def _submission(self, student, assignment):
        return StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )

    def _api(self, user):
        api = APIClient()
        api.force_authenticate(user=user)
        return api

    def _balance(self, user):
        return CreditWallet.objects.get(user=user).total_remaining_credits()

    def _edit(self, who, submission):
        return self._api(who).patch(
            reverse("student-submission-detail", kwargs={"pk": submission.pk}),
            {"raw_input": "An edited answer."},
            format="json",
        )

    def _upload(self, who, assignment):
        return self._api(who).post(
            reverse(
                "student-submission-upload-answers",
                kwargs={"assignment_id": assignment.pk},
            ),
            {"answer": SimpleUploadedFile("a.png", PNG, content_type="image/png")},
            format="multipart",
        )

    def assertStudentSentence(self, response):
        self.assertEqual(
            response.status_code, status.HTTP_402_PAYMENT_REQUIRED, response.data
        )
        # The machine-readable parts stay the credit refusal's.
        self.assertEqual(response.data["reason_code"], "INSUFFICIENT_CREDITS")
        self.assertEqual(response.data["code"], "insufficient_credits")
        # What a reader is shown: the fixed sentence, and no remediation.
        self.assertTrue(response.data["error"])
        self.assertEqual(response.data["error"], STUDENT_UPLOAD_NOT_PROCESSED)
        self.assertIn("remediation", response.data)
        self.assertIsNone(response.data["remediation"])
        rendered = json.loads(response.content.decode())
        self.assertEqual(rendered["message"], STUDENT_UPLOAD_NOT_PROCESSED)
        # The rendered body also holds the machine code "insufficient_credits",
        # so the money words are checked on each text a reader is shown, read
        # from its own key.
        for text in (response.data["error"], rendered["message"]):
            for word in MONEY_WORDS:
                self.assertNotIn(word, text.lower())
        # the whole body, with the ids taken out (a random id can hold digits)
        body = UUID.sub("<id>", response.content.decode()).lower()
        for number in ("5000", "25000", "20000"):
            self.assertNotIn(number, body)


class TheSynchronousEditReadsTheSentence(Base):
    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_e1_a_student_whose_teacher_cannot_pay_reads_the_fixed_sentence(
        self, ai_model
    ):
        ai_model.side_effect = AssertionError("the provider was reached")
        teacher, student, assignment = self._classroom(POOR)
        submission = self._submission(student, assignment)
        before = (self._balance(teacher), self._balance(student))

        response = self._edit(student, submission)

        self.assertStudentSentence(response)
        ai_model.assert_not_called()
        self.assertEqual((self._balance(teacher), self._balance(student)), before)
        submission.refresh_from_db()
        self.assertEqual(submission.answers[0]["answer_html"], "An answer.")

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_e2_the_students_own_funded_wallet_changes_nothing(self, ai_model):
        ai_model.side_effect = AssertionError("the provider was reached")
        teacher, student, assignment = self._classroom(POOR, student_credits=RICH)
        submission = self._submission(student, assignment)
        before = (self._balance(teacher), self._balance(student))
        self.assertGreater(before[1], before[0])

        response = self._edit(student, submission)

        self.assertStudentSentence(response)
        ai_model.assert_not_called()
        self.assertEqual((self._balance(teacher), self._balance(student)), before)


class TheSynchronousUploadReadsTheSentence(Base):
    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_u1_a_student_whose_teacher_cannot_pay_reads_the_fixed_sentence(
        self, ai_model
    ):
        ai_model.side_effect = AssertionError("the provider was reached")
        teacher, student, assignment = self._classroom(POOR)
        before = (self._balance(teacher), self._balance(student))

        response = self._upload(student, assignment)

        self.assertStudentSentence(response)
        ai_model.assert_not_called()
        self.assertEqual((self._balance(teacher), self._balance(student)), before)
        self.assertEqual(
            StudentSubmission.objects.filter(
                assignment=assignment, student=student
            ).count(),
            0,
        )


class EveryOtherAnswerIsUnchanged(Base):
    """Controls, green before and after."""

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_c1_a_teacher_asking_for_the_same_edit_keeps_the_generic_body(
        self, ai_model
    ):
        ai_model.side_effect = AssertionError("the provider was reached")
        teacher, student, assignment = self._classroom(POOR)
        submission = self._submission(student, assignment)

        response = self._edit(teacher, submission)

        self.assertEqual(
            response.status_code, status.HTTP_402_PAYMENT_REQUIRED, response.data
        )
        self.assertEqual(response.data["reason_code"], "INSUFFICIENT_CREDITS")
        self.assertEqual(response.data["code"], "insufficient_credits")
        self.assertTrue(response.data["error"])
        self.assertEqual(response.data["error"], INSUFFICIENT_CREDITS_MESSAGE)
        self.assertTrue(CREDITS_SPEC.remediation)
        self.assertEqual(response.data["remediation"], CREDITS_SPEC.remediation)
        ai_model.assert_not_called()

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_c2_a_students_plan_refusal_keeps_its_own_body(self, ai_model):
        # The teacher's subscription is cancelled (credits remain): the gate
        # refuses on the PLAN, a different refusal with its own student text.
        ai_model.side_effect = AssertionError("the provider was reached")
        teacher = cancelled_teacher("h216-plan")
        teacher, student, assignment = self._classroom(0, teacher=teacher)
        submission = self._submission(student, assignment)

        response = self._edit(student, submission)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)
        self.assertEqual(response.data["reason_code"], "AI_FEATURE_NOT_AVAILABLE")
        self.assertEqual(response.data["code"], "ai_feature_not_available")
        self.assertTrue(response.data["error"])
        self.assertEqual(response.data["error"], STUDENT_AI_UNAVAILABLE_MESSAGE)
        self.assertTrue(PLAN_SPEC.remediation)
        self.assertEqual(response.data["remediation"], PLAN_SPEC.remediation)
        ai_model.assert_not_called()

    def test_c3_a_students_coded_non_credit_failure_keeps_its_own_body(self):
        # A coded failure that is not a credit refusal (and whose message
        # itself speaks of credits) is not touched by the student switch.
        teacher, student, assignment = self._classroom(RICH)
        submission = self._submission(student, assignment)
        failure = ProviderFailureError(params={"credit_clause": NOT_CHARGED})
        with patch(
            "students.views.update_submission_from_raw_text", side_effect=failure
        ):
            response = self._edit(student, submission)

        self.assertEqual(
            response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data
        )
        self.assertEqual(response.data["reason_code"], "PROVIDER_FAILURE")
        self.assertTrue(response.data["error"])
        self.assertEqual(response.data["error"], failure.message)
        self.assertIn(NOT_CHARGED, response.data["error"])
        self.assertTrue(PROVIDER_SPEC.remediation)
        self.assertEqual(response.data["remediation"], PROVIDER_SPEC.remediation)

    def test_c4_the_unrelated_student_failure_text_is_unchanged(self):
        teacher, student, assignment = self._classroom(RICH)
        submission = self._submission(student, assignment)
        with patch(
            "students.views.update_submission_from_raw_text",
            side_effect=RuntimeError("internal detail H216-NOT-SHOWN"),
        ):
            response = self._edit(student, submission)

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertTrue(response.data["error"])
        self.assertNotIn("H216-NOT-SHOWN", response.content.decode())
        self.assertNotEqual(response.data["error"], STUDENT_UPLOAD_NOT_PROCESSED)
