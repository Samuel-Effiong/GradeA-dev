"""Upload quote, slice 3 (plan rev 3 section 3, with the owner's change of
2026-11-20): the routes.

POST /submissions-credit-estimate/ is multipart and does create + files in one request: the
frontend sends every file at once and does no checks of its own, so the server
does them all (type, empty, 50 MB each, 500 MB the request) and one bad file
never fails the others. GET one, GET the open list, DELETE (cancel). Confirm is
slice 5.

Tests 3 (nothing before consent), 9 (adds), 10 (idempotent create), 12
(limits), 13 (tenancy), 14 (privacy) and 15 (cancel) of the plan.
"""

import hashlib
import importlib
import io
import re
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import fitz
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from assignments.upload_prompts import BATCH_UPLOAD_PROMPT
from billing.models import AIJob, CreditLedger
from billing.tests.test_holds_entry import ai_ready_teacher, classroom
from students import kept_files as kept_files_module
from students.models import (
    BackgroundProcessingTask,
    BatchUploadSession,
    KeptUpload,
    UploadQuote,
    UploadQuoteFile,
)
from users.models import CustomUser, UserTypes

kept_files: Any = kept_files_module
on = override_settings(UPLOAD_QUOTES_ENABLED=True)

LIST_URL = "upload-quotes-collection"
DETAIL_URL = "upload-quote-detail"


def png(name="a.png", size=(64, 64), color="white"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def pdf(name="a.pdf", pages=2):
    document = fitz.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((50, 80), f"page {index} of {name}", fontsize=12)
    data = document.tobytes()
    document.close()
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def text_file(name="n.txt", content=b"hello"):
    return SimpleUploadedFile(name, content, content_type="text/plain")


def sha(upload):
    return hashlib.sha256(upload.read()).hexdigest()


def run_the_store():
    """The store task, run eagerly when the request commits (a Celery task on
    a worker in production): the reply comes first, then the store, with the
    task's own retries (no countdown when eager)."""
    from students.tasks import store_quote_files

    return patch(
        "students.upload_quote_service.enqueue_store",
        side_effect=lambda quote_id, ids: store_quote_files.apply(
            args=(str(quote_id), [str(i) for i in ids])
        ),
    )


def states():
    """Every file's state now, in position order (after the store ran)."""
    return list(
        UploadQuoteFile.objects.order_by("position").values_list("state", flat=True)
    )


def data_of(response):
    body = response.json()
    return body.get("data", body)


@on
class Base(APITestCase):
    def setUp(self):
        kept_files.MEMORY.clear()
        self.teacher, self.student, self.course, self.assignment, _ = classroom(
            "qr", 10**9
        )
        self.other = ai_ready_teacher("qr-other@example.com", 10**9)

    def create(self, files, *, user=None, kind="answer_papers", key=None, **extra):
        self.client.force_authenticate(user=user or self.teacher)
        body: dict[str, Any] = {"kind": kind, "files": files}
        if kind == "answer_papers":
            body["assignment_id"] = str(self.assignment.pk)
        else:
            body["course_id"] = str(self.course.pk)
        body.update(extra)
        headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
        with patch(
            "students.upload_quote_service.enqueue_measures"
        ) as self.enqueued, run_the_store(), self.captureOnCommitCallbacks(
            execute=True
        ):
            return self.client.post(
                reverse(LIST_URL), body, format="multipart", **headers
            )

    def get(self, quote_id, user=None):
        self.client.force_authenticate(user=user or self.teacher)
        return self.client.get(reverse(DETAIL_URL, args=[quote_id]))


class Create(Base):
    def test_answers_create_returns_the_quote_with_every_file(self):
        response = self.create([pdf("a.pdf"), png("b.png")])
        self.assertEqual(response.status_code, 201, response.content)
        quote = data_of(response)
        self.assertEqual(quote["kind"], "answer_papers")
        self.assertEqual(quote["state"], "OPEN")
        self.assertEqual(quote["total"], 2)
        self.assertEqual(quote["measured"], 0)
        self.assertFalse(quote["ready"])
        self.assertIsNone(quote["estimate"])
        self.assertIsNone(quote["session_id"])
        self.assertEqual([f["position"] for f in quote["files"]], [0, 1])
        # The reply does not wait for the storage; the task stores after it.
        self.assertEqual({f["state"] for f in quote["files"]}, {"STORING"})
        self.assertEqual(states(), ["STORED", "STORED"])
        self.assertTrue(UploadQuote.objects.get().assignment_id == self.assignment.pk)

    def test_assignment_files_create_with_a_topic(self):
        from classrooms.models import Topic

        topic = Topic.objects.create(name="T", course=self.course)
        response = self.create([pdf()], kind="assignment_files", topic_id=str(topic.pk))
        self.assertEqual(response.status_code, 201, response.content)
        quote = UploadQuote.objects.get()
        self.assertEqual(quote.course_id, self.course.pk)
        self.assertEqual(quote.relaunch, {"topic_id": str(topic.pk)})

    def test_a_topic_of_another_course_is_the_house_404(self):
        from classrooms.models import Course, Session, Topic

        session = Session.objects.create(name="O", teacher=self.other)
        course = Course.objects.create(name="O", teacher=self.other, session=session)
        topic = Topic.objects.create(name="T", course=course)
        response = self.create([pdf()], kind="assignment_files", topic_id=str(topic.pk))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(UploadQuote.objects.exists())

    def test_the_files_are_kept_and_the_measure_is_asked_for_each_after_commit(self):
        response = self.create([pdf("a.pdf"), pdf("b.pdf", pages=3)])
        ids = {f["id"] for f in data_of(response)["files"]}
        self.assertEqual(len(kept_files.MEMORY), 2)
        (asked,) = self.enqueued.call_args.args
        self.assertEqual({str(i) for i in asked}, ids)

    def test_every_check_is_the_servers_and_one_bad_file_fails_nothing_else(self):
        too_big = pdf("big.pdf")
        with override_settings(MAX_UPLOAD_FILE_SIZE_BYTES=too_big.size - 1):
            response = self.create(
                [
                    text_file(),
                    SimpleUploadedFile("e.pdf", b"", "application/pdf"),
                    too_big,
                ]
            )
        self.assertEqual(response.status_code, 201, response.content)
        files = data_of(response)["files"]
        self.assertEqual(
            [(f["state"], f["reason_code"]) for f in files],
            [
                ("REFUSED", "FILE_TYPE_UNSUPPORTED"),
                ("REFUSED", "FILE_EMPTY"),
                ("REFUSED", "FILE_TOO_LARGE"),
            ],
        )
        self.assertEqual(kept_files.MEMORY, {})
        self.assertEqual(UploadQuoteFile.objects.count(), 3)

    def test_a_refused_file_among_good_ones(self):
        response = self.create([pdf("a.pdf"), text_file(), png("c.png")])
        replied = [f["state"] for f in data_of(response)["files"]]
        self.assertEqual(replied, ["STORING", "REFUSED", "STORING"])
        self.assertEqual(states(), ["STORED", "REFUSED", "STORED"])
        self.assertEqual(len(kept_files.MEMORY), 2)

    def test_no_files_is_a_400(self):
        response = self.create([])
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(UploadQuote.objects.exists())

    def test_bad_kind_and_missing_ids_are_400(self):
        self.client.force_authenticate(user=self.teacher)
        url = reverse(LIST_URL)
        for body in (
            {"kind": "nope", "files": [pdf()]},
            {"kind": "answer_papers", "files": [pdf()]},
            {"kind": "assignment_files", "files": [pdf()]},
            {"kind": "answer_papers", "assignment_id": "not-a-uuid", "files": [pdf()]},
        ):
            with self.subTest(body=list(body)):
                self.assertEqual(
                    self.client.post(url, body, format="multipart").status_code, 400
                )

    def test_more_than_the_file_limit_is_a_400_and_stores_nothing(self):
        with override_settings(UPLOAD_QUOTE_MAX_FILES=2):
            response = self.create(
                [
                    png("a.png", color="red"),
                    png("b.png", color="blue"),
                    png("c.png", color="green"),
                ]
            )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn(
            "You can upload up to 2 files at a time. Remove some files, then try again.",
            response.content.decode(),
        )
        self.assertEqual(kept_files.MEMORY, {})
        self.assertFalse(UploadQuote.objects.exists())

    def test_the_same_bytes_twice_are_one_stored_file_and_a_duplicate_entry(self):
        one = pdf("a.pdf")
        two = SimpleUploadedFile("copy.pdf", one.read(), "application/pdf")
        one.seek(0)
        response = self.create([one, two])
        first, second = data_of(response)["files"]
        self.assertEqual(second["duplicate_of"], first["id"])
        self.assertEqual(second["state"], first["state"])
        self.assertEqual(
            UploadQuoteFile.objects.filter(storage_key__isnull=False).count(), 1
        )
        self.assertEqual(len(kept_files.MEMORY), 1)

    def test_a_file_the_store_cannot_keep_is_its_own_entry(self):
        real = kept_files.store
        calls = []

        def flaky(content, key=None):
            calls.append(key)
            # By content, not call order: the stores run in parallel.
            if content.startswith(b"%PDF"):
                raise kept_files.KeptFileUnavailable("x")
            return real(content, key=key)

        with patch.object(kept_files, "store", side_effect=flaky):
            response = self.create([pdf("a.pdf"), png("b.png")])
        self.assertEqual(response.status_code, 201, response.content)
        # The verdict arrives after the reply, on a poll.
        first, second = data_of(self.get(data_of(response)["id"]))["files"]
        self.assertEqual(first["state"], "NOT_KEPT")
        self.assertEqual(first["reason_code"], "QUOTE_FILE_NOT_KEPT")
        self.assertEqual(second["state"], "STORED")

    def test_cloudinary_refusing_a_file_for_size_fails_only_that_file(self):
        """The Free plan limits a raw file to 10 MB: Cloudinary answers a size
        error for that file, which ends QUOTE_FILE_NOT_KEPT; nothing else
        breaks."""
        import cloudinary.exceptions

        def upload(stream, **kwargs):
            if stream.getbuffer().nbytes > 5000:
                raise cloudinary.exceptions.BadRequest(
                    "File size too large. Got 11000000. Maximum is 10485760."
                )
            return {}

        with patch.object(kept_files, "_memory", return_value=False), patch(
            "students.kept_files.cloudinary.uploader.upload", side_effect=upload
        ):
            big_file = SimpleUploadedFile(
                "big.pdf", b"%PDF-1.4 " + b"x" * 6000, content_type="application/pdf"
            )
            response = self.create([big_file, png("b.png")])
        self.assertEqual(response.status_code, 201, response.content)
        big, small = data_of(self.get(data_of(response)["id"]))["files"]
        self.assertEqual(big["state"], "NOT_KEPT")
        self.assertEqual(big["reason_code"], "QUOTE_FILE_NOT_KEPT")
        self.assertEqual(small["state"], "STORED")

    def test_the_row_is_written_before_the_bytes(self):
        seen = []
        real = kept_files.store

        def spy(content, key=None):
            seen.append(UploadQuoteFile.objects.filter(storage_key=key).exists())
            return real(content, key=key)

        with patch.object(kept_files, "store", side_effect=spy):
            self.create([pdf()])
        self.assertEqual(seen, [True])

    def test_a_file_that_dies_between_row_and_bytes_leaves_a_row_the_purge_clears(self):
        # The store task dies before it settles anything (its worker is
        # killed): the reply was already sent, the row stays STORING.
        with patch(
            "students.upload_quote_service.store_staged_within",
            side_effect=RuntimeError("killed"),
        ):
            self.assertEqual(self.create([pdf()]).status_code, 201)
        row = UploadQuoteFile.objects.get()
        self.assertEqual(row.state, UploadQuoteFile.State.STORING)
        quotes: Any = importlib.import_module("students.upload_quotes")
        # Never ready, so only the unready ceiling can clear it.
        long_ago = timezone.now() - timedelta(
            minutes=settings.UPLOAD_QUOTE_UNREADY_CEILING_MINUTES + 1
        )
        UploadQuote.objects.update(created_at=long_ago)
        UploadQuoteFile.objects.update(created_at=long_ago)
        with self.captureOnCommitCallbacks(execute=True):
            result = quotes.purge_due()
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)
        self.assertEqual(result["failed"], 0)

    def test_nothing_is_processed_by_a_create(self):
        with patch("billing.holds.hold_for_call", side_effect=AssertionError):
            self.create([pdf(), pdf("b.pdf", pages=3)])
        wallet_before = CreditLedger.objects.count()
        self.assertEqual(BackgroundProcessingTask.objects.count(), 0)
        self.assertEqual(BatchUploadSession.objects.count(), 0)
        self.assertEqual(KeptUpload.objects.count(), 0)
        self.assertEqual(AIJob.objects.count(), 0)
        self.assertEqual(wallet_before, CreditLedger.objects.count())


class Idempotency(Base):
    def test_the_same_key_returns_the_same_quote_and_stores_nothing_again(self):
        first = self.create([pdf()], key="11111111-1111-1111-1111-111111111111")
        stored = len(kept_files.MEMORY)
        second = self.create([pdf()], key="11111111-1111-1111-1111-111111111111")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(data_of(first)["id"], data_of(second)["id"])
        self.assertEqual(len(kept_files.MEMORY), stored)
        self.assertEqual(UploadQuote.objects.count(), 1)
        self.assertEqual(UploadQuoteFile.objects.count(), 1)

    def test_without_a_key_a_retry_is_a_new_quote(self):
        self.create([pdf()])
        self.create([pdf()])
        self.assertEqual(UploadQuote.objects.count(), 2)

    def test_another_teachers_same_key_is_their_own_quote(self):
        self.create([pdf()], key="k-1")
        response = self.create(
            [pdf()],
            user=self.other,
            key="k-1",
        )
        self.assertEqual(response.status_code, 404)  # not their assignment
        self.assertEqual(UploadQuote.objects.count(), 1)


class Limits(Base):
    def test_a_request_over_the_cap_is_refused_before_the_body_is_read(self):
        self.create([pdf()])
        with override_settings(UPLOAD_REQUEST_MAX_BYTES=10), patch(
            "django.http.multipartparser.MultiPartParser.parse",
            side_effect=AssertionError("the body was parsed"),
        ):
            refused = self.create([pdf("big.pdf")])
        self.assertEqual(refused.status_code, 413, refused.content)
        coded = refused.json()["error"]["field_errors"]
        self.assertEqual(coded["reason_code"], "FILE_TOO_LARGE")
        self.assertEqual(coded["params"]["dimension"], "bytes")
        self.assertEqual(coded["params"]["limit"], 10)
        self.assertNotIn("file_name", coded["params"])
        self.assertEqual(UploadQuote.objects.count(), 1)  # only the first

    def test_a_fourth_open_quote_is_too_many_open(self):
        for n in range(3):
            self.assertEqual(
                self.create([png(f"{n}.png", color=(n, 0, 0))]).status_code, 201
            )
        response = self.create([png("x.png", color="blue")])
        self.assertEqual(response.status_code, 429, response.content)
        self.assertEqual(
            response.json()["error"]["field_errors"]["reason_code"],
            "QUOTE_TOO_MANY_OPEN",
        )
        self.assertEqual(UploadQuote.objects.count(), 3)

    def test_closed_quotes_do_not_count_as_open(self):
        for n in range(3):
            self.create([png(f"{n}.png", color=(n, 0, 0))])
        UploadQuote.objects.update(state=UploadQuote.State.CANCELLED)
        self.assertEqual(self.create([png("x.png")]).status_code, 201)

    def test_a_teacher_over_the_open_bytes_gets_storage_full(self):
        one = pdf("a.pdf")
        with override_settings(UPLOAD_QUOTE_OPEN_BYTES=one.size + 10):
            self.assertEqual(self.create([one]).status_code, 201)
            response = self.create([pdf("b.pdf", pages=4)])
        self.assertEqual(response.status_code, 413, response.content)
        self.assertEqual(
            response.json()["error"]["field_errors"]["reason_code"],
            "QUOTE_STORAGE_FULL",
        )

    def test_the_global_ceiling_refuses_new_files(self):
        self.create([pdf("a.pdf")])
        with override_settings(UPLOAD_QUOTE_GLOBAL_OPEN_BYTES_MAX=1):
            response = self.create([pdf("b.pdf", pages=5)], user=self.teacher)
        self.assertEqual(response.status_code, 413, response.content)
        self.assertEqual(
            response.json()["error"]["field_errors"]["reason_code"],
            "QUOTE_STORAGE_FULL",
        )

    def test_the_global_alert_counts_a_metric(self):
        with override_settings(UPLOAD_QUOTE_GLOBAL_OPEN_BYTES_ALERT=1), patch(
            "students.upload_quote_service.audit_metrics.count"
        ) as count:
            self.create([pdf("a.pdf")])
        self.assertIn(
            "upload_quote_global_alert", [c.args[0] for c in count.call_args_list]
        )

    def test_the_throttles_are_separate_scopes(self):
        from django.conf import settings as django_settings

        rates = django_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertEqual(rates["upload_quote_create"], "10/min")
        self.assertEqual(rates["upload_quote_read"], "120/min")
        views: Any = importlib.import_module("students.upload_quote_views")
        self.assertNotEqual(
            views.UploadQuoteCreateThrottle.scope, views.UploadQuoteReadThrottle.scope
        )

    def test_create_is_throttled_but_reads_have_their_own_budget(self):
        response = self.create([pdf()])
        quote_id = data_of(response)["id"]
        cache: Any = importlib.import_module("django.core.cache").cache
        cache.clear()
        with patch(
            "students.upload_quote_views.UploadQuoteCreateThrottle.get_rate",
            return_value="1/min",
        ):
            statuses = [
                self.create([png(f"{n}.png", color=(0, n, 0))]).status_code
                for n in range(2)
            ]
            self.assertEqual(statuses, [201, 429])
            self.assertEqual(self.get(quote_id).status_code, 200)
        cache.clear()


class Read(Base):
    def measured_quote(self, files=(None,)):
        quote = UploadQuote.objects.create(
            teacher=self.teacher,
            kind=UploadQuote.Kind.ANSWER_PAPERS,
            assignment=self.assignment,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        tokens: Any = importlib.import_module("ai_processor.call_estimates")
        for position, pages in enumerate(files or (2,)):
            stored = kept_files.store(f"bytes {position}".encode())
            UploadQuoteFile.objects.create(
                quote=quote,
                position=position,
                state=UploadQuoteFile.State.MEASURED,
                storage_key=stored.storage_key,
                original_name="secret-name.pdf",
                content_type="application/pdf",
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                pages=pages or 2,
                page_tokens=[765] * (pages or 2),
                readable=True,
                measured_at=timezone.now(),
            )
        del tokens
        return quote

    def test_get_shows_the_estimate_in_the_seven_keys_once_ready(self):
        quote = self.measured_quote((2, 4))
        body = data_of(self.get(quote.pk))
        self.assertTrue(body["ready"])
        self.assertEqual((body["measured"], body["total"]), (2, 2))
        self.assertEqual(
            set(body["estimate"]),
            {
                "p50",
                "p90",
                "verdict",
                "spendable",
                "projected_remaining_p50",
                "projected_remaining_p90",
                "n",
            },
        )
        self.assertEqual(body["estimate"]["n"], 2)
        self.assertEqual(body["would_park"], [])

    def test_each_file_carries_its_own_estimated_cost_pages_and_calls(self):
        body = data_of(self.get(self.measured_quote((2, 7)).pk))
        small, large = body["files"]
        self.assertEqual((small["pages"], small["calls"]), (2, 1))
        self.assertEqual((large["pages"], large["calls"]), (7, 3))
        self.assertGreater(large["estimated_cost"], small["estimated_cost"])
        self.assertEqual(
            body["estimate"]["p90"],
            -(-(self.raw(2) + self.raw(7)) // self.factor()),
        )

    def raw(self, pages):
        c: Any = importlib.import_module("ai_processor.call_estimates")
        return c.estimate_from_page_tokens(
            c.EXTRACT_ANSWER,
            [765] * pages,
            [{"type": "text", "text": BATCH_UPLOAD_PROMPT}],
            self.assignment,
        )

    def factor(self):
        from billing.models import CONVERSION_FACTOR

        return CONVERSION_FACTOR

    def test_the_figure_follows_the_live_balance_and_names_the_files_that_would_park(
        self,
    ):
        quote = self.measured_quote((2, 2))
        from billing.models import CreditWallet

        wallet = CreditWallet.objects.get(user=self.teacher)
        # Enough for one file only.
        self.assertTrue(wallet.total_remaining_credits() > 0)
        with patch(
            "students.upload_quote_service.door_balance", return_value=self.raw(2) + 5
        ):
            body = data_of(self.get(quote.pk))
        self.assertEqual(body["would_park"], [1])

    def test_an_unmeasured_quote_has_no_estimate(self):
        quote = self.measured_quote()
        UploadQuoteFile.objects.update(
            state=UploadQuoteFile.State.STORED,
            page_tokens=None,
            pages=None,
            readable=None,
        )
        body = data_of(self.get(quote.pk))
        self.assertFalse(body["ready"])
        self.assertIsNone(body["estimate"])
        self.assertEqual(body["measured"], 0)

    def test_a_refused_file_is_ready_and_left_out_of_the_estimate(self):
        quote = self.measured_quote((2,))
        UploadQuoteFile.objects.create(
            quote=quote,
            position=1,
            state=UploadQuoteFile.State.REFUSED,
            reason_code="FILE_TYPE_UNSUPPORTED",
            original_name="n.txt",
        )
        body = data_of(self.get(quote.pk))
        self.assertTrue(body["ready"])
        self.assertEqual(body["estimate"]["n"], 1)

    def test_an_assignment_file_the_course_already_has_counts_zero(self):
        from assignments.models import AssignmentUploadFingerprint

        quote = UploadQuote.objects.create(
            teacher=self.teacher,
            kind=UploadQuote.Kind.ASSIGNMENT_FILES,
            course=self.course,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        stored = kept_files.store(b"same bytes")
        UploadQuoteFile.objects.create(
            quote=quote,
            position=0,
            state=UploadQuoteFile.State.MEASURED,
            storage_key=stored.storage_key,
            original_name="a.pdf",
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            pages=2,
            page_tokens=[765, 765],
            readable=True,
        )
        AssignmentUploadFingerprint.objects.create(
            course=self.course,
            claimed_at=timezone.now(),
            sha256=stored.sha256,
            status=AssignmentUploadFingerprint.Status.COMPLETED,
            assignment=self.assignment,
        )
        body = data_of(self.get(quote.pk))
        self.assertTrue(body["files"][0]["already_uploaded"])
        self.assertEqual(body["estimate"]["p90"], 0)

    def test_the_text_part_is_live(self):
        quote = self.measured_quote((2,))
        before = data_of(self.get(quote.pk))["estimate"]["p90"]
        self.assignment.questions = self.assignment.questions * 60
        self.assignment.save()
        after = data_of(self.get(quote.pk))["estimate"]["p90"]
        self.assertGreater(after, before)

    def test_no_file_name_in_the_reply_or_in_logs(self):
        quote = self.measured_quote((2,))
        with patch("logging.Logger.info") as info:
            response = self.get(quote.pk)
        self.assertNotIn(b"secret-name", response.content)
        for call in info.call_args_list:
            self.assertNotIn("secret-name", str(call))

    def test_get_of_an_expired_quote_shows_its_state(self):
        quote = self.measured_quote((2,))
        UploadQuote.objects.update(state=UploadQuote.State.EXPIRED)
        self.assertEqual(data_of(self.get(quote.pk))["state"], "EXPIRED")

    def test_the_open_list_finds_only_my_open_quotes(self):
        mine = self.measured_quote((2,))
        done = self.measured_quote((3,))
        UploadQuote.objects.filter(pk=done.pk).update(state=UploadQuote.State.CANCELLED)
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(reverse(LIST_URL), {"open": "true"})
        self.assertEqual(response.status_code, 200)
        rows = data_of(response)
        rows = rows.get("results", rows) if isinstance(rows, dict) else rows
        self.assertEqual([r["id"] for r in rows], [str(mine.pk)])
        self.assertEqual(
            set(rows[0]),
            {
                "id",
                "kind",
                "created_at",
                "expires_at",
                "lifetime_minutes",
                "file_count",
                "ready",
            },
        )


class Cancel(Base):
    def test_delete_cancels_and_deletes_the_files_at_once(self):
        quote_id = data_of(self.create([pdf(), pdf("b.pdf", pages=3)]))["id"]
        self.client.force_authenticate(user=self.teacher)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(reverse(DETAIL_URL, args=[quote_id]))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(data_of(response), {"state": "CANCELLED"})
        self.assertEqual(kept_files.MEMORY, {})
        self.assertEqual(UploadQuote.objects.get().state, UploadQuote.State.CANCELLED)

    def test_a_second_cancel_is_200_too(self):
        quote_id = data_of(self.create([pdf()]))["id"]
        self.client.force_authenticate(user=self.teacher)
        url = reverse(DETAIL_URL, args=[quote_id])
        self.client.delete(url)
        self.assertEqual(self.client.delete(url).status_code, 200)

    def test_cancel_after_confirm_is_409_closed(self):
        quote_id = data_of(self.create([pdf()]))["id"]
        UploadQuote.objects.update(state=UploadQuote.State.CONFIRMED)
        self.client.force_authenticate(user=self.teacher)
        response = self.client.delete(reverse(DETAIL_URL, args=[quote_id]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["field_errors"]["reason_code"], "QUOTE_CLOSED"
        )


class Tenancy(Base):
    def test_other_teachers_unknown_and_malformed_ids_are_one_identical_404(self):
        quote_id = data_of(self.create([pdf()]))["id"]
        self.client.force_authenticate(user=self.other)
        bodies = set()
        for path in (
            reverse(DETAIL_URL, args=[quote_id]),
            reverse(DETAIL_URL, args=["00000000-0000-0000-0000-000000000000"]),
            "/api/v1/submissions-credit-estimate/not-a-uuid",
        ):
            for method in ("get", "delete"):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 404, (path, method))
                bodies.add(re.sub(rb"\s+", b"", response.content))
        self.assertEqual(len(bodies), 1)

    def test_a_quote_whose_course_the_teacher_can_no_longer_reach_is_404(self):
        quote_id = data_of(self.create([pdf()]))["id"]
        from classrooms.models import Course

        Course.objects.filter(pk=self.course.pk).update(teacher=self.other)
        self.assertEqual(self.get(quote_id).status_code, 404)

    def test_other_teachers_assignment_is_404_on_create(self):
        response = self.create([pdf()], user=self.other)
        self.assertEqual(response.status_code, 404)

    def test_students_and_admins_are_403(self):
        for user_type in (
            UserTypes.STUDENT,
            UserTypes.SCHOOL_ADMIN,
            UserTypes.SUPER_ADMIN,
        ):
            user = CustomUser.objects.create_user(
                email=f"{user_type}@example.com",
                password="password123",  # pragma: allowlist secret
                user_type=user_type,
                first_name="X",
                last_name="Y",
                is_active=True,
            )
            self.client.force_authenticate(user=user)
            with self.subTest(user_type=user_type):
                self.assertEqual(
                    self.client.get(reverse(LIST_URL), {"open": "true"}).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        reverse(LIST_URL), {}, format="multipart"
                    ).status_code,
                    403,
                )

    def test_anonymous_is_401(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(reverse(LIST_URL)).status_code, 401)

    def test_a_teacher_with_no_credits_gets_the_402_not_a_quote(self):
        """BE-B-04: the route is not behind HasCreditBalance, so a teacher at
        0 reads the verdict instead of a 402."""
        from billing.models import CreditWallet

        poor = ai_ready_teacher("qr-poor@example.com", 0)
        self.assertEqual(
            CreditWallet.objects.get(user=poor).total_remaining_credits(), 0
        )
        response = self.create([pdf()], user=poor)
        self.assertEqual(response.status_code, 404)  # not their assignment...
        from classrooms.models import Course, Session

        session = Session.objects.create(name="P", teacher=poor)
        course = Course.objects.create(name="P", teacher=poor, session=session)
        self.client.force_authenticate(user=poor)
        response = self.client.post(
            reverse(LIST_URL),
            {"kind": "assignment_files", "course_id": str(course.pk), "files": [pdf()]},
            format="multipart",
        )
        # Owner Q3 = C (2026-11-20): at 0 spendable the answer is the existing
        # 402 before anything is stored (the verdict at 0 is no longer given).
        self.assertEqual(response.status_code, 402, response.content)
        self.assertFalse(UploadQuote.objects.exists())

    def test_the_switch_off_is_404_before_authentication(self):
        with override_settings(UPLOAD_QUOTES_ENABLED=False):
            for authenticated in (False, True):
                self.client.force_authenticate(
                    user=self.teacher if authenticated else None
                )
                for response in (
                    self.client.get(reverse(LIST_URL)),
                    self.client.post(reverse(LIST_URL), {}, format="multipart"),
                    self.client.get(
                        reverse(
                            DETAIL_URL, args=["00000000-0000-0000-0000-000000000000"]
                        )
                    ),
                ):
                    self.assertEqual(response.status_code, 404)


class TheSwitchIsOnByDefault(SimpleTestCase):
    """Owner ruling 2026-11-20: on by default (the six sentences are approved)."""

    def test_default(self):
        from django.conf import settings as django_settings

        self.assertTrue(django_settings.UPLOAD_QUOTES_ENABLED)


class ThePath(Base):
    """Owner ruling 2026-11-27 (B, A): the routes moved straight to
    /api/v1/submissions-credit-estimate/ for both kinds; the old
    /api/v1/upload-quotes/ path is gone, with no alias."""

    NEW = "/api/v1/submissions-credit-estimate/"
    OLD = "/api/v1/upload-quotes/"

    def test_the_url_names_resolve_to_the_new_path(self):
        quote_id = "00000000-0000-0000-0000-000000000000"
        self.assertEqual(reverse(LIST_URL), self.NEW)
        self.assertEqual(reverse(DETAIL_URL, args=[quote_id]), self.NEW + quote_id)
        self.assertEqual(
            reverse("upload-quote-files", args=[quote_id]),
            f"{self.NEW}{quote_id}/files",
        )
        self.assertEqual(
            reverse("upload-quote-confirm", args=[quote_id]),
            f"{self.NEW}{quote_id}/confirm",
        )

    def test_the_new_path_works_for_both_kinds(self):
        for kind in ("answer_papers", "assignment_files"):
            created = self.create([pdf()], kind=kind)
            self.assertEqual(created.status_code, 201, (kind, created.content))
            quote_id = data_of(created)["id"]
            self.assertEqual(
                self.client.get(f"{self.NEW}{quote_id}").status_code, 200, kind
            )
        listed = self.client.get(self.NEW, {"open": "true"})
        self.assertEqual(listed.status_code, 200)

    def test_the_old_path_is_404_on_every_route(self):
        quote_id = data_of(self.create([pdf()]))["id"]
        self.client.force_authenticate(user=self.teacher)
        calls = (
            ("get", self.OLD),
            ("post", self.OLD),
            ("get", f"{self.OLD}{quote_id}"),
            ("delete", f"{self.OLD}{quote_id}"),
            ("post", f"{self.OLD}{quote_id}/files"),
            ("post", f"{self.OLD}{quote_id}/confirm"),
        )
        for method, path in calls:
            response = getattr(self.client, method)(path)
            self.assertEqual(response.status_code, 404, (method, path))
        self.assertEqual(self.get(quote_id).status_code, 200)

    def test_the_schema_has_only_the_new_path_and_tag(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        paths = [p for p in schema["paths"] if "credit-estimate" in p]
        self.assertEqual(
            sorted(paths),
            [
                "/api/v1/submissions-credit-estimate/",
                "/api/v1/submissions-credit-estimate/{id}",
                "/api/v1/submissions-credit-estimate/{id}/confirm",
                "/api/v1/submissions-credit-estimate/{id}/files",
            ],
        )
        self.assertFalse([p for p in schema["paths"] if "upload-quote" in p])
        for path in paths:
            for operation in schema["paths"][path].values():
                self.assertEqual(operation["tags"], ["Submissions credit estimates"])
