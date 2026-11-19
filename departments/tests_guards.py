"""Slice 7: structural guards (plan 5.1, 5.3, 7.3). They fail the build when
a later change widens what a department exposes, by name."""

import ast
import pathlib
from types import SimpleNamespace

from django.apps import apps
from django.db import models
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from departments.tests_factories import (
    add_member,
    make_department,
    make_school_with_license,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPARTMENTS = ROOT / "departments"

#: Files that name a department model only as a label string, never to read
#: rows (the activity-log label table, the student data map).
LABEL_ONLY_FILES = {"audit/serializers.py", "students/student_data_map.py"}

#: Bare columns that hold a department id by design (a captured value).
BARE_COLUMN_ALLOW = {("audit.AuditEvent", "department_id")}


def source_files():
    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        if parts[0] in {"departments", "venv", "node_modules", "staticfiles"}:
            continue
        if parts[0].startswith(".") or "migrations" in parts:
            continue
        name = path.name
        if name.startswith("test") or name == "settings_worktree.py":
            continue
        yield path


class SerializerGuardTests(SimpleTestCase):
    def tree(self):
        return ast.parse((DEPARTMENTS / "serializers.py").read_text())

    def test_no_serializer_is_imported_from_any_other_app(self):
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotRegex(node.module, r"(^|\.)serializers$", node.module)
                self.assertNotIn(
                    node.module.split(".")[0], {"users", "billing", "classrooms"}
                )

    def test_no_model_serializer_uses_all_or_exclude(self):
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Constant) and node.value == "__all__":
                self.fail("a serializer uses fields = '__all__'")
            if isinstance(node, ast.Name) and node.id == "exclude":
                self.fail("a serializer uses exclude")

    def test_every_model_serializer_names_its_fields(self):
        for node in ast.walk(self.tree()):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
            if "ModelSerializer" in bases:
                meta = [
                    n
                    for n in node.body
                    if isinstance(n, ast.ClassDef) and n.name == "Meta"
                ]
                names = {
                    t.id
                    for m in meta
                    for a in m.body
                    if isinstance(a, ast.Assign)
                    for t in a.targets
                    if isinstance(t, ast.Name)
                }
                self.assertIn("fields", names, node.name)


class AccessSweepTests(SimpleTestCase):
    def test_nothing_outside_departments_reaches_membership_rows(self):
        offenders = []
        for path in source_files():
            if str(path.relative_to(ROOT)) in LABEL_ONLY_FILES:
                continue
            text = path.read_text(errors="ignore")
            for needle in (
                "DepartmentMembership",
                "departments.models",
                "department__",
                "memberships__",
            ):
                if needle in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {needle}")
        self.assertEqual(offenders, [])


def unregistered_department_columns(fields):
    """The registry guard: a relation to Department must not cascade
    silently, and a bare column holding a department id must be allow-listed.
    `fields` are (model_label, field) pairs."""
    problems = []
    for label, field in fields:
        if label.startswith("departments."):
            continue
        related = getattr(field, "related_model", None)
        if related is not None and getattr(related, "__name__", "") == "Department":
            if (
                getattr(getattr(field, "remote_field", None), "on_delete", None)
                is not models.PROTECT
            ):
                problems.append(
                    f"{label}.{field.name}: a relation to Department must be PROTECT"
                )
            continue
        if "department" in field.name and (label, field.name) not in BARE_COLUMN_ALLOW:
            problems.append(f"{label}.{field.name}: a bare department column")
    return problems


class RegistryGuardTests(SimpleTestCase):
    def all_fields(self):
        for model in apps.get_models():
            for field in model._meta.get_fields():
                if getattr(field, "concrete", False):
                    yield model._meta.label, field

    def test_no_model_outside_departments_points_at_a_department(self):
        self.assertEqual(unregistered_department_columns(self.all_fields()), [])

    def test_the_guard_fires_on_a_bare_column_and_on_a_cascading_relation(self):
        bare = SimpleNamespace(name="department_id", related_model=None)
        cascading = SimpleNamespace(
            name="department",
            related_model=type("Department", (), {}),
            remote_field=SimpleNamespace(on_delete=models.CASCADE),
        )
        self.assertEqual(len(unregistered_department_columns([("x.Y", bare)])), 1)
        self.assertEqual(len(unregistered_department_columns([("x.Y", cascading)])), 1)


@override_settings(DEPARTMENTS_ENABLED=True)
class PayloadKeySetTests(TestCase):
    """Exact key sets, not a negative assertNotIn (plan 5.3)."""

    def test_my_departments_and_roster_expose_exactly_their_fields(self):
        school, admin, _license, (t1, t2) = make_school_with_license()
        dept = make_department(school, "Maths")
        add_member(dept, t1)
        add_member(dept, t2)
        client = APIClient()
        client.force_authenticate(t1)
        mine = client.get("/api/v1/departments").json()["data"]
        self.assertEqual(
            {frozenset(row) for row in mine}, {frozenset({"id", "name", "can_edit"})}
        )
        roster = client.get(f"/api/v1/departments/{dept.id}/roster").json()["data"]
        self.assertEqual(
            {frozenset(row) for row in roster}, {frozenset({"name", "role"})}
        )
        self.assertEqual({row["role"] for row in roster}, {"School admin", "Teacher"})
        text = str(roster) + str(mine)
        for forbidden in (t1.email, t2.email, admin.email):
            self.assertNotIn(forbidden, text)
