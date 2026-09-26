"""
HasCreditBalance - the permission that gates every billed AI endpoint.

`users/permissions.py` sat at ~34% coverage with `_get_teacher_for_request`
(the half that decides WHOSE wallet gets checked) entirely unexercised.
Sibling suites touch this class only incidentally: students/tests.py tops up
wallets specifically so the permission stays out of the way while it tests
something else.

Both directions of a mistake here are expensive:

  * too strict - a paying teacher is locked out of grading
  * too loose  - a student burns credits belonging to a teacher who is not
                 theirs, or an empty wallet still buys an AI call

The class is exercised directly rather than through an endpoint, because
that is the only way to reach every branch of the resource-id resolution
(assignment / course / submission / nothing).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from assignments.models import Assignment
from billing.errors import EmptyWalletError, InsufficientCreditsError
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import Course
from students.models import StudentSubmission
from users.models import UserTypes
from users.permissions import HasCreditBalance

User = get_user_model()


class FakeView:
    """Stands in for the DRF view: the permission only reads `.kwargs`."""

    __slots__ = ("kwargs",)

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def make_user(email, user_type=UserTypes.TEACHER, is_superuser=False):
    return User.objects.create_user(
        email=email,
        password="password123",  # pragma: allowlist secret
        first_name="Credit",
        last_name="User",
        user_type=user_type,
        is_active=True,
        is_superuser=is_superuser,
    )


def give_credits(user, amount=100_000):
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=amount,
        used_credits=0,
        expires_at=timezone.now() + timedelta(days=30),
    )
    return wallet


class HasCreditBalanceTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.permission = HasCreditBalance()

        self.teacher = make_user("credit.teacher@gmail.com")
        self.other_teacher = make_user("credit.other-teacher@gmail.com")
        self.student = make_user("credit.student@example.com", UserTypes.STUDENT)

        self.course = Course.objects.create(
            name="Credit 101",
            teacher=self.teacher,
            description="Course for credit permission tests.",
        )
        self.assignment = Assignment.objects.create(
            title="Credit Assignment",
            course=self.course,
            questions=[{"question_number": 1, "points": 10}],
        )
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )

    def check(self, user, **view_kwargs):
        request = self.factory.get("/")
        request.user = user
        return self.permission.has_permission(request, FakeView(**view_kwargs))

    # --- authentication -------------------------------------------------------

    def test_anonymous_is_denied_without_raising(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(self.check(AnonymousUser()))

    # --- the wallet owner's own balance ---------------------------------------

    def test_teacher_with_credits_is_allowed(self):
        give_credits(self.teacher)

        self.assertTrue(self.check(self.teacher))

    def test_teacher_with_an_empty_wallet_is_refused(self):
        CreditWallet.objects.get_or_create(user=self.teacher)

        with self.assertRaises(EmptyWalletError) as ctx:
            self.check(self.teacher)

        # A credit refusal like any other: 402 "insufficient_credits" via
        # users.exceptions, with no markup and no role-specific wording
        # (REFUSAL_HANDLING_EVIDENCE.md D11; was a 400 ParseError with HTML).
        self.assertIsInstance(ctx.exception, InsufficientCreditsError)
        self.assertEqual(ctx.exception.status_code, 402)

    def test_teacher_with_no_wallet_row_at_all_is_refused(self):
        CreditWallet.objects.filter(user=self.teacher).delete()

        with self.assertRaises(EmptyWalletError):
            self.check(self.teacher)

    def test_expired_credits_do_not_count(self):
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() - timedelta(days=1),
        )

        with self.assertRaises(EmptyWalletError):
            self.check(self.teacher)

    def test_fully_spent_credits_do_not_count(self):
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1_000,
            used_credits=1_000,
            expires_at=timezone.now() + timedelta(days=30),
        )

        with self.assertRaises(EmptyWalletError):
            self.check(self.teacher)

    # --- super admin ----------------------------------------------------------

    def test_super_admin_is_allowed_with_no_wallet(self):
        """Platform staff are unmetered - see AIProcessor.execute_graded_task.

        Platform staff means BOTH flags (H-19). The single-flag cases are
        directly below.
        """
        super_admin = make_user(
            "credit.super@example.com", UserTypes.SUPER_ADMIN, is_superuser=True
        )
        CreditWallet.objects.filter(user=super_admin).delete()

        self.assertTrue(self.check(super_admin))

    def test_single_flag_super_admin_is_not_unmetered(self):
        """H-19: user_type=SUPER_ADMIN alone used to skip the balance check,
        so an account with is_superuser unticked ran billed AI for free. It is
        now judged on its own wallet like anyone else."""
        type_only = make_user("credit.typeonly@example.com", UserTypes.SUPER_ADMIN)
        CreditWallet.objects.filter(user=type_only).delete()

        with self.assertRaises(EmptyWalletError):
            self.check(type_only)

    def test_createsuperuser_account_is_not_unmetered(self):
        """The other single-flag shape: is_superuser with user_type TEACHER,
        exactly what `manage.py createsuperuser` produces."""
        django_admin = make_user("credit.djadmin@example.com", is_superuser=True)
        CreditWallet.objects.filter(user=django_admin).delete()

        with self.assertRaises(EmptyWalletError):
            self.check(django_admin)

    # --- a student spends their TEACHER's credits -----------------------------

    def test_student_is_allowed_when_their_teacher_has_credits(self):
        """Resolution via assignment_id -> course.teacher."""
        give_credits(self.teacher)

        self.assertTrue(self.check(self.student, assignment_id=self.assignment.id))

    def test_student_is_refused_when_their_teacher_has_none(self):
        CreditWallet.objects.get_or_create(user=self.teacher)

        with self.assertRaises(EmptyWalletError) as ctx:
            self.check(self.student, assignment_id=self.assignment.id)

        # Same refusal for a student (D11); the client-facing message is the
        # role-neutral generic one (billing.errors.INSUFFICIENT_CREDITS_MESSAGE).
        self.assertIsInstance(ctx.exception, InsufficientCreditsError)
        self.assertEqual(ctx.exception.status_code, 402)

    def test_students_own_wallet_does_not_authorise_the_call(self):
        """
        The whole point of resolving the teacher: a student topping up their
        own wallet must not unlock an AI call billed to a broke teacher.
        """
        give_credits(self.student)
        CreditWallet.objects.get_or_create(user=self.teacher)

        with self.assertRaises(EmptyWalletError):
            self.check(self.student, assignment_id=self.assignment.id)

    def test_resolution_via_course_id(self):
        give_credits(self.teacher)

        self.assertTrue(self.check(self.student, course_id=self.course.id))

    def test_resolution_via_submission_id(self):
        give_credits(self.teacher)

        self.assertTrue(self.check(self.student, submission_id=self.submission.id))

    def test_resolution_via_pk_as_submission(self):
        give_credits(self.teacher)

        self.assertTrue(self.check(self.student, pk=self.submission.id))

    def test_the_resolved_teacher_is_the_owning_one_not_just_any_teacher(self):
        """
        A student's request must charge the teacher who owns the resource.
        Give the OTHER teacher credits and leave the owner empty: if
        resolution were wrong, this would wrongly pass.
        """
        give_credits(self.other_teacher)
        CreditWallet.objects.get_or_create(user=self.teacher)

        with self.assertRaises(EmptyWalletError):
            self.check(self.student, assignment_id=self.assignment.id)

    # --- resolution failures fall back to the student's own (empty) wallet ----

    def test_student_with_no_resolvable_resource_is_refused(self):
        give_credits(self.teacher)

        with self.assertRaises(EmptyWalletError):
            self.check(self.student)

    def test_unknown_assignment_id_does_not_crash(self):
        import uuid

        give_credits(self.teacher)

        with self.assertRaises(EmptyWalletError):
            self.check(self.student, assignment_id=uuid.uuid4())

    def test_unknown_course_id_does_not_crash(self):
        import uuid

        give_credits(self.teacher)

        with self.assertRaises(EmptyWalletError):
            self.check(self.student, course_id=uuid.uuid4())

    def test_unknown_submission_id_does_not_crash(self):
        import uuid

        give_credits(self.teacher)

        with self.assertRaises(EmptyWalletError):
            self.check(self.student, submission_id=uuid.uuid4())
