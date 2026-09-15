"""
Teacher multi-file assignment upload: every file gets its own outcome, and a
retry never charges for, or re-creates, a file that already succeeded.

The defect (Section 9 review, 2026-09-14): in a batch upload, a file refused
by prepare_ai_content escaped the per-file loop after earlier files had
already been extracted, charged and saved. The whole request answered 400
naming only the bad file, so a teacher who retried the "failed" batch paid
for the good files again and got duplicate assignments.

Billing here is real: CreditWallet / CreditBucket / CreditUsageLog rows,
consume_credits and refund_credits, on real PostgreSQL. Only the network call
to the model provider (AIProcessor.__ai_model) is replaced. It answers per
file: every good test file is an image of a distinct width, and the stand-in
reads that width back out of the rasterized image it is sent, so each charge
can be traced to its file by amount - even while requests race.

TransactionTestCase throughout: charges and refunds commit independently of
each other (billing/refunds.py), and the concurrency tests need real commits
across connections.
"""

import base64
import hashlib
import io
import json
import threading
import time
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from ai_processor.services import AIProcessor
from assignments.file_uploads import CLAIM_STALE_AFTER, IN_PROGRESS_MESSAGE
from assignments.models import Assignment, AssignmentUploadFingerprint
from assignments.serializers import AssignmentSerializer
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditUsageLog,
    CreditWallet,
    PlanCategory,
    PlanTier,
    SubscriptionPlan,
    UserSubscription,
)
from classrooms.models import Course, Session
from users.models import CustomUser, UserTypes

# Good files, by name: the image width that identifies each one to the
# provider stand-in, and the credits its extraction call is charged.
GOOD_FILES = {"a.png": 201, "c.png": 203, "d.png": 204}
TOKENS = {201: 1100, 203: 3300, 204: 4400}

EXTRACTION = json.dumps(
    {
        "title": "Uploaded quiz",
        "instructions": "Answer every question.",
        "assignment_type": "OBJECTIVE",
        "total_points": 5,
        "question_count": 1,
        "questions": [
            {
                "question_number": 1,
                "question_text": "What is 2 + 2?",
                "question_type": "OBJECTIVE",
                "points": 5,
                "options": ["3", "4"],
                "rubric": [],
                "model_answer": "4",
            }
        ],
    }
)

UNREADABLE_PDF_ERROR = "Could not read this PDF"
PROCESSING = AssignmentUploadFingerprint.Status.PROCESSING
COMPLETED = AssignmentUploadFingerprint.Status.COMPLETED


def png(width):
    buffer = io.BytesIO()
    Image.new("RGB", (width, 120), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def upload_file(name):
    """A good file is a PNG of its registered width; anything else is bytes
    that are neither a PDF nor an image, labelled as the one its name says."""
    if name in GOOD_FILES:
        return SimpleUploadedFile(name, png(GOOD_FILES[name]), content_type="image/png")
    content_type = "application/pdf" if name.endswith(".pdf") else "image/png"
    return SimpleUploadedFile(
        name, b"this is not a real file", content_type=content_type
    )


def ai_response(tokens, content=EXTRACTION):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage.total_tokens = tokens
    return response


def width_of(user_prompt):
    image = next(part for part in user_prompt if part.get("type") == "image_url")
    with Image.open(io.BytesIO(base64.b64decode(image["bytes"]))) as decoded:
        return decoded.size[0]


class Provider:
    """
    Stand-in for the model provider. By default every file extracts cleanly
    and is charged its own amount; `behaviour[width]` overrides one file.
    """

    def __init__(self):
        self.calls = []
        self.behaviour = {}
        self._lock = threading.Lock()

    def __call__(self, system_prompt, user_prompt, *args, **kwargs):
        width = width_of(user_prompt)
        with self._lock:
            self.calls.append(width)
        override = self.behaviour.get(width)
        if override is not None:
            return override(width)
        return ai_response(TOKENS[width])

    def calls_for(self, name):
        with self._lock:
            return self.calls.count(GOOD_FILES[name])


def funded_teacher_with_course(tag, credits=500_000):
    teacher = CustomUser.objects.create_user(
        email=f"batch-{tag}-{uuid.uuid4().hex[:8]}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
        first_name="Batch",
        last_name=tag.title(),
        is_active=True,
    )
    # can_user_access_ai (billing/access_control.py) needs an active
    # subscription as well as a non-empty wallet.
    plan = SubscriptionPlan.objects.create(
        name=f"plan-{uuid.uuid4().hex[:8]}",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        interval=BillingInterval.MONTHLY,
        monthly_credits=20_000,
        carry_over_percent=25,
        is_active=True,
    )
    now = timezone.now()
    UserSubscription.objects.create(
        user=teacher,
        plan=plan,
        is_active=True,
        billing_cycle_start=now,
        billing_cycle_end=now + timedelta(days=30),
        is_trial=False,
        auto_renew=True,
    )
    wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=credits,
        used_credits=0,
        expires_at=now + timedelta(days=30),
    )
    session = Session.objects.create(name=f"Session {tag}", teacher=teacher)
    course = Course.objects.create(
        name=f"Course {tag}", teacher=teacher, session=session
    )
    return teacher, course


def charges(teacher):
    """(kept, refunded): the credit amounts charged to this teacher, sorted."""
    logs = list(CreditUsageLog.objects.filter(wallet__user=teacher))
    kept = sorted(log.amount for log in logs if not log.is_refunded)
    refunded = sorted(log.amount for log in logs if log.is_refunded)
    return kept, refunded


def credits_used(teacher):
    return sum(
        CreditBucket.objects.filter(wallet__user=teacher).values_list(
            "used_credits", flat=True
        )
    )


class BatchUploadBillingFixture(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.provider = Provider()
        patcher = patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=self.provider
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.teacher, self.course = funded_teacher_with_course("main")

    def upload(self, names, *, teacher=None, course=None):
        client = APIClient()
        client.force_authenticate(user=teacher or self.teacher)
        return client.post(
            reverse("assignment-upload"),
            {
                "course": str((course or self.course).id),
                "assignments": [upload_file(name) for name in names],
            },
            format="multipart",
        )

    def outcomes(self, response):
        """(successful, failed) per-file entries from a 201 or 207 response."""
        data = response.json()["data"]
        if response.status_code == status.HTTP_207_MULTI_STATUS:
            return data["successful"], data["failed"]
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return data, []

    def assert_no_open_claims(self, course=None):
        self.assertFalse(
            AssignmentUploadFingerprint.objects.filter(
                course=course or self.course, status=PROCESSING
            ).exists(),
            "a finished request left a file claimed, so re-uploading it would "
            "be refused as in progress",
        )


class EveryFileHasItsOwnOutcomeTest(BatchUploadBillingFixture):
    def test_a_bad_file_in_any_position_fails_only_itself(self):
        batches = {
            "first": ["b.pdf", "a.png", "c.png"],
            "middle": ["a.png", "b.pdf", "c.png"],
            "last": ["a.png", "c.png", "b.pdf"],
        }
        for position, names in batches.items():
            with self.subTest(position=position):
                teacher, course = funded_teacher_with_course(position)

                response = self.upload(names, teacher=teacher, course=course)

                self.assertEqual(
                    response.status_code,
                    status.HTTP_207_MULTI_STATUS,
                    response.content[:300],
                )
                successful, failed = self.outcomes(response)
                self.assertEqual(
                    sorted(entry["file_name"] for entry in successful),
                    ["a.png", "c.png"],
                )
                self.assertEqual(
                    [entry["already_uploaded"] for entry in successful], [False, False]
                )
                self.assertEqual([entry["file_name"] for entry in failed], ["b.pdf"])
                self.assertIn(UNREADABLE_PDF_ERROR, failed[0]["error"])

                # A and C charged once each; the bad file never reached the AI.
                self.assertEqual(charges(teacher), ([1100, 3300], []))
                self.assertEqual(credits_used(teacher), 4400)
                self.assertEqual(Assignment.objects.filter(course=course).count(), 2)
                self.assert_no_open_claims(course)

    def test_an_all_invalid_batch_charges_and_creates_nothing(self):
        response = self.upload(["b.pdf", "x.png"])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        message = response.json()["message"]
        self.assertIn("b.pdf", message)
        self.assertIn("x.png", message)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(charges(self.teacher), ([], []))
        self.assertFalse(Assignment.objects.filter(course=self.course).exists())
        self.assertFalse(
            AssignmentUploadFingerprint.objects.filter(course=self.course).exists()
        )

    def test_an_all_valid_batch_charges_each_file_once(self):
        response = self.upload(["a.png", "c.png", "d.png"])

        successful, failed = self.outcomes(response)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(failed, [])
        self.assertEqual(len(successful), 3)
        self.assertTrue(all(entry["already_uploaded"] is False for entry in successful))
        self.assertEqual(charges(self.teacher), ([1100, 3300, 4400], []))
        self.assertEqual(Assignment.objects.filter(course=self.course).count(), 3)
        self.assertEqual(
            AssignmentUploadFingerprint.objects.filter(
                course=self.course, status=COMPLETED
            ).count(),
            3,
        )


class RetryDoesNotRechargeTest(BatchUploadBillingFixture):
    def test_replaying_a_partly_failed_batch_charges_and_creates_nothing_new(self):
        names = ["a.png", "b.pdf", "c.png"]
        first_successful, _ = self.outcomes(self.upload(names))
        first_ids = {e["file_name"]: e["assignment"]["id"] for e in first_successful}
        calls_before = len(self.provider.calls)

        response = self.upload(names)

        self.assertEqual(response.status_code, status.HTTP_207_MULTI_STATUS)
        successful, failed = self.outcomes(response)
        self.assertEqual(
            {e["file_name"]: e["assignment"]["id"] for e in successful}, first_ids
        )
        self.assertTrue(all(entry["already_uploaded"] for entry in successful))
        self.assertEqual([entry["file_name"] for entry in failed], ["b.pdf"])

        self.assertEqual(len(self.provider.calls), calls_before, "a retry re-extracted")
        self.assertEqual(charges(self.teacher), ([1100, 3300], []))
        self.assertEqual(credits_used(self.teacher), 4400)
        self.assertEqual(Assignment.objects.filter(course=self.course).count(), 2)

    def test_replaying_an_all_valid_batch_returns_every_existing_assignment(self):
        self.upload(["a.png", "c.png"])

        response = self.upload(["a.png", "c.png"])

        successful, _ = self.outcomes(response)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            [entry["already_uploaded"] for entry in successful], [True, True]
        )
        self.assertEqual(charges(self.teacher), ([1100, 3300], []))
        self.assertEqual(Assignment.objects.filter(course=self.course).count(), 2)

    def test_the_same_file_in_another_course_is_its_own_assignment(self):
        other_course = Course.objects.create(
            name="Second course", teacher=self.teacher, session=self.course.session
        )
        self.upload(["a.png"])

        response = self.upload(["a.png"], course=other_course)

        successful, _ = self.outcomes(response)
        self.assertEqual(successful[0]["already_uploaded"], False)
        self.assertEqual(charges(self.teacher), ([1100, 1100], []))

    def test_deleting_the_assignment_lets_the_file_be_uploaded_again(self):
        self.upload(["a.png"])
        Assignment.objects.filter(course=self.course).delete()
        self.assertFalse(
            AssignmentUploadFingerprint.objects.filter(course=self.course).exists(),
            "the fingerprint must go with its assignment",
        )

        response = self.upload(["a.png"])

        successful, _ = self.outcomes(response)
        self.assertEqual(successful[0]["already_uploaded"], False)
        self.assertEqual(Assignment.objects.filter(course=self.course).count(), 1)


class FailedFilesAreRefundedTest(BatchUploadBillingFixture):
    def test_a_provider_outage_on_one_file_charges_nothing_and_it_can_be_retried(self):
        def outage(width):
            raise ConnectionError("provider unreachable")

        self.provider.behaviour[GOOD_FILES["c.png"]] = outage

        response = self.upload(["a.png", "c.png"])

        successful, failed = self.outcomes(response)
        self.assertEqual([e["file_name"] for e in successful], ["a.png"])
        self.assertEqual([e["file_name"] for e in failed], ["c.png"])
        self.assertEqual(charges(self.teacher), ([1100], []))
        self.assert_no_open_claims()

        self.provider.behaviour.clear()
        successful, failed = self.outcomes(self.upload(["a.png", "c.png"]))

        self.assertEqual(failed, [])
        self.assertEqual(
            {e["file_name"]: e["already_uploaded"] for e in successful},
            {"a.png": True, "c.png": False},
        )
        self.assertEqual(charges(self.teacher), ([1100, 3300], []))

    def test_an_unusable_provider_response_is_charged_then_refunded_in_full(self):
        """The call succeeds and is billed, but its output is not JSON:
        extraction retries it, billing every attempt, then gives up. Every
        one of those charges has to come back."""
        self.provider.behaviour[GOOD_FILES["c.png"]] = lambda width: ai_response(
            TOKENS[width], "this is not JSON"
        )

        successful, failed = self.outcomes(self.upload(["a.png", "c.png"]))

        self.assertEqual([e["file_name"] for e in failed], ["c.png"])
        kept, refunded = charges(self.teacher)
        self.assertEqual(kept, [1100])
        self.assertEqual(refunded, [3300] * self.provider.calls_for("c.png"))
        self.assertGreater(len(refunded), 0)
        self.assertEqual(credits_used(self.teacher), 1100)
        self.assert_no_open_claims()

    def test_a_failed_save_refunds_the_file_and_it_can_be_retried(self):
        real_save = AssignmentSerializer.save
        saves = []

        def second_save_fails(serializer, **kwargs):
            saves.append(serializer)
            if len(saves) == 2:
                raise DatabaseError("simulated write failure")
            return real_save(serializer, **kwargs)

        with patch.object(
            AssignmentSerializer, "save", autospec=True, side_effect=second_save_fails
        ):
            successful, failed = self.outcomes(self.upload(["a.png", "c.png"]))

        self.assertEqual([e["file_name"] for e in successful], ["a.png"])
        self.assertEqual([e["file_name"] for e in failed], ["c.png"])
        self.assertEqual(charges(self.teacher), ([1100], [3300]))
        self.assertEqual(credits_used(self.teacher), 1100)
        self.assertEqual(Assignment.objects.filter(course=self.course).count(), 1)
        self.assert_no_open_claims()

        successful, failed = self.outcomes(self.upload(["a.png", "c.png"]))

        self.assertEqual(failed, [])
        self.assertEqual(charges(self.teacher), ([1100, 3300], [3300]))
        self.assertEqual(Assignment.objects.filter(course=self.course).count(), 2)


class ConcurrentRetriesTest(BatchUploadBillingFixture):
    def _in_thread(self, names, results):
        def run():
            try:
                results.append(self.upload(names))
            finally:
                connection.close()

        thread = threading.Thread(target=run)
        thread.start()
        return thread

    def test_a_retry_racing_the_original_is_refused_not_extracted_twice(self):
        entered = threading.Event()
        release = threading.Event()

        def held(width):
            entered.set()
            self.assertTrue(release.wait(30), "the test never released the original")
            return ai_response(TOKENS[width])

        self.provider.behaviour[GOOD_FILES["a.png"]] = held
        original = []
        thread = self._in_thread(["a.png"], original)
        try:
            self.assertTrue(
                entered.wait(30), "the original upload never reached the AI"
            )

            retry = self.upload(["a.png"])

            self.assertEqual(retry.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn(IN_PROGRESS_MESSAGE, retry.json()["message"])
            self.assertEqual(self.provider.calls_for("a.png"), 1)
        finally:
            release.set()
            thread.join(60)

        [original_response] = original
        successful, _ = self.outcomes(original_response)
        self.assertEqual(successful[0]["already_uploaded"], False)
        self.assertEqual(charges(self.teacher), ([1100], []))
        self.assertEqual(Assignment.objects.filter(course=self.course).count(), 1)

    def test_identical_batches_racing_each_other_extract_every_file_once(self):
        names = ["a.png", "c.png", "d.png"]
        gate = threading.Barrier(4)

        def slow(width):
            time.sleep(0.3)  # widens the window in which requests overlap
            return ai_response(TOKENS[width])

        for width in TOKENS:
            self.provider.behaviour[width] = slow

        responses = []
        lock = threading.Lock()

        def run():
            try:
                gate.wait(30)
                response = self.upload(names)
                with lock:
                    responses.append(response)
            finally:
                connection.close()

        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)

        self.assertEqual(len(responses), 4)
        for response in responses:
            self.assertLess(response.status_code, 500, response.content[:300])
            self.assertIn(
                response.status_code,
                (
                    status.HTTP_201_CREATED,
                    status.HTTP_207_MULTI_STATUS,
                    status.HTTP_400_BAD_REQUEST,
                ),
            )
            if response.status_code == status.HTTP_207_MULTI_STATUS:
                for entry in response.json()["data"]["failed"]:
                    self.assertEqual(entry["error"], IN_PROGRESS_MESSAGE)

        self.assertEqual(charges(self.teacher), ([1100, 3300, 4400], []))
        self.assertEqual(sorted(self.provider.calls), sorted(TOKENS))
        self.assertEqual(Assignment.objects.filter(course=self.course).count(), 3)
        self.assert_no_open_claims()


class LeftoverClaimsTest(BatchUploadBillingFixture):
    def _claim(self, claimed_at):
        return AssignmentUploadFingerprint.objects.create(
            course=self.course,
            sha256=hashlib.sha256(png(GOOD_FILES["a.png"])).hexdigest(),
            claimed_at=claimed_at,
        )

    def test_a_live_claim_held_by_another_request_is_respected(self):
        self._claim(timezone.now())

        response = self.upload(["a.png"])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(IN_PROGRESS_MESSAGE, response.json()["message"])
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(charges(self.teacher), ([], []))

    def test_a_stale_claim_left_by_a_dead_request_is_taken_over(self):
        stale = self._claim(timezone.now() - CLAIM_STALE_AFTER - timedelta(minutes=1))

        successful, _ = self.outcomes(self.upload(["a.png"]))

        self.assertEqual(successful[0]["already_uploaded"], False)
        self.assertEqual(charges(self.teacher), ([1100], []))
        stale_after = AssignmentUploadFingerprint.objects.get(id=stale.id)
        self.assertEqual(stale_after.status, COMPLETED)
        self.assertNotEqual(stale_after.claim_token, stale.claim_token)

    def test_a_claim_taken_over_mid_extraction_does_not_save_a_second_copy(self):
        """A request whose claim was declared stale and taken over while it
        was still extracting must roll its save back and refund, not leave a
        duplicate beside the new claimant's."""

        def taken_over_meanwhile(width):
            AssignmentUploadFingerprint.objects.filter(course=self.course).update(
                claim_token=uuid.uuid4()
            )
            return ai_response(TOKENS[width])

        self.provider.behaviour[GOOD_FILES["a.png"]] = taken_over_meanwhile

        response = self.upload(["a.png"])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(IN_PROGRESS_MESSAGE, response.json()["message"])
        self.assertFalse(Assignment.objects.filter(course=self.course).exists())
        self.assertEqual(charges(self.teacher), ([], [1100]))
        # The newer claimant's row is left alone for it to finish.
        self.assertTrue(
            AssignmentUploadFingerprint.objects.filter(
                course=self.course, status=PROCESSING
            ).exists()
        )
