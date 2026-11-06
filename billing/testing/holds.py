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
