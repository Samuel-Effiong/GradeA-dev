"""
H-89: no email address, and no URL password, in the text of what is sent
to Sentry. H-167: nor a value written as `password=...` (log_scrubbing's
third pattern), and the event's request, tags, user and contexts are
scrubbed like the rest.

Sentry's logging integration is not a logging handler. For each ERROR
record it builds an event from the record's parts (the message template,
the raw arguments, the exception object's own text and the frames' local
variables), so the log record factory in log_scrubbing.py, which scrubs
what handlers print, does not change what Sentry receives. These three
hooks do; settings pass them to sentry_sdk.init:

  * scrub_event (before_send, and before_send_transaction: a sampled
    transaction is an event that does not pass before_send): the log
    entry, the plain message, the exception values and the threads with
    their frame variables, the spans, the breadcrumbs and the extras;
  * scrub_breadcrumb (before_breadcrumb): each breadcrumb as it is recorded;
  * scrub_log (before_send_log): each item of the log stream.

They touch text only. Since H-167 the event's request (its URL, query
string, headers and data), tags, user and contexts pass the same scrub, as
a second defence: settings turn frame variables and request bodies off
(include_local_variables=False, max_request_body_size="never") and
send_default_pii=False keeps Sentry from adding a user's address, but
these hooks do not rely on it. H-89 had left those four parts alone. A
name or free text in a query string is recognised by no pattern.

A hook never raises and never drops the event. A part whose text cannot be
scrubbed is replaced by WITHHELD (fail closed).
"""

from AutoGrader.log_scrubbing import scrub

WITHHELD = "[withheld: this text could not be scrubbed]"

#: The parts of an event that carry free text.
_EVENT_TEXT_PARTS = (
    "logentry",
    "message",
    "exception",
    "threads",
    "spans",
    "breadcrumbs",
    "extra",
    # H-167
    "request",
    "tags",
    "user",
    "contexts",
)


def _scrubbed(value):
    """`value` with every string in it scrubbed. Numbers, booleans and None
    are kept; any other object is replaced by its scrubbed text (an
    argument Sentry has not turned into text yet)."""
    if isinstance(value, str):
        return scrub(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {key: _scrubbed(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrubbed(item) for item in value]
    return scrub(str(value))


def _scrub_parts(container, parts):
    for part in parts:
        if container.get(part) is None:
            continue
        try:
            container[part] = _scrubbed(container[part])
        except Exception:  # noqa: BLE001 - never into Sentry's caller
            container[part] = WITHHELD
    return container


def scrub_event(event, hint):
    """sentry_sdk's before_send and before_send_transaction."""
    return _scrub_parts(event, _EVENT_TEXT_PARTS)


def scrub_breadcrumb(crumb, hint):
    """sentry_sdk's before_breadcrumb."""
    return _scrub_parts(crumb, ("message", "data"))


def scrub_log(log, hint):
    """sentry_sdk's before_send_log."""
    return _scrub_parts(log, ("body", "attributes"))
