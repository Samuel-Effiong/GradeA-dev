"""Every cached student payload that shows a course roster keys on a scope
an enrolment bumps.

An enrolment write bumps a fixed five scopes (classrooms/signals.py
`clear_student_course_cache`), not every classmate's own generation. So a
cached student payload that shows a roster (a serializer's `students` or
`student_count`, or one that nests or subclasses such a serializer) stays
fresh only if its key carries a scope that write bumps and that is not the
viewer's own: the course's `crs` generation, or `global`. A roster-bearing
payload keyed on the viewer alone would show a stale classmate list for the
cache TTL, and nothing else would notice.

This static sweep finds every roster-bearing serializer, then every cached
view class (a `UserCacheMixin` viewset, or one calling `versioned_key`) that
uses one. For each, it requires:

* a `UserCacheMixin` viewset defines `extra_cache_scopes` naming
  SCOPE_COURSE (or SCOPE_GLOBAL); and
* every `versioned_key(...)` call in the class names SCOPE_COURSE or
  SCOPE_GLOBAL in its scope list;

unless the class is allow-listed with a reason. An allow-list entry that no
longer matches a roster-bearing cached view fails too, so the list cannot
go stale (the same rule as the H-38 sweep).

The dynamic half is classrooms/tests_cache_course_roster_scope.py.
"""

import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

REPO = Path(settings.BASE_DIR)

ROSTER_FIELDS = {"students", "student_count"}
ROSTER_SCOPES = {"SCOPE_COURSE", "SCOPE_GLOBAL"}

#: "path::ClassName" -> why it may cache a roster without a roster scope.
#: The criterion for a staff-only entry is narrow on purpose: its viewer is
#: never a student, so the per-classmate fan-out this sweep replaced never
#: reached its keys either, and removing it changes nothing for them.
#: Whether such a staff count is itself fresh after an enrolment is its own
#: family's concern (docs/HARDENING_BACKLOG.md H-1 coverage map), not this
#: sweep's.
ALLOWED = {
    "classrooms/views.py::SchoolViewSet": (
        "school admins and superadmins only, never a student: the removed "
        "classmate fan-out never covered these keys"
    ),
    "classrooms/views.py::CourseCategoryViewSet": (
        "never routed (HARDENING_BACKLOG H-6): no API surface, and its "
        "category_courses action is not cached"
    ),
    "dashboard/views.py::SuperAdminDashboardView": (
        "superadmins only, never a student: the removed classmate fan-out "
        "never covered these keys"
    ),
    "dashboard/views.py::SchoolAdminDashboardView": (
        "school admins only, never a student: the removed classmate fan-out "
        "never covered these keys (an enrolment still bumps their school)"
    ),
}


def _is_test_path(rel):
    name = rel.rsplit("/", 1)[-1]
    return (
        "/tests/" in rel or name == "tests.py" or name.startswith(("test_", "tests_"))
    )


def production_modules():
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if (
            rel.startswith(("docs/", "static/", "media/", "node_modules/", "."))
            or "/migrations/" in rel
            or "site-packages" in rel
            or _is_test_path(rel)
        ):
            continue
        yield rel, ast.parse(path.read_text(), filename=rel)


def _base_names(node):
    return [getattr(b, "id", None) or getattr(b, "attr", None) for b in node.bases]


def _is_serializer(node):
    return any(b and "Serializer" in b for b in _base_names(node))


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def roster_serializers(modules):
    """Serializer classes that declare a roster field, or nest or subclass
    one that does (to a fixed point)."""
    serializers = []
    for _rel, tree in modules:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _is_serializer(node):
                serializers.append(node)
    roster = set()
    for node in serializers:
        for statement in node.body:
            if isinstance(statement, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in ROSTER_FIELDS
                for t in statement.targets
            ):
                roster.add(node.name)
    grew = True
    while grew:
        grew = False
        for node in serializers:
            if (
                node.name not in roster
                and (_names(node) | set(_base_names(node))) & roster
            ):
                roster.add(node.name)
                grew = True
    return roster


def _versioned_key_scope_names(call):
    """Scope constant names in a versioned_key(...) call's scope list."""
    if len(call.args) < 2:
        return set()
    return _names(call.args[1]) | {
        n.id
        for kw in call.keywords
        if kw.arg == "scopes"
        for n in ast.walk(kw.value)
        if isinstance(n, ast.Name)
    }


def problems_in_class(node):
    """Why this roster-bearing cached view class is not keyed on a roster
    scope; an empty list when it is."""
    problems = []
    if "UserCacheMixin" in _base_names(node):
        hook = next(
            (
                item
                for item in node.body
                if isinstance(item, ast.FunctionDef)
                and item.name == "extra_cache_scopes"
            ),
            None,
        )
        if hook is None:
            problems.append("UserCacheMixin without extra_cache_scopes")
        elif not _names(hook) & ROSTER_SCOPES:
            problems.append(
                "extra_cache_scopes names neither SCOPE_COURSE nor SCOPE_GLOBAL"
            )
    for call in ast.walk(node):
        if (
            isinstance(call, ast.Call)
            and getattr(call.func, "id", getattr(call.func, "attr", None))
            == "versioned_key"
            and not _versioned_key_scope_names(call) & ROSTER_SCOPES
        ):
            problems.append(f"versioned_key at line {call.lineno} has no roster scope")
    return problems


def roster_bearing_cached_views(modules, roster):
    """{"path::ClassName": [problems]} for every cached view class that
    uses a roster-bearing serializer."""
    found = {}
    for rel, tree in modules:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or _is_serializer(node):
                continue
            names = _names(node)
            if not names & roster:
                continue
            cached = "UserCacheMixin" in _base_names(node) or any(
                isinstance(c, ast.Call)
                and getattr(c.func, "id", getattr(c.func, "attr", None))
                == "versioned_key"
                for c in ast.walk(node)
            )
            if cached:
                found[f"{rel}::{node.name}"] = problems_in_class(node)
    return found


class CourseRosterScopeSweepTests(SimpleTestCase):
    def setUp(self):
        self.modules = list(production_modules())
        self.roster = roster_serializers(self.modules)
        self.found = roster_bearing_cached_views(self.modules, self.roster)

    def test_the_sweep_sees_course_serializer_as_roster_bearing(self):
        self.assertIn("CourseSerializer", self.roster)
        self.assertIn("classrooms/views.py::CourseViewSet", self.found)

    def test_every_roster_bearing_cached_view_keys_on_a_roster_scope(self):
        unexplained = {
            site: problems
            for site, problems in self.found.items()
            if problems and site not in ALLOWED
        }
        self.assertEqual(
            unexplained,
            {},
            "cached payloads that show a course roster, keyed without a scope "
            "an enrolment bumps. Key them on the course's SCOPE_COURSE "
            "(UserCacheMixin: override extra_cache_scopes, as CourseViewSet "
            "does) or on SCOPE_GLOBAL, or allow-list the class with a reason.",
        )

    def test_every_allow_list_entry_is_still_needed(self):
        stale = sorted(
            site for site in ALLOWED if site not in self.found or not self.found[site]
        )
        self.assertEqual(
            stale,
            [],
            "allow-list entries that no longer match a roster-bearing cached "
            "view with a problem: remove them",
        )

    def test_the_sweep_flags_a_roster_view_keyed_on_the_viewer_alone(self):
        """Guard on the guard, on synthetic source."""
        bad = ast.parse(
            "class RosterSerializer(ModelSerializer):\n"
            "    students = SerializerMethodField()\n"
            "class NestingSerializer(Serializer):\n"
            "    inner = RosterSerializer()\n"
            "class LeakyViewSet(UserCacheMixin, ModelViewSet):\n"
            "    serializer_class = NestingSerializer\n"
            "class LeakyView(APIView):\n"
            "    def get(self, request):\n"
            "        key = versioned_key('b', [(SCOPE_USER, request.user.id)])\n"
            "        return RosterSerializer(x).data\n"
            "class GoodViewSet(UserCacheMixin, ModelViewSet):\n"
            "    serializer_class = RosterSerializer\n"
            "    def extra_cache_scopes(self, action):\n"
            "        return [(SCOPE_COURSE, 1)]\n"
        )
        modules = [("synthetic.py", bad)]
        roster = roster_serializers(modules)
        self.assertEqual(roster, {"RosterSerializer", "NestingSerializer"})
        found = roster_bearing_cached_views(modules, roster)
        self.assertEqual(
            found,
            {
                "synthetic.py::LeakyViewSet": [
                    "UserCacheMixin without extra_cache_scopes"
                ],
                "synthetic.py::LeakyView": [
                    "versioned_key at line 9 has no roster scope"
                ],
                "synthetic.py::GoodViewSet": [],
            },
        )
