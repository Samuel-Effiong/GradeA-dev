"""Kept upload files: where their bytes live (Epic B1b-5, 08b §3.2).

Cloudinary, called through its SDK directly (B1b-0 spike, NOTES §0.4): raw,
PRIVATE assets under `kept_uploads/<deployment>/<random hex>` (owner,
2026-11-19: one cloud is shared by every environment, so each keeps its own
folder; rows stored before keep their `kept_uploads/<random hex>` key, and
load and delete use whatever key a row holds). Standing rules (NOTES,
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
import re
import threading
import time
import uuid
from dataclasses import dataclass

import cloudinary.uploader
import cloudinary.utils
import requests
import urllib3
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
    #: What the storage said it kept (Cloudinary's upload reply: `bytes` and
    #: `etag`, the md5 of the bytes), for a caller that checks it; None when
    #: the reply did not say (or the memory backend, which keeps the bytes
    #: as given).
    reply_bytes: int | None = None
    reply_etag: str | None = None


def _memory():
    return settings.KEPT_UPLOADS_BACKEND == "memory"


def deployment_folder():
    """The folder this deployment's new kept files go under: the name Sentry
    files its events under (H-246: SENTRY_ENVIRONMENT when it holds a name,
    else ENVIRONMENT; beta runs with ENVIRONMENT=prod and only
    SENTRY_ENVIRONMENT=beta tells it from production), as a lowercase slug of
    letters, digits and single hyphens ("unknown" if nothing is left)."""
    from AutoGrader.sentry_environment import sentry_environment

    name = sentry_environment(
        str(getattr(settings, "ENVIRONMENT", "") or ""),
        getattr(settings, "SENTRY_ENVIRONMENT", ""),
    )
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "unknown"


def new_key():
    """A new random storage key, chosen before the bytes are stored so a
    caller can write its row first (upload quotes: a dead request leaves a
    row, never an asset nobody knows)."""
    return f"{KEY_PREFIX}{deployment_folder()}/{uuid.uuid4().hex}"


_POOL_LOCK = threading.Lock()
_pool_sized = False


def size_cloudinary_pool():
    """Give the Cloudinary SDK's urllib3 pool room for every store running at
    once in this process (UPLOAD_QUOTE_STORE_CONCURRENCY files per task, up to
    UPLOAD_QUOTE_STORE_SLOTS tasks). The SDK builds its pool once at import
    with urllib3's default of one connection per host and has no setting for
    it, so each extra parallel store logged "Connection pool is full,
    discarding connection" (live load test, 2026-11-26). The pool is rebuilt
    once with the SDK's own builder and its own options, plus `maxsize`."""
    global _pool_sized
    with _POOL_LOCK:
        if _pool_sized:
            return
        import cloudinary
        import cloudinary.utils

        current = getattr(cloudinary.uploader, "_http", None)
        if current is not None and hasattr(current, "connection_pool_kw"):
            size = max(1, int(settings.UPLOAD_QUOTE_STORE_CONCURRENCY)) * max(
                1, int(settings.UPLOAD_QUOTE_STORE_SLOTS)
            )
            cloudinary.uploader._http = cloudinary.utils.get_http_connector(
                cloudinary.config(), {**cloudinary.CERT_KWARGS, "maxsize": size}
            )
        _pool_sized = True


def store(content, key=None):
    """Keep `content` (bytes) under `key`, else under a new random key."""
    key = key or new_key()
    reply: dict = {}
    if _memory():
        MEMORY[key] = bytes(content)
    else:
        size_cloudinary_pool()
        try:
            reply = cloudinary.uploader.upload(
                io.BytesIO(content),
                public_id=key,
                resource_type="raw",
                type="private",
                overwrite=False,
                # One deadline for the whole call (connect and every read),
                # passed through the SDK to urllib3: a hung upload fails as
                # KeptFileUnavailable instead of holding its worker.
                timeout=urllib3.Timeout(
                    total=settings.KEPT_FILES_UPLOAD_TIMEOUT_SECONDS
                ),
            )
        except Exception as exc:
            raise KeptFileUnavailable(type(exc).__name__) from exc
    if not isinstance(reply, dict):
        reply = {}
    reply_bytes = reply.get("bytes")
    reply_etag = reply.get("etag")
    return StoredFile(
        storage_key=key,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        reply_bytes=int(reply_bytes) if reply_bytes is not None else None,
        reply_etag=str(reply_etag) if reply_etag else None,
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
