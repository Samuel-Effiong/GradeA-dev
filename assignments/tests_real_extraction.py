"""ONE real, unmocked extraction call through this section's AI seam.

Opt-in: set RUN_REAL_AI=1. It costs money and needs network, so it is
skipped by default - the same pattern tests_google_auth.py uses for its
live provider checks and tests_load.py uses for its load runs.

Why this exists even though tests_extraction_service.py already covers the
logic: that file stubs ai_processor, so it proves what this app does with
the model's answer, not that the call works. Per this project's standing
rule an AI/billed-API path must be verified against the real provider at
least once per review pass - a fully mocked suite cannot catch a payload
this app builds wrongly, because a mock returns whatever shape the test
author imagined.

What is deliberately exercised for real here:

  * AssignmentProcessingService.prepare_ai_content - including the image
    decode and the megapixel ceiling added during this review. A mock
    would happily accept a payload the real provider rejects.
  * ai_processor.extract_assignment_with_retry - the actual billed call.
  * The provenance/write-back logic in extract_assignment_data, running on
    a genuine model response rather than a hand-written one.

The assertions are deliberately about STRUCTURE and grounding, not exact
wording: the model is non-deterministic, so asserting an exact string
would produce a test that fails for the wrong reason. What must hold is
that the contract the rest of the pipeline depends on is satisfied, and
that the extraction is actually about the document we sent.
"""

import os
import unittest
from io import BytesIO

from django.test import TestCase

from assignments.services import AssignmentProcessingService
from classrooms.models import Course, Session
from users.models import CustomUser, UserTypes

RUN_REAL_AI = os.environ.get("RUN_REAL_AI") == "1"
SKIP_REASON = "Real AI call is opt-in and billed: set RUN_REAL_AI=1"

# Distinctive enough that a model answering from priors rather than from
# the image would not produce them.
QUESTION_ONE = "What is the capital city of Iceland?"
QUESTION_TWO = "Name the largest moon of Saturn."


def assignment_image_bytes():
    """Render a small, legible two-question worksheet as a real PNG."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1000, 500), "white")
    draw = ImageDraw.Draw(image)
    lines = [
        "Geography and Astronomy Quiz",
        "",
        "Answer both questions. Each is worth 5 marks.",
        "",
        f"1. {QUESTION_ONE} (5 marks)",
        "",
        f"2. {QUESTION_TWO} (5 marks)",
    ]
    y = 40
    for line in lines:
        draw.text((40, y), line, fill="black")
        y += 40

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@unittest.skipUnless(RUN_REAL_AI, SKIP_REASON)
class RealAssignmentExtractionTest(TestCase):
    def setUp(self):
        from datetime import timedelta

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.utils import timezone

        from billing.models import (
            BillingInterval,
            CreditBucket,
            CreditBucketType,
            CreditWallet,
            PlanCategory,
            PlanTier,
            PlanType,
            SubscriptionPlan,
            UserSubscription,
        )

        self.teacher = CustomUser.objects.create_user(
            email="real-extraction@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Real",
            last_name="Teacher",
            is_active=True,
        )
        # The AI access gate (billing/access_control.py) requires an active
        # account, an active subscription context AND remaining credits.
        # Building that here rather than mocking the gate keeps the billed
        # call on exactly the path a real teacher takes.
        plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Real Extraction",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            interval=BillingInterval.MONTHLY,
            monthly_credits=500_000,
            overage_block_size=500,
            overage_block_price=10,
            max_overage_blocks=10,
            is_active=True,
        )
        now = timezone.now()
        UserSubscription.objects.create(
            user=self.teacher,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + timedelta(days=29),
            next_credit_grant_at=now + timedelta(days=29),
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.filter(wallet=wallet).delete()
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            # A single image extraction was measured at ~33k credits, so
            # this is sized well clear of the estimator's requirement
            # rather than at a number that would make the test fail for
            # budget reasons instead of correctness ones.
            total_credits=500_000,
            used_credits=0,
            expires_at=now + timedelta(days=25),
        )
        self.session = Session.objects.create(name="Real Session", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Real Course", teacher=self.teacher, session=self.session
        )
        self.uploaded = SimpleUploadedFile(
            "quiz.png", assignment_image_bytes(), content_type="image/png"
        )

    def test_a_real_extraction_returns_a_usable_assignment(self):
        prompt = (
            "Analyze the image of an educational assignment and return a JSON.\n"
            "IMPORTANT: Return only valid JSON matching the required structure."
        )

        # REAL prepare_ai_content: real Pillow decode, real megapixel gate,
        # real base64 payload construction.
        content = AssignmentProcessingService.prepare_ai_content(self.uploaded, prompt)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(
            content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        )

        # REAL billed call.
        data = AssignmentProcessingService.extract_assignment_data(
            self.teacher, content, course=self.course
        )

        print("\n=== REAL EXTRACTION RESULT ===")
        print("title:", data.get("title"))
        print("assignment_type:", data.get("assignment_type"))
        print("total_points:", data.get("total_points"))
        print("question_count:", data.get("question_count"))
        for question in data.get("questions") or []:
            print(
                "  Q{}: {!r} type={} points={}".format(
                    question.get("question_number"),
                    (question.get("question_text") or "")[:90],
                    question.get("question_type"),
                    question.get("points"),
                )
            )
        print("extraction_confidence:", data.get("extraction_confidence"))
        print("==============================\n")

        # --- the contract the rest of the pipeline depends on -----------
        questions = data.get("questions")
        self.assertIsInstance(questions, list)
        assert questions is not None  # narrows the type for mypy
        self.assertGreaterEqual(
            len(questions), 1, "the model returned no questions at all"
        )

        for question in questions:
            self.assertIn("question_text", question)
            self.assertIn("question_type", question)
            self.assertIn(
                question["question_type"],
                {"OBJECTIVE", "ESSAY", "SHORT-ANSWER"},
                f"unknown question_type {question['question_type']!r} - the "
                "grader dispatches on this value",
            )
            self.assertIsNotNone(question.get("points"))

        # --- grounding: it read OUR document, not its own priors --------
        blob = " ".join((q.get("question_text") or "") for q in questions).lower()
        self.assertTrue(
            "iceland" in blob or "saturn" in blob,
            "the extraction does not mention anything from the image that "
            f"was sent - got: {blob[:300]!r}",
        )

        # --- provenance written by this app, on a real response ---------
        self.assertIs(data["ai_generated"], False)
        self.assertIn("questions", data["ai_raw_payload"])
        self.assertLessEqual(
            data["extraction_started_at"], data["extraction_completed_at"]
        )

    def test_the_extracted_document_survives_the_prosemirror_round_trip(self):
        """
        The real output has to convert into what the editor loads. A model
        response containing markup this app's converter chokes on would
        break the teacher's edit screen, and only a real response can
        prove it does not.
        """
        import json

        prompt = (
            "Analyze the image of an educational assignment and return a JSON.\n"
            "IMPORTANT: Return only valid JSON matching the required structure."
        )
        content = AssignmentProcessingService.prepare_ai_content(self.uploaded, prompt)

        data = AssignmentProcessingService.extract_assignment_data(
            self.teacher, content, course=self.course, generate_raw_input=True
        )

        document = json.loads(data["raw_input"])
        self.assertEqual(document["type"], "doc")
        self.assertTrue(document.get("content"), "the editor document is empty")
