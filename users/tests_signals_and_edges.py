"""
The post-save signal chain, plus the last uncovered branches of the
renderer, serializer, services and utils.

`users/signals.py` is the entry point for automatic trial activation, and
its whole job is to NEVER break registration: every step is wrapped so a
billing failure logs instead of losing the signup. That property was
untested, as was the `USE_BETA_PLAN_ON_SIGNUP` branch.
"""

import logging
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.response import Response

from billing.models import (
    BetaProfile,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from users.models import Settings, UserTypes
from users.renderers import APIJSONRenderer, flatten_errors, get_response_message
from users.serializers import CustomUserSerializer
from users.services import OTPManager, send_user_activation_email
from users.utils import email_domain, is_business_email

User = get_user_model()
PASSWORD = "a-strong-password-42"  # pragma: allowlist secret


def make_teacher(email="signal.teacher@gmail.com", **overrides):
    defaults = {
        "email": email,
        "password": PASSWORD,
        "first_name": "Sig",
        "last_name": "Nal",
        "user_type": UserTypes.TEACHER,
    }
    defaults.update(overrides)
    return User.objects.create_user(**defaults)


class SignalBootstrapTests(TestCase):
    """Every new user gets Settings and a CreditWallet."""

    def test_settings_and_wallet_are_created_for_a_new_user(self):
        user = make_teacher()

        self.assertTrue(Settings.objects.filter(user=user).exists())
        self.assertTrue(CreditWallet.objects.filter(user=user).exists())

    def test_a_settings_failure_is_logged_and_does_not_lose_the_signup(self):
        with patch(
            "users.signals.Settings.objects.get_or_create",
            side_effect=RuntimeError("settings table gone"),
        ):
            with self.assertLogs("users.signals", level="ERROR"):
                user = make_teacher("settings.fail@gmail.com")

        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_a_wallet_failure_is_logged_and_does_not_lose_the_signup(self):
        with patch(
            "users.signals.CreditWallet.objects.get_or_create",
            side_effect=RuntimeError("wallet table gone"),
        ):
            with self.assertLogs("users.signals", level="ERROR"):
                user = make_teacher("wallet.fail@gmail.com")

        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_an_update_does_not_re_run_the_bootstrap(self):
        user = make_teacher("update.only@gmail.com")

        with patch("users.signals.Settings.objects.get_or_create") as mock_settings:
            user.first_name = "Renamed"
            user.save(update_fields=["first_name"])

        mock_settings.assert_not_called()


class TrialActivationSignalTests(TestCase):
    def test_a_non_teacher_is_skipped(self):
        with patch(
            "users.signals.SubscriptionService.activate_automatic_free_trial"
        ) as mock_trial:
            make_teacher("student.notrial@example.com", user_type=UserTypes.STUDENT)

        mock_trial.assert_not_called()

    def test_a_teacher_gets_the_automatic_trial(self):
        with patch(
            "users.signals.SubscriptionService.activate_automatic_free_trial"
        ) as mock_trial:
            user = make_teacher("trial.teacher@gmail.com")

        mock_trial.assert_called_once_with(user)

    def test_a_license_invitation_skips_the_trial(self):
        """A teacher joining under a school licence must not also get a trial."""
        with patch(
            "users.signals.get_license_invitation_context", return_value={"license": 1}
        ), patch(
            "users.signals.SubscriptionService.activate_automatic_free_trial"
        ) as mock_trial:
            make_teacher("licensed.teacher@acme-school.org")

        mock_trial.assert_not_called()

    def test_a_validation_failure_is_warned_not_raised(self):
        """e.g. the STANDARD plan row does not exist yet."""
        with patch(
            "users.signals.SubscriptionService.activate_automatic_free_trial",
            side_effect=ValueError("Free trial plan not found"),
        ):
            with self.assertLogs("users.signals", level="WARNING"):
                user = make_teacher("trial.validation@gmail.com")

        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_an_unexpected_failure_is_logged_not_raised(self):
        with patch(
            "users.signals.SubscriptionService.activate_automatic_free_trial",
            side_effect=RuntimeError("stripe exploded"),
        ):
            with self.assertLogs("users.signals", level="ERROR"):
                user = make_teacher("trial.unexpected@gmail.com")

        self.assertTrue(User.objects.filter(pk=user.pk).exists())


@override_settings(USE_BETA_PLAN_ON_SIGNUP=True)
class BetaPlanSignupSignalTests(TestCase):
    """The `USE_BETA_PLAN_ON_SIGNUP` branch, off by default."""

    def _beta_plan(self):
        return SubscriptionPlan.objects.create(
            name=PlanType.BETA,
            display_name="Beta",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            monthly_credits=25_000,
            is_active=True,
        )

    def test_the_beta_plan_is_activated_and_a_profile_recorded(self):
        plan = self._beta_plan()

        with patch(
            "users.signals.SubscriptionService.activate_subscription"
        ) as mock_activate:
            user = make_teacher("beta.teacher@gmail.com")

        mock_activate.assert_called_once_with(user, plan)
        profile = BetaProfile.objects.get(user=user)
        self.assertEqual(profile.initial_beta_credits, 25_000)

    def test_the_automatic_trial_is_not_also_activated(self):
        self._beta_plan()

        with patch("users.signals.SubscriptionService.activate_subscription"), patch(
            "users.signals.SubscriptionService.activate_automatic_free_trial"
        ) as mock_trial:
            make_teacher("beta.notrial@gmail.com")

        mock_trial.assert_not_called()

    def test_a_missing_beta_plan_warns_rather_than_failing_the_signup(self):
        with self.assertLogs("users.signals", level="WARNING"):
            user = make_teacher("beta.noplan@gmail.com")

        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_a_beta_activation_failure_is_logged_not_raised(self):
        self._beta_plan()

        with patch(
            "users.signals.SubscriptionService.activate_subscription",
            side_effect=RuntimeError("stripe exploded"),
        ):
            with self.assertLogs("users.signals", level="ERROR"):
                user = make_teacher("beta.fail@gmail.com")

        self.assertTrue(User.objects.filter(pk=user.pk).exists())


class ActivationEmailFailureTests(TestCase):
    """`send_user_activation_email` swallows and logs; it never raises."""

    def test_a_dispatch_failure_returns_none_and_logs(self):
        user = make_teacher("email.fail@gmail.com")

        with patch(
            "users.services.send_email_task.delay",
            side_effect=RuntimeError("broker exploded"),
        ):
            with self.assertLogs("users.services", level="ERROR"):
                result = send_user_activation_email(user)

        self.assertIsNone(result)

    def test_registration_survives_an_activation_email_failure(self):
        """The serializer wraps the send so a mail outage cannot lose a signup."""
        with patch(
            "users.serializers.send_user_activation_email",
            side_effect=RuntimeError("mail exploded"),
        ):
            with self.assertLogs("users.serializers", level="ERROR"):
                serializer = CustomUserSerializer(
                    data={
                        "email": "survives@gmail.com",
                        "password": PASSWORD,
                        "first_name": "Sur",
                        "last_name": "Vives",
                    }
                )
                self.assertTrue(serializer.is_valid(), serializer.errors)
                serializer.save()

        self.assertTrue(User.objects.filter(email="survives@gmail.com").exists())

    def test_a_creation_failure_is_reported_as_a_validation_error(self):
        with patch(
            "users.serializers.CustomUser.objects.create_user",
            side_effect=RuntimeError("db exploded"),
        ):
            serializer = CustomUserSerializer(
                data={
                    "email": "creation.fail@gmail.com",
                    "password": PASSWORD,
                    "first_name": "Cre",
                    "last_name": "Fail",
                }
            )
            self.assertTrue(serializer.is_valid(), serializer.errors)

            from rest_framework.exceptions import ValidationError

            with self.assertRaises(ValidationError) as ctx:
                serializer.save()

        self.assertNotIn("exploded", str(ctx.exception.detail))

    def test_an_update_failure_is_reported_as_a_validation_error(self):
        user = make_teacher("update.fail@gmail.com")
        serializer = CustomUserSerializer(user, data={"bio": "new"}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)

        with patch(
            "users.serializers.CustomUser.save",
            side_effect=RuntimeError("db exploded"),
        ):
            from rest_framework.exceptions import ValidationError

            with self.assertRaises(ValidationError) as ctx:
                serializer.save()

        self.assertNotIn("exploded", str(ctx.exception.detail))

    def test_a_password_change_through_the_serializer_is_hashed(self):
        user = make_teacher("pwd.update@gmail.com")
        new_password = "another-strong-password-88"  # pragma: allowlist secret

        serializer = CustomUserSerializer(
            user, data={"password": new_password}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        user.refresh_from_db()
        self.assertTrue(user.check_password(new_password))
        self.assertNotEqual(user.password, new_password)


class OTPManagerTests(TestCase):
    def test_generated_codes_are_six_digits(self):
        code = OTPManager().generate_otp()

        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_the_cache_key_is_a_stable_hash_of_the_identifier(self):
        first = OTPManager.get_cache_key("someone@example.com")
        second = OTPManager.get_cache_key("someone@example.com")

        self.assertEqual(first, second)
        self.assertNotEqual(first, OTPManager.get_cache_key("other@example.com"))
        self.assertNotIn("someone@example.com", first)


class RendererEdgeTests(TestCase):
    def test_a_non_mapping_body_falls_back_to_the_default_message(self):
        self.assertEqual(get_response_message(["a", "b"], "fallback"), "fallback")

    def test_an_empty_error_payload_reports_a_generic_message(self):
        self.assertEqual(flatten_errors({}), "An error occurred")

    def test_non_field_errors_are_reported_without_a_field_label(self):
        message = flatten_errors({"non_field_errors": ["Something is wrong."]})

        self.assertEqual(message, "Something is wrong.")

    def test_a_renderer_without_a_response_context_still_renders(self):
        rendered = APIJSONRenderer().render({"a": 1}, renderer_context=None)

        self.assertIn(b'"a"', rendered)

    @override_settings(DEBUG=True)
    def test_a_traceback_is_attached_only_in_debug(self):
        response = Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        response.exception = True
        response.data = None
        try:
            raise KeyError("boom")
        except KeyError as exc:
            response._raw_exc = exc

        rendered = APIJSONRenderer().render(
            None, renderer_context={"response": response}
        )

        self.assertIn(b"traceback", rendered)


class UtilsEdgeTests(TestCase):
    def test_a_domain_that_cannot_be_punycoded_is_rejected(self):
        """An IDNA failure must fail closed, not raise."""
        # A label longer than 63 octets is not encodable.
        self.assertEqual(email_domain("a@" + "x" * 70 + ".com"), "")

    def test_an_empty_domain_is_not_business(self):
        self.assertFalse(is_business_email("not-an-email"))


class LoggingSanityTests(TestCase):
    """The signal module logs through its own named logger, not the root."""

    def test_the_signal_logger_is_named_for_its_module(self):
        from users import signals

        self.assertIsInstance(signals.logger, logging.Logger)
        self.assertEqual(signals.logger.name, "users.signals")
