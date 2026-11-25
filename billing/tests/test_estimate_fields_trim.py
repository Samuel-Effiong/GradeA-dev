"""Estimate fields trim (owner, 2026-11-25): source, sample_size and
shape_basis are not sent to the frontend, in any reply that carries an
estimate. They stay inside the server: the job still stores
estimate_source, and estimate() still returns all three.

    python manage.py test billing.tests.test_estimate_fields_trim
"""

from django.test import TestCase
from drf_spectacular import extensions
from rest_framework.test import APIClient

from billing import estimates
from billing.models import AIJobStatus, CreditWallet
from billing.testing.holds import make_job
from billing.tests import test_b1b7_job_api as job_api
from billing.tests.test_estimate_endpoint import EndpointFixture
from billing.tests.testing_estimates import make_assignment, papers

HIDDEN = {"source", "sample_size", "shape_basis"}
SHOWN = {
    "p50",
    "p90",
    "verdict",
    "spendable",
    "projected_remaining_p50",
    "projected_remaining_p90",
    "n",
}


class EstimateReplyTests(EndpointFixture):
    def test_the_estimate_reply_has_no_source_sample_size_or_shape_basis(self):
        response = self.post(
            {"assignment_id": str(self.assignment.pk), "submission_ids": "all_ungraded"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()["data"]
        self.assertEqual(set(body), SHOWN)
        self.assertFalse(HIDDEN & set(body))


class JobReplyTests(TestCase):
    def setUp(self):
        self.owner = job_api.user("trim-owner")
        wallet, _ = CreditWallet.objects.get_or_create(user=self.owner)
        self.job = make_job(
            wallet,
            job_type="BATCH_GRADING",
            total_items=3,
            status=AIJobStatus.QUEUED_PENDING_CREDITS,
            estimate_p50=300,
            estimate_p90=450,
            estimate_source="assignment",
        )

    def test_the_job_estimate_is_p50_and_p90_only(self):
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=self.owner)
        response = client.get(job_api.job_url(self.job.pk))
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["data"]["estimate"], {"p50": 300, "p90": 450})

    def test_the_job_still_stores_its_estimate_source(self):
        self.job.refresh_from_db()
        self.assertEqual(self.job.estimate_source, "assignment")


class InternalEstimateTests(TestCase):
    def test_estimate_still_returns_the_three_fields_inside_the_server(self):
        teacher = job_api.user("trim-internal")
        assignment = make_assignment(teacher, "Trim internal")
        papers(teacher, assignment, [100] * 10)
        result = estimates.estimate(assignment, 3, teacher=teacher)
        self.assertLessEqual(HIDDEN, set(result))


class SwaggerTests(TestCase):
    schema: dict

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
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
            cls.schema = SchemaGenerator().get_schema(request=None, public=True)
        finally:
            for ext, target in saved.items():
                ext.target_class = target

    def resolve(self, node):
        while "$ref" in node:
            node = self.schema["components"]["schemas"][node["$ref"].split("/")[-1]]
        if "allOf" in node and len(node["allOf"]) == 1:
            return self.resolve(node["allOf"][0])
        return node

    def component(self, name):
        return set(self.schema["components"]["schemas"][name].get("properties", {}))

    def test_estimate_reply_documents_no_hidden_field(self):
        self.assertEqual(self.component("EstimateReply"), SHOWN)

    def test_upload_quote_estimate_documents_no_hidden_field(self):
        self.assertEqual(self.component("UploadQuoteEstimate"), SHOWN)

    def test_job_estimate_documents_p50_and_p90_only(self):
        job = self.schema["components"]["schemas"]["AIJobDetail"]
        estimate = self.resolve(job["properties"]["estimate"])
        self.assertEqual(set(estimate.get("properties", {})), {"p50", "p90"})
