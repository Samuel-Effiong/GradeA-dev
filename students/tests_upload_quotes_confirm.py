"""Upload quote, slice 5 (plan rev 3 section 5; owner Q2 = A): confirm.

POST /upload-quotes/{id}/confirm runs what fits and parks the rest (kept,
retryable); it never asks the teacher to agree to a changed balance and never
takes the door's discard-all 402. A second confirm returns the stored body
with 200.

Tests 2 (identity), 5 (double confirm), 6 (wallet moved), 7 (two quotes),
8 (nothing fits) and 16 (handover) of the plan.
"""

import threading
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from billing.models import CreditWallet
from billing.tests.test_holds_entry import ai_ready_teacher, classroom
from classrooms.models import StudentCourse
from students import kept_files as kept_files_module
from students import upload_quote_service as service
from students.models import (
    BackgroundProcessingTask,
    BatchUploadSession,
    KeptUpload,
    UploadQuote,
    UploadQuoteFile,
)

kept_files: Any = kept_files_module
State = UploadQuoteFile.State
CONFIRM = "upload-quote-confirm"


def make_quote(teacher, assignment, pages=(2, 3), states=None, kind="answer_papers"):
    quote = UploadQuote.objects.create(
        teacher=teacher,
        kind=kind,
        assignment=assignment if kind == "answer_papers" else None,
        course=assignment.course if kind == "assignment_files" else None,
        relaunch={"topic_id": None} if kind == "assignment_files" else {},
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    for position, count in enumerate(pages):
        state = (states or {}).get(position, State.MEASURED)
        stored = kept_files.store(f"bytes {position} {quote.pk}".encode())
        UploadQuoteFile.objects.create(
            quote=quote,
            position=position,
            state=state,
            storage_key=stored.storage_key if state != State.REFUSED else None,
            original_name=f"paper{position}.pdf",
            content_type="application/pdf",
            size_bytes=stored.size_bytes,
            sha256=stored.sha256 if state != State.REFUSED else "",
            pages=count,
            page_tokens=[765] * count if state == State.MEASURED else None,
            readable=True if state == State.MEASURED else False,
            reason_code="FILE_EMPTY" if state == State.REFUSED else "",
            measured_at=timezone.now(),
        )
    return quote


def figures(quote):
    quote = UploadQuote.objects.select_related("assignment").get(pk=quote.pk)
    rows = list(quote.files.order_by("position"))
    return [service.figure_of(quote, row) for row in rows]


class Base(APITestCase):
    def setUp(self):
        kept_files.MEMORY.clear()
        self.teacher, _, self.course, self.assignment, _ = classroom("qc", 10**9)
        self.other = ai_ready_teacher("qc-other@example.com", 10**9)

    def confirm(self, quote, user=None, body=None):
        self.client.force_authenticate(user=user or self.teacher)
        self.launch = MagicMock()
        self.launch.side_effect = lambda *a, **k: MagicMock(id="celery-id")
        with patch(
            "students.upload_quote_confirm.launch_processing_task", self.launch
        ), self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse(CONFIRM, args=[quote.pk]), body or {}, format="json"
            )


def data_of(response):
    body = response.json()
    return body.get("data", body)


class Confirm(Base):
    def test_every_file_runs_and_the_quote_is_confirmed(self):
        quote = make_quote(self.teacher, self.assignment)
        response = self.confirm(quote)
        self.assertEqual(response.status_code, 202, response.content)
        body = data_of(response)
        self.assertEqual(body["quote_id"], str(quote.pk))
        self.assertEqual([t["status"] for t in body["tasks"]], ["ACCEPTED"] * 2)
        session = BatchUploadSession.objects.get()
        self.assertEqual(body["session_id"], str(session.pk))
        self.assertEqual(session.total_files, 2)
        quote.refresh_from_db()
        self.assertEqual(quote.state, UploadQuote.State.CONFIRMED)
        self.assertEqual(quote.session_id, session.pk)
        self.assertIsNotNone(quote.confirmed_at)
        self.assertEqual(self.launch.call_count, 2)

    def test_the_items_are_in_position_order_and_carry_the_names(self):
        quote = make_quote(self.teacher, self.assignment, pages=(2, 3, 4))
        self.confirm(quote)
        items = BackgroundProcessingTask.objects.order_by("item_index")
        self.assertEqual([i.item_index for i in items], [1, 2, 3])
        self.assertEqual(
            [i.file_name for i in items], ["paper0.pdf", "paper1.pdf", "paper2.pdf"]
        )

    def test_the_kept_file_is_handed_over_not_copied(self):
        """Test 16."""
        quote = make_quote(self.teacher, self.assignment)
        keys = list(
            quote.files.order_by("position").values_list("storage_key", flat=True)
        )
        stored_before = len(kept_files.MEMORY)
        self.confirm(quote)
        kept = KeptUpload.objects.order_by("processing_task__item_index")
        self.assertEqual([k.storage_key for k in kept], keys)
        self.assertEqual(len(kept_files.MEMORY), stored_before)
        self.assertFalse(
            quote.files.exclude(storage_key__isnull=True).exists(),
            "the quote files no longer own the assets",
        )
        UploadQuoteFile.objects.filter(quote=quote).delete()
        self.assertEqual(set(kept_files.MEMORY), set(keys))

    def test_the_task_is_launched_from_the_kept_file(self):
        quote = make_quote(self.teacher, self.assignment)
        self.confirm(quote)
        first = KeptUpload.objects.get(processing_task__item_index=1)
        args = self.launch.call_args_list[0].args
        self.assertEqual(args[3], {"kept_upload_id": str(first.pk)})

    def test_assignment_files_relaunch_with_the_topic(self):
        quote = make_quote(self.teacher, self.assignment, kind="assignment_files")
        response = self.confirm(quote)
        self.assertEqual(response.status_code, 202, response.content)
        session = BatchUploadSession.objects.get()
        self.assertEqual(session.course_id, self.course.pk)
        self.assertEqual(
            KeptUpload.objects.get(processing_task__item_index=1).relaunch,
            {"topic_id": None},
        )

    def test_would_park_in_the_body_is_ignored(self):
        quote = make_quote(self.teacher, self.assignment)
        response = self.confirm(quote, body={"would_park": [0, 1]})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            [t["status"] for t in data_of(response)["tasks"]], ["ACCEPTED"] * 2
        )

    def test_one_audit_event_per_confirm(self):
        from audit.models import AuditEvent

        quote = make_quote(self.teacher, self.assignment)
        self.confirm(quote)
        events = AuditEvent.objects.filter(action="SUBMISSION_UPLOAD")
        self.assertEqual(events.count(), 1)
        self.assertNotIn("paper0", str([e.metadata for e in events]))


class Replay(Base):
    def test_a_second_confirm_is_the_same_body_with_200(self):
        quote = make_quote(self.teacher, self.assignment)
        first = self.confirm(quote)
        second = self.confirm(quote)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(data_of(second), data_of(first))
        self.assertEqual(BatchUploadSession.objects.count(), 1)
        self.assertEqual(self.launch.call_count, 0)

    def test_not_ready_is_409(self):
        quote = make_quote(self.teacher, self.assignment, states={1: State.MEASURING})
        response = self.confirm(quote)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["field_errors"]["reason_code"], "QUOTE_NOT_READY"
        )
        self.assertFalse(BatchUploadSession.objects.exists())

    def test_cancelled_is_closed(self):
        quote = make_quote(self.teacher, self.assignment)
        UploadQuote.objects.update(state=UploadQuote.State.CANCELLED)
        response = self.confirm(quote)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["field_errors"]["reason_code"], "QUOTE_CLOSED"
        )

    @override_settings(UPLOAD_QUOTE_LIFETIME_MINUTES=4)
    def test_past_expiry_is_410_with_the_clock_in_the_params(self):
        quote = make_quote(self.teacher, self.assignment)
        UploadQuote.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.confirm(quote)
        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            response.json()["error"]["field_errors"]["params"]["lifetime_minutes"], 4
        )
        self.assertIn("more than 4 minutes", response.json()["message"])
        self.assertFalse(BatchUploadSession.objects.exists())

    def test_a_foreign_quote_is_the_house_404(self):
        quote = make_quote(self.teacher, self.assignment)
        self.assertEqual(self.confirm(quote, user=self.other).status_code, 404)

    def test_a_student_is_403(self):
        quote = make_quote(self.teacher, self.assignment)
        student = StudentCourse.objects.get(course=self.course).student
        self.assertEqual(self.confirm(quote, user=student).status_code, 403)


class WhatFits(Base):
    """Owner Q2 = A: run what fits, park the rest, kept and retryable."""

    def spendable(self, amount):
        return patch("students.upload_quote_confirm.door_balance", return_value=amount)

    def test_the_file_that_does_not_fit_parks_and_the_one_that_fits_runs(self):
        quote = make_quote(self.teacher, self.assignment, pages=(2, 6))
        small, large = figures(quote)
        with self.spendable(small):
            response = self.confirm(quote)
        self.assertEqual(response.status_code, 202, response.content)
        statuses = [t["status"] for t in data_of(response)["tasks"]]
        self.assertEqual(statuses, ["ACCEPTED", "PARKED"])
        self.assertEqual(self.launch.call_count, 1)
        parked = BackgroundProcessingTask.objects.get(item_index=2)
        self.assertEqual(parked.status, "FAILURE")
        self.assertTrue(KeptUpload.objects.filter(processing_task=parked).exists())
        self.assertIsNone(KeptUpload.objects.get(processing_task=parked).purged_at)
        self.assertGreater(large, small)

    def test_a_smaller_later_file_may_still_fit(self):
        quote = make_quote(self.teacher, self.assignment, pages=(6, 2))
        large, small = figures(quote)
        with self.spendable(small):
            response = self.confirm(quote)
        statuses = [t["status"] for t in data_of(response)["tasks"]]
        self.assertEqual(statuses, ["PARKED", "ACCEPTED"])

    def test_nothing_fits_parks_every_file_and_answers_202(self):
        """Test 8: never the discard-all 402; the inline fallback is never
        reached."""
        quote = make_quote(self.teacher, self.assignment)
        with self.spendable(0), patch(
            "students.kept_uploads.DoorFile", side_effect=AssertionError
        ):
            response = self.confirm(quote)
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(
            [t["status"] for t in data_of(response)["tasks"]], ["PARKED"] * 2
        )
        self.assertEqual(self.launch.call_count, 0)
        self.assertEqual(KeptUpload.objects.filter(purged_at__isnull=True).count(), 2)
        session = BatchUploadSession.objects.get()
        self.assertIsNone(session.credits_exhausted_at)
        quote.refresh_from_db()
        self.assertEqual(quote.state, UploadQuote.State.CONFIRMED)

    def test_a_refused_and_a_not_kept_file_are_their_own_items(self):
        quote = make_quote(
            self.teacher,
            self.assignment,
            pages=(2, 2, 2),
            states={1: State.REFUSED, 2: State.NOT_KEPT},
        )
        response = self.confirm(quote)
        statuses = [t["status"] for t in data_of(response)["tasks"]]
        self.assertEqual(statuses, ["ACCEPTED", "REFUSED", "REFUSED"])
        codes = [t["reason_code"] for t in data_of(response)["tasks"]]
        self.assertEqual(codes[1:], ["FILE_EMPTY", "QUOTE_FILE_NOT_KEPT"])
        self.assertEqual(self.launch.call_count, 1)

    def test_an_already_uploaded_file_holds_nothing_and_still_runs(self):
        quote = make_quote(
            self.teacher, self.assignment, pages=(6,), kind="assignment_files"
        )
        from assignments.models import AssignmentUploadFingerprint

        row = quote.files.get()
        AssignmentUploadFingerprint.objects.create(
            course=self.course,
            sha256=row.sha256,
            status=AssignmentUploadFingerprint.Status.COMPLETED,
            claimed_at=timezone.now(),
        )
        with self.spendable(0):
            response = self.confirm(quote)
        self.assertEqual(
            [t["status"] for t in data_of(response)["tasks"]], ["ACCEPTED"]
        )


@override_settings(BILLING_HOLDS_ENABLED=True)
class Holds(Base):
    def test_each_item_holds_its_quoted_figure(self):
        """Test 2: the hold equals the stored per-file figure, and the
        quote's total equals their sum."""
        quote = make_quote(self.teacher, self.assignment, pages=(2, 3, 5))
        expected = figures(quote)
        response = self.confirm(quote)
        self.assertEqual(response.status_code, 202, response.content)
        held = [
            i.hold_credits
            for i in BackgroundProcessingTask.objects.order_by("item_index")
        ]
        self.assertEqual(held, expected)
        wallet = CreditWallet.objects.get(user=self.teacher)
        self.assertEqual(wallet.reserved_credits, sum(expected))

    def test_the_figure_is_the_live_one_after_the_roster_changes(self):
        """P2: confirm never holds a figure frozen at measure time."""
        quote = make_quote(self.teacher, self.assignment, pages=(2,))
        before = figures(quote)
        from users.models import CustomUser, UserTypes

        for index in range(5):
            student = CustomUser.objects.create_user(
                email=f"extra{index}@example.com",
                password="password123",  # pragma: allowlist secret
                user_type=UserTypes.STUDENT,
                first_name="E",
                last_name=str(index),
                is_active=True,
            )
            StudentCourse.objects.create(
                student=student, course=self.course, enrollment_status="ENROLLED"
            )
        after = figures(quote)
        self.assertNotEqual(before, after)
        self.confirm(quote)
        self.assertEqual(BackgroundProcessingTask.objects.get().hold_credits, after[0])

    def test_two_quotes_against_a_tight_balance_the_loser_parks(self):
        """Test 7 (the S11a shape): reserved never exceeds the balance."""
        first = make_quote(self.teacher, self.assignment, pages=(4,))
        second = make_quote(self.teacher, self.assignment, pages=(4,))
        need = figures(first)[0]
        wallet = CreditWallet.objects.get(user=self.teacher)
        total = wallet.total_remaining_credits()
        CreditWallet.objects.filter(pk=wallet.pk).update(reserved_credits=total - need)
        with patch("students.upload_quote_confirm.door_balance", return_value=10**9):
            one = self.confirm(first)
            two = self.confirm(second)
        wallet.refresh_from_db()
        self.assertLessEqual(wallet.reserved_credits, total)
        self.assertEqual(data_of(one)["tasks"][0]["status"], "ACCEPTED")
        self.assertEqual(data_of(two)["tasks"][0]["status"], "PARKED")
        parked = BackgroundProcessingTask.objects.get(
            batch_session__pk=data_of(two)["session_id"]
        )
        self.assertTrue(KeptUpload.objects.filter(processing_task=parked).exists())
        self.assertIsNone(
            BatchUploadSession.objects.get(
                pk=data_of(two)["session_id"]
            ).credits_exhausted_at
        )


class HoldsOff(Base):
    def test_with_holds_off_confirm_launches_without_a_hold(self):
        quote = make_quote(self.teacher, self.assignment)
        response = self.confirm(quote)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            {i.hold_credits for i in BackgroundProcessingTask.objects.all()}, {0}
        )


class DoubleConfirm(TransactionTestCase):
    """Test 5, two connections: one session, one set of holds, one 202 and
    one 200 with the same body."""

    def setUp(self):
        kept_files.MEMORY.clear()
        self.teacher, _, self.course, self.assignment, _ = classroom("qd", 10**9)

    @override_settings(BILLING_HOLDS_ENABLED=True)
    def test_two_threads_make_one_session(self):
        quote = make_quote(self.teacher, self.assignment)
        results: list = []
        barrier = threading.Barrier(2)

        def go():
            try:
                client = APIClient()
                client.force_authenticate(user=self.teacher)
                barrier.wait(timeout=10)
                with patch(
                    "students.upload_quote_confirm.launch_processing_task",
                    side_effect=lambda *a, **k: MagicMock(id="c"),
                ):
                    results.append(
                        client.post(
                            reverse(CONFIRM, args=[quote.pk]), {}, format="json"
                        )
                    )
            finally:
                connection.close()

        threads = [threading.Thread(target=go) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(sorted(r.status_code for r in results), [200, 202])
        bodies = [data_of(r) for r in results]
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(BatchUploadSession.objects.count(), 1)
        self.assertEqual(BackgroundProcessingTask.objects.count(), 2)
        self.assertEqual(KeptUpload.objects.count(), 2)
