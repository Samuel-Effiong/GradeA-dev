"""The S4 guard (plan 08 §5.2): no bulk write can skip grade, roster,
permission or subscription history silently.

A queryset `.update()` or `bulk_update()` fires no model signal, so the
history receivers in `audit.history` never see it. Every such call in
production code whose fields include a TRACKED field must go through
`audit.history.record_bulk` instead. A call whose fields can't be read
statically (`.update(**changes)`, a `bulk_update` field list held in a name) is
flagged too, and must be listed in ALLOWED with a reason. The style follows
the H-38 sweep (`classrooms/tests_teacher_access_sweep.py`).

A tracked field is any field in `audit.history.REGISTRY`, under either
spelling (`plan` / `plan_id`). The name check is deliberately model-blind: an
`.update(is_active=...)` on an untracked model is flagged as well, and is
either routed or allow-listed with a reason, so a new tracked model never
inherits old unreviewed writes.
"""

import ast
from pathlib import Path
from typing import Optional, cast

from django.conf import settings
from django.test import SimpleTestCase

from audit.history import REGISTRY

REPO = Path(settings.BASE_DIR)
SKIP_PREFIXES = ("docs/", "static/", "media/", "node_modules/", ".", "scripts/")


def tracked_field_names():
    names = set()
    for spec in REGISTRY:
        for attname in spec.fields:
            names.add(attname)
            if attname.endswith("_id"):
                names.add(attname[: -len("_id")])
    return names


# (file, enclosing function) -> why this bulk write needs no history.
ALLOWED = {
    ("audit/history.py", "record_bulk"): (
        "the helper itself: it reads before, updates and emits the history"
    ),
    ("billing/immutable.py", "update"): (
        "the append-only guard's own QuerySet.update override (CreditLedger, "
        "CreditUsageLog): it refuses illegal fields, then delegates"
    ),
    ("billing/price_reconciliation.py", "_apply_stripe_values"): (
        "SubscriptionPlan catalogue prices from Stripe: a plan definition, "
        "not a subscription (untracked model)"
    ),
    ("assignments/management/commands/backfill_assignment_rigor.py", "_flush"): (
        "Assignment rigor backfill: RIGOR_FIELDS on Assignment (untracked " "model)"
    ),
    (
        "assignments/management/commands/repair_question_blooms_levels.py",
        "_flush",
    ): "Assignment questions + RIGOR_FIELDS repair (untracked model)",
}


def _enclosing_function(parents, node):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return "<module>"


def scan_source(source, tracked):
    """[(line, function, reason)] for every bulk write that needs history
    or a reviewed exemption."""
    tree = ast.parse(source)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        method = node.func.attr
        reason: Optional[str]
        if method == "update":
            if any(keyword.arg is None for keyword in node.keywords):
                reason = "fields not statically known"
            else:
                hit = {k.arg for k in node.keywords} & tracked
                reason = f"tracked fields {sorted(hit)}" if hit else None
        elif method == "bulk_update" and len(node.args) >= 2:
            fields = node.args[1]
            if isinstance(fields, (ast.List, ast.Tuple)) and all(
                isinstance(e, ast.Constant) for e in fields.elts
            ):
                hit = {cast(ast.Constant, e).value for e in fields.elts} & tracked
                reason = f"tracked fields {sorted(hit)}" if hit else None
            else:
                reason = "fields not statically known"
        else:
            continue
        if reason:
            found.append((node.lineno, _enclosing_function(parents, node), reason))
    return found


def production_files():
    for file in sorted(REPO.rglob("*.py")):
        rel = file.relative_to(REPO).as_posix()
        name = file.name
        if (
            rel.startswith(SKIP_PREFIXES)
            or "/migrations/" in rel
            or "site-packages" in rel
            or "/tests/" in rel
            or name == "tests.py"
            or name.startswith(("test_", "tests_"))
            or name.startswith("settings_worktree")
        ):
            continue
        yield rel, file


def scan_repository():
    tracked = tracked_field_names()
    found = []
    for rel, file in production_files():
        for line, function, reason in scan_source(file.read_text(), tracked):
            found.append((rel, line, function, reason))
    return found


# `history.suppressed()` switches history off: it must never be used quietly
# in production code (SM condition). (file, enclosing function) -> why.
SUPPRESSION_ALLOWED = {
    ("audit/history.py", "record_bulk"): (
        "the helper's own update: it writes one event per changed row itself, "
        "so the signals must not write a second"
    ),
    ("students/services.py", "_populate_and_save_grade"): (
        "the AI grading save (SM note 2): its before/after go onto the one "
        "GRADING_COMPLETED event, never a GRADE_CHANGE"
    ),
    ("audit/bench_history.py", "test_print_the_cost"): (
        "the Gate 6 benchmark's capture-off baseline; run by label only, "
        "never in the suite or in production"
    ),
}


def scan_suppression(source):
    """[(line, function)] for every call of `suppressed()` or
    `<anything>.suppressed()`."""
    tree = ast.parse(source)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        )
        if name == "suppressed":
            found.append((node.lineno, _enclosing_function(parents, node)))
    return found


def suppression_sites():
    return [
        (rel, line, function)
        for rel, file in production_files()
        for line, function in scan_suppression(file.read_text())
    ]


class HistorySuppressionIsNamedTests(SimpleTestCase):
    """SM condition: the audit off-switch has only named, justified uses."""

    def test_no_unlisted_production_use_of_suppressed(self):
        unlisted = [
            f"{rel}:{line} in {function}()"
            for rel, line, function in suppression_sites()
            if (rel, function) not in SUPPRESSION_ALLOWED
        ]
        self.assertEqual(
            unlisted,
            [],
            "history.suppressed() turns audit history off. Don't; or list the "
            "call in SUPPRESSION_ALLOWED with the reason, for review.",
        )

    def test_the_suppression_allow_list_has_no_stale_entries(self):
        seen = {(rel, function) for rel, _, function in suppression_sites()}
        self.assertEqual(sorted(set(SUPPRESSION_ALLOWED) - seen), [])

    def test_the_scanner_finds_both_spellings(self):
        source = (
            "def a():\n    with history.suppressed():\n        pass\n"
            "def b():\n    with suppressed():\n        pass\n"
            "def c():\n    history.record_bulk(qs, x=1)\n"
        )
        self.assertEqual(scan_suppression(source), [(2, "a"), (5, "b")])


class BulkWritesKeepHistoryTests(SimpleTestCase):
    def test_no_unlisted_bulk_write_of_a_tracked_field(self):
        unlisted = [
            f"{rel}:{line} in {function}(): {reason}"
            for rel, line, function, reason in scan_repository()
            if (rel, function) not in ALLOWED
        ]
        self.assertEqual(
            unlisted,
            [],
            "a queryset write skips the history signals. Route it through "
            "audit.history.record_bulk, or list it in ALLOWED with a reason.",
        )

    def test_the_allow_list_has_no_stale_entries(self):
        seen = {(rel, function) for rel, _, function, _ in scan_repository()}
        self.assertEqual(sorted(set(ALLOWED) - seen), [])

    def test_every_allow_list_entry_has_a_reason(self):
        for key, reason in ALLOWED.items():
            self.assertTrue(reason.strip(), key)

    def test_the_tracked_names_cover_every_registered_field(self):
        tracked = tracked_field_names()
        for spec in REGISTRY:
            for attname in spec.fields:
                self.assertIn(attname, tracked)
        self.assertIn("plan", tracked)  # an FK by its field name too


class ScannerSelfTests(SimpleTestCase):
    """The scanner flags every shape it exists to catch, and nothing else."""

    TRACKED = {"is_published", "score", "enrollment_status", "plan", "plan_id"}

    def flagged(self, source):
        return [reason for _, _, reason in scan_source(source, self.TRACKED)]

    def test_a_tracked_update_is_flagged(self):
        self.assertEqual(
            self.flagged("qs.filter(pk=1).update(is_published=True)\n"),
            ["tracked fields ['is_published']"],
        )

    def test_a_multi_line_update_and_an_fk_name_are_flagged(self):
        source = "Sub.objects.filter(user=u).update(\n    plan=new_plan,\n)\n"
        self.assertEqual(self.flagged(source), ["tracked fields ['plan']"])

    def test_a_tracked_bulk_update_is_flagged(self):
        self.assertEqual(
            self.flagged("Model.objects.bulk_update(rows, ['score', 'feedback'])\n"),
            ["tracked fields ['score']"],
        )

    def test_unreadable_fields_are_flagged(self):
        self.assertEqual(
            self.flagged("qs.update(**changes)\nM.objects.bulk_update(rows, FIELDS)\n"),
            ["fields not statically known", "fields not statically known"],
        )

    def test_the_enclosing_function_is_reported(self):
        source = "def publish():\n    qs.update(is_published=True)\n"
        self.assertEqual(
            scan_source(source, self.TRACKED),
            [(2, "publish", "tracked fields ['is_published']")],
        )

    def test_an_untracked_update_and_record_bulk_are_not_flagged(self):
        source = (
            "qs.update(grading_state='RUNNING')\n"
            "history.record_bulk(qs, is_published=True)\n"
            "M.objects.bulk_update(rows, ['feedback'])\n"
            "d.update({'score': 1})\n"
        )
        self.assertEqual(self.flagged(source), [])
