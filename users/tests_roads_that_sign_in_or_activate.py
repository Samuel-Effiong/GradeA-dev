"""
H-203: every place that issues a login token, marks an email verified, switches
an account on, sets a password or sends a credential by mail is on a NAMED LIST
with its mark. A new site fails this
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
  SET_PASSWORD    `<name>.set_password(` on any object but `self` (a road that
                  sets a password and mails it signs nobody in by itself, but
                  it hands the mailbox the way in)
  MAKE_PASSWORD   `make_password(`
  PASSWORD_KEYWORD  `password=` as a keyword argument (create/update/call)
  CREDENTIAL_MAIL a call or definition of a function that makes or mails a
                  temporary password (generate_temporary_password,
                  _generate_school_admin_password and the three invitation mails)
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
# A call that WRITES the named column through a keyword: `update(...)` or the
# audit history helper `history.record_bulk(...)` (the only write helper in
# audit/history.py; its `**changes` go straight into one UPDATE). The call's
# arguments may hold two levels of parentheses (a nested filter, a timezone
# call), and the keyword may be anywhere in them, on any line.
_WRITE_CALL = r"\b(?:update|record_bulk)\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*?"
KINDS = {
    "FOR_USER": re.compile(r"\b(?:Epoch)?(?:Refresh|Access|Sliding)Token\.for_user\("),
    # `email_verified_at=<value>` as a bare keyword already matches inside
    # `update(...)` and `history.record_bulk(...)`; the setattr form is new.
    "VERIFIED_WRITE": re.compile(
        r"""(?:\bemail_verified_at\s*=\s*(?!=|None\b)|["']email_verified_at["']\s*:"""
        r"""|\bsetattr\([^,()]+,\s*["']email_verified_at["']\s*,(?!\s*None\b))"""
    ),
    "ACTIVE_TRUE": re.compile(
        r"""(?:\.is_active\s*=\s*True\b|"""
        + _WRITE_CALL
        + r"""\bis_active\s*=\s*True\b|["']is_active["']\s*:\s*True\b"""
        r"""|\bsetattr\([^,()]+,\s*["']is_active["']\s*,\s*True\b)""",
        re.S,
    ),
    "SET_PASSWORD": re.compile(r"\b(?!self\b)\w+\.set_password\("),
    "MAKE_PASSWORD": re.compile(r"\bmake_password\("),
    "PASSWORD_KEYWORD": re.compile(r"\bpassword=(?!=)"),
    "CREDENTIAL_MAIL": re.compile(
        r"\b(?:send_student_login_invitation_email|_send_teacher_invitation"
        r"|_send_school_admin_invitation_email|generate_temporary_password"
        r"|_generate_school_admin_password)\("
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
TEXT_ONLY = "a pattern or a docstring about passwords, not a place that sets one"
DEFINITION = "the function that makes or mails the credential (its callers are listed)"

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
    # -- places that set a password or send a credential by mail ---------------
    ("billing/license_service.py", "SET_PASSWORD"): (
        2,
        CALLS_THE_HELPER,
        "licence invitation: an EXISTING never-signed-in teacher gets a new password "
        "only after the H-203 guard in _get_or_invite_teacher; a NEW teacher is "
        "created flagless",
    ),
    ("billing/license_service.py", "CREDENTIAL_MAIL"): (
        5,
        CALLS_THE_HELPER,
        "the same two sites, the call of _send_teacher_invitation and its definition",
    ),
    ("classrooms/services/enrollment.py", "SET_PASSWORD"): (
        2,
        CALLS_THE_HELPER,
        "enroll_student_by_email: a never-signed-in existing student (after "
        "check_existing_account_may_join) and a NEW student",
    ),
    ("classrooms/services/enrollment.py", "CREDENTIAL_MAIL"): (
        4,
        CALLS_THE_HELPER,
        "the two password makers and the two invitation mails of the same two sites",
    ),
    ("classrooms/services/notifications.py", "CREDENTIAL_MAIL"): (
        1,
        DEFINITION,
        "send_student_login_invitation_email",
    ),
    ("users/services.py", "CREDENTIAL_MAIL"): (
        1,
        DEFINITION,
        "generate_temporary_password",
    ),
    ("classrooms/serializers.py", "SET_PASSWORD"): (
        1,
        CREATES_ONLY,
        "a super admin creates a school admin (a new row, verified by that act)",
    ),
    ("classrooms/serializers.py", "CREDENTIAL_MAIL"): (
        5,
        CREATES_ONLY,
        "the same creation: the password maker, its mail, and their definitions "
        "and the resend call",
    ),
    (
        "classrooms/management/commands/backfill_pending_student_invites.py",
        "SET_PASSWORD",
    ): (
        1,
        OPERATOR,
        "backfill command",
    ),
    (
        "classrooms/management/commands/backfill_pending_student_invites.py",
        "CREDENTIAL_MAIL",
    ): (2, OPERATOR, "backfill command"),
    ("users/models.py", "SET_PASSWORD"): (
        1,
        CREATES_ONLY,
        "the user manager's create_user",
    ),
    ("users/models.py", "PASSWORD_KEYWORD"): (
        2,
        NOT_A_USER,
        "the manager's create_user / create_superuser signatures",
    ),
    ("users/serializers.py", "SET_PASSWORD"): (
        1,
        OWN_DOOR,
        "the signed-in account's own profile update (needs a live session)",
    ),
    ("users/views.py", "SET_PASSWORD"): (
        3,
        CALLS_THE_HELPER,
        "reset-password (calls the helper), change-password (own door: a live "
        "session) and school-admin registration (calls the helper)",
    ),
    ("users/management/commands/remediate_student123_passwords.py", "MAKE_PASSWORD"): (
        1,
        OPERATOR,
        "remediation command (makes an unusable password)",
    ),
    (
        "users/management/commands/remediate_student123_passwords.py",
        "PASSWORD_KEYWORD",
    ): (2, OPERATOR, "remediation command"),
    ("AutoGrader/log_scrubbing.py", "PASSWORD_KEYWORD"): (2, TEXT_ONLY, "docstring"),
    ("AutoGrader/sentry_scrubbing.py", "PASSWORD_KEYWORD"): (1, TEXT_ONLY, "docstring"),
    (
        "ai_processor/benchmark/isolation_run8/isolation_harness.py",
        "PASSWORD_KEYWORD",
    ): (
        1,
        OPERATOR,
        "benchmark harness fixture",
    ),
    ("ai_processor/management/commands/grading_benchmark.py", "PASSWORD_KEYWORD"): (
        1,
        OPERATOR,
        "benchmark command",
    ),
    ("billing/live_qa/scenarios_license.py", "PASSWORD_KEYWORD"): (
        1,
        OPERATOR,
        "live QA scenario script (random, never used to sign in)",
    ),
    ("billing/stripe_live_qa.py", "PASSWORD_KEYWORD"): (
        1,
        OPERATOR,
        "live QA script (random, never used to sign in)",
    ),
    ("classrooms/scale_my_students.py", "PASSWORD_KEYWORD"): (
        1,
        OPERATOR,
        "operator script",
    ),
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
            "A place that issues a token, marks an email verified, switches an "
            "account on, sets a password or mails a credential is new or changed. Decide what the principle in "
            "users/admin_power.py says about it, then add it to ROADS with its "
            "mark (and to docs/evidence/h203-google-road-admin-power/ROADS.md).",
        )

    def test_every_pinned_road_has_a_mark_and_a_reason(self):
        for key, (count, mark, why) in ROADS.items():
            with self.subTest(road=key):
                self.assertGreater(count, 0)
                self.assertTrue(mark)
                self.assertTrue(why)


class PatternReachTests(SimpleTestCase):
    """The scanner's patterns see the write forms the audit history helper
    (`history.record_bulk`, whose `**changes` go into one UPDATE) and
    `update(...)` allow, not only the bare `update(is_active=True` first
    keyword. Each text below is a made-up snippet, not production code."""

    def hits(self, kind, text):
        return len(KINDS[kind].findall(text))

    def test_active_true_is_seen_through_the_history_helper(self):
        self.assertEqual(
            self.hits(
                "ACTIVE_TRUE", "n = history.record_bulk(queryset, is_active=True)"
            ),
            1,
        )

    def test_active_true_is_seen_across_lines_and_nested_calls(self):
        text = (
            "history.record_bulk(\n"
            "    User.objects.filter(pk__in=[1, 2]),\n"
            "    is_active=True,\n"
            ")\n"
        )
        self.assertEqual(self.hits("ACTIVE_TRUE", text), 1)

    def test_active_true_is_seen_when_it_is_not_the_first_keyword(self):
        text = 'queryset.update(token_epoch=F("token_epoch") + 1, is_active=True)'
        self.assertEqual(self.hits("ACTIVE_TRUE", text), 1)

    def test_active_true_is_seen_through_setattr(self):
        self.assertEqual(
            self.hits("ACTIVE_TRUE", 'setattr(user, "is_active", True)'), 1
        )

    def test_active_false_and_filters_are_not_sites(self):
        text = (
            "history.record_bulk(qs, is_active=False)\n"
            "queryset.update(is_active=False)\n"
            "User.objects.filter(is_active=True)\n"
            "history.record_bulk(qs, enrollment_status=X)\n"
            "other.filter(is_active=True)\n"
        )
        self.assertEqual(self.hits("ACTIVE_TRUE", text), 0)

    def test_email_verified_is_seen_through_the_history_helper_and_setattr(self):
        text = (
            "history.record_bulk(qs, email_verified_at=timezone.now())\n"
            'setattr(user, "email_verified_at", timezone.now())\n'
        )
        self.assertEqual(self.hits("VERIFIED_WRITE", text), 2)

    def test_email_verified_cleared_to_none_is_not_a_site(self):
        text = (
            "history.record_bulk(qs, email_verified_at=None)\n"
            'setattr(user, "email_verified_at", None)\n'
        )
        self.assertEqual(self.hits("VERIFIED_WRITE", text), 0)

    def test_a_password_through_the_history_helper_is_seen_as_a_keyword(self):
        text = "history.record_bulk(qs, password=hashed)"  # pragma: allowlist secret
        self.assertEqual(self.hits("PASSWORD_KEYWORD", text), 1)
