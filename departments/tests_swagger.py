"""The department routes are documented: the schema generates with no
drf-spectacular warning or error for them, and every operation has a
summary, a description, a success schema, and a request body where it takes
one (docs only; no behavior is read from here)."""

from drf_spectacular import extensions
from drf_spectacular.drainage import GENERATOR_STATS
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APITestCase

DEPARTMENT_PATHS = ("/departments", "/school-admin/departments")

#: Operations that take a request body.
WITH_BODY = {
    ("post", "/api/v1/school-admin/departments"),
    ("patch", "/api/v1/school-admin/departments/{department_id}"),
    ("post", "/api/v1/school-admin/departments/{department_id}/members"),
    (
        "patch",
        "/api/v1/school-admin/departments/{department_id}/members/{membership_id}",
    ),
}
SUCCESS = {"get": "200", "post": None, "patch": "200", "delete": None}


class DepartmentSwaggerTests(APITestCase):
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
            message
            for cache in (GENERATOR_STATS._warn_cache, GENERATOR_STATS._error_cache)
            for message in cache
            if "departments" in str(message) or "Department" in str(message)
        ]

    def operations(self):
        for path, item in self.schema["paths"].items():
            if not any(path.startswith("/api/v1" + p) for p in DEPARTMENT_PATHS):
                continue
            for method, op in item.items():
                if method in {"get", "post", "put", "patch", "delete"}:
                    yield method, path, op

    def test_there_are_ten_department_operations(self):
        self.assertEqual(len(list(self.operations())), 10)

    def test_generating_the_schema_reports_nothing_for_department_views(self):
        self.assertEqual(self.problems, [])

    def test_every_operation_is_documented(self):
        missing = []
        for method, path, op in self.operations():
            where = f"{method.upper()} {path}"
            if not op.get("summary"):
                missing.append(f"{where}: no summary")
            if not op.get("description"):
                missing.append(f"{where}: no description")
            responses = op.get("responses", {})
            ok = [c for c in responses if c.startswith("2")]
            if not ok:
                missing.append(f"{where}: no success response")
            elif method != "delete" or ok != ["204"]:
                content = responses[ok[0]].get("content", {})
                if not content.get("application/json", {}).get("schema"):
                    missing.append(f"{where}: no success schema")
            if (method, path) in WITH_BODY and not op.get("requestBody"):
                missing.append(f"{where}: no request body")
            if "404" not in responses:
                missing.append(f"{where}: no 404 documented")
        self.assertEqual(missing, [])

    def test_operation_ids_are_unique(self):
        ids = [op.get("operationId") for _m, _p, op in self.operations()]
        self.assertEqual(len(ids), len(set(ids)))
        everywhere = [
            op.get("operationId")
            for item in self.schema["paths"].values()
            for method, op in item.items()
            if method in {"get", "post", "put", "patch", "delete"}
        ]
        self.assertEqual(len(everywhere), len(set(everywhere)))

    def test_the_delete_choice_and_the_error_codes_are_documented(self):
        op = self.schema["paths"]["/api/v1/school-admin/departments/{department_id}"][
            "delete"
        ]
        names = {p["name"] for p in op["parameters"] if p["in"] == "query"}
        self.assertEqual(names, {"content", "target"})
        self.assertIn("409", op["responses"])
        self.assertIn("DEPARTMENT_CONTENT_CHOICE_REQUIRED", str(op["responses"]["409"]))
