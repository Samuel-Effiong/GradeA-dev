import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def sample_periodic_task():
    logger.info("Executing sample periodic task")
    return "Task completed"
