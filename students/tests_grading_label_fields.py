"""BE-I-04, slice A: the six label columns on StudentSubmission.

Nothing writes them yet (the label is written with the grade in a later
slice). What this slice must hold:

  * every row that exists, and every row older code inserts after a
    code-only rollback, reads "unlabelled" - from the DATABASE's default,
    not from Django's;
  * the columns cannot be edited in the admin screen;
  * nothing the frontend receives changes;
  * the founder's condition: each column says it is the first form of the
    record and that a later stage improves on it.
"""

from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.db import connection, models
from django.test import RequestFactory, SimpleTestCase, TestCase

from students import grading_label
from students.grading_label import FIRST_FORM_NOTE, LABEL_FIELDS, UNLABELLED
from students.models import StudentSubmission


def _column(name) -> models.CharField:
    field = StudentSubmission._meta.get_field(name)
    assert isinstance(field, models.CharField), field
    return field


MAX_LENGTHS = {
    "grading_prompt_version": 128,
    "grading_config_version": 128,
    "grading_strictness": 32,
    "grading_model": 255,
    "grading_fallback_used": 16,
    "grading_release": 64,
}


class TheSixColumnsTest(SimpleTestCase):
    def test_there_are_exactly_these_six(self):
        self.assertEqual(
            LABEL_FIELDS,
            (
                "grading_prompt_version",
                "grading_config_version",
                "grading_strictness",
                "grading_model",
                "grading_fallback_used",
                "grading_release",
            ),
        )
        self.assertEqual(set(MAX_LENGTHS), set(LABEL_FIELDS))

    def test_each_is_a_not_null_text_column_of_the_designed_length(self):
        for name in LABEL_FIELDS:
            with self.subTest(name=name):
                field = _column(name)
                self.assertEqual(field.get_internal_type(), "CharField")
                self.assertFalse(field.null)
                self.assertEqual(field.max_length, MAX_LENGTHS[name])
                self.assertFalse(field.db_index)  # type: ignore[attr-defined]

    def test_each_has_the_placeholder_as_both_defaults(self):
        for name in LABEL_FIELDS:
            with self.subTest(name=name):
                field = _column(name)
                self.assertEqual(field.default, UNLABELLED)
                self.assertTrue(field.has_db_default())  # type: ignore[attr-defined]
                self.assertEqual(field.db_default, UNLABELLED)

    def test_a_new_unsaved_submission_reads_the_placeholder(self):
        submission = StudentSubmission()
        for name in LABEL_FIELDS:
            with self.subTest(name=name):
                self.assertEqual(getattr(submission, name), UNLABELLED)

    def test_the_placeholder_fits_every_column(self):
        for name in LABEL_FIELDS:
            with self.subTest(name=name):
                self.assertLessEqual(len(UNLABELLED), MAX_LENGTHS[name])

    def test_every_word_a_grading_can_write_fits_its_column(self):
        words = {
            "grading_strictness": [grading_label.STRICTNESS_NOT_YET_SET],
            "grading_model": [
                grading_label.MODEL_DETERMINISTIC,
                grading_label.MODEL_UNKNOWN,
            ],
            "grading_fallback_used": [
                grading_label.FALLBACK_YES,
                grading_label.FALLBACK_NO,
                grading_label.FALLBACK_UNKNOWN,
                grading_label.FALLBACK_NOT_APPLICABLE,
            ],
            "grading_release": [grading_label.RELEASE_NONE],
        }
        for name, values in words.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    self.assertLessEqual(len(value), MAX_LENGTHS[name])

    def test_no_grading_word_is_the_placeholder(self):
        """ "unlabelled" must mean only "no label recorded"."""
        written = {
            grading_label.STRICTNESS_NOT_YET_SET,
            grading_label.MODEL_DETERMINISTIC,
            grading_label.MODEL_UNKNOWN,
            grading_label.FALLBACK_YES,
            grading_label.FALLBACK_NO,
            grading_label.FALLBACK_UNKNOWN,
            grading_label.FALLBACK_NOT_APPLICABLE,
            grading_label.RELEASE_NONE,
        }
        self.assertNotIn(UNLABELLED, written)


class TheFoundersConditionTest(SimpleTestCase):
    """2026-10-06: the code must say this is the first form of the record
    and that a later stage improves on it."""

    def test_the_sentence_says_first_form_and_names_the_later_table(self):
        self.assertIn("First form of the grading record", FIRST_FORM_NOTE)
        self.assertIn("A later stage improves on it", FIRST_FORM_NOTE)
        self.assertIn("table of grading runs", FIRST_FORM_NOTE)
        self.assertIn("filled from these fields", FIRST_FORM_NOTE)

    def test_each_column_carries_the_sentence_in_its_help_text(self):
        for name in LABEL_FIELDS:
            with self.subTest(name=name):
                field = _column(name)
                self.assertIn(FIRST_FORM_NOTE, str(field.help_text))

    def test_the_model_and_both_modules_say_it_too(self):
        base = Path(settings.BASE_DIR)
        for relative in (
            "students/models.py",
            "students/grading_label.py",
            "ai_processor/grading_config.py",
        ):
            with self.subTest(file=relative):
                text = " ".join((base / relative).read_text(encoding="utf-8").split())
                text = text.replace("# ", "")
                self.assertIn("FIRST FORM OF THE GRADING RECORD", text)
                self.assertIn("A later stage improves on it", text)
                self.assertIn("table of grading runs", text)


class TheArchitectureDocumentsSayItTest(SimpleTestCase):
    """The founder's condition names the architecture documents too."""

    DOCUMENTS = (
        "03a_data_model.md",
        "05_epics_b_to_i_roadmap.md",
        "07_epic_i1_implementation_plan.md",
    )

    def test_each_document_says_first_form_and_names_the_later_table(self):
        folder = Path(settings.BASE_DIR) / "docs" / "phase2" / "architecture"
        for name in self.DOCUMENTS:
            with self.subTest(document=name):
                text = (folder / name).read_text(encoding="utf-8")
                text = " ".join(text.replace("\n> ", "\n").split()).lower()
                self.assertIn("first form of the grading record", text)
                self.assertIn("a later stage improves on it", text)
                self.assertIn("table of grading runs", text)
                self.assertIn("be-i-04", text)


class TheDatabaseHoldsTheDefaultTest(TestCase):
    """Rule 11 (H-56): older code, after a code-only rollback, inserts rows
    without naming these columns. Only a default in the DATABASE fills
    them then."""

    def test_each_column_is_not_null_with_the_placeholder_as_its_default(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, is_nullable, column_default, "
                "character_maximum_length FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = ANY(%s)",
                [StudentSubmission._meta.db_table, list(LABEL_FIELDS)],
            )
            rows = {row[0]: row[1:] for row in cursor.fetchall()}
        self.assertEqual(set(rows), set(LABEL_FIELDS))
        for name in LABEL_FIELDS:
            with self.subTest(name=name):
                nullable, default, length = rows[name]
                self.assertEqual(nullable, "NO")
                self.assertIsNotNone(default)
                self.assertTrue(
                    default.startswith(repr(UNLABELLED)),
                    f"database default is {default!r}",
                )
                self.assertEqual(length, MAX_LENGTHS[name])

    def test_no_index_was_added_for_them(self):
        """An index on the busiest table is its own careful job; none in
        this stage."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename = %s",
                [StudentSubmission._meta.db_table],
            )
            definitions = " ".join(row[0] for row in cursor.fetchall())
        for name in LABEL_FIELDS:
            with self.subTest(name=name):
                self.assertNotIn(name, definitions)


class TheAdminScreenTest(SimpleTestCase):
    def test_the_six_columns_are_read_only_there(self):
        model_admin = admin.site._registry[StudentSubmission]
        for name in LABEL_FIELDS:
            with self.subTest(name=name):
                self.assertIn(name, model_admin.readonly_fields)

    def test_they_are_read_only_for_a_real_request_object_too(self):
        model_admin = admin.site._registry[StudentSubmission]
        request = RequestFactory().get("/admin/")
        read_only = model_admin.get_readonly_fields(request, obj=None)
        self.assertTrue(set(LABEL_FIELDS) <= set(read_only))


class NothingTheFrontendReceivesChangesTest(SimpleTestCase):
    """No screen and no API response changes in this stage. No serializer
    names a label column, and none exposes every model field."""

    SERIALIZER_FILES = (
        "students/serializers.py",
        "students/second_opinion_serializers.py",
        "assignments/serializers.py",
        "classrooms/serializers.py",
        "dashboard/serializers.py",
    )

    def test_no_serializer_file_names_a_label_column(self):
        base = Path(settings.BASE_DIR)
        for relative in self.SERIALIZER_FILES:
            path = base / relative
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for name in LABEL_FIELDS:
                with self.subTest(file=relative, name=name):
                    self.assertNotIn(name, text)

    def test_no_serializer_of_the_submission_exposes_all_fields(self):
        from rest_framework import serializers as drf

        from students import serializers as students_serializers

        for attribute in vars(students_serializers).values():
            if not (
                isinstance(attribute, type)
                and issubclass(attribute, drf.ModelSerializer)
            ):
                continue
            meta = getattr(attribute, "Meta", None)
            if getattr(meta, "model", None) is not StudentSubmission:
                continue
            with self.subTest(serializer=attribute.__name__):
                self.assertNotEqual(getattr(meta, "fields", None), "__all__")
                self.assertIsNone(getattr(meta, "exclude", None))
