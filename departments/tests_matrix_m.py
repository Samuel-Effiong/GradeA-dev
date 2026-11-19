"""Epic F1 M-rows (plan §7.2): every existing path, B asks for A's objects.

B is T1 (D1, can edit) and T2 (D1, restricted); A is T5, who shares D1 with
both. FR-F-10 / BE-F-10: department membership grants access to department
lessons and the shared library "and nothing else"; it must never expose a
colleague's courses, rosters, submissions, grades or credit balance.

These are absolute expectations (they would also catch a path that leaked
before F1): a detail route given A's object answers neither 2xx nor any of
A's private data; a list never contains A's objects. The generic sweep
(M2/M8 and the rest) calls every readable route with every one of A's ids.
Written against INTERFACE.md (ad1fb1d09) only.
"""

import json
from typing import Any
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from audit.enums import AuditAction
from departments.tests_matrix_fixture import (
    API,
    SWEEP_EXCLUDED,
    allocation_id,
    build_world,
    jwt_client,
    object_ids,
    private_canaries,
    read_routes,
    urls_with_ids,
)

flag_on = override_settings(DEPARTMENTS_ENABLED=True)


def body(response):
    content = getattr(response, "content", b"") or b""
    if getattr(response, "streaming", False):
        content = b"".join(response.streaming_content)
    return content.decode("utf-8", "replace")


class _MatrixCase(TestCase):
    w: Any
    world = staticmethod(build_world)

    @classmethod
    def setUpTestData(cls):
        with override_settings(DEPARTMENTS_ENABLED=True):
            cls.w = cls.world()

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        patcher = patch("celery.app.task.Task.apply_async")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.a = self.w.data["t5"]
        self.bs = {"T1": self.w.t1, "T2": self.w.t2}

    def get(self, user, url, **params):
        return jwt_client(user).get(url, params or None)

    def assertHidden(self, url, **params):
        """For each B: not 2xx, and none of A's private data in the body."""
        for label, b in self.bs.items():
            with self.subTest(b=label, url=url):
                response = self.get(b, url, **params)
                self.assertNotIn(response.status_code // 100, (2,), body(response))
                text = body(response)
                for canary in private_canaries(self.a):
                    self.assertNotIn(canary, text)

    def assertExcludes(self, url, *ids, **params):
        """For each B: a list that answers, holding none of A's ids or data."""
        for label, b in self.bs.items():
            with self.subTest(b=label, url=url):
                response = self.get(b, url, **params)
                text = body(response)
                for value in [*map(str, ids), *private_canaries(self.a)]:
                    self.assertNotIn(value, text)


@flag_on
class M01Courses(_MatrixCase):
    def test_a_colleagues_course_is_hidden(self):
        self.assertHidden(f"{API}/course/{self.a.course.id}")
        self.assertHidden(f"{API}/course/{self.a.course.id}/students")
        self.assertHidden(f"{API}/course/{self.a.course.id}/student-summary")
        self.assertHidden(f"{API}/course/{self.a.course.id}/grade-breakdown")

    def test_course_lists_leave_it_out(self):
        self.assertExcludes(f"{API}/course", self.a.course.id)
        self.assertExcludes(f"{API}/course/my-courses", self.a.course.id)


@flag_on
class M03Rosters(_MatrixCase):
    def test_a_colleagues_enrollment_is_hidden(self):
        self.assertHidden(f"{API}/student-course/{self.a.enrollment.id}")

    def test_roster_lists_leave_it_out(self):
        self.assertExcludes(f"{API}/student-course", self.a.enrollment.id)
        self.assertExcludes(f"{API}/student-course/my-students", self.a.student.id)


@flag_on
class M04RosterAccounts(_MatrixCase):
    def test_a_colleague_and_their_student_are_hidden(self):
        self.assertHidden(f"{API}/users/{self.a.student.id}")

    def test_the_user_list_and_its_filters_leave_them_out(self):
        self.assertExcludes(f"{API}/users", self.a.student.id)
        self.assertExcludes(
            f"{API}/users", self.a.student.id, enrollments__course=str(self.a.course.id)
        )


@flag_on
class M05Submissions(_MatrixCase):
    def test_a_colleagues_submission_is_hidden(self):
        self.assertHidden(f"{API}/submissions/{self.a.submission.id}")

    def test_the_submission_list_leaves_it_out(self):
        self.assertExcludes(f"{API}/submissions", self.a.submission.id)
        self.assertExcludes(
            f"{API}/submissions",
            self.a.submission.id,
            assignment=str(self.a.assignment.id),
        )


@flag_on
class M06SubmissionTasks(_MatrixCase):
    def test_task_lists_hold_nothing_of_a_colleague(self):
        self.assertExcludes(f"{API}/tasks/sessions", self.a.course.id)
        self.assertHidden(f"{API}/tasks/session-results/{self.a.course.id}")


@flag_on
class M07Assignments(_MatrixCase):
    def test_a_colleagues_assignment_is_hidden(self):
        self.assertHidden(f"{API}/assignments/{self.a.assignment.id}")
        self.assertHidden(f"{API}/assignments/{self.a.assignment.id}/download-pdf")
        self.assertHidden(f"{API}/topics/{self.a.topic.id}")

    def test_assignment_lists_leave_it_out(self):
        self.assertExcludes(f"{API}/assignments", self.a.assignment.id)
        self.assertExcludes(
            f"{API}/assignments", self.a.assignment.id, course=str(self.a.course.id)
        )
        self.assertExcludes(f"{API}/assignment-generation-sessions", self.a.course.id)
        self.assertExcludes(f"{API}/topics", self.a.topic.id)


@flag_on
@override_settings(STUDENT_DATA_TOOLS_ENABLED=True)
class M09GradesExport(_MatrixCase):
    def test_exporting_a_colleagues_student_is_refused(self):
        self.assertHidden(f"{API}/students/{self.a.student.id}/data-export")


@flag_on
class M10CreditBalance(_MatrixCase):
    def test_a_colleagues_wallet_and_bucket_are_hidden(self):
        self.assertHidden(f"{API}/credit-wallets/{self.a.wallet.id}")
        self.assertHidden(f"{API}/credit-buckets/{self.a.bucket.id}")

    def test_credit_lists_leave_them_out(self):
        for path in (
            "credit-wallets",
            "credit-buckets",
            "credit-ledgers",
            "credit-usage-logs",
            "subscription/credits/wallet",
            "subscription/credits/buckets",
            "subscription/credits/ledger",
            "subscription/credits/summary",
        ):
            self.assertExcludes(f"{API}/{path}", self.a.wallet.id, self.a.bucket.id)


@flag_on
class M11LicenseBalance(_MatrixCase):
    def test_a_colleagues_allocation_is_hidden(self):
        mine = allocation_id(self.a.teacher)
        self.assertIsNotNone(mine)
        self.assertHidden(f"{API}/school-credit-allocations/{mine}")
        self.assertExcludes(f"{API}/school-credit-allocations", mine)
        self.assertExcludes(f"{API}/license-subscriptions", mine)


@flag_on
class M12DepartmentPayloadsCarryNoUserSerializer(_MatrixCase):
    """Exact key sets on the member routes (a nested CustomUserSerializer
    would bring credit_wallet and settings)."""

    def test_my_departments_has_exactly_the_agreed_keys(self):
        response = self.get(self.w.t1, reverse("departments-mine"))
        self.assertEqual(response.status_code, 200, body(response))
        self.assertTrue(response.json())
        for row in response.json():
            self.assertEqual(set(row), {"id", "name", "can_edit"})

    def test_the_roster_has_exactly_name_and_role(self):
        response = self.get(
            self.w.t1,
            reverse("departments-roster", kwargs={"department_id": self.w.d1.id}),
        )
        self.assertEqual(response.status_code, 200, body(response))
        self.assertTrue(response.json())
        for row in response.json():
            self.assertEqual(set(row), {"name", "role"})
        text = body(response)
        for leak in ("credit_wallet", "settings", "email", "balance"):
            self.assertNotIn(leak, text)


@flag_on
class M13Audit(_MatrixCase):
    def test_teachers_cannot_read_the_school_audit_log(self):
        for label, b in self.bs.items():
            with self.subTest(b=label):
                response = self.get(b, f"{API}/school-admin/audit/events")
                self.assertIn(response.status_code, (403, 404))

    def test_another_schools_admin_filtering_by_our_department_gets_nothing(self):
        response = self.get(
            self.w.a2,
            f"{API}/school-admin/audit/events",
            department_id=str(self.w.d1.id),
        )
        self.assertEqual(response.status_code, 200, body(response))
        payload = response.json()
        if isinstance(payload, dict) and "data" in payload:
            payload = payload["data"]  # the house envelope
        if isinstance(payload, dict):
            payload = payload.get("results", payload.get("events", payload))
        self.assertIsInstance(payload, list, response.content)
        self.assertEqual(payload, [])


@flag_on
class M14FiltersNeverWiden(_MatrixCase):
    """A department parameter on an existing list is ignored or refused,
    never widening: the answer equals the one without it, or is a 4xx."""

    LISTS = (
        "course",
        "student-course",
        "users",
        "submissions",
        "assignments",
        "topics",
        "credit-wallets",
        "credit-buckets",
        "school-credit-allocations",
        "tasks/sessions",
    )

    def test_department_parameters_change_nothing(self):
        for label, b in self.bs.items():
            for path in self.LISTS:
                with self.subTest(b=label, path=path):
                    plain = self.get(b, f"{API}/{path}")
                    for param in ("department", "department_id", "departments"):
                        cache.clear()
                        widened = self.get(
                            b, f"{API}/{path}", **{param: str(self.w.d1.id)}
                        )
                        if widened.status_code // 100 == 4:
                            continue
                        self.assertEqual(widened.status_code, plain.status_code)
                        self.assertEqual(body(widened), body(plain))


@flag_on
class M15Caches(_MatrixCase):
    def test_a_removed_member_is_not_served_their_cached_list(self):
        url = reverse("departments-mine")
        warm = self.get(self.w.t1, url)
        self.assertIn(str(self.w.d1.id), body(warm))
        from departments.models import DepartmentMembership

        membership = DepartmentMembership.objects.get(
            department=self.w.d1, teacher=self.w.t1
        )
        response = jwt_client(self.w.a1).delete(
            reverse(
                "departments-admin-member-detail",
                kwargs={"department_id": self.w.d1.id, "membership_id": membership.id},
            )
        )
        self.assertEqual(response.status_code, 204, body(response))
        self.assertNotIn(str(self.w.d1.id), body(self.get(self.w.t1, url)))

    def test_each_viewer_gets_their_own_list(self):
        url = reverse("departments-mine")
        t1 = self.get(self.w.t1, url).json()
        t3 = self.get(self.w.t3, url).json()
        self.assertEqual({row["id"] for row in t1}, {str(self.w.d1.id)})
        self.assertEqual({row["id"] for row in t3}, {str(self.w.d2.id)})

    def test_existing_cached_lists_do_not_change_when_a_member_is_added(self):
        url = f"{API}/course"
        before = body(self.get(self.w.t4, url))
        from departments.tests_factories import add_member

        add_member(self.w.d1, self.w.t4)
        self.assertEqual(body(self.get(self.w.t4, url)), before)
        cache.clear()
        self.assertEqual(body(self.get(self.w.t4, url)), before)


class M16NoFanOut(TestCase):
    """Departments defines no Celery task; mail goes only through
    departments/notifications.py (the two membership emails, owner Q8)."""

    def test_departments_defines_no_celery_task_and_sends_mail_in_one_place(self):
        import ast
        import pathlib

        import departments

        root = pathlib.Path(departments.__file__).parent
        for path in root.rglob("*.py"):
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            tree = ast.parse(path.read_text())
            names = {
                getattr(n, "attr", None) or getattr(n, "id", None)
                for n in ast.walk(tree)
                if isinstance(n, (ast.Name, ast.Attribute))
            }
            with self.subTest(path=path.name):
                self.assertNotIn("shared_task", names)
                if path.name != "notifications.py":
                    for sender in ("send_mail", "send_email_task", "EmailMessage"):
                        self.assertNotIn(sender, names)


@flag_on
class M16MembershipEmailGoesToTheTeacherOnly(_MatrixCase):
    def test_adding_a_teacher_emails_that_teacher_and_nobody_else(self):
        sent = []

        def record(*args, **kwargs):
            sent.append(json.dumps([args, kwargs], default=str))

        with patch("departments.notifications.safe_delay", side_effect=record), patch(
            "AutoGrader.tasks.send_email_task.delay", side_effect=record
        ), self.captureOnCommitCallbacks(execute=True):
            response = jwt_client(self.w.a1).post(
                reverse(
                    "departments-admin-members", kwargs={"department_id": self.w.d1.id}
                ),
                {"teacher_ids": [str(self.w.t4.id)]},
                format="json",
            )
        self.assertEqual(response.status_code, 200, body(response))
        self.assertTrue(sent)
        text = " ".join(sent)
        self.assertIn(self.w.t4.email, text)
        for other in (self.w.t1, self.w.t2, self.w.t5, self.w.t6, self.w.a1):
            self.assertNotIn(other.email, text)


@flag_on
class M17DjangoAdmin(_MatrixCase):
    def test_a_superuser_reaches_the_admin_and_sees_no_wallet(self):
        from departments.models import DepartmentMembership

        self.client.force_login(self.w.su)
        listing = self.client.get("/admin/departments/department/")
        self.assertEqual(listing.status_code, 200)
        membership = DepartmentMembership.objects.filter(teacher=self.w.t5).first()
        assert membership is not None
        page = self.client.get(
            f"/admin/departments/departmentmembership/{membership.pk}/change/"
        )
        self.assertEqual(page.status_code, 200)
        for canary in private_canaries(self.a):
            self.assertNotIn(canary, body(page))

    def test_staff_who_are_not_superusers_are_kept_out(self):
        from users.models import CustomUser, UserTypes

        staff = CustomUser.objects.create_user(
            email="staff@f1matrix.test",
            password="Str0ng-f1-staff!",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.w.s1,
            is_staff=True,
        )
        self.client.force_login(staff)
        for path in (
            "/admin/departments/department/",
            "/admin/departments/departmentmembership/",
        ):
            with self.subTest(path=path):
                self.assertIn(self.client.get(path).status_code, (302, 403))


@flag_on
class MSweepEveryReadableRoute(_MatrixCase):
    """M2, M8 and every other readable route: called by B with each of A's
    object ids in every path parameter, no response holds A's private data.
    A positive control proves the canaries are reachable where allowed."""

    #: A's own reads that must show A's canary (the sweep is not vacuous).
    CONTROL = (
        ("course-detail", "course"),
        ("assignment-detail", "assignment"),
        ("student-submission-detail", "submission"),
        ("credit-bucket-list", None),
    )

    def ids(self):
        return object_ids(self.a, allocation_id(self.a.teacher))

    def test_no_route_gives_b_any_of_as_private_data(self):
        routes = read_routes()
        self.assertGreater(len(routes), 100)
        ids = self.ids()
        for name, kwargs in sorted(routes.items()):
            if name in SWEEP_EXCLUDED:
                continue
            for url in urls_with_ids(name, kwargs, ids):
                for label, b in self.bs.items():
                    with self.subTest(route=name, url=url, b=label):
                        text = body(self.get(b, url))
                        for canary in private_canaries(self.a):
                            self.assertNotIn(canary, text)

    def test_positive_control_a_sees_their_own_canary(self):
        from django.urls import reverse

        for name, kind in self.CONTROL:
            with self.subTest(route=name):
                url = reverse(name, kwargs={"pk": self.ids()[kind]} if kind else None)
                response = self.get(self.a.teacher, url)
                self.assertEqual(response.status_code, 200, body(response))
                text = body(response)
                self.assertTrue(
                    any(c in text for c in private_canaries(self.a)),
                    f"{name}: A's own canary not visible, the sweep would be vacuous",
                )


class AuditActionsExist(TestCase):
    def test_the_department_actions_the_matrix_relies_on_exist(self):
        for action in ("DEPARTMENT_MEMBER_ADD", "DEPARTMENT_MEMBER_REMOVE"):
            self.assertTrue(hasattr(AuditAction, action))
