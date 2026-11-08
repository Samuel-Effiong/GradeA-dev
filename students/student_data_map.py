"""NFR-CMP-04: every column that can point at a user, and what student export
and erasure do with it.

The guard test (`students/tests_student_data_map.py`) fails when a model gains
a link to a user that is not listed here, so new student data cannot be
missed by export or erasure.

Roles, from the owner's rulings of 2026-11-06:

* ``DELETE``: the student's own data. Erased. ``export`` says whether it goes
  into the student's export (sign-in and security data does not).
* ``KEEP_RECORD``: the activity log and credit records. Never changed by an
  erasure. The student's name and email stay attached to them through the
  erased-student record, kept 7 years (D1, D4).
* ``NOT_A_STUDENT``: a column only a teacher or an admin can hold (course
  owner, license admin, ...). Erasure refuses to run if one of these points
  at the student, because that means the account isn't only a student.
* ``FINANCIAL_ACCOUNT``: the student's credit wallet. Deleted only when it
  holds nothing; otherwise erasure refuses.

``match`` says how a row is found for a student: ``USER`` (the column is
the student, as a foreign key or a bare id), ``EMAIL`` (the column equals
the student's email address, ignoring case) or ``OWN_ROW`` (a column on the
student's own user row). ``stored_file`` marks a column that names a file in
storage, deleted after the erasure commits.

``course_scoped`` marks rows that a teacher's erasure (D3: removes the
student from that teacher's courses only) also removes. Everything else is
removed only by a full erasure (school admin or super admin).
"""

from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    DELETE = "DELETE"
    KEEP_RECORD = "KEEP_RECORD"
    NOT_A_STUDENT = "NOT_A_STUDENT"
    FINANCIAL_ACCOUNT = "FINANCIAL_ACCOUNT"


ROLES = frozenset(Role)


class Match(str, Enum):
    USER = "USER"
    EMAIL = "EMAIL"
    OWN_ROW = "OWN_ROW"


@dataclass(frozen=True)
class Entry:
    role: Role
    why: str
    export: bool = False
    course_scoped: bool = False
    match: Match = Match.USER
    stored_file: bool = False


def _own(why, *, export=True, course_scoped=False):
    return Entry(Role.DELETE, why, export=export, course_scoped=course_scoped)


def _security(why):
    return Entry(Role.DELETE, why, export=False)


def _keep(why):
    return Entry(Role.KEEP_RECORD, why)


def _staff(why):
    return Entry(Role.NOT_A_STUDENT, why)


STUDENT_DATA_MAP = {
    # The student's own work and app data: exported, then deleted.
    ("classrooms.StudentCourse", "student"): _own(
        "Enrollments, final grade and the AI-written progress summary",
        course_scoped=True,
    ),
    ("students.StudentSubmission", "student"): _own(
        "Answers, extracted text, scores, feedback and AI grading results",
        course_scoped=True,
    ),
    # SM R3: no course column, so removed by a full erasure only.
    ("dashboard.StudentRiskAlertState", "student"): _own(
        "The at-risk flags teachers see"
    ),
    # SM E-Q2: through its assignment, a teacher's erasure removes it.
    ("students.BackgroundProcessingTask", "requested_by"): _own(
        "Processing records for files the student uploaded (file names only)",
        course_scoped=True,
    ),
    # SM R4: a chat belongs to a course, so a teacher's erasure removes it.
    ("ai_processor.ChatSession", "user"): _own(
        "Conversations with the in-app assistant", course_scoped=True
    ),
    # SM R8: exported, then deleted from storage after the erasure commits.
    ("users.CustomUser", "profile_image"): Entry(
        Role.DELETE,
        "The student's profile picture",
        export=True,
        match=Match.OWN_ROW,
        stored_file=True,
    ),
    ("users.UserActivity", "user"): _own("Last seen and activity counts"),
    ("users.Settings", "user"): _own("The student's app settings"),
    # Sign-in and security data: deleted, never exported.
    ("authtoken.Token", "user"): _security("API sign-in token"),
    ("authtoken.TokenProxy", "user"): _security(
        "Proxy of authtoken.Token (same table)"
    ),
    ("token_blacklist.OutstandingToken", "user"): _security("Issued sign-in tokens"),
    ("users.PasswordResetOTP", "user"): _security("Password-reset code"),
    ("users.PasswordChangeOTP", "user"): _security("Password-change code"),
    ("users.RevokedSession", "user"): _security("Signed-out sessions"),
    ("users.UserGoogleCredentials", "user"): _security("Google sign-in link"),
    # SM R7: kept by email address, not by user id; matched ignoring case.
    ("users.BetaWhitelist", "email"): Entry(
        Role.DELETE, "Beta access list entry", match=Match.EMAIL
    ),
    ("users.Waitlist", "email"): Entry(
        Role.DELETE, "Waitlist entry", match=Match.EMAIL
    ),
    # Activity log and credit records: kept, with name and email (D1, D4).
    ("audit.AuditEvent", "actor_id"): _keep("Activity log: who did it"),
    ("audit.AuditEvent", "target_id"): _keep("Activity log: what it was done to"),
    ("billing.CreditLedger", "user_id"): _keep("Credit records (7 years)"),
    ("billing.CreditUsageLog", "user_id"): _keep("Credit usage records (7 years)"),
    ("ai_processor.AICallRecord", "billed_user_id"): _keep(
        "AI call cost records, tied to credit records"
    ),
    ("billing.BillingTransaction", "user"): _keep("Billing records (SET_NULL)"),
    ("billing.BillingTransaction", "performed_by"): _keep("Billing records (SET_NULL)"),
    ("billing.PaymentDispute", "user"): _keep("Payment disputes (SET_NULL)"),
    # The erased-student record itself: kept 7 years (NFR-CMP-04 step 3).
    ("students.ErasedStudentRecord", "original_user_id"): _keep(
        "Who an erased student was (7 years)"
    ),
    # The student's wallet: deleted only when empty.
    ("billing.CreditWallet", "user"): Entry(
        Role.FINANCIAL_ACCOUNT, "Credit wallet: erasure refuses if it holds credits"
    ),
    # Columns only a teacher or an admin can hold.
    ("admin.LogEntry", "user"): _staff("Django admin history: staff only"),
    ("assignments.Assignment", "teacher"): _staff("Assignment owner"),
    ("assignments.AssignmentGenerationHistory", "user"): _staff(
        "Teacher's AI assignment generation"
    ),
    ("assignments.AssignmentGenerationSession", "user"): _staff(
        "Teacher's AI assignment generation"
    ),
    ("billing.BetaProfile", "user"): _staff("Teacher beta sign-up"),
    ("billing.LicenseBillingRecord", "performed_by"): _staff("License admin"),
    ("billing.LicenseOverageOfflineRequest", "requested_by"): _staff("License admin"),
    ("billing.LicenseOverageOfflineRequest", "reviewed_by"): _staff("Super admin"),
    ("billing.LicenseOveragePurchaseIntent", "initiated_by"): _staff("License admin"),
    ("billing.LicenseStripeMutationIntent", "performed_by"): _staff("License admin"),
    ("billing.LicenseStripeMutationIntent", "resolved_by"): _staff("Super admin"),
    ("billing.LicenseSubscription", "admin_user"): _staff("License admin"),
    ("billing.LiveQARun", "triggered_by"): _staff("Super admin QA run"),
    ("billing.SchoolCreditAllocation", "user"): _staff("Teacher on a license"),
    ("billing.UserSubscription", "user"): _staff("Teacher subscription"),
    ("classrooms.Course", "teacher"): _staff("Course owner"),
    ("classrooms.Session", "teacher"): _staff("Session owner"),
    ("classrooms.Session", "created_by"): _staff("Session creator"),
    ("dashboard.TeacherInactivityAlertState", "teacher"): _staff(
        "Teacher inactivity alert"
    ),
    ("students.BatchUploadSession", "teacher"): _staff("Teacher batch upload"),
    ("students.ErasedStudentRecord", "erased_by_id"): _staff(
        "The admin or teacher who erased a student"
    ),
    # SM R1: tags are teacher-only (Epic C1).
    ("tags.Tag", "teacher"): _staff("Tag owner"),
    ("tags.Tag", "created_by_id"): _staff(
        "Teacher who made the tag (empty for backfilled tags)"
    ),
}


def entries_with_role(role):
    return [
        (key, entry) for key, entry in STUDENT_DATA_MAP.items() if entry.role is role
    ]
