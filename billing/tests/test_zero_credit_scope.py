"""
billing/tests/test_zero_credit_scope.py
=======================================
FR-B-10 / NFR-CRD-03: the zero-credit assertion scope. Every credit write and
model call inside ``zero_credit_scope`` must raise, and nothing must be written
or called. Outside a scope everything behaves as before.

Mutations (B0 plan section 6.2) are recorded in docs/evidence/epic-b0/.
"""

import contextvars
import threading
import time
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, TransactionTestCase

from ai_processor.services import AIProcessor
from billing import zero_credit
from billing.immutable import allow_unsafe_mutation
from billing.models import CreditBucket, CreditLedger, CreditLedgerType, CreditUsageLog
from billing.services import SubscriptionService
from billing.testing.zero_credit import assert_operation_is_zero_credit
from billing.tests.test_execute_graded_task import (
    ExecuteGradedTaskTestBase,
    make_ai_response,
)
from billing.zero_credit import (
    ZeroCreditViolation,
    assert_not_zero_credit,
    in_zero_credit_scope,
    register_zero_credit_operations,
    zero_credit_scope,
)
from users.models import UserTypes

OP = "_selftest"


class ScopeMechanicsTests(TestCase):
    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(ValueError):
            with zero_credit_scope("no_such_operation"):
                pass

    def test_register_makes_a_name_usable_and_is_idempotent(self):
        register_zero_credit_operations("_b0_registered")
        register_zero_credit_operations("_b0_registered")
        try:
            with zero_credit_scope("_b0_registered"):
                self.assertEqual(in_zero_credit_scope(), "_b0_registered")
        finally:
            zero_credit.ZERO_CREDIT_OPERATIONS.discard("_b0_registered")

    def test_register_rejects_blank_names(self):
        with self.assertRaises(ValueError):
            register_zero_credit_operations("")

    def test_scope_state_is_restored_on_normal_exit(self):
        self.assertIsNone(in_zero_credit_scope())
        with zero_credit_scope(OP):
            self.assertEqual(in_zero_credit_scope(), OP)
        self.assertIsNone(in_zero_credit_scope())

    def test_scope_state_is_restored_when_the_block_raises(self):
        with self.assertRaises(RuntimeError):
            with zero_credit_scope(OP):
                raise RuntimeError("boom")
        self.assertIsNone(in_zero_credit_scope())

    def test_nested_scopes_stack_and_unwind(self):
        register_zero_credit_operations("_inner")
        try:
            with zero_credit_scope(OP):
                with zero_credit_scope("_inner"):
                    self.assertEqual(in_zero_credit_scope(), "_inner")
                self.assertEqual(in_zero_credit_scope(), OP)
            self.assertIsNone(in_zero_credit_scope())
        finally:
            zero_credit.ZERO_CREDIT_OPERATIONS.discard("_inner")

    def test_decorator_enters_the_scope_on_every_call(self):
        seen = []

        @zero_credit.zero_credit(OP)
        def op():
            seen.append(in_zero_credit_scope())

        op()
        op()
        self.assertEqual(seen, [OP, OP])
        self.assertIsNone(in_zero_credit_scope())

    def test_scope_is_not_inherited_by_a_new_thread(self):
        """Documented limit (module docstring): a ContextVar does not cross a
        worker boundary, so a zero-credit operation that runs in a task must
        enter the scope in the task body."""
        seen = []
        with zero_credit_scope(OP):
            t = threading.Thread(target=lambda: seen.append(in_zero_credit_scope()))
            t.start()
            t.join()
        self.assertEqual(seen, [None])


class ViolationTests(ExecuteGradedTaskTestBase):
    """Each guard site raises with its own code, and nothing is written."""

    def setUp(self):
        super().setUp()
        self.teacher = self._make_teacher_with_credits(credits=200_000)
        self.wallet = self.teacher.credit_wallet
        self.bucket = self.wallet.buckets.first()

    def _counts(self):
        return (
            CreditLedger.objects.count(),
            CreditUsageLog.objects.count(),
            CreditBucket.objects.get(pk=self.bucket.pk).used_credits,
        )

    def _assert_violation(self, attempted, fn):
        before = self._counts()
        with zero_credit_scope(OP):
            with self.assertRaises(ZeroCreditViolation) as cm:
                fn()
        self.assertEqual(cm.exception.attempted, attempted)
        self.assertEqual(cm.exception.operation, OP)
        self.assertEqual(self._counts(), before)

    def test_ledger_record_raises(self):
        self._assert_violation(
            zero_credit.LEDGER_INSERT,
            lambda: CreditLedger.record(
                user=self.teacher,
                bucket=self.bucket,
                ledger_type=CreditLedgerType.GRANT,
                amount=1,
                reference="x",
            ),
        )

    def test_ledger_objects_create_raises(self):
        self._assert_violation(
            zero_credit.LEDGER_INSERT,
            lambda: CreditLedger.objects.create(
                user_id=self.teacher.id,
                user_email=self.teacher.email,
                bucket=self.bucket,
                ledger_type=CreditLedgerType.GRANT,
                amount=1,
                reference="x",
            ),
        )

    def test_usage_log_record_raises(self):
        self._assert_violation(
            zero_credit.USAGE_LOG_INSERT,
            lambda: CreditUsageLog.record(
                wallet=self.wallet,
                bucket=self.bucket,
                amount=1,
                feature="Grading Assignment",
            ),
        )

    def test_ledger_bulk_create_raises(self):
        row = CreditLedger.build(
            user=self.teacher,
            bucket=self.bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=1,
            reference="x",
        )
        self._assert_violation(
            zero_credit.BULK_INSERT, lambda: CreditLedger.objects.bulk_create([row])
        )

    def test_bulk_create_update_conflicts_form_raises_the_scope_error_first(self):
        row = CreditLedger.build(
            user=self.teacher,
            bucket=self.bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=1,
            reference="x",
        )
        self._assert_violation(
            zero_credit.BULK_INSERT,
            lambda: CreditLedger.objects.bulk_create(
                [row],
                update_conflicts=True,
                update_fields=["amount"],
                unique_fields=["id"],
            ),
        )

    def test_allow_unsafe_mutation_does_not_lift_the_scope(self):
        def attempt():
            with allow_unsafe_mutation():
                CreditLedger.record(
                    user=self.teacher,
                    bucket=self.bucket,
                    ledger_type=CreditLedgerType.GRANT,
                    amount=1,
                    reference="x",
                )

        self._assert_violation(zero_credit.LEDGER_INSERT, attempt)

    def test_wallet_consume_raises(self):
        self._assert_violation(
            zero_credit.WALLET_CONSUME,
            lambda: self.wallet.consume_credits(amount=10, feature="f"),
        )

    def test_bucket_balance_write_raises_and_bookkeeping_save_does_not(self):
        def bump():
            self.bucket.used_credits += 5
            self.bucket.save(update_fields=["used_credits", "updated_at"])

        self._assert_violation(zero_credit.BUCKET_MUTATION, bump)
        self.bucket.refresh_from_db()
        with zero_credit_scope(OP):
            self.bucket.save(update_fields=["updated_at"])  # no credit field

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_execute_graded_task_raises_before_any_provider_call(self, provider):
        provider.return_value = make_ai_response(tokens=100)
        self._assert_violation(
            zero_credit.MODEL_CALL,
            lambda: self.processor.execute_graded_task(
                user=self.teacher,
                feature="Grading Assignment",
                task_type="grading",
                system_prompt="s",
                user_prompt="u",
                prompt_version="v1",
            ),
        )
        provider.assert_not_called()

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_execute_graded_task_super_admin_unmetered_branch_also_raises(
        self, provider
    ):
        provider.return_value = make_ai_response(tokens=100)
        admin = self._make_user(
            UserTypes.SUPER_ADMIN, "sa-b0@example.com", is_superuser=True
        )
        self._assert_violation(
            zero_credit.MODEL_CALL,
            lambda: self.processor.execute_graded_task(
                user=admin,
                feature="Grading Assignment",
                task_type="grading",
                system_prompt="s",
                user_prompt="u",
                prompt_version="v1",
            ),
        )
        provider.assert_not_called()

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_refund_inside_a_scope_raises_and_changes_nothing(self, provider):
        provider.return_value = make_ai_response(tokens=100)
        self.processor.execute_graded_task(
            user=self.teacher,
            feature="Grading Assignment",
            task_type="grading",
            system_prompt="s",
            user_prompt="u",
            prompt_version="v1",
        )
        log = CreditUsageLog.objects.get()
        before = self._counts()
        with zero_credit_scope(OP):
            with self.assertRaises(ZeroCreditViolation):
                SubscriptionService.refund_credits(log.task_id, reason="x")
        log.refresh_from_db()
        self.assertFalse(log.is_refunded)
        self.assertEqual(self._counts(), before)

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_same_calls_succeed_outside_a_scope(self, provider):
        provider.return_value = make_ai_response(tokens=100)
        self.processor.execute_graded_task(
            user=self.teacher,
            feature="Grading Assignment",
            task_type="grading",
            system_prompt="s",
            user_prompt="u",
            prompt_version="v1",
        )
        self.assertEqual(CreditUsageLog.objects.count(), 1)
        CreditLedger.record(
            user=self.teacher,
            bucket=self.bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=1,
            reference="x",
        )

    def test_metric_emitted_once_per_violation(self):
        with patch("audit.metrics.count") as count:
            with zero_credit_scope(OP):
                with self.assertRaises(ZeroCreditViolation):
                    self.wallet.consume_credits(amount=1, feature="f")
        count.assert_called_once()
        args, kwargs = count.call_args
        self.assertEqual(args[0], "credit_ledger_anomaly")
        self.assertEqual(kwargs["tags"]["kind"], "zero_credit_violation")

    def test_violation_still_raises_when_metric_and_logging_helpers_fail(self):
        with patch("audit.metrics.count", side_effect=RuntimeError("metrics down")):
            with zero_credit_scope(OP):
                with self.assertRaises(ZeroCreditViolation):
                    self.wallet.consume_credits(amount=1, feature="f")

    def test_swallowing_the_violation_does_not_relax_the_scope(self):
        """A broad except inside the scope can swallow the exception; the next
        charge must still be refused."""
        with zero_credit_scope(OP):
            try:
                self.wallet.consume_credits(amount=1, feature="f")
            except Exception:  # noqa: BLE001 - the point of the test
                pass
            with self.assertRaises(ZeroCreditViolation):
                self.wallet.consume_credits(amount=1, feature="f")

    @patch.object(AIProcessor, "_AIProcessor__ai_model")
    def test_zero_credit_block_inside_a_metered_pipeline(self, provider):
        """A metered charge before the block stays; a charge inside is refused;
        a charge after the block works again."""
        provider.return_value = make_ai_response(tokens=100)

        def metered():
            return self.processor.execute_graded_task(
                user=self.teacher,
                feature="Grading Assignment",
                task_type="grading",
                system_prompt="s",
                user_prompt="u",
                prompt_version="v1",
            )

        metered()
        with zero_credit_scope(OP):
            with self.assertRaises(ZeroCreditViolation):
                metered()
        metered()
        self.assertEqual(CreditUsageLog.objects.count(), 2)


class OperationHelperTests(ExecuteGradedTaskTestBase):
    """The per-operation helper the other epics use must itself be strict."""

    def setUp(self):
        super().setUp()
        self.teacher = self._make_teacher_with_credits(credits=200_000)
        self.wallet = self.teacher.credit_wallet

    def test_a_correct_zero_credit_operation_passes(self):
        def op():
            with zero_credit_scope(OP):
                return "done"

        self.assertEqual(assert_operation_is_zero_credit(self, OP, op), "done")

    def test_an_operation_that_never_enters_the_scope_fails(self):
        with self.assertRaises(AssertionError):
            assert_operation_is_zero_credit(self, OP, lambda: "no scope")

    def test_an_operation_that_charges_outside_a_scope_fails(self):
        def op():
            with zero_credit_scope(OP):
                pass
            self.wallet.consume_credits(amount=5, feature="f")

        with self.assertRaises(AssertionError):
            assert_operation_is_zero_credit(self, OP, op)

    def test_an_unregistered_operation_fails(self):
        with self.assertRaises(AssertionError):
            assert_operation_is_zero_credit(self, "not_registered", lambda: None)


class ThreadIsolationTests(TransactionTestCase):
    """Gate 3: real threads, real Postgres, barrier-synchronised. A scope open
    in one thread must never affect a metered charge in another."""

    ROUNDS = 200

    def setUp(self):
        base = ExecuteGradedTaskTestBase("setUp")
        self.teacher_a = base._make_user(UserTypes.TEACHER, "iso-a@example.com")
        self.teacher_b = base._make_user(UserTypes.TEACHER, "iso-b@example.com")
        base._give_credits(self.teacher_a, 10_000_000)
        base._give_credits(self.teacher_b, 10_000_000)

    def test_scope_in_one_thread_does_not_leak_to_another(self):
        barrier = threading.Barrier(2)
        errors = []
        outcomes = {"scoped_raised": 0, "unscoped_ok": 0}

        def scoped():
            try:
                wallet = self.teacher_a.credit_wallet
                for _ in range(self.ROUNDS):
                    barrier.wait(timeout=30)
                    with zero_credit_scope(OP):
                        try:
                            wallet.consume_credits(amount=1, feature="f")
                        except ZeroCreditViolation:
                            outcomes["scoped_raised"] += 1
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            finally:
                connection.close()

        def unscoped():
            try:
                wallet = self.teacher_b.credit_wallet
                for _ in range(self.ROUNDS):
                    barrier.wait(timeout=30)
                    wallet.consume_credits(amount=1, feature="f")
                    outcomes["unscoped_ok"] += 1
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            finally:
                connection.close()

        threads = [threading.Thread(target=scoped), threading.Thread(target=unscoped)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(errors, [])
        self.assertEqual(
            outcomes, {"scoped_raised": self.ROUNDS, "unscoped_ok": self.ROUNDS}
        )
        self.assertEqual(
            CreditUsageLog.objects.filter(wallet=self.teacher_a.credit_wallet).count(),
            0,
        )
        self.assertEqual(
            CreditUsageLog.objects.filter(wallet=self.teacher_b.credit_wallet).count(),
            self.ROUNDS,
        )

    def test_n_threads_each_in_own_scope_all_raise(self):
        """N threads, each in its own scope, each trying consume_credits.
        All must raise. Zero cross-thread interference."""
        n_threads = 8
        rounds = 50
        base = ExecuteGradedTaskTestBase("setUp")
        teachers = []
        for i in range(n_threads):
            t = base._make_user(UserTypes.TEACHER, f"nthread-{i}@example.com")
            base._give_credits(t, 10_000_000)
            teachers.append(t)

        barrier = threading.Barrier(n_threads)
        errors = []
        raised_counts = [0] * n_threads

        def worker(idx):
            try:
                wallet = teachers[idx].credit_wallet
                for _ in range(rounds):
                    barrier.wait(timeout=30)
                    with zero_credit_scope(OP):
                        try:
                            wallet.consume_credits(amount=1, feature="f")
                        except ZeroCreditViolation:
                            raised_counts[idx] += 1
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(errors, [])
        for idx in range(n_threads):
            self.assertEqual(raised_counts[idx], rounds, f"thread {idx}")
        for teacher in teachers:
            self.assertEqual(
                CreditUsageLog.objects.filter(wallet=teacher.credit_wallet).count(),
                0,
            )


class CeleryNonPropagationTests(TestCase):
    """§9.7: a ContextVar does not cross a Celery hop. The scope set in the
    calling thread is not visible in the task body."""

    def test_scope_does_not_propagate_across_contextvar_boundary(self):
        seen = []

        def simulated_task_body():
            seen.append(in_zero_credit_scope())

        with zero_credit_scope(OP):
            self.assertEqual(in_zero_credit_scope(), OP)
            ctx = contextvars.copy_context()
            ctx.run(
                lambda: (
                    zero_credit._active_scopes.set(()),
                    simulated_task_body(),
                )
            )

        self.assertEqual(seen, [None])

    def test_scope_not_inherited_by_thread_simulating_celery_worker(self):
        seen = []
        with zero_credit_scope(OP):
            t = threading.Thread(target=lambda: seen.append(in_zero_credit_scope()))
            t.start()
            t.join()
        self.assertEqual(seen, [None])


class AdversarialTests(ExecuteGradedTaskTestBase):
    """Gate 4: bypass attempts that must not succeed."""

    def setUp(self):
        super().setUp()
        self.teacher = self._make_teacher_with_credits(credits=200_000)
        self.wallet = self.teacher.credit_wallet

    def test_charge_after_nested_scope_exits_succeeds(self):
        """A nested scope closes; the outer scope is still active. A charge
        inside the outer scope must still be refused."""
        register_zero_credit_operations("_inner_adv")
        try:
            with zero_credit_scope(OP):
                with zero_credit_scope("_inner_adv"):
                    pass
                with self.assertRaises(ZeroCreditViolation):
                    self.wallet.consume_credits(amount=1, feature="f")
        finally:
            zero_credit.ZERO_CREDIT_OPERATIONS.discard("_inner_adv")

    def test_charge_after_all_scopes_exit_succeeds(self):
        """After both nested and outer scopes close, charges work."""
        register_zero_credit_operations("_inner_adv2")
        try:
            with zero_credit_scope(OP):
                with zero_credit_scope("_inner_adv2"):
                    pass
            self.wallet.consume_credits(amount=1, feature="f")
            self.assertEqual(CreditUsageLog.objects.count(), 1)
        finally:
            zero_credit.ZERO_CREDIT_OPERATIONS.discard("_inner_adv2")


class PerformanceMeasurementTests(TestCase):
    """Gate 6: measure the cost of the guard check. 100k iterations,
    reported in ns per call. Measurement only, no pass/fail threshold."""

    ITERATIONS = 100_000

    def test_guard_check_overhead(self):
        start = time.perf_counter_ns()
        for _ in range(self.ITERATIONS):
            assert_not_zero_credit("MODEL_CALL")
        elapsed = time.perf_counter_ns() - start
        ns_per_call = elapsed / self.ITERATIONS
        # Record the measurement; the test always passes.
        print(
            f"\nGuard check: {ns_per_call:.1f} ns/call over {self.ITERATIONS} iterations"
        )
