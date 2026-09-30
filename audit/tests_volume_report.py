"""Epic A S8: `audit_volume_report` reports counts and sizes only, reads a
bounded sample, cannot write, and projects correctly."""

import re
import uuid
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import DatabaseError, connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from audit.emitter import emit
from audit.enums import AuditAction
from audit.management.commands import audit_volume_report
from audit.models import AuditEvent
from billing.immutable import allow_unsafe_mutation
from users.models import CustomUser, UserTypes

PRIVATE_EMAIL = "volume.private.person@example.com"


def run(*args):
    out = StringIO()
    call_command("audit_volume_report", *args, stdout=out)
    return out.getvalue()


class MeasuredTests(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email=PRIVATE_EMAIL,
            password="Volume-test-pw-1",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.target = uuid.uuid4()
        for _ in range(3):
            emit(
                AuditAction.ASSIGNMENT_CREATE,
                actor=self.teacher,
                target_type="Assignment",
                target_id=self.target,
                metadata={"assignment_id": str(self.target)},
            )
        emit(AuditAction.GRADE_CHANGE, target_type="StudentSubmission")

    def test_it_counts_per_day_per_action_and_per_class(self):
        output = run("--days", "7")
        today = timezone.localdate()
        self.assertIn(f"day {today} ASSIGNMENT_CREATE 3", output)
        self.assertIn(f"day {today} GRADE_CHANGE 1", output)
        self.assertIn("total ASSIGNMENT_CREATE 3", output)
        self.assertIn("class GENERAL 3", output)
        self.assertIn("class STUDENT_RECORD 1", output)
        self.assertRegex(output, r"bytes_per_row \d+ \(sample of \d+\)")
        self.assertRegex(output, r"table_total_bytes \d+")

    def test_older_events_are_outside_the_window(self):
        # AuditEvent is append-only; the guard's own escape hatch (the one
        # the retention sweep uses) lets the test age a row.
        with allow_unsafe_mutation():
            AuditEvent.objects.filter(action=AuditAction.GRADE_CHANGE).update(
                occurred_at=timezone.now() - timedelta(days=40)
            )
        output = run("--days", "30")
        self.assertNotIn("total GRADE_CHANGE", output)

    def test_no_person_id_address_or_metadata_reaches_the_output(self):
        output = run("--days", "7", "--teachers", "1", "--students", "1")
        for private in (PRIVATE_EMAIL, str(self.teacher.id), str(self.target)):
            self.assertNotIn(private, output)
        # Nothing shaped like an email or a UUID at all.
        self.assertIsNone(re.search(r"\S+@\S+", output))
        self.assertIsNone(
            re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-", output)
        )

    def test_size_comes_from_a_bounded_sample_never_a_full_scan(self):
        with CaptureQueriesContext(connection) as queries:
            run("--days", "7")
        table = AuditEvent._meta.db_table
        touching = [q["sql"] for q in queries.captured_queries if table in q["sql"]]
        sized = [sql for sql in touching if "pg_column_size" in sql]
        self.assertTrue(sized)
        for sql in sized:
            self.assertIn("LIMIT", sql)
        self.assertTrue(any("TABLESAMPLE" in sql for sql in sized))
        # The only unwindowed statements are the catalogue lookups.
        for sql in touching:
            if "occurred_at" not in sql and "pg_column_size" not in sql:
                self.assertRegex(sql, r"reltuples|pg_relation_size|retention_class")


class ProjectionTests(TestCase):
    @patch.object(audit_volume_report, "PER_TEACHER_DAY", {"GRADE_CHANGE": 3})
    @patch.object(audit_volume_report, "PER_STUDENT_DAY", {"AUTH_LOGIN": 1})
    @patch.object(audit_volume_report, "SYSTEM_PER_DAY", {"AUDIT_RETENTION_SWEEP": 2})
    def test_rows_per_day_steady_size_and_sweep_volume(self):
        output = run("--teachers", "2", "--students", "10")

        self.assertIn("projected_per_day GRADE_CHANGE 6.0", output)
        self.assertIn("projected_per_day AUTH_LOGIN 10.0", output)
        self.assertIn("projected_per_day AUDIT_RETENTION_SWEEP 2.0", output)
        self.assertIn("projected_class STUDENT_RECORD 6.0/day kept 1095d", output)
        self.assertIn("projected_class GENERAL 12.0/day kept 365d", output)
        self.assertIn("sweep_deletes_per_day GENERAL 12.0", output)
        # 6/day x 1095 + 12/day x 365
        self.assertIn("projected_steady_rows 10950", output)
        # At 12 months both classes have 365 days of rows.
        self.assertRegex(output, r"projected_rows_at_12_months 6570 ")
        self.assertRegex(output, r"projected_rows_at_3_years 10950 ")

    def test_no_projection_without_a_school_size(self):
        self.assertNotIn("Projected", run())


class ReadOnlyTests(TransactionTestCase):
    """Run in a real transaction (not the TestCase wrapper), as it runs in
    production: any write inside the report is refused by Postgres."""

    def test_a_write_inside_the_report_is_refused(self):
        def writes(self, days):
            # Any write statement will do; this one matches no row, so only
            # the transaction's read-only mode can refuse it.
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {AuditEvent._meta.db_table} "  # nosec B608
                    "SET source_ip = NULL WHERE false"
                )

        with patch.object(audit_volume_report.Command, "measured", writes):
            with self.assertRaisesRegex(DatabaseError, "read-only transaction"):
                run()
        self.assertFalse(AuditEvent.objects.exists())
