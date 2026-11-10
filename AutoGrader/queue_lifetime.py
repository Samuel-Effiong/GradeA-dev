"""QUEUE_LIFETIME_DAYS (08b §2.1; brought forward to B1b-5 by the SM, Q1).

One setting drives the queue lifetime (B1b-6), the kept-file lifetime
(B1b-5, `students.KeptUpload.kept_until`) and the day count in the approved
texts (B1b-8). It is read once, at settings load, by every service (web,
worker, beat). A value that isn't a whole number of days from 1 to 365 stops
the process at start-up: a typo must never become 0 days or a huge number.
"""

from django.core.exceptions import ImproperlyConfigured

DEFAULT_DAYS = 30
MIN_DAYS = 1
MAX_DAYS = 365


def parse_queue_lifetime_days(raw):
    """The setting from its raw environment value (None when unset)."""
    if raw is None:
        return DEFAULT_DAYS
    text = str(raw).strip()
    if text.isdigit() and MIN_DAYS <= int(text) <= MAX_DAYS:
        return int(text)
    raise ImproperlyConfigured(
        f"QUEUE_LIFETIME_DAYS must be a whole number of days from {MIN_DAYS} "
        f"to {MAX_DAYS}, got {raw!r}."
    )
