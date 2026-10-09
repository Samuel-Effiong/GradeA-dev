import json

from django.test import SimpleTestCase

from AutoGrader.handlers import json_400, json_403, json_404, json_500


class ErrorHandlerMessageTests(SimpleTestCase):
    """Each handler must report its own message, not a hardcoded one.

    Regression test for a bug where every handler's response body said
    "message": "Not Found" regardless of the actual error - _json_error
    ignored its `message` argument and hardcoded the literal.
    """

    def test_json_400_reports_bad_request(self):
        response = json_400(request=None)
        body = json.loads(response.content)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(body["message"], "Bad Request")

    def test_json_403_reports_forbidden(self):
        response = json_403(request=None)
        body = json.loads(response.content)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(body["message"], "Forbidden")

    def test_json_404_reports_not_found(self):
        response = json_404(request=None)
        body = json.loads(response.content)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(body["message"], "Not Found")

    def test_json_500_reports_server_error(self):
        response = json_500(request=None)
        body = json.loads(response.content)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(body["message"], "Server Error")
