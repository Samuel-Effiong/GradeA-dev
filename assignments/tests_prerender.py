"""Tests for prerender_assignment_pdfs and the publish hook that fires it.

Publishing is the one moment the PDF cache is guaranteed cold (a new
assignment has never been rendered) and also the moment a whole class
opens the same assignment at once. Rendering ahead of that burst turns it
into cache hits. The renderer is mocked here - what's under test is when
the task runs, what it caches, and how it behaves when things go wrong.
"""

import threading
import time
from unittest.mock import patch

from django.core.cache import cache
from django.db import connections
from django.test import TestCase, TransactionTestCase

from assignments import pdf_cache
from assignments.models import Assignment, AssignmentStatus
from assignments.pdf_renderer import PDFRendererBusy
from assignments.tasks import prerender_assignment_pdfs
from assignments.tests_download_pdf import objective_question
from assignments.tests_rigor import RigorFixtureMixin


class PrerenderTaskTest(RigorFixtureMixin, TestCase):
    def setUp(self):
        cache.clear()
        pdf_cache._inflight.clear()
        self.course = self.make_course(suffix="-prerender")
        self.assignment = Assignment.objects.create(
            title="Warm Me",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            total_points=5,
            questions=[objective_question()],
        )

    def tearDown(self):
        cache.clear()
        pdf_cache._inflight.clear()

    @patch("assignments.pdf_document.render_assignment_pdf")
    def test_warms_both_views(self, mock_render):
        # Distinct bytes per view so we can prove they aren't crossed:
        # the teacher's copy carries rubrics the student's must not.
        mock_render.side_effect = lambda a, inc: (
            b"%PDF-teacher" if inc else b"%PDF-student"
        )

        result = prerender_assignment_pdfs(str(self.assignment.id))

        self.assertEqual(mock_render.call_count, 2)
        self.assertEqual(
            pdf_cache.get_cached_pdf(self.assignment, "student"), b"%PDF-student"
        )
        self.assertEqual(
            pdf_cache.get_cached_pdf(self.assignment, "teacher"), b"%PDF-teacher"
        )
        self.assertIn("student", result)
        self.assertIn("teacher", result)

    @patch("assignments.pdf_document.render_assignment_pdf")
    def test_skips_views_that_are_already_cached(self, mock_render):
        mock_render.return_value = b"%PDF-x"
        pdf_cache.store_pdf(self.assignment, "student", b"%PDF-already")

        prerender_assignment_pdfs(str(self.assignment.id))

        # Only the teacher view needed rendering.
        self.assertEqual(mock_render.call_count, 1)
        self.assertEqual(
            pdf_cache.get_cached_pdf(self.assignment, "student"), b"%PDF-already"
        )

    @patch("assignments.pdf_document.render_assignment_pdf")
    def test_does_nothing_for_an_unpublished_assignment(self, mock_render):
        draft = Assignment.objects.create(
            title="Draft",
            course=self.course,
            status=AssignmentStatus.DRAFT,
            questions=[objective_question()],
        )

        result = prerender_assignment_pdfs(str(draft.id))

        mock_render.assert_not_called()
        self.assertIn("not published", result)

    @patch("assignments.pdf_document.render_assignment_pdf")
    def test_does_nothing_for_an_assignment_with_no_questions(self, mock_render):
        empty = Assignment.objects.create(
            title="Empty",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[],
        )

        result = prerender_assignment_pdfs(str(empty.id))

        mock_render.assert_not_called()
        self.assertIn("no questions", result)

    @patch("assignments.pdf_document.render_assignment_pdf")
    def test_a_deleted_assignment_does_not_raise(self, mock_render):
        """
        The task is dispatched on commit, so the assignment can be gone by
        the time a worker picks it up. That must not become a stack trace
        in the worker log.
        """
        missing = "00000000-0000-0000-0000-000000000000"

        result = prerender_assignment_pdfs(missing)

        mock_render.assert_not_called()
        self.assertIn("no longer exists", result)

    @patch("assignments.pdf_document.render_assignment_pdf")
    def test_a_render_failure_is_swallowed_rather_than_retried(self, mock_render):
        """
        Pre-rendering only warms a cache, so a broken document must not
        keep a worker busy retrying - the download path will surface the
        real error to the teacher, who can act on it.
        """
        mock_render.side_effect = RuntimeError("bad document")

        result = prerender_assignment_pdfs(str(self.assignment.id))  # must not raise

        self.assertIsNone(pdf_cache.get_cached_pdf(self.assignment, "student"))
        self.assertIn("nothing", result)

    @patch("assignments.pdf_document.render_assignment_pdf")
    def test_load_shedding_defers_the_work_instead_of_dropping_it(self, mock_render):
        """
        Being shed means the renderer is busy with real users. Pre-warming
        is exactly the work that should yield and come back later, so this
        one case retries rather than giving up.
        """
        mock_render.side_effect = PDFRendererBusy("at capacity")

        with patch.object(prerender_assignment_pdfs, "retry") as mock_retry:
            mock_retry.side_effect = RuntimeError("retry called")
            with self.assertRaises(RuntimeError):
                prerender_assignment_pdfs(str(self.assignment.id))

        mock_retry.assert_called_once()


class PublishHookTest(RigorFixtureMixin, TestCase):
    """
    The signal wiring: publishing dispatches the pre-render.

    Every test drives the save inside captureOnCommitCallbacks(execute=True)
    because the hook runs via transaction.on_commit, and TestCase rolls
    each test back so those callbacks would otherwise never fire - which
    would make the negative assertions below pass even if the hook were
    completely broken.
    """

    PRERENDER = "assignments.tasks.prerender_assignment_pdfs"

    def setUp(self):
        cache.clear()
        self.course = self.make_course(suffix="-publishhook")

    def _dispatched(self, mock_delay):
        return [c.args[0].name for c in mock_delay.call_args_list if c.args]

    def _create(self, title, status):
        with self.captureOnCommitCallbacks(execute=True):
            return Assignment.objects.create(
                title=title,
                course=self.course,
                status=status,
                questions=[objective_question()],
            )

    @patch("AutoGrader.dispatch.safe_delay")
    def test_publishing_dispatches_a_prerender(self, mock_delay):
        self._create("Newly Published", AssignmentStatus.PUBLISHED)
        self.assertIn(self.PRERENDER, self._dispatched(mock_delay))

    @patch("AutoGrader.dispatch.safe_delay")
    def test_creating_a_draft_does_not_dispatch_a_prerender(self, mock_delay):
        self._create("Still A Draft", AssignmentStatus.DRAFT)
        self.assertNotIn(self.PRERENDER, self._dispatched(mock_delay))

    @patch("AutoGrader.dispatch.safe_delay")
    def test_a_draft_later_published_dispatches_then(self, mock_delay):
        assignment = self._create("Draft First", AssignmentStatus.DRAFT)
        self.assertNotIn(self.PRERENDER, self._dispatched(mock_delay))

        mock_delay.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            assignment.status = AssignmentStatus.PUBLISHED
            assignment.save()

        self.assertIn(self.PRERENDER, self._dispatched(mock_delay))

    @patch("AutoGrader.dispatch.safe_delay")
    def test_saving_an_already_published_assignment_does_not_re_dispatch(
        self, mock_delay
    ):
        """
        The hook keys off the transition into PUBLISHED, so routine saves
        of a published assignment must not keep re-queueing renders.
        """
        assignment = self._create("Published Once", AssignmentStatus.PUBLISHED)
        self.assertIn(self.PRERENDER, self._dispatched(mock_delay))

        mock_delay.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            assignment.total_points = 42
            assignment.save()

        self.assertNotIn(self.PRERENDER, self._dispatched(mock_delay))


class PublishSurvivesABrokerOutageTest(RigorFixtureMixin, TestCase):
    """
    Publishing must not depend on Celery being up.

    Every other test in this file mocks `safe_delay` itself, which proves
    the hook CALLS it but says nothing about what happens when the call
    fails. These dispatches run in a transaction.on_commit hook, so an
    exception escaping one would surface after the assignment was already
    written - the teacher would see an error for a publish that actually
    succeeded, and would very likely try again.

    AutoGrader.dispatch.safe_delay is the thing that must absorb that; here
    it is left REAL and the broker underneath it is broken instead.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.course = self.make_course(suffix="-brokeroutage")

    def _publish_with_broker_error(self, error):
        with patch(
            "assignments.tasks.prerender_assignment_pdfs.delay", side_effect=error
        ), patch(
            "assignments.tasks.send_new_assignment_posted_notification.delay",
            side_effect=error,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                return Assignment.objects.create(
                    title="Published During An Outage",
                    course=self.course,
                    status=AssignmentStatus.PUBLISHED,
                    questions=[objective_question()],
                )

    def test_a_redis_outage_does_not_fail_the_publish(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        assignment = self._publish_with_broker_error(
            RedisConnectionError("broker unreachable")
        )

        assignment.refresh_from_db()
        self.assertEqual(assignment.status, AssignmentStatus.PUBLISHED)
        self.assertTrue(Assignment.objects.filter(pk=assignment.pk).exists())

    def test_an_operational_error_from_the_broker_does_not_fail_the_publish(self):
        from kombu.exceptions import OperationalError

        assignment = self._publish_with_broker_error(OperationalError("cannot connect"))

        assignment.refresh_from_db()
        self.assertEqual(assignment.status, AssignmentStatus.PUBLISHED)

    def test_a_socket_timeout_does_not_fail_the_publish(self):
        assignment = self._publish_with_broker_error(TimeoutError("timed out"))

        assignment.refresh_from_db()
        self.assertEqual(assignment.status, AssignmentStatus.PUBLISHED)

    def test_a_programming_error_in_the_dispatch_still_propagates(self):
        """
        safe_delay swallows OUTAGES, not bugs. A TypeError from calling the
        task wrongly must not be hidden, or a broken dispatch would look
        like a healthy one forever.
        """
        with self.assertRaises(TypeError):
            self._publish_with_broker_error(TypeError("delay() got a bad argument"))

    def test_the_assignment_is_still_downloadable_after_a_failed_prerender(self):
        """
        Pre-rendering only warms a cache. If it never ran, the download
        path must still produce the PDF on demand.
        """
        from redis.exceptions import ConnectionError as RedisConnectionError

        assignment = self._publish_with_broker_error(
            RedisConnectionError("broker unreachable")
        )

        self.assertIsNone(pdf_cache.get_cached_pdf(assignment, "student"))

        with patch(
            "assignments.pdf_document.render_assignment_pdf", return_value=b"%PDF-live"
        ):
            rendered = pdf_cache.get_or_render(
                assignment,
                "student",
                lambda: b"%PDF-live",
            )

        self.assertEqual(rendered, b"%PDF-live")


class PrerenderIdempotencyTest(RigorFixtureMixin, TestCase):
    """
    Duplicate dispatch must be harmless.

    Celery gives at-least-once delivery, and this task is additionally
    dispatched from a signal that could fire more than once for the same
    publish (a retried save, a redelivered message). Running it twice must
    not render twice or produce different bytes.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        pdf_cache._inflight.clear()
        self.addCleanup(pdf_cache._inflight.clear)
        self.course = self.make_course(suffix="-idempotent")
        self.assignment = Assignment.objects.create(
            title="Run Me Twice",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            total_points=5,
            questions=[objective_question()],
        )

    @patch("assignments.pdf_document.render_assignment_pdf")
    def test_running_the_task_twice_renders_each_view_only_once(self, mock_render):
        mock_render.side_effect = lambda a, inc: (
            b"%PDF-teacher" if inc else b"%PDF-student"
        )

        first = prerender_assignment_pdfs(str(self.assignment.id))
        calls_after_first = mock_render.call_count
        second = prerender_assignment_pdfs(str(self.assignment.id))

        self.assertEqual(calls_after_first, 2)
        self.assertEqual(
            mock_render.call_count, 2, "the second run re-rendered an already-warm PDF"
        )
        self.assertIn("student", first)
        self.assertIn("nothing", second)

    @patch("assignments.pdf_document.render_assignment_pdf")
    def test_the_second_run_leaves_the_cached_bytes_identical(self, mock_render):
        mock_render.side_effect = lambda a, inc: (
            b"%PDF-teacher" if inc else b"%PDF-student"
        )

        prerender_assignment_pdfs(str(self.assignment.id))
        before = (
            pdf_cache.get_cached_pdf(self.assignment, "student"),
            pdf_cache.get_cached_pdf(self.assignment, "teacher"),
        )

        prerender_assignment_pdfs(str(self.assignment.id))
        after = (
            pdf_cache.get_cached_pdf(self.assignment, "student"),
            pdf_cache.get_cached_pdf(self.assignment, "teacher"),
        )

        self.assertEqual(before, after)
        self.assertEqual(before[0], b"%PDF-student")
        self.assertEqual(before[1], b"%PDF-teacher")


class PrerenderConcurrentDispatchTest(RigorFixtureMixin, TransactionTestCase):
    """
    Two workers picking up a redelivered message at the same instant.

    TransactionTestCase, not TestCase: the worker threads open their own
    database connections and must be able to SEE the assignment, which an
    uncommitted TestCase transaction would hide from them. Written as
    TestCase first, both threads simply reported "Assignment no longer
    exists" and rendered nothing - a green-looking test that proved
    nothing at all.
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
        self.course = self.make_course(suffix="-concurrentdispatch")
        self.assignment = Assignment.objects.create(
            title="Redelivered Twice",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            total_points=5,
            questions=[objective_question()],
        )

    @patch("assignments.pdf_document.render_assignment_pdf")
    def test_two_concurrent_dispatches_still_render_once_per_view(self, mock_render):
        renders = []
        lock = threading.Lock()

        def slow_render(assignment, include_rubric):
            with lock:
                renders.append(include_rubric)
            time.sleep(0.3)
            return b"%PDF-teacher" if include_rubric else b"%PDF-student"

        mock_render.side_effect = slow_render
        barrier = threading.Barrier(2)
        errors = []

        def run():
            try:
                barrier.wait(timeout=30)
                prerender_assignment_pdfs(str(self.assignment.id))
            except Exception as exc:  # pragma: no cover - a real failure
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [])
        # Exactly one student render and one teacher render - single-flight
        # in pdf_cache collapses the duplicate dispatch rather than paying
        # for the same document twice.
        self.assertEqual(sorted(renders), [False, True], f"rendered {renders}")
        self.assertEqual(
            pdf_cache.get_cached_pdf(self.assignment, "student"), b"%PDF-student"
        )
        self.assertEqual(
            pdf_cache.get_cached_pdf(self.assignment, "teacher"), b"%PDF-teacher"
        )
