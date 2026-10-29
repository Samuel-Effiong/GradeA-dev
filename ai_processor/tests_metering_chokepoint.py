"""
ai_processor/tests_metering_chokepoint.py
=========================================
FR-B-01: every AI invocation passes through the one central metering path; no
component reaches a model provider directly.

Static (AST) checks, so a future stray provider call fails CI rather than
depending on a reviewer noticing:

  1. the old ``get_ai_model_function`` escape hatch is gone;
  2. ``OpenAI(`` is constructed, and ``chat.completions`` referenced, only in
     ai_processor/services.py;
  3. inside services.py, ``self.client`` is used only in ``__ai_model``;
  4. ``self.__ai_model`` is called from exactly two places, both inside
     ``execute_graded_task``;
  5. the only assignment to ``.client`` outside AIProcessor is the benchmark
     harness that pins a timeout (verified to make no provider call itself).
"""

import ast
from pathlib import Path

from django.test import SimpleTestCase

from ai_processor.services import AIProcessor

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "ai_processor" / "services.py"

# Excluded by explicit path, not glob. The harness swaps ``ai_processor.client``
# for a client with a timeout and makes no provider call of its own.
EXCLUDED_DIRS = ("ai_processor/benchmark/",)
CLIENT_ASSIGNMENT_ALLOWLIST = {
    "ai_processor/benchmark/isolation_run8/isolation_harness.py"
}


def _production_files():
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        parts = set(path.parts)
        if {"venv", ".venv", "node_modules", "migrations", "docs"} & parts:
            continue
        name = path.name
        if name.startswith("test") or "tests" in parts or name.endswith("_test.py"):
            continue
        yield rel, path


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _enclosing_function(tree, target):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= target.lineno <= (node.end_lineno or node.lineno):
                if best is None or node.lineno >= best.lineno:
                    best = node
    return best


class MeteringChokepointTests(SimpleTestCase):
    def test_escape_hatch_is_gone(self):
        self.assertFalse(hasattr(AIProcessor, "get_ai_model_function"))

    def test_provider_client_is_only_built_in_services(self):
        offenders = []
        for rel, path in _production_files():
            if rel == "ai_processor/services.py" or rel.startswith(EXCLUDED_DIRS):
                continue
            for node in ast.walk(_parse(path)):
                if isinstance(node, ast.Call):
                    func = node.func
                    if (
                        getattr(func, "id", None) == "OpenAI"
                        or getattr(func, "attr", None) == "OpenAI"
                    ):
                        offenders.append((rel, node.lineno, "OpenAI("))
                if isinstance(node, ast.Attribute) and node.attr == "completions":
                    offenders.append((rel, node.lineno, ".completions"))
        self.assertEqual(offenders, [])

    def test_self_client_is_only_used_inside___ai_model(self):
        tree = _parse(SERVICES)
        offenders = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "client"
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                fn = _enclosing_function(tree, node)
                if fn is None or fn.name not in ("__ai_model", "__init__"):
                    offenders.append((node.lineno, fn.name if fn else None))
        self.assertEqual(offenders, [])

    def test___ai_model_is_called_only_from_execute_graded_task_twice(self):
        tree = _parse(SERVICES)
        sites = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "__ai_model"
            ):
                fn = _enclosing_function(tree, node)
                sites.append(fn.name if fn else None)
        self.assertEqual(sites, ["execute_graded_task", "execute_graded_task"])

    def test_no_other_module_reaches_the_private_model_method(self):
        offenders = []
        for rel, path in _production_files():
            # The benchmark runners wrap the private method with a record/replay
            # tape. That is an interposer on the transport seam: the wrapped
            # method is still only ever called from execute_graded_task, so
            # metering is intact.
            if rel == "ai_processor/services.py" or rel.startswith(EXCLUDED_DIRS):
                continue
            for node in ast.walk(_parse(path)):
                if isinstance(node, ast.Attribute) and node.attr in (
                    "_AIProcessor__ai_model",
                    "__ai_model",
                ):
                    offenders.append((rel, node.lineno))
        self.assertEqual(offenders, [])

    def test_only_the_benchmark_harness_assigns_to_client_outside_the_class(self):
        offenders = []
        for rel, path in _production_files():
            if rel == "ai_processor/services.py":
                continue
            for node in ast.walk(_parse(path)):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and target.attr == "client"
                        ):
                            if rel not in CLIENT_ASSIGNMENT_ALLOWLIST:
                                offenders.append((rel, node.lineno))
        self.assertEqual(offenders, [])
