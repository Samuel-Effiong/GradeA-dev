import hashlib
import logging
import string
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Avg, Max
from django.db.models.functions import ExtractHour
from django.utils import timezone
from django.utils.crypto import get_random_string

from AutoGrader.tasks import send_email_task

logger = logging.getLogger(__name__)


def generate_temporary_password(user):
    """A random password meeting AUTH_PASSWORD_VALIDATORS, never logged.

    Shared by every invite flow that hands a real, usable password to an
    account it creates or resets rather than leaving it with
    set_unusable_password() - the license-teacher invite
    (billing/license_service.py) and the single-add student course invite
    (classrooms/services/enrollment.py).
    """
    from django.contrib.auth.password_validation import validate_password

    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*"
    for _ in range(10):
        candidate = get_random_string(20, allowed_chars=alphabet)
        try:
            validate_password(candidate, user=user)
        except Exception:
            continue
        return candidate
    # Astronomically unlikely with a 20-char/66-symbol alphabet, but never
    # fall through to a weaker password.
    raise RuntimeError("Failed to generate a password passing validation.")


def send_user_activation_email(user):
    # Local import to dodge a circular import: users.models imports
    # OTPManager from this module at module load time.
    from users.models import UserTypes

    if user.user_type == UserTypes.SCHOOL_ADMIN:
        # A school admin account is invitation-only and is_active=False
        # only ever means "still pending that invite" for this user_type
        # (the only completion path, /auth/register/school-admin, sets
        # is_active=True and email_verified_at together - see H-42). The
        # generic flow below has no password step and would overwrite this
        # user's still-valid invite token with one leading to a dead end -
        # resend the actual invitation instead.
        from classrooms.serializers import resend_school_admin_invitation

        try:
            return resend_school_admin_invitation(user)
        except Exception:
            logger.exception(
                "Failed to resend school admin invitation to %s",
                getattr(user, "email", None),
            )
            return None

    try:
        token = otp_manager.generate_otp()
        user.activation_token = token
        user.activation_expires = timezone.now() + timedelta(minutes=15)
        user.save()

        protocol = "https://"
        frontend_domain = (
            settings.STUDENT_FRONTEND_DOMAIN
            if user.user_type == UserTypes.STUDENT
            else settings.FRONTEND_DOMAIN
        )

        activation_url = (
            f"{protocol}{frontend_domain}/verify-email?email={user.email}&token={token}"
        )

        top_content = """
        Your account is ready. Confirm your email address to activate your access and start managing grading,
        submissions, and course activity with confidence.<br><br>
        """

        bottom_content = """
        This link expires in 15 minutes. <br>
        If you did not create this account, you can safely ignore this email<br>.
        """

        merge_data = {
            "title": "Activate your Grade A+ account",
            "name": f"{user.first_name}",
            "activation_url": activation_url,
            "top_content": top_content,
            "bottom_content": bottom_content,
            "support_email": settings.SUPPORT_EMAIL,
            "current_year": timezone.now().year,
        }

        return send_email_task.delay(
            subject="Verify your email and get started with faster, smarter grading",
            message="",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=None,
            template_id="ynrw7gy0ye2l2k8e",
            merge_data=merge_data,
        )
    except Exception as e:
        logger.exception(
            "Failed to queue activation email for user %s %s",
            getattr(user, "email", None),
            str(e),
        )
        return None


class OTPManager:
    def __init__(self, length=6):
        self.otp_length = length

    def generate_otp(self):
        otp = get_random_string(self.otp_length, allowed_chars=string.digits)
        return otp

    @staticmethod
    def get_cache_key(identifier):
        md5_hash = hashlib.sha256(identifier.encode("utf-8")).hexdigest()

        return md5_hash


def cleanup_expired_users():
    """
    Synchronize the presence index with the individual heartbeat keys.

    Key names come from users.middleware so the two sides cannot drift -
    they were previously two independent string literals, which is how a
    rename would silently orphan the set.
    """
    from users.middleware import ONLINE_SET_KEY, heartbeat_key_for

    # Get all members from the index set
    all_members = cache.smembers(ONLINE_SET_KEY)

    if not all_members:
        return 0

    expired_members = []

    for member in all_members:
        member_str = member.decode() if isinstance(member, bytes) else member

        user_type, _, user_id = member_str.partition(":")
        heartbeat_key = heartbeat_key_for(user_type, user_id)

        # If the heartbeat key is gone, the user's TTL has expired
        if not cache.has_key(heartbeat_key):
            expired_members.append(member_str)

        # Batch remove the expired users from the set
    if expired_members:
        cache.srem(ONLINE_SET_KEY, *expired_members)

    return len(expired_members)


def get_current_concurrent_users():
    from users.middleware import ONLINE_SET_KEY

    members = cache.smembers(ONLINE_SET_KEY)
    return len(members) if members else 0


def get_opted_in_school_admins(school, *, flag):
    """Active, emailed SCHOOL_ADMIN users for one school who have opted in
    to the given Settings notification flag (e.g. "notify_weekly_summary")."""
    from users.models import CustomUser, UserTypes

    if not school:
        return CustomUser.objects.none()

    return (
        CustomUser.objects.filter(
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
            is_active=True,
            email__isnull=False,
            **{f"settings__{flag}": True},
        )
        .exclude(email="")
        .distinct()
    )


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
