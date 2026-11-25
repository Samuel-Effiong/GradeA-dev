"""Step 4 (owner go and answers, 2026-11-25): the conversion page numbers.

All for individual teachers only; "paid" (owner Q1, option A) is a
Stripe-billed individual plan: category INDIVIDUAL, tier STANDARD, PRO, POWER
or CUSTOM, not a trial, active, stripe_subscription_id set (past_due counts
while active).

1. paying_users_count: distinct users with such a subscription now.
2. new_trial_signups_count: trials started in the last 30 days AND on or after
   settings.CONVERSION_START_DATE (env, default 2026-11-25, 00:00 UTC). A
   trial's start is its TRIAL credit bucket's created_at.
3. trial_conversion_*: of the trials started on or after the start date, how
   many users are paying now (numerator, denominator, percent; 0 when none).
5a. churn_30d_*: base = users paying at now - 30 days, lost = base users not
   paying now. The start date does NOT apply to churn (owner Q2, option A).
5b. beta-chart/churn-monthly: the same for the last 12 calendar months, oldest
   first; the current month is partial; months that begin before the first
   reliable subscription history (BillingSettings.subscription_history_from)
   have null figures and no_data true (owner Q3).

History: UserSubscription.paid_since and ended_at, stamped on save from now
on (the guard below keeps bulk writes honest) and backfilled once by
migration 0092.

    python manage.py test billing.tests.test_conversion_numbers
"""

import ast
import importlib
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

from django.apps import apps as django_apps
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from freezegun import freeze_time
from rest_framework.test import APITestCase

from audit import history
from audit.tests_history_guard import production_files
from billing.models import (
    BillingInterval,
    BillingSettings,
    CreditBucket,
    CreditBucketType,
    PlanCategory,
    PlanTier,
    StripeEvent,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import SubscriptionService
from users.models import CustomUser, UserTypes

UTC = dt_timezone.utc
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
PW = "Conversion-numbers-1"  # pragma: allowlist secret
MIGRATION = "billing.migrations.0092_subscription_paid_since_ended_at"


def at(month, day, hour=12, year=2026):
    return datetime(year, month, day, hour, 0, tzinfo=UTC)


def make_plan(name, tier, category=PlanCategory.INDIVIDUAL):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=category,
        tier=tier,
        interval=BillingInterval.MONTHLY,
        price_cents=999,
        monthly_credits=10_000_000,
        stripe_price_id=f"price_conv_{name.lower()}",
        is_active=True,
    )


class Plans:
    """The catalog the tests need. TRIAL only where a trial is wanted: with
    no TRIAL plan the sign-up signal starts no trial."""

    def make_plans(self, trial=False):
        self.standard = make_plan("STANDARD", PlanTier.STANDARD)
        self.pro = make_plan("PRO", PlanTier.PRO)
        self.beta = make_plan("BETA", PlanTier.BETA)
        self.license = make_plan(
            "PRO_LICENSE", PlanTier.PRO, category=PlanCategory.LICENSE
        )
        if trial:
            self.trial_plan = make_plan("TRIAL", PlanTier.TRIAL)

    made = 0

    def teacher(self, tag=None):
        Plans.made += 1
        return CustomUser.objects.create_user(
            email=f"conv.{tag or Plans.made}.{Plans.made}@x.example",
            password=PW,
            user_type=UserTypes.TEACHER,
            is_active=True,
        )

    def sub(self, user, plan, since, ended=None, stripe=True, **fields):
        """A subscription row with its history set directly (as the
        backfill would leave it)."""
        Plans.made += 1
        if ended is None:
            # One active subscription per user: a trial in the way ends.
            UserSubscription.objects.filter(user=user, is_active=True).update(
                is_active=False, is_trial=False
            )
        row = UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=ended is None,
            is_trial=False,
            billing_cycle_start=since,
            billing_cycle_end=since + timedelta(days=30),
            stripe_subscription_id=f"sub_conv_{Plans.made}" if stripe else None,
            **fields,
        )
        UserSubscription.objects.filter(pk=row.pk).update(
            paid_since=since, ended_at=ended
        )
        row.refresh_from_db()
        return row


STAMPS: tuple[str, ...] = ("paid_since", "ended_at")
MARKER: str = "subscription_history_from"


def stamp(row):
    """(paid_since, ended_at) as stored."""
    return UserSubscription.objects.filter(pk=row.pk).values_list(*STAMPS).get()


def history_marker():
    return BillingSettings.objects.filter(pk=1).values_list(MARKER, flat=True).first()


def history_from(moment):
    BillingSettings.objects.update_or_create(
        pk=1, defaults={"subscription_history_from": moment}
    )


@override_settings(USE_BETA_PLAN_ON_SIGNUP=False)
class LifecycleStampTests(Plans, TestCase):
    """paid_since and ended_at are written by the saves that change
    is_active and is_trial, whatever path makes them."""

    def setUp(self):
        self.make_plans(trial=True)
        freezer = freeze_time(NOW)
        freezer.start()
        self.addCleanup(freezer.stop)
        self.user = self.teacher("stamp")
        self.trial = UserSubscription.objects.get(user=self.user, is_active=True)

    def test_a_trial_has_neither(self):
        self.assertTrue(self.trial.is_trial)
        self.assertEqual(stamp(self.trial), (None, None))

    def test_activating_a_paid_plan_ends_the_trial_and_starts_paid(self):
        with freeze_time(NOW + timedelta(hours=1)):
            paid = SubscriptionService.activate_subscription(self.user, self.standard)
        self.assertEqual(stamp(self.trial), (None, NOW + timedelta(hours=1)))
        self.assertEqual(stamp(paid), (NOW + timedelta(hours=1), None))

    def test_an_expired_trial_ends_and_was_never_paid(self):
        SubscriptionService.expire_trial(self.trial, force=True)
        self.assertEqual(stamp(self.trial), (None, NOW))

    def test_a_trial_converted_in_place_starts_paid(self):
        later = NOW + timedelta(days=2)
        with freeze_time(later):
            self.trial.is_trial = False
            self.trial.trial_end = None
            self.trial.save(update_fields=["is_trial", "trial_end"])
        self.assertEqual(stamp(self.trial), (later, None))

    def test_a_paid_end_is_stamped_once(self):
        paid = SubscriptionService.activate_subscription(self.user, self.standard)
        with freeze_time(NOW + timedelta(days=3)):
            paid.is_active = False
            paid.stripe_status = StripeSubscriptionStatus.CANCELED
            paid.save(update_fields=["is_active", "stripe_status"])
        with freeze_time(NOW + timedelta(days=9)):
            paid.save()
        self.assertEqual(stamp(paid), (NOW, NOW + timedelta(days=3)))

    def test_a_save_that_does_not_write_is_active_stamps_nothing(self):
        paid = SubscriptionService.activate_subscription(self.user, self.standard)
        paid.is_active = False
        paid.save(update_fields=["auto_renew"])
        paid.refresh_from_db()
        self.assertTrue(paid.is_active)
        self.assertEqual(stamp(paid), (NOW, None))


class EndedAtGuardTests(SimpleTestCase):
    """A queryset write skips the model's save, so every production bulk
    write that switches a UserSubscription off must set ended_at itself."""

    @staticmethod
    def offending_calls(source):
        tree = ast.parse(source)
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            name = func.attr
            if name not in ("record_bulk", "update"):
                continue
            keywords = {k.arg for k in node.keywords}
            if "is_active" not in keywords or "ended_at" in keywords:
                continue
            text = ast.get_source_segment(source, node) or ""
            if name == "update":
                text = ast.get_source_segment(source, func.value) or ""
            if "UserSubscription" in text or "subscriptions" in text:
                found.append(node.lineno)
        return found

    def test_the_scan_catches_a_planted_write(self):
        planted = (
            "history.record_bulk(UserSubscription.objects.filter(x=1),"
            " is_active=False)\n"
            "UserSubscription.objects.filter(x=1).update(is_active=False)\n"
            "history.record_bulk(UserSubscription.objects.all(),"
            " is_active=False, ended_at=now)\n"
            "LicenseSubscription.objects.filter(x=1).update(is_active=False)\n"
        )
        self.assertEqual(self.offending_calls(planted), [1, 2])

    def test_no_production_bulk_write_skips_ended_at(self):
        found = []
        for rel, file in production_files():
            for line in self.offending_calls(file.read_text()):
                found.append(f"{rel}:{line}")
        self.assertEqual(found, [])


class Frozen(APITestCase):
    def freeze(self):
        freezer = freeze_time(NOW)
        freezer.start()
        self.addCleanup(freezer.stop)
        cache.clear()
        self.admin = CustomUser.objects.create_user(
            email="conv.admin@x.example",
            password=PW,
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(user=self.admin)

    def summary(self):
        response = self.client.get(reverse("analytics-summary"))
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        return body.get("data", body)


@override_settings(USE_BETA_PLAN_ON_SIGNUP=False)
class PayingAndChurnTests(Plans, Frozen):
    def setUp(self):
        self.make_plans()
        self.freeze()
        history_from(at(9, 1))

    def test_paying_users_are_stripe_billed_individual_plans(self):
        self.sub(self.teacher("std"), self.standard, at(10, 1))
        self.sub(
            self.teacher("pastdue"),
            self.pro,
            at(10, 1),
            stripe_status=StripeSubscriptionStatus.PAST_DUE,
        )
        self.sub(self.teacher("comped"), self.standard, at(10, 1), stripe=False)
        self.sub(self.teacher("beta"), self.beta, at(10, 1))
        self.sub(self.teacher("license"), self.license, at(10, 1))
        self.sub(self.teacher("ended"), self.standard, at(9, 2), ended=at(10, 2))
        self.assertEqual(self.summary()["paying_users_count"], 2)

    def test_rolling_churn(self):
        # Period: 2026-10-21 12:00 to now.
        stay = self.teacher("stay")
        self.sub(stay, self.standard, at(9, 2))
        lost = self.teacher("lost")
        self.sub(lost, self.standard, at(9, 2), ended=at(11, 1))
        switch = self.teacher("switch")
        self.sub(switch, self.standard, at(9, 2), ended=at(11, 1))
        self.sub(switch, self.pro, at(11, 1))
        back = self.teacher("back")
        self.sub(back, self.standard, at(9, 2), ended=at(11, 1))
        self.sub(back, self.standard, at(11, 10))
        self.sub(self.teacher("new"), self.standard, at(11, 1))
        self.sub(self.teacher("gone.before"), self.standard, at(9, 2), ended=at(10, 1))
        self.sub(self.teacher("comped"), self.standard, at(9, 2), stripe=False)
        body = self.summary()
        self.assertEqual(body["churn_30d_base_count"], 4)
        self.assertEqual(body["churn_30d_lost_count"], 1)
        self.assertEqual(body["churn_30d_percent"], 25.0)

    def test_rolling_churn_with_no_base_is_zero(self):
        body = self.summary()
        self.assertEqual(body["churn_30d_base_count"], 0)
        self.assertEqual(body["churn_30d_lost_count"], 0)
        self.assertEqual(body["churn_30d_percent"], 0.0)

    def test_rolling_churn_before_reliable_history_is_null(self):
        history_from(at(11, 1))
        self.sub(self.teacher("x"), self.standard, at(9, 2))
        body = self.summary()
        self.assertIsNone(body["churn_30d_base_count"])
        self.assertIsNone(body["churn_30d_lost_count"])
        self.assertIsNone(body["churn_30d_percent"])

    def test_the_replaced_cards_are_gone(self):
        body = self.summary()
        # The start date in use (env CONVERSION_START_DATE, default
        # 2026-11-25), so the frontend can say "since".
        self.assertIsInstance(date.fromisoformat(body["conversion_start_date"]), date)
        for gone in (
            "login_greater_than_8_days",
            "login_greater_than_8_days_count",
            "grading_percent_greater_than_creation_percent",
            "grading_percent_greater_than_creation_count",
        ):
            self.assertNotIn(gone, body)
        self.assertIn("active_last_7_days_count", body)


@override_settings(
    USE_BETA_PLAN_ON_SIGNUP=False, CONVERSION_START_DATE=date(2026, 11, 1)
)
class TrialNumbersTests(Plans, Frozen):
    def setUp(self):
        self.make_plans(trial=True)
        self.freeze()

    def signed_up(self, when, tag):
        with freeze_time(when):
            user = self.teacher(tag)
        self.assertTrue(
            CreditBucket.objects.filter(
                wallet__user=user,
                bucket_type=CreditBucketType.TRIAL,
                created_at=when,
            ).exists()
        )
        return user

    def test_sign_ups_and_conversion_since_the_start_date(self):
        self.signed_up(at(9, 1), "old")
        before_start = self.signed_up(at(10, 25), "before.start")
        converted = self.signed_up(at(11, 5), "converted")
        self.signed_up(at(11, 18), "trialling")
        # A payer who trialled before the start date is not in the rate.
        self.sub(before_start, self.standard, at(11, 2))
        UserSubscription.objects.filter(user=converted, is_trial=True).update(
            is_active=False, is_trial=False, ended_at=at(11, 8)
        )
        self.sub(converted, self.standard, at(11, 8))
        body = self.summary()
        self.assertEqual(body["conversion_start_date"], "2026-11-01")
        self.assertEqual(body["new_trial_signups_count"], 2)
        self.assertEqual(body["trial_conversion_trials_count"], 2)
        self.assertEqual(body["trial_conversion_converted_count"], 1)
        self.assertEqual(body["trial_conversion_percent"], 50.0)

    def test_thirty_days_rolls(self):
        with override_settings(CONVERSION_START_DATE=date(2026, 1, 1)):
            self.signed_up(at(10, 20), "outside")
            self.signed_up(at(10, 22), "inside")
            body = self.summary()
        self.assertEqual(body["new_trial_signups_count"], 1)
        self.assertEqual(body["trial_conversion_trials_count"], 2)

    def test_no_trials_is_zero(self):
        body = self.summary()
        self.assertEqual(body["trial_conversion_trials_count"], 0)
        self.assertEqual(body["trial_conversion_percent"], 0.0)


@override_settings(USE_BETA_PLAN_ON_SIGNUP=False)
class MonthlyChurnTests(Plans, Frozen):
    def setUp(self):
        self.make_plans()
        self.freeze()
        history_from(at(9, 15))

    def months(self):
        response = self.client.get(reverse("beta-chart-churn-monthly"))
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        return body.get("data", body)["months"]

    def test_twelve_months_oldest_first(self):
        stay = self.teacher("stay")
        self.sub(stay, self.standard, at(8, 1))
        october = self.teacher("october")
        self.sub(october, self.standard, at(8, 1), ended=at(10, 10))
        november = self.teacher("november")
        self.sub(november, self.standard, at(10, 5), ended=at(11, 5))
        months = self.months()
        self.assertEqual(
            [m["month"] for m in months],
            ["2025-12"] + [f"2026-{n:02d}" for n in range(1, 12)],
        )
        by_month = {m["month"]: m for m in months}
        for month in ["2025-12"] + [f"2026-{n:02d}" for n in range(1, 10)]:
            row = by_month[month]
            self.assertTrue(row["no_data"], month)
            self.assertIsNone(row["base_count"])
            self.assertIsNone(row["lost_count"])
            self.assertIsNone(row["churn_percent"])
        self.assertEqual(
            by_month["2026-10"],
            {
                "month": "2026-10",
                "base_count": 2,
                "lost_count": 1,
                "churn_percent": 50.0,
                "partial": False,
                "no_data": False,
            },
        )
        self.assertEqual(
            by_month["2026-11"],
            {
                "month": "2026-11",
                "base_count": 2,
                "lost_count": 1,
                "churn_percent": 50.0,
                "partial": True,
                "no_data": False,
            },
        )

    def test_with_no_history_marker_every_month_has_data(self):
        BillingSettings.objects.filter(pk=1).update(subscription_history_from=None)
        months = self.months()
        self.assertFalse(any(m["no_data"] for m in months))
        self.assertEqual(months[0]["base_count"], 0)

    def test_super_admins_only(self):
        teacher = self.teacher("nosy")
        self.client.force_authenticate(user=teacher)
        response = self.client.get(reverse("beta-chart-churn-monthly"))
        self.assertEqual(response.status_code, 403)


@override_settings(USE_BETA_PLAN_ON_SIGNUP=False)
class BackfillTests(Plans, TestCase):
    """Migration 0092 fills paid_since and ended_at for existing rows, and
    records the first reliable history moment."""

    def setUp(self):
        self.make_plans(trial=True)

    def test_backfill_sources_in_order(self):
        # --- Before the audit trail (no history events) ---
        with history.suppressed():
            with freeze_time(at(8, 1)):
                converted = self.teacher("converted")
                stripe_ended = self.teacher("stripe.ended")
                switched = self.teacher("switched")
                expired = self.teacher("expired")
                touched = self.teacher("touched")
                for user in (stripe_ended, switched, touched):
                    UserSubscription.objects.filter(user=user).update(
                        is_active=False, is_trial=False
                    )
                stripe_row = self.sub(stripe_ended, self.standard, at(8, 1))
                switched_first = self.sub(switched, self.standard, at(8, 1))
                touched_row = self.sub(touched, self.standard, at(8, 1))
            with freeze_time(at(8, 10)):
                trial = UserSubscription.objects.get(user=converted)
                SubscriptionService.finalize_trial_to_paid_conversion(
                    trial, self.standard, "sub_conv_converted"
                )
            with freeze_time(at(8, 15)):
                SubscriptionService.expire_trial(
                    UserSubscription.objects.get(user=expired), force=True
                )
            with freeze_time(at(8, 20)):
                stripe_row.is_active = False
                stripe_row.save(update_fields=["is_active"])
            StripeEvent.objects.create(
                stripe_event_id="evt_conv_deleted",
                event_type="customer.subscription.deleted",
                payload={
                    "object": {
                        "id": stripe_row.stripe_subscription_id,
                        "ended_at": int(at(8, 18).timestamp()),
                    }
                },
            )
            with freeze_time(at(8, 5)):
                touched_row.is_active = False
                touched_row.save(update_fields=["is_active"])
            with freeze_time(at(8, 25)):
                switched_first.is_active = False
                switched_first.save(update_fields=["is_active"])
                switched_second = self.sub(switched, self.pro, at(8, 25))
            with freeze_time(at(10, 1)):
                touched_row.auto_renew = False
                touched_row.save(update_fields=["auto_renew"])
        # --- The audit trail starts ---
        with freeze_time(at(9, 1)):
            audited = self.teacher("audited")
        audited_row = UserSubscription.objects.get(user=audited)
        with freeze_time(at(9, 5)):
            audited_row.is_trial = False
            audited_row.trial_end = None
            audited_row.save(update_fields=["is_trial", "trial_end"])
        with freeze_time(at(10, 3)):
            audited_row.is_active = False
            audited_row.save(update_fields=["is_active"])
        with freeze_time(at(10, 4)):
            trialling = self.teacher("trialling")

        # As the rows stand before the migration.
        UserSubscription.objects.update(paid_since=None, ended_at=None)
        BillingSettings.objects.filter(pk=1).delete()

        with freeze_time(NOW):
            importlib.import_module(MIGRATION).backfill(django_apps, None)

        first_event = at(9, 1)
        self.assertEqual(history_marker(), first_event)
        # Converted in place before the trail: the trial bucket's expiry,
        # which the conversion stamps.
        converted_row = UserSubscription.objects.get(user=converted)
        self.assertEqual(stamp(converted_row), (at(8, 10), None))
        # Ended by Stripe before the trail: the deleted event's ended_at.
        self.assertEqual(stamp(stripe_row), (at(8, 1), at(8, 18)))
        # Switched before the trail: the next row's created_at.
        self.assertEqual(stamp(switched_first), (at(8, 1), at(8, 25)))
        self.assertEqual(stamp(switched_second), (at(8, 25), None))
        # Expired before the trail: never paid; ended at its last save.
        expired_row = UserSubscription.objects.get(user=expired)
        self.assertEqual(stamp(expired_row), (None, at(8, 15)))
        # Saved again after the trail started: capped at the trail's start.
        self.assertEqual(stamp(touched_row), (at(8, 1), first_event))
        # On the trail: exact.
        self.assertEqual(stamp(audited_row), (at(9, 5), at(10, 3)))
        # Still a trial: neither.
        trialling_row = UserSubscription.objects.get(user=trialling)
        self.assertEqual(stamp(trialling_row), (None, None))

    def test_no_audit_events_means_history_starts_now(self):
        with history.suppressed(), freeze_time(at(8, 1)):
            self.teacher("quiet")
        BillingSettings.objects.filter(pk=1).delete()
        from audit.models import AuditEvent

        if AuditEvent.objects.filter(
            action="SUBSCRIPTION_CHANGE", target_type="UserSubscription"
        ).exists():
            self.skipTest("events exist in this database")
        with freeze_time(NOW):
            importlib.import_module(MIGRATION).backfill(django_apps, None)
        self.assertEqual(history_marker(), NOW)
