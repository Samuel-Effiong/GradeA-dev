"""Upload quote F5 (owner Q20 A, 2026-11-23): a freed measure slot starts the
next waiting file at once.

Before, a measure task that found every slot taken retried on a 15-second
countdown, so files sat idle while slots were free (E2E run: 100 files 280 s,
575 slot-full retries). Now a task that finds no slot ends at once and the
run holding a slot takes the waiting files itself, oldest first; when it lets
its slot go it hands any file still waiting to a new task. Still at most
UPLOAD_QUOTE_MEASURE_CONCURRENCY at once, each file measured once, and
requeue_stuck stays the safety net.
"""

import threading
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from ai_processor.tests_page_tokens import fixtures
from assignments.services import AssignmentProcessingService
from billing.tests.test_holds_entry import classroom
from students.models import UploadQuoteFile
from students.tests_upload_quotes_measure import Base, State, kept_files, mod

prepare = AssignmentProcessingService.prepare_ai_content


def small():
    return fixtures()[0][1]


class Handover(Base):
    def task(self):
        return mod("students.tasks").measure_quote_file

    def measure_module(self):
        return mod("students.upload_quote_measure")

    def counting(self):
        return patch.object(
            AssignmentProcessingService,
            "prepare_ai_content",
            side_effect=prepare,
        )

    def three_waiting(self):
        quote = self.quote()
        return [self.stored(quote, small(), position=i) for i in range(3)]

    def test_a_file_that_finds_no_slot_ends_without_a_countdown(self):
        row = self.stored(self.quote(), small())
        task = self.task()
        measure = self.measure_module()
        with override_settings(UPLOAD_QUOTE_MEASURE_CONCURRENCY=1):
            self.assertTrue(measure.acquire())
            try:
                with patch.object(
                    task, "retry", side_effect=RuntimeError("retry")
                ) as retry, patch.object(measure, "enqueue"):
                    task.run(str(row.pk))
                retry.assert_not_called()
            finally:
                measure.release()
        row.refresh_from_db()
        self.assertEqual(row.state, State.STORED)
        self.assertEqual(row.measure_attempts, 0)

    def test_the_run_with_a_slot_measures_the_waiting_files_next(self):
        rows = self.three_waiting()
        with override_settings(UPLOAD_QUOTE_MEASURE_CONCURRENCY=1), patch.object(
            self.measure_module(), "enqueue"
        ):
            self.task().run(str(rows[0].pk))
        for row in rows:
            row.refresh_from_db()
            with self.subTest(position=row.position):
                self.assertEqual(row.state, State.MEASURED)

    def test_waiting_files_are_taken_oldest_first(self):
        now = timezone.now()
        later = self.quote()
        earlier = self.quote()
        b = self.stored(later, small(), created_at=now - timedelta(seconds=1))
        a = self.stored(earlier, small(), created_at=now - timedelta(seconds=5))
        c = self.stored(later, small(), position=1, created_at=now)
        with patch.object(self.measure_module(), "enqueue"):
            self.task().run(str(c.pk))
        order = list(
            UploadQuoteFile.objects.order_by("measured_at").values_list("pk", flat=True)
        )
        self.assertEqual(order, [a.pk, b.pk, c.pk])

    def test_each_file_is_measured_once(self):
        rows = self.three_waiting()
        with self.counting() as counted, patch.object(self.measure_module(), "enqueue"):
            for row in rows + rows:  # every file's own task, and again
                self.task().run(str(row.pk))
        self.assertEqual(counted.call_count, 3)
        self.assertEqual(
            sorted(UploadQuoteFile.objects.values_list("measure_attempts", flat=True)),
            [1, 1, 1],
        )

    def test_a_file_another_run_is_measuring_is_left_alone(self):
        row = self.stored(
            self.quote(),
            small(),
            state=State.MEASURING,
            measure_attempts=1,
            measured_at=timezone.now(),
        )
        with self.counting() as counted, patch.object(self.measure_module(), "enqueue"):
            self.task().run(str(row.pk))
        counted.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.state, State.MEASURING)
        self.assertEqual(row.measure_attempts, 1)

    def test_a_stale_measuring_file_is_taken_again(self):
        # A run that died mid-measure: requeue_stuck asks again after 10 min.
        row = self.stored(
            self.quote(),
            small(),
            state=State.MEASURING,
            measure_attempts=1,
            measured_at=timezone.now() - timedelta(minutes=11),
        )
        with patch.object(self.measure_module(), "enqueue"):
            self.task().run(str(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.state, State.MEASURED)
        self.assertEqual(row.measure_attempts, 2)

    def test_a_run_that_lets_its_slot_go_hands_a_waiting_file_on(self):
        rows = self.three_waiting()
        measure = self.measure_module()
        with patch.object(measure, "RUN_SECONDS", 0), patch.object(
            measure, "enqueue"
        ) as enqueue:
            self.task().run(str(rows[0].pk))
        rows[0].refresh_from_db()
        self.assertEqual(rows[0].state, State.MEASURED)
        enqueue.assert_called_once_with([rows[1].pk])
        # The slot is free for that task.
        self.assertTrue(measure.acquire())
        self.assertTrue(measure.acquire())

    def test_nothing_waiting_hands_nothing_on(self):
        row = self.stored(self.quote(), small())
        measure = self.measure_module()
        with patch.object(measure, "enqueue") as enqueue:
            self.task().run(str(row.pk))
        enqueue.assert_not_called()

    def test_a_storage_outage_backs_off_and_hands_nothing_on(self):
        rows = self.three_waiting()
        task = self.task()
        measure = self.measure_module()
        with patch.object(
            kept_files, "load", side_effect=kept_files.KeptFileUnavailable("down")
        ), patch.object(
            task, "retry", side_effect=RuntimeError("retry")
        ) as retry, patch.object(
            measure, "enqueue"
        ) as enqueue:
            with self.assertRaises(RuntimeError):
                task.run(str(rows[0].pk))
        self.assertEqual(
            retry.call_args.kwargs["countdown"], measure.STORAGE_RETRY_COUNTDOWN
        )
        enqueue.assert_not_called()
        self.assertEqual(
            set(UploadQuoteFile.objects.values_list("state", flat=True)),
            {State.STORED},
        )
        self.assertTrue(measure.acquire())
        self.assertTrue(measure.acquire())


class ConcurrentRuns(TransactionTestCase):
    """Several runs at once (worker replicas, gevent greenlets): each file is
    measured exactly once and never more than two at a time."""

    def test_four_runs_measure_eight_files_once_each_two_at_a_time(self):
        kept_files.MEMORY.clear()
        cache.clear()
        teacher, _, course, assignment, _ = classroom("qmc", 10**9)
        from students.models import UploadQuote

        quote = UploadQuote.objects.create(
            teacher=teacher,
            kind=UploadQuote.Kind.ANSWER_PAPERS,
            assignment=assignment,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        rows = []
        for i in range(8):
            put = kept_files.store(small())
            rows.append(
                UploadQuoteFile.objects.create(
                    quote=quote,
                    position=i,
                    state=State.STORED,
                    storage_key=put.storage_key,
                    original_name="secret.pdf",
                    content_type="application/pdf",
                    size_bytes=put.size_bytes,
                    sha256=put.sha256,
                )
            )
        lock = threading.Lock()
        running = {"now": 0, "most": 0, "calls": 0}

        def tracked(*args, **kwargs):
            with lock:
                running["now"] += 1
                running["calls"] += 1
                running["most"] = max(running["most"], running["now"])
            try:
                threading.Event().wait(0.05)
                return prepare(*args, **kwargs)
            finally:
                with lock:
                    running["now"] -= 1

        task = mod("students.tasks").measure_quote_file
        measure = mod("students.upload_quote_measure")
        errors = []

        def one(row_id):
            try:
                task.run(str(row_id))
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                connection.close()

        with patch.object(
            AssignmentProcessingService, "prepare_ai_content", side_effect=tracked
        ), patch.object(measure, "enqueue"):
            threads = [threading.Thread(target=one, args=(r.pk,)) for r in rows[:4]]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
            # A file a finished run left waiting: its own task takes it.
            for row in rows:
                one(row.pk)
        self.assertEqual(errors, [])
        self.assertLessEqual(running["most"], 2)
        self.assertEqual(running["calls"], 8)
        self.assertEqual(
            list(UploadQuoteFile.objects.values_list("state", flat=True).distinct()),
            [State.MEASURED],
        )
        self.assertEqual(
            set(UploadQuoteFile.objects.values_list("measure_attempts", flat=True)),
            {1},
        )
