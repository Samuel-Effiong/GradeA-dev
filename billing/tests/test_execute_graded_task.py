"""
ai_processor/tests/test_execute_graded_task.py
================================================
Covers Phases 9-10 of TEST_PLAN.md - the AIProcessor.execute_graded_task
enforcement chokepoint, and the regression fixes to the 6 outer
`*_with_retry` wrappers (retrying a deterministic access/credit denial is
pointless and was fixed to fail fast instead).

Run with:
    python manage.py test ai_processor.tests.test_execute_graded_task

NOTE 1: importing ai_processor.services triggers several module-level
`open("ai_processor/SOME_PROMPT.txt")` calls at import time. Run tests
from your project root (wherever manage.py lives) so these resolve.

NOTE 2: `AIProcessor.__ai_model` is name-mangled to
`AIProcessor._AIProcessor__ai_model` at the class level - that's the
literal attribute name `patch.object` needs below; this isn't a typo.

NOTE 3: classrooms.models.School's required fields aren't visible to me -
_make_school() creates one with only `name`; adjust if your model needs
more.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from ai_processor.services import GRADING_FALLBACK_MODELS, AIProcessor
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import InsufficientCreditsError
from billing.models import (  # PlanFeature,; PlanFeatureInclusion,; PlanFeatureKey,
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditUsageLog,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
    UserSubscription,
)
from classrooms.models import Course, School
from users.models import CustomUser, UserTypes


def make_ai_response(tokens=100, content='{"result": "ok"}'):
    """A minimal stand-in for the OpenAI SDK response object shape that
    execute_graded_task reads: response.choices[0].message.content and
    response.usage.total_tokens."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage.total_tokens = tokens
    return response


class ExecuteGradedTaskTestBase(TestCase):
    def setUp(self):
        self.processor = AIProcessor()

    def _make_user(self, user_type, email, is_active=True):
        return CustomUser.objects.create_user(
            email=email,
            password="testpass123",
            user_type=user_type,
            is_active=is_active,
        )

    def _make_plan(
        self,
        name,
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        monthly_credits=20000,
    ):
        return SubscriptionPlan.objects.create(
            name=name,
            category=category,
            tier=tier,
            interval=BillingInterval.MONTHLY,
            monthly_credits=monthly_credits,
            carry_over_percent=25,
            is_active=True,
        )

    def _give_credits(self, user, amount):
        # estimate_total_token() adds a fixed +20,000 baseline overhead to
        # every estimate regardless of prompt length - "has credits" test
        # fixtures must clear that bar, not just be > 0.
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        if amount > 0:
            CreditBucket.objects.create(
                wallet=wallet,
                bucket_type=CreditBucketType.MONTHLY,
                total_credits=amount,
                used_credits=0,
                expires_at=timezone.now() + timedelta(days=30),
            )
        return wallet

    def _make_individual_subscription(self, user, plan):
        now = timezone.now()
        return UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=30),
            is_trial=False,
            auto_renew=True,
        )

    def _make_school(self):
        return School.objects.create(name="Test School")

    def _make_license(self, plan, admin_user):
        now = timezone.now()
        return LicenseSubscription.objects.create(
            school=self._make_school(),
            admin_user=admin_user,
            plan=plan,
            contract_months=12,
            max_seats=10,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=365),
            is_active=True,
            auto_renew=True,
        )

    def _make_allocation(self, license_sub, user, is_admin=False):
        return SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=user,
            monthly_allocation=20000,
            is_active=True,
            is_admin_allocation=is_admin,
            next_credit_grant_at=timezone.now() + timedelta(days=30),
        )

    def _make_teacher_with_credits(self, plan=None, credits=100000):
        plan = plan or self._make_plan(PlanType.PRO)
        teacher = self._make_user(
            UserTypes.TEACHER, f"t-{uuid4().hex[:10]}@example.com"
        )
        self._make_individual_subscription(teacher, plan)
        self._give_credits(teacher, credits)
        return teacher


class UserTypeDispatchTests(ExecuteGradedTaskTestBase):
    """Phase 9.1, 9.5-9.11 - per-user-type branch correctness."""

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_teacher_with_access_succeeds_and_consumes_own_wallet(self, mock_ai_model):
        mock_ai_model.return_value = make_ai_response(tokens=500)
        teacher = self._make_teacher_with_credits()

        response = self.processor.execute_graded_task(
            user=teacher,
            feature="Grading Assignment",
            task_type="grade_assignment",
            user_prompt="short prompt",
        )

        self.assertIsNotNone(response)
        mock_ai_model.assert_called_once()
        wallet = teacher.credit_wallet
        # 100000 given - 500 consumed
        self.assertEqual(wallet.total_remaining_credits(), 99500)

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_response_schema_reaches_ai_model_for_metered_path(self, mock_ai_model):
        mock_ai_model.return_value = make_ai_response(tokens=500)
        teacher = self._make_teacher_with_credits()
        schema = {"name": "test_schema", "strict": True, "schema": {"type": "object"}}

        self.processor.execute_graded_task(
            user=teacher,
            feature="Grading Assignment",
            task_type="grade_assignment",
            user_prompt="short prompt",
            response_schema=schema,
        )

        # task_type="grade_assignment" restricts fallback routing to
        # comparable-capability models (never nano-tier) - see
        # GRADING_FALLBACK_MODELS.
        mock_ai_model.assert_called_once_with(
            None,
            "short prompt",
            None,
            None,
            True,
            schema,
            sub_models=GRADING_FALLBACK_MODELS,
        )

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_tool_call_message_with_none_content_does_not_crash(self, mock_ai_model):
        """
        Regression test: a replayed assistant tool-call message (as built
        by generate_assignment_from_prompt after a fetch_url_content round
        trip) has content=None - the token-estimation pass used to crash
        on this (`for item in content` with content=None) as soon as a
        real tool call was ever exercised end-to-end.
        """
        mock_ai_model.return_value = make_ai_response(tokens=500)
        teacher = self._make_teacher_with_credits()

        response = self.processor.execute_graded_task(
            user=teacher,
            feature="Grading Assignment",
            task_type="grade_assignment",
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "please make a quiz"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "fetch_url_content",
                                "arguments": '{"urls": ["https://example.com"]}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": "fetched page text",
                },
            ],
        )

        self.assertIsNotNone(response)

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_gated_feature_blocked_before_any_ai_call(self, mock_ai_model):
        standard_plan = self._make_plan(PlanType.STANDARD, tier=PlanTier.STANDARD)
        teacher = self._make_teacher_with_credits(plan=standard_plan)

        # AI_PROMPT_ASSIGNMENT_CREATION not configured/included at all on
        # this plan -> deny by default.
        with self.assertRaises(AIFeatureNotAvailableError):
            self.processor.execute_graded_task(
                user=teacher,
                feature="Assignment Generation",
                task_type="generate_assignment",
                user_prompt="prompt",
            )

        mock_ai_model.assert_not_called()

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_insufficient_credits_blocked_before_any_ai_call(self, mock_ai_model):
        # 100 raw credits is well under estimate_total_token's fixed
        # +20,000 baseline overhead.
        teacher = self._make_teacher_with_credits(credits=100)

        with self.assertRaises(InsufficientCreditsError):
            self.processor.execute_graded_task(
                user=teacher,
                feature="Grading Assignment",
                task_type="grade_assignment",
                user_prompt="prompt",
            )

        mock_ai_model.assert_not_called()

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_zero_balance_gives_refill_message(self, mock_ai_model):
        teacher = self._make_teacher_with_credits(credits=0)

        with self.assertRaises(InsufficientCreditsError) as ctx:
            self.processor.execute_graded_task(
                user=teacher,
                feature="Grading Assignment",
                task_type="grade_assignment",
                user_prompt="prompt",
            )
        self.assertIn("Refill your wallet", str(ctx.exception))

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_student_submission_billed_to_teacher_not_student(self, mock_ai_model):
        mock_ai_model.return_value = make_ai_response(tokens=500)
        teacher = self._make_teacher_with_credits()
        student = self._make_user(UserTypes.STUDENT, "student@example.com")
        # Deliberately give the student NO wallet/subscription at all -
        # proves consumption can never accidentally hit their account.

        # A real (saved) Course is needed here, not a bare MagicMock -
        # execute_graded_task now also reads assignment.course to attribute
        # the resulting CreditUsageLog to a course/session, and that FK
        # assignment requires an actual Course row.
        course = Course.objects.create(name="Course", teacher=teacher)
        assignment = MagicMock()
        assignment.course = course

        response = self.processor.execute_graded_task(
            user=student,
            feature="Grading Assignment",
            task_type="grade_assignment",
            user_prompt="prompt",
            assignment=assignment,
        )

        self.assertIsNotNone(response)
        self.assertEqual(teacher.credit_wallet.total_remaining_credits(), 99500)
        # Every user gets a CreditWallet automatically on registration (see
        # users/signals.py) — the real guarantee this test cares about is
        # that the student's own wallet, if any, was never touched.
        student_wallet = CreditWallet.objects.filter(user=student).first()
        if student_wallet is not None:
            self.assertEqual(student_wallet.total_remaining_credits(), 0)

    def test_student_without_assignment_raises_value_error(self):
        student = self._make_user(UserTypes.STUDENT, "noassign@example.com")
        with self.assertRaises(ValueError):
            self.processor.execute_graded_task(
                user=student,
                feature="Grading Assignment",
                task_type="grade_assignment",
                user_prompt="prompt",
                assignment=None,
            )

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_student_submission_denied_when_teacher_lacks_access(self, mock_ai_model):
        teacher = self._make_teacher_with_credits(credits=0)  # out of credits
        student = self._make_user(UserTypes.STUDENT, "student2@example.com")

        assignment = MagicMock()
        assignment.course.teacher = teacher

        with self.assertRaises(AIFeatureNotAvailableError) as ctx:
            self.processor.execute_graded_task(
                user=student,
                feature="Grading Assignment",
                task_type="grade_assignment",
                user_prompt="prompt",
                assignment=assignment,
            )
        self.assertIn("assignment's teacher", str(ctx.exception))
        mock_ai_model.assert_not_called()

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_school_admin_allowed_feature_succeeds(self, mock_ai_model):
        mock_ai_model.return_value = make_ai_response(tokens=500)
        plan = self._make_plan(PlanType.POWER_LICENSE, category=PlanCategory.LICENSE)
        admin = self._make_user(UserTypes.SCHOOL_ADMIN, "admin@example.com")
        license_sub = self._make_license(plan, admin)
        self._make_allocation(license_sub, admin, is_admin=True)
        self._give_credits(admin, 100000)

        response = self.processor.execute_graded_task(
            user=admin,
            feature="Weekly Course Summary",
            task_type="weekly_course_summary",
            user_prompt="prompt",
        )
        self.assertIsNotNone(response)

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_school_admin_disallowed_feature_denied(self, mock_ai_model):
        plan = self._make_plan(PlanType.POWER_LICENSE, category=PlanCategory.LICENSE)
        admin = self._make_user(UserTypes.SCHOOL_ADMIN, "admin2@example.com")
        license_sub = self._make_license(plan, admin)
        self._make_allocation(license_sub, admin, is_admin=True)
        self._give_credits(admin, 100000)

        with self.assertRaises(AIFeatureNotAvailableError):
            self.processor.execute_graded_task(
                user=admin,
                feature="Grading Assignment",  # admins can't grade
                task_type="grade_assignment",
                user_prompt="prompt",
            )
        mock_ai_model.assert_not_called()

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_super_admin_bypasses_everything_unmetered(self, mock_ai_model):
        mock_ai_model.return_value = make_ai_response(tokens=999999)
        superadmin = self._make_user(UserTypes.SUPER_ADMIN, "super@example.com")
        # Deliberately no subscription, no wallet, no credits at all.

        response = self.processor.execute_graded_task(
            user=superadmin,
            feature="Superadmin Custom AI Prompt",
            task_type="custom_ai_prompt:superadmin",
            user_prompt="prompt",
        )
        self.assertIsNotNone(response)
        # Every user gets a CreditWallet automatically on registration (see
        # users/signals.py) — the real guarantee is that the superadmin's
        # unmetered bypass never touches/decrements it, if one exists.
        superadmin_wallet = CreditWallet.objects.filter(user=superadmin).first()
        if superadmin_wallet is not None:
            self.assertEqual(superadmin_wallet.total_remaining_credits(), 0)

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_response_schema_reaches_ai_model_for_super_admin_path(self, mock_ai_model):
        mock_ai_model.return_value = make_ai_response(tokens=999999)
        superadmin = self._make_user(UserTypes.SUPER_ADMIN, "super2@example.com")
        schema = {"name": "test_schema", "strict": True, "schema": {"type": "object"}}

        self.processor.execute_graded_task(
            user=superadmin,
            feature="Superadmin Custom AI Prompt",
            task_type="custom_ai_prompt:superadmin",
            user_prompt="prompt",
            response_schema=schema,
        )

        mock_ai_model.assert_called_once_with(
            None, "prompt", None, None, True, schema, sub_models=None
        )

    def test_unrecognized_user_type_raises_clean_value_error(self):
        """
        Regression test: previously, an unrecognized user_type fell
        through every branch with `wallet`/`target_teacher` unbound,
        raising an opaque UnboundLocalError several lines later instead of
        a clear error at the dispatch point.
        """
        weird_user = self._make_user(UserTypes.TEACHER, "weird@example.com")
        weird_user.user_type = "SOMETHING_UNEXPECTED"
        # Not saved to DB deliberately - we only need the in-memory attribute
        # for execute_graded_task's dispatch, which reads user.user_type
        # directly off the passed-in object.

        with self.assertRaises(ValueError) as ctx:
            self.processor.execute_graded_task(
                user=weird_user,
                feature="Grading Assignment",
                task_type="grade_assignment",
                user_prompt="prompt",
            )
        self.assertIn("Unsupported user_type", str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, UnboundLocalError)


class CreditUsageLogSchoolSnapshotTests(ExecuteGradedTaskTestBase):
    """
    CreditUsageLog.school must be resolved from the BILLED user's school
    at the moment of consumption, and never change retroactively once
    written — even if that user is later reassigned to a different school.
    This is what makes school-level token reporting immune to a teacher
    transferring schools after the fact (see classrooms.views.SchoolViewSet
    tokens_used computations, and CreditUsageLog.school's docstring).
    """

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_teacher_call_snapshots_teachers_school(self, mock_ai_model):
        mock_ai_model.return_value = make_ai_response(tokens=500)
        teacher = self._make_teacher_with_credits()
        school = self._make_school()
        teacher.school = school
        teacher.save()

        self.processor.execute_graded_task(
            user=teacher,
            feature="Grading Assignment",
            task_type="grade_assignment",
            user_prompt="prompt",
        )

        log = CreditUsageLog.objects.get(wallet__user=teacher)
        self.assertEqual(log.school_id, school.id)

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_school_admin_call_snapshots_admins_own_school(self, mock_ai_model):
        mock_ai_model.return_value = make_ai_response(tokens=300)
        plan = self._make_plan(PlanType.POWER_LICENSE, category=PlanCategory.LICENSE)
        admin = self._make_user(UserTypes.SCHOOL_ADMIN, "admin@example.com")
        license_sub = self._make_license(plan, admin)
        self._make_allocation(license_sub, admin, is_admin=True)
        self._give_credits(admin, 100000)
        # _make_license doesn't set the admin's own `school` FK - only the
        # LicenseSubscription's - so wire it up explicitly for this test.
        admin.school = license_sub.school
        admin.save()

        self.processor.execute_graded_task(
            user=admin,
            feature="Weekly Course Summary",
            task_type="weekly_course_summary",
            user_prompt="prompt",
        )

        log = CreditUsageLog.objects.get(wallet__user=admin)
        self.assertEqual(log.school_id, license_sub.school_id)

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_student_submission_snapshots_teachers_school_not_students(
        self, mock_ai_model
    ):
        mock_ai_model.return_value = make_ai_response(tokens=200)
        school = self._make_school()
        teacher = self._make_teacher_with_credits()
        teacher.school = school
        teacher.save()
        student = self._make_user(UserTypes.STUDENT, "student-snapshot@example.com")

        course = Course.objects.create(name="Course", teacher=teacher)
        assignment = MagicMock()
        assignment.course = course

        self.processor.execute_graded_task(
            user=student,
            feature="Grading Assignment",
            task_type="grade_assignment",
            user_prompt="prompt",
            assignment=assignment,
        )

        log = CreditUsageLog.objects.get(wallet__user=teacher)
        # Billed to the TEACHER's school, not the student's (students have
        # no school of their own in this flow).
        self.assertEqual(log.school_id, school.id)

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_teacher_with_no_school_snapshots_null(self, mock_ai_model):
        mock_ai_model.return_value = make_ai_response(tokens=100)
        teacher = self._make_teacher_with_credits()  # individual, no school

        self.processor.execute_graded_task(
            user=teacher,
            feature="Grading Assignment",
            task_type="grade_assignment",
            user_prompt="prompt",
        )

        log = CreditUsageLog.objects.get(wallet__user=teacher)
        self.assertIsNone(log.school_id)

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_school_transfer_does_not_retroactively_move_earlier_usage(
        self, mock_ai_model
    ):
        """
        The core regression this field exists to fix: usage logged while a
        teacher was at School A must stay attributed to School A forever,
        even after the teacher moves to School B and generates new usage
        there.
        """
        mock_ai_model.return_value = make_ai_response(tokens=500)
        school_a = self._make_school()
        school_b = School.objects.create(name="School B")
        teacher = self._make_teacher_with_credits(credits=200000)
        teacher.school = school_a
        teacher.save()

        self.processor.execute_graded_task(
            user=teacher,
            feature="Grading Assignment",
            task_type="grade_assignment",
            user_prompt="prompt one",
        )

        # Teacher transfers to School B.
        teacher.school = school_b
        teacher.save()

        mock_ai_model.return_value = make_ai_response(tokens=300)
        self.processor.execute_graded_task(
            user=teacher,
            feature="Grading Assignment",
            task_type="grade_assignment",
            user_prompt="prompt two",
        )

        logs = list(
            CreditUsageLog.objects.filter(wallet__user=teacher).order_by("created_at")
        )
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0].school_id, school_a.id)
        self.assertEqual(logs[1].school_id, school_b.id)


class RetryWrapperRegressionTests(ExecuteGradedTaskTestBase):
    """
    Phase 10 - the 6 outer `*_with_retry` wrappers must fail fast on
    deterministic access/credit denials (not burn 3 retries on a failure
    retrying can never fix), while STILL retrying genuinely transient
    failures exactly as before. Both directions matter equally - it's easy
    to fix the first and accidentally break the second.
    """

    @patch.object(AIProcessor, "execute_graded_task")
    def test_feature_denial_not_retried(self, mock_execute):
        mock_execute.side_effect = AIFeatureNotAvailableError("blocked by tier")
        teacher = self._make_teacher_with_credits()

        with self.assertRaises(AIFeatureNotAvailableError):
            self.processor.generate_assignment_from_prompt_with_retry(
                user=teacher, prompt="write an assignment", max_retries=3
            )

        self.assertEqual(mock_execute.call_count, 1)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_insufficient_credits_not_retried(self, mock_execute):
        mock_execute.side_effect = InsufficientCreditsError("out of credits")
        teacher = self._make_teacher_with_credits()

        with self.assertRaises(InsufficientCreditsError):
            self.processor.generate_assignment_from_prompt_with_retry(
                user=teacher, prompt="write an assignment", max_retries=3
            )

        self.assertEqual(mock_execute.call_count, 1)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_transient_failure_still_retries_as_before(self, mock_execute):
        """
        The regression check that matters most: a genuinely transient
        failure (e.g. a flaky call, here just a generic Exception) must
        STILL be retried up to max_retries times, exactly as it was before
        the AIFeatureNotAvailableError/InsufficientCreditsError fast-path
        was added.
        """
        mock_execute.side_effect = Exception("transient network blip")
        teacher = self._make_teacher_with_credits()

        with self.assertRaises(Exception):
            self.processor.generate_assignment_from_prompt_with_retry(
                user=teacher, prompt="write an assignment", max_retries=3
            )

        self.assertEqual(mock_execute.call_count, 3)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_custom_ai_prompt_retry_fails_fast_on_denial(self, mock_execute):
        mock_execute.side_effect = AIFeatureNotAvailableError("blocked")
        admin = self._make_user(UserTypes.SUPER_ADMIN, "retrysuper@example.com")

        with self.assertRaises(AIFeatureNotAvailableError):
            self.processor.custom_ai_prompt_retry(
                user=admin,
                user_prompt="hello",
                role=UserTypes.SUPER_ADMIN,
                feature="Superadmin Custom AI Prompt",
                task_type="custom_ai_prompt:superadmin",
                max_retries=3,
            )

        self.assertEqual(mock_execute.call_count, 1)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_custom_ai_prompt_retry_still_retries_transient(self, mock_execute):
        mock_execute.side_effect = Exception("flaky")
        admin = self._make_user(UserTypes.SUPER_ADMIN, "retrysuper2@example.com")

        with self.assertRaises(Exception):
            self.processor.custom_ai_prompt_retry(
                user=admin,
                user_prompt="hello",
                role=UserTypes.SUPER_ADMIN,
                feature="Superadmin Custom AI Prompt",
                task_type="custom_ai_prompt:superadmin",
                max_retries=3,
            )

        self.assertEqual(mock_execute.call_count, 3)
