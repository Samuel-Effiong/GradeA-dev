"""
User list sorting, D1 (B, owner 2026-11-11): the list's existing payload
must stay identical while its per-row queries are removed.

The snapshot users/test_data/user_list_payload_pin.json was captured from
the list on the base commit, BEFORE any change, by running this test once
with USER_LIST_PIN_WRITE=1 (NOTES, step P0). On beta: base origin/beta
2c8165b69 (docs/evidence/user-list-beta/NOTES.md, P0).
Afterwards the list must reproduce it exactly, minus the nine keys the
sorting work adds on purpose.

Normalized only where a value cannot repeat between runs: every UUID
becomes "<uuid>" (a wallet's or settings' `user` is checked against its
row's id before that), and every timestamp the fixture does not set
becomes "<datetime>". Rows are keyed by email.
"""

import json
import os
import re
from pathlib import Path

from django.urls import reverse
from django.utils import timezone

from billing.models import (
    BillingInterval,
    CreditBucketType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
)
from users.models import Settings, UserTypes

from .tests_user_list_sorting import NEW_KEYS, Base

PIN = Path(__file__).parent / "test_data" / "user_list_payload_pin.json"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def normalize(value):
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    text = str(value) if value is not None else None
    if text and UUID.match(text):
        return "<uuid>"
    if isinstance(value, str) and DATETIME.match(value):
        return "<datetime>"
    return value


class MixedFixture(Base):
    """Every kind of row the list shows, with every wallet branch lit."""

    def build(self):
        # Users first, plans after: no automatic trial attaches itself.
        individual = self.person("a.individual.pin@x.example", first_name="Ada")
        lic_teacher = self.person("b.licteacher.pin@x.example", first_name="Ben")
        school_admin = self.person(
            "c.schooladmin.pin@x.example",
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Cy",
        )
        student = self.person(
            "d.student.pin@x.example", user_type=UserTypes.STUDENT, first_name="Di"
        )
        no_wallet = self.person("e.nowallet.pin@x.example", first_name="Ed")
        no_settings = self.person("f.nosettings.pin@x.example", first_name="Flo")
        empty = self.person("g.empty.pin@x.example", first_name="Gus")

        # Individual: every bucket type, an expired and a never-expiring one,
        # usage inside and outside the cycle, a refunded row. The
        # usage log is append-only, so the out-of-cycle row is written
        # first and the cycle starts after it.
        wallet = self.wallet(individual)
        self.used(individual, 4_400)
        sub = self.subscribe(
            individual,
            self.plan(PlanType.PRO, PlanTier.PRO, interval=BillingInterval.MONTHLY),
            start=timezone.now(),
        )
        self.bucket(wallet, 12_000, used=3_250)
        self.bucket(wallet, 4_000, used=0, kind=CreditBucketType.CARRY_OVER)
        self.bucket(wallet, 2_500, used=500, kind=CreditBucketType.OVERAGE)
        self.bucket(
            wallet, 1_700, used=0, kind=CreditBucketType.MANUAL_GRANT, expires_in=None
        )
        self.bucket(wallet, 900, used=100, kind=CreditBucketType.TRIAL)
        self.bucket(wallet, 8_000, used=0, expires_in=-3)
        for amount, feature, refunded in (
            (1_200, "Grading Assignment", False),
            (800, "Grading Assignment", False),
            (2_000, "Assignment Extraction", False),
            (5_000, "Answer Extraction", True),
            (300, "Assignment Generation", False),
            (700, "Some Other Feature", False),
        ):
            self.used(individual, amount, refunded=refunded, feature=feature)
        self.assertTrue(sub.is_active)

        # License: a school admin managing it (with their own allocation)
        # and a teacher enrolled in it.
        license_plan = self.plan(
            PlanType.POWER_LICENSE, PlanTier.POWER, PlanCategory.LICENSE
        )
        lic = self.license(license_plan, school_admin)
        self.allocate(lic, school_admin, is_admin=True)
        allocation = self.allocate(lic, lic_teacher)
        type(allocation).objects.filter(pk=allocation.pk).update(
            monthly_allocation=15_000
        )
        self.bucket(self.wallet(lic_teacher), 15_000, used=6_001)
        self.bucket(self.wallet(school_admin), 5_000, used=0)

        CreditWallet.objects.filter(user=no_wallet).delete()
        Settings.objects.filter(user=no_settings).delete()
        return [
            individual,
            lic_teacher,
            school_admin,
            student,
            no_wallet,
            no_settings,
            empty,
        ]

    def listed_payload(self):
        people = self.build()
        rows = {str(r["id"]): r for r in self.rows()}
        out = {}
        for person in people:
            row = dict(rows[str(person.pk)])
            for key in NEW_KEYS:
                row.pop(key, None)
            for nested in ("credit_wallet", "settings"):
                if row.get(nested):
                    self.assertEqual(str(row[nested]["user"]), str(person.pk))
            out[person.email] = normalize(json.loads(json.dumps(row, default=str)))
        return out


class ListPayloadPinTests(MixedFixture):
    def test_the_list_payload_is_what_it_was(self):
        payload = self.listed_payload()

        if os.environ.get("USER_LIST_PIN_WRITE") == "1":
            PIN.parent.mkdir(exist_ok=True)
            PIN.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            self.skipTest(f"pin written to {PIN}")

        self.assertEqual(payload, json.loads(PIN.read_text()))

    def test_the_pin_is_not_vacuous(self):
        pinned = json.loads(PIN.read_text())
        wallet = pinned["a.individual.pin@x.example"]["credit_wallet"]

        self.assertGreater(wallet["total_remaining_credits"], 0)
        self.assertIsNotNone(wallet["feature_usage_breakdown"])
        self.assertIsNone(pinned["e.nowallet.pin@x.example"]["credit_wallet"])
        self.assertIsNone(pinned["f.nosettings.pin@x.example"]["settings"])
        self.assertNotEqual(
            pinned["b.licteacher.pin@x.example"]["credit_wallet"][
                "monthly_credit_total"
            ],
            0,
        )
        self.assertNotEqual(
            pinned["c.schooladmin.pin@x.example"]["credit_wallet"][
                "monthly_credit_total"
            ],
            0,
        )


class SingleRetrieveParityTests(MixedFixture):
    """Each listed row, minus the nine keys, equals what retrieve returns
    for the same user (retrieve keeps the existing serializer, D7)."""

    def test_each_row_equals_its_retrieve(self):
        people = self.build()
        rows = {str(r["id"]): r for r in self.rows()}
        admin = self.as_user(self.admin)
        for person in people:
            with self.subTest(person.email):
                listed = dict(rows[str(person.pk)])
                for key in NEW_KEYS:
                    listed.pop(key, None)
                single = admin.get(reverse("user-detail", kwargs={"pk": person.pk}))
                self.assertEqual(single.status_code, 200, single.content)
                self.assertEqual(
                    json.loads(json.dumps(listed, default=str)),
                    json.loads(json.dumps(single.data, default=str)),
                )
