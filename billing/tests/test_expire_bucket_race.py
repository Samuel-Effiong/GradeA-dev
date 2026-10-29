"""SubscriptionService.expire_bucket expires a bucket once, however many
callers race for it.

It locked the bucket row but never re-checked `is_processed` under the lock,
so a caller holding a stale copy - a second overlapping run of
`cleanup_expired_credit_buckets` (a duplicate Beat, a redeploy overlap, a
manual run) - waited for the lock and then wrote a SECOND EXPIRE row for the
same unused credits. Spendable balance is summed from live buckets, so no
credits were lost twice; the ledger over-recorded the expiry.
"""

import threading
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
)
from billing.services import SubscriptionService
from billing.tasks import cleanup_expired_credit_buckets
from users.models import UserTypes

User = get_user_model()


def expired_bucket(email, total=1000, used=300):
    user = User.objects.create_user(
        email=email,
        password="Expire-race-pw-1",  # pragma: allowlist secret
        first_name="Expire",
        last_name="Race",
        user_type=UserTypes.TEACHER,
        is_active=True,
    )
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    return CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=total,
        used_credits=used,
        expires_at=timezone.now() - timedelta(minutes=1),
    )


def expire_rows(bucket):
    return list(
        CreditLedger.objects.filter(
            bucket=bucket, ledger_type=CreditLedgerType.EXPIRE
        ).values_list("amount", flat=True)
    )


class ExpireBucketOnceTests(TestCase):
    def test_a_normal_expiry_writes_one_expire_row(self):
        bucket = expired_bucket("expire.once@example.com")

        cleanup_expired_credit_buckets.apply()

        self.assertEqual(expire_rows(bucket), [700])

    def test_a_stale_copy_expires_nothing_the_second_time(self):
        """The overlapping cleanup run's view: it listed the bucket before
        the other run processed it."""
        bucket = expired_bucket("expire.stale@example.com")
        stale = CreditBucket.objects.get(pk=bucket.pk)

        first = SubscriptionService.expire_bucket(bucket)
        second = SubscriptionService.expire_bucket(stale)

        self.assertEqual((first, second), (700, 0))
        self.assertEqual(expire_rows(bucket), [700])


class ConcurrentExpiryTests(TransactionTestCase):
    """Real threads and real commits: two callers holding the same
    unprocessed bucket, released together."""

    def test_two_racing_expiries_write_one_expire_row(self):
        bucket = expired_bucket("expire.race@example.com")
        copies = [CreditBucket.objects.get(pk=bucket.pk) for _ in range(2)]
        barrier = threading.Barrier(2, timeout=30)
        results = []

        def expire(copy):
            try:
                barrier.wait()
                results.append(SubscriptionService.expire_bucket(copy))
            finally:
                connection.close()

        threads = [threading.Thread(target=expire, args=(c,)) for c in copies]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(sorted(results), [0, 700])
        self.assertEqual(expire_rows(bucket), [700])
