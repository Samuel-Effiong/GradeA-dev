"""Test 14 (plan rev 3; BE-A-01 privacy): no log line or metric in the upload
quote modules is given a file name. Names live only in the quote file row and,
at confirm, in each item's own file_name column."""

import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

MODULES = (
    "students/upload_quote_confirm.py",
    "students/upload_quote_measure.py",
    "students/upload_quote_service.py",
    "students/upload_quote_views.py",
    "students/upload_quotes.py",
)
LOGGING = {"debug", "info", "warning", "error", "exception", "critical", "count"}
NAMES = {"original_name", "file_name", "filename", "name"}


def named_calls(source):
    """The logging or metric calls whose arguments mention a file name."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOGGING
        ):
            continue
        parts = [*node.args, *(k.value for k in node.keywords)]
        parts += [ast.Constant(k.arg) for k in node.keywords if k.arg]
        for part in parts:
            for inner in ast.walk(part):
                if isinstance(inner, ast.Attribute) and inner.attr in NAMES:
                    found.append(node.lineno)
                elif isinstance(inner, ast.Name) and inner.id in NAMES:
                    found.append(node.lineno)
                elif isinstance(inner, ast.Constant) and inner.value in NAMES:
                    found.append(node.lineno)
    return found


class NoFileNameInLogs(SimpleTestCase):
    def test_no_logger_or_metric_call_is_given_a_file_name(self):
        for name in MODULES:
            source = (Path(settings.BASE_DIR) / name).read_text()
            with self.subTest(module=name):
                self.assertEqual(named_calls(source), [])

    def test_the_guard_sees_a_planted_name(self):
        planted = "logger.error('bad %s', row.original_name)\nmetrics.count(file_name)"
        self.assertEqual(named_calls(planted), [1, 2])
        self.assertEqual(named_calls("logger.error('plain')"), [])
