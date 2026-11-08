"""NFR-CMP-04 step 1: the student data map is complete, and stays complete.

`students.student_data_map.STUDENT_DATA_MAP` lists every column that can point
at a user, and what export and erasure do with it. The owner's rulings of
2026-11-06 decide the roles:

* the activity log (audit) and credit records keep the student's name and
  email after erasure (D1, D4);
* sign-in and security data is deleted and not exported;
* a student's own work, enrollments and app data is exported, then deleted.

These tests fail when a new model points at a user (a ForeignKey/OneToOne to
the user model, or a bare user-id column) and nobody has decided what export
and erasure do with it, or when the map names a column that no longer exists.
"""

import re

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.test import SimpleTestCase

from students.student_data_map import ROLES, STUDENT_DATA_MAP, Role, entries_with_role

# A bare (non-FK) column whose name says it holds a user's id.
# SM R1: also any name ending in "_by_id" (created_by_id, reviewed_by_id).
USER_ID_COLUMN = re.compile(
    r"(^|_)(user|student|actor|teacher|target|billed_user|requested_by|"
    r"performed_by|owner|member)_id$"
    r"|^[a-z0-9_]+_by_id$"
)


def _user_relations():
    """Every concrete FK/OneToOne to the user model, as (label, field)."""
    user = get_user_model()
    found = set()
    for model in apps.get_models():
        for field in model._meta.concrete_fields:
            if field.is_relation and field.related_model is user:
                found.add((model._meta.label, field.name))
        for field in model._meta.local_many_to_many:
            if field.related_model is user:
                found.add((model._meta.label, field.name))
    return found


def _bare_user_id_columns():
    """Every non-relation column named like a user id, as (label, field)."""
    found = set()
    for model in apps.get_models():
        for field in model._meta.concrete_fields:
            if field.is_relation:
                continue
            if USER_ID_COLUMN.search(field.name) and isinstance(
                field, (models.UUIDField, models.IntegerField, models.CharField)
            ):
                found.add((model._meta.label, field.name))
    return found


class StudentDataMapIsCompleteTests(SimpleTestCase):
    def test_every_relation_to_a_user_is_in_the_map(self):
        missing = _user_relations() - set(STUDENT_DATA_MAP)
        self.assertFalse(
            missing,
            "These columns point at a user but the student data map doesn't "
            "say what export and erasure do with them. Add each one to "
            f"students/student_data_map.py: {sorted(missing)}",
        )

    def test_every_bare_user_id_column_is_in_the_map(self):
        missing = _bare_user_id_columns() - set(STUDENT_DATA_MAP)
        self.assertFalse(
            missing,
            "These columns hold a user's id without a foreign key. Add each "
            f"one to students/student_data_map.py: {sorted(missing)}",
        )

    def test_the_map_names_only_columns_that_exist(self):
        stale = []
        for label, field_name in STUDENT_DATA_MAP:
            try:
                model = apps.get_model(label)
                model._meta.get_field(field_name)
            except (LookupError, FieldDoesNotExist):
                stale.append((label, field_name))
        self.assertFalse(stale, f"The map names columns that don't exist: {stale}")

    def test_every_entry_has_a_known_role_and_a_reason(self):
        for key, entry in STUDENT_DATA_MAP.items():
            with self.subTest(key=key):
                self.assertIn(entry.role, ROLES)
                self.assertTrue(entry.why.strip(), f"{key} has no reason")


class OwnerRulingsTests(SimpleTestCase):
    """The roles the owner ruled on 2026-11-06 are pinned by name."""

    def test_the_activity_log_is_kept(self):
        for field in ("actor_id", "target_id"):
            self.assertEqual(
                STUDENT_DATA_MAP[("audit.AuditEvent", field)].role, Role.KEEP_RECORD
            )

    def test_credit_records_are_kept(self):
        for key in (
            ("billing.CreditLedger", "user_id"),
            ("billing.CreditUsageLog", "user_id"),
            ("ai_processor.AICallRecord", "billed_user_id"),
            ("billing.BillingTransaction", "user"),
            ("billing.BillingTransaction", "performed_by"),
            ("billing.PaymentDispute", "user"),
        ):
            with self.subTest(key=key):
                self.assertEqual(STUDENT_DATA_MAP[key].role, Role.KEEP_RECORD)

    def test_sign_in_data_is_deleted_and_never_exported(self):
        for key in (
            ("authtoken.Token", "user"),
            ("users.PasswordResetOTP", "user"),
            ("users.PasswordChangeOTP", "user"),
            ("users.RevokedSession", "user"),
            ("users.UserGoogleCredentials", "user"),
            ("token_blacklist.OutstandingToken", "user"),
        ):
            with self.subTest(key=key):
                entry = STUDENT_DATA_MAP[key]
                self.assertEqual(entry.role, Role.DELETE)
                self.assertFalse(entry.export)

    def test_a_students_own_work_is_exported_then_deleted(self):
        for key in (
            ("classrooms.StudentCourse", "student"),
            ("students.StudentSubmission", "student"),
            ("dashboard.StudentRiskAlertState", "student"),
            ("ai_processor.ChatSession", "user"),
            ("users.UserActivity", "user"),
            ("users.Settings", "user"),
        ):
            with self.subTest(key=key):
                entry = STUDENT_DATA_MAP[key]
                self.assertEqual(entry.role, Role.DELETE)
                self.assertTrue(entry.export)

    def test_kept_records_are_never_part_of_a_course_scoped_erasure(self):
        # D3: a teacher's erasure only removes the student from that
        # teacher's courses; kept records are untouched by any erasure.
        for key, entry in entries_with_role(Role.KEEP_RECORD):
            with self.subTest(key=key):
                self.assertFalse(entry.course_scoped)


class StepOneBRulingsTests(SimpleTestCase):
    """The SM's step 1b rulings of 2026-11-08 (R1, R3, R4, R7, R8)."""

    def test_tag_columns_are_not_a_student(self):  # R1
        for key in (("tags.Tag", "teacher"), ("tags.Tag", "created_by_id")):
            with self.subTest(key=key):
                self.assertEqual(STUDENT_DATA_MAP[key].role, Role.NOT_A_STUDENT)

    def test_the_guard_sees_a_bare_created_by_id(self):  # R1
        self.assertIn(("tags.Tag", "created_by_id"), _bare_user_id_columns())

    def test_any_name_ending_in_by_id_is_a_user_id_column(self):  # R1
        for name in ("created_by_id", "reviewed_by_id", "erased_by_id"):
            with self.subTest(name=name):
                self.assertTrue(USER_ID_COLUMN.search(name))
        for name in ("graded_by", "by_id_count", "nearby"):
            with self.subTest(name=name):
                self.assertFalse(USER_ID_COLUMN.search(name))

    def test_risk_alerts_go_only_with_a_full_erasure(self):  # R3
        entry = STUDENT_DATA_MAP[("dashboard.StudentRiskAlertState", "student")]
        self.assertEqual(entry.role, Role.DELETE)
        self.assertFalse(entry.course_scoped)

    def test_chat_sessions_go_with_a_teachers_erasure(self):  # R4
        entry = STUDENT_DATA_MAP[("ai_processor.ChatSession", "user")]
        self.assertEqual(entry.role, Role.DELETE)
        self.assertTrue(entry.course_scoped)

    def test_a_course_scoped_table_can_be_scoped_by_course(self):  # R3, R4
        # A teacher's erasure (D3) finds the rows through a course: the
        # table needs a course, or an assignment that has one.
        for (label, _), entry in STUDENT_DATA_MAP.items():
            if not entry.course_scoped:
                continue
            with self.subTest(label=label):
                names = {f.name for f in apps.get_model(label)._meta.concrete_fields}
                self.assertTrue(names & {"course", "assignment"}, label)

    def test_whitelist_and_waitlist_rows_go_by_email(self):  # R7
        for key in (("users.BetaWhitelist", "email"), ("users.Waitlist", "email")):
            with self.subTest(key=key):
                entry = STUDENT_DATA_MAP[key]
                self.assertEqual(entry.role, Role.DELETE)
                self.assertFalse(entry.export)
                self.assertFalse(entry.course_scoped)
                self.assertEqual(getattr(entry, "match", None), "EMAIL")

    def test_an_email_matched_entry_is_an_email_column(self):  # R7
        for (label, field_name), entry in STUDENT_DATA_MAP.items():
            if getattr(entry, "match", None) != "EMAIL":
                continue
            with self.subTest(key=(label, field_name)):
                field = apps.get_model(label)._meta.get_field(field_name)
                self.assertIsInstance(field, models.EmailField)

    def test_the_profile_image_is_exported_then_deleted(self):  # R8
        entry = STUDENT_DATA_MAP[("users.CustomUser", "profile_image")]
        self.assertEqual(entry.role, Role.DELETE)
        self.assertTrue(entry.export)
        self.assertFalse(entry.course_scoped)
        self.assertEqual(getattr(entry, "match", None), "OWN_ROW")
        self.assertTrue(getattr(entry, "stored_file", False))

    def test_own_row_entries_are_on_the_user_model(self):  # R8
        for (label, field_name), entry in STUDENT_DATA_MAP.items():
            if getattr(entry, "match", None) != "OWN_ROW":
                continue
            with self.subTest(key=(label, field_name)):
                self.assertIs(apps.get_model(label), get_user_model())

    def test_every_other_entry_matches_by_user(self):
        for key, entry in STUDENT_DATA_MAP.items():
            with self.subTest(key=key):
                self.assertIn(
                    getattr(entry, "match", None), ("USER", "EMAIL", "OWN_ROW")
                )
