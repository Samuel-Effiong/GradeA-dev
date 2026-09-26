"""Sentry `before_send` PII scrubbing (FR-A-04 / NFR-CMP-02, plan §0.5a item 3).

`send_default_pii=False` (see settings.py) only suppresses Sentry's
*automatic* user/request context. It does nothing about the string content
of a log message: `LoggingIntegration(event_level="ERROR")` turns every
`logger.error`/`.exception` call into a Sentry event, so a leaked email or
name in a log message still ships as event text regardless of that flag.

This is defense-in-depth, not a substitute for fixing call sites: a lint
rule (see scripts/check_no_pii_in_logs.py) only catches patterns it is told
to look for, and a raw `f"{exc}"` on some future exception is not something
static analysis can catch. This hook scrubs known-shaped PII (currently:
email addresses) out of the two places that content actually lands in a
Sentry event, immediately before transmission.
"""

import re

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_REDACTED = "[redacted-email]"


def _scrub_text(value: str) -> str:
    return _EMAIL_RE.sub(_REDACTED, value)


def _scrub_logentry(logentry: dict) -> None:
    message = logentry.get("message")
    if isinstance(message, str):
        logentry["message"] = _scrub_text(message)

    formatted = logentry.get("formatted")
    if isinstance(formatted, str):
        logentry["formatted"] = _scrub_text(formatted)

    # `params` holds the raw %-style substitution arguments for the log
    # record (e.g. the second positional arg to `logger.error("... %s", x)`).
    # Sentry renders `message` from these too, so an unscrubbed param can
    # reintroduce what `message`/`formatted` just had removed.
    params = logentry.get("params")
    if isinstance(params, list):
        logentry["params"] = [
            _scrub_text(p) if isinstance(p, str) else p for p in params
        ]


def _scrub_exception(exception: dict) -> None:
    for value in exception.get("values", []) or []:
        exc_value = value.get("value")
        if isinstance(exc_value, str):
            value["value"] = _scrub_text(exc_value)

        for frame in (value.get("stacktrace") or {}).get("frames", []) or []:
            frame_vars = frame.get("vars")
            if isinstance(frame_vars, dict):
                for key, val in list(frame_vars.items()):
                    if isinstance(val, str):
                        frame_vars[key] = _scrub_text(val)


def scrub_pii_before_send(event: dict, hint: dict) -> dict:
    """`before_send` hook: scrub email-shaped strings from an outgoing event.

    Never raises — a bug in this function must not block a legitimate error
    report (same "logging failure never fails the caller" posture as
    FR-A-11's audit emitter).
    """
    try:
        logentry = event.get("logentry")
        if isinstance(logentry, dict):
            _scrub_logentry(logentry)

        exception = event.get("exception")
        if isinstance(exception, dict):
            _scrub_exception(exception)
    except Exception:  # pragma: no cover - defensive, see docstring
        pass

    return event
