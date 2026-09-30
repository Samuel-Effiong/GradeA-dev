"""FR-A-06: the reason-code catalogue and its error envelope (08a §1, §4.1-4.2).

`audit.enums.ReasonCode` is the vocabulary. This module is the user-facing
layer on top of it:

* `REASON_CODES`: one `ReasonSpec` per user-facing code, with its error class,
  HTTP status, display message, remediation and whether it is retryable
  as-is. `AUDIT_ONLY_CODES` are the sign-in and session outcomes, recorded in
  the audit trail but answered by the auth views' own responses. A test ties
  both lists to the enum.
* `CodedError`: the one base type for a coded failure. Existing exceptions
  are re-parented onto it slice by slice (S6b-S6d) rather than replaced, so
  their `except` clauses keep working.
* `coded_response(error)`: the one builder of a coded error body. It also
  answers the two existing refusals (credits, plan), which
  `billing.refusals.refusal_response` now delegates here.

The body, which the renderer wraps in `error.field_errors` (F7):

    {"error": <display message>,         # the one sentence the renderer shows
     "reason_code": "FILE_TOO_LARGE",    # UPPER_SNAKE, stable
     "error_class": "USER",
     "remediation": <what to do next>,
     "retryable": false,
     "params": {<whitelisted scalars>},
     "reference": <the server's X-Request-ID>,    # QA-ERR-04; never an inbound id (X-5)
     "code": "insufficient_credits"}      # legacy, the two refusals only (F8)

QA-ERR-03: the message comes from the spec template and whitelisted scalar
params only. An exception's own text (`detail`) stays server-side.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field

from rest_framework import status
from rest_framework.response import Response

from audit.enums import ErrorClass, ReasonCode
from AutoGrader.request_context import get_request_id
from billing.errors import INSUFFICIENT_CREDITS_MESSAGE

#: The keys a coded body adds next to "error". The renderer hides them from
#: the display message (users/renderers.py).
ENVELOPE_KEYS = (
    "reason_code",
    "error_class",
    "remediation",
    "retryable",
    "params",
    "reference",
    "code",
)

ACCEPTED_FILE_TYPES = "PDF, JPEG, PNG, GIF, WebP"


@dataclass(frozen=True)
class ReasonSpec:
    error_class: ErrorClass
    http_status: int
    message: str
    remediation: str
    retryable: bool
    #: Params a raiser may pass. Anything else is refused (QA-ERR-03).
    params: frozenset[str] = field(default_factory=frozenset)
    #: Values used when a raiser does not pass that param.
    defaults: dict = field(default_factory=dict)
    #: Seconds for a `Retry-After` header, when the status asks for one.
    retry_after: int | None = None

    def placeholders(self):
        return {
            name
            for _, name, _, _ in string.Formatter().parse(self.message)
            if name is not None
        }

    def render(self, params, display=None):
        return self.message.format(**{**self.defaults, **params, **(display or {})})


_FILE = frozenset({"file_name"})

REASON_CODES: dict[ReasonCode, ReasonSpec] = {
    ReasonCode.MISSING_STUDENT_NAME: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "We couldn't match {file_name} to a student: {name_state}.",
        "Choose the student this paper belongs to, or add them to the course.",
        retryable=False,
        params=_FILE | {"name_state"},
    ),
    ReasonCode.STUDENT_NOT_ON_ROSTER: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "{file_name} belongs to {student_display}, who isn't enrolled in this "
        "course.",
        "Add the student to the course roster, then retry this paper.",
        retryable=False,
        params=_FILE | {"student_display"},
    ),
    ReasonCode.FILE_UNREADABLE: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "We couldn't read {file_name}. It may be damaged, password-protected "
        "or incomplete.",
        "Re-export or re-scan the file and upload it again.",
        retryable=False,
        params=_FILE,
    ),
    ReasonCode.FILE_TYPE_UNSUPPORTED: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        "{file_name} is a {detected_type} file, which isn't supported. "
        "Accepted types: {accepted_types}.",
        "Save or export the file as a PDF or an image, and upload it again.",
        retryable=False,
        params=_FILE | {"detected_type", "accepted_types"},
        defaults={"accepted_types": ACCEPTED_FILE_TYPES},
    ),
    ReasonCode.FILE_TOO_LARGE: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        "{file_name} is {actual} and the limit is {limit}.",
        "Split the file, remove blank pages or scan at a lower resolution, "
        "then upload again.",
        retryable=False,
        # The client contract (SM ruling on S6b N1, 08a §4.2):
        #   dimension: always present, one of "bytes" | "pages" | "pixels";
        #   limit:     always present, an int in that unit (bytes as integer
        #              bytes, pixels as a pixel count);
        #   actual:    OPTIONAL, an int in that unit when the size is known;
        #              absent when it is genuinely unknown (Pillow refusing a
        #              decompression bomb before it reports dimensions), never
        #              a made-up number.
        # The message shows them formatted, through `display` ("63.2 MB").
        params=_FILE | {"actual", "limit", "dimension"},
    ),
    ReasonCode.SUBMISSION_EMPTY: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "{file_name} has no student answers to grade.",
        "Check that the right file was uploaded, then upload it again or mark "
        "the submission as missing.",
        retryable=False,
        params=_FILE,
    ),
    ReasonCode.RUBRIC_MISSING: ReasonSpec(
        ErrorClass.VALIDATION,
        status.HTTP_409_CONFLICT,
        "This assignment has no rubric to grade against, so grading hasn't "
        "started. No credits were used.",
        "Add questions and a rubric to the assignment, then grade again.",
        retryable=False,
    ),
    ReasonCode.DUPLICATE_SUBMISSION: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_409_CONFLICT,
        "{file_name} is a second submission for {student_display}. The "
        "existing submission ({existing_ref}) was kept.",
        "Choose which submission to keep.",
        retryable=False,
        params=_FILE | {"student_display", "existing_ref"},
    ),
    ReasonCode.PROVIDER_FAILURE: ReasonSpec(
        ErrorClass.PROVIDER,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "The grading service couldn't finish this item. {credit_clause}",
        "Try again in a few minutes. If it keeps happening, contact support "
        "and quote the reference.",
        retryable=True,
        # F1: "The credits were refunded." whenever anything was charged.
        params=frozenset({"credit_clause"}),
        defaults={"credit_clause": "No credits were charged."},
        retry_after=30,
    ),
    ReasonCode.INSUFFICIENT_CREDITS_MID_BATCH: ReasonSpec(
        ErrorClass.USER,
        # Item-level only today (the batch already answered 202); 402 if it
        # is ever answered synchronously.
        status.HTTP_402_PAYMENT_REQUIRED,
        "Credits ran out after {completed} of {total} items. The finished "
        "items are saved.",
        # F4: uploads are not stored, so unfinished uploads are re-uploaded.
        "Top up credits, then resume the remaining items. Unfinished uploads "
        "need their files uploaded again.",
        retryable=True,
        params=frozenset({"completed", "total"}),
    ),
    ReasonCode.INSUFFICIENT_CREDITS: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_402_PAYMENT_REQUIRED,
        # Fixed for every InsufficientCreditsError: the exception's own text
        # can carry balance or deficit detail (billing/errors.py).
        INSUFFICIENT_CREDITS_MESSAGE,
        "Top up credits, or ask your school administrator, then try again.",
        retryable=False,
    ),
    ReasonCode.AI_FEATURE_NOT_AVAILABLE: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_403_FORBIDDEN,
        # Shown when the refusal carries no text of its own; see
        # _refusal_reason.
        "This feature isn't included in your plan.",
        "Upgrade your plan, or ask your school administrator to enable it.",
        retryable=False,
    ),
    ReasonCode.NOT_RETRYABLE: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_409_CONFLICT,
        "This item can't be retried as it is.",
        "Fix what its failure message describes first, then try again.",
        retryable=False,
    ),
    # The three sign-in locks (v2's S6a N3, SM ruling). PENDING QA CATALOGUE
    # APPROVAL (staging only until QA agrees). Their responses keep every
    # field the auth docs promise; the envelope is ADDED beside them
    # (`add_coded_envelope`), and each keeps its own display text - these
    # messages are what a raised CodedError would show.
    ReasonCode.RESET_LOCKED: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Password reset is paused on this account for a while, because the "
        "code was entered incorrectly too many times.",
        "Wait until the time shown, then request a new code. Your password "
        "has not been changed.",
        retryable=True,
    ),
    ReasonCode.VERIFY_LOCKED: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Too many incorrect codes for this email address.",
        "Wait, then request a new verification email.",
        retryable=True,
    ),
    ReasonCode.ACCOUNT_LOCKED: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_401_UNAUTHORIZED,
        "Too many failed login attempts. Please try again later.",
        "Wait a few minutes and try again, or reset your password.",
        retryable=True,
    ),
}

#: Recorded in the audit trail only; the auth views shape their responses.
AUDIT_ONLY_CODES = frozenset(
    {
        ReasonCode.ACCOUNT_DEACTIVATED,
        ReasonCode.WRONG_PASSWORD,
        ReasonCode.INVALID_CREDENTIALS,
        ReasonCode.INVALID_CODE,
        ReasonCode.CODE_EXPIRED,
        ReasonCode.CODE_MISSING,
        ReasonCode.CODE_NOT_REQUESTED,
        ReasonCode.REFRESH_TOKEN_MISSING,
        ReasonCode.REFRESH_TOKEN_INVALID,
        ReasonCode.SESSION_REVOKE_FAILED,
        ReasonCode.GOOGLE_SIGN_IN_REFUSED,
        ReasonCode.GOOGLE_CODE_MISSING,
        ReasonCode.GOOGLE_EXCHANGE_FAILED,
        ReasonCode.GOOGLE_TOKEN_INVALID,
        ReasonCode.GOOGLE_EMAIL_UNVERIFIED,
        ReasonCode.INVALID_REQUEST,
        ReasonCode.SERVER_ERROR,
        ReasonCode.FAILED_AUTH_CAPPED,
    }
)

#: F8: the lowercase codes clients already read, kept for one release beside
#: `reason_code`. Remove in the release after S6 ships.
LEGACY_CODES = {
    ReasonCode.INSUFFICIENT_CREDITS: "insufficient_credits",
    ReasonCode.AI_FEATURE_NOT_AVAILABLE: "ai_feature_not_available",
}

_SCALARS = (str, int, float, bool)


class CodedError(Exception):
    """A failure with a stable reason code.

    `str(error)` is the display message, rendered from the spec and the
    whitelisted scalar `params`. `display` optionally says how a placeholder
    reads in the message when its param is machine-readable (an int byte
    count shown as "63.2 MB"): message only, never in the body's `params`.
    `detail` is for logs only and never reaches a response. A subclass fixes
    its code with the `reason_code` attribute.

    It survives being serialized, as Celery does to a task's failure: its
    `args` are `(reason_code, params, None, display)`, which is exactly what
    the constructor takes, so both `cls(*args)` (Celery's json result
    backend, production's serializer) and pickle's default reduce rebuild it
    with the same code, params and message. `detail` is deliberately left
    out of `args`: it is for logs, not for a result backend. Pickle keeps it
    (it restores `__dict__`); json does not.
    """

    reason_code: ReasonCode | None = None

    def __init__(self, reason_code=None, params=None, detail=None, display=None):
        code = reason_code or type(self).reason_code
        if code is None:
            raise TypeError("CodedError needs a reason_code")
        code = ReasonCode(code)
        if code not in REASON_CODES:
            raise ValueError(f"{code} is audit-only and has no error body")
        spec = REASON_CODES[code]
        params = dict(params or {})
        unknown = set(params) - spec.params
        if unknown:
            raise ValueError(f"{code}: params not allowed: {sorted(unknown)}")
        not_scalar = [k for k, v in params.items() if not isinstance(v, _SCALARS)]
        if not_scalar:
            raise TypeError(f"{code}: params must be scalars: {sorted(not_scalar)}")
        display = dict(display or {})
        stray = set(display) - spec.placeholders()
        if stray:
            raise ValueError(f"{code}: display for no placeholder: {sorted(stray)}")
        not_text = [k for k, v in display.items() if not isinstance(v, str)]
        if not_text:
            raise TypeError(f"{code}: display values must be text: {sorted(not_text)}")
        missing = spec.placeholders() - set(params) - set(spec.defaults) - set(display)
        if missing:
            raise ValueError(f"{code}: params missing: {sorted(missing)}")
        self.reason_code = code
        self.params = params
        self.detail = detail
        self._spec = spec
        self._message = spec.render(params, display)
        super().__init__(self._message)
        # After super().__init__: Exception.__init__ would set args to the
        # message, and a DRF APIException base sets none at all.
        self.args = (code.value, params, None, display or None)

    def __str__(self):
        return self._message

    @property
    def spec(self):
        return self._spec

    @property
    def message(self):
        return str(self)


def _refusal_reason(error):
    """(code, message) for the two refusals that predate CodedError, or
    None. Their shown message is unchanged (describe_user_error)."""
    from AutoGrader.error_messages import describe_user_error
    from billing.access_control import AIFeatureNotAvailableError
    from billing.errors import InsufficientCreditsError

    if isinstance(error, InsufficientCreditsError):
        return ReasonCode.INSUFFICIENT_CREDITS, describe_user_error(error)
    if isinstance(error, AIFeatureNotAvailableError):
        code = ReasonCode.AI_FEATURE_NOT_AVAILABLE
        return code, describe_user_error(
            error, fallback_message=REASON_CODES[code].message
        )
    return None


def reason_of(error):
    """(code, params, message) for a coded failure, or None."""
    if isinstance(error, CodedError):
        return error.reason_code, dict(error.params), error.message
    refusal = _refusal_reason(error)
    if refusal is not None:
        code, message = refusal
        return code, {}, message
    return None


def coded_body(code, params, message):
    """The envelope for `code`, as the view payload."""
    code = ReasonCode(code)
    spec = REASON_CODES[code]
    body = {
        "error": message,
        "reason_code": code.value,
        "error_class": spec.error_class.value,
        "remediation": spec.remediation,
        "retryable": spec.retryable,
        "params": params,
        "reference": get_request_id(),
    }
    if code in LEGACY_CODES:
        body["code"] = LEGACY_CODES[code]
    return body


def add_coded_envelope(data, code, message, *, code_value=None):
    """ADD the coded envelope to a body a view or DRF already built (the F8
    pattern): every key the body has - the fields its docs promise - stays
    exactly as it was. `code_value` sets `code` only if the body has none.

    `error` (the envelope's display sentence) is NOT added: these bodies
    already carry their text as `message` or `detail`, and a second text key
    would change what the renderer shows."""
    body = coded_body(code, {}, message)
    for key in ENVELOPE_KEYS:
        if key in body and key not in data:
            data[key] = body[key]
    if code_value is not None and "code" not in data:
        data["code"] = code_value
    return data


def coded_response(error):
    """The HTTP answer to a coded failure: its spec's status and the
    envelope. None for anything else, so a caller can fall through."""
    reason = reason_of(error)
    if reason is None:
        return None
    code, params, message = reason
    spec = REASON_CODES[code]
    response = Response(coded_body(code, params, message), status=spec.http_status)
    if spec.retry_after is not None:
        response["Retry-After"] = str(spec.retry_after)
    return response
