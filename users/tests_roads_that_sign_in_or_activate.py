"""
H-203: every place that issues a login token, marks an email verified or
switches an account on is on a NAMED LIST with its mark. A new site fails this
test until someone decides what the principle says about it (the same device
as the student-feedback guard).

THE PRINCIPLE (users/admin_power.py): an account with admin power that has
never been verified can be entered only with its password; no road that proves
only control of a mailbox (a reset code, a verification code, a Google
identity) signs it in, verifies it or activates it.

The scan is by text, in production code only (test files, migrations, docs and
`testing/` are skipped). Its kinds:
  FOR_USER        a call of RefreshToken/AccessToken/EpochRefreshToken.for_user(
  VERIFIED_WRITE  `email_verified_at = <not None>` or `"email_verified_at":`
  ACTIVE_TRUE     `.is_active = True`, `update(is_active=True` or `"is_active": True`
KNOWN LIMIT: an `is_active=True` KEYWORD in a `create(...)` call is not seen
(it would also match every filter); the creation roads are on the list by the
`"is_active": True` dict form where the code uses it, and the road table in
docs/evidence/h203-google-road-admin-power/ROADS.md names the others.

Run with:
    python manage.py test users.tests_roads_that_sign_in_or_activate
"""

import pathlib
import re
from collections import Counter

from django.test import SimpleTestCase

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    "migrations",
    "docs",
    "testing",
    ".git",
    "node_modules",
    "__pycache__",
    "staticfiles",
    "venv",
    ".venv",
}
KINDS = {
    "FOR_USER": re.compile(r"\b(?:Epoch)?(?:Refresh|Access|Sliding)Token\.for_user\("),
    "VERIFIED_WRITE": re.compile(
        r"""(?:\bemail_verified_at\s*=\s*(?!=|None\b)|["']email_verified_at["']\s*:)"""
    ),
    "ACTIVE_TRUE": re.compile(
        r"""(?:\.is_active\s*=\s*True\b|\bupdate\(\s*is_active\s*=\s*True"""
        r"""|["']is_active["']\s*:\s*True\b)"""
    ),
}

# Marks
OWN_DOOR = "the principle's own door or needs a live session: not mailbox-only"
CALLS_THE_HELPER = "mailbox-only road: calls holds_admin_power (H-164 / H-203)"
OPERATOR = "operator tool (Django admin or management command): no token, staff-only"
NOT_A_USER = (
    "not a user account (subscription, allocation, school or a field definition)"
)
CREATES_ONLY = "creates a NEW non-admin row; no existing account is touched"

ROADS = {
    ("users/views.py", "FOR_USER"): (
        5,
        CALLS_THE_HELPER,
        "verify, reset-password, change-password (own door), school-admin "
        "registration, Google sign-in",
    ),
    ("users/views.py", "VERIFIED_WRITE"): (
        5,
        CALLS_THE_HELPER,
        "verify, reset-password, school-admin registration, Google new account "
        "(creates a flagless TEACHER) and Google existing account",
    ),
    ("users/views.py", "ACTIVE_TRUE"): (
        4,
        CALLS_THE_HELPER,
        "verify, school-admin registration, Google new account, Google existing account",
    ),
    ("users/models.py", "VERIFIED_WRITE"): (1, NOT_A_USER, "the field definition"),
    ("users/admin.py", "ACTIVE_TRUE"): (
        1,
        OPERATOR,
        "the bulk 'Mark as active' action",
    ),
    ("users/management/commands/add_whitelist.py", "ACTIVE_TRUE"): (
        1,
        OPERATOR,
        "whitelist command",
    ),
    ("classrooms/services/enrollment.py", "ACTIVE_TRUE"): (
        1,
        CALLS_THE_HELPER,
        "enroll_student_by_email re-activation (check_existing_account_may_join)",
    ),
    ("classrooms/serializers.py", "ACTIVE_TRUE"): (
        2,
        CREATES_ONLY,
        "a teacher adds a new student; a super admin creates a school admin",
    ),
    ("classrooms/serializers.py", "VERIFIED_WRITE"): (
        1,
        CREATES_ONLY,
        "a super admin creates a school admin with credentials (no token issued)",
    ),
    ("classrooms/scale_my_students.py", "ACTIVE_TRUE"): (
        1,
        OPERATOR,
        "operator script",
    ),
    (
        "classrooms/management/commands/backfill_pending_student_invites.py",
        "ACTIVE_TRUE",
    ): (
        1,
        OPERATOR,
        "backfill command",
    ),
    ("ai_processor/management/commands/grading_benchmark.py", "ACTIVE_TRUE"): (
        1,
        OPERATOR,
        "benchmark command",
    ),
    ("billing/license_service.py", "ACTIVE_TRUE"): (
        4,
        NOT_A_USER,
        "licence / allocation rows",
    ),
    ("billing/stripe_view_schemas.py", "ACTIVE_TRUE"): (
        4,
        NOT_A_USER,
        "API schema examples",
    ),
    ("billing/views.py", "ACTIVE_TRUE"): (1, NOT_A_USER, "subscription row"),
}


def scan():
    found = Counter()
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if SKIP_DIRS & set(relative.parts) or any(
            part.startswith(".") for part in relative.parts[:-1]
        ):
            continue
        name = path.name
        if (
            "test" in name
            or name.startswith("conftest")
            or name.startswith("settings_worktree")
        ):
            continue
        text = path.read_text(errors="replace")
        for kind, pattern in KINDS.items():
            count = len(pattern.findall(text))
            if count:
                found[(relative.as_posix(), kind)] = count
    return found


class RoadsThatSignInOrActivateTests(SimpleTestCase):
    def test_every_site_is_on_the_named_list_with_its_count(self):
        found = scan()
        pinned = {key: value[0] for key, value in ROADS.items()}

        new_or_changed = sorted(
            f"{key}: found {found.get(key, 0)}, pinned {pinned.get(key, 0)}"
            for key in set(found) | set(pinned)
            if found.get(key, 0) != pinned.get(key, 0)
        )

        self.assertEqual(
            new_or_changed,
            [],
            "A place that issues a token, marks an email verified or switches an "
            "account on is new or changed. Decide what the principle in "
            "users/admin_power.py says about it, then add it to ROADS with its "
            "mark (and to docs/evidence/h203-google-road-admin-power/ROADS.md).",
        )

    def test_every_pinned_road_has_a_mark_and_a_reason(self):
        for key, (count, mark, why) in ROADS.items():
            with self.subTest(road=key):
                self.assertGreater(count, 0)
                self.assertTrue(mark)
                self.assertTrue(why)
