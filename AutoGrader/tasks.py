import logging

from celery import shared_task
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(self, subject, message, from_email, recipient_list, html_message):
    logger.info("Executing send_email_task")

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=recipient_list,
            html_message=html_message,
            fail_silently=False,
        )

        return f"Email sent successfully to {recipient_list}"
    except Exception as exc:
        raise self.retry(exc=exc) from exc
