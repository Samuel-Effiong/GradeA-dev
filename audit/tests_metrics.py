"""Tests for BE-A-09 / Epic A §9 alertable metrics: audit/metrics.py itself,
and every call site that feeds it (audit/emitter.py's chokepoint for
grading_failure_rate / model_fallback_rate / reason_code_rate /
audit_emit_failures_total, and the billing call sites for
credit_ledger_anomaly).

Real Sentry calls can't run in tests, so every test here spies on
audit.metrics.count/distribution (patched at the module that imported it,
not at audit.metrics itself - each caller has its own bound reference) and
asserts each signal fires EXACTLY once per triggering event - not >=1, so a
missing call and an accidental double-emit both fail the same way FR-A-01's
tests do elsewhere in this epic.
"""

from datetime import timedelta
from unittest.mock import call, patch

from django.test import TestCase
from django.utils import timezone

from audit import metrics as audit_metrics
from audit.emitter import emit
from audit.enums import AuditAction, AuditOutcome, ErrorClass
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
)
from billing.services import SubscriptionService
from billing.tasks import cleanup_expired_credit_buckets
from users.models import CustomUser, UserTypes


def make_user(email, **overrides):
    defaults = {
        "email": email,
        "password": "testpass123",  # pragma: allowlist secret
        "user_type": UserTypes.TEACHER,
    }
    defaults.update(overrides)
    return CustomUser.objects.create_user(**defaults)


class MetricsModuleNoOpTests(TestCase):
    """audit/metrics.py itself: a safe no-op whenever Sentry isn't live,
    and never raises into the caller even when it is."""

    @patch("audit.metrics.sentry_sdk")
    def test_count_is_a_noop_when_sentry_is_not_live(self, mock_sdk):
        mock_sdk.is_initialized.return_value = False
        audit_metrics.count("some_metric")
        mock_sdk.metrics.count.assert_not_called()

    @patch("audit.metrics.sentry_sdk")
    def test_count_calls_sentry_when_live(self, mock_sdk):
        mock_sdk.is_initialized.return_value = True
        audit_metrics.count("some_metric", 2, tags={"a": "b"})
        mock_sdk.metrics.count.assert_called_once_with(
            "some_metric", 2, attributes={"a": "b"}
        )

    @patch("audit.metrics.sentry_sdk")
    def test_distribution_is_a_noop_when_sentry_is_not_live(self, mock_sdk):
        mock_sdk.is_initialized.return_value = False
        audit_metrics.distribution("some_metric", 1.0)
        mock_sdk.metrics.distribution.assert_not_called()

    @patch("audit.metrics.sentry_sdk")
    def test_distribution_calls_sentry_when_live(self, mock_sdk):
        mock_sdk.is_initialized.return_value = True
        audit_metrics.distribution("some_metric", 1.0)
        mock_sdk.metrics.distribution.assert_called_once_with(
            "some_metric", 1.0, attributes={}
        )

    @patch("audit.metrics.sentry_sdk")
    def test_a_sentry_call_failure_never_raises(self, mock_sdk):
        mock_sdk.is_initialized.return_value = True
        mock_sdk.metrics.count.side_effect = RuntimeError("boom")
        audit_metrics.count("some_metric")  # must not raise

    def test_is_initialized_raising_is_treated_as_not_live(self):
        with patch("audit.metrics.sentry_sdk") as mock_sdk:
            mock_sdk.is_initialized.side_effect = RuntimeError("boom")
            audit_metrics.count("some_metric")
            mock_sdk.metrics.count.assert_not_called()

    def test_sentry_sdk_missing_entirely_is_a_noop(self):
        with patch("audit.metrics.sentry_sdk", None):
            audit_metrics.count("some_metric")  # must not raise


class GradingFailureAndFallbackRateMetricTests(TestCase):
    """audit/emitter.py's emit() chokepoint: BE-A-09 #1 and #2."""

    def setUp(self):
        self.user = make_user("grading-metrics@example.com")

    @patch("audit.emitter.audit_metrics.distribution")
    def test_a_completed_grading_with_the_main_model_emits_rate_zero_and_no_fallback(
        self, mock_dist
    ):
        emit(
            AuditAction.GRADING_COMPLETED,
            actor=self.user,
            target_type="StudentSubmission",
            target_id=self.user.id,
            outcome=AuditOutcome.SUCCESS,
            metadata={"model": "x-ai/grok-4.3"},
        )
        self.assertEqual(
            mock_dist.call_args_list,
            [call("grading_failure_rate", 0.0), call("model_fallback_rate", 0.0)],
        )

    @patch("audit.emitter.audit_metrics.distribution")
    def test_a_completed_grading_on_the_fallback_model_emits_fallback_rate_one(
        self, mock_dist
    ):
        emit(
            AuditAction.GRADING_COMPLETED,
            actor=self.user,
            target_type="StudentSubmission",
            target_id=self.user.id,
            outcome=AuditOutcome.SUCCESS,
            metadata={"model": "deepseek/deepseek-v4-pro"},
        )
        self.assertEqual(
            mock_dist.call_args_list,
            [call("grading_failure_rate", 0.0), call("model_fallback_rate", 1.0)],
        )

    @patch("audit.emitter.audit_metrics.distribution")
    def test_a_completed_grading_with_no_model_recorded_emits_no_fallback_signal(
        self, mock_dist
    ):
        emit(
            AuditAction.GRADING_COMPLETED,
            actor=self.user,
            target_type="StudentSubmission",
            target_id=self.user.id,
            outcome=AuditOutcome.SUCCESS,
        )
        self.assertEqual(mock_dist.call_args_list, [call("grading_failure_rate", 0.0)])

    @patch("audit.emitter.audit_metrics.distribution")
    def test_a_failed_grading_emits_rate_one_and_never_a_fallback_signal(
        self, mock_dist
    ):
        emit(
            AuditAction.GRADING_FAILED,
            actor=self.user,
            target_type="StudentSubmission",
            target_id=self.user.id,
            outcome=AuditOutcome.FAILURE,
            error_class=ErrorClass.SYSTEM,
            # Even if a model name were available on a failure, #2 is
            # scoped to completed runs only - see audit/emitter.py.
            metadata={"model": "deepseek/deepseek-v4-pro"},
        )
        self.assertEqual(mock_dist.call_args_list, [call("grading_failure_rate", 1.0)])

    @patch("audit.emitter.audit_metrics.distribution")
    def test_a_non_grading_action_emits_neither_signal(self, mock_dist):
        emit(
            AuditAction.AUTH_LOGOUT,
            actor=self.user,
            target_type="CustomUser",
            target_id=self.user.id,
            outcome=AuditOutcome.SUCCESS,
        )
        mock_dist.assert_not_called()


class ReasonCodeRateMetricTests(TestCase):
    """audit/emitter.py's emit() chokepoint: BE-A-09 #4."""

    def setUp(self):
        self.user = make_user("reason-code-metrics@example.com")

    @patch("audit.emitter.audit_metrics.count")
    def test_an_event_with_a_reason_code_emits_the_rate_metric_exactly_once(
        self, mock_count
    ):
        emit(
            AuditAction.AUTH_LOGIN,
            actor=None,
            target_type="CustomUser",
            target_id=self.user.id,
            outcome=AuditOutcome.DENIED,
            error_class=ErrorClass.USER,
            reason_code="ACCOUNT_LOCKED",
        )
        mock_count.assert_called_once_with(
            "reason_code_rate", tags={"code": "ACCOUNT_LOCKED"}
        )

    @patch("audit.emitter.audit_metrics.count")
    def test_an_event_with_no_reason_code_emits_nothing(self, mock_count):
        emit(
            AuditAction.AUTH_LOGIN,
            actor=self.user,
            target_type="CustomUser",
            target_id=self.user.id,
            outcome=AuditOutcome.SUCCESS,
        )
        mock_count.assert_not_called()


class AuditEmitFailuresMetricTests(TestCase):
    """audit/emitter.py's own store-failure except block: BE-A-09 #5."""

    def setUp(self):
        self.user = make_user("emit-failure-metrics@example.com")

    @patch("audit.emitter.audit_metrics.count")
    @patch(
        "audit.emitter.AuditEvent.objects.create",
        side_effect=RuntimeError("db down"),
    )
    def test_a_store_failure_emits_the_failure_metric_exactly_once(
        self, mock_create, mock_count
    ):
        result = emit(
            AuditAction.AUTH_LOGOUT,
            actor=self.user,
            target_type="CustomUser",
            target_id=self.user.id,
            outcome=AuditOutcome.SUCCESS,
        )
        self.assertIsNone(result)
        mock_count.assert_called_once_with(
            "audit_emit_failures_total", tags={"action": "AUTH_LOGOUT"}
        )

    @patch("audit.emitter.audit_metrics.count")
    def test_a_successful_store_never_emits_the_failure_metric(self, mock_count):
        emit(
            AuditAction.AUTH_LOGOUT,
            actor=self.user,
            target_type="CustomUser",
            target_id=self.user.id,
            outcome=AuditOutcome.SUCCESS,
        )
        mock_count.assert_not_called()

    @patch("audit.emitter.audit_metrics.count")
    def test_a_rejected_write_never_reaches_the_store_failure_metric(self, mock_count):
        # Rejected by _build (bad target_type) - never reaches
        # AuditEvent.objects.create at all, so this must not look like a
        # store failure.
        result = emit(
            AuditAction.AUTH_LOGOUT,
            actor=self.user,
            target_type="not a model name!!",
            target_id=self.user.id,
            outcome=AuditOutcome.SUCCESS,
        )
        self.assertIsNone(result)
        mock_count.assert_not_called()


class CreditLedgerNegativeBalanceAnomalyMetricTests(TestCase):
    """billing/models.py::_check_ledger_anomaly: BE-A-09 #3, negative
    running balance."""

    def setUp(self):
        self.user = make_user("ledger-balance-anomaly@example.com")
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)

    @patch("billing.models.audit_metrics.count")
    def test_a_ledger_write_that_leaves_a_negative_balance_emits_the_anomaly(
        self, mock_count
    ):
        # used_credits > total_credits bypasses the per-bucket clamp that
        # only lives in the `remaining_credits` PROPERTY, not the raw
        # aggregate `total_remaining_credits()` reads from - simulating
        # the bug class this metric exists to catch, not inventing a new
        # one.
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=100,
            used_credits=200,
            expires_at=timezone.now() + timedelta(days=30),
        )

        CreditLedger.record(
            user=self.user,
            ledger_type=CreditLedgerType.GRANT,
            amount=0,
            reference="test",
        )

        mock_count.assert_called_once_with(
            "credit_ledger_anomaly",
            tags={"kind": "negative_balance", "ledger_type": CreditLedgerType.GRANT},
        )

    @patch("billing.models.audit_metrics.count")
    def test_a_healthy_balance_emits_no_anomaly(self, mock_count):
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=500,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )

        CreditLedger.record(
            user=self.user,
            ledger_type=CreditLedgerType.GRANT,
            amount=500,
            reference="test",
        )

        mock_count.assert_not_called()


class CreditLedgerRefundAnomalyMetricTests(TestCase):
    """billing/services.py::refund_credits: BE-A-09 #3, a REFUND that
    doesn't reconcile with what its usage log claims."""

    def setUp(self):
        self.teacher = make_user("refund-anomaly@example.com")
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        self.bucket = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )

    def _consume(self, amount, task_id):
        return self.wallet.consume_credits(
            amount=amount,
            feature="Grading Assignment",
            task_type="grade_assignment",
            task_id=task_id,
        )

    @patch("billing.services.audit_metrics.count")
    def test_a_reconciling_refund_emits_no_anomaly(self, mock_count):
        self._consume(500, task_id="task-clean")
        SubscriptionService.refund_credits("task-clean")
        mock_count.assert_not_called()

    @patch("billing.services.audit_metrics.count")
    def test_a_refund_that_cannot_fully_reconcile_emits_the_mismatch_anomaly(
        self, mock_count
    ):
        self._consume(500, task_id="task-mismatch")
        # Same setup as test_credit_refund.py's
        # test_refund_clamps_instead_of_going_negative: the bucket was
        # reset out-of-band to less than what this task consumed from it.
        self.bucket.refresh_from_db()
        self.bucket.used_credits = 20
        self.bucket.save(update_fields=["used_credits"])

        SubscriptionService.refund_credits("task-mismatch")

        mock_count.assert_called_once_with(
            "credit_ledger_anomaly", tags={"kind": "refund_mismatch"}
        )

    # No test for the "refund_no_bucket" branch (a usage log whose bucket
    # row is gone): CreditUsageLog.bucket is NOT NULL, so
    # refund_credits()'s own `select_related("bucket")` INNER JOINs it -
    # deleting the bucket makes the log invisible to that query entirely
    # (refund_credits returns 0 before the loop this metric lives in ever
    # runs), not merely "bucket is None" inside the loop. That branch was
    # already defensive/unreachable-by-the-ORM before this change (see its
    # comment in billing/services.py) and stays that way; the metric call
    # is left in place as the same kind of defense-in-depth as the branch
    # itself, not exercised by a test that cannot actually reach it.


class CreditLedgerExpireAnomalyMetricTests(TestCase):
    """billing/tasks.py::cleanup_expired_credit_buckets: BE-A-09 #3, an
    EXPIRE that doesn't reconcile - reuses this task's own existing
    reconciliation-failure handling."""

    @patch("billing.tasks.audit_metrics.count")
    @patch(
        "billing.tasks.SubscriptionService.expire_bucket",
        side_effect=RuntimeError("boom"),
    )
    def test_a_failed_bucket_expiration_emits_the_anomaly_exactly_once(
        self, mock_expire, mock_count
    ):
        user = make_user("expire-anomaly@example.com")
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100,
            used_credits=0,
            expires_at=timezone.now() - timedelta(days=1),
            is_processed=False,
        )

        cleanup_expired_credit_buckets()

        mock_count.assert_called_once_with(
            "credit_ledger_anomaly", tags={"kind": "expire_failed"}
        )

    @patch("billing.tasks.audit_metrics.count")
    def test_a_clean_expiration_emits_no_anomaly(self, mock_count):
        user = make_user("expire-clean@example.com")
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100,
            used_credits=40,
            expires_at=timezone.now() - timedelta(days=1),
            is_processed=False,
        )

        cleanup_expired_credit_buckets()

        mock_count.assert_not_called()
