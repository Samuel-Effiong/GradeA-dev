"""Slice 6: the cached "my departments" list (plan 10, row T14). The
permission decision is never answered from the cache: the liveness check
runs first, and the cache holds payload only."""

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from AutoGrader.cache_generation import SCOPE_DEPARTMENT, VALID_SCOPES
from billing.license_service import LicenseSubscriptionService
from departments.tests_factories import (
    add_member,
    make_department,
    make_license,
    make_school_with_license,
)

CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(DEPARTMENTS_ENABLED=True, CACHES=CACHE)
class MyDepartmentsCacheTests(APITestCase):
    URL = "/api/v1/departments"

    def setUp(self):
        self.school, self.admin, self.license, (self.t1, self.t2) = (
            make_school_with_license()
        )
        self.maths = make_department(self.school, "Maths")
        self.art = make_department(self.school, "Art")
        self.m1 = add_member(self.maths, self.t1)
        add_member(self.maths, self.t2, can_edit=False)

    def mine(self, user):
        self.client.force_authenticate(user)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json().get("data") or []

    def admin_call(self, method, url, body=None):
        self.client.force_authenticate(self.admin)
        response = getattr(self.client, method)(url, body, format="json")
        self.assertLess(response.status_code, 300, response.content)
        return response

    def test_the_scope_is_registered(self):
        self.assertIn(SCOPE_DEPARTMENT, VALID_SCOPES)

    def test_a_second_read_is_served_from_the_cache(self):
        first = self.mine(self.t1)
        self.client.force_authenticate(self.t1)
        with CaptureQueriesContext(connection) as warm:
            self.client.get(self.URL)
        self.assertEqual(self.mine(self.t1), first)
        membership_reads = [
            q
            for q in warm
            if "departments_departmentmembership" in q["sql"] and "JOIN" in q["sql"]
        ]
        self.assertEqual(membership_reads, [])

    def test_each_viewer_gets_their_own_payload(self):
        self.assertTrue(self.mine(self.t1)[0]["can_edit"])
        self.assertFalse(self.mine(self.t2)[0]["can_edit"])

    def test_adding_a_new_department_shows_up_at_once(self):
        self.assertEqual(len(self.mine(self.t1)), 1)
        self.admin_call(
            "post",
            f"/api/v1/school-admin/departments/{self.art.id}/members",
            {"teacher_ids": [str(self.t1.id)]},
        )
        self.assertEqual({d["name"] for d in self.mine(self.t1)}, {"Maths", "Art"})

    def test_a_rename_shows_up_at_once(self):
        self.mine(self.t1)
        self.admin_call(
            "patch",
            f"/api/v1/school-admin/departments/{self.maths.id}",
            {"name": "Mathematics"},
        )
        self.assertEqual(self.mine(self.t1)[0]["name"], "Mathematics")

    def test_the_edit_switch_shows_up_at_once(self):
        self.assertTrue(self.mine(self.t1)[0]["can_edit"])
        self.admin_call(
            "patch",
            f"/api/v1/school-admin/departments/{self.maths.id}/members/{self.m1.id}",
            {"can_edit": False},
        )
        self.assertFalse(self.mine(self.t1)[0]["can_edit"])

    def test_a_removal_shows_up_at_once(self):
        self.assertEqual(len(self.mine(self.t1)), 1)
        self.admin_call(
            "delete",
            f"/api/v1/school-admin/departments/{self.maths.id}/members/{self.m1.id}",
        )
        self.assertEqual(self.mine(self.t1), [])

    def test_a_department_delete_shows_up_at_once(self):
        self.assertEqual(len(self.mine(self.t1)), 1)
        self.admin_call("delete", f"/api/v1/school-admin/departments/{self.maths.id}")
        self.assertEqual(self.mine(self.t1), [])

    def test_leaving_the_license_shows_up_at_once(self):
        self.assertEqual(len(self.mine(self.t1)), 1)
        LicenseSubscriptionService.remove_teacher_from_license(self.license, self.t1)
        self.t1.refresh_from_db()
        self.assertEqual(self.mine(self.t1), [])

    def test_a_warm_cache_is_never_served_to_a_teacher_who_is_no_longer_live(self):
        """A replacement license that leaves the teacher off changes no
        department row and no counter; the liveness check must still win."""
        self.assertEqual(len(self.mine(self.t1)), 1)
        make_license(self.school, self.admin)
        self.assertEqual(self.mine(self.t1), [])
