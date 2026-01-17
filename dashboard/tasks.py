from celery import shared_task

from users.models import ConcurrentUserSnapshot
from users.services import get_current_concurrent_users


@shared_task
def record_concurrent_users():
    count = get_current_concurrent_users()
    ConcurrentUserSnapshot.objects.create(concurrent_users=count)
