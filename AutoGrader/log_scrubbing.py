"""
H-89: no email address, and no URL password, in anything this process logs.

WHY A RECORD FACTORY AND NOT A HANDLER FILTER
--------------------------------------------
settings.LOGGING has one handler ("console") and routes only a few loggers
to it. Everything else (billing.*, users.*, classrooms.*, ...) is printed
by whatever the process provides: Python's last-resort stderr handler,
Celery's own handlers in a worker, gunicorn's in a web process. A filter on
the configured handler would cover a small part of what is emitted.

A log record factory makes every record, whichever logger created it and
whichever handler prints it. `install()` wraps the factory already in place
(never assuming it is Python's default) so that each record:
  * returns its formatted message with addresses and URL credentials
    replaced, from `getMessage()`, which is what every formatter prints;
  * carries its exception text already rendered and scrubbed in
    `exc_text`, which formatters reuse instead of rendering it again.
`record.msg`, `record.args` and `record.exc_info` are left as they were for
anything that reads them directly. (Sentry does: see sentry_scrubbing.)

WHAT IS REPLACED
----------------
  * `local-part@domain.tld` becomes `[email]`.
  * The userinfo of a URL (the user and password between "://" and the
    "@" before the host) becomes `[credentials]`, whatever the host looks
    like, so no DSN password is printed, on a dotless host such as
    `redis` or `localhost` either.
A message with no "@" in it is returned as it is, without running either
pattern.

IT NEVER RAISES, AND IT FAILS CLOSED
------------------------------------
If scrubbing (or the record's own formatting) fails, the record's message
is its unformatted template plus a fixed marker, never its arguments, and
its exception text is the exception's class plus a marker.

OFF UNDER THE TEST RUNNER
-------------------------
settings.LOG_SCRUB_ADDRESSES is False when tests run: the tests that prove
log lines carry ids and not addresses (H-80, H-91) read the captured
output, and would pass vacuously on scrubbed text. Tests of the scrubber
switch it on with override_settings(LOG_SCRUB_ADDRESSES=True).
"""

import logging
import re
import traceback

from django.core.signals import setting_changed

EMAIL = "[email]"
CREDENTIALS = "[credentials]"
MESSAGE_WITHHELD = "[log arguments withheld: the message could not be scrubbed]"
EXCEPTION_WITHHELD = "[exception text withheld: it could not be scrubbed]"

_ADDRESS = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)
#: The userinfo part of a URL: everything between "://" and the "@" that
#: ends it, with no "/" or whitespace in between.
_USERINFO = re.compile(r"(?<=://)[^\s/@]+(?=@)")

_MARKER = "_scrubs_addresses"
_enabled = False


def scrub(text):
    """`text` with every email address and URL userinfo replaced."""
    if "@" not in text:
        return text
    return _ADDRESS.sub(EMAIL, _USERINFO.sub(CREDENTIALS, text))


def is_enabled():
    return _enabled


def set_enabled(value):
    global _enabled
    _enabled = bool(value)


class _ScrubbedMessage:
    """getMessage(), scrubbed. Mixed in ahead of the record's own class."""

    def getMessage(self):
        if not _enabled:
            return super().getMessage()  # type: ignore[misc]
        try:
            return scrub(super().getMessage())  # type: ignore[misc]
        except Exception:  # noqa: BLE001 - never into the caller
            return _withheld_message(self)


class ScrubbedLogRecord(_ScrubbedMessage, logging.LogRecord):
    """The standard record, scrubbed. A module-level class, so a record
    still pickles (QueueHandler, multiprocessing)."""


_subclasses: dict = {logging.LogRecord: ScrubbedLogRecord}


def _scrubbed_class(base):
    """The scrubbed subclass of `base`, the class of the record the wrapped
    factory made. Another factory's own record class gets one too, kept
    under a module-level name so it pickles by reference like the
    standard one."""
    cls = _subclasses.get(base)
    if cls is None:
        name = f"Scrubbed{base.__name__}"
        cls = type(name, (_ScrubbedMessage, base), {"__module__": __name__})
        globals()[name] = cls
        _subclasses[base] = cls
    return cls


def _withheld_message(record):
    template = record.msg if isinstance(record.msg, str) else type(record.msg).__name__
    try:
        template = scrub(template)
    except Exception:  # noqa: BLE001
        template = ""
    return f"{template} {MESSAGE_WITHHELD}".strip()


def _scrubbed_exception_text(exc_info):
    """What logging.Formatter.formatException renders, scrubbed."""
    try:
        text = "".join(traceback.format_exception(*exc_info))
        return scrub(text[:-1] if text.endswith("\n") else text)
    except Exception:  # noqa: BLE001 - never into the caller
        name = getattr(exc_info[0], "__name__", "Exception")
        return f"{name}: {EXCEPTION_WITHHELD}"


def install(enabled):
    """Wrap the current log record factory. Idempotent: a second call (a
    re-import of settings) only updates the switch."""
    set_enabled(enabled)
    previous = logging.getLogRecordFactory()
    if getattr(previous, _MARKER, False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            record.__class__ = _scrubbed_class(type(record))
            if _enabled and isinstance(record.exc_info, tuple) and record.exc_info[0]:
                record.exc_text = _scrubbed_exception_text(record.exc_info)
        except Exception:  # noqa: BLE001 - a record must always be made
            # A record whose class cannot be replaced: closed, not open.
            if _enabled:
                record.msg, record.args = _withheld_message(record), None
        return record

    setattr(factory, _MARKER, True)
    factory.wrapped = previous  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


def is_installed():
    return getattr(logging.getLogRecordFactory(), _MARKER, False)


def _follow_the_setting(*, setting, value, **kwargs):
    """override_settings(LOG_SCRUB_ADDRESSES=...) switches the scrubber."""
    if setting == "LOG_SCRUB_ADDRESSES":
        if value is None:
            from django.conf import settings

            value = getattr(settings, "LOG_SCRUB_ADDRESSES", False)
        set_enabled(value)


setting_changed.connect(_follow_the_setting)
