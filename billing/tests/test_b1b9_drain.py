"""Epic B1b-9 (08b §6.3; NOTES §9.0-§9.3): the drain, a safe switch-off.

While BILLING_HOLDS_DRAINING is on (the flag still on):
- new work makes no hold, job or queue entry; it runs the flag-off path.
  Q9-1: a metered unit keeps the holds path only when its task row is
  HELD. No row, a row with no job (the SINGLE_ITEM attach), a retry and a
  late door are new work; a nested unit follows its root;
- Q9-2: the flag-off balance check reads SPENDABLE (total less reserved),
  by SM ruling (a departure from 08b's "exactly as flag off"), so open
  holds are not overspent; the door reads spendable as it already did;
- what is already held keeps running: settle, fail, the sweep, the
  invariant check; promotion stops;
- Q9-4: the four queue emails are silent;
- Q9-6: DRAINING with the flag OFF (the emergency state) is allowed, with
  one WARNING at start-up.

billing_holds_drain refuses unless both settings are on; expires queued
jobs with no email; waits (Q9-5: default timeout item_sweep_window() + 10
min); prints DRAINED or exits 1 naming what is open. --flag-off-emergency
needs the flag OFF and DRAINING on, and returns every hold at once through
the sweep with force=True (Q9-3), the only caller allowed to pass it.

    python manage.py test billing.tests.test_b1b9_drain
"""

import ast
import importlib
import inspect
import io
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from ai_processor.services import AIProcessor
from assignments.models import Assignment
from billing.errors import InsufficientCreditsError
from billing.models import (
    AIJob,
    AIJobStatus,
    CreditLedger,
    CreditUsageLog,
    CreditWallet,
    SettlementRecord,
)
from billing.testing.holds import (
    ai_ready_teacher,
    ai_response,
    call_ai,
    estimate_for,
    fresh,
    funded_wallet,
    holds,
    make_item,
    make_job,
)
from billing.tests.test_b1b8_texts import QueueFixture
from billing.tests.test_batch_queue import PER, BatchBase
from billing.tests.test_holds_door import fixed_estimate, png_file
from billing.tests.test_holds_entry import classroom
from students.models import BackgroundProcessingTask, BackgroundTaskType

PROVIDER = "_AIProcessor__ai_model"
SEND = "AutoGrader.tasks.send_email_task.delay"
COMMAND = "billing.management.commands.billing_holds_drain"
REPO = Path(settings.BASE_DIR)
DRAINING = override_settings(BILLING_HOLDS_ENABLED=True, BILLING_HOLDS_DRAINING=True)
EMERGENCY = override_settings(BILLING_HOLDS_ENABLED=False, BILLING_HOLDS_DRAINING=True)


def wallet_of(user):
    return CreditWallet.objects.get(user=user)


def drain(*args):
    """Run the command; (exit code, everything it printed or raised)."""
    out = io.StringIO()
    try:
        call_command("billing_holds_drain", *args, stdout=out, stderr=out)
    except CommandError as exc:
        return exc.returncode, out.getvalue() + str(exc)
    return 0, out.getvalue()


# --------------------------------------------------------------------------
# New work runs flag-off; what is held keeps running (Q9-1, Q9-2)
# --------------------------------------------------------------------------


@DRAINING
class DrainingCallTests(TestCase):
    def setUp(self):
        self.teacher = ai_ready_teacher(
            f"b9.{self._testMethodName[-24:]}@example.com", 10 * estimate_for()
        )
        self.wallet = wallet_of(self.teacher)

    def run_calls(self, calls=1, tokens=100, processing_task_id=None, unit=True):
        with patch.object(
            AIProcessor, PROVIDER, return_value=ai_response(tokens)
        ) as provider:
            if unit:
                with holds().metered_unit(
                    processing_task_id=processing_task_id, reason="t"
                ):
                    for _ in range(calls):
                        call_ai(self.teacher)
            else:
                for _ in range(calls):
                    call_ai(self.teacher)
        return provider

    def assert_ran_flag_off(self, jobs=0, charges=1):
        self.assertEqual(AIJob.objects.count(), jobs)
        self.assertEqual(SettlementRecord.objects.count(), 0)
        self.assertEqual(CreditUsageLog.objects.count(), charges)
        self.assertEqual(fresh(self.wallet).reserved_credits, 0)

    def test_a_single_call_takes_no_hold(self):
        self.run_calls(unit=False)
        self.assert_ran_flag_off()

    def test_q92_a_call_the_total_covers_but_spendable_does_not_is_refused(self):
        other = make_job(self.wallet, "SINGLE_CALL", status="RUNNING")
        holds().reserve(self.wallet, 10 * estimate_for() - 100, other)
        with patch.object(
            AIProcessor, PROVIDER, return_value=ai_response(100)
        ) as provider:
            with self.assertRaises(InsufficientCreditsError):
                call_ai(self.teacher)
        provider.assert_not_called()
        self.assertEqual(AIJob.objects.count(), 1)

    def test_q91_a_unit_with_no_row_runs_flag_off(self):
        self.run_calls(calls=2)
        self.assert_ran_flag_off(charges=2)

    def test_q91_a_row_with_no_job_is_not_attached_and_runs_flag_off(self):
        row = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher, task_type="answer_extraction"
        )
        self.run_calls(processing_task_id=row.id)
        self.assertIsNone(fresh(row).job_id)
        self.assert_ran_flag_off()

    def test_q91_a_retry_opens_no_job_and_runs_flag_off(self):
        old = make_job(self.wallet, "BATCH_GRADING", total_items=1, status="RUNNING")
        row = make_item(old)
        holds().reserve(self.wallet, 50, old, item=row)
        holds().fail_item(old, item=row)
        BackgroundProcessingTask.objects.filter(pk=row.pk).update(
            status="PENDING", retry_count=1
        )
        self.run_calls(processing_task_id=row.id)
        self.assert_ran_flag_off(jobs=1)

    def test_q91_a_late_door_opens_no_job_and_runs_flag_off(self):
        old = make_job(self.wallet, "UPLOAD", total_items=1, status="RUNNING")
        started = timezone.now() - holds().item_sweep_window() - timedelta(minutes=1)
        row = make_item(old, started_at=started)
        holds().reserve(self.wallet, 50, old, item=row)
        holds().sweep_stale_holds()
        self.run_calls(processing_task_id=row.id)
        self.assert_ran_flag_off(jobs=1)

    def test_q91_a_held_row_keeps_running_and_settles_on_its_hold(self):
        job = make_job(self.wallet, "BATCH_GRADING", total_items=2, status="RUNNING")
        row = make_item(job)
        holds().reserve(self.wallet, estimate_for(), job, item=row)
        holds().reserve(self.wallet, estimate_for(), job, item=make_item(job))
        self.run_calls(processing_task_id=row.id)
        self.assertEqual(SettlementRecord.objects.get(job=job).item_id, row.id)
        self.assertEqual(fresh(row).hold_state, "RETURNED")
        self.assertEqual(CreditUsageLog.objects.count(), 1)
        self.assertEqual(fresh(self.wallet).reserved_credits, estimate_for())

    def test_q91_a_nested_unit_follows_a_held_root(self):
        job = make_job(self.wallet, "BATCH_GRADING", total_items=2, status="RUNNING")
        row = make_item(job)
        holds().reserve(self.wallet, estimate_for(), job, item=row)
        holds().reserve(self.wallet, estimate_for(), job, item=make_item(job))
        loose = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher, task_type="answer_extraction"
        )
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(100)):
            with holds().metered_unit(processing_task_id=row.id, reason="t"):
                with holds().metered_unit(processing_task_id=loose.id, reason="t"):
                    call_ai(self.teacher)
        self.assertEqual(AIJob.objects.count(), 1)
        self.assertEqual(SettlementRecord.objects.get().item_id, row.id)
        self.assertIsNone(fresh(loose).job_id)

    def test_q91_a_nested_unit_follows_a_flag_off_root(self):
        job = make_job(self.wallet, "BATCH_GRADING", total_items=1, status="RUNNING")
        held = make_item(job)
        holds().reserve(self.wallet, estimate_for(), job, item=held)
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(100)):
            with holds().metered_unit(reason="t"):
                with holds().metered_unit(processing_task_id=held.id, reason="t"):
                    call_ai(self.teacher)
        self.assertIsNone(fresh(held).started_at)
        self.assertEqual(SettlementRecord.objects.count(), 0)
        self.assertEqual(CreditUsageLog.objects.count(), 1)
        self.assertEqual(fresh(self.wallet).reserved_credits, estimate_for())


@DRAINING
class DrainingDoorTests(TestCase):
    def upload(self, student, assignment):
        from django.urls import reverse
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=student)
        url = reverse(
            "student-submission-upload-async", kwargs={"assignment_id": assignment.pk}
        )
        return client.post(url, {"answer": png_file()}, format="multipart")

    def test_the_door_holds_nothing_and_launches_at_once(self):
        teacher, student, _, assignment, submission = classroom(
            "b9d1", 10 * estimate_for()
        )
        submission.delete()
        with fixed_estimate(), patch("students.views.launch_processing_task") as launch:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.upload(student, assignment)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(callbacks, [])
        launch.assert_called_once()
        self.assertFalse(AIJob.objects.exists())
        self.assertIsNone(BackgroundProcessingTask.objects.get().hold_state)
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)

    def test_q92_the_door_reads_spendable(self):
        teacher, student, _, assignment, submission = classroom("b9d2", 6_000)
        submission.delete()
        holds().reserve(wallet_of(teacher), 2_000, make_job(wallet_of(teacher)))
        with fixed_estimate(), patch("students.views.launch_processing_task") as launch:
            response = self.upload(student, assignment)
        self.assertEqual(response.status_code, 402)
        launch.assert_not_called()


class DrainingBatchTests(BatchBase):
    @DRAINING
    def test_a_batch_makes_no_job_and_sends_every_paper(self):
        self.classroom(credits=50, papers=3)
        self.dispatch()
        self.assertEqual(len(self.sent), 3)
        self.assertFalse(AIJob.objects.exists())
        self.assertEqual(self.reserved(), 0)

    def test_an_auto_grade_retry_while_draining_relaunches_its_papers_flag_off(self):
        from assignments.tasks import auto_grade_due_assignment
        from assignments.tests_cache_matrix_g3 import question

        self.classroom(credits=1000, papers=3)
        Assignment.objects.filter(pk=self.assignment.pk).update(
            auto_grade_on_due_date=True, questions=[question(1)]
        )
        self.fail_at = {2}
        with patch.object(auto_grade_due_assignment, "retry") as retry:
            retry.side_effect = RuntimeError("retried")
            with self.assertRaisesMessage(RuntimeError, "retried"):
                auto_grade_due_assignment(str(self.assignment.id))
        session_id = retry.call_args.kwargs["kwargs"]["session_id"]
        self.fail_at = set()
        with self.settings(BILLING_HOLDS_DRAINING=True):
            auto_grade_due_assignment(str(self.assignment.id), session_id=session_id)
        rows = BackgroundProcessingTask.objects.filter(batch_session_id=session_id)
        self.assertEqual(
            sorted(s for s in self.sent if s), sorted(str(r.pk) for r in rows)
        )
        self.assertEqual(AIJob.objects.filter(job_type="BATCH_GRADING").count(), 1)


# --------------------------------------------------------------------------
# Promotion stops; the queue emails are silent (Q9-4)
# --------------------------------------------------------------------------


@DRAINING
class DrainingPromotionTests(TestCase):
    def test_a_credit_entry_enqueues_no_promotion(self):
        wallet = funded_wallet("b9.p1@example.com", 0)
        with patch("billing.tasks.promote_queued_jobs_for_wallet.delay") as promote:
            with self.captureOnCommitCallbacks(execute=True):
                CreditLedger.record(user=wallet.user, ledger_type="GRANT", amount=500)
        promote.assert_not_called()

    def test_the_promotion_tasks_promote_nothing(self):
        from billing.tasks import promote_queued_jobs, promote_queued_jobs_for_wallet

        with patch("billing.batch_queue.promote_all") as every, patch(
            "billing.batch_queue.promote_wallet"
        ) as one:
            results = (
                promote_queued_jobs.apply().result,
                promote_queued_jobs_for_wallet.apply(args=(str(uuid.uuid4()),)).result,
            )
        every.assert_not_called()
        one.assert_not_called()
        self.assertEqual(results, ("credit holds draining",) * 2)


@DRAINING
class DrainingQueueEmailTests(QueueFixture):
    def test_queued_is_silent(self):
        self.waiting(3)
        self.send("queued").assert_not_called()

    def test_expiry_warning_is_silent(self):
        self.waiting(3)
        self.send("expiry_warning").assert_not_called()

    def test_restarted_is_silent(self):
        self.send("restarted", started=2, left=3).assert_not_called()

    def test_expired_is_silent(self):
        self.send("expired", left=3).assert_not_called()

    def test_the_beat_expiry_still_expires_and_sends_nothing(self):
        from billing.tasks import expire_queued_jobs

        self.waiting(2)
        AIJob.objects.filter(pk=self.job.pk).update(expires_at=timezone.now())
        with patch(SEND) as send:
            with self.captureOnCommitCallbacks(execute=True):
                expire_queued_jobs.apply()
        self.assertEqual(fresh(self.job).status, AIJobStatus.EXPIRED)
        send.assert_not_called()


# --------------------------------------------------------------------------
# Start-up (Q9-6) and the forced sweep's limits (Q9-3)
# --------------------------------------------------------------------------


class StartUpWarningTests(TestCase):
    @EMERGENCY
    def test_draining_with_the_flag_off_logs_one_warning(self):
        with self.assertLogs("billing.holds", "WARNING") as logs:
            holds().warn_if_draining_with_holds_off()
        self.assertEqual(len(logs.records), 1)

    def test_no_warning_otherwise(self):
        for enabled, draining in ((True, True), (True, False), (False, False)):
            with self.subTest(enabled=enabled, draining=draining), self.settings(
                BILLING_HOLDS_ENABLED=enabled, BILLING_HOLDS_DRAINING=draining
            ):
                with self.assertNoLogs("billing.holds", "WARNING"):
                    holds().warn_if_draining_with_holds_off()

    def test_the_app_checks_it_once_at_start_up(self):
        from django.apps import apps

        with patch("billing.holds.warn_if_draining_with_holds_off") as warn:
            apps.get_app_config("billing").ready()
        warn.assert_called_once_with()


class ForceLimitTests(TestCase):
    def test_force_is_keyword_only_and_off_by_default(self):
        force = inspect.signature(holds().sweep_stale_holds).parameters["force"]
        self.assertEqual((force.kind, force.default), (force.KEYWORD_ONLY, False))

    def test_only_the_emergency_command_passes_force(self):
        callers = set()
        for path in REPO.rglob("*.py"):
            relative = path.relative_to(REPO)
            parts = relative.parts
            if parts[0] in ("docs", "node_modules", "media", "static") or any(
                part.startswith(".") or part in ("tests", "site-packages")
                for part in parts
            ):
                continue
            if path.name.startswith("test"):
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", getattr(node.func, "id", None))
                if name == "sweep_stale_holds" and any(
                    k.arg == "force" for k in node.keywords
                ):
                    callers.add(str(relative))
        self.assertEqual(
            callers, {"billing/management/commands/billing_holds_drain.py"}
        )

    @override_settings(BILLING_HOLDS_ENABLED=True)
    def test_the_sweep_alone_leaves_an_unstarted_hold(self):
        wallet = funded_wallet("b9.f3@example.com", 1000)
        job = make_job(wallet, "BATCH_GRADING", total_items=1, status="RESERVED")
        row = make_item(job)
        holds().reserve(wallet, PER, job, item=row)
        holds().sweep_stale_holds(force=False)
        self.assertEqual(fresh(row).hold_state, "HELD")

    def test_force_returns_unstarted_holds_and_a_fresh_job_with_no_row(self):
        wallet = funded_wallet("b9.f4@example.com", 1000)
        job = make_job(wallet, "BATCH_GRADING", total_items=1, status="RESERVED")
        row = make_item(job)
        holds().reserve(wallet, PER, job, item=row)
        single = make_job(wallet, "SINGLE_CALL", status="RUNNING")
        holds().reserve(wallet, 50, single)
        result = holds().sweep_stale_holds(force=True)
        self.assertEqual((result["items_swept"], result["jobs_released"]), (1, 1))
        self.assertEqual(fresh(row).hold_state, "RETURNED")
        self.assertEqual(fresh(single).status, AIJobStatus.FAILED)
        self.assertEqual(fresh(wallet).reserved_credits, 0)
        self.assertEqual(holds().check_invariants(wallet), [])


# --------------------------------------------------------------------------
# billing_holds_drain (08b §6.3's four, and the rulings)
# --------------------------------------------------------------------------


class DrainCommandTests(TransactionTestCase):
    def setUp(self):
        self.wallet = funded_wallet(f"b9.c.{uuid.uuid4().hex[:6]}@example.com", 1000)

    def held_row(self, started=True):
        job = make_job(self.wallet, "BATCH_GRADING", total_items=1, status="RUNNING")
        row = make_item(job, started_at=timezone.now() if started else None)
        holds().reserve(self.wallet, PER, job, item=row)
        return job, row

    def queued_job(self, waiting=2):
        job = make_job(
            self.wallet,
            "BATCH_GRADING",
            total_items=waiting,
            status=AIJobStatus.QUEUED_PENDING_CREDITS,
            queued_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=30),
        )
        rows = [
            make_item(job, task_type=BackgroundTaskType.BATCH_SUBMISSION_GRADING)
            for _ in range(waiting)
        ]
        return job, rows

    def test_it_refuses_without_both_settings(self):
        for enabled, draining in ((False, False), (True, False), (False, True)):
            with self.subTest(enabled=enabled, draining=draining), self.settings(
                BILLING_HOLDS_ENABLED=enabled, BILLING_HOLDS_DRAINING=draining
            ):
                code, text = drain("--timeout=0")
                self.assertEqual(code, 2, text)

    @DRAINING
    def test_nothing_open_prints_drained(self):
        code, text = drain("--timeout=0")
        self.assertIn("DRAINED", text)
        self.assertEqual(code, 0, text)

    @DRAINING
    def test_an_open_hold_exits_1_naming_its_wallet_and_job(self):
        job, _ = self.held_row()
        code, text = drain("--timeout=0")
        self.assertIn(str(self.wallet.pk), text)
        self.assertIn(str(job.pk), text)
        self.assertNotIn("DRAINED\n", text)
        self.assertEqual(code, 1, text)

    @DRAINING
    def test_queued_jobs_are_expired_with_no_email(self):
        job, rows = self.queued_job()
        with patch(SEND) as send:
            code, text = drain("--timeout=0")
        self.assertEqual(fresh(job).status, AIJobStatus.EXPIRED)
        for row in rows:
            self.assertEqual(
                (fresh(row).status, fresh(row).reason_code),
                ("FAILURE", "INSUFFICIENT_CREDITS_MID_BATCH"),
            )
        send.assert_not_called()
        self.assertEqual(code, 0, text)

    @DRAINING
    def test_a_hold_that_settles_during_the_wait_ends_drained(self):
        job, row = self.held_row()

        def settle(seconds):
            holds().settle(fresh(job), 80, item=fresh(row))

        with patch(f"{COMMAND}.sleep", side_effect=settle) as sleep:
            code, text = drain("--interval=7", "--timeout=600")
        sleep.assert_called_once_with(7)
        self.assertIn("DRAINED", text)
        self.assertEqual(code, 0, text)
        self.assertEqual(fresh(self.wallet).reserved_credits, 0)

    def test_the_default_timeout_is_the_sweep_window_and_ten_minutes(self):
        module = importlib.import_module(COMMAND)
        self.assertEqual(
            module.default_timeout(),
            holds().item_sweep_window() + timedelta(minutes=10),
        )

    def test_the_emergency_refuses_unless_the_flag_is_off_and_draining_on(self):
        for enabled, draining in ((True, True), (False, False), (True, False)):
            with self.subTest(enabled=enabled, draining=draining), self.settings(
                BILLING_HOLDS_ENABLED=enabled, BILLING_HOLDS_DRAINING=draining
            ):
                code, text = drain("--flag-off-emergency")
                self.assertEqual(code, 2, text)

    @EMERGENCY
    def test_the_emergency_returns_every_hold_at_once_and_prints_drained(self):
        _, started = self.held_row(started=True)
        _, unstarted = self.held_row(started=False)
        single = make_job(self.wallet, "SINGLE_CALL", status="RUNNING")
        holds().reserve(self.wallet, 50, single)
        queued, waiting = self.queued_job()
        with patch(SEND) as send:
            code, text = drain("--flag-off-emergency")
        self.assertEqual(fresh(started).hold_state, "RETURNED")
        self.assertEqual(fresh(unstarted).hold_state, "RETURNED")
        self.assertEqual(fresh(single).status, AIJobStatus.FAILED)
        self.assertEqual(fresh(queued).status, AIJobStatus.EXPIRED)
        self.assertFalse(
            AIJob.objects.filter(status__in=holds().OPEN_STATUSES).exists()
        )
        self.assertEqual(fresh(self.wallet).reserved_credits, 0)
        self.assertEqual(holds().check_invariants(), [])
        send.assert_not_called()
        self.assertIn("DRAINED", text)
        self.assertEqual(code, 0, text)
