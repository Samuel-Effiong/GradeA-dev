"""H-281: a credit refusal tells an individual teacher to top up, not to ask
a school administrator they don't have.

Owner-approved wording (2026-11-03), verbatim. INDIVIDUAL teachers only: a
TEACHER whose wallet no school license funds (`not is_under_license()`).
Licensed teachers, school admins, super admins and students keep what they
read today (students: the H-216/H-225 sentence).

The decision is made where the refusal is raised, from the BILLED user, and
carried on the refusal (`InsufficientCreditsError.individual`). Every
surface reads it through the existing variant mechanism of the reason-code
spec (DESIGN.md, approved by the SM at cbf1af408).

New names are imported inside the tests, so this file loads on code without
them and each test fails on its own.
"""

import base64
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.services import AIProcessor
from assignments.tasks import upload_answers_engine_async
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import (
    EmptyWalletError,
    InsufficientCreditsError,
    StudentEmptyWalletError,
)
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from billing.services import SubscriptionService
from billing.tests.test_refusal_handling import classroom, spied_gate
from classrooms.models import School
from students.exceptions import STUDENT_UPLOAD_NOT_PROCESSED
from students.item_results import item_result
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
)
from students.task_tracking import mark_processing_task_failure
from students.tests_upload_credit_door import PNG
from users.models import CustomUser, UserTypes
from users.permissions import HasCreditBalance

# ------------------------------------------------------------ the contract
# Literal on purpose: these are what clients read.

INDIVIDUAL_MESSAGE = (
    "There aren't enough AI credits to do this. Top up your credits, then " "try again."
)
INDIVIDUAL_REMEDIATION = "Top up credits, then try again."
SCHOOL_MESSAGE = (
    "There aren't enough AI credits to do this. Top up your credits or ask "
    "your school administrator, then try again."
)
SCHOOL_REMEDIATION = "Top up credits, or ask your school administrator, then try again."

# OWNER RULING (2026-11-03, via the SM): a teacher who still has a school_id
# but no active license (lapsed, or removed from the license) reads the
# INDIVIDUAL sentence. So is_under_license() is the final rule, and this
# constant pins it.
REMOVED_FROM_LICENSE_IS_INDIVIDUAL = True
REMOVED_MESSAGE, REMOVED_REMEDIATION = (
    (INDIVIDUAL_MESSAGE, INDIVIDUAL_REMEDIATION)
    if REMOVED_FROM_LICENSE_IS_INDIVIDUAL
    else (SCHOOL_MESSAGE, SCHOOL_REMEDIATION)
)

GATE_TEXT = (
    "Task requires ~25000 credits, but you only have 5000 credits. "
    "Please refill your wallet to continue"
)


# ---------------------------------------------------------------- fixtures


def _user(tag, user_type, **extra):
    return CustomUser.objects.create_user(
        email=f"h281-{tag}-{uuid.uuid4().hex[:8]}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=user_type,
        first_name=tag.replace("-", " ").title(),
        last_name="Credits",
        is_active=True,
        email_verified_at=timezone.now(),
        **extra,
    )


def _empty(user):
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    CreditBucket.objects.filter(wallet=wallet).delete()
    return wallet


def _fund(user, credits):
    wallet = _empty(user)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=credits,
        used_credits=0,
        expires_at=timezone.now() + timedelta(days=30),
    )
    return wallet


def individual_teacher(tag="individual"):
    """No school license funds this wallet."""
    teacher = _user(tag, UserTypes.TEACHER)
    _empty(teacher)
    assert not teacher.is_under_license()
    return teacher


def _license_plan():
    return SubscriptionPlan.objects.create(
        name=f"h281-license-{uuid.uuid4().hex[:8]}",
        category=PlanCategory.LICENSE,
        tier=PlanTier.PRO,
        interval=BillingInterval.MONTHLY,
        monthly_credits=20_000,
        is_active=True,
    )


def licensed_teacher(tag="licensed"):
    """A teacher of a school, funded by an active allocation under the
    school's active license. The wallet is emptied after the allocation."""
    school = School.objects.create(name=f"H281 School {uuid.uuid4().hex[:6]}")
    admin = _user(f"{tag}-admin", UserTypes.SCHOOL_ADMIN, school=school)
    license_ = LicenseSubscription.objects.create(
        school=school,
        admin_user=admin,
        plan=_license_plan(),
        billing_cycle_start=timezone.now(),
        billing_cycle_end=timezone.now() + timedelta(days=30),
        is_active=True,
    )
    teacher = _user(tag, UserTypes.TEACHER, school=school)
    SchoolCreditAllocation.objects.create(
        license_subscription=license_, user=teacher, monthly_allocation=0
    )
    _empty(teacher)
    assert teacher.is_under_license()
    return teacher


def removed_teacher(tag="removed"):
    """Was licensed; the allocation is now inactive. `school_id` stays."""
    teacher = licensed_teacher(tag)
    SchoolCreditAllocation.objects.filter(user=teacher).update(is_active=False)
    teacher = CustomUser.objects.get(pk=teacher.pk)
    assert teacher.school_id is not None
    assert not teacher.is_under_license()
    return teacher


def school_admin(tag="admin"):
    school = School.objects.create(name=f"H281 Admin School {uuid.uuid4().hex[:6]}")
    admin = _user(tag, UserTypes.SCHOOL_ADMIN, school=school)
    _empty(admin)
    return admin


def half_set_up_super_admin(tag="half-super"):
    """user_type SUPER_ADMIN with is_superuser unticked (H-19's account)."""
    user = _user(tag, UserTypes.SUPER_ADMIN, is_superuser=False)
    _empty(user)
    return user


class FakeView:
    __slots__ = ("kwargs",)

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _request(user):
    return SimpleNamespace(user=user)


def _png(name="a.png"):
    return SimpleUploadedFile(name, PNG, content_type="image/png")


# ----------------------------------------------------- (a) who is individual


class WhoIsAnIndividualTeacher(TestCase):
    def predicate(self, user):
        from billing.errors import is_individual_teacher

        return is_individual_teacher(user)

    def test_a_teacher_no_license_funds_is_individual(self):
        self.assertIs(self.predicate(individual_teacher()), True)

    def test_a_licensed_teacher_is_not(self):
        self.assertIs(self.predicate(licensed_teacher()), False)

    def test_a_school_admin_is_not(self):
        self.assertIs(self.predicate(school_admin()), False)

    def test_a_student_is_not(self):
        self.assertIs(self.predicate(_user("student", UserTypes.STUDENT)), False)

    def test_a_half_set_up_super_admin_is_not(self):
        self.assertIs(self.predicate(half_set_up_super_admin()), False)

    def test_a_teacher_removed_from_their_license_follows_the_owner_rule(self):
        self.assertIs(
            self.predicate(removed_teacher()), REMOVED_FROM_LICENSE_IS_INDIVIDUAL
        )

    def test_no_billed_user_is_not_individual(self):
        self.assertIs(self.predicate(None), False)

    def test_an_anonymous_user_is_not_individual(self):
        """require_ai_access tags with request.user, which can be anonymous
        (AutoGrader.tests_reason_codes.RequireAiAccessTests, E3 on c1821c3a:
        a 500)."""
        from django.contrib.auth.models import AnonymousUser

        self.assertIs(self.predicate(AnonymousUser()), False)


# ------------------------------------------- (b) each raise site tags it


class TheRaiseSitesTagTheRefusal(TestCase):
    """R1-R8, R10: `individual` is set from the billed user where the
    refusal is made. R9 (the single-upload door) is checked with the
    surfaces, as its refusal is a response, not an exception."""

    def refused(self, call):
        with self.assertRaises(InsufficientCreditsError) as caught:
            call()
        return caught.exception

    def test_r2_consume_credits_balance_individual(self):
        wallet = _empty(individual_teacher())
        self.assertIs(self.refused(lambda: wallet.consume_credits(10)).individual, True)

    def test_r2_consume_credits_balance_licensed(self):
        wallet = _empty(licensed_teacher())
        self.assertIs(
            self.refused(lambda: wallet.consume_credits(10)).individual, False
        )

    def test_r2_consume_credits_balance_school_admin(self):
        wallet = _empty(school_admin())
        self.assertIs(
            self.refused(lambda: wallet.consume_credits(10)).individual, False
        )

    def test_r1_consume_credits_blocked_individual(self):
        wallet = _fund(individual_teacher(), 1_000)
        CreditWallet.objects.filter(pk=wallet.pk).update(
            is_consumption_blocked=True, dispute_deficit_credits=5
        )
        self.assertIs(self.refused(lambda: wallet.consume_credits(10)).individual, True)

    def test_r1_consume_credits_blocked_licensed(self):
        wallet = _fund(licensed_teacher(), 1_000)
        CreditWallet.objects.filter(pk=wallet.pk).update(
            is_consumption_blocked=True, dispute_deficit_credits=5
        )
        self.assertIs(
            self.refused(lambda: wallet.consume_credits(10)).individual, False
        )

    def test_r3_consume_credits_defensive_recheck_individual(self):
        wallet = _empty(individual_teacher())
        with patch.object(CreditWallet, "total_remaining_credits", return_value=10**9):
            error = self.refused(lambda: wallet.consume_credits(10))
        self.assertIs(error.individual, True)

    def test_r3_consume_credits_defensive_recheck_licensed(self):
        wallet = _empty(licensed_teacher())
        with patch.object(CreditWallet, "total_remaining_credits", return_value=10**9):
            error = self.refused(lambda: wallet.consume_credits(10))
        self.assertIs(error.individual, False)

    def test_r4_has_credit_balance_individual(self):
        with self.assertRaises(EmptyWalletError) as caught:
            HasCreditBalance().has_permission(
                _request(individual_teacher()), FakeView()
            )
        self.assertIs(caught.exception.individual, True)

    def test_r4_has_credit_balance_licensed(self):
        with self.assertRaises(EmptyWalletError) as caught:
            HasCreditBalance().has_permission(_request(licensed_teacher()), FakeView())
        self.assertIs(caught.exception.individual, False)

    def test_r4_a_student_still_gets_the_student_refusal(self):
        teacher = individual_teacher()
        _, student, assignment = classroom(teacher)
        _empty(teacher)
        with self.assertRaises(StudentEmptyWalletError):
            HasCreditBalance().has_permission(
                _request(student), FakeView(assignment_id=assignment.id)
            )

    def _subscribed_then_drained(self):
        """An individual teacher with an active plan and nothing left: the
        access gate answers NO_CREDITS_REMAINING_REASON."""
        teacher = _user("subscribed", UserTypes.TEACHER)
        plan = SubscriptionPlan.objects.create(
            name=f"h281-ind-{uuid.uuid4().hex[:8]}",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            interval=BillingInterval.MONTHLY,
            monthly_credits=1000,
            carry_over_percent=0,
            is_active=True,
        )
        SubscriptionService.activate_subscription(teacher, plan)
        _empty(teacher)
        return CustomUser.objects.get(pk=teacher.pk)

    def test_r5_require_ai_access_individual(self):
        from billing.access_control import require_ai_access

        captured = {}

        def coded(error):
            captured["error"] = error
            return "refused"

        @require_ai_access
        def view(self, request):
            return "ran"

        with patch("billing.access_control.coded_response", side_effect=coded):
            result = view(None, _request(self._subscribed_then_drained()))

        self.assertEqual(result, "refused")
        self.assertIsInstance(captured["error"], InsufficientCreditsError)
        self.assertIs(captured["error"].individual, True)

    def test_r6_execute_graded_task_access_gate_individual(self):
        teacher = self._subscribed_then_drained()
        with patch.object(
            AIProcessor,
            "_AIProcessor__ai_model",
            side_effect=AssertionError("reached the provider"),
        ):
            error = self.refused(
                lambda: AIProcessor().execute_graded_task(
                    teacher,
                    None,
                    "h281",
                    system_prompt="system",
                    user_prompt="user",
                    prompt_version="h281",
                )
            )
        self.assertIs(error.individual, True)

    def _gate_with_balance(self, teacher, balance):
        """Past the access gate, with `balance` against a fixed estimate."""
        with patch(
            "ai_processor.services.can_user_access_ai", return_value=(True, None)
        ), patch.object(
            AIProcessor, "estimate_messages_cost", return_value=25_000
        ), patch.object(
            CreditWallet, "total_remaining_credits", return_value=balance
        ), patch.object(
            AIProcessor,
            "_AIProcessor__ai_model",
            side_effect=AssertionError("reached the provider"),
        ):
            return self.refused(
                lambda: AIProcessor().execute_graded_task(
                    teacher,
                    None,
                    "h281",
                    system_prompt="system",
                    user_prompt="user",
                    prompt_version="h281",
                )
            )

    def test_r7_execute_graded_task_below_estimate_individual(self):
        self.assertIs(
            self._gate_with_balance(individual_teacher(), 5_000).individual, True
        )

    def test_r7_execute_graded_task_below_estimate_licensed(self):
        self.assertIs(
            self._gate_with_balance(licensed_teacher(), 5_000).individual, False
        )

    def test_r8_execute_graded_task_nothing_left_individual(self):
        """Only reachable with an estimate at or below a zero balance."""
        teacher = individual_teacher()
        with patch(
            "ai_processor.services.can_user_access_ai", return_value=(True, None)
        ), patch.object(
            AIProcessor, "estimate_messages_cost", return_value=0
        ), patch.object(
            CreditWallet, "total_remaining_credits", return_value=0
        ), patch.object(
            AIProcessor,
            "_AIProcessor__ai_model",
            side_effect=AssertionError("reached the provider"),
        ):
            error = self.refused(
                lambda: AIProcessor().execute_graded_task(
                    teacher,
                    None,
                    "h281",
                    system_prompt="system",
                    user_prompt="user",
                    prompt_version="h281",
                )
            )
        self.assertIs(error.individual, True)

    def test_r10_a_parked_file_is_tagged_from_the_billed_teacher(self):
        from assignments.upload_door import parked_files

        teacher = individual_teacher()
        _fund(teacher, 5_000)
        with patch.object(AIProcessor, "estimate_messages_cost", return_value=60_000):
            parked = parked_files(teacher, None, [_png()], "p")
        self.assertIs(parked[0].individual, True)

    def test_r10_a_parked_file_of_a_licensed_teacher_is_not(self):
        from assignments.upload_door import parked_files

        teacher = licensed_teacher()
        _fund(teacher, 5_000)
        with patch.object(AIProcessor, "estimate_messages_cost", return_value=60_000):
            parked = parked_files(teacher, None, [_png()], "p")
        self.assertIs(parked[0].individual, False)


# ----------------------------------------------- (b) what each surface says


class WhatEachSurfaceSays(APITestCase):
    def assertReads(self, response, message, remediation):
        self.assertEqual(
            response.status_code, status.HTTP_402_PAYMENT_REQUIRED, response.data
        )
        self.assertEqual(response.data["reason_code"], "INSUFFICIENT_CREDITS")
        self.assertEqual(response.data["error"], message)
        self.assertEqual(response.data["remediation"], remediation)

    def grade_all(self, teacher):
        _, _, assignment = classroom(teacher)
        _empty(teacher)
        self.client.force_authenticate(user=teacher)
        return self.client.post(
            reverse("assignment-grade-all", args=[str(assignment.id)]), {}
        )

    # S1: HasCreditBalance's 402 through users.exceptions.
    def test_s1_an_individual_teacher_reads_the_individual_sentence(self):
        self.assertReads(
            self.grade_all(individual_teacher()),
            INDIVIDUAL_MESSAGE,
            INDIVIDUAL_REMEDIATION,
        )

    def test_s1_a_licensed_teacher_keeps_the_school_sentence(self):
        self.assertReads(
            self.grade_all(licensed_teacher()), SCHOOL_MESSAGE, SCHOOL_REMEDIATION
        )

    def test_s1_a_teacher_removed_from_their_license_follows_the_owner_rule(self):
        self.assertReads(
            self.grade_all(removed_teacher()), REMOVED_MESSAGE, REMOVED_REMEDIATION
        )

    # S2: an in-view refusal through refusal_response (custom-ai-prompt).
    def test_s2_custom_ai_prompt_individual(self):
        teacher = individual_teacher()
        _fund(teacher, 1_000)  # past HasCreditBalance, below the estimate
        self.client.force_authenticate(user=teacher)
        with patch(
            "ai_processor.services.can_user_access_ai", return_value=(True, None)
        ), spied_gate():
            response = self.client.post(
                reverse("teacher-admin-custom-ai-prompt"),
                {"prompt": "How are my classes?"},
                format="json",
            )
        self.assertReads(response, INDIVIDUAL_MESSAGE, INDIVIDUAL_REMEDIATION)

    def test_s2_custom_ai_prompt_licensed_teacher_keeps_the_school_sentence(self):
        # The route is teacher-only (403 for a school admin; E1 on e02721c5).
        teacher = licensed_teacher()
        _fund(teacher, 1_000)
        self.client.force_authenticate(user=teacher)
        with patch(
            "ai_processor.services.can_user_access_ai", return_value=(True, None)
        ), spied_gate():
            response = self.client.post(
                reverse("teacher-admin-custom-ai-prompt"),
                {"prompt": "How are my classes?"},
                format="json",
            )
        self.assertReads(response, SCHOOL_MESSAGE, SCHOOL_REMEDIATION)

    # S5: the upload door's own 402s.
    def batch(self, teacher, estimate):
        _, _, assignment = classroom(teacher)
        _fund(teacher, 5_000)
        self.client.force_authenticate(user=teacher)
        with patch.object(
            AIProcessor, "estimate_messages_cost", return_value=estimate
        ), patch("students.views.launch_processing_task") as launch:
            response = self.client.post(
                reverse(
                    "student-submission-batch-upload",
                    kwargs={"assignment_id": assignment.pk},
                ),
                {"answers": [_png("s0.png"), _png("s1.png")]},
                format="multipart",
            )
        launch.assert_not_called()
        return response

    def assertDoorReads(self, response, message):
        self.assertEqual(
            response.status_code, status.HTTP_402_PAYMENT_REQUIRED, response.data
        )
        self.assertEqual(response.data["code"], "insufficient_credits")
        self.assertEqual(response.data["error"], message)

    def test_s5_a_whole_batch_refusal_individual(self):
        self.assertDoorReads(
            self.batch(individual_teacher(), 60_000), INDIVIDUAL_MESSAGE
        )

    def test_s5_a_whole_batch_refusal_licensed(self):
        self.assertDoorReads(self.batch(licensed_teacher(), 60_000), SCHOOL_MESSAGE)

    def test_s5_the_single_upload_door_for_a_teacher_individual(self):
        from assignments.upload_door import upload_refusal_if_unaffordable

        teacher = individual_teacher()
        _fund(teacher, 5_000)
        with patch.object(AIProcessor, "estimate_messages_cost", return_value=60_000):
            response = upload_refusal_if_unaffordable(teacher, None, [_png()], "p")
        self.assertDoorReads(response, INDIVIDUAL_MESSAGE)

    def test_s5_the_single_upload_door_for_a_teacher_licensed(self):
        from assignments.upload_door import upload_refusal_if_unaffordable

        teacher = licensed_teacher()
        _fund(teacher, 5_000)
        with patch.object(AIProcessor, "estimate_messages_cost", return_value=60_000):
            response = upload_refusal_if_unaffordable(teacher, None, [_png()], "p")
        self.assertDoorReads(response, SCHOOL_MESSAGE)

    def test_s5_a_student_at_the_door_keeps_the_student_sentence(self):
        from assignments.upload_door import upload_refusal_if_unaffordable

        teacher = individual_teacher()
        _, student, assignment = classroom(teacher)
        _fund(teacher, 5_000)
        with patch.object(AIProcessor, "estimate_messages_cost", return_value=60_000):
            response = upload_refusal_if_unaffordable(
                student, assignment, [_png()], "p"
            )
        self.assertDoorReads(response, STUDENT_UPLOAD_NOT_PROCESSED)


class WhatAPolledItemSays(TestCase):
    """S4/S6/S7: the shared message resolver, a polled task's stored error,
    and its session-results remediation."""

    def failed_item(self, requested_by, error):
        task = BackgroundProcessingTask.objects.create(
            requested_by=requested_by,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            celery_task_id=str(uuid.uuid4()),
        )
        mark_processing_task_failure(task.id, error)
        task.refresh_from_db()
        return task

    def test_s4_describe_user_error_individual(self):
        from AutoGrader.error_messages import describe_user_error

        error = InsufficientCreditsError(GATE_TEXT, individual=True)
        self.assertEqual(describe_user_error(error), INDIVIDUAL_MESSAGE)

    def test_s4_describe_user_error_untagged_keeps_the_school_sentence(self):
        from AutoGrader.error_messages import describe_user_error

        self.assertEqual(
            describe_user_error(InsufficientCreditsError(GATE_TEXT)), SCHOOL_MESSAGE
        )

    def test_s6_s7_an_individual_refusal_is_stored_and_read_back(self):
        teacher = individual_teacher()
        task = self.failed_item(
            teacher, InsufficientCreditsError(GATE_TEXT, individual=True)
        )
        self.assertEqual(task.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(task.reason_code, "INSUFFICIENT_CREDITS")
        self.assertEqual(task.error, INDIVIDUAL_MESSAGE)
        entry = item_result(task, {})
        self.assertEqual(entry["message"], INDIVIDUAL_MESSAGE)
        self.assertEqual(entry["remediation"], INDIVIDUAL_REMEDIATION)

    def test_s6_s7_an_untagged_refusal_keeps_the_school_sentence(self):
        task = self.failed_item(licensed_teacher(), InsufficientCreditsError(GATE_TEXT))
        self.assertEqual(task.error, SCHOOL_MESSAGE)
        entry = item_result(task, {})
        self.assertEqual(entry["remediation"], SCHOOL_REMEDIATION)
        self.assertNotIn("message_variant", task.meta or {})

    def test_s6_a_student_requested_task_keeps_the_student_sentence(self):
        """The task turns a student's credit refusal into the student
        sentence before it is stored (H-216), even when the billed teacher
        is individual."""
        teacher = individual_teacher()
        _, student, assignment = classroom(teacher)
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=student,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=assignment,
            celery_task_id=str(uuid.uuid4()),
        )
        payload = {
            "name": "a.png",
            "content_type": "image/png",
            "content_b64": base64.b64encode(PNG).decode("ascii"),
        }
        with patch(
            "assignments.tasks.upload_answers_engine",
            side_effect=InsufficientCreditsError(GATE_TEXT, individual=True),
        ):
            upload_answers_engine_async.apply(
                args=(str(assignment.id), payload, "prompt", str(student.id)),
                kwargs={"processing_task_id": tracked.id},
            )
        tracked.refresh_from_db()
        self.assertEqual(tracked.error, STUDENT_UPLOAD_NOT_PROCESSED)


# -------------------------- (d) a half-set-up super admin: characterization


class AHalfSetUpSuperAdminAtTheCreditGate(APITestCase):
    """Documented, not changed (SM, 2026-11-03). Real super admins are
    unmetered; this account (is_superuser unticked) is not."""

    def test_has_credit_balance_refuses_with_the_unchanged_sentence(self):
        user = half_set_up_super_admin()
        with self.assertRaises(EmptyWalletError) as caught:
            HasCreditBalance().has_permission(_request(user), FakeView())
        self.assertNotIsInstance(caught.exception, StudentEmptyWalletError)
        self.assertIs(getattr(caught.exception, "individual", False), False)

    def test_the_402_body_is_the_unchanged_sentence(self):
        from AutoGrader.reason_codes import coded_response

        user = half_set_up_super_admin()
        try:
            HasCreditBalance().has_permission(_request(user), FakeView())
        except EmptyWalletError as error:
            response = coded_response(error)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(response.data["error"], SCHOOL_MESSAGE)
        self.assertEqual(response.data["remediation"], SCHOOL_REMEDIATION)

    def test_the_ai_gate_refuses_it_as_a_plan_refusal_not_credits(self):
        user = half_set_up_super_admin()
        with patch.object(
            AIProcessor,
            "_AIProcessor__ai_model",
            side_effect=AssertionError("reached the provider"),
        ), self.assertRaises(AIFeatureNotAvailableError):
            AIProcessor().execute_graded_task(
                user,
                None,
                "h281",
                system_prompt="system",
                user_prompt="user",
                prompt_version="h281",
            )
