"""
billing/tests/test_zero_credit_registry.py
==========================================
Every declared zero-credit operation must be exercised through
``assert_operation_is_zero_credit`` somewhere in the test tree (FR-B-10: "a test
per operation"). Starts trivially green (only ``_selftest`` is registered) and
bites the moment an epic registers a name without writing its test.
"""

import ast
from pathlib import Path

from django.test import SimpleTestCase

from billing.zero_credit import ZERO_CREDIT_OPERATIONS

ROOT = Path(__file__).resolve().parents[2]
HELPER = "assert_operation_is_zero_credit"


def _operations_covered():
    covered = set()
    for path in ROOT.rglob("*.py"):
        parts = set(path.parts)
        if {"venv", ".venv", "node_modules", "migrations"} & parts:
            continue
        name = path.name
        if not (name.startswith("test") or "tests" in parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", None)) == HELPER
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                covered.add(node.args[1].value)
    return covered


class ZeroCreditRegistryTests(SimpleTestCase):
    def test_every_registered_operation_has_a_helper_test(self):
        import billing.tests.test_zero_credit_scope  # noqa: F401  (registers nothing)

        # `_selftest` is the scope's own fixture name, exercised by test_zero_credit_scope.
        missing = sorted(ZERO_CREDIT_OPERATIONS - {"_selftest"} - _operations_covered())
        self.assertEqual(
            missing,
            [],
            "Registered zero-credit operations with no assert_operation_is_zero_credit "
            f"test: {missing}",
        )
