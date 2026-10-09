"""Epic A S8 (plan 08 §8.1): how big is the audit trail, and how big will it get.

    python manage.py audit_volume_report [--days 30] [--teachers N --students M]

Reads COUNTS AND SIZES ONLY from the database it runs against. It never
selects an actor, a target, an address, a user agent or any metadata, so its
output carries no personal data and is safe to run against production. That
is plan 08's D5 "counts-only query": a founder action, run via Railway.

It is cheap and cannot write (SM):
- everything runs inside one READ ONLY transaction, so any write - by this
  command or anything it calls - is refused by Postgres;
- bytes per row come from a bounded sample (TABLESAMPLE, at most
  SAMPLE_ROWS rows), and the table's row count from the planner's estimate
  (`pg_class.reltuples`), so no statement scans the whole table;
- the per-day counts read only the last `--days`, through the
  (action/retention, occurred_at) indexes.

It prints:

1. Measured: events per day per action over the last `--days`, events per
   retention class, the average bytes per row (`pg_column_size`), and the
   table's heap, index and total size.
2. Projected (with --teachers/--students): rows per day for a school of N
   active teachers and M active students, from the per-person daily rates in
   `audit.volume` (measured by the S8 harness, `audit/tests/test_bench_volume.py`), and
   the steady-state table size at each retention class's age limit (12
   months GENERAL, 3 years STUDENT_RECORD), plus the sweeps' daily delete
   volume at steady state (= rows added per day, per class).
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from audit.enums import AuditAction, RetentionClass
from audit.models import AuditEvent
from audit.volume import (
    PER_STUDENT_DAY,
    PER_TEACHER_DAY,
    RETENTION_DAYS,
    SYSTEM_PER_DAY,
    retention_class_of,
)

SAMPLE_ROWS = 5000


class Command(BaseCommand):
    help = "Audit trail volume: counts and sizes only (safe for production)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--teachers", type=int, default=None)
        parser.add_argument("--students", type=int, default=None)
        parser.add_argument(
            "--exact-all-time",
            action="store_true",
            help=(
                "Also count every row per retention class exactly. This is a "
                "FULL SCAN of the audit table - off by default; the default "
                "all-time figure is the planner's estimate."
            ),
        )

    def handle(self, *args, days, teachers, students, exact_all_time, **options):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
            self.measured(days, exact_all_time)
            if teachers is not None or students is not None:
                self.projected(teachers or 0, students or 0)

    # ------------------------------------------------------------ measured

    def measured(self, days, exact_all_time=False):
        since = timezone.now() - timedelta(days=days)
        out = self.stdout.write
        out(f"== Measured: the last {days} day(s)")
        # Every count below names the leading column of an existing index
        # with an equality and bounds occurred_at, its second column, so each
        # is an index range scan over the window: (action, -occurred_at) per
        # action, (retention_class, occurred_at) per class. No index LEADS
        # with occurred_at, so a window alone would scan the table (v2's
        # N1; the EXPLAIN is asserted in tests_volume_report).
        totals = {}
        for action in AuditAction.values:
            per_day = (
                AuditEvent.objects.filter(action=action, occurred_at__gte=since)
                .annotate(day=TruncDate("occurred_at"))
                .values("day")
                .annotate(n=Count("pk"))
                .order_by("day")
            )
            for row in per_day:
                out(f"day {row['day']} {action} {row['n']}")
                totals[action] = totals.get(action, 0) + row["n"]
        for action, n in sorted(totals.items(), key=lambda item: -item[1]):
            out(f"total {action} {n} ({n / days:.1f}/day)")
        # Per class over the window only (SM ruling): all-time is the
        # planner's estimate below, unless --exact-all-time asks for the
        # scan explicitly.
        for retention_class in RetentionClass.values:
            n = AuditEvent.objects.filter(
                retention_class=retention_class, occurred_at__gte=since
            ).count()
            if n:
                out(f"class {retention_class} {n} (last {days} days)")
        if exact_all_time:
            for row in (
                AuditEvent.objects.values("retention_class")
                .annotate(n=Count("pk"))
                .order_by("retention_class")
            ):
                out(f"class {row['retention_class']} {row['n']} (all time, exact)")

        table = AuditEvent._meta.db_table
        quoted = connection.ops.quote_name(table)  # from the model, not input
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT reltuples::bigint FROM pg_class WHERE oid = %s::regclass",
                [table],
            )
            rows = cursor.fetchone()[0]
            # A ~1% block sample; a small table can sample to nothing, so fall
            # back to the first SAMPLE_ROWS rows. Both are bounded.
            sampled, avg_bytes = 0, 0
            for source in (
                f"{quoted} TABLESAMPLE SYSTEM (1)",  # nosec B608
                quoted,
            ):
                cursor.execute(
                    f"SELECT count(*), coalesce(avg(pg_column_size(s.*)), 0) "  # nosec B608
                    f"FROM (SELECT * FROM {source} LIMIT %s) s",
                    [SAMPLE_ROWS],
                )
                sampled, avg_bytes = cursor.fetchone()
                if sampled:
                    break
            cursor.execute(
                "SELECT pg_relation_size(%s), pg_indexes_size(%s), "
                "pg_total_relation_size(%s)",
                [table, table, table],
            )
            heap, indexes, total = cursor.fetchone()
        out(f"rows_all_time {rows} (planner estimate, not a count)")
        out(f"bytes_per_row {float(avg_bytes):.0f} (sample of {sampled})")
        out(f"table_heap_bytes {heap}")
        out(f"table_index_bytes {indexes}")
        out(f"table_total_bytes {total}")
        self._avg_bytes = float(avg_bytes) or None
        self._index_ratio = (indexes / heap) if heap else None

    # ------------------------------------------------------------ projected

    def projected(self, teachers, students):
        out = self.stdout.write
        out(f"== Projected: {teachers} active teacher(s), {students} student(s)")
        daily = {}
        for action, n in PER_TEACHER_DAY.items():
            daily[action] = daily.get(action, 0) + n * teachers
        for action, n in PER_STUDENT_DAY.items():
            daily[action] = daily.get(action, 0) + n * students
        for action, n in SYSTEM_PER_DAY.items():
            daily[action] = daily.get(action, 0) + n
        per_class = dict.fromkeys(RetentionClass.values, 0.0)
        for action, n in sorted(daily.items()):
            out(f"projected_per_day {action} {n:.1f}")
            per_class[retention_class_of(action)] += n
        from audit.volume import HARNESS_BYTES_PER_ROW, HARNESS_INDEX_RATIO

        avg_bytes = getattr(self, "_avg_bytes", None) or HARNESS_BYTES_PER_ROW
        index_ratio = getattr(self, "_index_ratio", None) or HARNESS_INDEX_RATIO
        steady_rows = 0.0
        for cls, n in per_class.items():
            keep = RETENTION_DAYS[cls]
            out(f"projected_class {cls} {n:.1f}/day kept {keep}d")
            out(f"sweep_deletes_per_day {cls} {n:.1f} (steady state)")
            steady_rows += n * keep
        heap = steady_rows * avg_bytes
        out(f"projected_steady_rows {steady_rows:.0f}")
        out(f"projected_steady_heap_bytes {heap:.0f}")
        out(f"projected_steady_total_bytes {heap * (1 + index_ratio):.0f}")
        for label, age in (("12_months", 365), ("3_years", 1095)):
            rows = sum(
                n * min(age, RETENTION_DAYS[cls]) for cls, n in per_class.items()
            )
            out(
                f"projected_rows_at_{label} {rows:.0f} "
                f"(~{rows * avg_bytes * (1 + index_ratio) / 1e6:.1f} MB with indexes)"
            )
