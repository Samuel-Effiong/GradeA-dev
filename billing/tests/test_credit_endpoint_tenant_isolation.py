"""
billing/tests/test_credit_endpoint_tenant_isolation.py
======================================================
Cross-tenant READ isolation on the four credit endpoints.

WHY THIS FILE EXISTS
--------------------
`CreditWalletViewSet`, `CreditBucketViewSet`, `CreditLedgerViewSet` and
`CreditUsageLogViewSet` each scope `get_queryset()` to the requesting user
(superadmins excepted). That scoping was measured to be **completely
untested**: replacing every one of those filter lines with `pass` and
running the entire 974-test billing suite produced exactly one failure —
`tests.UserSubscriptionViewSetTests.test_list_subscriptions_own_only`,
which guards a different viewset. All four credit endpoints stayed green
while returning every user's rows to every caller.

The existing `test_endpoint_permissions.py` is thorough about the WRITE
side (no minting, no forging, no erasing) and its read tests —
`test_teacher_can_still_read_own_wallet` / `_own_buckets` / `_own_ledger` —
assert only `status_code == 200`. A 200 is exactly what a leaking endpoint
returns, so those tests cannot see this class of bug by construction.

WHAT IS PINNED HERE
-------------------
For each of the four endpoints:
  * listing returns ONLY the caller's rows (asserted by id, not by count);
  * the other tenant's row is absent (the leak, stated directly);
  * fetching the other tenant's row by primary key is a 404;
  * a superadmin still sees both, so the fix is scoping and not a blanket
    deny that would silently break the admin console;
  * the DjangoFilterBackend query params (`?wallet=`, `?bucket=`) cannot be
    used to reach across the boundary — filters run AFTER get_queryset, and
    that ordering is load-bearing.

Assertions are on ROW IDENTITY, never on counts alone: a count assertion
passes by accident whenever the fixture happens to be balanced.
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditUsageLog,
    CreditWallet,
)
from users.models import UserTypes

CustomUser = get_user_model()


def rows_of(response):
    """
    APIJSONRenderer wraps everything as {"success", "message", "data"},
    and pagination puts the page under data["results"].
    """
    payload = response.json()["data"]
    return payload["results"] if isinstance(payload, dict) else payload


class _TwoTenants(TestCase):
    """Two unrelated teachers, each with a full credit stack of their own."""

    def setUp(self):
        self.client = APIClient()
        self.alice, self.alice_stack = self._make_tenant("alice.tenant@gmail.com")
        self.bob, self.bob_stack = self._make_tenant("bob.tenant@gmail.com")

        self.superadmin = CustomUser.objects.create_user(
            email="root.tenant@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )

    def _make_tenant(self, email):
        user = CustomUser.objects.create_user(
            email=email,
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        ledger = CreditLedger.objects.create(
            id=uuid.uuid4(),
            user_id=user.id,
            user_email=user.email,
            bucket=bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=1_000,
            reference=f"grant for {email}",
        )
        usage = CreditUsageLog.objects.create(
            id=uuid.uuid4(),
            user_id=user.id,
            user_email=user.email,
            wallet=wallet,
            bucket=bucket,
            amount=10,
            feature="Grading Assignment",
        )
        return user, {
            "wallet": wallet,
            "bucket": bucket,
            "ledger": ledger,
            "usage": usage,
        }

    def as_alice(self):
        self.client.force_authenticate(user=self.alice)

    def as_superadmin(self):
        self.client.force_authenticate(user=self.superadmin)

    def assert_isolated(self, list_url, mine, theirs):
        """
        Alice lists `list_url` and must see her own row and NOT Bob's.
        Identity, not count.
        """
        self.as_alice()
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)

        ids = {str(row["id"]) for row in rows_of(response)}
        self.assertIn(str(mine.pk), ids, "the owner lost access to their own row")
        self.assertNotIn(
            str(theirs.pk),
            ids,
            f"LEAK: {list_url} returned another user's row to Alice",
        )


class CreditWalletIsolationTests(_TwoTenants):
    def test_listing_returns_only_my_wallet(self):
        self.assert_isolated(
            reverse("credit-wallet-list"),
            self.alice_stack["wallet"],
            self.bob_stack["wallet"],
        )

    def test_fetching_another_users_wallet_by_id_is_a_404(self):
        self.as_alice()
        response = self.client.get(
            reverse("credit-wallet-detail", args=[self.bob_stack["wallet"].pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_a_superadmin_still_sees_both(self):
        """The scoping must not be a blanket deny that breaks the console."""
        self.as_superadmin()
        ids = {
            str(r["id"])
            for r in rows_of(self.client.get(reverse("credit-wallet-list")))
        }
        self.assertIn(str(self.alice_stack["wallet"].pk), ids)
        self.assertIn(str(self.bob_stack["wallet"].pk), ids)


class CreditBucketIsolationTests(_TwoTenants):
    def test_listing_returns_only_my_buckets(self):
        self.assert_isolated(
            reverse("credit-bucket-list"),
            self.alice_stack["bucket"],
            self.bob_stack["bucket"],
        )

    def test_fetching_another_users_bucket_by_id_is_a_404(self):
        self.as_alice()
        response = self.client.get(
            reverse("credit-bucket-detail", args=[self.bob_stack["bucket"].pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_a_superadmin_still_sees_both(self):
        self.as_superadmin()
        ids = {
            str(r["id"])
            for r in rows_of(self.client.get(reverse("credit-bucket-list")))
        }
        self.assertIn(str(self.alice_stack["bucket"].pk), ids)
        self.assertIn(str(self.bob_stack["bucket"].pk), ids)


class CreditLedgerIsolationTests(_TwoTenants):
    """
    The ledger is the append-only financial audit trail. A read leak here
    exposes another teacher's complete billing history.
    """

    def test_listing_returns_only_my_ledger_rows(self):
        self.assert_isolated(
            reverse("credit-ledger-list"),
            self.alice_stack["ledger"],
            self.bob_stack["ledger"],
        )

    def test_fetching_another_users_ledger_row_by_id_is_a_404(self):
        self.as_alice()
        response = self.client.get(
            reverse("credit-ledger-detail", args=[self.bob_stack["ledger"].pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_the_leaked_row_would_have_carried_identifying_data(self):
        """
        States the consequence rather than just the mechanism: the ledger
        row carries the other tenant's email and amounts.
        """
        self.as_alice()
        rows = rows_of(self.client.get(reverse("credit-ledger-list")))
        emails = {row.get("user_email") for row in rows}
        self.assertNotIn("bob.tenant@gmail.com", emails)

    def test_a_superadmin_still_sees_both(self):
        self.as_superadmin()
        ids = {
            str(r["id"])
            for r in rows_of(self.client.get(reverse("credit-ledger-list")))
        }
        self.assertIn(str(self.alice_stack["ledger"].pk), ids)
        self.assertIn(str(self.bob_stack["ledger"].pk), ids)


class CreditUsageLogIsolationTests(_TwoTenants):
    def test_listing_returns_only_my_usage_rows(self):
        self.assert_isolated(
            reverse("credit-usage-log-list"),
            self.alice_stack["usage"],
            self.bob_stack["usage"],
        )

    def test_fetching_another_users_usage_row_by_id_is_a_404(self):
        self.as_alice()
        response = self.client.get(
            reverse("credit-usage-log-detail", args=[self.bob_stack["usage"].pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_the_wallet_filter_cannot_be_used_to_cross_the_boundary(self):
        """
        `filterset_fields` exposes `wallet` as a query parameter. Filters run
        AFTER get_queryset, so the scoping still applies — that ordering is
        load-bearing and is pinned here, because the obvious "optimisation"
        of filtering before scoping would open the endpoint right back up.
        """
        self.as_alice()
        response = self.client.get(
            reverse("credit-usage-log-list"),
            {"wallet": str(self.bob_stack["wallet"].pk)},
        )

        self.assertEqual(response.status_code, 200)
        ids = {str(row["id"]) for row in rows_of(response)}
        self.assertNotIn(
            str(self.bob_stack["usage"].pk),
            ids,
            "LEAK: ?wallet= reached across the tenant boundary",
        )
        self.assertEqual(ids, set(), "no rows of Bob's should survive the filter")

    def test_the_bucket_filter_cannot_be_used_to_cross_the_boundary(self):
        self.as_alice()
        response = self.client.get(
            reverse("credit-usage-log-list"),
            {"bucket": str(self.bob_stack["bucket"].pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            str(self.bob_stack["usage"].pk),
            {str(row["id"]) for row in rows_of(response)},
        )

    def test_a_superadmin_still_sees_both(self):
        self.as_superadmin()
        ids = {
            str(r["id"])
            for r in rows_of(self.client.get(reverse("credit-usage-log-list")))
        }
        self.assertIn(str(self.alice_stack["usage"].pk), ids)
        self.assertIn(str(self.bob_stack["usage"].pk), ids)


class SuperAdminScopingRequiresBothSignalsTests(_TwoTenants):
    """
    The unscoping condition is `is_superuser AND user_type == SUPER_ADMIN`.
    Either flag alone must NOT unlock other tenants' rows — the same
    two-signal rule the QA console is gated by.
    """

    def _half_admin(self, email, *, user_type, is_superuser):
        return CustomUser.objects.create_user(
            email=email,
            password="testpass123",  # pragma: allowlist secret
            user_type=user_type,
            is_active=True,
            is_superuser=is_superuser,
        )

    def test_is_superuser_without_the_super_admin_type_stays_scoped(self):
        impostor = self._half_admin(
            "flag.only@gmail.com", user_type=UserTypes.TEACHER, is_superuser=True
        )
        self.client.force_authenticate(user=impostor)

        ids = {
            str(r["id"])
            for r in rows_of(self.client.get(reverse("credit-ledger-list")))
        }
        self.assertNotIn(str(self.alice_stack["ledger"].pk), ids)
        self.assertNotIn(str(self.bob_stack["ledger"].pk), ids)

    def test_super_admin_type_without_is_superuser_stays_scoped(self):
        impostor = self._half_admin(
            "type.only@gmail.com",
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=False,
        )
        self.client.force_authenticate(user=impostor)

        ids = {
            str(r["id"])
            for r in rows_of(self.client.get(reverse("credit-ledger-list")))
        }
        self.assertNotIn(str(self.alice_stack["ledger"].pk), ids)
        self.assertNotIn(str(self.bob_stack["ledger"].pk), ids)
