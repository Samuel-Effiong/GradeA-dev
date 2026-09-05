"""
users.exceptions.custom_exception_handler - the DRF EXCEPTION_HANDLER.

users/tests_renderers.py covers the renderer half of the error envelope by
setting `_drf_handled` / `_raw_exc` by hand. Nothing covered the handler
that actually sets them, so the contract between the two halves was
untested from the producing side - and its unhandled-500 branch (the one
that decides a raw exception never reaches a user verbatim) was the
uncovered part of the module.
"""

from unittest.mock import Mock

from django.test import TestCase
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError

from users.exceptions import custom_exception_handler


class CustomExceptionHandlerTests(TestCase):
    def context(self, path="/api/v1/thing", method="GET", user_pk=None):
        request = Mock()
        request.path = path
        request.method = method
        request.user = Mock(pk=user_pk)
        view = Mock()
        return {"view": view, "request": request}

    def handle(self, exc, **kwargs):
        with self.assertLogs("users.exceptions", level="ERROR"):
            return custom_exception_handler(exc, self.context(**kwargs))

    # --- exceptions DRF knows how to format -----------------------------------

    def test_a_drf_exception_keeps_its_status_and_is_marked_handled(self):
        response = self.handle(NotFound("No such thing."))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(getattr(response, "_drf_handled", False))

    def test_validation_errors_keep_their_field_payload(self):
        response = self.handle(ValidationError({"email": ["This field is required."]}))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["email"], ["This field is required."])
        self.assertTrue(getattr(response, "_drf_handled", False))

    # --- exceptions DRF does NOT handle ---------------------------------------

    def test_an_unhandled_exception_becomes_a_500_carrying_the_original(self):
        """
        DRF returns None for a non-API exception. The handler has to turn
        that into a real 500 response, flagged as an exception and carrying
        the original on `_raw_exc` - which is what lets the renderer decide
        what is safe to show. Returning None here instead would let Django
        render its own debug page.
        """
        exc = KeyError("grading_summary")

        response = self.handle(exc)

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertTrue(response.exception)
        self.assertIs(response._raw_exc, exc)
        self.assertFalse(hasattr(response, "_drf_handled"))

    def test_the_unhandled_response_body_carries_no_exception_text(self):
        """The renderer sanitises the message; the handler must not pre-leak it."""
        response = self.handle(KeyError("internal_detail_abc"))

        self.assertNotIn("internal_detail_abc", str(response.data))

    # --- logging --------------------------------------------------------------

    def test_every_exception_is_logged_with_request_context(self):
        """
        Without user/path/method on the record, an error in the log cannot
        be tied back to the request that caused it.
        """
        with self.assertLogs("users.exceptions", level="ERROR") as logs:
            custom_exception_handler(
                KeyError("boom"),
                self.context(path="/api/v1/grade", method="POST", user_pk=42),
            )

        record = logs.records[0]
        self.assertEqual(record.path, "/api/v1/grade")
        self.assertEqual(record.method, "POST")
        self.assertEqual(record.user, 42)
        self.assertIsNotNone(record.exc_info)

    def test_a_handled_exception_is_logged_too(self):
        with self.assertLogs("users.exceptions", level="ERROR"):
            custom_exception_handler(NotFound("nope"), self.context())
