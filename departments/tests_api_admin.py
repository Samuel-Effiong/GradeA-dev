"""Slice 2: the school admin's department routes (T1, T2, T13, T14, T15,
T22, T23 of the plan). The Fixer's matrix covers the cross-resource rows."""

import uuid
from unittest import mock

from django.test import override_settings
from rest_framework.test import APITestCase

from audit.enums import AuditAction, AuditOutcome, ReasonCode
from audit.models import AuditEvent
from AutoGrader.reason_codes import REASON_CODES
from billing.models import LicenseSubscription
from departments import wording
from departments.models import Department, DepartmentMembership
from departments.tests_factories import (
    add_member,
    make_department,
    make_school_with_license,
    make_user,
)
from users.models import CustomUser, UserTypes

ON = override_settings(DEPARTMENTS_ENABLED=True)


def events(action):
    return list(AuditEvent.objects.filter(action=action).order_by("occurred_at"))


@ON
class AdminApiBase(APITestCase):
    def setUp(self):
        self.school, self.admin, self.license, (self.t1, self.t2) = (
            make_school_with_license()
        )
        self.other_school, self.admin2, _l, (self.foreign, _x) = (
            make_school_with_license()
        )
        self.student = make_user(UserTypes.STUDENT, school=self.school)
        self.individual = make_user(UserTypes.TEACHER)
        self.superadmin = CustomUser.objects.create_superuser(
            email="dept-super@dept.test",
            password="dept-pass-123",  # pragma: allowlist secret
        )
        self.superadmin.user_type = UserTypes.SUPER_ADMIN
        self.superadmin.save()
        self.client.force_authenticate(self.admin)

    def as_(self, user):
        self.client.force_authenticate(user)
        return self.client

    def url_list(self):
        return "/api/v1/school-admin/departments"

    def url_detail(self, dept):
        return f"/api/v1/school-admin/departments/{getattr(dept, 'id', dept)}"

    def url_members(self, dept):
        return self.url_detail(dept) + "/members"

    def url_member(self, dept, membership):
        return self.url_members(dept) + f"/{getattr(membership, 'id', membership)}"

    def create(self, name="Math", **extra):
        return self.client.post(self.url_list(), {"name": name, **extra}, format="json")

    def assertCoded(self, response, code, status_code):
        self.assertEqual(response.status_code, status_code, response.content)
        self.assertEqual(response.data["reason_code"], code)


class DepartmentCrudTests(AdminApiBase):
    def test_admin_creates_a_department_and_one_event_is_written(self):
        response = self.create("  Math   and Science ")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            set(response.data),
            {"id", "name", "description", "member_count", "created_at"},
        )
        self.assertEqual(response.data["name"], "Math and Science")
        dept = Department.objects.get()
        self.assertEqual(dept.school_id, self.school.id)
        self.assertEqual(dept.created_by_id, self.admin.id)
        (event,) = events(AuditAction.DEPARTMENT_CREATE)
        self.assertEqual(event.actor_id, self.admin.id)
        self.assertEqual(event.school_id, self.school.id)
        self.assertEqual(event.department_id, dept.id)
        self.assertEqual(event.target_type, "Department")
        self.assertEqual(event.target_id, dept.id)

    def test_a_body_cannot_choose_the_school_or_the_creator(self):
        response = self.create(
            "Art", school=str(self.other_school.id), created_by_id=str(uuid.uuid4())
        )
        self.assertEqual(response.status_code, 201)
        dept = Department.objects.get()
        self.assertEqual(dept.school_id, self.school.id)
        self.assertEqual(dept.created_by_id, self.admin.id)

    def test_name_taken_ignores_case_and_spaces_with_the_approved_text(self):
        self.create("Math")
        response = self.create("  mATH ")
        self.assertCoded(response, "DEPARTMENT_NAME_TAKEN", 400)
        self.assertEqual(
            response.data["error"],
            wording.NAME_TAKEN.format(name="Math"),
        )
        self.assertEqual(Department.objects.count(), 1)

    def test_the_same_name_is_fine_in_another_school(self):
        make_department(self.other_school, "Math")
        self.assertEqual(self.create("Math").status_code, 201)

    def test_empty_and_overlong_names_use_the_approved_text(self):
        for bad in ("", "   ", "x" * 101):
            with self.subTest(name=bad):
                response = self.create(bad)
                self.assertCoded(response, "DEPARTMENT_NAME_INVALID", 400)
                self.assertEqual(response.data["error"], wording.NAME_INVALID)
        self.assertEqual(Department.objects.count(), 0)

    def test_list_is_the_schools_own_with_member_counts_ordered_by_name(self):
        b = make_department(self.school, "Beta")
        a = make_department(self.school, "alpha")
        make_department(self.other_school, "Gamma")
        add_member(b, self.t1)
        response = self.client.get(self.url_list())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([d["name"] for d in response.data], ["alpha", "Beta"])
        self.assertEqual([d["member_count"] for d in response.data], [0, 1])
        self.assertEqual(a.school_id, self.school.id)

    def test_detail_lists_members_with_the_admin_row_fields_only(self):
        dept = make_department(self.school, "Math")
        add_member(dept, self.t1, can_edit=False, added_by=self.admin)
        response = self.client.get(self.url_detail(dept))
        self.assertEqual(response.status_code, 200)
        (member,) = response.data["members"]
        self.assertEqual(
            set(member),
            {"membership_id", "teacher_id", "name", "email", "can_edit", "added_at"},
        )
        self.assertFalse(member["can_edit"])

    def test_rename_records_the_changed_field_not_the_name(self):
        dept = make_department(self.school, "Math")
        response = self.client.patch(
            self.url_detail(dept), {"name": "Maths"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        dept.refresh_from_db()
        self.assertEqual(dept.name, "Maths")
        (event,) = events(AuditAction.DEPARTMENT_UPDATE)
        self.assertEqual(event.metadata["changed_fields"], ["name"])
        self.assertNotIn("Maths", str(event.metadata))
        self.assertNotIn("Maths", str(event.before) + str(event.after))

    def test_rename_into_a_taken_name_is_refused(self):
        make_department(self.school, "Art")
        dept = make_department(self.school, "Math")
        response = self.client.patch(
            self.url_detail(dept), {"name": "ART"}, format="json"
        )
        self.assertCoded(response, "DEPARTMENT_NAME_TAKEN", 400)

    def test_delete_of_a_department_with_members_is_one_event(self):
        dept = make_department(self.school, "Math")
        add_member(dept, self.t1)
        add_member(dept, self.t2)
        before = AuditEvent.objects.filter(
            action=AuditAction.DEPARTMENT_MEMBER_REMOVE
        ).count()
        response = self.client.delete(self.url_detail(dept))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Department.objects.exists())
        self.assertFalse(DepartmentMembership.objects.exists())
        (event,) = events(AuditAction.DEPARTMENT_DELETE)
        self.assertEqual(event.department_id, dept.id)
        self.assertEqual(event.metadata["member_count"], 2)
        self.assertEqual(
            set(event.metadata["member_ids"]), {str(self.t1.id), str(self.t2.id)}
        )
        self.assertEqual(
            AuditEvent.objects.filter(
                action=AuditAction.DEPARTMENT_MEMBER_REMOVE
            ).count(),
            before,
        )

    def test_delete_event_caps_member_ids_at_fifty(self):
        dept = make_department(self.school, "Big")
        for _ in range(51):
            add_member(dept, make_user(UserTypes.TEACHER, school=self.school))
        self.assertEqual(self.client.delete(self.url_detail(dept)).status_code, 200)
        (event,) = events(AuditAction.DEPARTMENT_DELETE)
        self.assertEqual(event.metadata["member_count"], 51)
        self.assertEqual(len(event.metadata["member_ids"]), 50)
        self.assertTrue(event.metadata["member_ids_truncated"])


class RoleAndScopeTests(AdminApiBase):
    def test_everyone_but_a_school_admin_is_refused_on_every_route(self):
        dept = make_department(self.school, "Math")
        membership = add_member(dept, self.t1)
        routes = [
            ("get", self.url_list(), None),
            ("post", self.url_list(), {"name": "X"}),
            ("get", self.url_detail(dept), None),
            ("patch", self.url_detail(dept), {"name": "X"}),
            ("delete", self.url_detail(dept), None),
            ("post", self.url_members(dept), {"teacher_ids": [str(self.t2.id)]}),
            ("patch", self.url_member(dept, membership), {"can_edit": False}),
            ("delete", self.url_member(dept, membership), None),
        ]
        for actor, status_code in (
            (self.t1, 403),
            (self.individual, 403),
            (self.student, 403),
            (self.superadmin, 403),
        ):
            for method, url, body in routes:
                with self.subTest(actor=actor.user_type, method=method, url=url):
                    response = getattr(self.as_(actor), method)(
                        url, body, format="json"
                    )
                    self.assertEqual(response.status_code, status_code)
        self.client.force_authenticate(None)
        for method, url, body in routes:
            with self.subTest(actor="anonymous", method=method, url=url):
                response = getattr(self.client, method)(url, body, format="json")
                self.assertEqual(response.status_code, 401)
        # Nothing changed.
        self.assertEqual(Department.objects.count(), 1)
        self.assertEqual(DepartmentMembership.objects.count(), 1)
        self.assertTrue(DepartmentMembership.objects.get().can_edit)

    def test_a_teacher_gets_the_house_message(self):
        response = self.as_(self.t1).get(self.url_list())
        self.assertEqual(
            response.data["detail"],
            "You must be a school admin to access this endpoint.",
        )

    def test_another_schools_admin_gets_the_same_404_as_an_unknown_id(self):
        dept = make_department(self.school, "Math")
        membership = add_member(dept, self.t1)
        self.as_(self.admin2)
        unknown = self.client.get(self.url_detail(uuid.uuid4()))
        self.assertEqual(unknown.status_code, 404)
        for method, url, body in (
            ("get", self.url_detail(dept), None),
            ("patch", self.url_detail(dept), {"name": "X"}),
            ("delete", self.url_detail(dept), None),
            ("post", self.url_members(dept), {"teacher_ids": [str(self.foreign.id)]}),
            ("patch", self.url_member(dept, membership), {"can_edit": False}),
            ("delete", self.url_member(dept, membership), None),
        ):
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, body, format="json")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.content, unknown.content)
        self.assertTrue(Department.objects.filter(pk=dept.pk).exists())
        self.assertTrue(DepartmentMembership.objects.filter(pk=membership.pk).exists())

    def test_a_malformed_id_is_the_same_404(self):
        response = self.client.get("/api/v1/school-admin/departments/not-a-uuid")
        self.assertEqual(response.status_code, 404)

    def test_an_admin_with_no_school_sees_nothing(self):
        make_department(self.school, "Math")
        orphan = make_user(UserTypes.SCHOOL_ADMIN)
        self.as_(orphan)
        self.assertEqual(self.client.get(self.url_list()).data, [])
        self.assertEqual(
            self.client.get(self.url_detail(uuid.uuid4())).status_code, 404
        )
        self.assertEqual(self.create("X").status_code, 404)

    def test_any_admin_of_the_school_may_manage_it(self):
        second = make_user(UserTypes.SCHOOL_ADMIN, school=self.school)
        dept = make_department(self.school, "Math")
        response = self.as_(second).patch(
            self.url_detail(dept), {"name": "Maths"}, format="json"
        )
        self.assertEqual(response.status_code, 200)

    def test_a_denial_leaves_one_denied_event_and_no_change(self):
        dept = make_department(self.school, "Math")
        self.as_(self.t1).delete(self.url_detail(dept))
        self.assertTrue(Department.objects.filter(pk=dept.pk).exists())
        denied = AuditEvent.objects.filter(outcome=AuditOutcome.DENIED)
        self.assertGreaterEqual(denied.count(), 1)

    @override_settings(DEPARTMENTS_ENABLED=False)
    def test_with_the_flag_off_every_route_is_an_unknown_url_for_everyone(self):
        dept = make_department(self.school, "Math")
        unknown = self.client.get("/api/v1/school-admin/departments-nope")
        for actor in (self.admin, self.t1, self.superadmin, None):
            self.client.force_authenticate(actor)
            for method, url in (
                ("get", self.url_list()),
                ("post", self.url_list()),
                ("get", self.url_detail(dept)),
                ("delete", self.url_detail(dept)),
                ("post", self.url_members(dept)),
                ("get", "/api/v1/departments"),
            ):
                with self.subTest(actor=getattr(actor, "user_type", None), url=url):
                    response = getattr(self.client, method)(url, {}, format="json")
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.content, unknown.content)


class MemberTests(AdminApiBase):
    def setUp(self):
        super().setUp()
        self.dept = make_department(self.school, "Math")

    def add(self, *ids):
        return self.client.post(
            self.url_members(self.dept),
            {"teacher_ids": [str(i) for i in ids]},
            format="json",
        )

    def test_add_two_teachers_one_event_each(self):
        response = self.add(self.t1.id, self.t2.id)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["successful"], 2)
        self.assertEqual(response.data["failed"], 0)
        self.assertEqual(DepartmentMembership.objects.count(), 2)
        added = events(AuditAction.DEPARTMENT_MEMBER_ADD)
        self.assertEqual(len(added), 2)
        self.assertEqual(
            {e.metadata["teacher_id"] for e in added},
            {str(self.t1.id), str(self.t2.id)},
        )
        for event in added:
            self.assertEqual(event.department_id, self.dept.id)
            self.assertEqual(event.school_id, self.school.id)
            self.assertEqual(event.target_type, "DepartmentMembership")
            self.assertEqual(event.metadata["source"], "admin")
        row = DepartmentMembership.objects.get(teacher=self.t1)
        self.assertEqual(row.added_by_id, self.admin.id)
        self.assertTrue(row.can_edit)

    def test_every_kind_of_wrong_id_gets_the_identical_coded_item(self):
        bad = [
            self.foreign.id,
            self.student.id,
            self.admin.id,
            uuid.uuid4(),
            "not-a-uuid",
            self.individual.id,
        ]
        response = self.add(*bad)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["failed"], len(bad))
        for entry in response.data["errors"]:
            self.assertEqual(entry["reason_code"], "TEACHER_NOT_ON_LICENCE")
            self.assertEqual(
                entry["message"],
                "This teacher isn't an active teacher on this license.",
            )
        self.assertEqual(
            {e["message"] for e in response.data["errors"]},
            {"This teacher isn't an active teacher on this license."},
        )
        self.assertFalse(DepartmentMembership.objects.exists())

    def test_a_deactivated_teacher_cannot_be_added(self):
        CustomUser.objects.filter(pk=self.t1.pk).update(is_active=False)
        response = self.add(self.t1.id)
        self.assertEqual(
            response.data["errors"][0]["reason_code"], "TEACHER_NOT_ON_LICENCE"
        )

    def test_a_teacher_removed_from_the_license_cannot_be_added(self):
        from billing.license_service import LicenseSubscriptionService

        LicenseSubscriptionService.remove_teacher_from_license(self.license, self.t1)
        response = self.add(self.t1.id)
        self.assertEqual(
            response.data["errors"][0]["reason_code"], "TEACHER_NOT_ON_LICENCE"
        )

    def test_a_teacher_on_a_lapsed_license_can_still_be_added(self):
        LicenseSubscription.objects.filter(pk=self.license.pk).update(is_active=False)
        response = self.add(self.t1.id)
        self.assertEqual(response.data["successful"], 1, response.content)

    def test_a_teacher_left_off_a_replacement_license_cannot_be_added(self):
        from departments.tests_factories import make_license

        LicenseSubscription.objects.filter(pk=self.license.pk).update(is_active=False)
        newer = make_license(self.school, self.admin)
        from departments.tests_factories import make_licensed_teacher

        carried = make_licensed_teacher(self.school, newer)
        response = self.add(self.t1.id, carried.id)
        self.assertEqual(response.data["successful"], 1)
        self.assertEqual(response.data["failed"], 1)
        self.assertEqual(DepartmentMembership.objects.get().teacher_id, carried.id)

    def test_adding_twice_is_coded_already_member(self):
        self.add(self.t1.id)
        response = self.add(self.t1.id, self.t2.id)
        self.assertEqual(response.data["successful"], 1)
        (entry,) = response.data["errors"]
        self.assertEqual(entry["reason_code"], "DEPARTMENT_ALREADY_MEMBER")
        self.assertEqual(entry["message"], wording.ALREADY_MEMBER)
        self.assertEqual(len(events(AuditAction.DEPARTMENT_MEMBER_ADD)), 2)

    def test_more_than_fifty_ids_is_refused_whole(self):
        response = self.add(*[uuid.uuid4() for _ in range(51)])
        self.assertCoded(response, "DEPARTMENT_BATCH_TOO_LARGE", 400)
        self.assertEqual(response.data["error"], wording.BATCH_TOO_LARGE)
        self.assertFalse(DepartmentMembership.objects.exists())

    def test_an_empty_list_is_refused(self):
        response = self.add()
        self.assertEqual(response.status_code, 400)

    def test_a_department_holds_at_most_the_cap(self):
        with mock.patch("departments.service.MAX_MEMBERS_PER_DEPARTMENT", 1):
            response = self.add(self.t1.id, self.t2.id)
        self.assertCoded(response, "DEPARTMENT_TOO_MANY_TEACHERS", 409)
        self.assertEqual(response.data["error"], wording.TOO_MANY_TEACHERS)
        self.assertFalse(DepartmentMembership.objects.exists())

    def test_edit_switch_changes_and_is_audited_as_a_permission_change(self):
        membership = add_member(self.dept, self.t1)
        response = self.client.patch(
            self.url_member(self.dept, membership), {"can_edit": False}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        membership.refresh_from_db()
        self.assertFalse(membership.can_edit)
        (event,) = events(AuditAction.PERMISSION_CHANGE)
        self.assertEqual(event.target_type, "DepartmentMembership")
        self.assertEqual(event.department_id, self.dept.id)
        self.assertEqual(event.before, {"can_edit": True})
        self.assertEqual(event.after, {"can_edit": False})

    def test_a_patch_body_cannot_move_a_row(self):
        membership = add_member(self.dept, self.t1)
        other = make_department(self.school, "Art")
        response = self.client.patch(
            self.url_member(self.dept, membership),
            {
                "can_edit": False,
                "department": str(other.id),
                "teacher": str(self.t2.id),
                "added_by_id": str(uuid.uuid4()),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.department_id, self.dept.id)
        self.assertEqual(membership.teacher_id, self.t1.id)

    def test_a_non_boolean_switch_is_refused(self):
        membership = add_member(self.dept, self.t1)
        response = self.client.patch(
            self.url_member(self.dept, membership), {"can_edit": "maybe"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_remove_a_member_one_event(self):
        membership = add_member(self.dept, self.t1)
        response = self.client.delete(self.url_member(self.dept, membership))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(DepartmentMembership.objects.exists())
        (event,) = events(AuditAction.DEPARTMENT_MEMBER_REMOVE)
        self.assertEqual(event.metadata["source"], "admin")
        self.assertEqual(event.metadata["teacher_id"], str(self.t1.id))

    def test_a_membership_of_another_department_is_not_found_here(self):
        other = make_department(self.school, "Art")
        membership = add_member(other, self.t1)
        response = self.client.delete(self.url_member(self.dept, membership))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(DepartmentMembership.objects.exists())


class ReasonCodeTextTests(APITestCase):
    def test_the_new_codes_use_the_approved_texts_and_no_remediation(self):
        expected = {
            "DEPARTMENT_NAME_TAKEN": wording.NAME_TAKEN,
            "DEPARTMENT_NAME_INVALID": wording.NAME_INVALID,
            "DEPARTMENT_ALREADY_MEMBER": wording.ALREADY_MEMBER,
            "DEPARTMENT_CONTENT_CHOICE_REQUIRED": wording.CONTENT_CHOICE_REQUIRED,
            "DEPARTMENT_REASSIGN_TARGET_INVALID": wording.REASSIGN_TARGET_INVALID,
            "DEPARTMENT_TOO_MANY_TEACHERS": wording.TOO_MANY_TEACHERS,
            "DEPARTMENT_BATCH_TOO_LARGE": wording.BATCH_TOO_LARGE,
        }
        for code, message in expected.items():
            with self.subTest(code=code):
                spec = REASON_CODES[ReasonCode(code)]
                self.assertEqual(spec.message, message)
                self.assertEqual(spec.remediation, "")
