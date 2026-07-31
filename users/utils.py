import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings

DEFAULT_DISALLOWED_DOMAINS = [
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "aol.com",
    "icloud.com",
    "mail.com",
    "protonmail.com",
    "zoho.com",
    "yandex.com",
    "live.com",
    "msn.com",
    "qq.com",
    "163.com",
    "mail.ru",
    "inbox.lv",
    "rediffmail.com",
    "lycos.com",
    "gmx.com",
    "fastmail.com",
    "hushmail.com",
]

DEFAULT_EXEMPT_DOMAINS = [
    "yopmail.com",
]


def get_cipher():
    """Derives a valid Fernet key from the Django SECRET_KEY."""
    key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key)
    return Fernet(fernet_key)


def encrypt_token(token: str) -> str:
    """Encrypts a string token using Fernet."""
    if not token:
        return token
    return get_cipher().encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    """Decrypts a Fernet encrypted string token."""
    if not encrypted_token:
        return encrypted_token
    return get_cipher().decrypt(encrypted_token.encode()).decode()


def is_business_email(email: str) -> bool:
    """Return True if email domain is not in the disallowed list."""

    domain = email.split("@")[-1].lower()
    disallowed = getattr(
        settings, "DISALLOWED_EMAIL_DOMAINS", DEFAULT_DISALLOWED_DOMAINS
    )

    return domain not in disallowed


def is_exempt_email_domain(email: str) -> bool:
    """Return True if the email domain is exempt from the personal/business
    email restriction (e.g. testing domains like yopmail.com), and should be
    accepted regardless of account type."""

    domain = email.split("@")[-1].lower()
    exempt = getattr(settings, "EXEMPT_EMAIL_DOMAINS", DEFAULT_EXEMPT_DOMAINS)

    return domain in exempt
