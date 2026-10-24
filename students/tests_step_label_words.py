"""AI-call record, slice 0: the words the step labels can hold, in the
students app.

`students/step_label.py` holds only words, as `students/grading_label.py`
does for the grading: it imports nothing from the project, so the models,
their migrations, the services and the audit code can all read it. The
AI-processor keeps its own copy (it must not import the students app) and
`ai_processor/tests_step_run.py` compares the two.
"""

import ast
from pathlib import Path

from django.test import SimpleTestCase

from students import grading_label, step_label


class TheStepWordsTest(SimpleTestCase):
    def test_the_words(self):
        self.assertEqual(step_label.UNLABELLED, "unlabelled")
        self.assertEqual(step_label.MODEL_UNKNOWN, "unknown")
        self.assertEqual(step_label.NOT_RUN, "not_run")
        self.assertEqual(step_label.FAILED, "failed")
        self.assertEqual(step_label.CONFIRMED_BLANK, "confirmed_blank")
        self.assertEqual(step_label.FOUND_WRITING, "found_writing")
        self.assertEqual(step_label.SOURCE_EXTRACTED, "extracted")
        self.assertEqual(step_label.SOURCE_GENERATED, "generated")

    def test_the_gradings_shared_words_are_the_same_words(self):
        self.assertEqual(step_label.UNLABELLED, grading_label.UNLABELLED)
        self.assertEqual(step_label.MODEL_UNKNOWN, grading_label.MODEL_UNKNOWN)

    def test_the_fixed_model_words(self):
        self.assertEqual(
            step_label.FIXED_MODEL_WORDS,
            frozenset({"unlabelled", "unknown", "not_run"}),
        )

    def test_the_recheck_results_are_these_four_and_unlabelled(self):
        self.assertEqual(
            step_label.RECHECK_RESULTS,
            ("not_run", "failed", "confirmed_blank", "found_writing"),
        )

    def test_the_sources_are_these_two(self):
        self.assertEqual(step_label.SOURCES, ("extracted", "generated"))

    def test_every_word_fits_the_narrowest_column_it_may_be_written_to(self):
        words = [
            step_label.UNLABELLED,
            step_label.MODEL_UNKNOWN,
            step_label.NOT_RUN,
            step_label.FAILED,
            step_label.CONFIRMED_BLANK,
            step_label.FOUND_WRITING,
            step_label.SOURCE_EXTRACTED,
            step_label.SOURCE_GENERATED,
        ]
        # content_source is CharField(16); answers_recheck_result is (32).
        for word in words:
            with self.subTest(word=word):
                self.assertLessEqual(len(word), 16)


class TheModuleImportsNothingFromTheProjectTest(SimpleTestCase):
    def test_no_project_import(self):
        source = Path(step_label.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        for name in imported:
            with self.subTest(name=name):
                self.assertFalse(
                    name.split(".")[0]
                    in {
                        "students",
                        "assignments",
                        "ai_processor",
                        "billing",
                        "audit",
                        "classrooms",
                        "users",
                        "AutoGrader",
                        "django",
                    },
                    name,
                )
