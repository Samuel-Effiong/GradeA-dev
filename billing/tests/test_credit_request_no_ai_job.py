"""S14 (owner go 2026-11-17, beta): credit requests no longer carry an AI job.
A client that still sends `ai_job` is ignored (201), and replies do not show
it."""

import uuid
from unittest.mock import patch

from billing.models import CreditRequest
from billing.tests.test_credit_requests import BASE, ON, SEND, Fixture


class NoAiJobTests(Fixture):
    def setUp(self):
        self.make_school()
        from rest_framework.test import APIClient

        self.api = APIClient()
        self.api.force_authenticate(self.teacher)

    def test_the_model_has_no_ai_job_field(self):
        names = {f.name for f in CreditRequest._meta.get_fields()}
        self.assertNotIn("ai_job", names)

    @ON
    def test_a_client_that_still_sends_ai_job_is_ignored(self):
        with patch(SEND):
            response = self.api.post(
                BASE,
                {"requested_blocks": 1, "ai_job": str(uuid.uuid4())},
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("ai_job", response.data)
        listed = self.api.get(BASE).data
        self.assertEqual(len(listed), 1)
        self.assertNotIn("ai_job", listed[0])

    @ON
    def test_the_swagger_shapes_do_not_show_ai_job(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        parts = schema["components"]["schemas"]
        for name in ("CreateCreditRequest", "CreditRequest"):
            self.assertNotIn("ai_job", parts[name].get("properties", {}), name)
