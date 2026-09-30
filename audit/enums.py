"""The fixed vocabularies of the audit trail (FR-A-02, BE-A-05, A6).

`ActorRole`, `AuditOutcome`, `ErrorClass` and `RetentionClass` are closed sets
and are wired into the model as `choices`. `AuditAction` is deliberately NOT
wired into the model: it grows with every epic, and a `choices=` list would
turn each new verb into a migration. The emitter validates against it instead,
so a verb that is not in this file cannot be written.
"""

from django.db import models


class ActorRole(models.TextChoices):
    STUDENT = "STUDENT", "Student"
    TEACHER = "TEACHER", "Teacher"
    SCHOOL_ADMIN = "SCHOOL_ADMIN", "School admin"
    SUPER_ADMIN = "SUPER_ADMIN", "Super admin"
    SYSTEM = "SYSTEM", "System"
    # Epic A S1: someone not signed in (a failed sign-in, a refused code).
    # Distinct from SYSTEM, which is the app's own background work.
    ANONYMOUS = "ANONYMOUS", "Anonymous (not signed in)"


class AuditOutcome(models.TextChoices):
    SUCCESS = "SUCCESS", "Success"
    FAILURE = "FAILURE", "Failure"
    DENIED = "DENIED", "Denied"


class ErrorClass(models.TextChoices):
    """BE-A-05: every non-success outcome falls in exactly one of these."""

    USER = "USER", "User"
    VALIDATION = "VALIDATION", "Validation"
    PROVIDER = "PROVIDER", "Provider"
    MODEL = "MODEL", "Model"
    SYSTEM = "SYSTEM", "System"


class RetentionClass(models.TextChoices):
    """A6: 12 months for general events, 3 years for those touching student
    records. Stored per row so the retention sweep has a column to filter on."""

    GENERAL = "GENERAL", "General (12 months)"
    STUDENT_RECORD = "STUDENT_RECORD", "Student record (3 years)"


class AuditAction(models.TextChoices):
    """One stable verb per meaningful action (FR-A-01). Success and failure are
    the row's `outcome`, not separate verbs, except where the requirement names
    them as separate events (grading requested / completed / failed)."""

    AUTH_LOGIN = "AUTH_LOGIN", "Sign-in"
    AUTH_LOGOUT = "AUTH_LOGOUT", "Sign-out"
    GRADING_REQUESTED = "GRADING_REQUESTED", "Grading requested"
    GRADING_COMPLETED = "GRADING_COMPLETED", "Grading completed"
    GRADING_FAILED = "GRADING_FAILED", "Grading failed"
    ASSIGNMENT_CREATE = "ASSIGNMENT_CREATE", "Assignment created"
    ASSIGNMENT_UPDATE = "ASSIGNMENT_UPDATE", "Assignment updated"
    ASSIGNMENT_DELETE = "ASSIGNMENT_DELETE", "Assignment deleted"
    ASSIGNMENT_COPY = "ASSIGNMENT_COPY", "Assignment copied"
    LESSON_CREATE = "LESSON_CREATE", "Lesson created"
    LESSON_UPDATE = "LESSON_UPDATE", "Lesson updated"
    LESSON_DELETE = "LESSON_DELETE", "Lesson deleted"
    TAG_CREATE = "TAG_CREATE", "Tag created"
    TAG_RENAME = "TAG_RENAME", "Tag renamed"
    TAG_DELETE = "TAG_DELETE", "Tag deleted"
    ROSTER_CHANGE = "ROSTER_CHANGE", "Roster changed"
    SUBMISSION_UPLOAD = "SUBMISSION_UPLOAD", "Submission uploaded"
    CREDIT_TRANSACTION = "CREDIT_TRANSACTION", "Credit transaction"
    DEPARTMENT_CREATE = "DEPARTMENT_CREATE", "Department created"
    DEPARTMENT_UPDATE = "DEPARTMENT_UPDATE", "Department updated"
    DEPARTMENT_DELETE = "DEPARTMENT_DELETE", "Department deleted"
    DEPARTMENT_MEMBER_ADD = "DEPARTMENT_MEMBER_ADD", "Department member added"
    DEPARTMENT_MEMBER_REMOVE = "DEPARTMENT_MEMBER_REMOVE", "Department member removed"
    LIBRARY_ADD = "LIBRARY_ADD", "Library item added"
    LIBRARY_EDIT = "LIBRARY_EDIT", "Library item edited"
    LIBRARY_COPY = "LIBRARY_COPY", "Library item copied"
    ADMIN_ACTION = "ADMIN_ACTION", "Admin action"
    DATA_EXPORT = "DATA_EXPORT", "Data export"
    PERMISSION_CHANGE = "PERMISSION_CHANGE", "Permission changed"
    # Epic A completion S1: the generic event for a state-changing request
    # that recorded no named event. `metadata.route` says which route.
    STATE_CHANGE = "STATE_CHANGE", "State-changing request"
    # Epic A completion S2: a new account from self-registration
    # (POST /auth/register). Not AUTH_LOGIN - nobody is signed in until the
    # address is verified, and that /auth/verify success is the AUTH_LOGIN.
    ACCOUNT_REGISTER = "ACCOUNT_REGISTER", "Account registered"


# Actions that always touch a student's record, so they are kept 3 years.
# Any other action can be raised to STUDENT_RECORD per event by the caller
# (`touches_student_record=True`); no event can be lowered below its action's
# floor.
STUDENT_RECORD_ACTIONS = frozenset(
    {
        AuditAction.GRADING_REQUESTED,
        AuditAction.GRADING_COMPLETED,
        AuditAction.GRADING_FAILED,
        AuditAction.ROSTER_CHANGE,
        AuditAction.SUBMISSION_UPLOAD,
    }
)


class ReasonCode(models.TextChoices):
    """FR-A-06: the stable, machine-readable reason for a non-success outcome.

    One vocabulary for both the wire (a coded error body, see
    `AutoGrader/reason_codes.py`) and the audit trail (the emitter accepts
    only these as `AuditEvent.reason_code`). UPPER_SNAKE, stable: a client
    branches on the value, so a value is never renamed, only added.

    Every member is either user-facing (it has a spec in
    `AutoGrader.reason_codes.REASON_CODES`: status, message, remediation) or
    audit-only (`AutoGrader.reason_codes.AUDIT_ONLY_CODES`: the sign-in and
    session outcomes, whose responses the auth views already shape). A test
    ties the two lists to this enum. Design: 08a §1 and §4.1.
    """

    # The FR-A-06 / QA-ERR-02 catalogue (08a §1).
    MISSING_STUDENT_NAME = "MISSING_STUDENT_NAME", "Missing or unmatched student name"
    STUDENT_NOT_ON_ROSTER = "STUDENT_NOT_ON_ROSTER", "Student not on the roster"
    FILE_UNREADABLE = "FILE_UNREADABLE", "File unreadable"
    FILE_TYPE_UNSUPPORTED = "FILE_TYPE_UNSUPPORTED", "File type not supported"
    FILE_TOO_LARGE = "FILE_TOO_LARGE", "File too large"
    SUBMISSION_EMPTY = "SUBMISSION_EMPTY", "Submission empty"
    RUBRIC_MISSING = "RUBRIC_MISSING", "Rubric missing"
    DUPLICATE_SUBMISSION = "DUPLICATE_SUBMISSION", "Duplicate submission"
    PROVIDER_FAILURE = "PROVIDER_FAILURE", "Grading service failure"
    INSUFFICIENT_CREDITS_MID_BATCH = (
        "INSUFFICIENT_CREDITS_MID_BATCH",
        "Credits ran out during a batch",
    )

    # Request-level refusals kept alongside the catalogue (08a §1, §4.4).
    INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS", "Insufficient credits"
    AI_FEATURE_NOT_AVAILABLE = "AI_FEATURE_NOT_AVAILABLE", "AI feature not available"
    NOT_RETRYABLE = "NOT_RETRYABLE", "Not retryable"

    # Sign-in and session outcomes (audit-only; FR-A-01, Epic A S1).
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED", "Account locked"
    ACCOUNT_DEACTIVATED = "ACCOUNT_DEACTIVATED", "Account deactivated"
    WRONG_PASSWORD = "WRONG_PASSWORD", "Wrong password"  # pragma: allowlist secret
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS", "Invalid credentials"
    INVALID_CODE = "INVALID_CODE", "Invalid code"
    CODE_EXPIRED = "CODE_EXPIRED", "Code expired"
    CODE_MISSING = "CODE_MISSING", "Code missing"
    CODE_NOT_REQUESTED = "CODE_NOT_REQUESTED", "Code not requested"
    RESET_LOCKED = "RESET_LOCKED", "Password reset locked"
    REFRESH_TOKEN_MISSING = "REFRESH_TOKEN_MISSING", "Refresh token missing"
    REFRESH_TOKEN_INVALID = "REFRESH_TOKEN_INVALID", "Refresh token invalid"
    SESSION_REVOKE_FAILED = "SESSION_REVOKE_FAILED", "Session revoke failed"
    GOOGLE_SIGN_IN_REFUSED = "GOOGLE_SIGN_IN_REFUSED", "Google sign-in refused"
    GOOGLE_CODE_MISSING = "GOOGLE_CODE_MISSING", "Google code missing"
    GOOGLE_EXCHANGE_FAILED = "GOOGLE_EXCHANGE_FAILED", "Google code exchange failed"
    GOOGLE_TOKEN_INVALID = "GOOGLE_TOKEN_INVALID", "Google token invalid"
    GOOGLE_EMAIL_UNVERIFIED = "GOOGLE_EMAIL_UNVERIFIED", "Google email unverified"
    # A malformed body on an anonymous door (Epic A S2, task/epic-a-s2:
    # audit.request_audit.INVALID_REQUEST). Listed ahead of S2 landing so
    # the emitter does not reject it.
    INVALID_REQUEST = "INVALID_REQUEST", "Invalid request"


# The ten FR-A-06 conditions, as QA-ERR-02 lists them.
FR_A_06_CODES = frozenset(
    {
        ReasonCode.MISSING_STUDENT_NAME,
        ReasonCode.STUDENT_NOT_ON_ROSTER,
        ReasonCode.FILE_UNREADABLE,
        ReasonCode.FILE_TYPE_UNSUPPORTED,
        ReasonCode.FILE_TOO_LARGE,
        ReasonCode.SUBMISSION_EMPTY,
        ReasonCode.RUBRIC_MISSING,
        ReasonCode.DUPLICATE_SUBMISSION,
        ReasonCode.PROVIDER_FAILURE,
        ReasonCode.INSUFFICIENT_CREDITS_MID_BATCH,
    }
)
