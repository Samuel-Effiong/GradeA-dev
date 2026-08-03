import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def sample_periodic_task():
    logger.info("Executing sample periodic task")
    return "Task completed"


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_user_to_mailerlite(self, user_id):
    from users.mailerlite_service import MailerLiteService
    from users.models import CustomUser

    try:
        user = CustomUser.objects.select_related("school").get(id=user_id)
    except CustomUser.DoesNotExist:
        logger.warning("sync_user_to_mailerlite: user %s not found", user_id)
        return

    if MailerLiteService.sync_user(user) is False:
        raise self.retry()
