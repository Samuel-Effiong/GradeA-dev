"""Slice 1: the two tables, their rules, the flag, the audit labels."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.test import TestCase

from audit.metadata import ALLOWED_KEYS, METADATA_ALLOWLIST
from audit.serializers import _TARGET_LABEL
from departments.models import Department, DepartmentMembership
from departments.tests_factories import (
    add_member,
    make_department,
    make_school,
    make_school_with_license,
    make_user,
)
from users.models import UserTypes


class DepartmentModelTests(TestCase):
    def test_name_key_is_lowercase_with_collapsed_spaces(self):
        dept = make_department(make_school(), name="  Math   and  Science ")
        self.assertEqual(dept.name, "Math and Science")
        self.assertEqual(dept.name_key, "math and science")

    def test_same_name_in_a_school_is_refused_ignoring_case_and_spaces(self):
        school = make_school()
        make_department(school, name="Math")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_department(school, name="  mATH ")

    def test_same_name_in_another_school_is_fine(self):
        make_department(make_school(), name="Math")
        make_department(make_school(), name="Math")

    def test_empty_and_overlong_names_fail_validation(self):
        school = make_school()
        for bad in ("", "   ", "x" * 101):
            with self.subTest(name=bad):
                dept = Department(school=school, name=bad)
                with self.assertRaises(ValidationError):
                    dept.full_clean()

    def test_has_no_session_field(self):
        names = {f.name for f in Department._meta.get_fields()}
        self.assertNotIn("session", names)

    def test_school_delete_cascades_and_the_rule_is_pinned(self):
        field = Department._meta.get_field("school")
        self.assertIs(field.remote_field.on_delete, models.CASCADE)
        school, _admin, _lic, (t1, _t2) = make_school_with_license()
        dept = make_department(school)
        add_member(dept, t1)
        school.delete()
        self.assertFalse(Department.objects.filter(pk=dept.pk).exists())
        self.assertFalse(DepartmentMembership.objects.exists())


class MembershipModelTests(TestCase):
    def setUp(self):
        self.school, self.admin, self.lic, (self.t1, self.t2) = (
            make_school_with_license()
        )
        self.dept = make_department(self.school)

    def test_can_edit_defaults_to_true(self):
        self.assertTrue(add_member(self.dept, self.t1).can_edit)

    def test_one_membership_per_teacher_per_department(self):
        add_member(self.dept, self.t1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            add_member(self.dept, self.t1)

    def test_a_teacher_of_another_school_cannot_be_added(self):
        other_school, _a, _l, (foreign, _x) = make_school_with_license()
        with self.assertRaises(ValidationError):
            add_member(self.dept, foreign)
        self.assertFalse(DepartmentMembership.objects.exists())

    def test_a_teacher_with_no_school_cannot_be_added(self):
        loner = make_user(UserTypes.TEACHER)
        with self.assertRaises(ValidationError):
            add_member(self.dept, loner)

    def test_only_teachers_can_be_members(self):
        for user_type in (UserTypes.STUDENT, UserTypes.SCHOOL_ADMIN):
            with self.subTest(user_type=user_type):
                person = make_user(user_type, school=self.school)
                with self.assertRaises(ValidationError):
                    add_member(self.dept, person)

    def test_only_one_edit_flag_exists(self):
        names = {f.name for f in DepartmentMembership._meta.get_fields()}
        self.assertIn("can_edit", names)
        self.assertNotIn("can_edit_lessons", names)
        self.assertNotIn("can_edit_library", names)


class FlagAndAuditTests(TestCase):
    def test_flag_is_off_under_the_test_runner_by_default(self):
        self.assertFalse(settings.DEPARTMENTS_ENABLED)

    def test_new_target_types_have_labels(self):
        self.assertEqual(_TARGET_LABEL["Department"], "Department")
        self.assertEqual(_TARGET_LABEL["DepartmentMembership"], "Department member")

    def test_audit_allow_lists_carry_the_department_keys(self):
        from audit.enums import AuditAction

        delete = METADATA_ALLOWLIST[AuditAction.DEPARTMENT_DELETE]
        for key in (
            "member_count",
            "member_ids",
            "member_ids_truncated",
            "item_count",
            "delete_choice",
            "target_department_id",
        ):
            self.assertIn(key, delete)
            self.assertIn(key, ALLOWED_KEYS)
        for action in (
            AuditAction.DEPARTMENT_MEMBER_ADD,
            AuditAction.DEPARTMENT_MEMBER_REMOVE,
        ):
            self.assertLessEqual({"teacher_id", "source"}, METADATA_ALLOWLIST[action])
