"""Epic B1b-7 D7-e: `ai_job_id` on the in-request AI views (08b §1.6, plan
§9 item 9; NOTES §7.8-§7.9, ruling §7.19).

A request records the jobs it settled in a contextvar: a root metered
unit's job when the unit settles, and a single call's job when it settles
(billing.holds). FAILED attempts return their hold and settle nothing, so
they are never recorded.

`ai_job_id` is the id of the ONE job the request settled. Several settled
jobs (chunked extraction) or none give null: a partial cost must not read
as the whole one. The key is always present on a success body.
"""

import contextlib
import contextvars
import functools

from rest_framework import serializers

_settled: contextvars.ContextVar = contextvars.ContextVar(
    "billing_settled_jobs", default=None
)


@contextlib.contextmanager
def tracking():
    """Record the jobs settled inside the block; yields their list."""
    jobs: list = []
    token = _settled.set(jobs)
    try:
        yield jobs
    finally:
        _settled.reset(token)


def note_settled(job_pk):
    """Called by billing.holds when a job settles."""
    jobs = _settled.get()
    if jobs is not None and job_pk not in jobs:
        jobs.append(job_pk)


def settled_job_id(jobs):
    return str(jobs[0]) if len(jobs) == 1 else None


def with_ai_job_id(view):
    """A view (or view helper) whose 2xx JSON-object body gains `ai_job_id`."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        with tracking() as jobs:
            response = view(*args, **kwargs)
        data = getattr(response, "data", None)
        if 200 <= response.status_code < 300 and isinstance(data, dict):
            data["ai_job_id"] = settled_job_id(jobs)
        return response

    return wrapped


_schemas: dict = {}


def ai_job_schema(serializer_class):
    """`serializer_class` plus a nullable `ai_job_id`, for OpenAPI only."""
    if serializer_class not in _schemas:
        attrs: dict[str, object] = {
            "ai_job_id": serializers.UUIDField(
                read_only=True,
                allow_null=True,
                help_text="The AI job this request settled (null if none, or "
                "more than one).",
            )
        }
        meta = getattr(serializer_class, "Meta", None)
        fields = getattr(meta, "fields", None)
        if meta is not None and isinstance(fields, (list, tuple)):
            attrs["Meta"] = type("Meta", (meta,), {"fields": [*fields, "ai_job_id"]})
        _schemas[serializer_class] = type(
            f"{serializer_class.__name__}WithAIJob", (serializer_class,), attrs
        )
    return _schemas[serializer_class]
