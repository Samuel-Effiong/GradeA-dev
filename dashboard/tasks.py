from celery import shared_task
from django.utils import timezone

from users.models import ConcurrentUserSnapshot
from users.services import cleanup_expired_users, get_current_concurrent_users


@shared_task
def record_concurrent_users():
    expired_users = cleanup_expired_users()
    count = get_current_concurrent_users()

    ConcurrentUserSnapshot.objects.create(
        concurrent_users=count,
        timestamp=timezone.now(),
    )

    return f"Expired users: {expired_users}, Current users: {count}"
