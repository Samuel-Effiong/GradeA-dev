from django.test import SimpleTestCase

from AutoGrader.error_messages import (
    DEFAULT_ERROR_MESSAGE,
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


class DescribeStripeErrorTest(SimpleTestCase):
    def test_uses_stripe_user_message_when_present(self):
        exc = Exception("raw internal stripe detail")
        exc.user_message = "Your card was declined."

        self.assertEqual(describe_stripe_error(exc), "Your card was declined.")

    def test_falls_back_when_no_user_message(self):
        exc = ConnectionError("could not reach api.stripe.com")

        message = describe_stripe_error(exc, fallback_message="Please try again.")

        self.assertEqual(message, "Please try again.")
        self.assertNotIn("api.stripe.com", message)

    def test_falls_back_to_generic_classifier_for_known_exceptions(self):
        exc = InsufficientCreditsError("Refill your wallet to continue")

        self.assertEqual(describe_stripe_error(exc), "Refill your wallet to continue")
