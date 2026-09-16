"""H-1 Stage 3, pre-existing staleness P5: CreditWallet/CreditBucket changes
invalidate nothing.

`users/me` nests `credit_wallet.total_remaining_credits` and is cached
scoped only to the viewing user's own `SCOPE_USER` (users/views.py `me`).
Neither `CreditBucket` nor `CreditWallet` has any signal receiver
(billing/signals.py -- the one that used to exist was removed as dead
code, see its own comment), so a credit grant leaves a teacher's cached
`users/me` payload reporting their old balance.

This is pre-existing staleness the owner decided Stage 3 fixes too (plan
§ "Old staleness"): live under BOTH mechanisms today, since no receiver
means no wildcard clear either.

Fixtures follow the Stage 3 rule (plan §0): the `CreditBucket` is created
directly, with exactly the fields the project's own top-up/licence paths
write (`wallet`, `bucket_type`, `total_credits`, `used_credits`,
`expires_at`) -- the same pattern already used for this in
`assignments/tests_partial_update_credit_gate.py`, since exercising
`ManualCreditService.top_up_credits` end-to-end needs a full resolvable
billing plan and contributes nothing this matrix cares about: the
receiver gap is on `CreditBucket`'s own `post_save`, which fires
identically regardless of which service constructed the row.

Real Redis + real Postgres.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse

from AutoGrader.tests_cache_matrix_support import (
    STALE,
    FreshnessMatrixMixin,
    Read,
    legacy_wildcards_disabled,
)
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from users.models import UserTypes

User = get_user_model()


def make_active_user(email, user_type, first_name):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
    )


class CreditGrantFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    """P5: a credit grant must refresh the recipient's own cached `me`."""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.patched_modules = self.enterContext(legacy_wildcards_disabled())
        self.assertTrue(self.patched_modules, "no legacy module was patched")

        self.teacher = make_active_user("p5-teacher@x.test", UserTypes.TEACHER, "P5T")
        # CreditWallet is auto-created by users.signals.create_default_settings_and_wallet.
        self.wallet = CreditWallet.objects.get(user=self.teacher)

        self.me_url = reverse("user-me")

    def reads(self):
        return [Read("teacher's own profile (me)", self.teacher, self.me_url)]

    def grant_credits(self):
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=100_000,
            used_credits=0,
        )

    def test_credit_grant_currently_leaves_me_stale(self):
        result = self.run_matrix(
            "grant credits (P5 gap, no receiver on CreditBucket)",
            self.reads(),
            self.grant_credits,
        )
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(
            verdicts, {"teacher's own profile (me)": STALE}, result.table()
        )
