"""BE-I-04: the settings version on every grade.

`GradingConfig.read()` reads every setting that can change a grade once;
`version` is one short code for that reading. These tests hold the four
things the label depends on:

  * the list is complete - a GRADING_* setting added to settings.py and
    forgotten here fails a test, loudly, whatever the environment sets;
  * every listed setting and code constant moves the version, and the
    three deliberately left out do not;
  * a reading does not change after it is taken;
  * the release is recorded beside the version and never inside it.
"""

import ast
import re
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.db import models
from django.test import SimpleTestCase, override_settings

from ai_processor import grading_config, services
from ai_processor.grading_config import (
    CODE_CONSTANTS,
    GRADE_SHAPING_SETTINGS,
    NOT_GRADE_SHAPING,
    GradingConfig,
)
from students import grading_label

#: A value different from the default of each grade-shaping setting (and
#: from anything an environment is likely to set), of the setting's type.
OTHER_VALUE = {
    "GRADING_CUSTOM_INSTRUCTIONS_ENABLED": "be-i-04-other",
    "GRADING_DETERMINISTIC_OBJECTIVE": "be-i-04-other",
    "GRADING_DISAGREEMENT_CRITICAL_FRACTION": 0.123456,
    "GRADING_DISAGREEMENT_MODERATE_FRACTION": 0.123456,
    "GRADING_EVIDENCE_ENFORCEMENT": "be-i-04-other",
    "GRADING_MAX_IMAGES_PER_CALL": 123456,
    "GRADING_RESPONSE_SCHEMA_ENABLED": "be-i-04-other",
    "GRADING_SECOND_OPINION_ENABLED": "be-i-04-other",
    "GRADING_SECOND_OPINION_HIGH_POINTS": 123456,
    "GRADING_SECOND_OPINION_MIN_CONFIDENCE": 123456,
    "GRADING_SECOND_OPINION_MODELS": ["be-i-04/other-model"],
    "GRADING_SECOND_OPINION_ON_BORDERLINE": "be-i-04-other",
    "GRADING_SECOND_OPINION_SAMPLE_RATE": 0.123456,
    "GRADING_SECOND_OPINION_SUBJECTIVE_TYPES": ["be-i-04-other-type"],
}

OTHER_CONSTANT = {
    "AI_CONFIDENCE_THRESHOLD": 123456,
    "GRADING_FALLBACK_MODELS": ["be-i-04/other-backup"],
    "GRADING_QUESTIONS_PER_CHUNK": 123456,
    "MAIN_MODEL": "be-i-04/other-main",
}

#: The fixed reading the pinned version below is computed from: every
#: setting and constant set explicitly, so the pin does not depend on what
#: the environment of a run overrides.
PINNED_SETTINGS = {
    "GRADING_CUSTOM_INSTRUCTIONS_ENABLED": True,
    "GRADING_DETERMINISTIC_OBJECTIVE": True,
    "GRADING_DISAGREEMENT_CRITICAL_FRACTION": 0.5,
    "GRADING_DISAGREEMENT_MODERATE_FRACTION": 0.25,
    "GRADING_EVIDENCE_ENFORCEMENT": "strict",
    "GRADING_MAX_IMAGES_PER_CALL": 5,
    "GRADING_RESPONSE_SCHEMA_ENABLED": True,
    "GRADING_SECOND_OPINION_ENABLED": True,
    "GRADING_SECOND_OPINION_HIGH_POINTS": 15,
    "GRADING_SECOND_OPINION_MIN_CONFIDENCE": 80,
    "GRADING_SECOND_OPINION_MODELS": ["pinned/second"],
    "GRADING_SECOND_OPINION_ON_BORDERLINE": True,
    "GRADING_SECOND_OPINION_SAMPLE_RATE": 0.05,
    "GRADING_SECOND_OPINION_SUBJECTIVE_TYPES": ["essay", "short_answer"],
}
PINNED_CONSTANTS = {
    "AI_CONFIDENCE_THRESHOLD": 80,
    "GRADING_FALLBACK_MODELS": ["pinned/backup"],
    "GRADING_QUESTIONS_PER_CHUNK": 10,
    "MAIN_MODEL": "pinned/main",
}
PINNED_VERSION = "cfg:c039947043de"


def _grading_names_in_settings_file():
    """Every GRADING_* name assigned at the top level of settings.py, read
    from the file's syntax tree: what the file DECLARES, not what one
    environment happens to hold."""
    source = (Path(settings.BASE_DIR) / "AutoGrader" / "settings.py").read_text(
        encoding="utf-8"
    )
    names = set()
    for node in ast.parse(source).body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id.startswith("GRADING_"):
                names.add(target.id)
    return names


class TheListIsCompleteTest(SimpleTestCase):
    def test_every_grading_setting_is_in_the_version_or_named_as_left_out(self):
        declared = _grading_names_in_settings_file()
        self.assertGreaterEqual(len(declared), 17, declared)
        unclassified = declared - set(GRADE_SHAPING_SETTINGS) - set(NOT_GRADE_SHAPING)
        self.assertEqual(
            unclassified,
            set(),
            "A GRADING_* setting is in neither GRADE_SHAPING_SETTINGS nor "
            "NOT_GRADE_SHAPING (ai_processor/grading_config.py). If it can "
            "change a grade it must be in the settings version (BE-I-04); "
            "if it cannot, say why in NOT_GRADE_SHAPING.",
        )

    def test_no_name_is_in_both_lists(self):
        self.assertEqual(set(GRADE_SHAPING_SETTINGS) & set(NOT_GRADE_SHAPING), set())

    def test_every_listed_name_is_a_declared_setting(self):
        declared = _grading_names_in_settings_file()
        listed = set(GRADE_SHAPING_SETTINGS) | set(NOT_GRADE_SHAPING)
        self.assertEqual(listed - declared, set())

    def test_every_listed_constant_exists_in_the_grading_service(self):
        for name in CODE_CONSTANTS:
            with self.subTest(name=name):
                self.assertTrue(hasattr(services, name))

    def test_the_other_values_cover_every_name(self):
        """The loops below prove nothing for a name they skip."""
        self.assertEqual(set(OTHER_VALUE), set(GRADE_SHAPING_SETTINGS))
        self.assertEqual(set(OTHER_CONSTANT), set(CODE_CONSTANTS))
        self.assertEqual(set(PINNED_SETTINGS), set(GRADE_SHAPING_SETTINGS))
        self.assertEqual(set(PINNED_CONSTANTS), set(CODE_CONSTANTS))


class TheVersionMovesWithEverySettingTest(SimpleTestCase):
    def test_each_grade_shaping_setting_changes_the_version(self):
        before = GradingConfig.read().version
        for name in GRADE_SHAPING_SETTINGS:
            with self.subTest(name=name):
                with override_settings(**{name: OTHER_VALUE[name]}):
                    self.assertNotEqual(GradingConfig.read().version, before)

    def test_each_code_constant_changes_the_version(self):
        before = GradingConfig.read().version
        for name in CODE_CONSTANTS:
            with self.subTest(name=name):
                with patch.object(services, name, OTHER_CONSTANT[name]):
                    self.assertNotEqual(GradingConfig.read().version, before)

    def test_the_saved_answer_switch_and_lifetime_do_not_change_it(self):
        before = GradingConfig.read().version
        with override_settings(
            GRADING_ANSWER_CACHE_ENABLED="be-i-04-other",
            GRADING_ANSWER_CACHE_TTL_SECONDS=123456,
        ):
            self.assertEqual(GradingConfig.read().version, before)

    def test_the_order_of_a_model_list_is_part_of_the_version(self):
        with override_settings(GRADING_SECOND_OPINION_MODELS=["a/one", "b/two"]):
            one_two = GradingConfig.read().version
        with override_settings(GRADING_SECOND_OPINION_MODELS=["b/two", "a/one"]):
            two_one = GradingConfig.read().version
        self.assertNotEqual(one_two, two_one)

    def test_the_same_settings_give_the_same_version_twice(self):
        self.assertEqual(GradingConfig.read().version, GradingConfig.read().version)

    def test_the_version_for_a_fixed_reading_is_pinned(self):
        """Fails when the way a version is computed changes: every version
        already stored on a grade would then stop matching its settings.
        Change the pin only together with a note in the evidence."""
        with ExitStack() as stack:
            stack.enter_context(override_settings(**PINNED_SETTINGS))
            for name, value in PINNED_CONSTANTS.items():
                stack.enter_context(patch.object(services, name, value))
            self.assertEqual(GradingConfig.read().version, PINNED_VERSION)


class TheVersionsShapeTest(SimpleTestCase):
    def test_it_is_the_prefix_and_twelve_hex_digits(self):
        self.assertRegex(GradingConfig.read().version, r"\Acfg:[0-9a-f]{12}\Z")

    def test_audit_metadata_would_keep_it(self):
        """Audit metadata drops any string shaped like an email address
        (audit/metadata.py), so no '@', and within its string limit."""
        from audit import metadata

        version = GradingConfig.read().version
        self.assertIsNone(metadata._EMAIL_SHAPED.search(version))
        self.assertLessEqual(len(version), metadata.MAX_STRING)

    def test_it_fits_the_column(self):
        from students.models import StudentSubmission

        column = StudentSubmission._meta.get_field("grading_config_version")
        assert isinstance(column, models.CharField)
        self.assertLessEqual(len(GradingConfig.read().version), column.max_length or 0)


class AReadingDoesNotChangeTest(SimpleTestCase):
    def test_a_setting_changed_after_the_reading_is_not_seen(self):
        reading = GradingConfig.read()
        version = reading.version
        images = reading.get("GRADING_MAX_IMAGES_PER_CALL")
        with override_settings(GRADING_MAX_IMAGES_PER_CALL=123456):
            self.assertEqual(reading.get("GRADING_MAX_IMAGES_PER_CALL"), images)
            self.assertEqual(reading.version, version)
            self.assertNotEqual(GradingConfig.read().version, version)

    def test_a_list_setting_mutated_after_the_reading_is_not_seen(self):
        models = ["a/one", "b/two"]
        with override_settings(GRADING_SECOND_OPINION_MODELS=models):
            reading = GradingConfig.read()
            version = reading.version
            models.append("c/three")
            self.assertEqual(
                reading.get("GRADING_SECOND_OPINION_MODELS"), ("a/one", "b/two")
            )
            self.assertEqual(reading.version, version)

    def test_get_returns_what_was_read(self):
        with override_settings(GRADING_EVIDENCE_ENFORCEMENT="log"):
            reading = GradingConfig.read()
        self.assertEqual(reading.get("GRADING_EVIDENCE_ENFORCEMENT"), "log")
        self.assertEqual(reading.get("MAIN_MODEL"), services.MAIN_MODEL)

    def test_get_refuses_a_name_the_version_does_not_cover(self):
        reading = GradingConfig.read()
        for name in ("GRADING_ANSWER_CACHE_ENABLED", "DEBUG", "NO_SUCH_SETTING"):
            with self.subTest(name=name):
                with self.assertRaises(KeyError):
                    reading.get(name)

    def test_a_reading_cannot_be_assigned_to(self):
        reading = GradingConfig.read()
        with self.assertRaises(AttributeError):
            reading.release = "changed"


class TheReleaseIsBesideTheVersionTest(SimpleTestCase):
    def test_no_release_from_the_host_is_the_word_none(self):
        for empty in ("", "   ", None):
            with self.subTest(empty=empty):
                with override_settings(GRADING_RELEASE_ID=empty):
                    self.assertEqual(GradingConfig.read().release, "none")

    def test_the_word_matches_the_labels(self):
        self.assertEqual(grading_config.RELEASE_NONE, grading_label.RELEASE_NONE)

    def test_a_release_from_the_host_is_recorded_as_given(self):
        with override_settings(GRADING_RELEASE_ID="  release-42  "):
            self.assertEqual(GradingConfig.read().release, "release-42")

    def test_a_long_release_is_cut_to_the_column(self):
        from students.models import StudentSubmission

        column = StudentSubmission._meta.get_field("grading_release")
        with override_settings(GRADING_RELEASE_ID="r" * 200):
            release = GradingConfig.read().release
        assert isinstance(column, models.CharField)
        self.assertEqual(release, "r" * (column.max_length or 0))
        self.assertEqual(len(release), grading_config.RELEASE_MAX_LENGTH)

    def test_the_release_never_changes_the_version(self):
        with override_settings(GRADING_RELEASE_ID=""):
            without = GradingConfig.read().version
        with override_settings(GRADING_RELEASE_ID="release-42"):
            with_release = GradingConfig.read()
        self.assertEqual(with_release.version, without)
        self.assertNotIn("release-42", with_release.version)
        self.assertNotIn("release-42", repr(with_release.values))


class TheModuleStaysLightTest(SimpleTestCase):
    def test_it_does_not_import_the_grading_service_at_module_level(self):
        """ai_processor.services imports grading_cache, which will import
        this module (slice B): a module-level import here would be a cycle."""
        source = Path(grading_config.__file__).read_text(encoding="utf-8")
        top_level = [
            node
            for node in ast.parse(source).body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        imported = " ".join(ast.unparse(node) for node in top_level)
        self.assertIsNone(re.search(r"\bservices\b|\bstudents\b", imported), imported)
