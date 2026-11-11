"""Epic B1b-7 (08b §1.5, plan §3.9; NOTES §7.2 D7-a/D7-b, rulings §7.5,
§7.7, contract §7.10): the low-balance warning and `balance_status`.

At each settlement the wallet's spendable credits are compared with its
allocation, plan_used_credits() + plan_remaining_credits() (the live
non-OVERAGE buckets). Levels 25, 10 and 0 send one email each per cycle; the
cycle is the live MONTHLY bucket (else the live non-OVERAGE bucket with a
non-null expires_at). An allocation of 0, or no cycle bucket, sends nothing
and writes nothing. The emails stay off (LOW_BALANCE_EMAIL_APPROVED False)
until B1b-8.

    python manage.py test billing.tests.test_b1b7_low_balance
"""

import importlib
import threading
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from ai_processor.services import AIProcessor
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    SchoolCreditAllocation,
)
from billing.testing.holds import (
    ai_ready_teacher,
    ai_response,
    estimate_for,
    fresh,
    make_job,
    real_billed_call,
)
from billing.tests.test_beat_lock_catch_up import make_school_licence
from users.models import CustomUser, UserTypes

PROVIDER = "_AIProcessor__ai_model"
SEND = "AutoGrader.tasks.send_email_task.delay"

# Approved texts (team/approved_wording_b1b_2026-11-09.md #6, #7, #11-#13),
# verbatim.
CLOSING_INDIVIDUAL = "Top up your credits to continue."
CLOSING_LICENSE = "Ask your school administrator for more credits to continue."
SUBJECT = {
    25: "You have 25% of your credits left",
    10: "You have 10% of your credits left",
    0: "You're out of credits",
}
BODY = {
    25: (
        "You've used 75% of this cycle's credits. You have {remaining} credits "
        "left until {cycle_end}."
    ),
    10: (
        "You've used 90% of this cycle's credits. You have {remaining} credits "
        "left until {cycle_end}."
    ),
    0: (
        "You've used all of this cycle's credits. Grading stops until you add "
        "credits or your credits renew on {cycle_end}. Batches that run out "
        "will wait for {days} days."
    ),
}

ALLOCATION = 100_000  # 100 display credits


def low_balance():
    return importlib.import_module("billing.low_balance")


def holds():
    return importlib.import_module("billing.holds")


def teacher(tag):
    return CustomUser.objects.create_user(
        email=f"b1b7.lb.{tag}@example.com",
        password="testpass123",  # pragma: allowlist secret  # nosec B106
        user_type=UserTypes.TEACHER,
    )


def bucket(wallet, kind=CreditBucketType.MONTHLY, total=ALLOCATION, **over):
    fields = {
        "wallet": wallet,
        "bucket_type": kind,
        "total_credits": total,
        "used_credits": 0,
        "expires_at": timezone.now() + timedelta(days=20),
    }
    fields.update(over)
    return CreditBucket.objects.create(**fields)


def wallet_of(user):
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    return wallet


class Base(TestCase):
    def setUp(self):
        self.user = teacher("t")
        self.wallet = wallet_of(self.user)
        self.monthly = bucket(self.wallet)
        self.sent = []
        approve = patch.object(low_balance(), "LOW_BALANCE_EMAIL_APPROVED", True)
        approve.start()
        self.addCleanup(approve.stop)
        send = patch(SEND, side_effect=lambda **kw: self.sent.append(kw))
        send.start()
        self.addCleanup(send.stop)

    def spend_to(self, left, wallet=None):
        """Settle a charge that leaves `left` credits in the wallet."""
        wallet = wallet or self.wallet
        amount = fresh(wallet).total_remaining_credits() - left
        job = make_job(wallet)
        holds().reserve(wallet, amount, job)
        with self.captureOnCommitCallbacks(execute=True):
            holds().settle(job, amount)
        return fresh(wallet)

    def expected(self, level, left, closing=CLOSING_INDIVIDUAL, cycle=None):
        cycle = cycle or self.monthly
        body = BODY[level].format(
            remaining=left // 1000,
            cycle_end=cycle.expires_at.date().isoformat(),
            days=settings.QUEUE_LIFETIME_DAYS,
        )
        return SUBJECT[level], f"{body}\n\n{closing}"

    def subjects(self):
        return [mail["subject"] for mail in self.sent]


class WarningTests(Base):
    def test_crossing_25_percent_sends_the_25_email_once(self):
        wallet = self.spend_to(25_000)
        self.assertEqual(len(self.sent), 1, "the 25% email was not sent once")
        subject, message = self.expected(25, 25_000)
        self.assertEqual(self.sent[0]["subject"], subject)
        self.assertEqual(self.sent[0]["message"], message)
        self.assertEqual(self.sent[0]["recipient_list"], [self.user.email])
        self.assertEqual(wallet.low_balance_warned_level, 25)
        self.assertEqual(
            wallet.low_balance_warned_cycle_start, self.monthly.created_at.date()
        )

    def test_the_same_level_again_in_the_cycle_sends_nothing(self):
        # 08b's once-per-cycle check.
        self.spend_to(25_000)
        self.spend_to(20_000)
        self.assertEqual(self.subjects(), [SUBJECT[25]], "a level was sent twice")

    def test_lower_levels_later_in_the_cycle_each_send(self):
        self.spend_to(25_000)
        self.spend_to(10_000)
        wallet = self.spend_to(0)
        self.assertEqual(self.subjects(), [SUBJECT[25], SUBJECT[10], SUBJECT[0]])
        self.assertEqual(self.sent[2]["message"], self.expected(0, 0)[1])
        self.assertEqual(wallet.low_balance_warned_level, 0)

    def test_a_jump_past_two_levels_sends_only_the_lowest(self):
        self.spend_to(5_000)
        self.assertEqual(self.subjects(), [SUBJECT[10]])

    def test_above_25_percent_sends_nothing(self):
        wallet = self.spend_to(25_001)
        self.assertEqual(self.sent, [])
        self.assertIsNone(wallet.low_balance_warned_level)

    def test_a_new_cycle_starts_over(self):
        self.spend_to(25_000)
        CreditBucket.objects.filter(pk=self.monthly.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.monthly = bucket(self.wallet)
        CreditBucket.objects.filter(pk=self.monthly.pk).update(
            created_at=timezone.now() + timedelta(days=1)
        )
        self.monthly = fresh(self.monthly)
        self.spend_to(25_000)
        self.assertEqual(self.subjects(), [SUBJECT[25], SUBJECT[25]])

    def test_unapproved_the_fields_move_and_nothing_is_sent(self):
        with patch.object(low_balance(), "LOW_BALANCE_EMAIL_APPROVED", False):
            wallet = self.spend_to(25_000)
        self.assertEqual(self.sent, [])
        self.assertEqual(wallet.low_balance_warned_level, 25)

    def test_a_license_teacher_gets_the_license_closing_line(self):
        school, licence = make_school_licence(
            "b1b7lb",
            billing_cycle_start=timezone.now() - timedelta(days=5),
            billing_cycle_end=timezone.now() + timedelta(days=25),
        )
        member = teacher("lic")
        member.school = school
        member.save()
        SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=member,
            monthly_allocation=ALLOCATION,
            is_active=True,
            next_credit_grant_at=timezone.now() + timedelta(days=25),
        )
        self.assertTrue(member.is_under_license(), "fixture: not under license")
        wallet = wallet_of(member)
        self.monthly = bucket(wallet)
        self.spend_to(25_000, wallet=wallet)
        self.assertEqual(
            self.sent[0]["message"],
            self.expected(25, 25_000, closing=CLOSING_LICENSE)[1],
        )


class AllocationAndCycleTests(Base):
    def test_a_zero_allocation_sends_nothing_writes_nothing_and_does_not_crash(self):
        # A spent license budget hands the teacher an empty MONTHLY bucket;
        # overage is still spendable (NOTES §7.6-§7.7).
        CreditBucket.objects.filter(pk=self.monthly.pk).update(total_credits=0)
        bucket(self.wallet, CreditBucketType.OVERAGE, total=10_000)
        wallet = self.spend_to(1_000)
        self.assertEqual(self.sent, [], "an empty month got a low-balance email")
        self.assertIsNone(wallet.low_balance_warned_level)
        self.assertIsNone(wallet.low_balance_warned_cycle_start)

    def test_no_live_non_overage_bucket_sends_nothing_and_does_not_crash(self):
        CreditBucket.objects.filter(pk=self.monthly.pk).delete()
        bucket(self.wallet, CreditBucketType.OVERAGE, total=10_000)
        wallet = self.spend_to(0)
        self.assertEqual(self.sent, [])
        self.assertIsNone(wallet.low_balance_warned_level)

    def test_monthly_and_carry_over_give_the_monthly_cycle(self):
        carry = bucket(
            self.wallet,
            CreditBucketType.CARRY_OVER,
            total=0,
            expires_at=timezone.now() + timedelta(days=50),
        )
        self.assertEqual(low_balance().cycle_bucket(self.wallet).pk, self.monthly.pk)
        self.assertNotEqual(carry.pk, self.monthly.pk)
        self.spend_to(25_000)
        self.assertEqual(self.sent[0]["message"], self.expected(25, 25_000)[1])

    def test_no_monthly_and_a_never_expiring_grant_sends_nothing(self):
        CreditBucket.objects.filter(pk=self.monthly.pk).delete()
        bucket(self.wallet, CreditBucketType.MANUAL_GRANT, expires_at=None)
        self.assertIsNone(low_balance().cycle_bucket(self.wallet))
        wallet = self.spend_to(0)
        self.assertEqual(self.sent, [])
        self.assertIsNone(wallet.low_balance_warned_level)


class BalanceStatusTests(Base):
    def status_at(self, left):
        CreditBucket.objects.filter(pk=self.monthly.pk).update(
            used_credits=ALLOCATION - left
        )
        return low_balance().balance_status(fresh(self.wallet))

    def test_the_edges_match_the_email_levels(self):
        cases = (
            (25_001, None),
            (25_000, "low"),
            (10_001, "low"),
            (10_000, "very_low"),
            (1, "very_low"),
            (0, "out"),
        )
        for left, status in cases:
            with self.subTest(left=left):
                self.assertEqual(self.status_at(left), status)

    def test_a_zero_allocation_is_out_only_when_nothing_is_spendable(self):
        CreditBucket.objects.filter(pk=self.monthly.pk).update(total_credits=0)
        self.assertEqual(low_balance().balance_status(fresh(self.wallet)), "out")
        bucket(self.wallet, CreditBucketType.OVERAGE, total=10_000)
        self.assertIsNone(low_balance().balance_status(fresh(self.wallet)))

    def test_the_wallet_endpoint_shows_the_three_new_fields(self):
        CreditBucket.objects.filter(pk=self.monthly.pk).update(used_credits=87_000)
        job = make_job(self.wallet)
        holds().reserve(self.wallet, 5_000, job)  # spendable 8,000: 8%
        client = APIClient()
        # A fresh user, as a real request loads: self.user caches its wallet
        # from before reserve() wrote reserved_credits (NOTES §7.15-§7.16).
        client.force_authenticate(user=CustomUser.objects.get(pk=self.user.pk))
        response = client.get(reverse("subscription-credit-wallet"))
        self.assertEqual(response.status_code, 200, response.content[:300])
        data = response.json()["data"]
        self.assertEqual(data["balance_status"], "very_low")
        self.assertEqual(data["reserved_credits"], 5_000)
        self.assertEqual(data["spendable"], 8_000)


class FlagOffTests(TestCase):
    """Q7-3: with the flag off there are no settlements, so no warnings."""

    def test_with_the_flag_off_a_billed_call_sends_no_warning(self):
        estimate = estimate_for()
        allocation = estimate + estimate // 4  # the call leaves about 20%
        user = ai_ready_teacher("b1b7.lb.off@example.com", allocation)
        sent = []
        with override_settings(BILLING_HOLDS_ENABLED=False), patch.object(
            low_balance(), "LOW_BALANCE_EMAIL_APPROVED", True
        ), patch(SEND, side_effect=lambda **kw: sent.append(kw)), patch.object(
            AIProcessor, PROVIDER, return_value=ai_response(estimate)
        ), self.captureOnCommitCallbacks(
            execute=True
        ):
            real_billed_call(user)
        wallet = CreditWallet.objects.get(user=user)
        self.assertLessEqual(
            wallet.total_remaining_credits() * 100,
            25 * allocation,
            "fixture: the call did not cross 25%",
        )
        self.assertEqual(sent, [], "a warning with the flag off")
        self.assertIsNone(wallet.low_balance_warned_level)


class ConcurrentSettlementTests(TransactionTestCase):
    def test_two_settlements_crossing_25_percent_send_once(self):
        user = teacher("race")
        wallet = wallet_of(user)
        bucket(wallet, used_credits=75_000)  # 25,000 left: the 25% level
        sent = []
        start = threading.Barrier(2)
        errors = []

        def check():
            try:
                start.wait(30)
                low_balance().check_low_balance(wallet.pk)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                connection.close()

        with patch.object(low_balance(), "LOW_BALANCE_EMAIL_APPROVED", True), patch(
            SEND, side_effect=lambda **kw: sent.append(kw)
        ):
            threads = [threading.Thread(target=check) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(len(sent), 1, "two racing checks sent twice")
