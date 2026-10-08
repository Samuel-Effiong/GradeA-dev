"""AI-call record, slice 2: the version of the settings that shape a reading.

A student's answers are read from the paper by an AI call, then checked for
completeness, then the blank ones are read again. Which settings were in
force decides what the teacher's saved answers are. `ReadingConfig` is that
reading's version, built as `GradingConfig` is for the grading:

  * "rcfg:" and twelve hex characters;
  * it changes when ANY setting or constant that shapes a reading changes,
    and only then;
  * it is read ONCE at the top of an extraction and every site uses that
    reading, so a setting that changes while the extraction runs cannot split
    one reading between two versions;
  * nothing outside the module reads an ANSWER_* setting;
  * a new setting whose name starts with ANSWER_ cannot be added without
    deciding whether it shapes a reading (a guard).

Every mocked provider reply here carries a real string `model`, or None on
purpose (rule 14).
"""

import ast
import json
import re
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from ai_processor import reading_config, services
from ai_processor.extraction_schemas import ANSWERED
from ai_processor.reading_config import (
    READING_NOT_SHAPING,
    READING_SHAPING_SETTINGS,
    ReadingConfig,
)
from ai_processor.reading_run import ReadingRun
from users.models import CustomUser, UserTypes

BASE = Path(settings.BASE_DIR)
PROJECT_APPS = (
    "AutoGrader",
    "ai_processor",
    "assignments",
    "audit",
    "billing",
    "classrooms",
    "dashboard",
    "students",
    "users",
)

SIX = (
    "ANSWER_BLANK_VERIFICATION_ENABLED",
    "ANSWER_BLANK_VERIFICATION_MAX_PAGES",
    "ANSWER_BLANK_VERIFICATION_MAX_QUESTIONS",
    "ANSWER_BLANK_VERIFICATION_MODEL",
    "ANSWER_COMPLETENESS_ENFORCEMENT",
    "ANSWER_EXTRACTION_SCHEMA_ENABLED",
)

#: A different value for each setting, never equal to the default.
ALTERNATIVE = {
    "ANSWER_BLANK_VERIFICATION_ENABLED": False,
    "ANSWER_BLANK_VERIFICATION_MAX_PAGES": 3,
    "ANSWER_BLANK_VERIFICATION_MAX_QUESTIONS": 2,
    "ANSWER_BLANK_VERIFICATION_MODEL": "other/recheck-model",
    "ANSWER_COMPLETENESS_ENFORCEMENT": "log",
    "ANSWER_EXTRACTION_SCHEMA_ENABLED": False,
}


class TheVersionTest(SimpleTestCase):
    def test_the_six_settings_are_these_six(self):
        self.assertEqual(tuple(sorted(READING_SHAPING_SETTINGS)), SIX)

    def test_the_version_is_rcfg_and_twelve_hex_characters(self):
        self.assertRegex(ReadingConfig.read().version, r"^rcfg:[0-9a-f]{12}$")

    def test_the_same_settings_give_the_same_version(self):
        self.assertEqual(ReadingConfig.read().version, ReadingConfig.read().version)

    def test_it_fits_the_designed_column(self):
        self.assertLessEqual(len(ReadingConfig.read().version), 128)

    def test_each_of_the_six_settings_changes_the_version(self):
        base = ReadingConfig.read().version
        for name in SIX:
            with self.subTest(name=name):
                with override_settings(**{name: ALTERNATIVE[name]}):
                    self.assertNotEqual(ReadingConfig.read().version, base)

    def test_an_unrelated_setting_does_not_change_the_version(self):
        base = ReadingConfig.read().version
        with override_settings(GRADING_ANSWER_CACHE_ENABLED=False, DEBUG=True):
            self.assertEqual(ReadingConfig.read().version, base)

    def test_a_grade_shaping_setting_is_not_part_of_the_reading(self):
        """The reading is its own version: a change to how an answer is
        graded is not a change to how a paper was read."""
        base = ReadingConfig.read().version
        with override_settings(GRADING_QUESTIONS_PER_CHUNK=3):
            self.assertEqual(ReadingConfig.read().version, base)

    def test_each_constant_that_shapes_a_reading_changes_the_version(self):
        base = ReadingConfig.read().version
        constants = {
            "MAIN_MODEL": "other/main",
            "GRADING_FALLBACK_MODELS": ["other/backup"],
            "ANSWERS_EXTRACTION_PAGES_PER_CHUNK": 7,
            "AI_TEMPERATURE": 0.9,
        }
        for name, value in constants.items():
            with self.subTest(name=name):
                with patch.object(services, name, value):
                    self.assertNotEqual(ReadingConfig.read().version, base)

    def test_the_rechecks_instruction_version_changes_the_version(self):
        base = ReadingConfig.read().version
        changed = SimpleNamespace(version="blank_instruction:changed")
        with patch.object(services, "BLANK_VERIFICATION_INSTRUCTION", changed):
            self.assertNotEqual(ReadingConfig.read().version, base)

    def test_a_reading_cannot_be_changed_afterwards(self):
        config = ReadingConfig.read()
        with self.assertRaises(FrozenInstanceError):
            config.release = "changed"  # type: ignore[misc]
        with override_settings(ANSWER_BLANK_VERIFICATION_ENABLED=False):
            self.assertTrue(config.get("ANSWER_BLANK_VERIFICATION_ENABLED"))

    def test_a_name_the_reading_does_not_cover_is_refused(self):
        with self.assertRaises(KeyError):
            ReadingConfig.read().get("GRADING_QUESTIONS_PER_CHUNK")

    def test_the_release_is_the_hosts_release_or_none(self):
        with override_settings(GRADING_RELEASE_ID=""):
            self.assertEqual(ReadingConfig.read().release, "none")
        with override_settings(GRADING_RELEASE_ID="  2026.10.08-rc1 "):
            self.assertEqual(ReadingConfig.read().release, "2026.10.08-rc1")
        with override_settings(GRADING_RELEASE_ID="r" * 200):
            self.assertEqual(len(ReadingConfig.read().release), 64)

    def test_the_release_is_not_part_of_the_version(self):
        base = ReadingConfig.read().version
        with override_settings(GRADING_RELEASE_ID="another-release"):
            self.assertEqual(ReadingConfig.read().version, base)

    def test_the_none_word_equals_the_grading_one(self):
        from ai_processor import grading_config

        self.assertEqual(reading_config.RELEASE_NONE, grading_config.RELEASE_NONE)


class TheGuardOnNewSettingsTest(SimpleTestCase):
    def test_every_setting_starting_with_answer_is_decided(self):
        """A new ANSWER_* setting fails this test until it is in the list of
        settings that shape a reading, or in the list that does not (with a
        reason)."""
        named = {name for name in dir(settings) if name.startswith("ANSWER_")}
        decided = set(READING_SHAPING_SETTINGS) | set(READING_NOT_SHAPING)
        self.assertEqual(named - decided, set(), "decide the new setting")
        self.assertEqual(decided - named, set(), "a listed name is not a setting")

    def test_the_two_lists_do_not_overlap(self):
        self.assertEqual(
            set(READING_SHAPING_SETTINGS) & set(READING_NOT_SHAPING), set()
        )

    def test_each_name_not_shaping_has_a_reason(self):
        for name, reason in dict(READING_NOT_SHAPING).items():
            with self.subTest(name=name):
                self.assertTrue(str(reason).strip())


class NothingElseReadsAnAnswerSettingTest(SimpleTestCase):
    """Static: the only code that reads an ANSWER_* setting is the module
    that builds the reading. The reading code asks the reading."""

    ALLOWED = {"ai_processor/reading_config.py", "AutoGrader/settings.py"}
    PATTERN = re.compile(r"""["']ANSWER_[A-Z_]+["']|settings\.ANSWER_""")

    def test_no_other_file_names_an_answer_setting(self):
        found = []
        for app in PROJECT_APPS:
            for path in (BASE / app).rglob("*.py"):
                relative = path.relative_to(BASE).as_posix()
                if (
                    relative in self.ALLOWED
                    or "/tests" in relative
                    or "/test_" in relative
                    or "/migrations/" in relative
                    or relative.startswith("ai_processor/benchmark/")
                ):
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if self.PATTERN.search(text):
                    found.append(relative)
        self.assertEqual(found, [])


QUESTIONS = [
    {"question_number": 1, "question_text": "Q1", "points": 5},
    {"question_number": 2, "question_text": "Q2", "points": 5},
]
SUPER = CustomUser(user_type=UserTypes.SUPER_ADMIN, is_superuser=True)
IMAGE = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,PAGE"}}


def _reply(payload, model):
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(total_tokens=10),
    )


def _answers(*, blank_first=False):
    return {
        "student_name": "Ada",
        "answers": [
            {
                "question_number": 1,
                "answer_html": "" if blank_first else "x",
                "answer_status": "BLANK" if blank_first else ANSWERED,
            },
            {"question_number": 2, "answer_html": "y", "answer_status": ANSWERED},
        ],
    }


class OneReadingPerExtractionTest(SimpleTestCase):
    """The reading is taken once, at the top of extract_answer_with_retry,
    and every site uses it."""

    def _run(self, provider_script, **kwargs):
        calls = []

        def create(**request):
            calls.append(request)
            return provider_script(len(calls))

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        run = ReadingRun()
        with patch.object(services.ai_processor, "client", client):
            result = services.ai_processor.extract_answer_with_retry(  # type: ignore[call-arg]
                SUPER,
                [{"type": "text", "text": "paper"}, IMAGE],
                "ctx",
                assignment_model=SimpleNamespace(questions=QUESTIONS),
                reading_run=run,
                **kwargs,
            )
        return result, run, calls

    def test_the_settings_are_read_once_for_the_whole_extraction(self):
        reads = []
        real = ReadingConfig.read.__func__  # type: ignore[attr-defined]

        def counting(cls):
            reads.append(1)
            return real(cls)

        def script(n):
            return _reply(_answers(blank_first=True), "read/model")

        with patch.object(ReadingConfig, "read", classmethod(counting)):
            self._run(script)
        self.assertEqual(len(reads), 1)

    def test_a_setting_changed_while_the_extraction_runs_is_not_seen(self):
        """The provider's first reply flips the re-check off. The extraction
        began with it on, so the re-check still runs and the label says the
        version the extraction began with."""
        begun = ReadingConfig.read().version
        flipped = override_settings(ANSWER_BLANK_VERIFICATION_ENABLED=False)

        def script(n):
            if n == 1:
                flipped.enable()
                self.addCleanup(flipped.disable)
                return _reply(_answers(blank_first=True), "read/model")
            return _reply({"findings": []}, "check/model")

        result, run, calls = self._run(script)
        self.assertEqual(len(calls), 2, "the re-check began on, so it ran")
        self.assertEqual(run.config.version, begun)
        self.assertEqual(
            run.label_fields(result["answers"])["answers_config_version"], begun
        )

    def test_the_label_names_the_version_the_extraction_began_with(self):
        with override_settings(ANSWER_COMPLETENESS_ENFORCEMENT="log"):
            expected = ReadingConfig.read().version

            def script(n):
                return _reply(_answers(), "read/model")

            result, run, _ = self._run(script)
        self.assertEqual(
            run.label_fields(result["answers"])["answers_config_version"], expected
        )


class NoReadingSiteAsksTheSettingsLiveTest(SimpleTestCase):
    """Static, on the code of the reading: none of its functions calls
    getattr(settings, ...) or reads django.conf settings at all. They are
    handed the reading."""

    FUNCTIONS = (
        "_extract_answers_chunked",
        "extract_answer_image",
        "_answer_extraction_schema",
        "_verify_blank_answers",
        "_answer_completeness_mode",
        "extract_answer_with_retry",
    )

    def test_no_function_of_the_reading_reads_settings(self):
        source = (BASE / "ai_processor/services.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in self.FUNCTIONS:
                found[node.name] = node
        self.assertEqual(set(found), set(self.FUNCTIONS))
        for name, node in found.items():
            with self.subTest(function=name):
                reads = [
                    sub.lineno
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.Name) and sub.id == "settings"
                ]
                self.assertEqual(reads, [], "reads settings live")
