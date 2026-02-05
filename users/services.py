import hashlib
import string
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models import Avg, Max
from django.db.models.functions import ExtractHour
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.crypto import get_random_string


def send_user_activation_email(user):
    token = otp_manager.generate_otp()
    user.activation_token = token
    user.activation_expires = timezone.now() + timedelta(minutes=15)
    user.save()

    protocol = "https://"
    frontend_domain = settings.FRONTEND_DOMAIN

    activation_url = (
        f"{protocol}{frontend_domain}/verify-email?email={user.email}&token={token}"
    )

    context = {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "token": user.activation_token,
        "activation_url": activation_url,
        "expiration_duration": 15,
        "support_email": settings.SUPPORT_EMAIL,
        "current_year": timezone.now().year,
    }

    html_content = render_to_string("email/token_activation.html", context=context)

    return send_mail(
        subject="Activate your account",
        message="",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_content,
        fail_silently=False,
    )


class OTPManager:
    def __init__(self, length=6):
        self.otp_length = length
        # self.max_attempts = max_attempts

    def generate_otp(self):
        otp = get_random_string(self.otp_length, allowed_chars=string.digits)
        return otp

    @staticmethod
    def get_cache_key(identifier):
        md5_hash = hashlib.sha256(identifier.encode("utf-8")).hexdigest()

        return md5_hash


def cleanup_expired_users():
    """
    Synchronize the "online_users_set" index with individual heatbeat keys
    """

    # Get all memebers from the Index set
    all_members = cache.smembers("online_users_set")

    if not all_members:
        return 0

    expired_members = []

    for member in all_members:
        member_str = member.decode() if isinstance(member, bytes) else member

        hearbeat_key = f"active_user:{member_str}"

        # If the heartbeat key is gone, the user's TTL has expired
        if not cache.has_key(hearbeat_key):
            expired_members.append(member_str)

        # Batch remove the expired users from the set
        if expired_members:
            cache.srem("online_users_set", *expired_members)

        return len(expired_members)


def get_current_concurrent_users():

    keys = cache.keys("active_user*")
    all_active_data = cache.get_many(keys)

    return len(all_active_data)


def base_queryset(start=None, end=None):
    from users.models import ConcurrentUserSnapshot

    qs = ConcurrentUserSnapshot.objects.all()
    if start:
        qs = qs.filter(timestamp__gte=start)
    if end:
        qs = qs.filter(timestamp__lte=end)
    return qs


def get_peak_concurrent_users(start=None, end=None):
    return (
        base_queryset(start, end).aggregate(Max("concurrent_users"))[
            "concurrent_users__max"
        ]
        or 0
    )


def get_peak_time_of_day(start=None, end=None):
    qs = (
        base_queryset(start, end)
        .annotate(hour=ExtractHour("timestamp"))
        .values("hour")
        .annotate(avg_users=Avg("concurrent_users"))
        .order_by("-avg_users")
    )
    top = qs.first()

    if top is None:
        return {"hour": None, "label": "No data available", "average_users": 0}

    return {
        "hour": top["hour"],
        "label": f"{top['hour']:02d}:00 - {top['hour'] + 1:02d}:00",
        "average_users": round(top["avg_users"], 2),
    }


def get_time_range(range_key: str):
    now = timezone.now()

    if range_key == "daily":
        return now - timedelta(days=1), now
    if range_key == "weekly":
        return now - timedelta(days=7), now
    if range_key == "monthly":
        return now - timedelta(days=30), now

    raise ValueError("Invalid range")


otp_manager = OTPManager()
