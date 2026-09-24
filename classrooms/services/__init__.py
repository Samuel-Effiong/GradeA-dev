"""Business logic for the classrooms app.

Views in this app parse a request, call one of these functions, and
serialize the result. Enrollment rules, roster parsing and notification
copy live here so they can be read and tested without a request object,
and so other apps have a public surface to call rather than reaching into
`classrooms.views`.

Authorization is NOT handled here: every function takes an already-scoped
`course`, and it is the caller's job to have obtained it from
`CourseViewSet.get_queryset()` (or an equivalent tenant-scoped queryset).
"""

from .enrollment import (
    CROSS_SCHOOL_REJECTION_MESSAGE,
    EnrollmentError,
    check_existing_account_may_join,
    enroll_student_by_email,
    find_account_by_email,
    normalize_email,
    remove_student_from_course,
    renew_student_activation,
    schools_associated_with,
)
from .roster_import import (
    MAX_FILE_BYTES,
    MAX_ROWS,
    RosterImportError,
    RosterRow,
    import_roster,
    parse_roster,
)

__all__ = [
    "CROSS_SCHOOL_REJECTION_MESSAGE",
    "EnrollmentError",
    "check_existing_account_may_join",
    "MAX_FILE_BYTES",
    "MAX_ROWS",
    "RosterImportError",
    "RosterRow",
    "enroll_student_by_email",
    "find_account_by_email",
    "normalize_email",
    "schools_associated_with",
    "import_roster",
    "parse_roster",
    "remove_student_from_course",
    "renew_student_activation",
]
