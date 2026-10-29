"""
Free-plan activation attack replay: records outcomes, asserts nothing.

Runs unchanged against the pre-fix tree (beta b744c9f) and the fixed tree, so
the same scenarios prove the vulnerability first and its closure second. It
imports nothing that exists only after the fix. Users are created through
CustomUser.objects.create_user, which fires the real signup signal (automatic
TRIAL for teachers). Plans mirror the local database on 2026-09-17.

Usage, from a worktree root:
    PYTHONPATH=docs/evidence/free_plan_activation \\
        python manage.py test attack_replay --settings=settings_worktree \\
        --noinput 2>&1 | tee <log>

Each scenario prints a line: REPLAY <id> <verdict> <json details>.
  verdict EXPLOITED: the attack changed subscription/credit state
  verdict REFUSED:   every request was rejected and state is unchanged
"""

import json
import uuid
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from billing.context import (
    clear_license_invitation_context,
    set_license_invitation_context,
)
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import SubscriptionService
from classrooms.models import School
from users.models import CustomUser, UserTypes

PASSWORD = "Replay-pass-123!"  # pragma: allowlist secret


def state(user):
    """Entitlement state: subscriptions, bucket grants and GRANT ledger rows.

    Deliberately excludes used_credits and CONSUME rows, because S01 spends
    the user's own credits between requests; spending is not the attack.
    """
    return (
        list(
            UserSubscription.objects.filter(user=user)
            .order_by("created_at", "id")
            .values_list("id", "plan__name", "is_active")
        ),
        list(
            CreditBucket.objects.filter(wallet__user=user)
            .order_by("created_at", "id")
            .values_list("id", "bucket_type", "total_credits")
        ),
        CreditLedger.objects.filter(
            user_id=user.id, ledger_type=CreditLedgerType.GRANT
        ).count(),
    )


def live_balance(user):
    return sum(
        b.remaining_credits
        for b in CreditBucket.objects.filter(
            wallet__user=user, expires_at__gt=timezone.now()
        )
    )


class AttackReplay(APITestCase):
    def setUp(self):
        def plan(name, credits, price="0.00", **kw):
            fields = {
                "category": PlanCategory.INDIVIDUAL,
                "tier": PlanTier.STANDARD,
                "interval": BillingInterval.MONTHLY,
                "is_active": True,
            }
            fields.update(kw)
            return SubscriptionPlan.objects.create(
                name=name,
                monthly_credits=credits,
                price_cents=Decimal(price),
                **fields,
            )

        self.beta = plan(PlanType.BETA, 10_000_000, carry_over_expiry_months=1)
        self.trial = plan(
            PlanType.TRIAL,
            5_000_000,
            tier=PlanTier.TRIAL,
            interval=BillingInterval.NONE,
        )
        self.benchmark = plan("Grading Benchmark Plan", 5_000_000)
        self.inactive_free = plan(PlanType.CUSTOM, 99_000_000, is_active=False)
        self.pro = plan(
            PlanType.PRO, 20_000_000, "2499.00", stripe_price_id="price_pro"
        )
        self.standard = plan(
            PlanType.STANDARD, 10_000_000, "1499.00", stripe_price_id="price_std"
        )
        self.license_plan = plan(
            PlanType.CUSTOM_LICENSE_STARTER,
            40_000_000,
            category=PlanCategory.LICENSE,
            tier=PlanTier.CUSTOM,
        )

    def user(self, user_type, **extra):
        return CustomUser.objects.create_user(
            email=f"{user_type.lower()}-{uuid.uuid4().hex[:8]}@example.com",
            password=PASSWORD,
            user_type=user_type,
            is_active=True,
            **extra,
        )

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def post(self, client, route, user, plan_id):
        return client.post(
            reverse(route), {"user": str(user.id), "plan": str(plan_id)}, format="json"
        ).status_code

    def report(self, scenario, before, after, **details):
        verdict = "EXPLOITED" if before != after else "REFUSED"
        print(f"\nREPLAY {scenario} {verdict} {json.dumps(details, default=str)}")

    def spend_all(self, user):
        balance = live_balance(user)
        if balance:
            CreditWallet.objects.get(user=user).consume_credits(balance, feature="x")
        return balance

    def test_s01_ten_beta_requests_with_spending(self):
        teacher = self.user(UserTypes.TEACHER)
        client = self.client_for(teacher)
        before = state(teacher)
        codes, spent = [], 0
        for _ in range(10):
            codes.append(
                self.post(client, "user-subscription-list", teacher, self.beta.pk)
            )
            spent += self.spend_all(teacher)
        self.report(
            "S01-10x-BETA-user-subscriptions",
            before,
            state(teacher),
            codes=codes,
            raw_credits_spent=spent,
        )

    def test_s02_thirty_rapid_requests(self):
        teacher = self.user(UserTypes.TEACHER)
        client = self.client_for(teacher)
        before = state(teacher)
        codes = [
            self.post(client, "user-subscription-list", teacher, self.beta.pk)
            for _ in range(30)
        ]
        self.report(
            "S02-30x-rapid",
            before,
            state(teacher),
            status_counts={c: codes.count(c) for c in set(codes)},
            beta_subscriptions=UserSubscription.objects.filter(
                user=teacher, plan=self.beta
            ).count(),
        )

    def test_s03_paid_subscriber_to_beta(self):
        teacher = self.user(UserTypes.TEACHER)
        paid = SubscriptionService.activate_subscription(teacher, self.pro)
        paid.stripe_subscription_id = "sub_replay_paid"
        paid.stripe_status = StripeSubscriptionStatus.ACTIVE
        paid.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )
        before = state(teacher)
        codes = [
            self.post(self.client_for(teacher), route, teacher, self.beta.pk)
            for route in ("user-subscription-list", "subscription-list")
        ]
        paid.refresh_from_db()
        active = UserSubscription.objects.filter(user=teacher, is_active=True).first()
        self.report(
            "S03-paid-to-BETA",
            before,
            state(teacher),
            codes=codes,
            paid_row_still_active=paid.is_active,
            active_plan=active.plan.name if active else None,
            active_row_stripe_id=active.stripe_subscription_id if active else None,
        )

    def test_s04_known_plan_id_and_inactive_plan(self):
        teacher = self.user(UserTypes.TEACHER)
        client = self.client_for(teacher)
        for label, plan in (
            ("S04-direct-id-benchmark", self.benchmark),
            ("S05-inactive-free-plan", self.inactive_free),
            ("S06-TRIAL-plan", self.trial),
        ):
            before = state(teacher)
            codes = [
                self.post(client, route, teacher, plan.pk)
                for route in ("user-subscription-list", "subscription-list")
            ]
            self.report(label, before, state(teacher), codes=codes)

    def test_s07_school_admin_via_subscription(self):
        admin = self.user(UserTypes.SCHOOL_ADMIN)
        client = self.client_for(admin)
        before = state(admin)
        codes = {
            plan.name: self.post(client, "subscription-list", admin, plan.pk)
            for plan in (self.trial, self.benchmark, self.beta)
        }
        self.report(
            "S07-school-admin-subscription-route",
            before,
            state(admin),
            codes=codes,
            buckets=list(
                CreditBucket.objects.filter(wallet__user=admin).values_list(
                    "bucket_type", "total_credits"
                )
            ),
        )

    def test_s08_licensed_teacher_license_plan_from_me(self):
        school = School.objects.create(name="Replay School")
        admin = self.user(UserTypes.SCHOOL_ADMIN, school=school)
        set_license_invitation_context(True)
        try:
            teacher = self.user(UserTypes.TEACHER, school=school)
        finally:
            clear_license_invitation_context()
        license_sub = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=self.license_plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timezone.timedelta(days=30),
            is_active=True,
            max_seats=5,
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=teacher,
            is_active=True,
            monthly_allocation=1000,
        )
        client = self.client_for(teacher)
        me = client.get(reverse("subscription-get-my-subscription"))
        plan_id = me.data.get("plan", {}).get("id") if me.status_code == 200 else None
        before = state(teacher)
        codes = [
            self.post(client, route, teacher, plan_id)
            for route in ("user-subscription-list", "subscription-list")
        ]
        self.report(
            "S08-licensed-teacher-license-plan",
            before,
            state(teacher),
            me_status=me.status_code,
            plan_id_read_from_me=plan_id,
            codes=codes,
        )

    def test_s09_plan_discovery(self):
        teacher = self.user(UserTypes.TEACHER)
        client = self.client_for(teacher)
        listed = sorted(
            r["name"] for r in client.get(reverse("subscription-plan")).data
        )
        listed_vs = sorted(
            r["name"]
            for r in client.get(reverse("subscription-plan-list")).data["results"]
        )
        hidden = {
            self.beta.name,
            self.trial.name,
            self.benchmark.name,
            self.inactive_free.name,
            self.license_plan.name,
        }
        exposed = sorted(hidden & (set(listed) | set(listed_vs)))
        verdict = "EXPLOITED" if exposed else "REFUSED"
        print(
            f"\nREPLAY S09-plan-discovery {verdict} "
            + json.dumps(
                {
                    "subscription_plan": listed,
                    "subscription_plans": listed_vs,
                    "hidden_exposed": exposed,
                }
            )
        )

    def test_s10_carry_over_accumulation(self):
        SubscriptionPlan.objects.filter(pk=self.beta.pk).update(carry_over_percent=100)
        teacher = self.user(UserTypes.TEACHER)
        client = self.client_for(teacher)
        before = state(teacher)
        codes = [
            self.post(client, "user-subscription-list", teacher, self.beta.pk)
            for _ in range(5)
        ]
        self.report(
            "S10-carry-over-100pct-5x",
            before,
            state(teacher),
            codes=codes,
            live_balance_raw=live_balance(teacher),
        )
