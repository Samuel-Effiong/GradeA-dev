from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from AutoGrader.middleware import RequestIDMiddleware
from AutoGrader.request_context import REQUEST_ID_HEADER, get_request_id


class RequestIDMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        # Sanity check the contextvar isn't already dirty from a previous
        # test in this process (would indicate a reset() bug elsewhere).
        self.assertIsNone(get_request_id())

    def _middleware(self, get_response):
        return RequestIDMiddleware(get_response)

    def test_generates_id_when_no_inbound_header(self):
        seen = {}

        def get_response(request):
            seen["request_id"] = request.request_id
            seen["contextvar_during_request"] = get_request_id()
            return HttpResponse("ok")

        request = self.factory.get("/")
        response = self._middleware(get_response)(request)

        self.assertIsNotNone(seen["request_id"])
        self.assertEqual(len(seen["request_id"]), 32)
        self.assertEqual(seen["contextvar_during_request"], seen["request_id"])
        self.assertEqual(response[REQUEST_ID_HEADER], seen["request_id"])

    def test_reuses_valid_inbound_header(self):
        def get_response(request):
            return HttpResponse("ok")

        request = self.factory.get("/", HTTP_X_REQUEST_ID="client-supplied-id-123")
        response = self._middleware(get_response)(request)

        self.assertEqual(response[REQUEST_ID_HEADER], "client-supplied-id-123")

    def test_generates_new_id_when_inbound_header_is_malformed(self):
        def get_response(request):
            return HttpResponse("ok")

        request = self.factory.get("/", HTTP_X_REQUEST_ID="has a space\nand a newline")
        response = self._middleware(get_response)(request)

        returned = response[REQUEST_ID_HEADER]
        self.assertNotIn("\n", returned)
        self.assertNotEqual(returned, "has a space\nand a newline")
        self.assertEqual(len(returned), 32)

    def test_generates_new_id_when_inbound_header_is_empty(self):
        def get_response(request):
            return HttpResponse("ok")

        request = self.factory.get("/", HTTP_X_REQUEST_ID="")
        response = self._middleware(get_response)(request)

        self.assertEqual(len(response[REQUEST_ID_HEADER]), 32)

    def test_contextvar_is_reset_after_response(self):
        def get_response(request):
            return HttpResponse("ok")

        request = self.factory.get("/")
        self._middleware(get_response)(request)

        self.assertIsNone(get_request_id())

    def test_contextvar_is_reset_even_when_view_raises(self):
        def get_response(request):
            raise ValueError("boom")

        request = self.factory.get("/")
        with self.assertRaises(ValueError):
            self._middleware(get_response)(request)

        self.assertIsNone(get_request_id())

    def test_two_sequential_requests_get_different_ids(self):
        seen = []

        def get_response(request):
            seen.append(request.request_id)
            return HttpResponse("ok")

        self._middleware(get_response)(self.factory.get("/"))
        self._middleware(get_response)(self.factory.get("/"))

        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1])

    def test_sets_sentry_tag_when_sentry_sdk_available(self):
        def get_response(request):
            return HttpResponse("ok")

        with patch("sentry_sdk.set_tag") as mock_set_tag:
            request = self.factory.get("/")
            response = self._middleware(get_response)(request)

        mock_set_tag.assert_called_once_with("request_id", response[REQUEST_ID_HEADER])

    def test_does_not_raise_when_sentry_sdk_not_installed(self):
        def get_response(request):
            return HttpResponse("ok")

        with patch.dict("sys.modules", {"sentry_sdk": None}):
            request = self.factory.get("/")
            response = self._middleware(get_response)(request)

        self.assertEqual(len(response[REQUEST_ID_HEADER]), 32)
