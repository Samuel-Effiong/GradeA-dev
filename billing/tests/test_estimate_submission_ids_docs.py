"""POST billing/estimate docs (beta, owner 2026-11-28): submission_ids is
documented as what it takes, a list of up to 500 ids or "all_ungraded",
instead of drf-spectacular's fallback "string", with one request example of
each. Docs only: validation and replies are covered unchanged by the other
estimate test modules.
"""

from django.test import SimpleTestCase

from billing.tests.test_credit_request_email_hide_approve import generated_schema

PATH = "/api/v1/billing/estimate"
#: W-F and W-G, drafts for the owner (2026-11-28).
HELP_TEXT = (
    'Either a list of up to 500 submission ids, or "all_ungraded" for every '
    "paper not yet graded."
)
EXAMPLE_NAMES = {"Chosen papers", "All ungraded papers"}


class EstimateSubmissionIdsDocsTests(SimpleTestCase):
    schema: dict
    body: dict

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = generated_schema()
        cls.body = cls.schema["paths"][PATH]["post"]["requestBody"]["content"][
            "application/json"
        ]

    def field(self):
        name = self.body["schema"]["$ref"].rsplit("/", 1)[1]
        return self.schema["components"]["schemas"][name]["properties"][
            "submission_ids"
        ]

    def test_submission_ids_is_a_list_of_ids_or_all_ungraded(self):
        field = self.field()
        self.assertNotEqual(field.get("type"), "string")
        self.assertEqual(
            field["oneOf"],
            [
                {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                    "maxItems": 500,
                },
                {"type": "string", "enum": ["all_ungraded"]},
            ],
        )

    def test_submission_ids_carries_the_help_text(self):
        self.assertEqual(self.field()["description"], HELP_TEXT)

    def test_both_request_examples_are_documented(self):
        examples = self.body.get("examples", {}).values()
        by_name = {example["summary"]: example["value"] for example in examples}
        self.assertEqual(set(by_name), EXAMPLE_NAMES)
        chosen = by_name["Chosen papers"]["submission_ids"]
        self.assertIsInstance(chosen, list)
        self.assertEqual(len(chosen), 2)
        self.assertEqual(
            by_name["All ungraded papers"]["submission_ids"], "all_ungraded"
        )
        for value in by_name.values():
            self.assertEqual(set(value), {"assignment_id", "submission_ids"})

    def test_the_reply_examples_are_not_request_examples(self):
        # The examples are request-only: the 200 reply keeps its own shape.
        reply = self.schema["paths"][PATH]["post"]["responses"]["200"]["content"][
            "application/json"
        ]
        self.assertNotIn("examples", reply)
