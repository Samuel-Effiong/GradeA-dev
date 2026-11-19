"""Slice 3: memberships end when a teacher stops being on the school (plan
section 6; rows T8, T9, T11, T12, T25, T26). Access is checked through the
routes and the access functions, never only through row counts."""

from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from audit.enums import AuditAction
from audit.history import record_bulk
from audit.models import AuditEvent
from billing.license_service import LicenseSubscriptionService
from billing.models import SchoolCreditAllocation
from departments import service
from departments.access import member_department_ids
from departments.models import DepartmentMembership
from departments.tests_factories import (
    add_member,
    make_department,
    make_school_with_license,
    make_user,
)
from users.models import CustomUser, UserTypes

ON = override_settings(DEPARTMENTS_ENABLED=True)


def removals():
    return list(AuditEvent.objects.filter(action=AuditAction.DEPARTMENT_MEMBER_REMOVE))


def listed(response):
    """The rows of a list reply (the envelope shows an empty list as {})."""
    return response.json().get("data") or []


def remove_from_license(license_sub, teacher):
    LicenseSubscriptionService.remove_teacher_from_license(license_sub, teacher)


@ON
class RemovalReapTests(APITestCase):
    def setUp(self):
        self.school, self.admin, self.license, (self.t1, self.t2) = (
            make_school_with_license()
        )
        self.d1 = make_department(self.school, "Maths")
        self.d2 = make_department(self.school, "Art")
        add_member(self.d1, self.t1)
        add_member(self.d2, self.t1)
        add_member(self.d1, self.t2)

    def test_removal_ends_every_membership_and_access_with_one_event_each(self):
        self.client.force_authenticate(self.t1)
        self.assertEqual(len(listed(self.client.get("/api/v1/departments"))), 2)
        remove_from_license(self.license, self.t1)
        self.assertFalse(DepartmentMembership.objects.filter(teacher=self.t1).exists())
        events = removals()
        self.assertEqual(len(events), 2)
        self.assertEqual({e.metadata["source"] for e in events}, {"left_license"})
        self.assertEqual({e.metadata["teacher_id"] for e in events}, {str(self.t1.id)})
        self.t1.refresh_from_db()
        self.client.force_authenticate(self.t1)
        self.assertEqual(listed(self.client.get("/api/v1/departments")), [])
        self.assertEqual(
            self.client.get(f"/api/v1/departments/{self.d1.id}/roster").status_code,
            404,
        )

    def test_other_teachers_keep_their_memberships(self):
        remove_from_license(self.license, self.t1)
        self.assertEqual(
            list(
                DepartmentMembership.objects.filter(teacher=self.t2).values_list(
                    "department_id", flat=True
                )
            ),
            [self.d1.id],
        )

    def test_a_second_reap_finds_nothing_and_writes_nothing(self):
        remove_from_license(self.license, self.t1)
        before = len(removals())
        service.end_memberships(self.t1.id, None, UserTypes.TEACHER, source="x")
        self.assertEqual(len(removals()), before)

    def test_a_failing_reap_undoes_the_whole_removal(self):
        with mock.patch(
            "departments.service.end_memberships", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                remove_from_license(self.license, self.t1)
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.school_id, self.school.id)
        self.assertTrue(
            SchoolCreditAllocation.objects.get(
                user=self.t1, license_subscription=self.license
            ).is_active
        )
        self.assertEqual(
            DepartmentMembership.objects.filter(teacher=self.t1).count(), 2
        )

    def test_revival_removed_then_readded_has_no_department_access(self):
        remove_from_license(self.license, self.t1)
        self.t1.refresh_from_db()
        self.t1.school = self.school
        self.t1.save()
        LicenseSubscriptionService._enroll_teacher_internal(self.license, self.t1)
        self.client.force_authenticate(self.t1)
        self.assertEqual(listed(self.client.get("/api/v1/departments")), [])
        self.assertEqual(list(member_department_ids(self.t1)), [])


class AccountChangeReapTests(TestCase):
    def setUp(self):
        self.school, self.admin, self.license, (self.t1, self.t2) = (
            make_school_with_license()
        )
        self.other, _a, _l, (self.o1, _o2) = make_school_with_license()
        self.d1 = make_department(self.school, "Maths")
        self.m1 = add_member(self.d1, self.t1)

    def _fresh(self, user):
        return CustomUser.objects.get(pk=user.pk)

    def test_school_change_by_instance_save_ends_memberships_with_one_event(self):
        teacher = self._fresh(self.t1)
        teacher.school = self.other
        teacher.save()
        self.assertFalse(DepartmentMembership.objects.filter(pk=self.m1.pk).exists())
        (event,) = removals()
        self.assertEqual(event.metadata["source"], "account_change")

    def test_school_cleared_ends_memberships(self):
        teacher = self._fresh(self.t1)
        teacher.school = None
        teacher.save()
        self.assertFalse(DepartmentMembership.objects.filter(teacher=self.t1).exists())

    def test_role_change_ends_memberships(self):
        teacher = self._fresh(self.t1)
        teacher.user_type = UserTypes.SCHOOL_ADMIN
        teacher.save()
        self.assertFalse(DepartmentMembership.objects.filter(teacher=self.t1).exists())
        self.assertEqual(len(removals()), 1)

    def test_a_failing_reap_aborts_the_school_change(self):
        teacher = self._fresh(self.t1)
        teacher.school = self.other
        with mock.patch(
            "departments.service.end_memberships", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                teacher.save()
        self.assertEqual(self._fresh(self.t1).school_id, self.school.id)
        self.assertTrue(DepartmentMembership.objects.filter(pk=self.m1.pk).exists())

    def test_bulk_school_change_ends_memberships_one_event_each(self):
        add_member(self.d1, self.t2)
        record_bulk(
            CustomUser.objects.filter(pk__in=[self.t1.pk, self.t2.pk]),
            school=self.other,
        )
        self.assertFalse(
            DepartmentMembership.objects.filter(
                teacher_id__in=[self.t1.pk, self.t2.pk]
            ).exists()
        )
        self.assertEqual(len(removals()), 2)

    def test_a_membership_in_another_school_cannot_be_saved(self):
        from django.core.exceptions import ValidationError

        foreign_dept = make_department(self.other, "Foreign")
        with self.assertRaises(ValidationError):
            add_member(foreign_dept, self.t2)

    def test_reap_is_by_state_not_by_stale_snapshot(self):
        stale = self._fresh(self.t1)
        moved = self._fresh(self.t1)
        moved.school = self.other
        moved.save()
        add_member(make_department(self.other, "Elsewhere"), self._fresh(self.t1))
        stale.refresh_from_db()
        stale.save()
        self.assertEqual(
            DepartmentMembership.objects.filter(
                teacher=self.t1, department__school=self.other
            ).count(),
            1,
        )

    def test_password_only_save_after_in_memory_change_reaps_nothing(self):
        teacher = self._fresh(self.t1)
        teacher.school = self.other
        with CaptureQueriesContext(connection) as queries:
            teacher.save(update_fields=["password"])
        self.assertTrue(DepartmentMembership.objects.filter(pk=self.m1.pk).exists())
        sql = " ".join(q["sql"] for q in queries)
        self.assertNotIn("departments_departmentmembership", sql)
        self.assertNotIn("FOR UPDATE", sql)

    def test_enrolment_from_no_school_takes_no_user_lock_and_reaps_nothing(self):
        teacher = make_user(UserTypes.TEACHER)
        teacher = self._fresh(teacher)
        teacher.school = self.school
        with CaptureQueriesContext(connection) as queries:
            teacher.save()
        sql = " ".join(q["sql"] for q in queries)
        self.assertNotIn("FOR UPDATE", sql)
        self.assertNotIn("departments_departmentmembership", sql)

    def test_unrelated_save_reaps_nothing(self):
        teacher = self._fresh(self.t1)
        teacher.first_name = "Renamed"
        with CaptureQueriesContext(connection) as queries:
            teacher.save()
        self.assertTrue(DepartmentMembership.objects.filter(pk=self.m1.pk).exists())
        self.assertNotIn(
            "departments_departmentmembership", " ".join(q["sql"] for q in queries)
        )
