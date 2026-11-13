"""Kept upload files: where their bytes live (Epic B1b-5, 08b §3.2).

Cloudinary, called through its SDK directly (B1b-0 spike, NOTES §0.4): raw,
PRIVATE assets under `kept_uploads/<random hex>`. Standing rules (NOTES,
switch-on checklist):

* `type="private"`, never `use_filename`: the name never holds the student's
  or the file's name;
* no URL leaves this module. The signed download URL is built and fetched
  here, server-side, and only the bytes are returned;
* tests use the in-memory backend (`KEPT_UPLOADS_BACKEND = "memory"`, the
  default when tests run), so no test touches the network.

`KeptFileUnavailable` is a storage fault worth retrying; `KeptFileMissing`
says the asset is gone.
"""

import hashlib
import io
import time
import uuid
from dataclasses import dataclass

import cloudinary.uploader
import cloudinary.utils
import requests
from django.conf import settings

KEY_PREFIX = "kept_uploads/"
#: How long the signed download link the worker fetches is valid.
DOWNLOAD_LINK_SECONDS = 300
DOWNLOAD_TIMEOUT_SECONDS = 60

#: The memory backend's store (tests).
MEMORY: dict[str, bytes] = {}


class KeptFileError(Exception):
    """A kept file couldn't be stored, loaded or deleted."""


class KeptFileUnavailable(KeptFileError):
    """The storage didn't answer; trying again may work."""


class KeptFileMissing(KeptFileError):
    """The asset doesn't exist."""


@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    sha256: str
    size_bytes: int


def _memory():
    return settings.KEPT_UPLOADS_BACKEND == "memory"


def store(content):
    """Keep `content` (bytes) under a new random key."""
    key = KEY_PREFIX + uuid.uuid4().hex
    if _memory():
        MEMORY[key] = bytes(content)
    else:
        try:
            cloudinary.uploader.upload(
                io.BytesIO(content),
                public_id=key,
                resource_type="raw",
                type="private",
                overwrite=False,
            )
        except Exception as exc:
            raise KeptFileUnavailable(type(exc).__name__) from exc
    return StoredFile(
        storage_key=key,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def load(storage_key):
    """The bytes kept under `storage_key`."""
    if _memory():
        try:
            return MEMORY[storage_key]
        except KeyError:
            raise KeptFileMissing(storage_key) from None
    link = cloudinary.utils.private_download_url(
        storage_key,
        "",
        resource_type="raw",
        type="private",
        expires_at=int(time.time()) + DOWNLOAD_LINK_SECONDS,
    )
    try:
        response = requests.get(link, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise KeptFileUnavailable(type(exc).__name__) from exc
    if response.status_code == 404:
        raise KeptFileMissing(storage_key)
    if response.status_code != 200:
        raise KeptFileUnavailable(f"HTTP {response.status_code}")
    return response.content


def delete(storage_key):
    """Delete the asset. One that is already gone is not an error."""
    if _memory():
        MEMORY.pop(storage_key, None)
        return
    try:
        cloudinary.uploader.destroy(
            storage_key, resource_type="raw", type="private", invalidate=True
        )
    except Exception as exc:
        raise KeptFileUnavailable(type(exc).__name__) from exc
