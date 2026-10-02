from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from AutoGrader.tasks import send_email_task


class SendEmailTaskTests(SimpleTestCase):
    @patch("AutoGrader.tasks.send_mail")
    @patch("AutoGrader.tasks.EmailMultiAlternatives")
    def test_falls_back_to_plain_send_mail_when_templated_send_fails(
        self, mock_email_multi: Mock, mock_send_mail: Mock
    ) -> None:
        mock_mail = mock_email_multi.return_value
        mock_mail.send.side_effect = Exception("Template rejected by provider")

        send_email_task.__wrapped__(
            subject="Welcome",
            message="Plain body",
            from_email="from@example.com",
            recipient_list=["teacher@example.com"],
            html_message="<p>hi</p>",
            template_id="template-123",
            merge_data={"name": "Teacher"},
        )

        mock_send_mail.assert_called_once_with(
            subject="Welcome",
            message="Plain body",
            from_email="from@example.com",
            recipient_list=["teacher@example.com"],
            html_message="<p>hi</p>",
            fail_silently=False,
        )

    @patch("AutoGrader.tasks._send_email_impl")
    def test_retries_on_transient_send_failure(self, mock_send_impl: Mock) -> None:
        """A single transient failure should be retried, not dropped."""
        mock_send_impl.side_effect = [
            Exception("Provider timed out"),
            "Email sent successfully to ['teacher@example.com']",
        ]

        result = send_email_task.apply(
            kwargs={
                "subject": "Welcome",
                "message": "",
                "from_email": "from@example.com",
                "recipient_list": ["teacher@example.com"],
                "html_message": None,
                "template_id": "template-123",
                "merge_data": {"name": "Teacher"},
            }
        )

        self.assertEqual(mock_send_impl.call_count, 2)
        self.assertEqual(
            result.get(), "Email sent successfully to ['teacher@example.com']"
        )

    @patch("AutoGrader.tasks._send_email_impl")
    def test_gives_up_after_max_retries(self, mock_send_impl: Mock) -> None:
        """A permanently-failing send stops after max_retries, not forever."""
        mock_send_impl.side_effect = Exception("Provider permanently rejecting")

        result = send_email_task.apply(
            kwargs={
                "subject": "Welcome",
                "message": "",
                "from_email": "from@example.com",
                "recipient_list": ["teacher@example.com"],
                "html_message": None,
                "template_id": "template-123",
                "merge_data": {"name": "Teacher"},
            }
        )

        # Initial attempt + 3 retries configured via max_retries=3. Celery
        # re-raises the original exception (not MaxRetriesExceededError)
        # once retries are exhausted, since retry() was called with exc=.
        self.assertEqual(mock_send_impl.call_count, 4)
        with self.assertRaisesMessage(Exception, "Provider permanently rejecting"):
            result.get()
