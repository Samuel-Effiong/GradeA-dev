"""Slice 4: the two membership emails (row T27, guard M16). Sent after the
change commits, one per teacher, never for a reap, a delete or a switch, and
an email problem never undoes the change."""

from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from billing.license_service import LicenseSubscriptionService
from departments import wording
from departments.models import DepartmentMembership
from departments.tests_factories import (
    add_member,
    make_department,
    make_licensed_teacher,
    make_school_with_license,
)

ON = override_settings(DEPARTMENTS_ENABLED=True)
SEND = "departments.notifications.safe_delay"


@ON
class MembershipEmailTests(TestCase):
    def setUp(self):
        self.school, self.admin, self.license, (self.t1, self.t2) = (
            make_school_with_license()
        )
        self.dept = make_department(self.school, "Maths")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def add(self, *teachers):
        return self.client.post(
            f"/api/v1/school-admin/departments/{self.dept.id}/members",
            {"teacher_ids": [str(t.id) for t in teachers]},
            format="json",
        )

    def test_an_admin_add_sends_one_email_after_commit(self):
        with mock.patch(SEND) as send:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.add(self.t1)
            self.assertEqual(response.status_code, 200, response.content)
            send.assert_not_called()
            for callback in callbacks:
                callback()
        self.assertEqual(send.call_count, 1)
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["recipient_list"], [self.t1.email])
        self.assertEqual(
            kwargs["subject"], wording.ADDED_SUBJECT.format(department="Maths")
        )
        self.assertIn(
            wording.ADDED_BODY.format(school=self.school.name, department="Maths"),
            kwargs["merge_data"]["content"],
        )
        self.assertEqual(kwargs["merge_data"]["name"], self.t1.get_full_name())

    def test_an_admin_remove_sends_one_email(self):
        membership = add_member(self.dept, self.t1)
        with mock.patch(SEND) as send:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.delete(
                    f"/api/v1/school-admin/departments/{self.dept.id}"
                    f"/members/{membership.id}"
                )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            send.call_args.kwargs["subject"],
            wording.REMOVED_SUBJECT.format(department="Maths"),
        )
        self.assertEqual(send.call_args.kwargs["recipient_list"], [self.t1.email])

    def test_a_refused_add_sends_nothing(self):
        add_member(self.dept, self.t1)
        with mock.patch(SEND) as send:
            with self.captureOnCommitCallbacks(execute=True):
                self.add(self.t1)
        send.assert_not_called()

    def test_a_rolled_back_change_sends_nothing(self):
        with mock.patch(SEND) as send:
            with self.captureOnCommitCallbacks(execute=True):
                with mock.patch(
                    "departments.service._emit", side_effect=RuntimeError("boom")
                ):
                    response = self.add(self.t1)
        self.assertEqual(response.status_code, 500)
        send.assert_not_called()
        self.assertFalse(DepartmentMembership.objects.exists())

    def test_a_broker_outage_never_undoes_the_change_and_logs_no_address(self):
        with mock.patch(SEND, side_effect=RuntimeError("broker down")):
            with self.assertLogs("departments.notifications", level="WARNING") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.add(self.t1)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(DepartmentMembership.objects.filter(teacher=self.t1).exists())
        text = "\n".join(logs.output)
        self.assertNotIn(self.t1.email, text)
        self.assertIn(str(self.t1.id), text)

    def test_fifty_teachers_queue_exactly_fifty_emails(self):
        teachers = [make_licensed_teacher(self.school, self.license) for _ in range(50)]
        with mock.patch(SEND) as send:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.add(*teachers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(send.call_count, 50)

    def test_the_department_name_is_escaped_in_the_body(self):
        dept = make_department(self.school, "<b>Art</b>")
        with mock.patch(SEND) as send:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(
                    f"/api/v1/school-admin/departments/{dept.id}/members",
                    {"teacher_ids": [str(self.t1.id)]},
                    format="json",
                )
        content = send.call_args.kwargs["merge_data"]["content"]
        self.assertNotIn("<b>Art</b>", content)
        self.assertIn("&lt;b&gt;Art&lt;/b&gt;", content)

    def test_no_email_for_a_switch_a_leave_or_a_department_change(self):
        membership = add_member(self.dept, self.t1)
        with mock.patch(SEND) as send:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.patch(
                    f"/api/v1/school-admin/departments/{self.dept.id}"
                    f"/members/{membership.id}",
                    {"can_edit": False},
                    format="json",
                )
                LicenseSubscriptionService.remove_teacher_from_license(
                    self.license, self.t1
                )
                self.client.patch(
                    f"/api/v1/school-admin/departments/{self.dept.id}",
                    {"name": "Mathematics"},
                    format="json",
                )
        send.assert_not_called()
