"""Test helpers for the credit-hold service (Epic B1a, plan 08 §3.2).

`holds()` imports billing.holds when a test runs, so a missing module fails
each test on its own instead of the whole module at import.
"""

import importlib
import uuid
from datetime import timedelta

from django.utils import timezone

from billing.models import AIJob, CreditBucket, CreditBucketType, CreditWallet
from students.models import BackgroundProcessingTask
from users.models import CustomUser, UserTypes


def holds():
    return importlib.import_module("billing.holds")


def funded_wallet(email, credits):
    """A teacher's wallet holding exactly `credits` spendable credits."""
    user = CustomUser.objects.create_user(
        email=email,
        password="testpass123",  # pragma: allowlist secret  # nosec B106
        user_type=UserTypes.TEACHER,
    )
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    if credits:
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=credits,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
    return wallet


def make_job(wallet, job_type="SINGLE_CALL", **over):
    fields = {
        "job_type": job_type,
        "wallet": wallet,
        "requested_by": wallet.user,
        "trace_id": uuid.uuid4(),
        "total_items": 1,
    }
    fields.update(over)
    return AIJob.objects.create(**fields)


def make_item(job, **over):
    fields = {
        "requested_by": job.requested_by,
        "task_type": "answer_extraction",
        "job": job,
    }
    fields.update(over)
    return BackgroundProcessingTask.objects.create(**fields)


def fresh(obj):
    return type(obj).objects.get(pk=obj.pk)


def remaining(wallet):
    return fresh(wallet).total_remaining_credits()


def ai_ready_teacher(email, credits):
    """A teacher who passes can_user_access_ai (active individual PRO
    subscription) with exactly `credits` in one MONTHLY bucket."""
    from billing.models import (
        BillingInterval,
        PlanCategory,
        PlanTier,
        SubscriptionPlan,
        UserSubscription,
    )

    teacher = CustomUser.objects.create_user(
        email=email,
        password="testpass123",  # pragma: allowlist secret  # nosec B106
        user_type=UserTypes.TEACHER,
        is_active=True,
    )
    plan = SubscriptionPlan.objects.create(
        name=f"plan-{teacher.id}",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        interval=BillingInterval.MONTHLY,
        monthly_credits=20_000,
        carry_over_percent=25,
        is_active=True,
    )
    now = timezone.now()
    UserSubscription.objects.create(
        user=teacher,
        plan=plan,
        is_active=True,
        billing_cycle_start=now,
        billing_cycle_end=now + timedelta(days=30),
        is_trial=False,
        auto_renew=True,
    )
    wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=credits,
        used_credits=0,
        expires_at=now + timedelta(days=30),
    )
    return teacher


def ai_response(tokens):
    """A provider response whose usage is `tokens`."""
    from unittest.mock import MagicMock

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = '{"result": "ok"}'
    response.usage.total_tokens = tokens
    return response


PROMPT = "Grade this."


def estimate_for(prompt=PROMPT):
    """What execute_graded_task estimates for a call with this user prompt."""
    from ai_processor.services import ai_processor

    return ai_processor.estimate_messages_cost(prompt, None, None)


def call_ai(teacher, *, prompt=PROMPT):
    """One billed call through the real execute_graded_task."""
    from ai_processor.services import ai_processor

    return ai_processor.execute_graded_task(
        teacher,
        "Grading Assignment",
        "grade_assignment",
        user_prompt=prompt,
        prompt_version="b1a-test-v1",
    )


# --------------------------------------------------------------------------
# B1b-2 (NOTES §2.10): the B3 flag-on twins
# --------------------------------------------------------------------------


def fund(user, credits):
    """Add `credits` in one MONTHLY bucket to `user`'s wallet."""
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=credits,
        used_credits=0,
        expires_at=timezone.now() + timedelta(days=30),
    )
    return wallet


def real_billed_call(teacher, *, prompt=PROMPT):
    """One billed call through the real AIProcessor.execute_graded_task,
    called on the class, so it is real even where a test has patched the
    shared ai_processor instance's method."""
    from ai_processor.services import AIProcessor, ai_processor

    return AIProcessor.execute_graded_task(
        ai_processor,
        teacher,
        "Grading Assignment",
        "grade_assignment",
        user_prompt=prompt,
        prompt_version="b1b2-twin",
    )


def assert_hold_ledger(test, user, *, kept=None, kept_count=None):
    """The flag-on ledger shape a B3 twin asserts (08b §5.2):

    - no usage row is ever refunded (a failed call is never charged, so
      nothing needs refunding);
    - the usage rows are exactly what settle charged: `kept` (the sorted
      amounts) or `kept_count` (how many, where the amount is the
      provider's token count through the real pricing);
    - nothing is left reserved on the wallet: every hold was settled or
      returned;
    - and a hold was taken at all, so the twin is not vacuous.
    """
    from billing.models import CreditUsageLog

    # Not vacuous: a hold was really taken (a twin whose billed call never
    # ran would pass every check below).
    test.assertTrue(
        AIJob.objects.filter(wallet__user=user, reserved_credits__gt=0).exists(),
        "no hold was ever taken",
    )
    logs = list(CreditUsageLog.objects.filter(wallet__user=user))
    test.assertEqual(
        sorted(log.amount for log in logs if log.is_refunded),
        [],
        "a refund row was written with the flag on",
    )
    charged = sorted(log.amount for log in logs if not log.is_refunded)
    if kept is not None:
        test.assertEqual(charged, sorted(kept), "the charges settle made")
    if kept_count is not None:
        test.assertEqual(len(charged), kept_count, "the number of settle charges")
    test.assertEqual(
        CreditWallet.objects.get(user=user).reserved_credits,
        0,
        "a hold was left reserved",
    )
