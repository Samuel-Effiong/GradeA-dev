"""Slice 7: who is a live teacher of a school (plan 5.2; row T10) and the
rule that departments do not reset with a term (row T16)."""

from django.db import transaction
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from billing.license_service import LicenseSubscriptionService
from billing.models import SchoolCreditAllocation
from classrooms.models import Session
from departments.access import is_licensed_teacher_of, live_teachers
from departments.models import Department, DepartmentMembership
from departments.tests_factories import (
    add_member,
    make_department,
    make_license,
    make_school_with_license,
)


@override_settings(DEPARTMENTS_ENABLED=True)
class LivenessTests(APITestCase):
    def setUp(self):
        self.school, self.admin, self.license, (self.t1, self.t2) = (
            make_school_with_license()
        )
        self.dept = make_department(self.school, "Maths")
        add_member(self.dept, self.t1)
        add_member(self.dept, self.t2)

    def listed(self, user):
        self.client.force_authenticate(user)
        return self.client.get("/api/v1/departments").json().get("data") or []

    def roster_status(self, user):
        self.client.force_authenticate(user)
        return self.client.get(f"/api/v1/departments/{self.dept.id}/roster").status_code

    def assertLive(self, user):
        self.assertTrue(is_licensed_teacher_of(user, self.school.id))
        self.assertEqual(len(self.listed(user)), 1)
        self.assertEqual(self.roster_status(user), 200)

    def assertCutOff(self, user):
        self.assertFalse(is_licensed_teacher_of(user, self.school.id))
        self.assertEqual(self.listed(user), [])
        self.assertEqual(self.roster_status(user), 404)

    def test_a_live_teacher_sees_the_department(self):
        self.assertLive(self.t1)

    def test_a_lapse_changes_nothing(self):
        self.license.is_active = False
        self.license.save()
        self.assertLive(self.t1)

    def test_an_admin_can_still_add_a_teacher_during_a_lapse(self):
        self.license.is_active = False
        self.license.save()
        other = make_department(self.school, "Art")
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/v1/school-admin/departments/{other.id}/members",
            {"teacher_ids": [str(self.t1.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(
            DepartmentMembership.objects.filter(department=other, teacher=self.t1)
        )

    def test_a_replacement_license_keeps_a_carried_forward_teacher(self):
        newer = make_license(self.school, self.admin)
        with transaction.atomic():
            LicenseSubscriptionService._enroll_teacher_internal(newer, self.t1)
        self.assertLive(self.t1)

    def test_a_replacement_license_cuts_off_a_teacher_left_off_it(self):
        newer = make_license(self.school, self.admin)
        with transaction.atomic():
            LicenseSubscriptionService._enroll_teacher_internal(newer, self.t1)
        self.assertCutOff(self.t2)
        self.assertTrue(DepartmentMembership.objects.filter(teacher=self.t2).exists())

    def test_a_school_with_no_license_shows_teachers_nothing(self):
        self.license.delete()
        self.assertEqual(list(live_teachers(self.school.id)), [])
        self.assertCutOff(self.t1)

    def test_two_licenses_created_in_the_same_instant_order_by_id(self):
        first = make_license(self.school, self.admin)
        second = make_license(self.school, self.admin)
        type(first).objects.filter(pk__in=[first.pk, second.pk]).update(
            created_at=self.license.created_at
        )
        winner = max([first, second, self.license], key=lambda x: x.pk)
        with transaction.atomic():
            LicenseSubscriptionService._enroll_teacher_internal(winner, self.t1)
        self.assertLive(self.t1)

    def test_an_inactive_allocation_cuts_off(self):
        SchoolCreditAllocation.objects.filter(user=self.t1).update(is_active=False)
        self.assertCutOff(self.t1)

    def test_an_inactive_school_cuts_off_everyone_and_keeps_the_data(self):
        self.school.is_active = False
        self.school.save()
        self.assertCutOff(self.t1)
        self.assertEqual(DepartmentMembership.objects.count(), 2)


class TermsDoNotResetDepartmentsTests(TestCase):
    def test_no_department_model_has_a_session_field(self):
        for model in (Department, DepartmentMembership):
            names = {f.name for f in model._meta.get_fields()}
            self.assertFalse({n for n in names if "session" in n}, model)

    def test_a_new_session_leaves_departments_and_memberships_untouched(self):
        school, admin, _license, (t1, _t2) = make_school_with_license()
        dept = make_department(school, "Maths")
        add_member(dept, t1, can_edit=False)

        def snapshot():
            return (
                list(Department.objects.values()),
                list(DepartmentMembership.objects.values()),
            )

        before = snapshot()
        Session.objects.create(name="Term 2", school=school, owner_type="SCHOOL")
        Session.objects.create(name="Term 3", teacher=t1)
        self.assertEqual(snapshot(), before)
