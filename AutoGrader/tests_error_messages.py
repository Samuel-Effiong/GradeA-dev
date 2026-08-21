from django.test import SimpleTestCase

from AutoGrader.error_messages import (
    DEFAULT_ERROR_MESSAGE,
    classify_infra_error,
    describe_background_task_error,
    describe_stripe_error,
    describe_user_error,
)
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import InsufficientCreditsError
from students.exceptions import CannotAssociateStudentError


class DescribeUserErrorTest(SimpleTestCase):
    def test_known_user_facing_exceptions_pass_through_verbatim(self):
        cases = [
            CannotAssociateStudentError("Student not among the enrolled students"),
            AIFeatureNotAvailableError("Upgrade your plan to unlock this feature"),
            InsufficientCreditsError("Refill your wallet to continue"),
        ]
        for exc in cases:
            with self.subTest(exc_type=type(exc).__name__):
                self.assertEqual(describe_user_error(exc), str(exc))

    def test_unknown_exception_never_leaks_raw_technical_detail(self):
        exc = KeyError("grading_summary")
        message = describe_user_error(exc, fallback_message="We couldn't grade this.")
        self.assertEqual(message, "We couldn't grade this.")
        self.assertNotIn("KeyError", message)
        self.assertNotIn("grading_summary", message)

    def test_unknown_exception_falls_back_to_default_when_no_fallback_given(self):
        self.assertEqual(describe_user_error(ValueError("boom")), DEFAULT_ERROR_MESSAGE)

    def test_plain_string_is_treated_as_unknown(self):
        self.assertEqual(
            describe_user_error("Some internal code path failed"),
            DEFAULT_ERROR_MESSAGE,
        )

    def test_none_error_uses_fallback(self):
        self.assertEqual(
            describe_user_error(None, fallback_message="Try again."), "Try again."
        )

    def test_empty_user_facing_exception_message_falls_back(self):
        message = describe_user_error(
            CannotAssociateStudentError(""), fallback_message="fallback text"
        )
        self.assertEqual(message, "fallback text")


class ClassifyInfraErrorTest(SimpleTestCase):
    def test_timeout_is_recognized(self):
        message = classify_infra_error(TimeoutError("timed out"))
        self.assertIn("timed out", message)

    def test_connection_error_is_recognized(self):
        message = classify_infra_error(ConnectionError("connection reset by peer"))
        self.assertIn("lost connection", message)
        self.assertNotIn("connection reset by peer", message)

    def test_unidentified_image_is_recognized(self):
        from PIL import UnidentifiedImageError

        message = classify_infra_error(UnidentifiedImageError("cannot identify image"))
        self.assertIn("couldn't read this file", message)

    def test_image_compression_error_is_recognized(self):
        from ai_processor.tools import ImageCompressionError

        message = classify_infra_error(
            ImageCompressionError("still too big after 5 passes")
        )
        self.assertIn("too large", message)

    def test_storage_oserror_is_recognized(self):
        message = classify_infra_error(OSError("disk full"))
        self.assertIn("storage issue", message)
        self.assertNotIn("disk full", message)

    def test_walks_cause_chain_through_generic_wrapper_exception(self):
        # Mirrors the ai_processor pattern of catching a typed exception and
        # re-raising `raise Exception(str(e)) from e` — the wrapper itself
        # is a bare Exception, but the original type survives on __cause__.
        try:
            try:
                raise TimeoutError("upstream timed out")
            except TimeoutError as inner:
                raise Exception(f"Error during AI model: {inner}") from inner
        except Exception as wrapped:
            message = classify_infra_error(wrapped)

        self.assertIn("timed out", message)

    def test_unrecognized_exception_returns_none(self):
        self.assertIsNone(classify_infra_error(KeyError("grading_summary")))

    def test_non_exception_returns_none(self):
        self.assertIsNone(classify_infra_error("not an exception"))


class DescribeBackgroundTaskErrorTest(SimpleTestCase):
    def test_known_user_facing_exception_still_passes_through_verbatim(self):
        exc = InsufficientCreditsError("Refill your wallet to continue")
        self.assertEqual(describe_background_task_error(exc), str(exc))

    def test_infra_failure_gets_distinct_message_over_generic_fallback(self):
        message = describe_background_task_error(
            TimeoutError("timed out"),
            fallback_message="We couldn't grade this submission. Please try again.",
        )
        self.assertIn("timed out", message)
        self.assertNotEqual(
            message, "We couldn't grade this submission. Please try again."
        )

    def test_unclassified_exception_falls_back_like_describe_user_error(self):
        message = describe_background_task_error(
            KeyError("grading_summary"), fallback_message="fallback text"
        )
        self.assertEqual(message, "fallback text")


class DescribeStripeErrorTest(SimpleTestCase):
    def test_uses_stripe_user_message_when_present(self):
        exc = Exception("raw internal stripe detail")
        exc.user_message = "Your card was declined."  # type: ignore[attr-defined]

        self.assertEqual(describe_stripe_error(exc), "Your card was declined.")

    def test_falls_back_when_no_user_message(self):
        exc = ConnectionError("could not reach api.stripe.com")

        message = describe_stripe_error(exc, fallback_message="Please try again.")

        self.assertEqual(message, "Please try again.")
        self.assertNotIn("api.stripe.com", message)

    def test_falls_back_to_generic_classifier_for_known_exceptions(self):
        exc = InsufficientCreditsError("Refill your wallet to continue")

        self.assertEqual(describe_stripe_error(exc), "Refill your wallet to continue")
