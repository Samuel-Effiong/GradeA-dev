"""Upload quote, slice 4 (plan rev 3 sections 4 and 7; BE-B-03/04/09): the
measure.

A zero-credit task (`measure_quote_file`) loads a quote file's kept bytes,
runs the door's own preparation (`prepare_ai_content`) and keeps the pages'
tokens; the figure is then the door's number for these bytes (tests 1, 3, 11
and 18 of the plan). It runs on its own queue `quote_measure` of the existing
worker, never more than UPLOAD_QUOTE_MEASURE_CONCURRENCY at once; a beat task
re-enqueues files stuck unmeasured.
"""

import ast
import importlib
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from ai_processor.models import AICallRecord
from ai_processor.tests_page_tokens import fixtures
from assignments.upload_door import _estimate_for
from assignments.upload_prompts import BATCH_UPLOAD_PROMPT
from audit import metrics
from AutoGrader.beat_locks import declared_lock
from billing.models import CreditLedger
from billing.tests.test_holds_entry import classroom
from students import kept_files as kept_files_module
from students.models import (
    BackgroundProcessingTask,
    BatchUploadSession,
    UploadQuote,
    UploadQuoteFile,
)

kept_files: Any = kept_files_module
State = UploadQuoteFile.State


def mod(name) -> Any:
    return importlib.import_module(name)


class Base(TestCase):
    def setUp(self):
        kept_files.MEMORY.clear()
        cache.clear()
        self.teacher, _, self.course, self.assignment, _ = classroom("qm", 10**9)

    def quote(self, kind=UploadQuote.Kind.ANSWER_PAPERS, **kwargs):
        defaults: dict = {
            "teacher": self.teacher,
            "kind": kind,
            "expires_at": timezone.now() + timedelta(hours=24),
        }
        if kind == UploadQuote.Kind.ANSWER_PAPERS:
            defaults["assignment"] = self.assignment
        else:
            defaults["course"] = self.course
        defaults.update(kwargs)
        return UploadQuote.objects.create(**defaults)

    def stored(self, quote, data, content_type="application/pdf", position=0, **kw):
        put = kept_files.store(data)
        defaults = {
            "quote": quote,
            "position": position,
            "state": State.STORED,
            "storage_key": put.storage_key,
            "original_name": "secret.pdf",
            "content_type": content_type,
            "size_bytes": put.size_bytes,
            "sha256": put.sha256,
        }
        defaults.update(kw)
        return UploadQuoteFile.objects.create(**defaults)

    def measure(self, row):
        task = mod("students.tasks").measure_quote_file
        task.run(str(row.pk))
        row.refresh_from_db()
        return row


class MeasureEqualsTheDoor(Base):
    """Test 1: the quote's per-file figure equals `_estimate_for` on the same
    bytes, both kinds."""

    def check(self, kind, assignment):
        service = mod("students.upload_quote_service")
        for label, data, content_type in fixtures():
            quote = self.quote(kind)
            row = self.stored(quote, data, content_type)
            self.measure(row)
            with self.subTest(fixture=label, kind=kind):
                self.assertEqual(row.state, State.MEASURED)
                self.assertTrue(row.readable)
                upload = SimpleUploadedFile("f", data, content_type=content_type)
                self.assertEqual(
                    service.figure_of(quote, row),
                    _estimate_for(upload, BATCH_UPLOAD_PROMPT, assignment),
                )
            quote.delete()

    def test_answer_papers(self):
        self.check(UploadQuote.Kind.ANSWER_PAPERS, self.assignment)

    def test_assignment_files(self):
        self.check(UploadQuote.Kind.ASSIGNMENT_FILES, None)

    def test_pages_are_kept_with_their_tokens(self):
        _, data, content_type = fixtures()[3]  # letter-4... any multi-page
        row = self.measure(self.stored(self.quote(), data, content_type))
        self.assertEqual(row.pages, len(row.page_tokens))
        self.assertIsNotNone(row.measured_at)

    def test_a_roster_change_after_the_measure_keeps_the_figure_the_doors(self):
        from classrooms.models import EnrollmentStatusType, StudentCourse
        from users.models import CustomUser, UserTypes

        service = mod("students.upload_quote_service")
        _, data, content_type = fixtures()[2]
        quote = self.quote()
        row = self.measure(self.stored(quote, data, content_type))
        before = service.figure_of(quote, row)
        for n in range(30):
            student = CustomUser.objects.create_user(
                email=f"qm-r{n}@example.com",
                password="password123",  # pragma: allowlist secret
                user_type=UserTypes.STUDENT,
                first_name=f"Roster{n}",
                last_name="Name",
            )
            StudentCourse.objects.create(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
        after = service.figure_of(quote, row)
        self.assertGreater(after, before)
        upload = SimpleUploadedFile("f", data, content_type=content_type)
        self.assertEqual(
            after, _estimate_for(upload, BATCH_UPLOAD_PROMPT, self.assignment)
        )


class MeasureOutcomes(Base):
    def test_a_file_the_door_cannot_read_is_left_out_not_failed(self):
        row = self.measure(self.stored(self.quote(), b"%PDF-1.4 not really a pdf"))
        self.assertEqual(row.state, State.MEASURED)
        self.assertIsNone(row.readable)
        self.assertIsNone(row.page_tokens)
        self.assertTrue(row.reason_code)  # the door's own code
        service = mod("students.upload_quote_service")
        self.assertIsNone(service.figure_of(row.quote, row))
        self.assertTrue(service.is_ready([row]))

    def test_bytes_that_no_longer_match_their_hash_are_not_kept(self):
        row = self.stored(self.quote(), fixtures()[0][1])
        kept_files.MEMORY[row.storage_key] = b"something else"
        row = self.measure(row)
        self.assertEqual(row.state, State.NOT_KEPT)
        self.assertEqual(row.reason_code, "QUOTE_FILE_NOT_KEPT")

    def test_a_missing_asset_is_not_kept(self):
        row = self.stored(self.quote(), fixtures()[0][1])
        kept_files.MEMORY.pop(row.storage_key)
        row = self.measure(row)
        self.assertEqual(row.state, State.NOT_KEPT)

    def test_a_storage_outage_goes_back_to_stored_for_the_next_try(self):
        row = self.stored(self.quote(), fixtures()[0][1])
        task = mod("students.tasks").measure_quote_file
        with patch.object(
            kept_files, "load", side_effect=kept_files.KeptFileUnavailable("x")
        ), patch.object(task, "retry", side_effect=RuntimeError("retry")):
            with self.assertRaises(RuntimeError):
                task.run(str(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.state, State.STORED)
        self.assertEqual(row.measure_attempts, 1)

    def test_a_cancelled_or_expired_quote_is_not_measured(self):
        for state in (UploadQuote.State.CANCELLED, UploadQuote.State.EXPIRED):
            quote = self.quote(state=state)
            row = self.stored(quote, fixtures()[0][1])
            with patch.object(kept_files, "load", side_effect=AssertionError):
                self.assertEqual(self.measure(row).state, State.STORED)

    def test_measuring_twice_is_harmless(self):
        row = self.measure(self.stored(self.quote(), fixtures()[0][1]))
        tokens = row.page_tokens
        row = self.measure(row)
        self.assertEqual(row.page_tokens, tokens)
        self.assertEqual(row.state, State.MEASURED)

    def test_a_teacher_at_zero_credits_is_measured_all_the_same(self):
        """Variant A (exact measure always), the current default."""
        from billing.models import CreditWallet

        poor, _, course, assignment, _ = classroom("qmpoor", 0)
        self.assertEqual(
            CreditWallet.objects.get(user=poor).total_remaining_credits(), 0
        )
        quote = UploadQuote.objects.create(
            teacher=poor,
            kind=UploadQuote.Kind.ASSIGNMENT_FILES,
            course=course,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        row = self.measure(self.stored(quote, fixtures()[0][1]))
        self.assertEqual(row.state, State.MEASURED)


class NothingBeforeConsent(Base):
    """Test 3: after add and measure there are no processing tasks, sessions,
    holds, jobs, ledger rows or AICallRecords; the provider is never reached."""

    def test_a_measure_touches_nothing_that_costs(self):
        from billing.models import AIJob, CreditWallet

        before = list(CreditWallet.objects.values_list("pk", "reserved_credits"))
        wallet = list(CreditWallet.objects.values())
        with patch(
            "ai_processor.services.AIProcessor._AIProcessor__ai_model",
            side_effect=AssertionError("the provider was reached"),
        ), patch("billing.holds.hold_for_call", side_effect=AssertionError("a hold")):
            row = self.measure(self.stored(self.quote(), fixtures()[2][1]))
        self.assertEqual(row.state, State.MEASURED)
        self.assertEqual(AICallRecord.objects.count(), 0)
        self.assertEqual(CreditLedger.objects.count(), 0)
        self.assertEqual(BackgroundProcessingTask.objects.count(), 0)
        self.assertEqual(BatchUploadSession.objects.count(), 0)
        self.assertEqual(AIJob.objects.count(), 0)
        self.assertEqual(list(CreditWallet.objects.values()), wallet)
        self.assertEqual(
            list(CreditWallet.objects.values_list("pk", "reserved_credits")), before
        )

    def test_the_quote_modules_call_no_metering_function(self):
        forbidden = {
            "execute_graded_task",
            "consume_credits",
            "hold_for_call",
            "reserve",
            "launch_processing_task",
            "create_processing_task",
            "extract_answer_image",
            "DoorHold",
        }
        for name in (
            "students/upload_quote_measure.py",
            "students/upload_quote_service.py",
            "students/upload_quotes.py",
            "students/upload_quote_views.py",
        ):
            tree = ast.parse((Path(settings.BASE_DIR) / name).read_text())
            used = {
                n.attr if isinstance(n, ast.Attribute) else n.id
                for n in ast.walk(tree)
                if isinstance(n, (ast.Attribute, ast.Name))
            }
            with self.subTest(module=name):
                self.assertFalse(forbidden & used, forbidden & used)

    def test_no_file_name_is_logged_by_a_measure(self):
        with self.assertLogs("students", "DEBUG") as logs:
            logging.getLogger("students.upload_quote_measure").info("start")
            self.measure(self.stored(self.quote(), b"%PDF-1.4 broken"))
        self.assertNotIn("secret", "\n".join(logs.output))


class OwnQueue(Base):
    def test_the_task_is_routed_to_its_own_queue(self):
        from AutoGrader.celery import app

        amqp: Any = app.amqp
        route = amqp.router.route({}, "students.tasks.measure_quote_file")
        self.assertEqual(route["queue"].name, "quote_measure")

    def test_the_worker_serves_both_queues(self):
        script = (Path(settings.BASE_DIR) / "scripts" / "start-worker.sh").read_text()
        self.assertIn("-Q celery,quote_measure", script)

    def test_enqueue_sends_each_file_to_that_queue(self):
        with patch("students.tasks.measure_quote_file.apply_async") as send:
            mod("students.upload_quote_measure").enqueue(["a", "b"])
        self.assertEqual(
            [c.kwargs["queue"] for c in send.call_args_list], ["quote_measure"] * 2
        )
        self.assertEqual(
            [c.kwargs["args"] for c in send.call_args_list], [["a"], ["b"]]
        )


class Semaphore(Base):
    def test_default_is_two_and_a_full_semaphore_retries_with_a_short_countdown(self):
        self.assertEqual(settings.UPLOAD_QUOTE_MEASURE_CONCURRENCY, 2)
        row = self.stored(self.quote(), fixtures()[0][1])
        task = mod("students.tasks").measure_quote_file
        with override_settings(UPLOAD_QUOTE_MEASURE_CONCURRENCY=1):
            measure = mod("students.upload_quote_measure")
            self.assertTrue(measure.acquire())
            try:
                with patch.object(
                    task, "retry", side_effect=RuntimeError("retry")
                ) as retry:
                    with self.assertRaises(RuntimeError):
                        task.run(str(row.pk))
                self.assertLessEqual(retry.call_args.kwargs["countdown"], 30)
            finally:
                measure.release()
        row.refresh_from_db()
        self.assertEqual(row.state, State.STORED)
        self.assertEqual(row.measure_attempts, 0)

    def test_two_slots_then_full(self):
        measure = mod("students.upload_quote_measure")
        self.assertTrue(measure.acquire())
        self.assertTrue(measure.acquire())
        self.assertFalse(measure.acquire())
        measure.release()
        self.assertTrue(measure.acquire())

    def test_the_slot_is_released_when_the_measure_fails(self):
        measure = mod("students.upload_quote_measure")
        row = self.stored(self.quote(), fixtures()[0][1])
        with patch.object(
            measure, "do_measure", side_effect=RuntimeError("boom")
        ), self.assertRaises(RuntimeError):
            mod("students.tasks").measure_quote_file.run(str(row.pk))
        self.assertTrue(measure.acquire())
        self.assertTrue(measure.acquire())


class StuckMeasures(Base):
    def old(self, row, minutes, field="created_at"):
        UploadQuoteFile.objects.filter(pk=row.pk).update(
            **{field: timezone.now() - timedelta(minutes=minutes)}
        )

    def run_sweep(self):
        with patch("students.tasks.measure_quote_file.apply_async") as send:
            result = mod("students.upload_quote_measure").requeue_stuck()
        return result, send

    def test_a_file_stored_and_unmeasured_for_over_ten_minutes_is_asked_for_again(self):
        row = self.stored(self.quote(), fixtures()[0][1])
        self.old(row, 11)
        _, send = self.run_sweep()
        self.assertEqual(send.call_count, 1)

    def test_a_fresh_file_is_left_alone(self):
        row = self.stored(self.quote(), fixtures()[0][1])
        self.old(row, 5)
        _, send = self.run_sweep()
        self.assertEqual(send.call_count, 0)

    def test_a_measure_that_started_and_died_is_asked_for_again(self):
        row = self.stored(self.quote(), fixtures()[0][1], state=State.MEASURING)
        UploadQuoteFile.objects.filter(pk=row.pk).update(
            measured_at=timezone.now() - timedelta(minutes=11)
        )
        _, send = self.run_sweep()
        self.assertEqual(send.call_count, 1)

    def test_after_three_attempts_the_file_is_left_out_with_a_counter(self):
        row = self.stored(self.quote(), fixtures()[0][1], measure_attempts=3)
        self.old(row, 11)
        with patch.object(metrics, "count") as count:
            _, send = self.run_sweep()
        row.refresh_from_db()
        self.assertEqual(send.call_count, 0)
        self.assertEqual(row.state, State.MEASURED)
        self.assertIsNone(row.readable)
        self.assertIn(
            "upload_quote_measure_gave_up", [c.args[0] for c in count.call_args_list]
        )

    def test_a_quote_still_measuring_after_thirty_minutes_raises_the_alarm(self):
        row = self.stored(self.quote(), fixtures()[0][1])
        self.old(row, 31)
        with self.assertLogs("students.upload_quote_measure", "ERROR"):
            self.run_sweep()

    def test_the_sweep_is_a_guarded_beat_task_every_five_minutes(self):
        task = mod("students.tasks").requeue_stuck_quote_measures
        self.assertIsNotNone(declared_lock(task))
        entry = settings.CELERY_BEAT_SCHEDULE["requeue-stuck-quote-measures"]
        self.assertEqual(entry["task"], "students.tasks.requeue_stuck_quote_measures")
        from celery.schedules import crontab

        self.assertEqual(entry["schedule"], crontab(minute="*/5"))


class GapCounter(Base):
    """Test 18 (BE-B-09): the gate's figure above the quoted one is counted."""

    def test_a_gate_figure_above_the_quoted_one_counts(self):
        quotes = mod("students.upload_quotes")
        with patch.object(metrics, "count") as count:
            quotes.note_gate_figure(quoted=1000, gate=1001)
        self.assertEqual(
            [c.args[0] for c in count.call_args_list], ["upload_quote_under"]
        )

    def test_equal_or_below_does_not(self):
        quotes = mod("students.upload_quotes")
        with patch.object(metrics, "count") as count:
            quotes.note_gate_figure(quoted=1000, gate=1000)
            quotes.note_gate_figure(quoted=1000, gate=900)
            quotes.note_gate_figure(quoted=None, gate=900)
        count.assert_not_called()
