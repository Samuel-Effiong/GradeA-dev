"""
B1b-10 (NOTES §10.2, SM -31 fix plan af5dc858a): the super admin user list
shows B1b-7's `spendable` and `balance_status` for each wallet, read from the
page's prefetched buckets instead of per-row queries (DESIGN §9 of the user
list sorting). Each case compares the list's values with
billing.holds.spendable_credits and billing.low_balance.balance_status for the
same wallet, and with the value the case is built to give, so the parity
cannot pass with both sides wrong.

`status_from` is the level logic both read: pure, no database, no clock.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from billing.holds import spendable_credits
from billing.models import CreditBucketType, CreditWallet
from users.tests_user_list_sorting import Base

ALLOCATION = 10_000


class ListWalletParityTests(Base):
    def teacher_with(self, *, reserved=0):
        user = self.person()
        return user, self.wallet(user, reserved=reserved)

    def assert_parity(self, user, wallet, spendable, status):
        from billing.low_balance import balance_status

        fresh = CreditWallet.objects.get(pk=wallet.pk)
        listed = self.row(user)["credit_wallet"]
        self.assertEqual(spendable_credits(fresh), spendable, "billing spendable")
        self.assertEqual(balance_status(fresh), status, "billing balance_status")
        self.assertEqual(listed["spendable"], spendable, "listed spendable")
        self.assertEqual(listed["balance_status"], status, "listed balance_status")

    def left(self, wallet, left, kind=CreditBucketType.MONTHLY, expires_in=30):
        return self.bucket(
            wallet, ALLOCATION, used=ALLOCATION - left, kind=kind, expires_in=expires_in
        )

    def test_a_healthy_wallet(self):
        user, wallet = self.teacher_with()
        self.left(wallet, 9_000)
        self.assert_parity(user, wallet, 9_000, None)

    def test_a_hold_lowers_spendable_and_the_level(self):
        user, wallet = self.teacher_with(reserved=8_000)
        self.left(wallet, ALLOCATION)
        self.assert_parity(user, wallet, 2_000, "low")

    def test_a_hold_above_the_balance_is_out(self):
        user, wallet = self.teacher_with(reserved=6_000)
        self.left(wallet, 5_000)
        self.assert_parity(user, wallet, 0, "out")

    def test_an_expired_bucket_counts_for_nothing(self):
        user, wallet = self.teacher_with()
        self.left(wallet, 9_000)
        self.left(wallet, ALLOCATION, expires_in=-1)
        self.assert_parity(user, wallet, 9_000, None)

    def test_the_low_boundary(self):
        for left, status in ((2_501, None), (2_500, "low")):
            with self.subTest(left=left):
                user, wallet = self.teacher_with()
                self.left(wallet, left)
                self.assert_parity(user, wallet, left, status)

    def test_the_very_low_boundary(self):
        for left, status in ((1_001, "low"), (1_000, "very_low"), (1, "very_low")):
            with self.subTest(left=left):
                user, wallet = self.teacher_with()
                self.left(wallet, left)
                self.assert_parity(user, wallet, left, status)

    def test_nothing_left_with_a_cycle_is_out(self):
        user, wallet = self.teacher_with()
        self.left(wallet, 0)
        self.assert_parity(user, wallet, 0, "out")

    def test_overage_counts_in_spendable(self):
        # 500 plan credits left plus 2,000 overage: 2,500 spendable, 25% of
        # the 10,000 plan, "low". Without the overage it would be 500,
        # "very_low".
        user, wallet = self.teacher_with()
        self.left(wallet, 500)
        self.bucket(wallet, 2_000, used=0, kind=CreditBucketType.OVERAGE)
        self.assert_parity(user, wallet, 2_500, "low")

    def test_overage_lifts_a_wallet_above_the_low_level(self):
        # 1,000 plan left + 2,000 overage = 3,000 of a 10,000 allocation:
        # 30%, no level. Counting overage in the allocation (12,000) would
        # still be 25% "low": the allocation must leave overage out.
        user, wallet = self.teacher_with()
        self.left(wallet, 1_000)
        self.bucket(wallet, 2_000, used=0, kind=CreditBucketType.OVERAGE)
        self.assert_parity(user, wallet, 3_000, None)

    def test_overage_only_has_no_cycle(self):
        user, wallet = self.teacher_with()
        self.bucket(wallet, 5_000, used=0, kind=CreditBucketType.OVERAGE)
        self.assert_parity(user, wallet, 5_000, None)

    def test_spent_overage_only_is_out(self):
        user, wallet = self.teacher_with()
        self.bucket(wallet, 5_000, used=5_000, kind=CreditBucketType.OVERAGE)
        self.assert_parity(user, wallet, 0, "out")

    def test_a_carry_over_with_an_expiry_is_the_cycle(self):
        user, wallet = self.teacher_with()
        self.left(wallet, 2_000, kind=CreditBucketType.CARRY_OVER)
        self.assert_parity(user, wallet, 2_000, "low")

    def test_a_never_expiring_grant_alone_has_no_cycle(self):
        user, wallet = self.teacher_with()
        self.left(wallet, 2_000, kind=CreditBucketType.MANUAL_GRANT, expires_in=None)
        self.assert_parity(user, wallet, 2_000, None)

    def test_a_never_expiring_monthly_is_still_the_cycle(self):
        user, wallet = self.teacher_with()
        self.left(wallet, 2_000, expires_in=None)
        self.assert_parity(user, wallet, 2_000, "low")

    def test_a_wallet_with_no_buckets(self):
        user, wallet = self.teacher_with()
        self.assert_parity(user, wallet, 0, "out")


class StatusFromTests(SimpleTestCase):
    """The level logic alone: pure (SM condition 2)."""

    def status_from(self, *args):
        from billing import low_balance

        # Looked up by name so the module imports (and mypy passes) before
        # status_from exists: red is an AttributeError per case.
        name = "status_from"
        # SimpleTestCase refuses any database query; the clock raises.
        with patch(
            "django.utils.timezone.now", side_effect=AssertionError("clock read")
        ):
            return getattr(low_balance, name)(*args)

    def test_the_levels(self):
        cases = (
            ((2_501, ALLOCATION, True), None),
            ((2_500, ALLOCATION, True), "low"),
            ((1_001, ALLOCATION, True), "low"),
            ((1_000, ALLOCATION, True), "very_low"),
            ((1, ALLOCATION, True), "very_low"),
            ((0, ALLOCATION, True), "out"),
        )
        for args, status in cases:
            with self.subTest(args=args):
                self.assertEqual(self.status_from(*args), status)

    def test_no_allocation_or_no_cycle_is_out_only_when_nothing_is_spendable(self):
        cases = (
            ((0, 0, True), "out"),
            ((5, 0, True), None),
            ((0, ALLOCATION, False), "out"),
            ((5, ALLOCATION, False), None),
        )
        for args, status in cases:
            with self.subTest(args=args):
                self.assertEqual(self.status_from(*args), status)
