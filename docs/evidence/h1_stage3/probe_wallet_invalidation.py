"""PROBE (not a regression test): do wallet changes that are not a credit
bucket save leave the nested `credit_wallet` stale?

`CustomUserSerializer` nests `credit_wallet`, and every `UserCacheMixin`
`users/me` and `users/<pk>` read is keyed on the viewer's own generation.
Only a `CreditBucket` save bumps anything wallet-related. The wallet also
renders the licence allocation's `monthly_allocation` (as
`monthly_credit_total`) and `overage_blocks_used`, which change through
other writes.

Mutations run through the real services:

* `LicenseSubscriptionService.update_license_plan` - saves the plan and
  every allocation's `monthly_allocation`, and creates no bucket;
* `SubscriptionService.grant_overage_bucket` - creates a bucket and raises
  `overage_blocks_used` with a `QuerySet.update()`.

Each runs twice: with the legacy wildcard receivers live (production today)
and with them disabled (Stage 3). Real Redis + real Postgres.
"""

import json
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from AutoGrader.tests_cache_matrix_support import (
    FreshnessMatrixMixin,
    Read,
    legacy_wildcards_disabled,
)
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    CreditWallet,
    LicenseBillingMethod,
    LicenseSubscription,
    PlanTier,
    PlanType,
)
from billing.services import SubscriptionService
from billing.tests.test_mailerlite_sync import make_license_plan
from classrooms.models import School
from users.models import UserTypes

User = get_user_model()


class WalletInvalidationProbe(FreshnessMatrixMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.enterContext(patch("users.tasks.sync_user_to_mailerlite.delay"))

    def _user(self, email, user_type, first, **extra):
        return User.objects.create_user(
            email=email,
            password="password123",  # nosec  # pragma: allowlist secret
            user_type=user_type,
            is_active=True,
            first_name=first,
            last_name=user_type.title(),
            **extra,
        )

    def _fixture(self, tag):
        f = {"school": School.objects.create(name=f"Wallet {tag}")}
        f["admin"] = self._user(
            f"w-{tag}-a@x.test", UserTypes.SCHOOL_ADMIN, "WA", school=f["school"]
        )
        f["teacher"] = self._user(
            f"w-{tag}-t@x.test", UserTypes.TEACHER, "WT", school=f["school"]
        )
        f["superadmin"] = self._user(
            f"w-{tag}-s@x.test", UserTypes.SUPER_ADMIN, "WS", is_superuser=True
        )
        f["plan"] = make_license_plan(name=f"{PlanType.PRO_LICENSE}-{tag}"[:50])
        f["license"] = LicenseSubscription.objects.create(
            school=f["school"],
            admin_user=f["admin"],
            plan=f["plan"],
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            billing_method=LicenseBillingMethod.OFFLINE,
        )
        LicenseSubscriptionService.add_teacher_to_license(
            f["license"], f["teacher"].email
        )
        return f

    def _reads(self, f):
        teacher_detail = reverse("user-detail", args=[f["teacher"].pk])
        return [
            Read("teacher: users/me", f["teacher"], reverse("user-me")),
            Read("admin: users/<teacher>", f["admin"], teacher_detail),
            Read("superadmin: users/<teacher>", f["superadmin"], teacher_detail),
        ]

    def _mutations(self, f, tag):
        def plan_change():
            new_plan = make_license_plan(
                name=f"{PlanType.POWER_LICENSE}-{tag}"[:50],
                tier=PlanTier.POWER,
                monthly_credits=40_000_000,
            )
            LicenseSubscriptionService.update_license_plan(f["license"], new_plan)

        def overage():
            SubscriptionService.grant_overage_bucket(
                CreditWallet.objects.get(user=f["teacher"]), f["plan"], quantity=1
            )

        return {"licence plan change": plan_change, "overage purchase": overage}

    def test_probe(self):
        lines = ["", "[wallet invalidation probe]"]
        run = 0
        for mode in ("legacy ON (production today)", "legacy OFF (Stage 3)"):
            for name in ("licence plan change", "overage purchase"):
                run += 1
                cache.clear()
                tag = f"r{run}"
                f = self._fixture(tag)
                with ExitStack() as stack:
                    if mode.startswith("legacy OFF"):
                        stack.enter_context(legacy_wildcards_disabled())
                    result = self.run_matrix(
                        f"{name} [{mode}]",
                        self._reads(f),
                        self._mutations(f, tag)[name],
                    )
                lines.append(f"--- {name} [{mode}]")
                for o in result.outcomes:
                    lines.append(f"    {o.label:<30} {o.verdict}")
                    if o.verdict == "STALE":
                        cached = json.loads(o.cached_after[1])
                        truth = json.loads(o.truth_after[1])
                        top = sorted(k for k in truth if cached.get(k) != truth.get(k))
                        wallet = sorted(
                            k
                            for k in (truth.get("credit_wallet") or {})
                            if (cached.get("credit_wallet") or {}).get(k)
                            != truth["credit_wallet"][k]
                        )
                        lines.append(f"        differs: {top} credit_wallet: {wallet}")
        print("\n".join(lines), flush=True)
