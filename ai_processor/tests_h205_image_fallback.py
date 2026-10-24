"""H-205: page reading gets an image-capable backup model; Haiku replaces
gemini-2.5-flash as the second grader's third choice.

deepseek/deepseek-v4-pro, the backup of grading and both kinds of extraction,
cannot read images (it refused one with a 404). A request of those three task
types that carries an image part now falls back to anthropic/claude-haiku-5.5;
a text-only request keeps deepseek exactly as before. A pinned model (the
second grader) still wins over both. Every place that decides "a backup
answered" knows both lists, and both config versions name the new one.

The model names are written out here on purpose, not read from the module,
so that each test fails for its own reason against code without the change.

Run with:
    python manage.py test ai_processor.tests_h205_image_fallback
"""

import ast
import io
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from PIL import Image

from ai_processor import services
from ai_processor.grading_config import GradingConfig
from ai_processor.grading_run import GradingRun
from ai_processor.reading_config import ReadingConfig
from ai_processor.services import AIProcessor
from assignments.services import AssignmentProcessingService
from billing.errors import InsufficientCreditsError
from users.models import CustomUser, UserTypes

TEST_PROMPT_VERSION = "TEST_PROMPT:00000000"
HAIKU = "anthropic/claude-haiku-5.5"
DEEPSEEK = "deepseek/deepseek-v4-pro"
PAGE_TASKS = ("grade_assignment", "extract_answer", "extract_assignment")
IMAGE = {"type": "image_url", "image_url": {"url": "https://example.com/page.png"}}
PDF = {"type": "pdf_url", "pdf_url": {"url": "https://example.com/page.pdf"}}
TEXT = {"type": "text", "text": "read this"}
BASE = Path(settings.BASE_DIR)
#: Read by name: the list is new in H-205 (tests first).
IMAGE_LIST = "IMAGE_FALLBACK_MODELS"


def _fake_reply(*args, **kwargs):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "{}"
    response.usage.total_tokens = 10
    return response


class _Caller(TestCase):
    """A SUPER_ADMIN holding both flags (H-19): the routing decision is made
    before, and independently of, billing."""

    def setUp(self):
        self.processor = AIProcessor()
        self.super_admin = CustomUser.objects.create_user(
            email="h205-admin@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
        )

    def sub_models(self, task_type, **prompt):
        """The fallback list execute_graded_task hands to __ai_model."""
        captured = {}

        def fake_model(*args, **kwargs):
            captured["sub_models"] = kwargs.get("sub_models")
            return _fake_reply()

        arguments = {"system_prompt": "system", "user_prompt": "user"}
        arguments.update(prompt)
        with patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=fake_model
        ):
            self.processor.execute_graded_task(
                prompt_version=TEST_PROMPT_VERSION,
                user=self.super_admin,
                feature="Grading Assignment",
                task_type=task_type,
                **arguments,
            )
        return captured["sub_models"]


class AnImageCallFallsBackToHaikuTest(_Caller):
    def test_an_image_in_the_user_prompt_of_each_page_task(self):
        for task_type in PAGE_TASKS:
            with self.subTest(task_type=task_type):
                self.assertEqual(
                    self.sub_models(task_type, user_prompt=[TEXT, IMAGE]), [HAIKU]
                )

    def test_an_image_only_in_a_message(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": [TEXT, IMAGE]},
        ]
        self.assertEqual(
            self.sub_models(
                "extract_answer",
                system_prompt=None,
                user_prompt=None,
                messages=messages,
            ),
            [HAIKU],
        )

    def test_an_image_only_in_a_list_system_prompt(self):
        self.assertEqual(
            self.sub_models("grade_assignment", system_prompt=[TEXT, IMAGE]),
            [HAIKU],
        )


class EverythingElseIsAsBeforeTest(_Caller):
    def test_a_text_call_of_each_page_task_keeps_deepseek(self):
        for task_type in PAGE_TASKS:
            with self.subTest(task_type=task_type):
                self.assertEqual(self.sub_models(task_type), [DEEPSEEK])

    def test_a_list_prompt_with_only_text_keeps_deepseek(self):
        self.assertEqual(
            self.sub_models("extract_answer", user_prompt=[TEXT, dict(TEXT)]),
            [DEEPSEEK],
        )

    def test_a_pinned_model_wins_over_an_image(self):
        self.assertEqual(
            self.sub_models(
                "grade_assignment",
                user_prompt=[TEXT, IMAGE],
                override_model="pin/model",
            ),
            ["pin/model"],
        )

    def test_another_task_type_with_an_image_keeps_the_default_list(self):
        self.assertIsNone(
            self.sub_models("generate_assignment", user_prompt=[TEXT, IMAGE])
        )

    def test_a_pdf_part_alone_is_not_an_image(self):
        self.assertEqual(
            self.sub_models("extract_assignment", user_prompt=[TEXT, PDF]),
            [DEEPSEEK],
        )


class TheRequestThatLeavesTest(_Caller):
    """Through the real __ai_model, with only the SDK's create replaced."""

    def test_an_image_call_sends_haiku_as_the_only_backup(self):
        sent = []

        def create(**kwargs):
            sent.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="{}"), finish_reason="stop"
                    )
                ],
                usage=SimpleNamespace(total_tokens=1),
                model="x/y",
            )

        self.processor.client = SimpleNamespace(  # type: ignore[assignment]
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        for task_type in ("extract_assignment", "grade_assignment"):
            with self.subTest(task_type=task_type):
                del sent[:]
                self.processor.execute_graded_task(
                    prompt_version=TEST_PROMPT_VERSION,
                    user=self.super_admin,
                    feature="Grading Assignment",
                    task_type=task_type,
                    system_prompt="system",
                    user_prompt=[TEXT, IMAGE],
                )
                self.assertEqual(len(sent), 1)
                self.assertEqual(sent[0]["extra_body"]["models"], [HAIKU])


class TheRealCallersTest(_Caller):
    """The production callers build the parts themselves: an uploaded page
    through prepare_ai_content and extract_answer_image, and a question with
    an image through _grade_question_batch. Only __ai_model is replaced: it
    keeps the fallback list it was handed, then stops the call with an
    InsufficientCreditsError, which both callers pass on unchanged."""

    def capture(self):
        captured = []

        def fake_model(*args, **kwargs):
            captured.append(kwargs.get("sub_models"))
            raise InsufficientCreditsError("stopped after the call left")

        return captured, patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=fake_model
        )

    def test_an_uploaded_page_read_for_answers_falls_back_to_haiku(self):
        buffer = io.BytesIO()
        Image.new("RGB", (24, 24), "white").save(buffer, format="PNG")
        upload = SimpleUploadedFile(
            "page.png", buffer.getvalue(), content_type="image/png"
        )
        content = AssignmentProcessingService.prepare_ai_content(upload, "read")
        self.assertIn("image_url", [part.get("type") for part in content])
        captured, model = self.capture()
        with model, self.assertRaises(InsufficientCreditsError):
            self.processor.extract_answer_image(
                self.super_admin, content, "assignment context"
            )
        self.assertEqual(captured, [[HAIKU]])

    def _grade(self, question):
        pairs = [
            {"question": question, "answer": {"question_number": 1, "answer": "4"}}
        ]
        captured, model = self.capture()
        with model, self.assertRaises(InsufficientCreditsError):
            self.processor._grade_question_batch(
                self.super_admin, pairs, batch_number=1, total_batches=1
            )
        return captured

    def test_grading_a_question_with_an_image_falls_back_to_haiku(self):
        question = {
            "question_number": 1,
            "question_text": "What does the diagram show?",
            "points": 2,
            "question_image": "https://example.com/q1.png",
        }
        self.assertEqual(self._grade(question), [[HAIKU]])

    def test_grading_a_question_without_an_image_keeps_deepseek(self):
        question = {"question_number": 1, "question_text": "2 + 2?", "points": 2}
        self.assertEqual(self._grade(question), [[DEEPSEEK]])


class TheListTest(SimpleTestCase):
    def test_the_image_backup_is_haiku_and_never_a_nano_model(self):
        image_backups = getattr(services, IMAGE_LIST)
        self.assertEqual(image_backups, [HAIKU])
        for model in image_backups:
            self.assertNotIn("nano", model)


class ABackupAnsweredTest(SimpleTestCase):
    def test_a_grading_answered_by_haiku_says_a_backup_was_used(self):
        run = GradingRun.start()
        run.fresh_answers.append(HAIKU)
        self.assertEqual(run.label()["grading_fallback_used"], "yes")
        self.assertEqual(run.fresh_backup_used(), "yes")


class TheConfigVersionsNameTheListTest(SimpleTestCase):
    def test_the_grading_version_changes_with_the_image_list(self):
        base = GradingConfig.read().version
        with patch.object(services, "IMAGE_FALLBACK_MODELS", ["other/image"]):
            self.assertNotEqual(GradingConfig.read().version, base)

    def test_the_reading_version_changes_with_the_image_list(self):
        base = ReadingConfig.read().version
        with patch.object(services, "IMAGE_FALLBACK_MODELS", ["other/image"]):
            self.assertNotEqual(ReadingConfig.read().version, base)


def _settings_default(name):
    """The `default=` list of `name = env.list(...)` in settings.py, read
    from the file's syntax tree with literal_eval (no quoting to match)."""
    source = (BASE / "AutoGrader" / "settings.py").read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
            and isinstance(node.value, ast.Call)
        ):
            for keyword in node.value.keywords:
                if keyword.arg == "default":
                    return ast.literal_eval(keyword.value)
    raise AssertionError(f"{name} has no default in settings.py")


class TheSecondGraderTest(SimpleTestCase):
    def test_the_default_candidates_are_deepseek_then_haiku(self):
        self.assertEqual(
            _settings_default("GRADING_SECOND_OPINION_MODELS"), [DEEPSEEK, HAIKU]
        )

    def test_no_production_file_names_gemini_2_5_flash(self):
        found = []
        for app in (
            "AutoGrader",
            "ai_processor",
            "assignments",
            "audit",
            "billing",
            "classrooms",
            "dashboard",
            "students",
            "users",
        ):
            for path in (BASE / app).rglob("*.py"):
                relative = path.relative_to(BASE).as_posix()
                if (
                    "/tests" in relative
                    or "/test_" in relative
                    or "/migrations/" in relative
                    or relative.startswith("ai_processor/benchmark/")
                ):
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if re.search(r"gemini-2\.5-flash\b", text):
                    found.append(relative)
        self.assertEqual(found, [])
