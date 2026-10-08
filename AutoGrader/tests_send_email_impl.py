"""
Tests for AutoGrader.tasks._send_email_impl's branches that
AutoGrader/tests.py's SendEmailTaskTests don't reach: those tests all
either mock `_send_email_impl` itself (never running its body) or drive it
through exactly one path (templated send fails, falls back, merge_data is a
flat dict). The successful-send path, the "no fallback possible" failure
mode, the already-per-recipient merge_data shape, and a failure while
building the message itself were all previously unexercised - `coverage`
on AutoGrader/tasks.py sat at 72.3% against ~96%+ everywhere else in this
section, entirely on these branches.
"""

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from AutoGrader.tasks import _send_email_impl


class SendEmailImplTests(SimpleTestCase):
    @patch("AutoGrader.tasks.send_mail")
    @patch("AutoGrader.tasks.EmailMultiAlternatives")
    def test_successful_send_returns_success_message_without_fallback(
        self, mock_email_multi: Mock, mock_send_mail: Mock
    ) -> None:
        mock_mail = mock_email_multi.return_value

        result = _send_email_impl(
            subject="Welcome",
            message="Plain body",
            from_email="from@example.com",
            recipient_list=["teacher@example.com"],
        )

        self.assertEqual(result, "Email sent successfully to ['teacher@example.com']")
        mock_mail.send.assert_called_once_with(fail_silently=False)
        mock_send_mail.assert_not_called()

    @patch("AutoGrader.tasks.send_mail")
    @patch("AutoGrader.tasks.EmailMultiAlternatives")
    def test_html_message_is_attached_as_alternative_when_provided(
        self, mock_email_multi: Mock, mock_send_mail: Mock
    ) -> None:
        mock_mail = mock_email_multi.return_value

        _send_email_impl(
            subject="Welcome",
            message="Plain body",
            from_email="from@example.com",
            recipient_list=["teacher@example.com"],
            html_message="<p>hi</p>",
        )

        mock_mail.attach_alternative.assert_called_once_with("<p>hi</p>", "text/html")

    @patch("AutoGrader.tasks.send_mail")
    @patch("AutoGrader.tasks.EmailMultiAlternatives")
    def test_no_html_message_means_no_alternative_attached(
        self, mock_email_multi: Mock, mock_send_mail: Mock
    ) -> None:
        mock_mail = mock_email_multi.return_value

        _send_email_impl(
            subject="Welcome",
            message="Plain body",
            from_email="from@example.com",
            recipient_list=["teacher@example.com"],
            html_message=None,
        )

        mock_mail.attach_alternative.assert_not_called()

    @patch("AutoGrader.tasks.send_mail")
    @patch("AutoGrader.tasks.EmailMultiAlternatives")
    def test_flat_merge_data_is_wrapped_identically_for_every_recipient(
        self, mock_email_multi: Mock, mock_send_mail: Mock
    ) -> None:
        mock_mail = mock_email_multi.return_value
        recipients = ["a@example.com", "b@example.com"]

        _send_email_impl(
            subject="Welcome",
            message="Plain body",
            from_email="from@example.com",
            recipient_list=recipients,
            template_id="template-123",
            merge_data={"name": "Teacher"},
        )

        self.assertEqual(
            mock_mail.merge_data,
            {
                "a@example.com": {"name": "Teacher"},
                "b@example.com": {"name": "Teacher"},
            },
        )

    @patch("AutoGrader.tasks.send_mail")
    @patch("AutoGrader.tasks.EmailMultiAlternatives")
    def test_per_recipient_merge_data_is_used_as_is_not_rewrapped(
        self, mock_email_multi: Mock, mock_send_mail: Mock
    ) -> None:
        mock_mail = mock_email_multi.return_value
        per_recipient = {
            "a@example.com": {"name": "Alice"},
            "b@example.com": {"name": "Bob"},
        }

        _send_email_impl(
            subject="Welcome",
            message="Plain body",
            from_email="from@example.com",
            recipient_list=["a@example.com", "b@example.com"],
            template_id="template-123",
            merge_data=per_recipient,
        )

        self.assertEqual(mock_mail.merge_data, per_recipient)

    @patch("AutoGrader.tasks.send_mail")
    @patch("AutoGrader.tasks.EmailMultiAlternatives")
    def test_template_id_without_merge_data_sets_template_id_only(
        self, mock_email_multi: Mock, mock_send_mail: Mock
    ) -> None:
        mock_mail = mock_email_multi.return_value

        _send_email_impl(
            subject="Welcome",
            message="Plain body",
            from_email="from@example.com",
            recipient_list=["a@example.com"],
            template_id="template-123",
            merge_data=None,
        )

        self.assertEqual(mock_mail.template_id, "template-123")
        # merge_data assignment is nested inside `if merge_data:` - without
        # it, mock_mail.merge_data is never reassigned to a literal dict.
        self.assertIsInstance(mock_mail.merge_data, Mock)

    @patch("AutoGrader.tasks.send_mail")
    @patch("AutoGrader.tasks.EmailMultiAlternatives")
    def test_no_template_id_means_merge_data_is_never_set(
        self, mock_email_multi: Mock, mock_send_mail: Mock
    ) -> None:
        mock_mail = mock_email_multi.return_value

        _send_email_impl(
            subject="Welcome",
            message="Plain body",
            from_email="from@example.com",
            recipient_list=["a@example.com"],
            merge_data={"name": "Teacher"},
        )

        # merge_data is only ever assigned inside the `if template_id:`
        # branch - without a template_id, mock_mail.merge_data was never
        # reassigned, so it's still Mock's auto-generated child attribute
        # rather than the literal dict that would prove assignment happened.
        self.assertIsInstance(mock_mail.merge_data, Mock)

    @patch("AutoGrader.tasks.send_mail")
    @patch("AutoGrader.tasks.EmailMultiAlternatives")
    def test_raises_without_fallback_when_no_body_available(
        self, mock_email_multi: Mock, mock_send_mail: Mock
    ) -> None:
        """
        A templated-only send (no plain `message`, no `html_message`) that
        fails at the provider has nothing for send_mail to send instead -
        per the module docstring, this must raise the original error rather
        than attempt a fallback that would send an empty email.
        """
        mock_mail = mock_email_multi.return_value
        mock_mail.send.side_effect = Exception("Template rejected by provider")

        with self.assertRaisesMessage(Exception, "Template rejected by provider"):
            _send_email_impl(
                subject="Welcome",
                message="",
                from_email="from@example.com",
                recipient_list=["teacher@example.com"],
                html_message=None,
                template_id="template-123",
                merge_data={"name": "Teacher"},
            )

        mock_send_mail.assert_not_called()

    @patch("AutoGrader.tasks.send_mail")
    @patch("AutoGrader.tasks.EmailMultiAlternatives")
    def test_falls_back_when_html_message_present_even_without_plain_message(
        self, mock_email_multi: Mock, mock_send_mail: Mock
    ) -> None:
        # A non-empty html_message alone is enough for the fallback to have
        # something to send, so this must NOT hit the "no fallback
        # possible" raise above.
        mock_mail = mock_email_multi.return_value
        mock_mail.send.side_effect = Exception("Template rejected by provider")

        result = _send_email_impl(
            subject="Welcome",
            message="",
            from_email="from@example.com",
            recipient_list=["teacher@example.com"],
            html_message="<p>hi</p>",
            template_id="template-123",
            merge_data={"name": "Teacher"},
        )

        mock_send_mail.assert_called_once()
        self.assertEqual(
            result, "Fallback plain email sent successfully to ['teacher@example.com']"
        )

    @patch("AutoGrader.tasks.EmailMultiAlternatives")
    def test_failure_building_the_message_itself_propagates(
        self, mock_email_multi: Mock
    ) -> None:
        """
        A failure before the message is even sent (e.g. EmailMultiAlternatives
        rejecting a malformed argument) is a different failure mode than a
        provider-side send failure - the outer except in _send_email_impl
        must still log and re-raise it rather than swallow it.
        """
        mock_email_multi.side_effect = TypeError("unexpected keyword argument")

        with self.assertRaises(TypeError):
            _send_email_impl(
                subject="Welcome",
                message="Plain body",
                from_email="from@example.com",
                recipient_list=["teacher@example.com"],
            )
