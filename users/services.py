import hashlib
import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.crypto import get_random_string


def send_user_activation_email(user):
    token = secrets.token_urlsafe(16)
    user.activation_token = token
    user.activation_expires = timezone.now() + timedelta(minutes=15)
    user.save()

    protocol = "https://"
    frontend_domain = settings.FRONTEND_DOMAIN

    activation_url = f"{protocol}{frontend_domain}/verify/{token}"

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
