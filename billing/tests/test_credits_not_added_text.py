"""
Brief 4 (owner, 19 Nov, reword only): a paid credit purchase whose credits
cannot be added shows the teacher a plain sentence, not staff wording. The
row is still PAID and the credits are still not granted. A data migration
rewrites rows written with the old texts.
"""

import importlib

from django.apps import apps as global_apps
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from billing.models import (
    BillingTransaction,
    BillingTransactionMethod,
    BillingTransactionSource,
    BillingTransactionStatus,
    BillingTransactionType,
)
from billing.transaction_wording import (
    CREDITS_NOT_ADDED,
    rewrite_credits_not_added_description,
)

NEW = "Credit top-up – payment received, credits not added. We'll refund you."
OLD_INDIVIDUAL = (
    "Overage purchase paid but block cap already reached at "
    "grant time — credits NOT granted, needs manual refund."
)
OLD_LICENSE = (
    "Overage purchase ({n} AI credit(s)) paid but license was inactive at "
    "fulfillment time — credits NOT granted, needs manual refund."
)


class WordingTests(SimpleTestCase):
    def test_constant_is_the_approved_text(self):
        self.assertEqual(CREDITS_NOT_ADDED, NEW)

    def test_rewrite_matches_both_old_forms_only(self):
        self.assertEqual(rewrite_credits_not_added_description(OLD_INDIVIDUAL), NEW)
        for n in ("1", "500", "12,000"):
            self.assertEqual(
                rewrite_credits_not_added_description(OLD_LICENSE.format(n=n)), NEW
            )
        self.assertIsNone(rewrite_credits_not_added_description(NEW))
        self.assertIsNone(
            rewrite_credits_not_added_description("Credit top-up — 500 AI credits")
        )
        self.assertIsNone(rewrite_credits_not_added_description(""))


class MigrationTests(TestCase):
    def _row(self, description):
        return BillingTransaction.objects.create(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=BillingTransactionType.INDIVIDUAL_OVERAGE_PURCHASE,
            status=BillingTransactionStatus.PAID,
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=100,
            description=description,
            occurred_at=timezone.now(),
        )

    def _mod(self):
        return importlib.import_module(
            "billing.migrations.0090_plain_credits_not_added_description"
        )

    def test_rewrites_old_rows_once_and_leaves_others(self):
        a = self._row(OLD_INDIVIDUAL)
        b = self._row(OLD_LICENSE.format(n="2,000"))
        other = self._row("Credit top-up — 500 AI credits")
        self._mod().rewrite(global_apps, None)
        for row in (a, b, other):
            row.refresh_from_db()
        self.assertEqual(a.description, NEW)
        self.assertEqual(b.description, NEW)
        self.assertEqual(other.description, "Credit top-up — 500 AI credits")
        self.assertEqual(a.status, BillingTransactionStatus.PAID)
        self._mod().rewrite(global_apps, None)
        a.refresh_from_db()
        self.assertEqual(a.description, NEW)

    def test_reverse_does_nothing(self):
        a = self._row(NEW)
        self.assertIsNone(self._mod().noop(global_apps, None))
        a.refresh_from_db()
        self.assertEqual(a.description, NEW)
