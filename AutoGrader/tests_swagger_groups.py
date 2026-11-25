"""
Swagger groups (S12, owner go 13 Nov, beta, docs only): twenty endpoints had
no tag and showed under the catch-all "api" group. Each now has the approved
group name.
"""

from drf_spectacular import extensions
from rest_framework.test import APITestCase

APPROVED = {
    ("post", "/api/v1/billing/estimate"): "Credit Requests",
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

# Epic E2 (owner, 19 Nov, docs only): its 15 endpoints get their group too.
E2_APPROVED = {
    ("post", "/api/v1/assignments/{id}/copy"): "Assignments",
    ("post", "/api/v1/assignments/bulk-copy"): "Assignments",
    ("post", "/api/v1/assignments/{id}/excused"): "Assignments",
    ("delete", "/api/v1/assignments/{id}/excused"): "Assignments",
    ("get", "/api/v1/category-templates"): "Grade Categories",
    ("post", "/api/v1/category-templates"): "Grade Categories",
    ("get", "/api/v1/category-templates/{id}"): "Grade Categories",
    ("patch", "/api/v1/category-templates/{id}"): "Grade Categories",
    ("delete", "/api/v1/category-templates/{id}"): "Grade Categories",
    ("get", "/api/v1/course/{id}/categories"): "Grade Categories",
    ("put", "/api/v1/course/{id}/categories"): "Grade Categories",
    ("post", "/api/v1/course/{id}/categories/preview"): "Grade Categories",
    ("get", "/api/v1/course/{id}/grade-breakdown"): "Grade Categories",
    ("get", "/api/v1/course/{id}/grading-policy"): "Grade Categories",
    ("patch", "/api/v1/course/{id}/grading-policy"): "Grade Categories",
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

    # S12 addendum: request and reply shapes.
    BODIES = {
        ("post", "/api/v1/billing/estimate"): (
            "EstimateRequest",
            ("assignment_id", "submission_ids"),
        ),
        ("post", "/api/v1/billing/credit-requests"): (
            "CreateCreditRequest",
            ("requested_blocks", "message"),
        ),
        ("post", "/api/v1/billing/credit-requests/{id}/approve"): (
            "ApproveCreditRequest",
            ("blocks", "payment_method"),
        ),
        ("post", "/api/v1/billing/credit-requests/{id}/decline"): (
            "DeclineCreditRequest",
            ("reason",),
        ),
        ("post", "/api/v1/tags"): (None, ("tag_type", "name")),
        ("patch", "/api/v1/tags/{id}"): (None, ("name",)),
        ("put", "/api/v1/assignments/{id}/tags"): ("AssignmentTags", ("tag_ids",)),
    }
    REPLIES = {
        ("post", "/api/v1/billing/estimate"): (
            "p50",
            "p90",
            "verdict",
            "spendable",
            "projected_remaining_p50",
            "projected_remaining_p90",
            "n",
        ),
        ("get", "/api/v1/billing/credit-requests/counts"): (
            "pending",
            "answered",
            "granted",
            "declined",
        ),
        ("get", "/api/v1/tags/{id}/impact"): ("affected_count",),
        ("put", "/api/v1/assignments/{id}/tags"): ("tags",),
        ("post", "/api/v1/billing/credit-requests/{id}/approve"): (
            "request",
            "purchase",
        ),
        ("post", "/api/v1/billing/credit-requests"): ("id", "status"),
        ("post", "/api/v1/billing/credit-requests/{id}/decline"): ("id", "status"),
        ("post", "/api/v1/billing/credit-requests/{id}/withdraw"): ("id", "status"),
        ("post", "/api/v1/tags"): ("id", "tag_type", "name"),
        ("patch", "/api/v1/tags/{id}"): ("id", "tag_type", "name"),
    }

    def resolve(self, node):
        ref = node.get("$ref")
        if ref:
            return self.schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
        return node

    def properties(self, node):
        node = self.resolve(node)
        if node.get("type") == "array":
            node = self.resolve(node["items"])
        return set(node.get("properties", {}))

    def success(self, op):
        codes = [c for c in op["responses"] if c.startswith("2")]
        content = op["responses"][codes[0]].get("content", {}) if codes else {}
        return content.get("application/json", {}).get("schema")

    def test_request_bodies_are_documented(self):
        problems = []
        for (method, path), (_, fields) in self.BODIES.items():
            op = self.schema["paths"][path][method]
            body = op.get("requestBody", {}).get("content", {})
            node = (body.get("application/json") or {}).get("schema")
            if node is None:
                problems.append(f"{method.upper()} {path}: no request body")
            elif not set(fields) <= self.properties(node):
                problems.append(f"{method.upper()} {path}: {self.properties(node)}")
        self.assertEqual(problems, [])

    def test_withdraw_takes_no_body(self):
        op = self.schema["paths"]["/api/v1/billing/credit-requests/{id}/withdraw"][
            "post"
        ]
        self.assertNotIn("requestBody", op)

    def test_success_replies_are_documented(self):
        problems = []
        for (method, path), fields in self.REPLIES.items():
            node = self.success(self.schema["paths"][path][method])
            if node is None:
                problems.append(f"{method.upper()} {path}: no 2xx schema")
            elif not set(fields) <= self.properties(node):
                problems.append(f"{method.upper()} {path}: {self.properties(node)}")
        self.assertEqual(problems, [])

    def test_credit_request_list_documents_status_and_reply(self):
        op = self.schema["paths"]["/api/v1/billing/credit-requests"]["get"]
        self.assertIn("status", {p["name"] for p in op.get("parameters", [])})
        self.assertIn("id", self.properties(self.success(op)))

    def test_each_of_the_e2_fifteen_has_its_approved_tag(self):
        wrong = []
        for (method, path), tag in E2_APPROVED.items():
            op = self.schema["paths"].get(path, {}).get(method)
            tags = op.get("tags") if op else None
            if tags != [tag]:
                wrong.append(f"{method.upper()} {path}: {tags} != {[tag]}")
        self.assertEqual(wrong, [])


COURSE_OPERATIONS = (
    ("get", "/api/v1/course"),
    ("post", "/api/v1/course"),
    ("get", "/api/v1/course/{id}"),
    ("patch", "/api/v1/course/{id}"),
    ("delete", "/api/v1/course/{id}"),
    ("post", "/api/v1/course/{id}/bulk-add-students"),
    ("post", "/api/v1/course/{id}/direct-add-student"),
    ("get", "/api/v1/course/{id}/student-summary"),
    ("delete", "/api/v1/course/{id}/student/{student_id}"),
    ("post", "/api/v1/course/{id}/students"),
    ("post", "/api/v1/course/{id}/topics"),
    ("get", "/api/v1/course/my-courses"),
    ("post", "/api/v1/course/renew-student-token"),
)


class CourseGroupTests(SwaggerGroupsTests):
    """Brief 3 (owner, 19 Nov): "02 Course" is now just "Course"."""

    def test_the_thirteen_course_operations_are_in_the_course_group(self):
        wrong = []
        for method, path in COURSE_OPERATIONS:
            op = self.schema["paths"].get(path, {}).get(method)
            tags = op.get("tags") if op else None
            if tags != ["Course"]:
                wrong.append(f"{method.upper()} {path}: {tags}")
        self.assertEqual(wrong, [])

    def test_no_operation_carries_the_old_group_name(self):
        old = [
            f"{method.upper()} {path}"
            for path, item in self.schema["paths"].items()
            for method, op in item.items()
            if method in {"get", "post", "put", "patch", "delete"}
            and "02 Course" in (op.get("tags") or [])
        ]
        self.assertEqual(old, [])
