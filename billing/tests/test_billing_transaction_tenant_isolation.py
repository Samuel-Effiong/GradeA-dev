"""
billing/tests/test_billing_transaction_tenant_isolation.py
==========================================================
Cross-tenant isolation on `BillingTransactionViewSet` — the unified money
ledger, exposed at `/invoices/`.

WHY THIS FILE EXISTS
--------------------
Grepping the whole suite for that endpoint's route name found NOTHING: no
test anywhere exercised it. It is the endpoint that returns real charge
amounts, Stripe invoice/charge/payment-intent ids and receipt URLs, and its
`get_queryset` implements a THREE-WAY visibility rule:

    superadmin        -> every transaction in the system
    school admin      -> own INDIVIDUAL rows + LICENSE rows of their schools
    everyone else     -> own INDIVIDUAL rows only

None of those branches had a test. A regression in any of them exposes one
customer's payment history to another, and would have shipped silently.

The school-admin branch is the subtle one: it widens visibility via
`School.objects.filter(users=user)`, so it must widen to the admin's OWN
schools and no further, and must NOT hand LICENSE rows to an ordinary
teacher who merely belongs to the same school.

A NOTE ON `.distinct()`: the queryset ends in `.distinct()`, but it cannot
actually be exercised. `CustomUser.school` is a ForeignKey (one school per
user, `related_name="users"`), so `School.objects.filter(users=user)`
yields at most one row, and it is consumed as a `school__in=` subquery —
which cannot duplicate outer rows. Removing `.distinct()` fails none of
these tests, and that was verified by mutation rather than assumed. It is
therefore dead defensive code costing a DISTINCT on every page of the money
ledger. Flagged, deliberately NOT removed here: this is billing code and
the change belongs in its own reviewed commit.
`test_a_school_admin_sees_each_row_once` still asserts the real, valuable
property — no row comes back twice — it simply is not what keeps
`.distinct()` honest.

Assertions are on row IDENTITY. A count-only assertion passes by accident
whenever the fixture happens to be balanced.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    BillingTransaction,
    BillingTransactionSource,
    BillingTransactionStatus,
    BillingTransactionType,
)
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()


def rows_of(response):
    payload = response.json()["data"]
    return payload["results"] if isinstance(payload, dict) else payload


def ids_of(response):
    return {str(row["id"]) for row in rows_of(response)}


class BillingTransactionVisibilityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.list_url = reverse("billing-invoice-list")

        self.school_a = School.objects.create(name="Isolation School A")
        self.school_b = School.objects.create(name="Isolation School B")

        self.teacher_a = self._user("teacher.a@isolation.test", UserTypes.TEACHER)
        self.teacher_b = self._user("teacher.b@isolation.test", UserTypes.TEACHER)
        self.admin_a = self._user(
            "admin.a@isolation.test", UserTypes.SCHOOL_ADMIN, school=self.school_a
        )
        self.admin_b = self._user(
            "admin.b@isolation.test", UserTypes.SCHOOL_ADMIN, school=self.school_b
        )
        # A plain teacher who belongs to school A. Belonging to a school
        # must NOT by itself grant sight of the school's LICENSE spend.
        self.teacher_in_a = self._user(
            "teacher.in.a@isolation.test", UserTypes.TEACHER, school=self.school_a
        )
        self.superadmin = self._user(
            "root@isolation.test",
            UserTypes.SUPER_ADMIN,
            is_staff=True,
            is_superuser=True,
        )

        self.tx_teacher_a = self._individual_tx(self.teacher_a, 1_000)
        self.tx_teacher_b = self._individual_tx(self.teacher_b, 2_000)
        self.tx_admin_a = self._individual_tx(self.admin_a, 3_000)
        self.tx_school_a = self._license_tx(self.school_a, 40_000)
        self.tx_school_b = self._license_tx(self.school_b, 50_000)

    def _user(self, email, user_type, school=None, **extra):
        return CustomUser.objects.create_user(
            email=email,
            password="testpass123",  # pragma: allowlist secret
            user_type=user_type,
            is_active=True,
            school=school,
            **extra,
        )

    def _individual_tx(self, user, cents):
        return BillingTransaction.objects.create(
            id=uuid.uuid4(),
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=(BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE),
            status=BillingTransactionStatus.PAID,
            user=user,
            amount_cents=cents,
            occurred_at=timezone.now(),
            currency="usd",
            description=f"charge for {user.email}",
        )

    def _license_tx(self, school, cents):
        return BillingTransaction.objects.create(
            id=uuid.uuid4(),
            source=BillingTransactionSource.LICENSE,
            transaction_type=BillingTransactionType.LICENSE_SUBSCRIPTION_CHARGE,
            status=BillingTransactionStatus.PAID,
            school=school,
            amount_cents=cents,
            occurred_at=timezone.now(),
            currency="usd",
            description=f"licence charge for {school.name}",
        )

    def as_(self, user):
        self.client.force_authenticate(user=user)

    # --- individual teachers -------------------------------------------

    def test_a_teacher_sees_only_their_own_transactions(self):
        self.as_(self.teacher_a)
        ids = ids_of(self.client.get(self.list_url))

        self.assertIn(str(self.tx_teacher_a.pk), ids)
        self.assertNotIn(
            str(self.tx_teacher_b.pk),
            ids,
            "LEAK: another teacher's payment history was returned",
        )

    def test_a_teacher_never_sees_licence_transactions(self):
        """Even a teacher who belongs to that very school."""
        self.as_(self.teacher_in_a)
        ids = ids_of(self.client.get(self.list_url))

        self.assertNotIn(
            str(self.tx_school_a.pk),
            ids,
            "LEAK: a plain teacher saw their school's licence spend",
        )
        self.assertNotIn(str(self.tx_school_b.pk), ids)

    def test_fetching_another_users_transaction_by_id_is_a_404(self):
        self.as_(self.teacher_a)
        response = self.client.get(
            reverse("billing-invoice-detail", args=[self.tx_teacher_b.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_the_leaked_row_would_have_carried_the_amount(self):
        """States the consequence: these rows carry real money figures."""
        self.as_(self.teacher_a)
        amounts = {
            row.get("amount_cents") for row in rows_of(self.client.get(self.list_url))
        }
        self.assertNotIn(2_000, amounts)
        self.assertNotIn(50_000, amounts)

    # --- school admins --------------------------------------------------

    def test_a_school_admin_sees_their_own_schools_licence_rows(self):
        self.as_(self.admin_a)
        ids = ids_of(self.client.get(self.list_url))

        self.assertIn(str(self.tx_school_a.pk), ids, "the admin lost their own data")
        self.assertIn(str(self.tx_admin_a.pk), ids, "own individual row missing")

    def test_a_school_admin_does_not_see_another_schools_licence_rows(self):
        self.as_(self.admin_a)
        ids = ids_of(self.client.get(self.list_url))

        self.assertNotIn(
            str(self.tx_school_b.pk),
            ids,
            "LEAK: a school admin saw another school's licence spend",
        )

    def test_a_school_admin_does_not_see_other_individuals(self):
        self.as_(self.admin_a)
        ids = ids_of(self.client.get(self.list_url))

        self.assertNotIn(str(self.tx_teacher_a.pk), ids)
        self.assertNotIn(str(self.tx_teacher_b.pk), ids)

    def test_another_schools_admin_is_isolated_symmetrically(self):
        """Isolation is not accidentally one-directional."""
        self.as_(self.admin_b)
        ids = ids_of(self.client.get(self.list_url))

        self.assertIn(str(self.tx_school_b.pk), ids)
        self.assertNotIn(str(self.tx_school_a.pk), ids)

    def test_a_school_admin_sees_each_row_once(self):
        """
        No row is returned twice — a client that sums the page must not
        report double the school's real spend.

        This does NOT pin `.distinct()`: see the module docstring. With a
        FK-based school relation the join cannot duplicate, so removing
        `.distinct()` leaves this green. The property is still worth
        asserting because a future move to a many-to-many school
        membership WOULD make duplication reachable, and this test is
        where that would surface.
        """
        self.as_(self.admin_a)
        all_ids = [str(row["id"]) for row in rows_of(self.client.get(self.list_url))]

        self.assertEqual(
            len(all_ids), len(set(all_ids)), f"duplicate rows returned: {all_ids}"
        )

    def test_pagination_never_leaks_across_the_boundary(self):
        """
        Required before removing `.distinct()`: paging must not change WHO
        you can see, and no row may appear on two pages or vanish between
        them. Walks every page and checks the union.
        """
        # Give Alice's tenant enough rows to span several pages.
        extra = [self._individual_tx(self.teacher_a, 100 + i) for i in range(45)]
        self.as_(self.teacher_a)

        seen, page, guard = [], 1, 0
        while guard < 20:
            guard += 1
            response = self.client.get(self.list_url, {"page": page, "page_size": 10})
            if response.status_code == 404:
                break
            self.assertEqual(response.status_code, 200)
            rows = rows_of(response)
            if not rows:
                break
            seen.extend(str(r["id"]) for r in rows)
            page += 1

        self.assertEqual(
            len(seen), len(set(seen)), "a row appeared on more than one page"
        )
        expected = {str(self.tx_teacher_a.pk)} | {str(t.pk) for t in extra}
        self.assertEqual(
            set(seen), expected, "paging lost or gained rows versus the tenant scope"
        )
        # And nothing belonging to anyone else surfaced on any page.
        for foreign in (self.tx_teacher_b, self.tx_school_a, self.tx_school_b):
            self.assertNotIn(str(foreign.pk), seen)

    def test_a_school_admin_pages_without_duplicates(self):
        """
        The admin branch is the one that joins through School — the join
        `.distinct()` was defending against. Pinned across pages, not just
        on page 1.
        """
        extra = [self._license_tx(self.school_a, 500 + i) for i in range(25)]
        self.as_(self.admin_a)

        seen, page, guard = [], 1, 0
        while guard < 20:
            guard += 1
            response = self.client.get(self.list_url, {"page": page, "page_size": 10})
            if response.status_code == 404:
                break
            rows = rows_of(response)
            if not rows:
                break
            seen.extend(str(r["id"]) for r in rows)
            page += 1

        self.assertEqual(
            len(seen), len(set(seen)), "duplicate rows across pages for a school admin"
        )
        for t in extra:
            self.assertIn(str(t.pk), seen)
        self.assertNotIn(str(self.tx_school_b.pk), seen)

    # --- superadmin -----------------------------------------------------

    def test_a_superadmin_sees_everything(self):
        self.as_(self.superadmin)
        ids = ids_of(self.client.get(self.list_url))

        for tx in (
            self.tx_teacher_a,
            self.tx_teacher_b,
            self.tx_admin_a,
            self.tx_school_a,
            self.tx_school_b,
        ):
            self.assertIn(str(tx.pk), ids)

    def test_unscoping_requires_both_signals(self):
        """
        Same two-signal rule as the credit endpoints: `is_superuser` alone,
        or `user_type == SUPER_ADMIN` alone, must not unlock the system.
        """
        for email, user_type, is_superuser in (
            ("flag.only@isolation.test", UserTypes.TEACHER, True),
            ("type.only@isolation.test", UserTypes.SUPER_ADMIN, False),
        ):
            with self.subTest(email=email):
                impostor = self._user(email, user_type, is_superuser=is_superuser)
                self.as_(impostor)
                ids = ids_of(self.client.get(self.list_url))

                self.assertNotIn(str(self.tx_teacher_a.pk), ids)
                self.assertNotIn(str(self.tx_school_a.pk), ids)

    # --- filter / ordering parameters cannot widen the scope -------------

    def test_the_source_filter_cannot_reach_licence_rows(self):
        """
        `filterset_fields` exposes `source`. Filters run AFTER get_queryset,
        so asking for LICENSE as a plain teacher must return nothing rather
        than everything — that ordering is load-bearing.
        """
        self.as_(self.teacher_in_a)
        response = self.client.get(
            self.list_url, {"source": BillingTransactionSource.LICENSE}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ids_of(response), set(), "the source filter widened the tenant scope"
        )

    def test_the_status_filter_cannot_reach_other_tenants(self):
        self.as_(self.teacher_a)
        response = self.client.get(
            self.list_url, {"status": BillingTransactionStatus.PAID}
        )

        ids = ids_of(response)
        self.assertIn(str(self.tx_teacher_a.pk), ids)
        self.assertNotIn(str(self.tx_teacher_b.pk), ids)

    def test_ordering_cannot_page_into_another_tenant(self):
        """Ordering by amount descending must still only see own rows."""
        self.as_(self.teacher_a)
        response = self.client.get(self.list_url, {"ordering": "-amount_cents"})

        self.assertEqual(ids_of(response), {str(self.tx_teacher_a.pk)})

    def test_the_endpoint_is_read_only(self):
        """Money records are written by webhooks, never over HTTP."""
        self.as_(self.superadmin)
        before = BillingTransaction.objects.count()

        response = self.client.post(
            self.list_url,
            {
                "source": BillingTransactionSource.INDIVIDUAL,
                "amount_cents": 999_999,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 405)
        self.assertEqual(BillingTransaction.objects.count(), before)

    def test_students_cannot_read_the_money_ledger_at_all(self):
        student = self._user("student@isolation.test", UserTypes.STUDENT)
        self.as_(student)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 403)

    def test_anonymous_users_are_rejected(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 401)
