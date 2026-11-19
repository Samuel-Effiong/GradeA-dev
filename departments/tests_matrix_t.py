"""Epic F1 T-rows owned by the Fixer (plan §7.2): T3-T7, T13, T15, T17-T21.

Written against INTERFACE.md (ad1fb1d09 and the slice 1 note) only, through
the routes by URL name: departments-admin-list, departments-admin-detail,
departments-admin-members, departments-admin-member-detail, departments-mine,
departments-roster (kwargs department_id, membership_id).

Statuses (agreed): missing or out-of-scope department, an individual teacher
on a roster and an admin with no school get the house 404, byte-identical; a
teacher on an admin route gets 403; a student 403 on both prefixes; a school
admin 403 on the teacher prefix; a super admin 403 on both; anonymous 401.
"""

import importlib
import threading
import time
import uuid
from typing import Any

from django.core.cache import cache
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from departments.tests_matrix_fixture import (
    API,
    all_users,
    build_world,
    jwt_client,
    private_canaries,
)

flag_on = override_settings(DEPARTMENTS_ENABLED=True)
flag_off = override_settings(DEPARTMENTS_ENABLED=False)


def mine_url():
    return reverse("departments-mine")


def roster_url(department_id):
    return reverse("departments-roster", kwargs={"department_id": department_id})


def admin_list_url():
    return reverse("departments-admin-list")


def admin_detail_url(department_id):
    return reverse("departments-admin-detail", kwargs={"department_id": department_id})


def members_url(department_id):
    return reverse("departments-admin-members", kwargs={"department_id": department_id})


def member_url(department_id, membership_id):
    return reverse(
        "departments-admin-member-detail",
        kwargs={"department_id": department_id, "membership_id": membership_id},
    )


def ids(response):
    return {row["id"] for row in response.json()}


class _WorldCase(TestCase):
    w: Any

    @classmethod
    def setUpTestData(cls):
        with override_settings(DEPARTMENTS_ENABLED=True):
            cls.w = build_world()

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def get(self, user, url):
        return jwt_client(user).get(url)

    def membership(self, department, teacher):
        from departments.models import DepartmentMembership

        return DepartmentMembership.objects.get(department=department, teacher=teacher)

    def every_route(self):
        """(method, url) for every F1 route against D1 and T1's membership."""
        d1 = self.w.d1.id
        m = self.membership(self.w.d1, self.w.t1).id
        return [
            ("get", mine_url()),
            ("get", roster_url(d1)),
            ("get", admin_list_url()),
            ("post", admin_list_url()),
            ("get", admin_detail_url(d1)),
            ("patch", admin_detail_url(d1)),
            ("delete", admin_detail_url(d1)),
            ("post", members_url(d1)),
            ("patch", member_url(d1, m)),
            ("delete", member_url(d1, m)),
        ]


@flag_on
class T03TeacherList(_WorldCase):
    def test_no_department_or_individual_gets_an_empty_list(self):
        for user in (self.w.t4, self.w.i1):
            with self.subTest(user=user.email):
                response = self.get(user, mine_url())
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json(), [])

    def test_each_teacher_sees_exactly_their_departments(self):
        expected = {
            self.w.t1: {self.w.d1.id},
            self.w.t2: {self.w.d1.id},
            self.w.t3: {self.w.d2.id},
            self.w.t5: {self.w.d1.id, self.w.d2.id},
            self.w.t8: {self.w.d3.id},
        }
        for user, departments in expected.items():
            with self.subTest(user=user.email):
                response = self.get(user, mine_url())
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(ids(response), {str(d) for d in departments})
                self.assertEqual(len(response.json()), len(departments))

    def test_the_removed_teacher_sees_nothing(self):
        response = self.get(self.w.t6, mine_url())
        self.assertIn(response.status_code, (200, 403))
        if response.status_code == 200:
            self.assertEqual(response.json(), [])

    def test_admins_list_their_own_schools_departments_only(self):
        for admin, departments in (
            (self.w.a1, {self.w.d1.id, self.w.d2.id}),
            (self.w.a1b, {self.w.d1.id, self.w.d2.id}),
            (self.w.a2, {self.w.d3.id}),
        ):
            with self.subTest(admin=admin.email):
                response = self.get(admin, admin_list_url())
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(ids(response), {str(d) for d in departments})

    def test_an_admin_with_no_school_gets_an_empty_list(self):
        response = self.get(self.w.a0, admin_list_url())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), [])

    def test_role_statuses(self):
        w = self.w
        cases = [
            (w.st, mine_url(), 403),
            (w.st, admin_list_url(), 403),
            (w.a1, mine_url(), 403),
            (w.su, mine_url(), 403),
            (w.su, admin_list_url(), 403),
            (w.t1, admin_list_url(), 403),
            (None, mine_url(), 401),
            (None, admin_list_url(), 401),
        ]
        for user, url, status in cases:
            with self.subTest(user=getattr(user, "email", "anon"), url=url):
                self.assertEqual(self.get(user, url).status_code, status)


@flag_on
class T04GuessedIds(_WorldCase):
    def test_every_out_of_scope_lookup_is_the_same_404(self):
        w = self.w
        d1 = w.d1.id
        unknown = uuid.uuid4()
        cases = [
            (w.t3, roster_url(d1)),
            (w.i1, roster_url(d1)),
            (w.t8, roster_url(d1)),
            (w.t6, roster_url(d1)),
            (w.t4, roster_url(d1)),
            (w.t1, roster_url(unknown)),
            (w.t1, roster_url(w.d3.id)),
            (w.a2, admin_detail_url(d1)),
            (w.a0, admin_detail_url(d1)),
            (w.a1, admin_detail_url(unknown)),
            (w.a1, admin_detail_url(w.d3.id)),
        ]
        bodies = set()
        for user, url in cases:
            with self.subTest(user=user.email, url=url):
                response = self.get(user, url)
                self.assertEqual(response.status_code, 404, response.content)
                bodies.add(response.content)
        self.assertEqual(len(bodies), 1, bodies)

    def test_a_malformed_id_is_the_same_404(self):
        good = self.get(self.w.t3, roster_url(self.w.d1.id))
        base = roster_url(self.w.d1.id).replace(str(self.w.d1.id), "not-a-uuid")
        response = self.get(self.w.t3, base)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, good.content)


@flag_on
class T05RosterFields(_WorldCase):
    def test_the_roster_is_exactly_name_and_role_admins_first(self):
        response = self.get(self.w.t1, roster_url(self.w.d1.id))
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()
        for row in rows:
            self.assertEqual(set(row), {"name", "role"})
        roles = [row["role"] for row in rows]
        self.assertEqual(roles, sorted(roles, key=lambda r: r != "School admin"))
        admins = [row["name"] for row in rows if row["role"] == "School admin"]
        teachers = [row["name"] for row in rows if row["role"] == "Teacher"]
        self.assertEqual(len(admins), 2)
        self.assertEqual(admins, sorted(admins))
        self.assertEqual(teachers, sorted(teachers))
        # Live members of D1: T1, T2, T5. T6 left the license.
        self.assertEqual(len(teachers), 3)
        for user in (self.w.t1, self.w.t2, self.w.t5):
            self.assertTrue(any(user.first_name in name for name in teachers))
        self.assertFalse(any(self.w.t6.first_name in name for name in teachers))

    def test_the_roster_shows_no_email_id_balance_or_flag(self):
        text = self.get(self.w.t1, roster_url(self.w.d1.id)).content.decode()
        for user in (self.w.t1, self.w.t2, self.w.t5, self.w.a1, self.w.a1b):
            self.assertNotIn(user.email, text)
            self.assertNotIn(str(user.id), text)
        for leak in ("can_edit", "balance", "credit", "course"):
            self.assertNotIn(leak, text)


@flag_on
class T06OwnFlags(_WorldCase):
    def test_a_restricted_member_sees_their_own_flag(self):
        rows = self.get(self.w.t2, mine_url()).json()
        self.assertEqual(
            rows, [{"id": str(self.w.d1.id), "name": self.w.d1.name, "can_edit": False}]
        )
        rows = self.get(self.w.t1, mine_url()).json()
        self.assertEqual(rows[0]["can_edit"], True)


@flag_on
class T07Canary(_WorldCase):
    def test_no_department_route_shows_anyones_private_data(self):
        canaries = [c for data in self.w.data.values() for c in private_canaries(data)]
        for label, user in all_users(self.w).items():
            for method, url in self.every_route():
                if method != "get":
                    continue
                with self.subTest(user=label, url=url):
                    text = self.get(user, url).content.decode()
                    for canary in canaries:
                        self.assertNotIn(canary, text)


@flag_on
class T13TeachersCannotWrite(_WorldCase):
    def snapshot(self):
        from departments.models import Department, DepartmentMembership

        return (
            sorted(Department.objects.values_list("id", "name", "description")),
            sorted(
                DepartmentMembership.objects.values_list(
                    "id", "department_id", "teacher_id", "can_edit"
                )
            ),
        )

    def test_every_admin_write_is_403_for_teachers_and_changes_nothing(self):
        before = self.snapshot()
        own = self.membership(self.w.d1, self.w.t2).id
        bodies = {
            "post": {"name": "Teacher made", "teacher_ids": [str(self.w.t4.id)]},
            "patch": {"name": "Renamed by teacher", "can_edit": True},
            "delete": {},
        }
        for teacher in (self.w.t1, self.w.t2, self.w.t5, self.w.i1):
            client = jwt_client(teacher)
            routes = [r for r in self.every_route() if r[0] != "get"]
            routes.append(("patch", member_url(self.w.d1.id, own)))
            for method, url in routes:
                with self.subTest(teacher=teacher.email, method=method, url=url):
                    response = getattr(client, method)(
                        url, bodies[method], format="json"
                    )
                    self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(self.snapshot(), before)

    def test_no_self_unrestrict(self):
        own = self.membership(self.w.d1, self.w.t2)
        response = jwt_client(self.w.t2).patch(
            member_url(self.w.d1.id, own.id), {"can_edit": True}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        own.refresh_from_db()
        self.assertFalse(own.can_edit)


@flag_off
class T15FlagOff(_WorldCase):
    def test_every_route_is_404_for_everyone(self):
        unknown = jwt_client(None).get(f"{API}/no-such-matrix-route")
        self.assertEqual(unknown.status_code, 404)
        for label, user in all_users(self.w).items():
            client = jwt_client(user)
            for method, url in self.every_route():
                with self.subTest(user=label, method=method, url=url):
                    response = getattr(client, method)(url, {}, format="json")
                    # Lead, 2026-11-19: the house 404 runs before authentication,
                    # so everyone, anonymous included, gets the unknown-URL body.
                    self.assertEqual(response.status_code, 404, response.content)
                    self.assertEqual(response.content, unknown.content)


@flag_on
class T03DeniedEvents(_WorldCase):
    """A refused signed-in person on an admin route leaves exactly one
    ADMIN_ACTION event with outcome DENIED (lead, 2026-11-19)."""

    def test_each_refusal_leaves_one_denied_event(self):
        from audit.models import AuditEvent

        w = self.w
        for user, method in (
            (w.t1, "get"),
            (w.st, "get"),
            (w.su, "post"),
            (w.t1, "post"),
        ):
            with self.subTest(user=user.email, method=method):
                before = AuditEvent.objects.filter(
                    actor_id=user.id, action="ADMIN_ACTION", outcome="DENIED"
                ).count()
                response = getattr(jwt_client(user), method)(
                    admin_list_url(), {"name": "Matrix Denied"}, format="json"
                )
                self.assertEqual(response.status_code, 403, response.content)
                after = AuditEvent.objects.filter(
                    actor_id=user.id, action="ADMIN_ACTION", outcome="DENIED"
                ).count()
                self.assertEqual(after - before, 1)


@flag_on
class T17InactiveSchool(_WorldCase):
    def test_a_soft_deleted_school_shows_nothing_to_anyone_and_keeps_the_data(self):
        from classrooms.models import School
        from departments.models import Department, DepartmentMembership

        School.objects.filter(pk=self.w.s1.pk).update(is_active=False)
        for teacher in (self.w.t1, self.w.t5):
            with self.subTest(teacher=teacher.email):
                self.assertEqual(self.get(teacher, mine_url()).json(), [])
                self.assertEqual(
                    self.get(teacher, roster_url(self.w.d1.id)).status_code, 404
                )
        admin_list = self.get(self.w.a1, admin_list_url())
        self.assertIn(admin_list.status_code, (200, 403))
        if admin_list.status_code == 200:
            self.assertEqual(admin_list.json(), [])
        self.assertIn(
            self.get(self.w.a1, admin_detail_url(self.w.d1.id)).status_code, (403, 404)
        )
        self.assertTrue(Department.objects.filter(pk=self.w.d1.pk).exists())
        self.assertTrue(
            DepartmentMembership.objects.filter(department=self.w.d1).exists()
        )
        # The other school is untouched.
        self.assertEqual(ids(self.get(self.w.t8, mine_url())), {str(self.w.d3.id)})


@flag_on
class T18MultipleAdmins(_WorldCase):
    def test_any_admin_of_the_school_may_manage_and_no_other(self):
        client = jwt_client(self.w.a1b)
        created = client.post(admin_list_url(), {"name": "Matrix Arts"}, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        renamed = client.patch(
            admin_detail_url(self.w.d1.id), {"name": "Matrix Sciences"}, format="json"
        )
        self.assertEqual(renamed.status_code, 200, renamed.content)
        foreign = jwt_client(self.w.a2).patch(
            admin_detail_url(self.w.d1.id), {"name": "Taken over"}, format="json"
        )
        self.assertEqual(foreign.status_code, 404)
        self.w.d1.refresh_from_db()
        self.assertEqual(self.w.d1.name, "Matrix Sciences")


@flag_on
class T19MassAssignment(_WorldCase):
    def test_create_ignores_school_and_creator_in_the_body(self):
        from departments.models import Department

        response = jwt_client(self.w.a1).post(
            admin_list_url(),
            {
                "name": "Matrix Music",
                "school": str(self.w.s2.id),
                "created_by_id": str(self.w.a2.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        made = Department.objects.get(name="Matrix Music")
        self.assertEqual(made.school_id, self.w.s1.id)
        self.assertEqual(str(made.created_by_id), str(self.w.a1.id))

    def test_rename_cannot_move_a_department(self):
        jwt_client(self.w.a1).patch(
            admin_detail_url(self.w.d1.id),
            {"name": "Matrix Science", "school": str(self.w.s2.id)},
            format="json",
        )
        self.w.d1.refresh_from_db()
        self.assertEqual(self.w.d1.school_id, self.w.s1.id)

    def test_member_writes_cannot_move_a_row_or_change_who_added_it(self):
        from departments.models import DepartmentMembership

        jwt_client(self.w.a1).post(
            members_url(self.w.d1.id),
            {
                "teacher_ids": [str(self.w.t4.id)],
                "department": str(self.w.d3.id),
                "added_by": str(self.w.a2.id),
                "added_by_id": str(self.w.a2.id),
            },
            format="json",
        )
        row = DepartmentMembership.objects.get(teacher=self.w.t4)
        self.assertEqual(row.department_id, self.w.d1.id)
        self.assertEqual(str(row.added_by_id), str(self.w.a1.id))
        t1 = self.membership(self.w.d1, self.w.t1)
        jwt_client(self.w.a1).patch(
            member_url(self.w.d1.id, t1.id),
            {
                "can_edit": False,
                "teacher": str(self.w.t8.id),
                "department": str(self.w.d2.id),
            },
            format="json",
        )
        t1.refresh_from_db()
        self.assertEqual(
            (t1.teacher_id, t1.department_id), (self.w.t1.id, self.w.d1.id)
        )
        self.assertFalse(
            DepartmentMembership.objects.filter(
                department=self.w.d3, teacher=self.w.t4
            ).exists()
        )

    def test_a_foreign_teacher_cannot_be_added(self):
        from departments.models import DepartmentMembership

        response = jwt_client(self.w.a1).post(
            members_url(self.w.d1.id),
            {"teacher_ids": [str(self.w.t8.id), str(self.w.st.id), str(self.w.i1.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["successful"], 0)
        self.assertFalse(
            DepartmentMembership.objects.filter(
                department=self.w.d1, teacher__in=[self.w.t8, self.w.st, self.w.i1]
            ).exists()
        )


@flag_on
class T21MovingSchools(_WorldCase):
    def test_a_teacher_moved_to_another_school_keeps_nothing_of_the_first(self):
        from departments.models import DepartmentMembership
        from departments.tests_matrix_fixture import add_to_license, remove_from_license

        t5 = self.w.t5
        remove_from_license(self.w.a1, self.w.l1, t5)
        self.assertFalse(
            DepartmentMembership.objects.filter(
                teacher=t5, department__school=self.w.s1
            ).exists()
        )
        add_to_license(self.w.a2, self.w.l2, [t5])
        self.assertEqual(self.get(t5, mine_url()).json(), [])
        self.assertEqual(self.get(t5, roster_url(self.w.d1.id)).status_code, 404)
        response = jwt_client(self.w.a2).post(
            members_url(self.w.d3.id), {"teacher_ids": [str(t5.id)]}, format="json"
        )
        self.assertEqual(response.json()["successful"], 1, response.content)
        self.assertEqual(ids(self.get(t5, mine_url())), {str(self.w.d3.id)})
        self.assertNotIn(
            t5.first_name,
            self.get(self.w.t1, roster_url(self.w.d1.id)).content.decode(),
        )


@flag_on
class T20FlagRace(TransactionTestCase):
    """can_member_edit(..., lock=True) reads the membership FOR SHARE; the flag
    PATCH takes it FOR UPDATE. While an edit holds its read, the PATCH waits;
    once the PATCH commits, the next locked read says no."""

    def setUp(self):
        from departments.tests_factories import (
            add_member,
            make_department,
            make_school_with_license,
        )

        cache.clear()
        self.school, self.admin, _, (self.teacher, _) = make_school_with_license(
            teachers=2
        )
        self.department = make_department(self.school)
        self.member = add_member(self.department, self.teacher, can_edit=True)

    def test_a_flag_change_waits_for_an_edit_in_flight_and_then_wins(self):
        access = importlib.import_module("departments.access")

        holding = threading.Event()
        release = threading.Event()
        seen = {}

        def edit_in_flight():
            try:
                with transaction.atomic():
                    seen["first"] = access.can_member_edit(
                        self.teacher, self.department, "lessons", lock=True
                    )
                    holding.set()
                    release.wait(10)
            finally:
                connection.close()

        def patch_flag():
            try:
                seen["patch"] = (
                    jwt_client(self.admin)
                    .patch(
                        member_url(self.department.id, self.member.id),
                        {"can_edit": False},
                        format="json",
                    )
                    .status_code
                )
                seen["patched_at"] = time.monotonic()
            finally:
                connection.close()

        editor = threading.Thread(target=edit_in_flight)
        editor.start()
        self.assertTrue(holding.wait(10))
        patcher = threading.Thread(target=patch_flag)
        patcher.start()
        time.sleep(0.5)
        self.assertNotIn(
            "patch", seen, "the flag PATCH did not wait for the edit's lock"
        )
        released_at = time.monotonic()
        release.set()
        editor.join(10)
        patcher.join(10)
        self.assertTrue(seen["first"])
        self.assertEqual(seen["patch"], 200)
        self.assertGreaterEqual(seen["patched_at"], released_at)
        with transaction.atomic():
            self.assertFalse(
                access.can_member_edit(
                    self.teacher, self.department, "library", lock=True
                )
            )
