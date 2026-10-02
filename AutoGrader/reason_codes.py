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
    #: Other approved remediations a raiser may choose instead (one code,
    #: two routes: TEACHER_LIST_EMPTY on add and on remove). An empty
    #: `remediation` means "nothing to do" and reaches the body as null.
    alternative_remediations: tuple[str, ...] = ()
    #: Other approved message templates, by name, that a raiser picks with
    #: `CodedError(variant=)` (INSUFFICIENT_CREDITS_MID_BATCH when nothing
    #: had finished yet). Each takes its placeholders from `params`.
    alternative_messages: dict = field(default_factory=dict)

    def template(self, variant=None):
        return self.message if variant is None else self.alternative_messages[variant]

    def placeholders(self, variant=None):
        return {
            name
            for _, name, _, _ in string.Formatter().parse(self.template(variant))
            if name is not None
        }

    def render(self, params, display=None, variant=None):
        return self.template(variant).format(
            **{**self.defaults, **params, **(display or {})}
        )


_FILE = frozenset({"file_name"})

#: The one place TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION's text lives. QA kept it
#: knowing it tells a school admin whether a teacher pays for their own
#: subscription; the generic alternative is "This email can't be added as a
#: teacher." (TEACHER_EMAIL_OTHER_ROLE's text). Change it here only.
TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION_MESSAGE = (
    "{email} has their own subscription, which must be cancelled before they "
    "can join the licence."
)


def _item_spec(
    message, remediation, *, params=(), retryable=False, alternative_remediations=()
):
    """A per-item code of a sync batch route (S7d): USER, reported in the
    route's result list, 422 if one is ever answered directly."""
    return ReasonSpec(
        ErrorClass.USER,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        message,
        remediation,
        retryable=retryable,
        params=frozenset(params),
        alternative_remediations=tuple(alternative_remediations),
    )


def _row_spec(
    message, remediation, *, params=(), retryable=False, alternative_remediations=()
):
    """A roster-import row's code: every one carries its `row` number."""
    return _item_spec(
        message,
        remediation,
        params={"row", *params},
        retryable=retryable,
        alternative_remediations=alternative_remediations,
    )


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
        # Approved by QA (the founder), 2026-10-01: when not one item had
        # finished, "after 0 of N" read wrong. Picked by
        # students.task_tracking._mid_batch_error, the one place both S7c
        # raise sites build this error.
        alternative_messages={
            "none_finished": (
                "Credits ran out before any of the {total} items were finished."
            )
        },
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
        # S7b: `why` is a server constant, given through `display` (an upload
        # item: its file isn't kept, F4); the default is the general case.
        "{why}",
        "Fix what its failure message describes first, then try again.",
        retryable=False,
        # resolution: "replace_file" for an upload item (re-upload it).
        params=frozenset({"why", "resolution"}),
        defaults={"why": "This item can't be retried as it is."},
    ),
    # The three sign-in locks (v2's S6a N3, SM ruling). Approved by QA
    # (the founder) on 2026-09-30, catalogue section A. Their responses keep
    # every field the auth docs promise; the envelope is ADDED beside them
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
    # ------------------------------------------------------------------
    # QA catalogue additions, sections B-F (Epic A S7d). Approved AS WRITTEN
    # by the founder acting as QA, 2026-09-30:
    # docs/phase2/qa/catalogue_additions_proposal.md. Every text below is
    # the approved text; change one only through QA.
    #
    # B. Student registration paused (H-68: register_student's budget).
    # The message is today's text; DRF appends "Expected available in N
    # seconds." to the body's `detail` and sets Retry-After itself.
    ReasonCode.REGISTRATION_PAUSED: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Student registration is paused for a short while because of too "
        "many invalid activation codes. Please try again later; if your code "
        "has expired by then, ask for a new one.",
        "Try again in a few minutes. If your code has expired, ask your "
        "teacher for a new one.",
        retryable=True,
    ),
    # C. A photo or scan uploaded as a PDF (it was FILE_UNREADABLE).
    ReasonCode.FILE_NOT_A_PDF: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "{file_name} is not a PDF. If it is a photo or scan, upload it as an "
        "image instead.",
        "Upload the photo or scan as an image (JPEG, PNG, GIF or WebP).",
        retryable=False,
        params=_FILE,
    ),
    # D1. Roster import: the whole request (the import doesn't start).
    ReasonCode.ROSTER_NO_INPUT: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_400_BAD_REQUEST,
        "Upload a roster file or paste your student list.",
        "Choose a CSV file, or paste rows copied from your spreadsheet.",
        retryable=False,
    ),
    ReasonCode.ROSTER_EMPTY: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_400_BAD_REQUEST,
        "This roster has no student rows.",
        "Check that the file has one student per row, then try again.",
        retryable=False,
    ),
    ReasonCode.ROSTER_FILE_UNREADABLE: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_400_BAD_REQUEST,
        "{file_name} isn't readable as text.",
        "Export your roster as a CSV file (UTF-8) and try again.",
        retryable=False,
        params=_FILE,
    ),
    ReasonCode.ROSTER_TOO_MANY_ROWS: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_400_BAD_REQUEST,
        "This roster has {row_count} rows. Upload at most {max_rows} rows at a "
        "time.",
        "Split the roster into smaller files.",
        retryable=False,
        params=frozenset({"row_count", "max_rows"}),
    ),
    # D2. Roster import: one row. Item-level only (the import answers 200);
    # 422 if one is ever answered directly.
    ReasonCode.ROW_NAME_MISSING: _row_spec(
        "Row {row}: a first and a last name are required.",
        "Add the missing name and import the row again.",
    ),
    ReasonCode.ROW_NAME_INVALID: _row_spec(
        "Row {row}: each name needs between 2 and 150 characters.",
        "Correct the name and import the row again.",
    ),
    ReasonCode.ROW_ALREADY_ENROLLED: _row_spec(
        "Row {row}: {student_display} is already in this course.",
        "",
        params={"student_display"},
    ),
    ReasonCode.ROW_NAME_CLASH: _row_spec(
        "Row {row}: a student named {student_display} is already in this course.",
        "Add an email address to tell the two students apart.",
        params={"student_display"},
        # A row that already HAS an email (approved by QA, the founder,
        # 2026-10-01): one course can't hold two students of exactly the
        # same name, email or not.
        alternative_remediations=(
            "Two students in one course can't have exactly the same name, "
            "because papers are matched to students by name. Add a middle "
            "name or initial to tell them apart.",
        ),
    ),
    # Neutral on purpose (H-71): no role, no `account_type` param. Naming
    # the role let a teacher learn who on the platform is staff.
    ReasonCode.ROW_STAFF_EMAIL: _row_spec(
        "Row {row}: this email can't be added as a student.",
        "Use the student's own email address.",
    ),
    # Generic on purpose: never names the other school. The remediation is
    # in the message itself.
    ReasonCode.ROW_OTHER_SCHOOL: _row_spec(
        "Row {row}: this account can't be added to this school. If you "
        "believe this is a mistake, contact your school administrator.",
        "",
    ),
    ReasonCode.ROW_ACCOUNT_DISABLED: _row_spec(
        "Row {row}: this student's account is disabled.",
        "Contact support if they should have access.",
    ),
    ReasonCode.ROW_EMAIL_INVALID: _row_spec(
        'Row {row}: "{email}" isn\'t a valid email address.',
        "Correct the email and import the row again.",
        params={"email"},
    ),
    ReasonCode.ROW_DUPLICATE: _row_spec(
        "Row {row} repeats row {first_row}.",
        "",
        params={"first_row"},
    ),
    ReasonCode.ROW_FAILED: _row_spec(
        "Row {row}: this student couldn't be added.",
        "Check the row and try again. If it keeps failing, contact support "
        "and quote the reference.",
        retryable=True,
    ),
    # E1. Licence teacher management: the whole request.
    ReasonCode.TEACHER_LIST_EMPTY: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_400_BAD_REQUEST,
        "Add at least one teacher.",
        "Enter the teachers' email addresses.",
        retryable=False,
        alternative_remediations=("Choose the teachers to remove.",),
    ),
    ReasonCode.LICENCE_INACTIVE: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_400_BAD_REQUEST,
        "This licence isn't active, so teachers can't be added to it.",
        "Renew the licence, or contact us.",
        retryable=False,
    ),
    # The approved text has two forms: "{remaining} seats left, but you're
    # adding {adding} teachers" and, with none left, "no seats left".
    # `availability` is that clause, given through `display`; the numbers
    # stay machine-readable in `params`.
    ReasonCode.LICENCE_SEATS_EXCEEDED: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_400_BAD_REQUEST,
        "Your licence has {availability} ({in_use} of {max_seats} in use).",
        "Add fewer teachers, remove a teacher, or ask us to add seats.",
        retryable=False,
        params=frozenset(
            {"availability", "remaining", "adding", "in_use", "max_seats"}
        ),
    ),
    # E2. Licence teacher management: one teacher. Item-level only.
    ReasonCode.TEACHER_EMAIL_NOT_BUSINESS: _item_spec(
        "{email} isn't a school or work email address.",
        "Use the teacher's school or work email.",
        params={"email"},
    ),
    # Neutral on purpose: never names the account's role.
    ReasonCode.TEACHER_EMAIL_OTHER_ROLE: _item_spec(
        "This email can't be added as a teacher.",
        "Use the teacher's own account email.",
    ),
    ReasonCode.TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION: _item_spec(
        TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION_MESSAGE,
        "Ask the teacher to cancel their individual subscription, then add "
        "them again.",
        params={"email"},
    ),
    # Generic on purpose: never names the other school.
    ReasonCode.TEACHER_IN_OTHER_SCHOOL: _item_spec(
        "This teacher already belongs to another school.",
        "Contact support if the teacher has moved schools.",
    ),
    ReasonCode.TEACHER_ALREADY_ON_LICENCE: _item_spec(
        "{email} is already on this licence.",
        "",
        params={"email"},
    ),
    ReasonCode.TEACHER_NOT_ON_LICENCE: _item_spec(
        "This teacher isn't an active teacher on this licence.",
        "",
    ),
    ReasonCode.TEACHER_ADD_FAILED: _item_spec(
        "We couldn't add this teacher.",
        "Try again. If it keeps failing, contact support and quote the " "reference.",
        retryable=True,
    ),
    ReasonCode.TEACHER_REMOVE_FAILED: _item_spec(
        "We couldn't remove this teacher.",
        "Try again. If it keeps failing, contact support and quote the " "reference.",
        retryable=True,
    ),
    # F. Publishing grades: the single publish (400) and, per item, the
    # skipped list of publish-all.
    ReasonCode.SUBMISSION_NOT_GRADED: ReasonSpec(
        ErrorClass.USER,
        status.HTTP_400_BAD_REQUEST,
        "This submission hasn't been graded yet, so it can't be published.",
        "Grade it first, then publish.",
        retryable=False,
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
        ReasonCode.COURSE_NOT_REACHABLE,
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
    its code with the `reason_code` attribute. `remediation` optionally picks
    one of the spec's `alternative_remediations` (S7d).

    It survives being serialized, as Celery does to a task's failure: its
    `args` are `(reason_code, params, None, display)`, which is exactly what
    the constructor takes, so both `cls(*args)` (Celery's json result
    backend, production's serializer) and pickle's default reduce rebuild it
    with the same code, params and message. `detail` is deliberately left
    out of `args`: it is for logs, not for a result backend. Pickle keeps it
    (it restores `__dict__`); json does not.
    """

    reason_code: ReasonCode | None = None

    def __init__(
        self,
        reason_code=None,
        params=None,
        detail=None,
        display=None,
        remediation=None,
        variant=None,
    ):
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
        if variant is not None and variant not in spec.alternative_messages:
            raise ValueError(f"{code}: not an approved message variant: {variant!r}")
        display = dict(display or {})
        stray = set(display) - spec.placeholders(variant)
        if stray:
            raise ValueError(f"{code}: display for no placeholder: {sorted(stray)}")
        not_text = [k for k, v in display.items() if not isinstance(v, str)]
        if not_text:
            raise TypeError(f"{code}: display values must be text: {sorted(not_text)}")
        missing = (
            spec.placeholders(variant) - set(params) - set(spec.defaults) - set(display)
        )
        if missing:
            raise ValueError(f"{code}: params missing: {sorted(missing)}")
        if remediation is not None and remediation not in (
            spec.alternative_remediations
        ):
            raise ValueError(f"{code}: not an approved remediation: {remediation!r}")
        self.reason_code = code
        self.params = params
        self.detail = detail
        self._spec = spec
        self._message = spec.render(params, display, variant)
        self._remediation = remediation
        self._variant = variant
        super().__init__(self._message)
        # After super().__init__: Exception.__init__ would set args to the
        # message, and a DRF APIException base sets none at all. A chosen
        # remediation (5th) and message variant (6th) ride only when there
        # is one, so every existing error keeps its 4-tuple.
        extra: tuple = ()
        if variant is not None:
            extra = (remediation, variant)
        elif remediation is not None:
            extra = (remediation,)
        self.args = (code.value, params, None, display or None) + extra

    def __str__(self):
        return self._message

    @property
    def spec(self):
        return self._spec

    @property
    def message(self):
        return str(self)

    @property
    def remediation(self):
        """What to do next: the chosen alternative, else the spec's (None
        when the spec has nothing to suggest)."""
        if self._remediation is not None:
            return self._remediation
        return self._spec.remediation or None


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


def coded_body(code, params, message, remediation=None):
    """The envelope for `code`, as the view payload. `remediation` is a
    CodedError's chosen one; by default the spec's (null for "nothing to
    do")."""
    code = ReasonCode(code)
    spec = REASON_CODES[code]
    body = {
        "error": message,
        "reason_code": code.value,
        "error_class": spec.error_class.value,
        "remediation": remediation or spec.remediation or None,
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
    remediation = error.remediation if isinstance(error, CodedError) else None
    response = Response(
        coded_body(code, params, message, remediation), status=spec.http_status
    )
    if spec.retry_after is not None:
        response["Retry-After"] = str(spec.retry_after)
    return response


def coded_entry(error, **fields):
    """One item of a sync batch route's result list (S7d: roster rows,
    licence teachers, publish-all's skipped list), for a coded failure or
    skip. `fields` are the route's own keys (row, name, status, ...), kept
    first. The coded keys match session-results' items
    (students.item_results) and a request's envelope; `error` is the legacy
    key, the same text as `message` (QA-ERR-03: never an exception's own
    text)."""
    spec = error.spec
    return {
        **fields,
        "error": error.message,
        "reason_code": error.reason_code.value,
        "error_class": spec.error_class.value,
        "message": error.message,
        "remediation": error.remediation,
        "retryable": spec.retryable,
        "params": dict(error.params),
        "reference": get_request_id(),
    }
