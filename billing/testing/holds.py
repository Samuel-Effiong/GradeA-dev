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
