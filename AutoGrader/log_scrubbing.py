"""
H-89: no email address, and no URL password, in anything this process logs.
H-167: and no value written as `password=...`, `passwd=...`, `secret=...`
or `token=...`.

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
  * `local-part@domain.tld` becomes `[email]`: letters and digits of any
    script and the special characters our own validation accepts in the
    local part, and a domain as it is read or in punycode.
  * The userinfo of a URL (the user and password between "://" and the
    last "@" before the host) becomes `[credentials]`, whatever the host
    looks like, so no DSN password is printed, on a dotless host such as
    `redis` or `localhost` either.
  * (H-167) What follows `password=`, `passwd=`, `secret=` or `token=`
    (in any case, also at the end of a longer name such as
    `new_password=`) becomes `[secret]`, TO THE END OF THAT LINE: such a
    value may hold a comma, a bracket or a space, so nothing shorter is
    safe, and what follows it on the line is lost with it. This is the
    form in which redis-py's connection pool prints itself, password
    included, and a pool is what a frame of a failed dispatch holds.
The first two patterns are not run on a message with no "@" in it, nor the
third on one with no "=".

WHAT IS NOT, AND WHAT IS REPLACED WITH IT (KNOWN LIMITS)
--------------------------------------------------------
  * "/", "=", "?" and "&" are legal in a local part, but they are not
    followed: `email=a@b.co` and a URL with an address in its query stay
    readable. An address that itself contains one of the four keeps the
    piece before that character in print (a fragment, not an address).
  * A quote, brace or bar directly before an address is a character an
    address may start with, and is replaced with it.
  * A URL password with an unencoded "/" in it is not a URL any parser
    reads; nothing is replaced there.
  * An address written percent-encoded (`%40` for "@") is not recognised.
  * A secret under another name than the four above, or written in
    another form (`password: x`, a dict's `'password': 'x'`, a bare
    value), is not recognised.

IT NEVER RAISES, AND IT FAILS CLOSED
------------------------------------
If scrubbing (or the record's own formatting) fails, the record's message
is its unformatted template plus a fixed marker, never its arguments, and
its exception text is the exception's class plus a marker.

WHERE IT IS INSTALLED, AND ITS SWITCH
-------------------------------------
The AutoGrader package installs the factory when it is imported
(AutoGrader/__init__.py), which every process does before it loads
AutoGrader.settings. settings.py itself imports nothing from the project
(it must still load on its own, by path). The switch is
settings.LOG_SCRUB_ADDRESSES, read once Django's settings are configured;
a record made before that is scrubbed.

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
SECRET = "[secret]"
MESSAGE_WITHHELD = "[log arguments withheld: the message could not be scrubbed]"
EXCEPTION_WITHHELD = "[exception text withheld: it could not be scrubbed]"

#: A character of an address's local part: a letter or digit of any script,
#: or one of the special characters Django's validator accepts, EXCEPT
#: "/", "=", "?" and "&". Those four also separate a key from its value
#: and the parts of a URL, so the scrubber stops at them (see the
#: docstring above).
_LOCAL = r"[\w.!#$%'*+^`{|}~-]"
#: A character of a domain label: a letter or digit of any script, or "-"
#: (so an IDN domain matches as it is read and in its punycode form).
_LABEL = r"(?:[^\W_]|-)"
_ADDRESS = re.compile(
    # Only from the start of a run of local-part characters: the match is
    # the same, and a long unbroken token is scanned once, not once per
    # character.
    rf"(?<!{_LOCAL}){_LOCAL}+@"
    rf"{_LABEL}+(?:\.{_LABEL}+)*\.[^\W\d_]{_LABEL}+"
)
#: The userinfo part of a URL: everything between "://" and the LAST "@"
#: before the next "/" or whitespace (a password may hold an "@").
_USERINFO = re.compile(r"(?<=://)[^\s/]+(?=@)")
#: H-167: one of four names, "=", and the rest of the line.
_NAMED_VALUE = re.compile(r"(?i)(password|passwd|secret|token)=[^\n]+")

_MARKER = "_scrubs_addresses"
#: True or False once known; None until Django's settings are configured.
_enabled = None


def scrub(text):
    """`text` with every email address, URL userinfo and named secret
    value replaced."""
    if "=" in text:
        text = _NAMED_VALUE.sub(_name_kept, text)
    if "@" not in text:
        return text
    return _ADDRESS.sub(EMAIL, _USERINFO.sub(CREDENTIALS, text))


def _name_kept(match):
    return f"{match.group(1)}={SECRET}"


def is_enabled():
    """The switch: settings.LOG_SCRUB_ADDRESSES, read once Django's settings
    are configured. Until then (a record made while settings are still
    loading) the answer is yes: scrubbing a line too many costs nothing,
    and missing one is the failure this module exists to prevent."""
    global _enabled
    if _enabled is None:
        try:
            from django.conf import settings

            if settings.configured:
                _enabled = bool(getattr(settings, "LOG_SCRUB_ADDRESSES", True))
        except Exception:  # noqa: BLE001 - never into the caller
            pass
    return True if _enabled is None else _enabled


def set_enabled(value):
    """Set the switch (True or False), or forget it (None) so that it is
    read from settings again."""
    global _enabled
    _enabled = None if value is None else bool(value)


class _ScrubbedMessage:
    """getMessage(), scrubbed. Mixed in ahead of the record's own class."""

    def getMessage(self):
        if not is_enabled():
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


def install(enabled=None):
    """Wrap the current log record factory. Idempotent: a second call wraps
    nothing again. Called with no argument by the AutoGrader package; the
    switch then comes from settings (see is_enabled). A test may pass True
    or False to set it."""
    if enabled is not None:
        set_enabled(enabled)
    previous = logging.getLogRecordFactory()
    if getattr(previous, _MARKER, False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            record.__class__ = _scrubbed_class(type(record))
            if (
                is_enabled()
                and isinstance(record.exc_info, tuple)
                and record.exc_info[0]
            ):
                record.exc_text = _scrubbed_exception_text(record.exc_info)
        except Exception:  # noqa: BLE001 - a record must always be made
            # A record whose class cannot be replaced: closed, not open.
            if is_enabled():
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
        # None: the override ended and the setting did not exist before;
        # forget the switch so that it is read from settings again.
        set_enabled(value)


setting_changed.connect(_follow_the_setting)
