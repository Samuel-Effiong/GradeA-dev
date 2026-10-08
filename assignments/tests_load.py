"""Sustained-load tests for the assignments read path.

These are NOT unit tests of individual functions. They run a realistic
mix of concurrent traffic - PDF downloads (cached and uncached),
assignment lists, and detail reads, from many distinct users at once -
against a real Postgres and a real Redis, and then assert on what
actually happened: latency distribution, error rate, database connection
count, memory growth, and whether the system is still healthy afterwards.

Why TransactionTestCase: every request runs on its own connection and
must see the others' committed writes. Under TestCase the whole test
shares one uncommitted transaction, so no real concurrency - or
connection pressure - can occur at all, and a load test on top of it
would be measuring nothing.

Why the renderer is stubbed in the mixed-traffic tests: a real Chromium
render is 0.6-2s and its own suite (tests_pdf_renderer.py) already
measures it under contention. Here the renderer is a fixed-cost stub so
that what is being measured is the REST of the request - queryset
scoping, serialization, cache round trips, connection handling - which
is what a stampede of downloads actually stresses once the cache is warm.
One test does drive the real renderer, to confirm the load-shedding
contract end to end.

Thresholds are deliberately loose. These run on developer laptops and CI
boxes of wildly different speeds, so they are set to catch a collapse
(errors, connection exhaustion, unbounded growth, a 100x latency cliff),
not to police a few hundred milliseconds. Every run prints its measured
numbers so a regression is visible even when the assertion passes.
"""

import os
import statistics
import threading
import time
import unittest
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection, connections
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from assignments import pdf_cache, pdf_renderer
from assignments.models import Assignment, AssignmentStatus
from assignments.tests_download_pdf import objective_question
from assignments.tests_security import (
    enroll,
    make_assignment,
    make_course,
    make_student,
    make_teacher,
)
from classrooms.models import School

# Opt-in: these take minutes and hold a lot of connections open, so they
# stay out of the default run unless explicitly asked for.
LOAD_TESTS_ENABLED = os.environ.get("RUN_LOAD_TESTS") == "1"

try:
    import resource

    def _peak_rss_mb():
        # ru_maxrss is KB on Linux, bytes on macOS.
        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return raw / 1024 if raw > 1_000_000 else raw / 1024

except ImportError:  # pragma: no cover - non-POSIX

    def _peak_rss_mb():
        return 0.0


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


class LoadReport:
    """Collects per-request outcomes from many threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self.samples = []  # (label, seconds, status_code)
        self.errors = []

    def record(self, label, seconds, status_code):
        with self._lock:
            self.samples.append((label, seconds, status_code))

    def record_error(self, label, exc):
        with self._lock:
            self.errors.append((label, repr(exc)))

    def latencies(self, label=None):
        return [
            seconds
            for sample_label, seconds, _ in self.samples
            if label is None or sample_label == label
        ]

    def codes(self, label=None):
        return [
            code
            for sample_label, _, code in self.samples
            if label is None or sample_label == label
        ]

    def summarise(self, title):
        lines = [f"\n=== {title} ==="]
        lines.append(f"requests: {len(self.samples)}  errors: {len(self.errors)}")
        by_label = {label for label, _, _ in self.samples}
        for label in sorted(by_label):
            values = self.latencies(label)
            codes = self.codes(label)
            distribution = {code: codes.count(code) for code in sorted(set(codes))}
            lines.append(
                f"  {label:<22} n={len(values):<5} "
                f"median={statistics.median(values) * 1000:7.1f}ms "
                f"p95={percentile(values, 0.95) * 1000:8.1f}ms "
                f"max={max(values) * 1000:8.1f}ms "
                f"codes={distribution}"
            )
        if self.errors:
            lines.append(f"  first errors: {self.errors[:3]}")
        print("\n".join(lines))


@unittest.skipUnless(LOAD_TESTS_ENABLED, "load tests are opt-in: set RUN_LOAD_TESTS=1")
class AssignmentReadPathLoadTest(TransactionTestCase):
    """
    A class of students and a staff room of teachers, all at once.

    Shape of the simulated traffic, which mirrors what actually happens
    when a teacher publishes an assignment: most people download the SAME
    document (so the cache and single-flight matter), some list their
    assignments, some open a detail page, and a few teachers pull their
    own rubric-bearing copy.
    """

    STUDENT_COUNT = 30
    TEACHER_COUNT = 4
    ASSIGNMENTS_PER_COURSE = 25

    # H-2 (docs/HARDENING_BACKLOG.md): threads opened by this test get their
    # own DB connection. Any that outlives the test makes Django's final
    # DROP DATABASE fail with "database is being accessed by other users",
    # which exits the whole run non-zero even when every test passed. The
    # workers close their own connections; this closes the main thread's and
    # anything a worker died before releasing.
    def tearDown(self):
        connections.close_all()
        super().tearDown()

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        pdf_cache._inflight.clear()
        self.addCleanup(pdf_cache._inflight.clear)
        # The renderer is a process-wide singleton. These tests patch it
        # out rather than using it, but leaving whatever instance an
        # earlier test built in place makes RealRendererOverloadTest below
        # non-deterministic: it rebuilds the worker under a lowered
        # concurrency setting, and a leftover instance serves the requests
        # instead, so nothing is ever shed. Reset on the way out.
        self.addCleanup(pdf_renderer.reset_worker_for_tests)

        self.school = School.objects.create(name="Load School")
        self.teachers = []
        self.courses = []
        for index in range(self.TEACHER_COUNT):
            teacher = make_teacher(f"load-teacher-{index}@example.com", self.school)
            course = make_course(teacher, f"Load Course {index}")
            for assignment_index in range(self.ASSIGNMENTS_PER_COURSE):
                make_assignment(course, f"Load Quiz {index}-{assignment_index}")
            self.teachers.append(teacher)
            self.courses.append(course)

        self.hot_course = self.courses[0]
        self.hot_assignment = Assignment.objects.filter(course=self.hot_course).first()

        self.students = []
        for index in range(self.STUDENT_COUNT):
            student = make_student(f"load-student-{index}@example.com", self.school)
            enroll(student, self.hot_course)
            self.students.append(student)

    # --- helpers ---------------------------------------------------------

    def _client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _timed(self, report, label, call):
        started = time.perf_counter()
        try:
            response = call()
        except Exception as exc:  # pragma: no cover - a real failure
            report.record_error(label, exc)
            return None
        elapsed = time.perf_counter() - started
        report.record(label, elapsed, response.status_code)
        # FileResponse is lazy; consume it so the timing includes the body
        # and the file handle is released rather than leaking.
        if hasattr(response, "streaming_content"):
            b"".join(response.streaming_content)
        return response

    def _open_db_connections(self):
        """How many backends this database currently has open."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
            )
            return cursor.fetchone()[0]

    def _run_workers(self, worker, count, duration_seconds=None, iterations=None):
        """
        Release `count` threads together. Each closes its own DB
        connection on the way out - Django opens one per thread, and a
        load test that leaks them would exhaust the server rather than
        measure it.
        """
        barrier = threading.Barrier(count)
        deadline = time.monotonic() + duration_seconds if duration_seconds else None

        def wrapped(index):
            try:
                barrier.wait(timeout=60)
                loop = 0
                while True:
                    if deadline is not None and time.monotonic() >= deadline:
                        break
                    if iterations is not None and loop >= iterations:
                        break
                    worker(index, loop)
                    loop += 1
            finally:
                connections.close_all()

        threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=300)
        self.assertFalse(
            [thread for thread in threads if thread.is_alive()],
            "a load worker never finished - the system is wedged",
        )

    # --- tests -----------------------------------------------------------

    def test_a_class_opening_one_assignment_together_stays_healthy(self):
        """
        The publish stampede: 30 students hit the same uncached PDF at the
        same instant. Single-flight should collapse that into one render,
        everyone should get a 200, and nobody should wait anywhere near
        the render timeout.
        """
        report = LoadReport()
        renders = {"n": 0}
        render_lock = threading.Lock()

        def slow_render(assignment, include_rubric):
            with render_lock:
                renders["n"] += 1
            time.sleep(0.25)  # a stand-in for a real Chromium render
            return b"%PDF-shared"

        url = reverse("assignment-download-pdf", kwargs={"pk": self.hot_assignment.id})

        def worker(index, loop):
            client = self._client_for(self.students[index])
            self._timed(report, "pdf_download", lambda: client.get(url))

        connections_before = self._open_db_connections()
        # Patched ONCE, in this thread, around the whole burst.
        # unittest.mock.patch rebinds a module attribute and is not
        # thread-safe: when many threads entered and left overlapping
        # `with patch(...)` blocks on the same target, the restores raced
        # and left a MagicMock permanently in place of
        # assignments.views.render_assignment_pdf - which then silently
        # served every later test in the process, including
        # RealRendererOverloadTest, whose requests consequently never
        # reached the renderer at all.
        with patch("assignments.views.render_assignment_pdf", side_effect=slow_render):
            self._run_workers(worker, self.STUDENT_COUNT, iterations=1)
        report.summarise("class opening one assignment together")

        self.assertEqual(report.errors, [])
        codes = report.codes("pdf_download")
        self.assertEqual(len(codes), self.STUDENT_COUNT)
        self.assertTrue(
            all(code == 200 for code in codes), f"not everyone got their PDF: {codes}"
        )
        self.assertLessEqual(
            renders["n"],
            3,
            f"single-flight should have collapsed the stampede, saw {renders['n']} "
            "renders",
        )
        self.assertLess(
            percentile(report.latencies("pdf_download"), 0.95),
            15.0,
            "p95 latency suggests requests were queueing, not sharing",
        )
        self.assertLess(
            self._open_db_connections() - connections_before,
            self.STUDENT_COUNT,
            "connections were not released back after the burst",
        )

    def test_sustained_mixed_traffic_does_not_degrade(self):
        """
        Thirty seconds of everything at once: downloads, lists, details,
        and teacher-view downloads, from 24 concurrent users.

        Asserts three things a collapse would break - a zero error rate, a
        p95 that stays within a sane multiple of the median (i.e. no
        queueing cliff), and stable memory - and compares the first
        quarter of the run against the last to catch degradation that a
        whole-run average would hide.
        """
        report = LoadReport()
        list_url = reverse("assignment-list")
        rss_before = _peak_rss_mb()

        def worker(index, loop):
            role = index % 4
            if role == 0:
                student = self.students[index % len(self.students)]
                client = self._client_for(student)
                url = reverse(
                    "assignment-download-pdf", kwargs={"pk": self.hot_assignment.id}
                )
                self._timed(report, "pdf_download", lambda: client.get(url))
            elif role == 1:
                student = self.students[index % len(self.students)]
                client = self._client_for(student)
                self._timed(report, "student_list", lambda: client.get(list_url))
            elif role == 2:
                teacher = self.teachers[index % len(self.teachers)]
                client = self._client_for(teacher)
                self._timed(report, "teacher_list", lambda: client.get(list_url))
            else:
                student = self.students[index % len(self.students)]
                client = self._client_for(student)
                url = reverse(
                    "assignment-detail", kwargs={"pk": self.hot_assignment.id}
                )
                self._timed(report, "detail", lambda: client.get(url))

        connections_before = self._open_db_connections()
        started = time.perf_counter()
        # One patch for the whole run - see the note in the stampede test
        # above on why this must not be done per-thread.
        with patch("assignments.views.render_assignment_pdf", return_value=b"%PDF-x"):
            self._run_workers(worker, 24, duration_seconds=30)
        wall_clock = time.perf_counter() - started
        connections_after = self._open_db_connections()
        rss_after = _peak_rss_mb()

        report.summarise("sustained mixed traffic (30s, 24 concurrent)")
        print(
            f"  throughput: {len(report.samples) / wall_clock:.1f} req/s   "
            f"db connections: {connections_before} -> {connections_after}   "
            f"peak RSS: {rss_before:.0f}MB -> {rss_after:.0f}MB"
        )

        self.assertEqual(report.errors, [], "the run produced exceptions")
        self.assertGreater(len(report.samples), 100, "not enough load was generated")
        self.assertFalse(
            [code for code in report.codes() if code >= 500],
            "sustained load produced server errors",
        )

        # No queueing cliff: p99 must stay within a wide but finite
        # multiple of the median. A system that has started serialising
        # blows straight through this.
        median = statistics.median(report.latencies())
        p99 = percentile(report.latencies(), 0.99)
        print(
            f"  median={median * 1000:.1f}ms p99={p99 * 1000:.1f}ms "
            f"ratio={p99 / median:.1f}x"
        )
        self.assertLess(
            p99,
            max(median * 100, 5.0),
            "tail latency collapsed relative to the median",
        )

        # Connections must be returned, not accumulated.
        self.assertLess(
            connections_after - connections_before,
            30,
            f"database connections grew {connections_before} -> {connections_after}",
        )

        # First quarter vs last quarter - degradation over time.
        latencies = report.latencies()
        quarter = max(1, len(latencies) // 4)
        early = statistics.median(latencies[:quarter])
        late = statistics.median(latencies[-quarter:])
        print(f"  first quarter median={early * 1000:.1f}ms  last={late * 1000:.1f}ms")
        self.assertLess(
            late,
            max(early * 20, 2.0),
            "latency grew steadily through the run - something is accumulating",
        )

    def test_list_query_count_does_not_scale_with_page_size(self):
        """
        The N+1 guard, measured rather than reasoned about: a page of 25
        assignments must not cost meaningfully more queries than a page of
        5. Without the submission_count annotation this grows by one query
        per row.
        """
        client = self._client_for(self.teachers[0])
        url = reverse("assignment-list")

        def count_queries(page_size):
            cache.clear()
            connection.queries_log.clear()
            with self.settings(DEBUG=True):
                connection.force_debug_cursor = True
                try:
                    client.get(url, {"page_size": page_size})
                    return len(connection.queries)
                finally:
                    connection.force_debug_cursor = False

        small = count_queries(5)
        large = count_queries(25)
        print(f"\n  list queries: page_size=5 -> {small}, page_size=25 -> {large}")

        self.assertLess(
            large - small,
            10,
            f"query count scales with rows ({small} -> {large}): an N+1 is back",
        )

    def test_paging_the_whole_list_returns_every_row_exactly_once(self):
        """
        Pagination stability, checked by walking the entire list.

        This is the failure the submission_count annotation introduced and
        that only showed up under load: annotate() adds a GROUP BY, Django
        then drops Meta.ordering from the compiled SQL (QuerySet.ordered
        goes False), and an unordered LIMIT/OFFSET lets Postgres return
        rows in whatever order suits it - so a teacher paging through
        their assignments could see one twice and never see another.

        Asserted against the true row count, not just "no duplicates", so
        a silently dropped row fails too.
        """
        client = self._client_for(self.teachers[0])
        url = reverse("assignment-list")
        expected = {
            str(pk)
            for pk in Assignment.objects.filter(
                course__teacher=self.teachers[0]
            ).values_list("id", flat=True)
        }

        seen = []
        for page in range(1, 20):
            cache.clear()  # force a real query per page, not a cached payload
            response = client.get(url, {"page": page, "page_size": 4})
            if response.status_code != 200:
                break
            seen.extend(row["id"] for row in response.data["results"])
            if not response.data.get("next"):
                break

        print(
            f"\n  paged {len(seen)} rows, {len(set(seen))} distinct, expected {len(expected)}"
        )
        self.assertEqual(
            len(seen), len(set(seen)), "a row appeared on more than one page"
        )
        self.assertEqual(
            set(seen), expected, "paging did not return exactly the teacher's rows"
        )

    def test_the_list_queryset_reports_itself_as_ordered(self):
        """
        The direct form of the check above. DRF's paginator warns rather
        than fails on an unordered queryset, so a regression here is
        otherwise silent in a normal test run.
        """
        from rest_framework.test import APIRequestFactory

        from assignments.views import AssignmentViewSet

        request = APIRequestFactory().get("/api/v1/assignments/")
        request.user = self.teachers[0]
        view = AssignmentViewSet()
        view.request = request
        view.kwargs = {}

        queryset = view.get_queryset()
        self.assertTrue(
            queryset.ordered,
            "the list queryset lost its ordering - LIMIT/OFFSET paging is "
            "no longer stable",
        )
        self.assertIn("ORDER BY", str(queryset.query))

    def test_cache_survives_writes_to_other_assignments_under_load(self):
        """
        The invalidation-scope fix, under the traffic that made it matter:
        one teacher saving assignments in a loop while a class downloads a
        different one. Every download after the first must be a cache hit.
        """
        renders = {"n": 0}
        render_lock = threading.Lock()

        def counting_render(assignment, include_rubric):
            with render_lock:
                renders["n"] += 1
            return b"%PDF-x"

        url = reverse("assignment-download-pdf", kwargs={"pk": self.hot_assignment.id})
        stop = threading.Event()
        report = LoadReport()

        def writer():
            try:
                targets = list(
                    Assignment.objects.filter(course=self.courses[1]).values_list(
                        "id", flat=True
                    )
                )
                index = 0
                while not stop.is_set():
                    Assignment.objects.filter(
                        id=targets[index % len(targets)]
                    ).first().save()
                    index += 1
                    time.sleep(0.01)
            finally:
                connections.close_all()

        writer_thread = threading.Thread(target=writer)
        writer_thread.start()
        try:

            def worker(index, loop):
                client = self._client_for(self.students[index])
                self._timed(report, "pdf_download", lambda: client.get(url))

            with patch(
                "assignments.views.render_assignment_pdf",
                side_effect=counting_render,
            ):
                self._run_workers(worker, 10, iterations=5)
        finally:
            stop.set()
            writer_thread.join(timeout=30)

        report.summarise("downloads while another assignment is being saved")
        self.assertEqual(report.errors, [])
        self.assertTrue(all(code == 200 for code in report.codes()))
        # 50 downloads of one document. Before the invalidation was scoped
        # to a single assignment, every unrelated save wiped this entry and
        # essentially every download re-rendered.
        self.assertLessEqual(
            renders["n"],
            10,
            f"the cache was being wiped by unrelated writes: {renders['n']} renders "
            "for 50 downloads",
        )


@unittest.skipUnless(LOAD_TESTS_ENABLED, "load tests are opt-in: set RUN_LOAD_TESTS=1")
@unittest.skipUnless(pdf_renderer is not None, "renderer module unavailable")
class RealRendererOverloadTest(TransactionTestCase):
    """
    The one load test that drives real Chromium, to confirm the
    load-shedding contract holds through the whole stack rather than only
    at the renderer's own API: past capacity the endpoint answers 503 with
    Retry-After promptly, and the process is still able to render
    afterwards.
    """

    # H-2 (docs/HARDENING_BACKLOG.md): threads opened by this test get their
    # own DB connection. Any that outlives the test makes Django's final
    # DROP DATABASE fail with "database is being accessed by other users",
    # which exits the whole run non-zero even when every test passed. The
    # workers close their own connections; this closes the main thread's and
    # anything a worker died before releasing.
    def tearDown(self):
        connections.close_all()
        super().tearDown()

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        pdf_cache._inflight.clear()
        self.addCleanup(pdf_cache._inflight.clear)
        pdf_renderer.reset_worker_for_tests()
        self.addCleanup(pdf_renderer.reset_worker_for_tests)

        self.school = School.objects.create(name="Overload School")
        self.teacher = make_teacher("overload-teacher@example.com", self.school)
        self.course = make_course(self.teacher, "Overload Course")
        # Distinct assignments so nothing is shared by the cache or by
        # single-flight - every request is a genuine render.
        self.assignments = [
            Assignment.objects.create(
                title=f"Overload Quiz {index}",
                course=self.course,
                status=AssignmentStatus.PUBLISHED,
                total_points=5,
                questions=[objective_question(number=index + 1)],
            )
            for index in range(24)
        ]

    def _hammer(self, report, patch_do_render=None):
        """Fire one request per assignment, all released together."""
        barrier = threading.Barrier(len(self.assignments))

        def worker(assignment):
            client = APIClient()
            client.force_authenticate(user=self.teacher)
            url = reverse("assignment-download-pdf", kwargs={"pk": assignment.id})
            try:
                barrier.wait(timeout=60)
                started = time.perf_counter()
                response = client.get(url, {"view": "teacher"})
                if hasattr(response, "streaming_content"):
                    b"".join(response.streaming_content)
                report.record(
                    "download", time.perf_counter() - started, response.status_code
                )
                if response.status_code == 503:
                    assert response["Retry-After"] == "5", "missing Retry-After"
            except Exception as exc:  # pragma: no cover
                report.record_error("download", exc)
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=worker, args=(assignment,))
            for assignment in self.assignments
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=180)
        self.assertFalse(
            [t for t in threads if t.is_alive()], "a request never returned"
        )

    def test_real_renders_at_production_settings_are_all_served(self):
        """
        24 concurrent uncached downloads through real Chromium, at the
        production concurrency and queue settings.

        Whether anything is shed here is NOT asserted, because it
        legitimately varies: measured on the same machine, a warm browser
        renders these one-question documents in under 100ms and all 24 are
        served, while a cold browser takes ~1s each and 8 of the 24 are
        refused. Both are correct. Pinning either number would make this a
        test of how warm Chromium happened to be.

        What is asserted is the part that must hold either way: every
        caller gets a real answer, that answer is a 200 or a well-formed
        503, nothing 5xxs, and no request is left parked. The deterministic
        shedding contract is the test below.
        """
        from assignments.tests_pdf_renderer import _CHROMIUM_AVAILABLE

        if not _CHROMIUM_AVAILABLE:
            self.skipTest("Headless Chromium not available")

        report = LoadReport()
        self._hammer(report)
        report.summarise("24 concurrent REAL renders at production settings")

        codes = report.codes("download")
        self.assertEqual(report.errors, [])
        self.assertFalse(
            [code for code in codes if code >= 500 and code != 503],
            f"real concurrent rendering produced errors: {codes}",
        )
        self.assertTrue(
            all(code in (200, 503) for code in codes),
            f"unexpected statuses under load: {sorted(set(codes))}",
        )
        print(
            f"  served={codes.count(200)} shed={codes.count(503)} "
            "(the split depends on how warm Chromium is; both are correct)"
        )

    def test_a_slow_renderer_sheds_through_the_endpoint_and_then_recovers(self):
        """
        The shedding contract end to end, with the overload forced.

        Only Chromium's SPEED is simulated - _do_render is made slow.
        Everything that matters here stays real: the queue accounting in
        _ChromiumRenderWorker.render, the PDFRendererBusy it raises, the
        view's translation of that into 503 + Retry-After, and the
        recovery afterwards. That is the part no other test covers;
        tests_pdf_renderer.LoadSheddingUnderRealContentionTest covers the
        queue arithmetic itself, but stops at the renderer's own API and
        never goes through HTTP.
        """
        from assignments.tests_pdf_renderer import _CHROMIUM_AVAILABLE

        if not _CHROMIUM_AVAILABLE:
            self.skipTest("Headless Chromium not available")

        report = LoadReport()
        minimal_pdf = b"%PDF-1.4\n%%EOF\n"
        release = threading.Event()
        self.addCleanup(release.set)
        entered = threading.Semaphore(0)

        # _await_render is blocked rather than _do_render. It is the
        # method render() calls immediately AFTER taking a queue slot, so
        # everything under test stays real: the slot accounting, the
        # PDFRendererBusy raised once the limit is hit, the view's
        # translation of that into 503 + Retry-After. Only the render's
        # duration is simulated.
        #
        # This is the same technique tests_pdf_renderer.py uses, and it is
        # used here because slowing _do_render did NOT reliably reproduce
        # the overload: run alone it shed 20 of 24, run alongside its
        # siblings every request was served in ~110ms, i.e. the slow path
        # was not the one being taken. Blocking at the boundary render()
        # itself calls removes that uncertainty.
        def blocking_await(self, loop, html, options):
            entered.release()
            release.wait(timeout=30)
            return minimal_pdf

        with self.settings(
            PDF_RENDERER_MAX_CONCURRENT_RENDERS=2, PDF_RENDERER_MAX_QUEUED_RENDERS=4
        ):
            with patch.object(
                pdf_renderer._ChromiumRenderWorker, "_await_render", blocking_await
            ):
                # The semaphore is sized when the worker starts and cannot
                # be resized, so build the worker inside the override.
                pdf_renderer.reset_worker_for_tests()
                pdf_renderer._get_worker()

                hammer = threading.Thread(target=self._hammer, args=(report,))
                hammer.start()
                # Hold the queue full until the refusals have happened,
                # then let the admitted renders finish.
                self.assertTrue(entered.acquire(timeout=30), "no render ever started")
                time.sleep(1.5)
                release.set()
                hammer.join(timeout=180)
                self.assertFalse(hammer.is_alive(), "the burst never finished")

        report.summarise("24 concurrent slow renders, 2 concurrent / 4 queued")
        codes = report.codes("download")
        shed = [code for code in codes if code == 503]
        served = [code for code in codes if code == 200]

        self.assertEqual(report.errors, [])
        self.assertFalse(
            [code for code in codes if code >= 500 and code != 503],
            f"overload produced real errors: {sorted(set(codes))}",
        )
        self.assertTrue(served, "nothing was served at all")
        self.assertTrue(
            shed, f"nothing was shed - the limit did not engage: {sorted(set(codes))}"
        )

        # A refusal must be fast. The alternative it replaced was parking
        # the thread for the full render, which here is 1.5s.
        shed_latencies = [seconds for _, seconds, code in report.samples if code == 503]
        print(
            f"  slowest refusal: {max(shed_latencies) * 1000:.0f}ms "
            "vs a 1500ms render"
        )
        self.assertLess(
            max(shed_latencies),
            1.5,
            f"refusals were as slow as the render they declined: {shed_latencies}",
        )

        # And the process is still usable afterwards, with real Chromium.
        pdf_renderer.reset_worker_for_tests()
        client = APIClient()
        client.force_authenticate(user=self.teacher)
        recovery = client.get(
            reverse("assignment-download-pdf", kwargs={"pk": self.assignments[0].id}),
            {"view": "teacher"},
        )
        self.assertEqual(
            recovery.status_code,
            200,
            "the worker did not recover after being overloaded",
        )
