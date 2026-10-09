"""The audit retention sweeps take the Beat lock (H-65 on Epic A).

Beta's H-65 guard requires every CELERY_BEAT_SCHEDULE entry to run under
`single_instance` or be exempt. The two audit sweeps are Epic A's own Beat
entries, so they got the lock at the bundle 5 merge-down.

What the lock must mean for a sweep:
- a run that overlaps another is skipped: it deletes or scrubs nothing and
  writes no AUDIT_RETENTION_SWEEP event (the trail shows one run, not two);
- a run that cannot reach the lock store is skipped the same way, with an
  ERROR (H-65 fails closed);
- nothing is lost by a skip: both sweeps select by cutoff, so the next run
  does the skipped run's work as well.
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError

from AutoGrader import beat_locks
from AutoGrader.beat_locks import SKIPPED_CACHE_ERROR, SKIPPED_HELD, BeatLock

from .enums import AuditAction, RetentionClass
from .models import AuditEvent
from .tasks import (
    PII_SHORT_RETENTION_DAYS,
    sweep_audit_pii_short_retention,
    sweep_audit_retention,
)
from .tests_schema import event_fields

RETENTION = "audit.tasks.sweep_audit_retention"
PII = "audit.tasks.sweep_audit_pii_short_retention"


def sweep_events():
    return AuditEvent.objects.filter(action=AuditAction.AUDIT_RETENTION_SWEEP)


class SweepLockTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        now = timezone.now()
        self.expired = AuditEvent.objects.create(
            **event_fields(
                retention_class=RetentionClass.GENERAL,
                occurred_at=now - timedelta(days=365, hours=1),
            )
        )
        self.stale_pii = AuditEvent.objects.create(
            **event_fields(
                source_ip="203.0.113.7",
                user_agent="Mozilla/5.0",
                occurred_at=now - timedelta(days=PII_SHORT_RETENTION_DAYS, hours=1),
            )
        )

    def hold(self, name):
        self.assertTrue(BeatLock(name, 600, 600).acquire("another-run"))

    def assertNothingSwept(self):
        self.assertTrue(AuditEvent.objects.filter(pk=self.expired.pk).exists())
        self.stale_pii.refresh_from_db()
        self.assertEqual(self.stale_pii.source_ip, "203.0.113.7")
        self.assertEqual(self.stale_pii.user_agent, "Mozilla/5.0")
        self.assertEqual(sweep_events().count(), 0)


class AnOverlappingSweepIsSkippedTests(SweepLockTestCase):
    def test_the_retention_sweep_is_skipped_and_records_nothing(self):
        self.hold(RETENTION)
        with self.assertLogs("AutoGrader.beat_locks", "WARNING"):
            summary = sweep_audit_retention()
        self.assertEqual(summary, f"{RETENTION}: {SKIPPED_HELD}.")
        self.assertNothingSwept()

    def test_the_pii_sweep_is_skipped_and_records_nothing(self):
        self.hold(PII)
        with self.assertLogs("AutoGrader.beat_locks", "WARNING"):
            summary = sweep_audit_pii_short_retention()
        self.assertEqual(summary, f"{PII}: {SKIPPED_HELD}.")
        self.assertNothingSwept()

    def test_the_two_sweeps_do_not_block_each_other(self):
        self.hold(RETENTION)
        summary = sweep_audit_pii_short_retention()
        self.assertIn("nulled 1 rows", summary)
        self.hold(PII)
        with self.assertLogs("AutoGrader.beat_locks", "WARNING"):
            self.assertEqual(sweep_audit_retention(), f"{RETENTION}: {SKIPPED_HELD}.")


class TheLockStoreIsUnavailableTests(SweepLockTestCase):
    def unavailable(self):
        return patch.object(
            beat_locks, "_redis", side_effect=RedisConnectionError("refused")
        )

    def test_the_retention_sweep_is_skipped_with_an_error(self):
        with self.unavailable(), self.assertLogs(
            "AutoGrader.beat_locks", "ERROR"
        ) as logs:
            summary = sweep_audit_retention()
        self.assertEqual(summary, f"{RETENTION}: {SKIPPED_CACHE_ERROR}.")
        self.assertIn(RETENTION, logs.output[0])
        self.assertNothingSwept()

    def test_the_pii_sweep_is_skipped_with_an_error(self):
        with self.unavailable(), self.assertLogs(
            "AutoGrader.beat_locks", "ERROR"
        ) as logs:
            summary = sweep_audit_pii_short_retention()
        self.assertEqual(summary, f"{PII}: {SKIPPED_CACHE_ERROR}.")
        self.assertIn(PII, logs.output[0])
        self.assertNothingSwept()

    def test_the_next_run_does_the_skipped_runs_work(self):
        """A skip costs one schedule interval and no more: the sweeps select
        by cutoff, so the first run that gets the lock catches up."""
        with self.unavailable(), self.assertLogs("AutoGrader.beat_locks", "ERROR"):
            sweep_audit_retention()
            sweep_audit_pii_short_retention()
        self.assertNothingSwept()

        self.assertIn("general=1", sweep_audit_retention())
        self.assertIn("nulled 1 rows", sweep_audit_pii_short_retention())

        self.assertFalse(AuditEvent.objects.filter(pk=self.expired.pk).exists())
        self.stale_pii.refresh_from_db()
        self.assertIsNone(self.stale_pii.source_ip)
        self.assertIsNone(self.stale_pii.user_agent)
        self.assertEqual(sweep_events().count(), 2)


class AnUncontendedSweepStillRunsTests(SweepLockTestCase):
    def test_each_sweep_runs_and_releases_its_lock(self):
        self.assertIn("general=1", sweep_audit_retention())
        self.assertIn("nulled 1 rows", sweep_audit_pii_short_retention())
        self.assertEqual(sweep_events().count(), 2)
        self.assertIsNone(BeatLock(RETENTION, 1, 1).holder())
        self.assertIsNone(BeatLock(PII, 1, 1).holder())
