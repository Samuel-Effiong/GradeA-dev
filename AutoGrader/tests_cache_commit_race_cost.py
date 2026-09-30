"""What the post-commit bump costs at roster scale.

The commit-race fix bumps every counter a second time once the transaction
commits. This measures that second bump against the same workload with the
fix switched off, on two shapes:

* the worst case: 6,000 enrollments in ONE transaction. No production path
  does this today, since roster import commits per row, but a future bulk
  path could. Every in-transaction bump queues its own callback, so the
  commit replays all of them back to back.
* production's shape: `import_roster` with its 2,000-row cap, where every
  row is its own transaction and pays its second bump at its own commit.
  Its students have signed in before, so each row is one enrollment and
  one bump. A first import of students who never signed in (the common
  case for a new class) takes the PENDING path instead: the account is
  saved with a fresh temporary password as well, two bumps per row, and
  that is pinned too.

Reported per run: Redis round trips and commands sent, split into those
sent while the transaction was open and those sent by its commit; p50/p95
of a single bump; the wall time of the whole write and of its commit. The
figures are printed for the evidence log. What is asserted is exact: the
fix adds one round trip per in-transaction bump and no more, and the
counters end exactly one bump per round trip ahead.

Legacy wildcard deletes are disabled, as in the other race tests: they run a
SCAN per pattern per row and would drown the figure being measured.

Real Redis + real Postgres.
"""

import os
import statistics
import time
import tracemalloc
from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import redis
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone

import AutoGrader.cache_generation as cache_generation
from AutoGrader.cache_generation import SCOPE_COURSE, get_generation
from AutoGrader.tests_cache_commit_race import CommitRaceBase
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from classrooms.services.roster_import import RosterRow, import_roster
from users.models import UserTypes

User = get_user_model()

# The scale the evidence is measured at. The environment variables exist only
# to smoke-test this harness cheaply; every recorded figure comes from a run
# that leaves them unset.
SINGLE_TRANSACTION_STUDENTS = int(os.environ.get("RACE_COST_ENROLLMENTS", 6000))
ROSTER_IMPORT_ROWS = int(os.environ.get("RACE_COST_ROWS", 2000))


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


@contextmanager
def fix_switched_off():
    """The code as it was before the fix: no bump is ever queued for commit."""
    not_atomic = SimpleNamespace(
        get_connection=lambda: SimpleNamespace(in_atomic_block=False),
        on_commit=None,
    )
    with patch.object(cache_generation, "transaction", not_atomic):
        yield


class Meter:
    """Round trips, commands and per-bump latency, split by phase.

    `phase` is flipped to "commit" by an on_commit callback registered
    before any write, so it runs first when the transaction commits.
    """

    def __init__(self):
        self.phase = "open"
        self.round_trips = Counter()
        self.commands = Counter()
        self.bump_ms = {"open": [], "commit": []}

    @contextmanager
    def recording(self):
        connection_class = redis.connection.AbstractConnection
        send_one = connection_class.send_command
        pack_many = connection_class.pack_commands
        bump_now = cache_generation._bump_now
        meter = self

        def counting_send_command(conn, *args, **kwargs):
            meter.round_trips[meter.phase] += 1
            meter.commands[meter.phase] += 1
            return send_one(conn, *args, **kwargs)

        def counting_pack_commands(conn, commands):
            commands = list(commands)
            meter.round_trips[meter.phase] += 1
            meter.commands[meter.phase] += len(commands)
            return pack_many(conn, commands)

        def timed_bump_now(unique):
            started = time.perf_counter()
            try:
                return bump_now(unique)
            finally:
                meter.bump_ms[meter.phase].append(
                    (time.perf_counter() - started) * 1000
                )

        with patch.object(connection_class, "send_command", counting_send_command):
            with patch.object(
                connection_class, "pack_commands", counting_pack_commands
            ):
                with patch.object(cache_generation, "_bump_now", timed_bump_now):
                    yield self

    def merge(self, other):
        """Fold another chunk's meter into this one."""
        self.round_trips.update(other.round_trips)
        self.commands.update(other.commands)
        for phase in ("open", "commit"):
            self.bump_ms[phase] += other.bump_ms[phase]
        return self

    def enter_commit_phase(self):
        self.phase = "commit"

    def summary(self):
        lines = []
        for phase in ("open", "commit"):
            samples = self.bump_ms[phase]
            timing = (
                f"bump p50={percentile(samples, 0.50):.3f}ms "
                f"p95={percentile(samples, 0.95):.3f}ms"
                if samples
                else "no bumps"
            )
            lines.append(
                f"  {phase:<6} round_trips={self.round_trips[phase]:>6} "
                f"commands={self.commands[phase]:>6} bumps={len(samples):>6} "
                f"{timing}"
            )
        return "\n".join(lines)


class RosterScaleCostBase(CommitRaceBase):
    def setUp(self):
        super().setUp()
        school = School.objects.create(name="Race cost school")
        self.teacher.school = school
        self.teacher.save()

    def fresh_course(self, label):
        """Each measured pass gets its own empty course.

        Enrolling runs `full_clean`, whose duplicate-name check scans the
        students already in the course, so a pass into a course the previous
        pass filled is slower for reasons that have nothing to do with the
        change being measured.
        """
        self.course = Course.objects.create(
            name=f"Race cost course {label}",
            teacher=self.teacher,
            session=Session.objects.create(
                name=f"Race cost term {label}", teacher=self.teacher
            ),
        )
        self.detail = reverse("course-detail", args=[self.course.pk])
        return self.course

    def make_students(self, count, tag, signed_in=True):
        """Existing student accounts. `signed_in` sets the `last_login` a
        real sign-in records; without it, enrolling one takes the PENDING
        path (classrooms.services.enrollment has_signed_in)."""
        password = make_password("password123")  # nosec  # pragma: allowlist secret
        last_login = timezone.now() if signed_in else None
        User.objects.bulk_create(
            User(
                email=f"cost-{tag}-{i}@x.test",
                password=password,
                user_type=UserTypes.STUDENT,
                is_active=True,
                first_name=f"Cost{tag}x{i}",
                last_name="Student",
                last_login=last_login,
            )
            for i in range(count)
        )
        return list(
            User.objects.filter(email__startswith=f"cost-{tag}-").order_by("pk")
        )

    def report(self, title, fixed, unfixed, fixed_s, unfixed_s, extra=""):
        print(
            f"\n=== {title}\n"
            f"fix OFF (before): wall={unfixed_s['total']:.2f}s "
            f"commit={unfixed_s['commit']:.3f}s\n{unfixed.summary()}\n"
            f"fix ON  (after):  wall={fixed_s['total']:.2f}s "
            f"commit={fixed_s['commit']:.3f}s\n{fixed.summary()}\n"
            f"added by the fix: round_trips="
            f"{sum(fixed.round_trips.values()) - sum(unfixed.round_trips.values())} "
            f"commands={sum(fixed.commands.values()) - sum(unfixed.commands.values())} "
            f"wall={fixed_s['total'] - unfixed_s['total']:+.2f}s{extra}",
            flush=True,
        )


class SingleTransactionRosterCostTests(RosterScaleCostBase):
    def enroll_all_in_one_transaction(self, students):
        meter = Meter()
        timing = {}
        with meter.recording():
            started = time.perf_counter()
            with transaction.atomic():
                transaction.on_commit(meter.enter_commit_phase)
                transaction.on_commit(
                    lambda: timing.__setitem__("commit_started", time.perf_counter())
                )
                for student in students:
                    StudentCourse.objects.create(
                        student=student,
                        course=self.course,
                        enrollment_status=EnrollmentStatusType.ENROLLED,
                        auto_added=True,
                    )
            finished = time.perf_counter()
        timing["total"] = finished - started
        timing["commit"] = finished - timing["commit_started"]
        return meter, timing

    def instrumented(self, students):
        """Same write, uninstrumented for Redis, measuring DB queries and
        peak Python memory. Separate from the timed pass because tracemalloc
        slows everything it traces."""
        queries = []

        def count_query(execute, sql, params, many, context):
            queries.append(1)
            return execute(sql, params, many, context)

        # Counted through the cursor, not connection.queries: that log is a
        # deque capped at 9,000 entries, so at this scale it silently stops
        # growing and the count reads as 0.
        tracemalloc.start()
        try:
            with connection.execute_wrapper(count_query):
                with transaction.atomic():
                    for student in students:
                        StudentCourse.objects.create(
                            student=student,
                            course=self.course,
                            enrollment_status=EnrollmentStatusType.ENROLLED,
                            auto_added=True,
                        )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return len(queries), peak

    def measure_size(self, size):
        students = self.make_students(6 * size, tag=f"s{size}")
        batches = [students[i * size : (i + 1) * size] for i in range(6)]

        # Both orders, because whichever pass runs second inherits any drift
        # in machine load, and the fix's pass must not be the one that always
        # runs second.
        passes = {"off": [], "on": []}
        timings = {"off": [], "on": []}
        for index, mode in enumerate(("off", "on", "on", "off")):
            course = self.fresh_course(f"{mode}-{size}-{index}")
            before = get_generation(SCOPE_COURSE, course.pk)
            if mode == "off":
                with fix_switched_off():
                    meter, timing = self.enroll_all_in_one_transaction(batches[index])
            else:
                meter, timing = self.enroll_all_in_one_transaction(batches[index])
            timing["generations"] = get_generation(SCOPE_COURSE, course.pk) - before
            passes[mode].append(meter)
            timings[mode].append(timing)

        unfixed, fixed = passes["off"][0], passes["on"][0]
        unfixed_s = min(timings["off"], key=lambda t: t["total"])
        fixed_s = min(timings["on"], key=lambda t: t["total"])
        before_fixed, after_fixed = 0, 2 * size  # asserted per pass below
        for timing in timings["off"]:
            self.assertEqual(timing["generations"], size)
        for timing in timings["on"]:
            self.assertEqual(timing["generations"], 2 * size)

        self.fresh_course(f"qoff-{size}")
        with fix_switched_off():
            unfixed_queries, unfixed_peak = self.instrumented(batches[4])
        self.fresh_course(f"qon-{size}")
        fixed_queries, fixed_peak = self.instrumented(batches[5])

        self.assertEqual(StudentCourse.objects.filter(course=self.course).count(), size)
        # Before the fix nothing is sent at commit; after it, the commit
        # replays exactly the in-transaction bumps, one round trip each.
        self.assertEqual(unfixed.round_trips["commit"], 0)
        self.assertEqual(len(fixed.bump_ms["open"]), size)
        self.assertEqual(len(fixed.bump_ms["commit"]), size)
        self.assertEqual(fixed.round_trips["commit"], fixed.round_trips["open"])
        self.assertEqual(fixed.commands["commit"], fixed.commands["open"])
        self.assertEqual(after_fixed - before_fixed, 2 * size)
        # The fix sends nothing to the database.
        self.assertEqual(fixed_queries, unfixed_queries)

        self.report(
            f"{size} enrollments in ONE transaction",
            fixed,
            unfixed,
            fixed_s,
            unfixed_s,
            extra=(
                "\nboth orders (off,on,on,off), best wall per mode is reported; "
                f"off={[round(t['total'], 2) for t in timings['off']]}s "
                f"on={[round(t['total'], 2) for t in timings['on']]}s"
                f"\nDB queries: before={unfixed_queries} after={fixed_queries} "
                f"({fixed_queries / size:.2f}/row)"
                f"\npeak traced memory: before={unfixed_peak / 1024:.0f}KiB "
                f"after={fixed_peak / 1024:.0f}KiB "
                f"(+{(fixed_peak - unfixed_peak) / 1024:.0f}KiB, "
                f"{(fixed_peak - unfixed_peak) / size:.0f} bytes/row)"
            ),
        )
        return fixed_queries

    def test_one_transaction_at_600_and_6000_enrollments(self):
        size = SINGLE_TRANSACTION_STUDENTS // 10
        small = self.measure_size(size)
        large = self.measure_size(SINGLE_TRANSACTION_STUDENTS)
        # Queries per enrollment do not grow with the batch: the cost of the
        # extra 9x rows matches the small batch's own per-row cost. Compared as
        # a marginal cost because a batch also pays a small fixed overhead.
        marginal = (large - small) / (9 * size)
        self.assertAlmostEqual(marginal, small / size, delta=0.5)
        print(
            f"queries/row: {size} rows={small / size:.2f} "
            f"{SINGLE_TRANSACTION_STUDENTS} rows={large / (10 * size):.2f} "
            f"marginal={marginal:.2f}",
            flush=True,
        )


class PerRowRosterImportCostTests(RosterScaleCostBase):
    def import_rows(self, students, status=EnrollmentStatusType.ENROLLED):
        rows = [
            RosterRow(first_name=s.first_name, last_name=s.last_name, email=s.email)
            for s in students
        ]
        meter = Meter()
        row_ms = []

        # Each row is its own transaction: wrap only roster_import's own
        # `transaction.atomic` (not Django's, which nested saves also use) to
        # time the row and mark where its commit starts.
        @contextmanager
        def row_atomic(*args, **kwargs):
            meter.phase = "open"
            row_started = time.perf_counter()
            with transaction.atomic(*args, **kwargs):
                transaction.on_commit(meter.enter_commit_phase)
                yield
            row_ms.append((time.perf_counter() - row_started) * 1000)
            meter.phase = "open"

        row_transaction = SimpleNamespace(atomic=row_atomic)
        with meter.recording():
            started = time.perf_counter()
            with patch(
                "classrooms.services.roster_import.transaction", row_transaction
            ):
                result = import_roster(
                    course=self.course, rows=rows, total_processed=len(rows)
                )
            total = time.perf_counter() - started
        self.assertEqual(result["failure_count"], 0, result["results"][:3])
        self.assertEqual(result["success_count"], len(rows))
        self.assertEqual(
            StudentCourse.objects.filter(
                course=self.course, enrollment_status=status
            ).count(),
            len(rows),
            f"every row should be enrolled {status}",
        )
        return meter, {"total": total, "commit": 0.0}, row_ms

    def measure_size(self, size):
        students = self.make_students(2 * size, tag=f"r{size}")
        first, second = students[:size], students[size:]

        # Alternating chunks, not one pass each: the two modes then face the
        # same machine conditions minute by minute, so drift in background
        # load cannot land entirely on whichever pass runs second.
        chunks = min(10, size)
        step = size // chunks
        unfixed_rows, fixed_rows = [], []
        unfixed_walls, fixed_walls = [], []
        unfixed, fixed = Meter(), Meter()
        for index in range(chunks):
            off_chunk = first[index * step : (index + 1) * step]
            on_chunk = second[index * step : (index + 1) * step]

            self.fresh_course(f"roff-{size}-{index}")
            with fix_switched_off():
                meter, timing, rows_ms = self.import_rows(off_chunk)
            unfixed.merge(meter)
            unfixed_rows += rows_ms
            unfixed_walls.append(timing["total"])

            self.fresh_course(f"ron-{size}-{index}")
            meter, timing, rows_ms = self.import_rows(on_chunk)
            fixed.merge(meter)
            fixed_rows += rows_ms
            fixed_walls.append(timing["total"])

        unfixed_s = {"total": sum(unfixed_walls), "commit": 0.0}
        fixed_s = {"total": sum(fixed_walls), "commit": 0.0}

        self.assertEqual(unfixed.round_trips["commit"], 0)
        self.assertEqual(len(fixed.bump_ms["commit"]), size)
        self.assertEqual(fixed.round_trips["commit"], len(fixed.bump_ms["commit"]))

        self.report(
            f"{size}-row import_roster, one transaction per row",
            fixed,
            unfixed,
            fixed_s,
            unfixed_s,
            extra=(
                f"\nper-row write: before p50={statistics.median(unfixed_rows):.2f}ms "
                f"p95={percentile(unfixed_rows, 0.95):.2f}ms | after "
                f"p50={statistics.median(fixed_rows):.2f}ms "
                f"p95={percentile(fixed_rows, 0.95):.2f}ms"
            ),
        )
        return sum(fixed.round_trips.values()) / size

    def test_import_roster_at_200_and_2000_rows(self):
        small = self.measure_size(ROSTER_IMPORT_ROWS // 10)
        large = self.measure_size(ROSTER_IMPORT_ROWS)
        # Round trips per row stay flat as the file grows.
        self.assertEqual(large, small)

    def test_first_import_of_never_signed_in_students_is_two_bumps_per_row(self):
        """The PENDING path: each row saves the account (fresh temporary
        password) and creates the enrollment, so it bumps twice, and each
        bump is replayed once at the row's own commit. Still flat per row.

        Fix ON only: the before/after comparison is the test above. Sizes
        are a tenth of that test's, since the per-row figure is exact.
        """
        per_row = {}
        for size in (max(1, ROSTER_IMPORT_ROWS // 100), ROSTER_IMPORT_ROWS // 10):
            students = self.make_students(size, tag=f"p{size}", signed_in=False)
            self.fresh_course(f"pending-{size}")
            meter, timing, rows_ms = self.import_rows(
                students, status=EnrollmentStatusType.PENDING
            )
            self.assertEqual(len(meter.bump_ms["open"]), 2 * size)
            self.assertEqual(len(meter.bump_ms["commit"]), 2 * size)
            self.assertEqual(meter.round_trips["commit"], 2 * size)
            per_row[size] = sum(meter.round_trips.values()) / size
            print(
                f"\n=== {size}-row import_roster, never-signed-in students "
                f"(PENDING), fix ON: wall={timing['total']:.2f}s "
                f"round_trips/row={per_row[size]:.2f} per-row write "
                f"p50={statistics.median(rows_ms):.2f}ms\n{meter.summary()}",
                flush=True,
            )
        small, large = per_row.values()
        self.assertEqual(large, small)
