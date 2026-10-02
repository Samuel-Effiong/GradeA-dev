"""
billing/tests/test_stripe_live_qa.py
====================================
Tests for the real-Stripe QA harness ITSELF — all mocked, no network.

There is an obvious irony in mock-testing the suite whose purpose is to
escape mocks, so be precise about what is being locked down here. The
scenarios' VALUE comes from running against real Stripe and cannot be
asserted offline. What CAN and MUST be asserted offline is everything
that protects real money and real data:

  * the live-key refusal truth table, in both directions of disagreement
  * that no code path reaches Stripe without re-checking test mode
  * that cleanup removes the StripeEvent ledger rows a run created,
    because a leftover FAILED QA row makes sweep_stale_stripe_events
    page about a customer who never existed
  * that cleanup never raises, so it cannot mask a scenario's result
  * that one scenario failing neither stops the others nor skips teardown

Those are the properties that would cause damage if wrong, and they are
exactly the properties that do not need Stripe to verify.
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import StripeEvent, StripeEventStatus
from billing.stripe_live_qa import (
    Check,
    CheckRecorder,
    LiveQAConfigurationError,
    LiveQAHarness,
    LiveQARefused,
    ScenarioResult,
    SuiteResult,
    _event_customer_id,
    _is_test_key,
    assert_live_qa_enabled,
    assert_test_mode,
    guarded_call,
    live_qa_enabled,
    qa_email_domain,
    require_plan,
)
from billing.tasks import nightly_stripe_live_qa
from users.models import CustomUser, UserTypes

TEST_KEY = "sk_test_fake"  # pragma: allowlist secret
LIVE_KEY = "sk_live_fake"  # pragma: allowlist secret


def enabled(**extra):
    """Settings under which the suite is permitted to run."""
    options = {"ENABLE_STRIPE_LIVE_QA": True, "STRIPE_SECRET_KEY": TEST_KEY}
    options.update(extra)
    return override_settings(**options)


class KeyClassificationTests(TestCase):
    """_is_test_key must answer 'no' to everything it does not recognise.
    The caller refuses on False, so an unrecognised key must never be
    optimistically treated as safe."""

    def test_only_sk_test_prefix_is_a_test_key(self):
        self.assertTrue(_is_test_key("sk_test_abc"))

    def test_live_key_is_not_a_test_key(self):
        self.assertFalse(_is_test_key("sk_live_abc"))

    def test_empty_and_none_are_not_test_keys(self):
        self.assertFalse(_is_test_key(""))
        self.assertFalse(_is_test_key(None))

    def test_restricted_and_publishable_keys_are_not_test_keys(self):
        for key in ("rk_test_abc", "pk_test_abc", "whsec_abc", "sk_TEST_abc"):
            with self.subTest(key=key):
                self.assertFalse(_is_test_key(key))


class AssertTestModeTests(TestCase):
    """The full truth table. A DISAGREEMENT between the runtime key and
    the configured key must refuse in BOTH directions — if they diverge,
    something is wrong and 'pick the convenient one' is not an option."""

    def _probe(self, runtime, configured):
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = runtime
            with override_settings(STRIPE_SECRET_KEY=configured):
                assert_test_mode("probe")

    def test_both_test_keys_allowed(self):
        self._probe(TEST_KEY, TEST_KEY)  # must not raise

    def test_both_live_keys_refused(self):
        with self.assertRaises(LiveQARefused):
            self._probe(LIVE_KEY, LIVE_KEY)

    def test_runtime_live_configured_test_refused(self):
        with self.assertRaises(LiveQARefused):
            self._probe(LIVE_KEY, TEST_KEY)

    def test_runtime_test_configured_live_refused(self):
        """The subtle one: stripe.api_key looks safe but settings do not."""
        with self.assertRaises(LiveQARefused):
            self._probe(TEST_KEY, LIVE_KEY)

    def test_missing_keys_refused(self):
        with self.assertRaises(LiveQARefused):
            self._probe("", "")

    def test_error_message_never_contains_the_key(self):
        """Exception text reaches logs and tickets."""
        with self.assertRaises(LiveQARefused) as ctx:
            self._probe(LIVE_KEY, LIVE_KEY)
        self.assertNotIn(LIVE_KEY, str(ctx.exception))
        self.assertIn("sk_test_", str(ctx.exception))


class EnablementTests(TestCase):
    @override_settings(ENABLE_STRIPE_LIVE_QA=False, STRIPE_SECRET_KEY=TEST_KEY)
    def test_disabled_by_toggle_even_with_test_keys(self):
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = TEST_KEY
            self.assertFalse(live_qa_enabled())
            with self.assertRaises(LiveQARefused):
                assert_live_qa_enabled()

    @override_settings(ENABLE_STRIPE_LIVE_QA=True, STRIPE_SECRET_KEY=LIVE_KEY)
    def test_toggle_alone_cannot_enable_against_live_keys(self):
        """The toggle is necessary but never sufficient."""
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = LIVE_KEY
            self.assertFalse(live_qa_enabled())
            with self.assertRaises(LiveQARefused):
                assert_live_qa_enabled()

    @enabled()
    def test_enabled_with_toggle_and_test_keys(self):
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = TEST_KEY
            self.assertTrue(live_qa_enabled())
            assert_live_qa_enabled()

    @override_settings(STRIPE_LIVE_QA_EMAIL_DOMAIN="")
    def test_email_domain_falls_back_to_non_routable_default(self):
        self.assertTrue(qa_email_domain().endswith(".invalid"))

    @override_settings(STRIPE_LIVE_QA_EMAIL_DOMAIN="@Example.COM ")
    def test_email_domain_is_normalised(self):
        self.assertEqual(qa_email_domain(), "example.com")


class GuardedCallTests(TestCase):
    """guarded_call is the single chokepoint to Stripe. If it can be
    bypassed or fails open, every other guarantee here is void."""

    def test_refuses_before_invoking_the_callable(self):
        """The call must not happen at all — not merely be undone."""
        target = MagicMock(return_value="should not happen")
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = LIVE_KEY
            with override_settings(STRIPE_SECRET_KEY=LIVE_KEY):
                with self.assertRaises(LiveQARefused):
                    guarded_call(target, "arg")
        target.assert_not_called()

    def test_passes_through_arguments_and_result_in_test_mode(self):
        target = MagicMock(return_value="ok")
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = TEST_KEY
            with override_settings(STRIPE_SECRET_KEY=TEST_KEY):
                result = guarded_call(target, "a", b=2)
        self.assertEqual(result, "ok")
        target.assert_called_once_with("a", b=2)

    def test_rechecks_on_every_call_so_a_mid_run_key_swap_is_caught(self):
        """This is why the check is per-call rather than once at import:
        a key swapped after the run started must stop the NEXT call."""
        target = MagicMock(return_value="ok")
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            with override_settings(STRIPE_SECRET_KEY=TEST_KEY):
                mock_stripe.api_key = TEST_KEY
                guarded_call(target)

                mock_stripe.api_key = LIVE_KEY
                with self.assertRaises(LiveQARefused):
                    guarded_call(target)

        self.assertEqual(target.call_count, 1)


class EventCustomerExtractionTests(TestCase):
    def test_reads_a_plain_customer_id(self):
        event = {"data": {"object": {"customer": "cus_1"}}}
        self.assertEqual(_event_customer_id(event), "cus_1")

    def test_reads_an_expanded_customer_object(self):
        event = {"data": {"object": {"customer": {"id": "cus_2"}}}}
        self.assertEqual(_event_customer_id(event), "cus_2")

    def test_returns_none_for_events_without_a_customer(self):
        self.assertIsNone(_event_customer_id({"data": {"object": {}}}))

    def test_returns_none_for_malformed_events_instead_of_raising(self):
        """A malformed event must be skipped, not crash the drain loop."""
        for event in ({}, {"data": {}}, {"data": None}, {"data": {"object": None}}):
            with self.subTest(event=event):
                self.assertIsNone(_event_customer_id(event))


def _make_event(event_id, customer, created, event_type="invoice.payment_succeeded"):
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "data": {"object": {"customer": customer}},
    }


@enabled()
class HarnessFetchEventsTests(TestCase):
    def setUp(self):
        patcher = patch("billing.stripe_live_qa.stripe")
        self.mock_stripe = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_stripe.api_key = TEST_KEY
        self.harness = LiveQAHarness(run_id="testrun")

    def _set_events(self, events, has_more=False):
        self.mock_stripe.Event.list.return_value = {
            "data": events,
            "has_more": has_more,
        }

    def test_filters_to_the_requested_customer(self):
        self._set_events(
            [
                _make_event("evt_mine", "cus_me", 100),
                _make_event("evt_theirs", "cus_other", 101),
            ]
        )
        found = self.harness._fetch_new_events("cus_me")
        self.assertEqual([e["id"] for e in found], ["evt_mine"])

    def test_returns_events_oldest_first(self):
        """Handlers assume ordering: subscription.created must be seen
        before the invoice that follows it. Stripe returns newest first."""
        self._set_events(
            [
                _make_event("evt_c", "cus_me", 300),
                _make_event("evt_a", "cus_me", 100),
                _make_event("evt_b", "cus_me", 200),
            ]
        )
        found = self.harness._fetch_new_events("cus_me")
        self.assertEqual([e["id"] for e in found], ["evt_a", "evt_b", "evt_c"])

    def test_same_second_events_are_ordered_deterministically(self):
        self._set_events(
            [
                _make_event("evt_z", "cus_me", 100),
                _make_event("evt_a", "cus_me", 100),
            ]
        )
        found = self.harness._fetch_new_events("cus_me")
        self.assertEqual([e["id"] for e in found], ["evt_a", "evt_z"])

    def test_already_dispatched_events_are_not_returned_again(self):
        self._set_events([_make_event("evt_1", "cus_me", 100)])
        self.harness.dispatched_event_ids.add("evt_1")
        self.assertEqual(self.harness._fetch_new_events("cus_me"), [])

    def test_event_window_starts_before_now_to_absorb_clock_skew(self):
        """A locally-fast clock would otherwise filter out our own events,
        and the drain would silently observe nothing."""
        import time as _time

        self._set_events([])
        self.harness._fetch_new_events("cus_me")
        params = self.mock_stripe.Event.list.call_args.kwargs
        self.assertLessEqual(
            params["created"]["gte"],
            int(_time.time()) - 30,
            "the event window must start meaningfully before now",
        )


@enabled()
class HarnessDrainTests(TestCase):
    def setUp(self):
        patcher = patch("billing.stripe_live_qa.stripe")
        self.mock_stripe = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_stripe.api_key = TEST_KEY
        self.harness = LiveQAHarness(run_id="drainrun")
        self.harness.EVENT_POLL_INTERVAL = 0
        self.harness.EVENT_QUIET_POLLS = 1

    def test_dispatches_through_the_real_webhook_path(self):
        """Not by calling handlers directly — going through
        _record_and_dispatch is what exercises the C3 ledger."""
        self.mock_stripe.Event.list.return_value = {
            "data": [_make_event("evt_1", "cus_me", 100)],
            "has_more": False,
        }
        response = MagicMock(status_code=200)
        with patch(
            "billing.webhooks._record_and_dispatch", return_value=response
        ) as dispatch:
            dispatched = self.harness.drain_events(customer_id="cus_me")

        dispatch.assert_called_once()
        self.assertEqual(dispatched, [("invoice.payment_succeeded", 200)])

    def test_event_is_marked_dispatched_before_dispatch_so_a_raise_cannot_loop(self):
        self.mock_stripe.Event.list.return_value = {
            "data": [_make_event("evt_boom", "cus_me", 100)],
            "has_more": False,
        }
        with patch(
            "billing.webhooks._record_and_dispatch", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                self.harness.drain_events(customer_id="cus_me")

        self.assertIn("evt_boom", self.harness.dispatched_event_ids)


@enabled()
class HarnessCleanupTests(TestCase):
    def setUp(self):
        patcher = patch("billing.stripe_live_qa.stripe")
        self.mock_stripe = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_stripe.api_key = TEST_KEY
        self.mock_stripe.error.InvalidRequestError = Exception
        self.harness = LiveQAHarness(run_id="cleanrun")

    def test_deletes_the_ledger_rows_the_run_created(self):
        """A leftover FAILED QA row would make sweep_stale_stripe_events
        log 'a customer may have paid without receiving anything' three
        days later — a page about a customer who never existed."""
        StripeEvent.objects.create(
            stripe_event_id="evt_qa",
            event_type="invoice.payment_succeeded",
            status=StripeEventStatus.FAILED,
        )
        StripeEvent.objects.create(
            stripe_event_id="evt_real",
            event_type="invoice.payment_succeeded",
            status=StripeEventStatus.SUCCEEDED,
        )
        self.harness.dispatched_event_ids.add("evt_qa")

        errors = self.harness.cleanup()

        self.assertEqual(errors, [])
        self.assertFalse(StripeEvent.objects.filter(stripe_event_id="evt_qa").exists())
        self.assertTrue(
            StripeEvent.objects.filter(stripe_event_id="evt_real").exists(),
            "cleanup must only ever touch rows this run created",
        )

    def test_deletes_local_users_it_created(self):
        user = CustomUser.objects.create_user(
            email="liveqa-cleanrun-x@stripe-live-qa.invalid",
            password="x",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.harness.local_user_ids.append(user.id)

        self.harness.cleanup()

        self.assertFalse(CustomUser.objects.filter(id=user.id).exists())

    def test_deletes_test_clocks(self):
        self.harness.clock_ids.append("clock_1")
        self.harness.cleanup()
        self.mock_stripe.test_helpers.TestClock.delete.assert_called_once_with(
            "clock_1"
        )

    def test_returns_errors_instead_of_raising(self):
        """Teardown must never mask the scenario result that preceded it."""
        self.harness.clock_ids.append("clock_boom")
        self.mock_stripe.test_helpers.TestClock.delete.side_effect = RuntimeError(
            "stripe down"
        )

        errors = self.harness.cleanup()

        self.assertEqual(len(errors), 1)
        self.assertIn("clock_boom", errors[0])

    def test_one_failure_does_not_abandon_the_rest_of_cleanup(self):
        self.harness.clock_ids.extend(["clock_boom", "clock_ok"])
        self.mock_stripe.test_helpers.TestClock.delete.side_effect = [
            RuntimeError("stripe down"),
            None,
        ]

        errors = self.harness.cleanup()

        self.assertEqual(len(errors), 1)
        self.assertEqual(self.mock_stripe.test_helpers.TestClock.delete.call_count, 2)


class HarnessConstructionTests(TestCase):
    @override_settings(ENABLE_STRIPE_LIVE_QA=False)
    def test_cannot_be_constructed_while_disabled(self):
        with self.assertRaises(LiveQARefused):
            LiveQAHarness()

    @enabled()
    def test_cannot_be_constructed_against_live_keys(self):
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = LIVE_KEY
            with override_settings(STRIPE_SECRET_KEY=LIVE_KEY):
                with self.assertRaises(LiveQARefused):
                    LiveQAHarness()


class RequirePlanTests(TestCase):
    def test_raises_an_actionable_error_when_no_wired_plan_exists(self):
        with self.assertRaises(LiveQAConfigurationError) as ctx:
            require_plan(tier="STANDARD")
        self.assertIn("stripe_price_id", str(ctx.exception))


class CheckRecorderTests(TestCase):
    def test_records_every_failure_rather_than_stopping_at_the_first(self):
        """A nightly job that hides the second bug costs a day per bug."""
        rec = CheckRecorder()
        rec.expect("a", False, "first")
        rec.expect("b", False, "second")
        rec.expect("c", True)

        self.assertEqual(len(rec.checks), 3)
        self.assertEqual(len([c for c in rec.checks if not c.passed]), 2)
        self.assertFalse(rec.passed)

    def test_expect_returns_the_condition_so_scenarios_can_bail_out(self):
        rec = CheckRecorder()
        self.assertTrue(rec.expect("ok", True))
        self.assertFalse(rec.expect("no", False))

    def test_expect_close_tolerates_sub_tolerance_drift(self):
        """Stripe timestamps are whole seconds and our write lands a
        moment later, so exact equality would be flaky for reasons that
        are not bugs."""
        now = timezone.now()
        rec = CheckRecorder()
        rec.expect_close("close", now, now + timedelta(seconds=30))
        self.assertTrue(rec.passed)

    def test_expect_close_fails_beyond_tolerance(self):
        now = timezone.now()
        rec = CheckRecorder()
        rec.expect_close("far", now, now + timedelta(hours=2), tolerance_seconds=60)
        self.assertFalse(rec.passed)

    def test_expect_close_fails_on_none_rather_than_raising(self):
        rec = CheckRecorder()
        rec.expect_close("missing", None, None)
        self.assertFalse(rec.passed)


@enabled()
class RunSuiteTests(TestCase):
    def setUp(self):
        patcher = patch("billing.stripe_live_qa.stripe")
        self.mock_stripe = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_stripe.api_key = TEST_KEY

    def _run(self, scenarios, **kwargs):
        from billing import stripe_live_qa_scenarios as mod

        with patch.dict(mod.SCENARIOS, scenarios, clear=True):
            return mod.run_suite(**kwargs)

    def test_rejects_unknown_scenario_names(self):
        from billing import stripe_live_qa_scenarios as mod

        with self.assertRaises(LiveQAConfigurationError) as ctx:
            mod.run_suite(["not_a_scenario"])
        self.assertIn("not_a_scenario", str(ctx.exception))

    def test_a_raising_scenario_does_not_stop_the_others(self):
        calls = []

        def boom(harness):
            calls.append("boom")
            raise RuntimeError("scenario exploded")

        def fine(harness):
            calls.append("fine")
            rec = CheckRecorder()
            rec.expect("ok", True)
            return rec

        result = self._run({"boom": boom, "fine": fine})

        self.assertEqual(calls, ["boom", "fine"])
        self.assertFalse(result.passed)
        by_name = {s.name: s for s in result.scenarios}
        self.assertFalse(by_name["boom"].passed)
        self.assertIn("scenario exploded", by_name["boom"].error)
        self.assertTrue(by_name["fine"].passed)

    def test_failed_checks_mark_the_scenario_failed(self):
        def failing(harness):
            rec = CheckRecorder()
            rec.expect("bad", False, "observed nonsense")
            return rec

        result = self._run({"failing": failing})

        self.assertFalse(result.passed)
        self.assertEqual(len(result.failed_scenarios), 1)

    def test_cleanup_runs_even_when_a_scenario_raises(self):
        def boom(harness):
            harness.clock_ids.append("clock_leak")
            raise RuntimeError("boom")

        self._run({"boom": boom})

        self.mock_stripe.test_helpers.TestClock.delete.assert_called_once_with(
            "clock_leak"
        )

    def test_keep_objects_skips_cleanup(self):
        def scenario(harness):
            harness.clock_ids.append("clock_keep")
            return CheckRecorder()

        self._run({"s": scenario}, keep_objects=True)

        self.mock_stripe.test_helpers.TestClock.delete.assert_not_called()

    def test_summary_reports_counts(self):
        def fine(harness):
            return CheckRecorder()

        result = self._run({"a": fine, "b": fine})
        self.assertIn("2/2 scenarios passed", result.summary())


class SuiteResultTests(TestCase):
    def test_cleanup_errors_alone_fail_the_run(self):
        """Leaked objects and leaked ledger rows both need a human."""
        result = SuiteResult(run_id="r", scenarios=[], cleanup_errors=["leak"])
        self.assertFalse(result.passed)

    def test_empty_run_passes(self):
        self.assertTrue(SuiteResult(run_id="r").passed)


class ManagementCommandTests(TestCase):
    """The command is what cron actually invokes, so its exit contract is
    part of the safety story: a run that fails silently with exit 0 is a
    monitoring hole, not a passing test."""

    def _call(self, *args):
        out = StringIO()
        call_command("run_stripe_live_qa", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_list_does_not_require_enablement_or_touch_stripe(self):
        output = self._call("--list")
        for name in ("renewals", "failed_renewal", "deferred_downgrade"):
            self.assertIn(name, output)

    @override_settings(ENABLE_STRIPE_LIVE_QA=False)
    def test_refusal_is_a_command_error_not_a_traceback(self):
        with self.assertRaises(CommandError) as ctx:
            self._call()
        self.assertIn("ENABLE_STRIPE_LIVE_QA", str(ctx.exception))

    @enabled()
    def test_failing_run_raises_command_error_so_cron_sees_a_non_zero_exit(self):
        failed = ScenarioResult(
            name="renewals", passed=False, checks=[Check("x", False, "nope")]
        )
        result = SuiteResult(run_id="r", scenarios=[failed])
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = TEST_KEY
            with patch(
                "billing.management.commands.run_stripe_live_qa.run_suite",
                return_value=result,
            ):
                with self.assertRaises(CommandError):
                    self._call()

    @enabled()
    def test_passing_run_exits_cleanly(self):
        result = SuiteResult(
            run_id="r", scenarios=[ScenarioResult(name="renewals", passed=True)]
        )
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = TEST_KEY
            with patch(
                "billing.management.commands.run_stripe_live_qa.run_suite",
                return_value=result,
            ):
                output = self._call()
        self.assertIn("PASS renewals", output)

    @enabled()
    def test_cleanup_errors_alone_fail_the_command(self):
        """Leaked ledger rows make the event sweeper page falsely, so a
        clean scenario run with dirty teardown is still a failure."""
        result = SuiteResult(
            run_id="r",
            scenarios=[ScenarioResult(name="renewals", passed=True)],
            cleanup_errors=["clock leak"],
        )
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = TEST_KEY
            with patch(
                "billing.management.commands.run_stripe_live_qa.run_suite",
                return_value=result,
            ):
                with self.assertRaises(CommandError):
                    self._call()


class NightlyTaskTests(TestCase):
    @override_settings(ENABLE_STRIPE_LIVE_QA=False)
    def test_is_a_no_op_when_disabled(self):
        """On a production worker this task must do nothing at all."""
        with patch("billing.stripe_live_qa_scenarios.run_suite") as run:
            summary = nightly_stripe_live_qa()
        run.assert_not_called()
        self.assertIn("skipped", summary.lower())

    @override_settings(ENABLE_STRIPE_LIVE_QA=True, STRIPE_SECRET_KEY=LIVE_KEY)
    def test_is_a_no_op_against_live_keys(self):
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = LIVE_KEY
            with patch("billing.stripe_live_qa_scenarios.run_suite") as run:
                summary = nightly_stripe_live_qa()
        run.assert_not_called()
        self.assertIn("skipped", summary.lower())

    @enabled()
    def test_logs_an_error_naming_the_scenario_and_repair_command(self):
        from billing.stripe_live_qa import ScenarioResult

        failed = ScenarioResult(name="renewals", passed=False, error="kaboom")
        result = SuiteResult(run_id="r", scenarios=[failed])

        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = TEST_KEY
            with patch(
                "billing.stripe_live_qa_scenarios.run_suite", return_value=result
            ):
                with self.assertLogs("billing.tasks", level="ERROR") as logs:
                    nightly_stripe_live_qa()

        joined = "\n".join(logs.output)
        self.assertIn("renewals", joined)
        self.assertIn("run_stripe_live_qa", joined)

    @enabled()
    def test_configuration_problems_warn_rather_than_page(self):
        """Nobody should be woken because a QA box is not set up."""
        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = TEST_KEY
            with patch(
                "billing.stripe_live_qa_scenarios.run_suite",
                side_effect=LiveQAConfigurationError("no plans seeded"),
            ):
                with self.assertLogs("billing.tasks", level="WARNING") as logs:
                    summary = nightly_stripe_live_qa()

        self.assertIn("no plans seeded", summary)
        self.assertNotIn("ERROR:", "\n".join(logs.output))
