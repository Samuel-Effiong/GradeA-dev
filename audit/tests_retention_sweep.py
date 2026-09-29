"""
The A6/X-4 retention sweep (§7 of the Epic A plan): FR-A-08.

sweep_audit_retention deletes AuditEvent rows past their retention_class's
cutoff (12mo general / 3yr student-record). sweep_audit_pii_short_retention
nulls source_ip/user_agent after PII_SHORT_RETENTION_DAYS, on an independent
90-day clock, regardless of the row's own retention_class.

Run with:
    python manage.py test audit.tests_retention_sweep --settings=settings_worktree
"""

import threading
from datetime import timedelta

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from .enums import RetentionClass
from .models import AuditEvent
from .tasks import (
    PII_SHORT_RETENTION_DAYS,
    sweep_audit_pii_short_retention,
    sweep_audit_retention,
)
from .tests_schema import event_fields


def make_event(**over):
    return AuditEvent.objects.create(**event_fields(**over))


class RowDeleteCutoffTests(TestCase):
    """Seed a row just inside and just outside each retention class's
    cutoff; only the outside one should be gone afterwards."""

    def test_general_row_past_cutoff_is_deleted_within_cutoff_survives(self):
        now = timezone.now()
        expired = make_event(
            retention_class=RetentionClass.GENERAL,
            occurred_at=now - timedelta(days=365, hours=1),
        )
        fresh = make_event(
            retention_class=RetentionClass.GENERAL,
            occurred_at=now - timedelta(days=364),
        )

        summary = sweep_audit_retention()

        self.assertFalse(AuditEvent.objects.filter(pk=expired.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(pk=fresh.pk).exists())
        self.assertIn("general=1", summary)
        self.assertIn("student_record=0", summary)

    def test_student_record_row_past_cutoff_is_deleted_within_cutoff_survives(self):
        now = timezone.now()
        expired = make_event(
            retention_class=RetentionClass.STUDENT_RECORD,
            occurred_at=now - timedelta(days=365 * 3, hours=1),
        )
        fresh = make_event(
            retention_class=RetentionClass.STUDENT_RECORD,
            occurred_at=now - timedelta(days=365 * 3 - 1),
        )

        summary = sweep_audit_retention()

        self.assertFalse(AuditEvent.objects.filter(pk=expired.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(pk=fresh.pk).exists())
        self.assertIn("general=0", summary)
        self.assertIn("student_record=1", summary)

    def test_a_general_row_is_never_swept_by_the_student_record_cutoff(self):
        """A GENERAL row older than the general cutoff but younger than the
        (much longer) student-record cutoff must still go - the two filters
        must not cross-apply to the wrong class."""
        now = timezone.now()
        row = make_event(
            retention_class=RetentionClass.GENERAL,
            occurred_at=now - timedelta(days=400),
        )

        sweep_audit_retention()

        self.assertFalse(AuditEvent.objects.filter(pk=row.pk).exists())

    def test_running_the_sweep_twice_reports_zero_on_the_second_run(self):
        now = timezone.now()
        make_event(
            retention_class=RetentionClass.GENERAL,
            occurred_at=now - timedelta(days=366),
        )
        make_event(
            retention_class=RetentionClass.STUDENT_RECORD,
            occurred_at=now - timedelta(days=365 * 3 + 1),
        )

        first = sweep_audit_retention()
        second = sweep_audit_retention()

        self.assertIn("general=1", first)
        self.assertIn("student_record=1", first)
        self.assertIn("general=0", second)
        self.assertIn("student_record=0", second)


class PiiShortRetentionCutoffTests(TestCase):
    def test_ip_and_agent_are_nulled_past_cutoff_and_untouched_within_it(self):
        now = timezone.now()
        stale = make_event(
            occurred_at=now - timedelta(days=PII_SHORT_RETENTION_DAYS, hours=1),
            source_ip="203.0.113.5",
            user_agent="Mozilla/5.0 stale",
        )
        recent = make_event(
            occurred_at=now - timedelta(days=PII_SHORT_RETENTION_DAYS - 1),
            source_ip="203.0.113.9",
            user_agent="Mozilla/5.0 recent",
        )

        summary = sweep_audit_pii_short_retention()

        stale.refresh_from_db()
        recent.refresh_from_db()
        self.assertIsNone(stale.source_ip)
        self.assertIsNone(stale.user_agent)
        self.assertEqual(recent.source_ip, "203.0.113.9")
        self.assertEqual(recent.user_agent, "Mozilla/5.0 recent")
        self.assertIn("nulled 1 rows", summary)

    def test_the_row_itself_survives_regardless_of_its_own_retention_class(self):
        """X-4: the sweep runs on a 90-day clock independent of
        retention_class - a STUDENT_RECORD row (3yr) still gets its IP/UA
        nulled at 90 days, but the row is not deleted."""
        now = timezone.now()
        row = make_event(
            retention_class=RetentionClass.STUDENT_RECORD,
            occurred_at=now - timedelta(days=PII_SHORT_RETENTION_DAYS + 1),
            source_ip="203.0.113.5",
            user_agent="Mozilla/5.0",
        )

        sweep_audit_pii_short_retention()

        row.refresh_from_db()
        self.assertIsNone(row.source_ip)
        self.assertIsNone(row.user_agent)

    def test_a_row_with_no_ip_or_agent_is_left_out_of_the_update_count(self):
        now = timezone.now()
        make_event(
            occurred_at=now - timedelta(days=PII_SHORT_RETENTION_DAYS + 1),
            source_ip=None,
            user_agent=None,
        )

        summary = sweep_audit_pii_short_retention()

        self.assertIn("nulled 0 rows", summary)

    def test_running_the_sweep_twice_reports_zero_on_the_second_run(self):
        now = timezone.now()
        make_event(
            occurred_at=now - timedelta(days=PII_SHORT_RETENTION_DAYS + 1),
            source_ip="203.0.113.5",
            user_agent="Mozilla/5.0",
        )

        first = sweep_audit_pii_short_retention()
        second = sweep_audit_pii_short_retention()

        self.assertIn("nulled 1 rows", first)
        self.assertIn("nulled 0 rows", second)


class ConcurrentSweepTests(TransactionTestCase):
    """Two sweeps racing on the same overlapping rows: real threads, real
    connections, matching the barrier-synchronised pattern already used in
    billing/tests/test_free_plan_activation_security.py."""

    def run_concurrently(self, count, target):
        start = threading.Barrier(count)
        results: list = [None] * count

        def worker(index):
            try:
                start.wait(timeout=10)
                results[index] = target(index)
            except Exception as exc:  # recorded, asserted on below
                results[index] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        return results

    def test_two_concurrent_delete_sweeps_never_double_process_or_raise(self):
        now = timezone.now()
        expired_general_ids = {
            make_event(
                retention_class=RetentionClass.GENERAL,
                occurred_at=now - timedelta(days=366),
            ).pk
            for _ in range(10)
        }
        expired_student_ids = {
            make_event(
                retention_class=RetentionClass.STUDENT_RECORD,
                occurred_at=now - timedelta(days=365 * 3 + 1),
            ).pk
            for _ in range(10)
        }

        results = self.run_concurrently(2, lambda i: sweep_audit_retention())

        for result in results:
            self.assertNotIsInstance(result, Exception, result)

        self.assertFalse(
            AuditEvent.objects.filter(
                pk__in=expired_general_ids | expired_student_ids
            ).exists()
        )

        general_deleted = sum(int(s.split("general=")[1].split()[0]) for s in results)
        student_deleted = sum(
            int(s.split("student_record=")[1].split()[0]) for s in results
        )
        self.assertEqual(general_deleted, len(expired_general_ids))
        self.assertEqual(student_deleted, len(expired_student_ids))

    def test_two_concurrent_pii_sweeps_never_double_process_or_raise(self):
        now = timezone.now()
        stale_ids = {
            make_event(
                occurred_at=now - timedelta(days=PII_SHORT_RETENTION_DAYS + 1),
                source_ip="203.0.113.5",
                user_agent="Mozilla/5.0",
            ).pk
            for _ in range(10)
        }

        results = self.run_concurrently(2, lambda i: sweep_audit_pii_short_retention())

        for result in results:
            self.assertNotIsInstance(result, Exception, result)

        for row in AuditEvent.objects.filter(pk__in=stale_ids):
            self.assertIsNone(row.source_ip)
            self.assertIsNone(row.user_agent)

        nulled_total = sum(int(s.split("nulled ")[1].split()[0]) for s in results)
        self.assertEqual(nulled_total, len(stale_ids))
