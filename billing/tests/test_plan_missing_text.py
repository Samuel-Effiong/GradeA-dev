"""QA row 128: a plan id that does not exist reads a plain sentence."""

import uuid

from django.test import TestCase

from billing.serializers import SelectIndividualPlanSerializer


class MissingPlanTextTests(TestCase):
    def test_unknown_plan_id(self):
        serializer = SelectIndividualPlanSerializer(
            data={
                "plan_id": str(uuid.uuid4()),
                "success_url": "https://example.test/ok",
                "cancel_url": "https://example.test/cancel",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            [str(e) for e in serializer.errors["plan_id"]],
            ["That plan no longer exists. Please choose another plan."],
        )
