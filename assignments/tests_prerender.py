"""Tests for prerender_assignment_pdfs and the publish hook that fires it.

Publishing is the one moment the PDF cache is guaranteed cold (a new
assignment has never been rendered) and also the moment a whole class
opens the same assignment at once. Rendering ahead of that burst turns it
into cache hits. The renderer is mocked here - what's under test is when
the task runs, what it caches, and how it behaves when things go wrong.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

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
