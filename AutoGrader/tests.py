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
