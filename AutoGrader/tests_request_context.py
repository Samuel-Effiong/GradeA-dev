import logging

from django.test import SimpleTestCase

from AutoGrader.request_context import (
    RequestIDLogFilter,
    generate_request_id,
    get_request_id,
    is_valid_request_id,
    reset_request_id,
    set_request_id,
)


class GenerateRequestIdTests(SimpleTestCase):
    def test_generates_32_char_lowercase_hex(self):
        value = generate_request_id()
        self.assertEqual(len(value), 32)
        self.assertRegex(value, r"^[0-9a-f]{32}$")

    def test_generates_unique_values(self):
        values = {generate_request_id() for _ in range(1000)}
        self.assertEqual(len(values), 1000)

    def test_generated_ids_are_always_valid(self):
        for _ in range(50):
            self.assertTrue(is_valid_request_id(generate_request_id()))


class IsValidRequestIdTests(SimpleTestCase):
    def test_none_is_invalid(self):
        self.assertFalse(is_valid_request_id(None))

    def test_empty_string_is_invalid(self):
        self.assertFalse(is_valid_request_id(""))

    def test_plain_uuid4_hex_is_valid(self):
        self.assertTrue(is_valid_request_id("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"))

    def test_dashed_uuid_is_valid(self):
        self.assertTrue(is_valid_request_id("550e8400-e29b-41d4-a716-446655440000"))

    def test_dotted_and_underscored_tokens_are_valid(self):
        self.assertTrue(is_valid_request_id("abc.123_XYZ-789"))

    def test_128_chars_is_valid_boundary(self):
        self.assertTrue(is_valid_request_id("a" * 128))

    def test_129_chars_is_invalid(self):
        self.assertFalse(is_valid_request_id("a" * 129))

    def test_whitespace_is_invalid(self):
        self.assertFalse(is_valid_request_id("abc 123"))

    def test_newline_injection_is_invalid(self):
        # Rejected specifically because this value is echoed verbatim into
        # log lines and response headers - a newline could inject a fake
        # log record or split into a second HTTP header.
        self.assertFalse(is_valid_request_id("abc\n123"))
        self.assertFalse(is_valid_request_id("abc\r\nSet-Cookie: evil=1"))

    def test_comma_and_pipe_are_invalid(self):
        self.assertFalse(is_valid_request_id("abc,123"))
        self.assertFalse(is_valid_request_id("abc|123"))

    def test_unicode_lookalikes_are_invalid(self):
        self.assertFalse(is_valid_request_id("аbc123"))  # Cyrillic 'а'

    def test_html_special_chars_are_invalid(self):
        self.assertFalse(is_valid_request_id("<script>123"))


class GetSetResetRequestIdTests(SimpleTestCase):
    def test_defaults_to_none(self):
        self.assertIsNone(get_request_id())

    def test_set_then_get_roundtrips(self):
        token = set_request_id("abc-123")
        try:
            self.assertEqual(get_request_id(), "abc-123")
        finally:
            reset_request_id(token)

    def test_reset_restores_prior_value(self):
        outer_token = set_request_id("outer")
        try:
            inner_token = set_request_id("inner")
            self.assertEqual(get_request_id(), "inner")
            reset_request_id(inner_token)
            self.assertEqual(get_request_id(), "outer")
        finally:
            reset_request_id(outer_token)
        self.assertIsNone(get_request_id())


class RequestIDLogFilterTests(SimpleTestCase):
    def _make_record(self):
        return logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )

    def test_injects_dash_when_no_request_id_set(self):
        record = self._make_record()
        result = RequestIDLogFilter().filter(record)
        self.assertTrue(result)
        self.assertEqual(record.request_id, "-")

    def test_injects_current_request_id(self):
        token = set_request_id("req-42")
        try:
            record = self._make_record()
            RequestIDLogFilter().filter(record)
            self.assertEqual(record.request_id, "req-42")
        finally:
            reset_request_id(token)

    def test_always_returns_true_never_filters_out_a_record(self):
        record = self._make_record()
        self.assertTrue(RequestIDLogFilter().filter(record))
