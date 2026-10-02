"""
billing/tests/test_h28_cancel_phases.py
=======================================
H-28 Change 1, commit 3: cancel_license_subscription on the four-phase
plumbing (billing/license_stripe_mutation.py), tested at each injection
point of DESIGN_PROPOSAL.md §5:

  1. before the Stripe call: Stripe refuses -> nothing changes anywhere,
     and the intent says FAILED;
  2. during the call: the response is lost -> Stripe is READ BACK, and the
     intent follows what Stripe really did (or stays PENDING, loudly, when
     even the read fails);
  3. after Stripe applied, before the local commit: the cancellation is
     undone at Stripe (no money moved) -> COMPENSATED; if the undo fails
     too -> ESCALATED, loudly;
  4. on retry: a repeat request makes no second Stripe call, and a request
     while another change is in flight is refused with a 400-shaped error.

Plus the properties the phases rest on: no Stripe call runs inside a
transaction, every call carries its intent's idempotency key, and the
operation refuses to run inside a caller's transaction (durable=True).

The reproductions for this flow are in test_h28_licence_stripe_divergence
(the two cancel tests); this file tests the machinery's own states. Every
Stripe response is a real StripeObject from the stateful fake there, never
a mock (rule 14).
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.db import OperationalError, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from audit.context import request_audit_state
from audit.enums import ActorRole, AuditAction
from audit.models import AuditEvent
from billing import license_stripe_mutation
from billing.imports import stripe
from billing.license_service import LicenseSubscriptionService
from billing.license_stripe_mutation import LicenceStripe
from billing.models import (
    LicenseBillingMethod,
    LicenseBillingRecord,
    LicenseBillingRecordType,
    LicenseStripeMutationIntent,
    LicenseStripeMutationOperation,
    LicenseStripeMutationStatus,
    LicenseSubscription,
    PlanTier,
    PlanType,
)
from billing.tests.test_h28_licence_stripe_divergence import (
    CONTRACT_MONTHS,
    _FakeLicenceStripe,
    _make_plan,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

MUTATION_LOGGER = "billing.license_stripe_mutation"

# -- Epic A: the audit events of intent status changes -----------------------
# Shared with the other phase modules and test_h28_intent_audit, which import
# them from here as they do LicencePhaseTestCase.
IN_FLIGHT_BEFORE_ESCALATION = (
    str(LicenseStripeMutationStatus.PENDING),
    str(LicenseStripeMutationStatus.STRIPE_APPLIED),
)


def intent_events(intent=None):
    events = AuditEvent.objects.filter(
        action=AuditAction.SUBSCRIPTION_CHANGE,
        target_type="LicenseStripeMutationIntent",
    )
    return events if intent is None else events.filter(target_id=intent.id)


def as_a_request_by(user):
    """What AuditMiddleware gives the code under a request by `user`: the
    licence views call the service methods inside exactly this state."""
    return request_audit_state(SimpleNamespace(user=user))


def assert_ids_only(test, intent, event):
    """The event carries ids and statuses, never the intent's free text."""
    test.assertEqual(event.target_id, intent.id)
    test.assertEqual(event.school_id, intent.license_subscription.school_id)
    test.assertEqual(set(event.before), {"intent_status"})
    test.assertEqual(set(event.after), {"intent_status"})
    test.assertLessEqual(set(event.metadata), {"license_id", "command"})
    test.assertEqual(event.metadata["license_id"], str(intent.license_subscription_id))
    recorded = f"{event.before} {event.after} {event.metadata}"
    for text in (intent.failure_reason, intent.resolution_note):
        if text:
            test.assertNotIn(text, recorded)


def assert_one_escalation_event(test, intent, actor):
    """Exactly one event for this intent's move to ESCALATED, by `actor`
    (None means SYSTEM)."""
    [event] = list(intent_events(intent))
    if actor is None:
        test.assertIsNone(event.actor_id)
        test.assertEqual(event.actor_role, ActorRole.SYSTEM)
    else:
        test.assertEqual(event.actor_id, actor.id)
        test.assertEqual(event.actor_role, actor.user_type)
    test.assertIn(event.before["intent_status"], IN_FLIGHT_BEFORE_ESCALATION)
    test.assertEqual(
        event.after, {"intent_status": str(LicenseStripeMutationStatus.ESCALATED)}
    )
    test.assertNotIn("command", event.metadata)
    assert_ids_only(test, intent, event)
    return event


class LicencePhaseTestCase(TransactionTestCase):
    """
    The fixture for every flow on the phase plumbing: one STRIPE licence on
    the stateful fake, with each Stripe call recorded along with whether a
    transaction was open. No tests of its own, so importing it into another
    module collects nothing twice.

    TransactionTestCase: the phases' own transactions must really commit,
    and `connection.in_atomic_block` must mean what it says.
    """

    SUB_ID = "sub_h28_phases"
    SEATS = 10

    def setUp(self):
        school = School.objects.create(name="H-28 Cancel School")
        admin = CustomUser.objects.create_user(
            email="admin@h28-cancel.school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        self.superadmin = CustomUser.objects.create_superuser(
            email="root@h28-cancel.gradea.com",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        plan = _make_plan(PlanTier.PRO, PlanType.PRO, "1000.00", "price_h28c_pro")
        self.licence = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            billing_method=LicenseBillingMethod.STRIPE,
            contract_months=CONTRACT_MONTHS,
            max_seats=self.SEATS,
            is_active=True,
            auto_renew=True,
            stripe_subscription_id=self.SUB_ID,
            stripe_customer_id="cus_h28c",
        )
        self.stripe = _FakeLicenceStripe(
            sub_id=self.SUB_ID,
            price_id="price_h28c_initial",
            unit_amount=int(plan.price_cents) * CONTRACT_MONTHS,
            quantity=self.SEATS,
        )
        for p in self.stripe.patches():
            p.start()
            self.addCleanup(p.stop)

        # Every Stripe call, with whether a transaction was open when it was
        # made. Wraps the fake, so its state still changes as Stripe's would.
        self.in_transaction_at_call = []
        for name, fake_method in (
            ("modify_subscription", self.stripe.subscription_modify),
            ("retrieve_subscription", self.stripe.subscription_retrieve),
        ):
            p = patch.object(
                LicenceStripe, name, side_effect=self._recording(fake_method)
            )
            p.start()
            self.addCleanup(p.stop)

    def _recording(self, fake_method):
        def call(*args, **kwargs):
            # The caller's transaction state: under the request budget the
            # call itself runs on a worker thread (call_stripe).
            self.in_transaction_at_call.append(
                license_stripe_mutation.caller_in_atomic_block()
            )
            return fake_method(*args, **kwargs)

        return call

    # -- helpers -------------------------------------------------------------

    def fresh_licence(self):
        return LicenseSubscription.objects.get(pk=self.licence.pk)

    def only_intent(self):
        intents = list(LicenseStripeMutationIntent.objects.all())
        self.assertEqual(len(intents), 1, f"expected one intent, got {intents!r}")
        return intents[0]

    def modify_calls(self):
        return [c for c in self.stripe.calls if c[0] == "Subscription.modify"]

    def assert_no_stripe_call_inside_a_transaction(self):
        self.assertTrue(self.in_transaction_at_call, "no Stripe call was made")
        self.assertNotIn(
            True,
            self.in_transaction_at_call,
            "a Stripe call was made with a database transaction open",
        )


class CancelPhaseTests(LicencePhaseTestCase):
    SUB_ID = "sub_h28_cancel"

    def cancel(self):
        return LicenseSubscriptionService.cancel_license_subscription(
            self.licence, performed_by=self.superadmin, notes="h28 phases"
        )

    def cancellation_records(self):
        return LicenseBillingRecord.objects.filter(
            license_subscription=self.licence,
            record_type=LicenseBillingRecordType.CANCELLED,
        ).count()

    def assert_unchanged_everywhere(self):
        self.assertTrue(self.fresh_licence().auto_renew)
        self.assertFalse(self.stripe.cancel_at_period_end)
        self.assertEqual(self.cancellation_records(), 0)

    # -- the forward path ----------------------------------------------------

    def test_success_completes_the_intent_outside_any_transaction(self):
        updated = self.cancel()

        self.assertFalse(updated.auto_renew)
        self.assertTrue(updated.is_active)
        self.assertTrue(self.stripe.cancel_at_period_end)
        self.assertEqual(self.cancellation_records(), 1)

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.COMPLETE)
        self.assertEqual(intent.operation, LicenseStripeMutationOperation.CANCEL)
        self.assertEqual(intent.stripe_subscription_id, self.SUB_ID)
        self.assertEqual(intent.performed_by, self.superadmin)
        self.assertIsNotNone(intent.completed_at)

        self.assert_no_stripe_call_inside_a_transaction()

    def test_the_stripe_call_carries_the_intents_apply_key(self):
        self.cancel()
        intent = self.only_intent()

        [(_, sub_id, kwargs)] = self.modify_calls()
        self.assertEqual(sub_id, self.SUB_ID)
        self.assertEqual(kwargs["cancel_at_period_end"], True)
        self.assertEqual(kwargs["idempotency_key"], intent.idempotency_key("apply"))

    # -- 1. before the call: Stripe refuses ----------------------------------

    def test_a_refusal_changes_nothing_and_marks_the_intent_failed(self):
        def refuse(*args, **kwargs):
            raise stripe.error.InvalidRequestError(
                "No such subscription", "id", http_status=404
            )

        with patch.object(LicenceStripe, "modify_subscription", side_effect=refuse):
            with self.assertRaisesRegex(
                ValueError, "Failed to schedule Stripe cancellation"
            ):
                self.cancel()

        self.assert_unchanged_everywhere()
        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.FAILED)
        self.assertIn("Stripe refused", intent.failure_reason)
        # A refusal is definitive: nothing is read back.
        self.assertEqual(
            [c for c in self.stripe.calls if c[0] == "Subscription.retrieve"], []
        )

    # -- 2. during the call: the response is lost ----------------------------

    def test_a_lost_response_that_landed_is_read_back_and_completed(self):
        self.stripe.lost_response_on = {"modify"}

        updated = self.cancel()

        self.assertFalse(updated.auto_renew)
        self.assertTrue(self.stripe.cancel_at_period_end)
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPLETE
        )
        self.assertIn("Subscription.retrieve", [c[0] for c in self.stripe.calls])
        self.assert_no_stripe_call_inside_a_transaction()

    def test_a_lost_response_that_did_not_land_is_read_back_and_failed(self):
        def time_out_without_applying(*args, **kwargs):
            raise stripe.error.APIConnectionError("Request timed out")

        with patch.object(
            LicenceStripe, "modify_subscription", side_effect=time_out_without_applying
        ):
            # H-60: the client sees fixed text; Stripe's is on the intent.
            with self.assertRaisesRegex(
                ValueError, "Failed to schedule Stripe cancellation"
            ):
                self.cancel()

        self.assert_unchanged_everywhere()
        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.FAILED)
        self.assertIn("read-back shows not applied", intent.failure_reason)
        self.assertIn("Request timed out", intent.failure_reason)

    def test_a_server_error_is_treated_as_an_unknown_outcome(self):
        def fail_after_applying(*args, **kwargs):
            self.stripe.subscription_modify(*args, **kwargs)
            raise stripe.error.APIError("Internal error", http_status=500)

        with patch.object(
            LicenceStripe, "modify_subscription", side_effect=fail_after_applying
        ):
            updated = self.cancel()

        self.assertFalse(updated.auto_renew)
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPLETE
        )

    def test_an_unreadable_outcome_leaves_the_intent_pending_and_says_so(self):
        self.stripe.lost_response_on = {"modify"}

        def unreachable(*args, **kwargs):
            raise stripe.error.APIConnectionError("Stripe unreachable")

        with patch.object(
            LicenceStripe, "retrieve_subscription", side_effect=unreachable
        ):
            with self.assertLogs(MUTATION_LOGGER, level="ERROR") as logs:
                with self.assertRaises(ValueError):
                    self.cancel()

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.PENDING)
        self.assertIn("MANUAL RECONCILIATION NEEDED", "\n".join(logs.output))
        self.assertIn(str(intent.id), "\n".join(logs.output))
        # Nothing local was written for a change whose outcome is unknown,
        # and the PENDING intent keeps the licence's guard closed.
        self.assertTrue(self.fresh_licence().auto_renew)
        self.assertEqual(self.cancellation_records(), 0)
        with self.assertRaises(license_stripe_mutation.LicenceBillingChangeInProgress):
            self.cancel()

    # -- 3. after Stripe applied, before the local commit --------------------

    def _fail_the_local_write(self):
        return patch.object(
            LicenseBillingRecord.objects,
            "create",
            side_effect=OperationalError("server closed the connection"),
        )

    def test_a_failed_local_write_is_undone_at_stripe_and_compensated(self):
        with self._fail_the_local_write():
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.cancel()

        self.assert_unchanged_everywhere()
        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.COMPENSATED)
        self.assertIn("OperationalError", intent.failure_reason)

        apply_call, revert_call = self.modify_calls()
        self.assertEqual(apply_call[2]["cancel_at_period_end"], True)
        self.assertEqual(revert_call[2]["cancel_at_period_end"], False)
        self.assertEqual(
            revert_call[2]["idempotency_key"], intent.idempotency_key("revert")
        )
        self.assertNotEqual(
            apply_call[2]["idempotency_key"], revert_call[2]["idempotency_key"]
        )
        self.assert_no_stripe_call_inside_a_transaction()

    def test_a_compensated_intent_frees_the_licence_for_a_retry(self):
        with self._fail_the_local_write():
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.cancel()

        updated = self.cancel()

        self.assertFalse(updated.auto_renew)
        self.assertTrue(self.stripe.cancel_at_period_end)
        self.assertEqual(
            sorted(
                LicenseStripeMutationIntent.objects.values_list("status", flat=True)
            ),
            sorted(
                [
                    LicenseStripeMutationStatus.COMPENSATED,
                    LicenseStripeMutationStatus.COMPLETE,
                ]
            ),
        )

    def test_an_escalated_cancel_is_audited_as_the_signed_in_user(self):
        """Epic A: the move to ESCALATED writes one audit event, by the user
        whose request it was, and a COMPENSATED cancel writes none."""

        def apply_but_refuse_the_undo(*args, **kwargs):
            if kwargs.get("cancel_at_period_end") is False:
                raise stripe.error.APIConnectionError("Request timed out")
            return self.stripe.subscription_modify(*args, **kwargs)

        with as_a_request_by(self.superadmin), self._fail_the_local_write():
            with patch.object(
                LicenceStripe,
                "modify_subscription",
                side_effect=apply_but_refuse_the_undo,
            ), self.assertLogs(MUTATION_LOGGER, level="ERROR"):
                with self.assertRaises(
                    license_stripe_mutation.LicenceStripeChangeNotRecorded
                ):
                    self.cancel()

        assert_one_escalation_event(self, self.only_intent(), self.superadmin)

    def test_a_compensated_cancel_writes_no_intent_event(self):
        with as_a_request_by(self.superadmin), self._fail_the_local_write():
            with self.assertLogs(MUTATION_LOGGER, level="WARNING"):
                with self.assertRaises(
                    license_stripe_mutation.LicenceStripeChangeNotRecorded
                ):
                    self.cancel()

        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPENSATED
        )
        self.assertEqual(intent_events().count(), 0)

    def test_a_failed_undo_escalates_to_a_human(self):
        def apply_but_refuse_the_undo(*args, **kwargs):
            if kwargs.get("cancel_at_period_end") is False:
                raise stripe.error.APIConnectionError("Request timed out")
            return self.stripe.subscription_modify(*args, **kwargs)

        with self._fail_the_local_write(), patch.object(
            LicenceStripe, "modify_subscription", side_effect=apply_but_refuse_the_undo
        ):
            with self.assertLogs(MUTATION_LOGGER, level="ERROR") as logs:
                with self.assertRaises(
                    license_stripe_mutation.LicenceStripeChangeNotRecorded
                ):
                    self.cancel()

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.ESCALATED)
        self.assertIsNotNone(intent.escalated_at)
        self.assertIn("the revert at Stripe also failed", intent.failure_reason)
        self.assertIn("MANUAL RECONCILIATION NEEDED", "\n".join(logs.output))
        # The disagreement is real and recorded: Stripe cancels, the app
        # still renews, and the escalated intent blocks further changes.
        self.assertTrue(self.stripe.cancel_at_period_end)
        self.assertTrue(self.fresh_licence().auto_renew)
        with self.assertRaises(license_stripe_mutation.LicenceBillingChangeInProgress):
            self.cancel()

    def test_a_licence_changed_meanwhile_is_not_overwritten(self):
        def apply_then_someone_else_deactivates(*args, **kwargs):
            result = self.stripe.subscription_modify(*args, **kwargs)
            if kwargs.get("cancel_at_period_end") is True:
                LicenseSubscription.objects.filter(pk=self.licence.pk).update(
                    stripe_subscription_id="sub_replaced"
                )
            return result

        with patch.object(
            LicenceStripe,
            "modify_subscription",
            side_effect=apply_then_someone_else_deactivates,
        ):
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.cancel()

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.COMPENSATED)
        self.assertIn("LicenceMovedOn", intent.failure_reason)
        self.assertFalse(self.stripe.cancel_at_period_end)
        self.assertEqual(self.cancellation_records(), 0)

    # -- 4. on retry ---------------------------------------------------------

    def test_a_repeat_request_makes_no_second_stripe_call(self):
        self.cancel()

        with self.assertRaisesRegex(ValueError, "already scheduled to cancel"):
            self.cancel()

        self.assertEqual(len(self.modify_calls()), 1)
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 1)

    def test_a_request_while_another_change_is_in_flight_is_refused(self):
        LicenseStripeMutationIntent.objects.create(
            license_subscription=self.licence,
            operation=LicenseStripeMutationOperation.UPDATE_SEATS,
            stripe_subscription_id=self.SUB_ID,
            requested_change={"old_max_seats": 10, "new_max_seats": 12},
        )

        with self.assertRaisesRegex(
            ValueError, "Another billing change for this licence is still in progress"
        ) as caught:
            self.cancel()

        self.assertIsInstance(
            caught.exception, license_stripe_mutation.LicenceBillingChangeInProgress
        )
        self.assertEqual(self.modify_calls(), [])
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 1)
        self.assertTrue(self.fresh_licence().auto_renew)

    # -- durable=True --------------------------------------------------------

    def test_refuses_to_run_inside_a_callers_transaction(self):
        """Wrapping the operation in a transaction would put the Stripe call
        straight back inside one; durable=True makes that fail loudly."""
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.cancel()

        self.assertEqual(self.modify_calls(), [])
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 0)
        self.assertTrue(self.fresh_licence().auto_renew)

    # -- the paths that never reach Stripe are unchanged ---------------------

    def test_an_offline_licence_is_deactivated_at_once_without_an_intent(self):
        LicenseSubscription.objects.filter(pk=self.licence.pk).update(
            billing_method=LicenseBillingMethod.OFFLINE
        )

        updated = self.cancel()

        self.assertFalse(updated.is_active)
        self.assertFalse(updated.auto_renew)
        self.assertEqual(self.cancellation_records(), 1)
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 0)
        self.assertEqual(self.stripe.calls, [])

    def test_a_stripe_licence_without_a_subscription_cancels_locally(self):
        LicenseSubscription.objects.filter(pk=self.licence.pk).update(
            stripe_subscription_id=None
        )

        updated = self.cancel()

        self.assertTrue(updated.is_active)
        self.assertFalse(updated.auto_renew)
        self.assertEqual(self.cancellation_records(), 1)
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 0)
        self.assertEqual(self.stripe.calls, [])
