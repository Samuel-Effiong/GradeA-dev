"""The per-call usage record (estimate step 1, H-201).

Every AI call through `AIProcessor.execute_graded_task` leaves one row in
`AICallRecord` AFTER the call and its charge are finished: the estimate the
gate used, the token counts the reply carried, the model that answered, the
provider's reported cost if there is one, counts of what was sent, and what
became of the charge. Calls that were NOT charged (refused, failed,
cancelled, super admin) are recorded too. Ids and numbers only: no prompt,
student or answer text, no error message.

HOW IT HOOKS IN (the seam exception, five lines in execute_graded_task):
`@recorded_call` on the function, and four one-line notes that hand over what
only the function knows (`note_billed`, `note_estimate`, `note_task_id`,
`note_response`). The notes go into a per-call state held in a context
variable; with the switch off, or outside a decorated call, they do nothing.
No other line of `execute_graded_task` is touched, and neither call to
`__ai_model` (those lines differ between the beta line and the epic line).

THE RULES, each held by a test:
* the row is written after the function has returned or raised, so never
  inside the charge's transaction, and in a savepoint of its own, so a caller
  that is itself inside a transaction is not poisoned by a failed insert;
* one INSERT and no read, no lock; the estimate is the value the gate used,
  never computed again;
* a failure of the write or of the reading of the reply is swallowed, counted
  (`failure_count`) and logged with the call id and the exception CLASS only;
  on a call that raised, a recorder error never replaces the original
  exception, which is re-raised as it was (same object, same traceback);
* `SoftTimeLimitExceeded` raised in the recorder after a call that returned
  is NOT swallowed (it is the worker's decision to stop; the charge is
  already committed); `SystemExit` and `KeyboardInterrupt` never are;
* the switch `AI_CALL_RECORD_ENABLED` (default on) turns all of it off.
"""

import contextvars
import functools
import inspect
import logging
import math
import time
import uuid
from decimal import Decimal, InvalidOperation

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import transaction

from ai_processor.models import (
    AICallRecord,
    CallErrorClass,
    CallFeature,
    CallFinishReason,
    CallKind,
    CallOutcome,
    CallTaskType,
)

logger = logging.getLogger(__name__)

#: task type -> kind, only where EVERY call site of that task type agrees.
#: grade_assignment (single call, batch of a larger paper, summary) and
#: extract_answer (single, part of a long paper, blank re-check) are not here:
#: their kind is NULL. A static test lists every call site.
KIND_BY_TASK_TYPE = {
    "extract_assignment": CallKind.ASSIGNMENT_EXTRACTION.value,
    "generate_assignment": CallKind.ASSIGNMENT_GENERATION.value,
    "formatted_grade": CallKind.FORMATTING.value,
    "student_summary": CallKind.STUDENT_SUMMARY.value,
    "weekly_course_summary": CallKind.WEEKLY_COURSE_SUMMARY.value,
    "weekly_school_admin_summary": CallKind.WEEKLY_SCHOOL_ADMIN_SUMMARY.value,
}

_TASK_TYPES = {c.value for c in CallTaskType}
_FEATURES = {c.value for c in CallFeature}
_FINISH_REASONS = {c.value for c in CallFinishReason}
_BIGINT_MAX = 2**63 - 1
_COST_MAX = Decimal("999999.99999999")

#: exception class name -> closed error class, looked up along the MRO
_ERROR_CLASSES = {
    "TimeoutError": CallErrorClass.TIMEOUT,
    "APITimeoutError": CallErrorClass.TIMEOUT,
    "Timeout": CallErrorClass.TIMEOUT,
    "ReadTimeout": CallErrorClass.TIMEOUT,
    "RateLimitError": CallErrorClass.RATE_LIMIT,
    "APIConnectionError": CallErrorClass.CONNECTION,
    "ConnectionError": CallErrorClass.CONNECTION,
    "APIStatusError": CallErrorClass.API_STATUS,
    "TaskCancelledError": CallErrorClass.CANCELLED,
    "InsufficientCreditsError": CallErrorClass.INSUFFICIENT_CREDITS,
    "AIFeatureNotAvailableError": CallErrorClass.FEATURE_NOT_AVAILABLE,
    "ValueError": CallErrorClass.VALUE,
}

_CURRENT: contextvars.ContextVar["_State | None"] = contextvars.ContextVar(
    "ai_call_record_state", default=None
)
_failures = 0


class _State:
    __slots__ = ("estimate", "task_id", "response", "billed_user_id", "school_id")

    def __init__(self):
        self.estimate = None
        self.task_id = None
        self.response = None
        self.billed_user_id = None
        self.school_id = None


# -- the four notes -----------------------------------------------------------


def current_state():
    return _CURRENT.get()


def note_billed(target_teacher):
    state = _CURRENT.get()
    if state is not None:
        state.billed_user_id = getattr(target_teacher, "id", None)
        state.school_id = getattr(target_teacher, "school_id", None)


def note_estimate(estimate):
    state = _CURRENT.get()
    if state is not None:
        state.estimate = estimate


def note_task_id(task_id):
    state = _CURRENT.get()
    if state is not None:
        state.task_id = task_id


def note_response(response):
    state = _CURRENT.get()
    if state is not None:
        state.response = response


# -- the counter ----------------------------------------------------------------


def failure_count():
    return _failures


def reset_failure_count():
    global _failures
    _failures = 0


def _count_failure(error, state):
    global _failures
    _failures += 1
    logger.warning(
        "ai_call_record_failed call=%s error_class=%s",
        getattr(state, "task_id", None) or "none",
        type(error).__name__,
    )


# -- reading the reply ----------------------------------------------------------


def _count(value):
    """A non-negative integer that fits the column, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    number = int(value)
    return number if 0 <= number <= _BIGINT_MAX else None


def _cost(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0 or number > _COST_MAX:
        return None
    return number.quantize(Decimal("0.00000001"))


def _model_name(value):
    return value[:255] if isinstance(value, str) and value else None


def _provider_name(value):
    """The upstream provider's name as the reply gave it, capped; None for anything but a non-empty string."""
    return value[:100] if isinstance(value, str) and value.strip() else None


def usage_numbers(response):
    """What the reply carries, as plain values; anything absent, odd or
    out of range is None. Reads attributes only."""
    usage = getattr(response, "usage", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    choices = getattr(response, "choices", None)
    finish = None
    try:
        finish = getattr(choices[0], "finish_reason", None) if choices else None
    except (TypeError, IndexError, KeyError):
        finish = None
    if isinstance(finish, str) and finish:
        finish = finish if finish in _FINISH_REASONS else CallFinishReason.OTHER.value
    else:
        finish = None
    return {
        "total_tokens": _count(getattr(usage, "total_tokens", None)),
        "prompt_tokens": _count(getattr(usage, "prompt_tokens", None)),
        "completion_tokens": _count(getattr(usage, "completion_tokens", None)),
        "reasoning_tokens": _count(
            getattr(completion_details, "reasoning_tokens", None)
        ),
        "cached_tokens": _count(getattr(prompt_details, "cached_tokens", None)),
        "cost_usd": _cost(getattr(usage, "cost", None)),
        "finish_reason": finish,
        "model_answered": _model_name(getattr(response, "model", None)),
        "upstream_provider": _provider_name(getattr(response, "provider", None)),
    }


def _error_class(error):
    if error is None:
        return None
    for cls in type(error).__mro__:
        found = _ERROR_CLASSES.get(cls.__name__)
        if found is not None:
            return found.value
    return CallErrorClass.OTHER.value


def _outcome(state, result, error):
    if error is None:
        return CallOutcome.CHARGED if state.task_id else CallOutcome.UNMETERED
    name = type(error).__name__
    if state.response is not None:
        if name == "TaskCancelledError":
            return CallOutcome.CANCELLED_AFTER_REPLY
        if name == "InsufficientCreditsError":
            return CallOutcome.BALANCE_CHANGED
        return CallOutcome.CHARGE_FAILED
    if state.task_id:
        return CallOutcome.PROVIDER_ERROR
    if state.estimate is not None:
        if name == "InsufficientCreditsError":
            return CallOutcome.REFUSED_GATE
        return CallOutcome.OTHER_ERROR
    if name in ("AIFeatureNotAvailableError", "InsufficientCreditsError"):
        return CallOutcome.REFUSED_ACCESS
    return CallOutcome.OTHER_ERROR


def _media_counts(arguments):
    images = pdfs = 0
    parts = []
    for key in ("user_prompt", "system_prompt"):
        value = arguments.get(key)
        if isinstance(value, list):
            parts.extend(value)
    for message in arguments.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            parts.extend(content)
    for part in parts:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "image_url":
            images += 1
        elif kind == "pdf_url":
            pdfs += 1
    return images, pdfs


def _uuid_or_none(value):
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _fields(arguments, state, result, error, started):
    from ai_processor import services

    outcome = _outcome(state, result, error)
    response = state.response if state.response is not None else result
    numbers = usage_numbers(response) if response is not None else usage_numbers(None)
    task_type = arguments.get("task_type")
    task_type = task_type if task_type in _TASK_TYPES else CallTaskType.OTHER.value
    feature = arguments.get("feature")
    feature = feature if feature in _FEATURES else CallFeature.OTHER.value
    images, pdfs = _media_counts(arguments)
    assignment = arguments.get("assignment")
    questions = getattr(assignment, "questions", None)
    user = arguments.get("user")
    billed = state.billed_user_id
    if billed is None and getattr(user, "user_type", None) != "STUDENT":
        billed = getattr(user, "id", None)
    return {
        "task_id": str(state.task_id) if state.task_id else None,
        "task_type": task_type,
        "feature": feature,
        "kind": KIND_BY_TASK_TYPE.get(task_type),
        "outcome": outcome.value,
        "error_class": _error_class(error),
        "model_asked": (arguments.get("override_model") or services.MAIN_MODEL)[:255],
        "prompt_version": str(arguments.get("prompt_version") or "unknown")[:128],
        "estimate": _count(state.estimate),
        "images": images,
        "pdf_items": pdfs,
        "questions": len(questions) if isinstance(questions, list) else None,
        "charged_credits": (
            numbers["total_tokens"] or 0 if outcome == CallOutcome.CHARGED else 0
        ),
        "billed_user_id": _uuid_or_none(billed),
        "school_id": _uuid_or_none(state.school_id),
        "assignment_id": _uuid_or_none(getattr(assignment, "id", None)),
        "processing_task_id": _uuid_or_none(arguments.get("processing_task_id")),
        "duration_ms": min(int((time.monotonic() - started) * 1000), 2**31 - 1),
        **numbers,
    }


# -- the write ----------------------------------------------------------------------


def write_row(**fields):
    """One INSERT in a savepoint of its own (or its own short transaction when
    the caller has none). A failure rolls back only this."""
    with transaction.atomic():
        AICallRecord.objects.create(**fields)


def enabled():
    return bool(getattr(settings, "AI_CALL_RECORD_ENABLED", True))


def recorded_call(function):
    """Decorator for AIProcessor.execute_graded_task."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        if not enabled():
            return function(*args, **kwargs)
        state = _State()
        token = _CURRENT.set(state)
        started = time.monotonic()

        def record(result, error):
            arguments = signature.bind_partial(*args, **kwargs).arguments
            write_row(**_fields(arguments, state, result, error, started))

        try:
            try:
                result = function(*args, **kwargs)
            except Exception as error:
                # The original exception goes on exactly as it was; nothing
                # the recorder does may replace it.
                try:
                    record(None, error)
                except Exception as recorder_error:
                    _count_failure(recorder_error, state)
                raise
            try:
                record(result, None)
            except SoftTimeLimitExceeded:
                raise
            except Exception as recorder_error:
                _count_failure(recorder_error, state)
            return result
        finally:
            _CURRENT.reset(token)

    return wrapper
