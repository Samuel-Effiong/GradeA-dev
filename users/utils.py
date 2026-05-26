import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


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
