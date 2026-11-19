"""The Epic E2 endpoints (and publish-all-grades) are documented: the schema
generates with no drf-spectacular warning or error for their views, and every
operation has a summary, a description, a success schema, and a request body
only where the view reads one (docs only)."""

from drf_spectacular import extensions
from drf_spectacular.drainage import GENERATOR_STATS
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APITestCase

BASE = "/api/v1"
#: (method, path, takes a request body)
OPERATIONS = [
    ("post", "/assignments/{id}/copy", True),
    ("post", "/assignments/bulk-copy", True),
    ("post", "/assignments/{id}/excused", True),
    ("delete", "/assignments/{id}/excused", False),
    ("get", "/category-templates", False),
    ("post", "/category-templates", True),
    ("get", "/category-templates/{id}", False),
    ("patch", "/category-templates/{id}", True),
    ("delete", "/category-templates/{id}", False),
    ("get", "/course/{id}/categories", False),
    ("put", "/course/{id}/categories", True),
    ("post", "/course/{id}/categories/preview", True),
    ("get", "/course/{id}/grade-breakdown", False),
    ("get", "/course/{id}/grading-policy", False),
    ("patch", "/course/{id}/grading-policy", True),
    ("post", "/assignments/{id}/publish-all-grades", False),
]
VIEW_MARKERS = ("views_grade_categories", "views_copy", "publish_all_grades")


class E2SwaggerTests(APITestCase):
    schema: dict
    problems: list

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
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
        GENERATOR_STATS.reset()
        try:
            cls.schema = SchemaGenerator().get_schema(request=None, public=True)
        finally:
            for ext, target in saved.items():
                ext.target_class = target
        cls.problems = [
            str(message)
            for cache in (GENERATOR_STATS._warn_cache, GENERATOR_STATS._error_cache)
            for message in cache
            if any(marker in str(message) for marker in VIEW_MARKERS)
        ]

    def op(self, method, path):
        return self.schema["paths"].get(BASE + path, {}).get(method)

    def test_the_schema_reports_nothing_for_these_views(self):
        self.assertEqual(self.problems, [])

    def test_every_operation_is_documented(self):
        missing = []
        for method, path, takes_body in OPERATIONS:
            op = self.op(method, path)
            where = f"{method.upper()} {path}"
            if op is None:
                missing.append(f"{where}: not in the schema")
                continue
            if not op.get("summary"):
                missing.append(f"{where}: no summary")
            if not op.get("description"):
                missing.append(f"{where}: no description")
            responses = op.get("responses", {})
            ok = [c for c in responses if c.startswith("2")]
            if not ok:
                missing.append(f"{where}: no success response")
            elif ok != ["204"]:
                content = responses[ok[0]].get("content", {})
                if not content.get("application/json", {}).get("schema"):
                    missing.append(f"{where}: no success schema")
            has_body = bool(op.get("requestBody"))
            if has_body != takes_body:
                missing.append(
                    f"{where}: request body is "
                    f"{'documented' if has_body else 'missing'}, expected "
                    f"{'one' if takes_body else 'none'}"
                )
        self.assertEqual(missing, [])

    def test_operation_ids_are_unique(self):
        ids = [
            op.get("operationId")
            for item in self.schema["paths"].values()
            for method, op in item.items()
            if method in {"get", "post", "put", "patch", "delete"}
        ]
        self.assertEqual(len(ids), len(set(ids)))
