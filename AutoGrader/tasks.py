"""Eyes to see Lord Jesus to anyone using this website"""

import logging

from celery import shared_task
from django.core.mail import EmailMultiAlternatives, send_mail

logger = logging.getLogger(__name__)


def _send_email_impl(
    subject,
    message,
    from_email,
    recipient_list,
    html_message=None,
    template_id=None,
    merge_data=None,
):
    logger.info("Executing send_email_task")

    try:
        mail = EmailMultiAlternatives(
            subject=subject,
            body=message,
            from_email=from_email,
            to=recipient_list,
        )

        if html_message:
            mail.attach_alternative(html_message, "text/html")

        # Logic for MailerSend Template
        if template_id:
            mail.template_id = template_id
            if merge_data:
                # Anymail expects merge_data to be a dict mapping recipient emails to their data
                # If you passed a simple dict of variables, we map it to every recipient
                if not any(isinstance(v, dict) for v in merge_data.values()):
                    mail.merge_data = {
                        recipient: merge_data for recipient in recipient_list
                    }
                else:
                    mail.merge_data = merge_data

        try:
            mail.send(fail_silently=False)
            logger.info("Email sent successfully to %s", recipient_list)
            return f"Email sent successfully to {recipient_list}"
        except Exception as exc:
            logger.warning(
                "Templated email send failed for %s, falling back to plain send_mail. Error: %s",
                recipient_list,
                exc,
            )
            send_mail(
                subject=subject,
                message=message,
                from_email=from_email,
                recipient_list=recipient_list,
                html_message=html_message,
                fail_silently=False,
            )
            return f"Fallback plain email sent successfully to {recipient_list}"
    except Exception as exc:
        logger.error("Error sending email: %s", exc)
        raise


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(
    self,
    subject,
    message,
    from_email,
    recipient_list,
    html_message=None,
    template_id=None,
    merge_data=None,
):
    logger.warning("sending email to subjec: %s", subject)
    return _send_email_impl(
        subject=subject,
        message=message,
        from_email=from_email,
        recipient_list=recipient_list,
        html_message=html_message,
        template_id=template_id,
        merge_data=merge_data,
    )
