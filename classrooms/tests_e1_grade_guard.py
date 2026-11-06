"""Epic E1a: grade maths lives in `classrooms.grade_service` only (FR-E-06).

FR-E-06: "a mutation adding a second implementation fails a test". This is
that test. It parses every non-test Python file in the repository and fails
on any of these outside `classrooms/grade_service.py`:

  G1  Avg(...) or Sum(...) over a grade field: the last `__` segment of the
      field (a string, or the string inside F()) is exactly score,
      score_percentage, max_points or final_grade.
  G2  Coalesce("max_points", ...): the weight fallback (H-33), as SQL.
  G3  `x.max_points if ... else y.total_points`: the weight fallback, in
      Python.
  G4  A division whose left side names a score and whose right side names
      points (`total_score / total_points`): a hand-rolled weighted grade.
  G5  sum(...) / len(...) where the expression names a score or a
      percentage: a hand-rolled plain mean.
  G6  Importing `final_grade_from` or `classrooms.final_grade` (the module
      E1a removed).

Exact field names, so `Avg("conf_score")`, `Avg("grading_confidence")` and
turnaround averages are not grade maths and do not match.

Every remaining hit outside the service must be in ALLOWED below, with the
reason it is not a second grade formula. An allow-list entry that no
longer matches anything fails too, so the list cannot rot.
"""

import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

GRADE_FIELDS = {"score", "score_percentage", "max_points", "final_grade"}
SERVICE = "classrooms/grade_service.py"
SKIP_PARTS = {
    "migrations",
    "node_modules",
    "static",
    "staticfiles",
    "media",
    "__pycache__",
}
SKIP_PREFIXES = ("docs/evidence/", "ai_processor/benchmark/")

# (path, enclosing function, rule) -> reason. Keep each reason honest: an
# entry says why the hit is NOT a second implementation of a grade figure.
ALLOWED = {
    (
        "ai_processor/services.py",
        "generate_student_summary",
        "G5",
    ): "Lane 1, reclassify when the Fixes Worker's B1a lands. A plain mean of "
    "percentages (round 1) written into the AI summary prompt text; it is "
    "not shown as a grade figure. Out of E1a's lane.",
    (
        "ai_processor/services.py",
        "_finalize_grading_result",
        "G4",
    ): "Lane 1. One submission's own percentage (its score over its own "
    "maximum) at grading time: the input every grade figure reads, not a "
    "course or aggregate figure.",
    (
        "students/views.py",
        "update_grade",
        "G4",
    ): "One submission's own percentage when a teacher edits its score: the "
    "input every grade figure reads, not a course or aggregate figure.",
    (
        "ai_processor/management/commands/grading_eval.py",
        "_score_band",
        "G4",
    ): "Grading-evaluation tool: bands one submission's score for an AI "
    "accuracy report. Not a product grade figure.",
    (
        "assignments/serializers.py",
        "get_student_submissions",
        "G3",
    ): "Display maximum, not the weight rule: `max_points if submission else "
    "total_points` picks the submission's maximum when a submission exists, "
    "even when it is NULL, and is never a weight.",
}


def _repo_root():
    return Path(settings.BASE_DIR)


def _is_test_file(rel):
    name = rel.name
    return (
        name.startswith("tests")
        or name.startswith("test_")
        or name == "conftest.py"
        or "tests" in rel.parts[:-1]
    )


def _source_files(root):
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0].startswith("."):
            continue
        if SKIP_PARTS & set(rel.parts):
            continue
        if str(rel).startswith(SKIP_PREFIXES):
            continue
        if _is_test_file(rel):
            continue
        yield rel, path


def _field_of(node):
    """The field path an aggregate argument names, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and _callee(node) == "F" and node.args:
        return _field_of(node.args[0])
    return None


def _callee(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _names_in(node):
    """Every identifier, attribute name and string subscript key in node,
    lower-cased, as one string to search."""
    parts = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            parts.append(sub.id)
        elif isinstance(sub, ast.Attribute):
            parts.append(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            parts.append(sub.value)
    return " ".join(parts).lower()


class _Scanner(ast.NodeVisitor):
    def __init__(self):
        self.hits = []  # (lineno, function, rule, snippet)
        self.stack = []
        self.aliases = {"Avg": "Avg", "Sum": "Sum", "Coalesce": "Coalesce"}

    def _function(self):
        return self.stack[-1] if self.stack else "<module>"

    def _hit(self, node, rule):
        self.hits.append((node.lineno, self._function(), rule))

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ImportFrom(self, node):
        module = node.module or ""
        for alias in node.names:
            if alias.name in ("Avg", "Sum", "Coalesce") and alias.asname:
                self.aliases[alias.asname] = alias.name
            if alias.name == "final_grade_from" or (
                module.endswith("classrooms") and alias.name == "final_grade"
            ):
                self._hit(node, "G6")
        relative_final_grade = node.level and module == "final_grade"
        if relative_final_grade or module.endswith("classrooms.final_grade"):
            self._hit(node, "G6")
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name == "classrooms.final_grade":
                self._hit(node, "G6")
        self.generic_visit(node)

    def visit_Call(self, node):
        kind = self.aliases.get(_callee(node))
        if kind in ("Avg", "Sum") and node.args:
            field = _field_of(node.args[0])
            if field and field.split("__")[-1] in GRADE_FIELDS:
                self._hit(node, "G1")
        if kind == "Coalesce" and node.args:
            field = _field_of(node.args[0])
            if field and field.split("__")[-1] == "max_points":
                self._hit(node, "G2")
        self.generic_visit(node)

    def visit_IfExp(self, node):
        if (
            isinstance(node.body, ast.Attribute)
            and node.body.attr == "max_points"
            and isinstance(node.orelse, ast.Attribute)
            and node.orelse.attr == "total_points"
        ):
            self._hit(node, "G3")
        self.generic_visit(node)

    def visit_BinOp(self, node):
        if isinstance(node.op, ast.Div):
            left, right = _names_in(node.left), _names_in(node.right)
            is_sum_len = (
                isinstance(node.left, ast.Call)
                and _callee(node.left) == "sum"
                and isinstance(node.right, ast.Call)
                and _callee(node.right) == "len"
            )
            if is_sum_len and ("score" in left + right or "percent" in left + right):
                self._hit(node, "G5")
            elif "score" in left and "point" in right:
                self._hit(node, "G4")
        self.generic_visit(node)


def scan_source(text):
    scanner = _Scanner()
    scanner.visit(ast.parse(text))
    return scanner.hits


def scan_repo(root=None):
    root = root or _repo_root()
    found = []
    for rel, path in _source_files(root):
        if str(rel) == SERVICE:
            continue
        for lineno, function, rule in scan_source(path.read_text(encoding="utf-8")):
            found.append((str(rel), lineno, function, rule))
    return found


class GradeMathsLivesInTheServiceTest(SimpleTestCase):
    def test_no_grade_maths_outside_the_service(self):
        found = scan_repo()
        unexpected = [hit for hit in found if (hit[0], hit[2], hit[3]) not in ALLOWED]
        self.assertEqual(
            unexpected,
            [],
            "Grade maths outside classrooms/grade_service.py. Route it through "
            "the service, or (only if it is not a grade figure) add it to "
            "ALLOWED with the reason:\n"
            + "\n".join(f"  {p}:{n} in {f}() [{r}]" for p, n, f, r in unexpected),
        )

    def test_every_allow_list_entry_still_matches(self):
        keys = {(p, f, r) for p, _, f, r in scan_repo()}
        stale = [key for key in ALLOWED if key not in keys]
        self.assertEqual(stale, [], "ALLOWED entries that no longer match anything")

    def test_the_service_exists(self):
        self.assertTrue((_repo_root() / SERVICE).exists())


class GuardCatchesBypassesTest(SimpleTestCase):
    """The scanner, written from the bypass side (plan 09 §6 Gate 4)."""

    def rules(self, source):
        return [rule for _, _, rule in scan_source(source)]

    def test_aggregates_over_grade_fields(self):
        self.assertEqual(self.rules('Avg("submissions__score_percentage")'), ["G1"])
        self.assertEqual(self.rules('models.Avg("final_grade")'), ["G1"])
        self.assertEqual(self.rules('Sum(F("score"))'), ["G1"])
        self.assertEqual(self.rules('Sum("max_points")'), ["G1"])
        self.assertEqual(
            self.rules('from django.db.models import Avg as Mean\nMean("score")'),
            ["G1"],
        )
        self.assertEqual(self.rules('qs.aggregate(**{"x": Avg("score")})'), ["G1"])

    def test_non_grade_aggregates_do_not_match(self):
        for source in (
            'Avg("conf_score")',
            'Avg("grading_confidence")',
            'Avg("turnaround_time")',
            'Sum("amount")',
            'Avg("scores_total")',
        ):
            self.assertEqual(self.rules(source), [], source)

    def test_weight_fallbacks(self):
        self.assertEqual(
            self.rules('Coalesce("max_points", "assignment__total_points")'), ["G2"]
        )
        self.assertEqual(
            self.rules(
                "s.max_points if s.max_points is not None else s.assignment.total_points"
            ),
            ["G3"],
        )

    def test_hand_rolled_formulas(self):
        self.assertEqual(self.rules("total_score / total_points"), ["G4"])
        self.assertEqual(self.rules("sum(scores) / len(scores)"), ["G5"])
        self.assertEqual(
            self.rules("sum(float(s.score_percentage) for s in xs) / len(xs)"), ["G5"]
        )
        self.assertEqual(self.rules("sum(gpas) / len(gpas)"), [])

    def test_old_module_imports(self):
        self.assertEqual(
            self.rules("from classrooms.final_grade import final_grade_from"),
            ["G6", "G6"],
        )
        self.assertEqual(self.rules("from .final_grade import x"), ["G6"])
        self.assertEqual(self.rules("import classrooms.final_grade"), ["G6"])
