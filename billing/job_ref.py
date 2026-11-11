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


def _is_student(args):
    """Whether the request among a view's arguments is a student's."""
    from users.models import UserTypes

    for arg in args:
        user = getattr(arg, "user", None)
        if user is not None and hasattr(arg, "method"):
            return getattr(user, "user_type", None) == UserTypes.STUDENT
    return False


def with_ai_job_id(view=None, *, staff_only=False):
    """A view (or view helper) whose 2xx JSON-object body gains `ai_job_id`.

    staff_only (§7.25): a student never sees a billing job's id, so on a
    route open to a student and to staff the key is left out of a
    student's body (the job is billed to the teacher; the job API answers
    a student 404). Used bare or as @with_ai_job_id(staff_only=True)."""

    def decorate(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            with tracking() as jobs:
                response = view(*args, **kwargs)
            data = getattr(response, "data", None)
            if (
                200 <= response.status_code < 300
                and isinstance(data, dict)
                and not (staff_only and _is_student(args))
            ):
                data["ai_job_id"] = settled_job_id(jobs)
            return response

        return wrapped

    return decorate if view is None else decorate(view)


_schemas: dict = {}


def ai_job_schema(serializer_class, *, staff_only=False):
    """`serializer_class` plus a nullable `ai_job_id`, for OpenAPI only."""
    key = (serializer_class, staff_only)
    if key not in _schemas:
        help_text = "The AI job this request settled (null if none, or more than one)."
        if staff_only:
            help_text += " Left out when a student makes the request."
        attrs: dict[str, object] = {
            "ai_job_id": serializers.UUIDField(
                read_only=True, allow_null=True, help_text=help_text
            )
        }
        meta = getattr(serializer_class, "Meta", None)
        fields = getattr(meta, "fields", None)
        if meta is not None and isinstance(fields, (list, tuple)):
            attrs["Meta"] = type("Meta", (meta,), {"fields": [*fields, "ai_job_id"]})
        suffix = "WithStaffAIJob" if staff_only else "WithAIJob"
        _schemas[key] = type(
            f"{serializer_class.__name__}{suffix}", (serializer_class,), attrs
        )
    return _schemas[key]
