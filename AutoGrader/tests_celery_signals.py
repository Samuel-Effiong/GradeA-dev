"""
Tests for the request-id propagation wired into AutoGrader.celery_signals.

Two layers are covered deliberately:

  - Unit tests against the signal receiver functions directly, with fake
    task/request objects - fast, and pin down each function's behavior in
    isolation (including the eager-vs-real-broker header shape difference
    documented in _extract_request_id).

  - One end-to-end test that spins up a real (non-eager) Celery worker
    against an in-memory broker and round-trips an actual task through
    .delay(), to prove the before_task_publish -> message headers ->
    task_prerun wiring works together against this project's real signal
    handlers, not just each piece in isolation. task_always_eager bypasses
    the message/headers pipeline entirely (see the module docstring in
    celery_signals.py), so a pure-eager test would not have caught a wiring
    bug here - this is why the E2E test insists on eager=False.
"""

from types import SimpleNamespace

from celery import Celery
from celery.contrib.testing.worker import start_worker
from django.test import SimpleTestCase, TestCase

from AutoGrader import celery_signals
from AutoGrader.request_context import get_request_id, reset_request_id, set_request_id


class ExtractRequestIdTests(SimpleTestCase):
    def test_none_task_returns_none(self):
        self.assertIsNone(celery_signals._extract_request_id(None))

    def test_missing_request_returns_none(self):
        task = SimpleNamespace()
        self.assertIsNone(celery_signals._extract_request_id(task))

    def test_direct_attribute_is_used_when_present(self):
        # The shape produced by a real (non-eager) broker round trip.
        request = SimpleNamespace(request_id="from-attr", headers={})
        task = SimpleNamespace(request=request)
        self.assertEqual(celery_signals._extract_request_id(task), "from-attr")

    def test_falls_back_to_headers_dict(self):
        # The shape produced by CELERY_TASK_ALWAYS_EAGER=True.
        request = SimpleNamespace(headers={"request_id": "from-headers"})
        task = SimpleNamespace(request=request)
        self.assertEqual(celery_signals._extract_request_id(task), "from-headers")

    def test_none_headers_does_not_raise(self):
        request = SimpleNamespace(headers=None)
        task = SimpleNamespace(request=request)
        self.assertIsNone(celery_signals._extract_request_id(task))

    def test_no_request_id_anywhere_returns_none(self):
        request = SimpleNamespace(headers={"other_key": "x"})
        task = SimpleNamespace(request=request)
        self.assertIsNone(celery_signals._extract_request_id(task))


class BeforeTaskPublishHandlerTests(SimpleTestCase):
    def test_stamps_current_request_id_into_headers(self):
        token = set_request_id("dispatch-id")
        try:
            headers = {}
            celery_signals._stamp_request_id_on_publish(headers=headers)
            self.assertEqual(headers["request_id"], "dispatch-id")
        finally:
            reset_request_id(token)

    def test_does_nothing_when_no_request_id_set(self):
        self.assertIsNone(get_request_id())
        headers = {}
        celery_signals._stamp_request_id_on_publish(headers=headers)
        self.assertNotIn("request_id", headers)

    def test_does_not_raise_when_headers_is_none(self):
        token = set_request_id("dispatch-id")
        try:
            # Defensive: should never happen in real Celery (it always
            # passes a dict), but a signal receiver must not crash the
            # publish path over a signature-shape surprise.
            celery_signals._stamp_request_id_on_publish(headers=None)
        finally:
            reset_request_id(token)


class TaskPrerunPostrunHandlerTests(SimpleTestCase):
    def tearDown(self):
        celery_signals._tokens_by_task_id.clear()

    def test_prerun_sets_contextvar_from_task_request(self):
        request = SimpleNamespace(request_id="worker-id", headers={})
        task = SimpleNamespace(request=request)

        self.assertIsNone(get_request_id())
        celery_signals._restore_request_id_on_prerun(task_id="t1", task=task)
        self.assertEqual(get_request_id(), "worker-id")

        celery_signals._clear_request_id_on_postrun(task_id="t1")
        self.assertIsNone(get_request_id())

    def test_prerun_is_noop_when_task_has_no_request_id(self):
        request = SimpleNamespace(headers={})
        task = SimpleNamespace(request=request)

        celery_signals._restore_request_id_on_prerun(task_id="t2", task=task)
        self.assertIsNone(get_request_id())
        self.assertNotIn("t2", celery_signals._tokens_by_task_id)

    def test_postrun_with_unknown_task_id_does_not_raise(self):
        celery_signals._clear_request_id_on_postrun(task_id="never-seen")

    def test_sequential_tasks_do_not_leak_request_id(self):
        # Simulates a prefork worker process handling task A (which has a
        # request id) followed by task B (which does not, e.g. a periodic
        # beat task) - B must not inherit A's id.
        request_a = SimpleNamespace(request_id="task-a-id", headers={})
        task_a = SimpleNamespace(request=request_a)
        celery_signals._restore_request_id_on_prerun(task_id="a", task=task_a)
        self.assertEqual(get_request_id(), "task-a-id")
        celery_signals._clear_request_id_on_postrun(task_id="a")
        self.assertIsNone(get_request_id())

        request_b = SimpleNamespace(headers={})
        task_b = SimpleNamespace(request=request_b)
        celery_signals._restore_request_id_on_prerun(task_id="b", task=task_b)
        self.assertIsNone(get_request_id())
        celery_signals._clear_request_id_on_postrun(task_id="b")


class EndToEndBrokerRoundTripTests(TestCase):
    """Real (non-eager) dispatch through this project's actual signal
    handlers, using a throwaway Celery app + in-memory broker so this
    doesn't touch any real task or require Redis.

    A plain TestCase (real, migrated DB), not SimpleTestCase: starting a
    Celery worker - even an in-memory, throwaway one - triggers Celery's
    Django fixup, which runs the *full* Django system-check registry
    (celery/fixups/django.py -> validate_models() -> run_checks()). That
    includes this project's own billing.checks.check_plan_feature_catalogue_seeded,
    which reads the PlanFeature table. SimpleTestCase forbids DB access and
    would fail on that unrelated check, not on anything this test is
    actually verifying.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = Celery(
            "request_id_e2e_test", broker="memory://", backend="cache+memory://"
        )
        cls.app.conf.task_always_eager = False

        captured = cls.captured = {}

        @cls.app.task(bind=True, name="request_id_e2e_test.echo_request_id")
        def echo_request_id(self):
            captured["seen_in_task_body"] = get_request_id()
            return get_request_id()

        cls.echo_request_id = echo_request_id

    def test_request_id_propagates_from_dispatcher_to_worker(self):
        token = set_request_id("e2e-test-id")
        try:
            with start_worker(self.app, perform_ping_check=False):
                result = self.echo_request_id.delay()
                value = result.get(timeout=10)

            # Dispatching context's own contextvar must be untouched by the
            # worker thread's set/reset (they run in different threads,
            # but this pins down that no unexpected cross-thread leakage
            # occurred) - checked before reset_request_id() below.
            self.assertEqual(get_request_id(), "e2e-test-id")
        finally:
            reset_request_id(token)

        self.assertEqual(value, "e2e-test-id")
        self.assertEqual(self.captured["seen_in_task_body"], "e2e-test-id")

    def test_no_request_id_set_means_none_propagates(self):
        self.assertIsNone(get_request_id())
        with start_worker(self.app, perform_ping_check=False):
            result = self.echo_request_id.delay()
            value = result.get(timeout=10)

        self.assertIsNone(value)
        self.assertIsNone(self.captured["seen_in_task_body"])
