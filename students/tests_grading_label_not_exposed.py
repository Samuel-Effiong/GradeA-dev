"""BE-I-04: nothing the frontend receives changes.

No serializer of a submission, in any app, exposes one of the six label
columns, and none exposes every model field. Slice A checked the files by
name; this is the stronger form the Checker asked for: every serializer
class that the project defines for `StudentSubmission` is found by walking
the installed apps, and its real fields are read.
"""

import importlib
import pkgutil

from django.apps import apps
from django.test import SimpleTestCase
from rest_framework import serializers as drf

from students.grading_label import LABEL_FIELDS
from students.models import StudentSubmission

PROJECT_APPS = (
    "ai_processor",
    "assignments",
    "audit",
    "billing",
    "classrooms",
    "dashboard",
    "students",
    "users",
)


def _serializer_modules():
    for label in PROJECT_APPS:
        package = importlib.import_module(apps.get_app_config(label).name)
        for info in pkgutil.iter_modules(package.__path__):
            if "serializer" in info.name and not info.name.startswith("tests"):
                yield importlib.import_module(f"{package.__name__}.{info.name}")


def _serializer_classes():
    seen = set()
    for module in _serializer_modules():
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, drf.BaseSerializer)
                and value.__module__ == module.__name__
                and value not in seen
            ):
                seen.add(value)
                yield value


def _is_for_a_submission(serializer_class):
    meta = getattr(serializer_class, "Meta", None)
    return getattr(meta, "model", None) is StudentSubmission


class NoSerializerExposesTheLabelTest(SimpleTestCase):
    def test_the_walk_finds_the_submission_serializers(self):
        """Guard on the guard: the walk is not blind."""
        found = [c for c in _serializer_classes() if _is_for_a_submission(c)]
        self.assertGreaterEqual(len(found), 2, found)

    def test_no_submission_serializer_has_a_label_field(self):
        for serializer_class in _serializer_classes():
            if not _is_for_a_submission(serializer_class):
                continue
            with self.subTest(serializer=serializer_class.__qualname__):
                # Read from the class, not from an instance: some of these
                # serializers need a request to be built. With an explicit
                # `fields` list (the next test refuses anything else) the
                # list and the declared fields are everything exposed.
                listed = getattr(serializer_class.Meta, "fields", ())
                self.assertIsInstance(listed, (list, tuple))
                fields = set(listed) | set(serializer_class._declared_fields)
                self.assertEqual(fields & set(LABEL_FIELDS), set())

    def test_no_submission_serializer_exposes_all_fields_or_excludes(self):
        for serializer_class in _serializer_classes():
            if not _is_for_a_submission(serializer_class):
                continue
            with self.subTest(serializer=serializer_class.__qualname__):
                meta = serializer_class.Meta
                self.assertNotEqual(getattr(meta, "fields", None), "__all__")
                self.assertIsNone(getattr(meta, "exclude", None))

    def test_no_serializer_anywhere_declares_a_field_named_like_a_label(self):
        """Also the serializers that are not built on the model: a plain
        serializer could carry a label column under its own name."""
        for serializer_class in _serializer_classes():
            declared = set(getattr(serializer_class, "_declared_fields", {}))
            with self.subTest(serializer=serializer_class.__qualname__):
                self.assertEqual(declared & set(LABEL_FIELDS), set())
