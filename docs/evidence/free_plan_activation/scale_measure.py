"""
Gate 6 measurements for the free-plan activation fix. Records, asserts nothing.

Version-neutral (uses only endpoints and CustomUser.objects.create_user), so
the same numbers can be taken on the pre-fix tree and the fixed tree.

    PYTHONPATH=docs/evidence/free_plan_activation \\
        python manage.py test scale_measure --settings=settings_worktree --noinput

Prints lines: SCALE <measurement> <json>.
"""

import json
import statistics
import time
import tracemalloc
import uuid
from decimal import Decimal

from django.db import connection
from django.test.utils import override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from billing.models import (
    BillingInterval,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from users.models import CustomUser, UserTypes

CATALOG = [
    PlanType.STANDARD,
    PlanType.PRO,
    PlanType.POWER,
    PlanType.STANDARD_ANNUAL,
    PlanType.PRO_ANNUAL,
    PlanType.POWER_ANNUAL,
]


class QueryCounter:
    """Counts SQL statements via connection.execute_wrapper.

    CaptureQueriesContext is unusable here: the test client fires
    request_started, which resets connection.queries_log mid-capture, and the
    log is capped at 9000 entries, so both measurements read 0.
    """

    def __init__(self):
        self.count = 0

    def __call__(self, execute, sql, params, many, context):
        self.count += 1
        return execute(sql, params, many, context)

    def __enter__(self):
        self._cm = connection.execute_wrapper(self)
        self._cm.__enter__()
        return self

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)


def emit(name, payload):
    print(f"\nSCALE {name} {json.dumps(payload)}")


class ScaleMeasure(APITestCase):
    def setUp(self):
        for name in CATALOG:
            SubscriptionPlan.objects.create(
                name=name,
                category=PlanCategory.INDIVIDUAL,
                tier=PlanTier.STANDARD,
                interval=BillingInterval.MONTHLY,
                monthly_credits=10_000_000,
                price_cents=Decimal("1499.00"),
                stripe_price_id=f"price_{str(name).lower()}",
                is_active=True,
            )
        self.beta = SubscriptionPlan.objects.create(
            name=PlanType.BETA,
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.BETA,
            interval=BillingInterval.MONTHLY,
            monthly_credits=10_000_000,
            is_active=True,
        )
        SubscriptionPlan.objects.create(
            name=PlanType.TRIAL,
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.TRIAL,
            interval=BillingInterval.NONE,
            monthly_credits=5_000_000,
            is_active=True,
        )

    def add_internal_plans(self, count):
        SubscriptionPlan.objects.bulk_create(
            SubscriptionPlan(
                name=f"INTERNAL_{uuid.uuid4().hex[:10]}",
                category=PlanCategory.INDIVIDUAL,
                tier=PlanTier.CUSTOM,
                interval=BillingInterval.MONTHLY,
                monthly_credits=1,
                is_active=True,
            )
            for _ in range(count)
        )

    def measure_listing(self, client, url, repeats=30):
        with QueryCounter() as counter:
            response = client.get(url)
        timings = []
        for _ in range(repeats):
            start = time.perf_counter()
            client.get(url)
            timings.append((time.perf_counter() - start) * 1000)
        timings.sort()
        return {
            "status": response.status_code,
            "queries": counter.count,
            "bytes": len(response.content),
            "p50_ms": round(statistics.median(timings), 2),
            "p95_ms": round(timings[int(len(timings) * 0.95) - 1], 2),
        }

    def test_plan_listing_queries_at_two_sizes(self):
        teacher = CustomUser.objects.create_user(
            email="scale-teacher@example.com",
            password="Scale-pass-123!",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        client = APIClient()
        client.force_authenticate(user=teacher)
        urls = {
            "subscription_plan": reverse("subscription-plan"),
            "subscription_plans": reverse("subscription-plan-list"),
        }
        results = {}
        for label, extra in (("small_8_plans", 0), ("large_808_plans", 800)):
            self.add_internal_plans(extra)
            results[label] = {
                name: self.measure_listing(client, url) for name, url in urls.items()
            }
            results[label]["total_plans"] = SubscriptionPlan.objects.count()
        emit("plan_listing", results)

    def test_signup_burst_with_beta_on_signup(self):
        count = 500
        per_signup_queries, timings = [], []
        tracemalloc.start()
        with override_settings(USE_BETA_PLAN_ON_SIGNUP=True):
            for i in range(count):
                with QueryCounter() as counter:
                    start = time.perf_counter()
                    CustomUser.objects.create_user(
                        email=f"burst-{i}-{uuid.uuid4().hex[:6]}@example.com",
                        password="Scale-pass-123!",  # pragma: allowlist secret
                        user_type=UserTypes.TEACHER,
                        is_active=True,
                    )
                    timings.append((time.perf_counter() - start) * 1000)
                per_signup_queries.append(counter.count)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        timings.sort()
        teachers = CustomUser.objects.filter(email__startswith="burst-")
        beta_counts = [
            UserSubscription.objects.filter(user=t, plan=self.beta).count()
            for t in teachers
        ]
        emit(
            "signup_burst",
            {
                "signups": count,
                "queries_per_signup_min": min(per_signup_queries),
                "queries_per_signup_max": max(per_signup_queries),
                "queries_first_10": per_signup_queries[:10],
                "queries_last_10": per_signup_queries[-10:],
                "p50_ms": round(statistics.median(timings), 2),
                "p95_ms": round(timings[int(count * 0.95) - 1], 2),
                "peak_traced_memory_kb": peak // 1024,
                "users_with_exactly_one_beta": beta_counts.count(1),
                "users_with_more_than_one_beta": sum(1 for c in beta_counts if c > 1),
                "users_with_no_beta": beta_counts.count(0),
            },
        )
