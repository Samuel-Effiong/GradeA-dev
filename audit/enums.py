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
