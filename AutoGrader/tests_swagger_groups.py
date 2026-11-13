"""
Swagger groups (S12, owner go 13 Nov, beta, docs only): twenty endpoints had
no tag and showed under the catch-all "api" group. Each now has the approved
group name.
"""

from drf_spectacular import extensions
from rest_framework.test import APITestCase

APPROVED = {
    ("post", "/api/v1/billing/estimate"): "Billing",
    ("get", "/api/v1/billing/credit-requests"): "Credit Requests",
    ("post", "/api/v1/billing/credit-requests"): "Credit Requests",
    ("get", "/api/v1/billing/credit-requests/counts"): "Credit Requests",
    ("post", "/api/v1/billing/credit-requests/{id}/approve"): "Credit Requests",
    ("post", "/api/v1/billing/credit-requests/{id}/decline"): "Credit Requests",
    ("post", "/api/v1/billing/credit-requests/{id}/withdraw"): "Credit Requests",
    ("get", "/api/v1/tags"): "Tags",
    ("post", "/api/v1/tags"): "Tags",
    ("patch", "/api/v1/tags/{id}"): "Tags",
    ("delete", "/api/v1/tags/{id}"): "Tags",
    ("get", "/api/v1/tags/{id}/impact"): "Tags",
    ("put", "/api/v1/assignments/{id}/tags"): "Tags",
    ("get", "/api/v1/students/{id}/data-export"): "Student Data",
    ("post", "/api/v1/students/{id}/erase"): "Student Data",
    ("get", "/api/v1/health"): "Health",
    ("get", "/api/v1/health/beat"): "Health",
    ("get", "/api/v1/health/client"): "Health",
    ("post", "/api/v1/qa/time-travel"): "QA",
    ("post", "/api/v1/waitlist"): "Waitlist",
}


class SwaggerGroupsTests(APITestCase):
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

    def test_no_operation_is_untagged_or_in_the_api_group(self):
        bad = []
        for path, item in self.schema["paths"].items():
            for method, op in item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                tags = op.get("tags") or []
                if not tags or "api" in tags:
                    bad.append(f"{method.upper()} {path} {tags}")
        self.assertEqual(bad, [])

    def test_each_of_the_twenty_has_its_approved_tag(self):
        wrong = []
        for (method, path), tag in APPROVED.items():
            op = self.schema["paths"].get(path, {}).get(method)
            tags = op.get("tags") if op else None
            if tags != [tag]:
                wrong.append(f"{method.upper()} {path}: {tags} != {[tag]}")
        self.assertEqual(wrong, [])
