"""H-204: every provider request asks for the cheapest supplier first and
never uses an fp4 endpoint.

The `provider` object of each request (`_provider_block`, H-206) gains
`sort: "price"`, so the provider tries the cheapest supplier first and the
next cheapest on an outage, and `quantizations`, which leaves out the fp4
endpoints (two of deepseek-v4-pro's). "unknown" stays in the list: every
endpoint of grok-4.3, claude-haiku-5.5 and gpt-5.4-nano publishes its
quantization as unknown (the endpoints list, read 2026-10-25), so without it
those models would have no endpoint at all. `data_collection` and the zdr
switch are unchanged.

The request tests go through the real callers and the real __ai_model; only
the SDK's create is replaced. It keeps the request, then either returns the
next prepared reply or stops the call with an InsufficientCreditsError,
which every caller here passes on unchanged.

The values are written out here, not read from the module, so that each test
fails for its own reason against code without the change.

Run with:
    python manage.py test ai_processor.tests_h204_provider_price_sort
"""

import io
import json
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from ai_processor import services
from ai_processor.services import AIProcessor
from assignments.services import AssignmentProcessingService
from billing.errors import InsufficientCreditsError
from users.models import CustomUser, UserTypes

SORT = "price"
QUANTIZATIONS = ["fp8", "fp16", "bf16", "fp32", "unknown"]
HAIKU = "anthropic/claude-haiku-5.5"
DEEPSEEK = "deepseek/deepseek-v4-pro"
GROK = "x-ai/grok-4.3"
ANSWER_TEXT = "The diagram shows a cell dividing into two."


class TheBlockTest(SimpleTestCase):
    def test_the_cheapest_supplier_is_tried_first(self):
        self.assertEqual(services._provider_block().get("sort"), SORT)

    def test_the_allowed_quantizations(self):
        self.assertEqual(services._provider_block().get("quantizations"), QUANTIZATIONS)

    def test_unknown_stays_because_grok_and_haiku_publish_none(self):
        self.assertIn("unknown", services._provider_block().get("quantizations") or [])

    def test_no_fp4_endpoint(self):
        allowed = services._provider_block().get("quantizations")
        self.assertTrue(allowed, "no quantization list is sent")
        for value in ("fp4", "fp6", "int4", "int8"):
            with self.subTest(value=value):
                self.assertNotIn(value, allowed or [])

    @override_settings(AI_PROVIDER_ZDR_ENABLED=True)
    def test_with_zdr_on_the_block_keeps_zdr_and_has_both_keys(self):
        block = services._provider_block()
        self.assertIs(block.get("zdr"), True)
        self.assertEqual(block.get("data_collection"), "deny")
        self.assertEqual(block.get("sort"), SORT)
        self.assertEqual(block.get("quantizations"), QUANTIZATIONS)

    @override_settings(AI_PROVIDER_ZDR_ENABLED=False)
    def test_with_zdr_off_there_is_no_zdr_key_and_data_collection_is_deny(self):
        """Control, green before and after H-204."""
        block = services._provider_block()
        self.assertEqual(block.get("data_collection"), "deny")
        self.assertNotIn("zdr", block)

    def test_each_call_gives_a_new_list(self):
        """A caller changing one request's list cannot change the next."""
        allowed = services._provider_block().get("quantizations")
        self.assertEqual(allowed, QUANTIZATIONS)
        if isinstance(allowed, list):
            allowed.append("fp4")
        self.assertEqual(services._provider_block().get("quantizations"), QUANTIZATIONS)


def _reply(payload, model):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload)),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(total_tokens=10),
        model=model,
    )


def _question(with_image):
    question = {
        "question_number": 1,
        "question_text": "What does the diagram show?",
        "question_type": "ESSAY",
        "points": 10,
        "options": [],
        "rubric": [
            {"level": "excellent", "description": "Full", "points": 10},
            {"level": "good", "description": "Most", "points": 8},
            {"level": "poor", "description": "None", "points": 0},
        ],
        "model_answer": "A cell dividing.",
    }
    if with_image:
        question["question_image"] = "https://example.com/q1.png"
    return question


def _evaluation():
    return {
        "question_number": 1,
        "score_awarded": 8,
        "max_points": 10,
        "level_achieved": "good",
        "evaluation_rationale": "Names the division.",
        "evidence_quotes": [ANSWER_TEXT],
    }


@override_settings(
    AI_PROVIDER_ZDR_ENABLED=False,
    GRADING_SECOND_OPINION_ENABLED=True,
    GRADING_SECOND_OPINION_MODELS=[HAIKU, DEEPSEEK],
    GRADING_SECOND_OPINION_MIN_CONFIDENCE=80,
    GRADING_SECOND_OPINION_SAMPLE_RATE=0,
    GRADING_SECOND_OPINION_SUBJECTIVE_TYPES=[],
    GRADING_ANSWER_CACHE_ENABLED=False,
)
class TheRequestsThatLeaveTest(TestCase):
    """A SUPER_ADMIN holding both flags (H-19): no billing step stands
    between the caller and the request."""

    def setUp(self):
        self.processor = AIProcessor()
        self.super_admin = CustomUser.objects.create_user(
            email="h204-admin@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
        )
        self.sent = []

    def send_through(self, replies=()):
        queue = list(replies)

        def create(**kwargs):
            self.sent.append(kwargs)
            if not queue:
                raise InsufficientCreditsError("stopped after the request was built")
            return queue.pop(0)

        self.processor.client = SimpleNamespace(  # type: ignore[assignment]
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    def assertCarriesBothKeys(self, request):
        provider = request["extra_body"]["provider"]
        self.assertEqual(provider.get("sort"), SORT)
        self.assertEqual(provider.get("quantizations"), QUANTIZATIONS)
        self.assertEqual(provider.get("data_collection"), "deny")

    def grade_one_question(self, question):
        pairs = [
            {"question": question, "answer": {"question_number": 1, "answer": "4"}}
        ]
        self.send_through()
        with self.assertRaises(InsufficientCreditsError):
            self.processor._grade_question_batch(
                self.super_admin, pairs, batch_number=1, total_batches=1
            )
        self.assertEqual(len(self.sent), 1)
        return self.sent[0]

    def test_a_text_grading_request_and_its_deepseek_backup(self):
        request = self.grade_one_question(
            {"question_number": 1, "question_text": "2 + 2?", "points": 2}
        )
        self.assertEqual(request["model"], GROK)
        self.assertEqual(request["extra_body"]["models"], [DEEPSEEK])
        self.assertCarriesBothKeys(request)
        self.assertNotIn("zdr", request["extra_body"]["provider"])

    @override_settings(AI_PROVIDER_ZDR_ENABLED=True)
    def test_a_text_grading_request_with_zdr_on(self):
        request = self.grade_one_question(
            {"question_number": 1, "question_text": "2 + 2?", "points": 2}
        )
        self.assertIs(request["extra_body"]["provider"].get("zdr"), True)
        self.assertCarriesBothKeys(request)

    def test_an_image_grading_request_and_its_haiku_backup(self):
        request = self.grade_one_question(_question(with_image=True))
        self.assertEqual(request["extra_body"]["models"], [HAIKU])
        self.assertCarriesBothKeys(request)

    def test_the_second_opinion_request(self):
        grader_a = {
            "question_evaluations": [_evaluation()],
            "grading_summary": {
                "total_score": 0,
                "max_total_points": 0,
                "percentage": 0,
            },
            "grading_confidence": 50,
            "overall_performance_analysis": "ok",
            "recommendations": [],
        }
        grader_b = {"question_evaluations": [_evaluation()]}
        self.send_through([_reply(grader_a, GROK), _reply(grader_b, HAIKU)])
        self.processor._grade_student_submission_impl(
            user=self.super_admin,
            rubric_json=[_question(with_image=False)],
            answer_json=[
                {"question_number": 1, "answer_html": f"<p>{ANSWER_TEXT}</p>"}
            ],
        )
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[1]["model"], HAIKU)
        self.assertCarriesBothKeys(self.sent[1])
        self.assertCarriesBothKeys(self.sent[0])

    def test_a_reading_request(self):
        buffer = io.BytesIO()
        Image.new("RGB", (24, 24), "white").save(buffer, format="PNG")
        upload = SimpleUploadedFile(
            "page.png", buffer.getvalue(), content_type="image/png"
        )
        content = AssignmentProcessingService.prepare_ai_content(upload, "read")
        self.send_through()
        with self.assertRaises(InsufficientCreditsError):
            self.processor.extract_answer_image(
                self.super_admin, content, "assignment context"
            )
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["extra_body"]["models"], [HAIKU])
        self.assertCarriesBothKeys(self.sent[0])
