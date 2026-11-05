"""H-295 B2: a {"status", "message"} refusal reads as its message.

Found by the H-294 audit: five billing 404s answer
{"status": "inactive", "message": "..."} and the renderer flattened that to
"1. Status: inactive\\n 2. Message: No active subscription found to cancel".
The body is kept as it is (a client may read `status`); only the top-level
`message` changes, to the message alone.
"""

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from assignments.tests_cache_matrix_g3 import make_active_user
from users.models import UserTypes
from users.renderers import flatten_errors


class FlattenStatusMessageTests(SimpleTestCase):
    def test_status_and_message_reads_as_the_message(self):
        self.assertEqual(
            flatten_errors({"status": "inactive", "message": "No subscription found"}),
            "No subscription found",
        )

    def test_a_serializer_error_on_a_field_named_status_is_unchanged(self):
        # Field errors are lists; the special case is only the string pair.
        out = flatten_errors({"status": ["Not a valid choice."], "name": ["Required."]})
        self.assertIn("Status: Not a valid choice.", out)
        self.assertIn("Name: Required.", out)

    def test_status_message_plus_another_key_is_unchanged(self):
        out = flatten_errors({"status": "inactive", "message": "M", "extra": "E"})
        self.assertIn("Extra: E", out)


class StatusRefusalApiTests(TestCase):
    client: APIClient

    def setUp(self):
        self.user = make_active_user("h295-nosub@grade.test", UserTypes.TEACHER)
        (
            self.user.subscriptions.all().delete()
            if hasattr(self.user, "subscriptions")
            else None
        )
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(user=self.user)

    def _assert(self, response, text):
        self.assertEqual(response.status_code, 404, response.content)
        body = response.json()
        self.assertEqual(body["message"], text)
        # The body a client may read is unchanged.
        self.assertEqual(
            body["error"]["field_errors"], {"status": "inactive", "message": text}
        )

    def test_cancel_with_no_subscription(self):
        self._assert(
            self.client.post(reverse("subscription-cancel"), {}, format="json"),
            "You don't have an active subscription to cancel.",
        )

    def test_status_with_no_subscription(self):
        self._assert(
            self.client.get(reverse("subscription-status")),
            "You don't have an active subscription.",
        )
