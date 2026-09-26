"""
HasCreditBalance composition guard: H-19 (fix-idor, 93) x H-24 D11 (refusal
handling). FOR THE INTEGRATION BUILD ONLY.

Two independent fixes changed one permission class:
  * H-19 decides WHO bypasses the credit check: only a real superadmin, with
    BOTH is_superuser and user_type == SUPER_ADMIN. A single-flag impostor
    falls through to the wallet check like anyone else.
  * H-24 D11 decides HOW an empty wallet is answered: 402 with
    code "insufficient_credits" and plain text, not a 400 ParseError with
    HTML markup.

This test pins the COMBINATION. It cannot pass on either branch alone:
  * on b744c9f and on task/refusal-handling alone, the impostor BYPASSES
    (the H-19 bug), so no refusal happens at all;
  * on 93's branch alone, the impostor is refused with the OLD 400 + HTML.
So it belongs in the integration commit, committed by the integrator on top
of the merges, as users/tests_credit_balance_composition.py.

Demonstrate it on the merged tree with two mutants (disposable workers,
restore verified by sha256 against the merged commit's blob):
  (i)  H-19 reverted - in users/permissions.py, make the superadmin bypass
       check user_type alone again (drop the is_superuser requirement).
       Expected: the impostor bypasses -> this test FAILS (not a 402).
  (ii) D11 reverted - in users/permissions.py, replace
           raise EmptyWalletError("Credit wallet is empty")
       with
           raise ParseError("<b>Insufficient Credits:</b> Your Credit Wallet is currently empty.")
       (import ParseError from rest_framework.exceptions).
       Expected: 400 + HTML -> this test FAILS.
Then restore, and the test PASSES on the merged tree.

Run with:
    python manage.py test users.tests_credit_balance_composition
"""

import json
import uuid

from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from billing.errors import (
    INSUFFICIENT_CREDITS_MESSAGE,
    EmptyWalletError,
    InsufficientCreditsError,
)
from billing.models import CreditWallet
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import BackgroundProcessingTask, StudentSubmission
from users.models import CustomUser, UserTypes
from users.permissions import HasCreditBalance


def _user(tag, user_type, **extra):
    return CustomUser.objects.create_user(
        email=f"{tag}-{uuid.uuid4().hex[:8]}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        **extra,
    )


def impostor_superadmin():
    """user_type SUPER_ADMIN, but NOT is_superuser: the H-19 impostor."""
    user = _user("impostor", UserTypes.SUPER_ADMIN, is_superuser=False)
    assert user.user_type == UserTypes.SUPER_ADMIN and not user.is_superuser
    return user


def real_superadmin():
    return CustomUser.objects.create_superuser(
        email=f"real-super-{uuid.uuid4().hex[:8]}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.SUPER_ADMIN,
    )


def empty_wallet(user):
    """The signal gives every new user a wallet with no buckets; prove it's
    empty rather than assume it."""
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    assert wallet.total_remaining_credits() == 0
    return wallet


class ImpostorWithEmptyWalletIsRefusedOverHttpTest(APITestCase):
    """The end-to-end case: the impostor calls a credit-guarded endpoint
    whose ONLY gates are IsAuthenticated + HasCreditBalance:
    POST submissions/{id}/update-async - the same surface red-team-1 replays
    over HTTP, so the committed test and the replay corroborate each other.

    NOT grade-all or any other IsTeacher-gated endpoint: IsTeacher admits
    only user_type == TEACHER and runs first, so the impostor gets 403 "You
    must be a teacher" on every tree, the credit check never runs, and both
    mutants below would "fail" for that unrelated reason - proving nothing.
    (Verified live on b744c9f by red-team-1 and against the code here.)"""

    def setUp(self):
        teacher = _user("owner", UserTypes.TEACHER)
        student = _user("student", UserTypes.STUDENT)
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
            questions=[{"question_number": 1, "question_text": "Q?", "points": 10}],
        )
        self.submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "original"}],
            attempt_count=1,
        )
        self.url = reverse(
            "student-submission-update-async", args=[str(self.submission.id)]
        )

    def test_single_flag_impostor_with_an_empty_wallet_gets_402_plain_text(self):
        impostor = impostor_superadmin()
        empty_wallet(impostor)
        self.client.force_authenticate(user=impostor)

        tasks_before = BackgroundProcessingTask.objects.count()
        response = self.client.post(
            self.url, {"raw_input": "Question 1: edited"}, format="json"
        )

        # Not a bypass (H-19) and not a 400 (D11).
        self.assertEqual(response.status_code, 402, response.content)
        # The body too, so a 402 carrying the old text can't pass.
        body = json.loads(response.content)
        self.assertEqual(body["message"], INSUFFICIENT_CREDITS_MESSAGE)
        self.assertEqual(body["error"]["field_errors"]["code"], "insufficient_credits")
        self.assertNotIn("<b>", response.content.decode())
        self.assertNotIn("Insufficient Credits:", response.content.decode())
        # And the refusal happened BEFORE any work: nothing was queued and the
        # submission is untouched.
        self.assertEqual(BackgroundProcessingTask.objects.count(), tasks_before)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.answers[0]["answer_html"], "original")


class HasCreditBalanceCompositionUnitTest(TestCase):
    """The same composition at the permission itself, both directions."""

    def _check(self, user):
        request = RequestFactory().post("/")
        request.user = user
        view = type("View", (), {"kwargs": {}})()
        return HasCreditBalance().has_permission(request, view)

    def test_impostor_with_empty_wallet_is_refused_as_an_insufficient_credits_error(
        self,
    ):
        impostor = impostor_superadmin()
        empty_wallet(impostor)

        with self.assertRaises(EmptyWalletError) as raised:
            self._check(impostor)

        self.assertIsInstance(raised.exception, InsufficientCreditsError)
        self.assertEqual(raised.exception.status_code, 402)

    def test_real_superadmin_with_empty_wallet_still_bypasses(self):
        """The other direction: H-19 must not have turned the legitimate
        bypass into a refusal."""
        admin = real_superadmin()
        empty_wallet(admin)

        self.assertTrue(self._check(admin))
