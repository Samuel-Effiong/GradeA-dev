"""Credit requests (owner, 2026-11-27, beta): the approve route is hidden from
the API docs but still works exactly as before (the frontend gives overage
credit another way; removing approve needs its own yes), and every reply
that returns a request carries the requesting teacher's email.
"""

from unittest.mock import patch

from drf_spectacular import extensions
from rest_framework.test import APIClient

from billing.tests.test_credit_requests import (
    BASE,
    CUSTOMER,
    ON,
    SEND,
    SESSION_CREATE,
    Fixture,
    fake_session,
)

APPROVE_PATH = "/api/v1/billing/credit-requests/{id}/approve"


def generated_schema():
    from drf_spectacular.generators import SchemaGenerator

    # Schema generation changes every extension's target_class; restore.
    registered = {
        ext
        for base in (
            extensions.OpenApiAuthenticationExtension,
            extensions.OpenApiSerializerExtension,
            extensions.OpenApiSerializerFieldExtension,
            extensions.OpenApiViewExtension,
            extensions.OpenApiFilterExtension,
        )
        for ext in base._registry
    }
    saved = {ext: ext.__dict__.get("target_class") for ext in registered}
    try:
        return SchemaGenerator().get_schema(request=None, public=True)
    finally:
        for ext, target in saved.items():
            ext.target_class = target


class ApproveHiddenFromDocsTests(Fixture):
    def test_the_schema_has_no_approve_path_but_keeps_the_others(self):
        paths = generated_schema()["paths"]
        self.assertNotIn(APPROVE_PATH, paths)
        for kept in (
            "/api/v1/billing/credit-requests",
            "/api/v1/billing/credit-requests/counts",
            "/api/v1/billing/credit-requests/{id}/decline",
            "/api/v1/billing/credit-requests/{id}/withdraw",
        ):
            self.assertIn(kept, paths)

    @ON
    def test_the_approve_route_still_answers_as_before(self):
        self.make_school()
        request = self.new_request()
        api = APIClient()
        api.force_authenticate(self.admin)
        with patch(CUSTOMER, return_value="cus_cr"), patch(
            SESSION_CREATE, return_value=fake_session()
        ):
            approved = api.post(
                f"{BASE}/{request.pk}/approve",
                {
                    "payment_method": "stripe",
                    "success_url": "https://app.test/ok",
                    "cancel_url": "https://app.test/cancel",
                },
                format="json",
            )
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(approved.data["purchase"]["action"], "checkout")
        self.assertEqual(approved.data["request"]["status"], "APPROVED")
        self.assertEqual(approved.data["request"]["teacher_email"], self.teacher.email)


class TeacherEmailTests(Fixture):
    def setUp(self):
        self.make_school()
        self.api = APIClient()

    def as_user(self, user):
        self.api.force_authenticate(user)
        return self.api

    @ON
    def test_the_create_and_list_replies_carry_the_teachers_email(self):
        client = self.as_user(self.teacher)
        with patch(SEND):
            created = client.post(BASE, {"requested_blocks": 2}, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.data["teacher_email"], self.teacher.email)
        listed = client.get(BASE).data
        self.assertEqual([row["teacher_email"] for row in listed], [self.teacher.email])

    @ON
    def test_admins_see_their_licenses_teacher_emails_and_teachers_only_their_own(
        self,
    ):
        self.new_request()
        self.new_request(teacher=self.other)
        both = {self.teacher.email, self.other.email}
        for viewer in (self.admin, self.super_admin):
            emails = {
                row["teacher_email"] for row in self.as_user(viewer).get(BASE).data
            }
            self.assertEqual(emails, both, viewer.email)
        own = [row["teacher_email"] for row in self.as_user(self.other).get(BASE).data]
        self.assertEqual(own, [self.other.email])

    @ON
    def test_decline_and_withdraw_replies_carry_it_too(self):
        declined_row = self.new_request()
        declined = self.as_user(self.admin).post(
            f"{BASE}/{declined_row.pk}/decline", {"reason": "x"}, format="json"
        )
        self.assertEqual(declined.status_code, 200, declined.content)
        self.assertEqual(declined.data["teacher_email"], self.teacher.email)
        withdrawn_row = self.new_request(teacher=self.other)
        withdrawn = self.as_user(self.other).post(f"{BASE}/{withdrawn_row.pk}/withdraw")
        self.assertEqual(withdrawn.status_code, 200, withdrawn.content)
        self.assertEqual(withdrawn.data["teacher_email"], self.other.email)

    def test_the_documented_request_shape_has_teacher_email(self):
        parts = generated_schema()["components"]["schemas"]
        field = parts["CreditRequest"]["properties"]["teacher_email"]
        self.assertTrue(field.get("readOnly"), field)
