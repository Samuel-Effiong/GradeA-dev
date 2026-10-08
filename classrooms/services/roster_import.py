"""Bulk roster import from a CSV file or a pasted spreadsheet range.

Two concerns, kept apart on purpose:
  * `parse_roster` turns bytes or pasted text into a list of RosterRow. It
    touches no database and no email - it can be tested on strings alone.
  * `import_roster` decides what each parsed row means for a course.

Callers must have already scoped `course` to the requesting teacher.
"""

import csv
import io
import logging
from dataclasses import dataclass

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction

from AutoGrader.error_messages import describe_user_error
from users.models import CustomUser, UserTypes

from ..models import EnrollmentStatusType, StudentCourse, teacher_course_access_q
from ..serializers import DirectAddStudentSerializer
from .enrollment import (
    AccountDisabledError,
    EnrollmentError,
    enroll_student_by_email,
    find_account_by_email,
    normalize_email,
)

logger = logging.getLogger(__name__)

# Bulk import runs synchronously in the request cycle, creating a user and
# queueing an email per row, so both the upload and the row count need a
# ceiling. 2000 rows is far above any real class roster. The byte cap is
# sized for a WIDE export - a school SIS dump carries columns we ignore
# (student id, homeroom, guardian, address), so a legitimate 2000-row file
# can approach 1 MB even though a bare four-column roster is under 200 KB.
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 2000

HEADER_VARIATIONS = {
    "first_name": ["First Name", "FirstName", "first_name", "first"],
    "last_name": ["Last Name", "LastName", "last_name", "last"],
    "middle_name": ["Middle Name", "middle_name", "middle"],
    "email": ["Email", "email", "e-mail"],
}


class RosterImportError(Exception):
    """Input the caller must fix before anything can be imported.

    Distinct from a per-row failure, which is reported in the result rather
    than raised - one bad row must not abort the other 1999.
    """

    def __init__(self, message, field="detail"):
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass
class RosterRow:
    first_name: str = ""
    last_name: str = ""
    middle_name: str = ""
    email: str = ""

    @property
    def display_name(self):
        return f"{self.first_name} {self.last_name}".strip() or "Unknown"

    @property
    def is_named(self):
        return bool(self.first_name and self.last_name)


def _is_email_value(value):
    candidate = (value or "").strip()
    if not candidate:
        return False
    try:
        validate_email(candidate)
        return True
    except DjangoValidationError:
        return False


def _row_without_headers(row):
    """Map a headerless row by position, detecting the email by its value.

    Order is first, last, middle - the email may sit in any column.
    """
    email = ""
    name_parts = []

    for cell in row:
        value = (cell or "").strip()
        if not value:
            continue
        if _is_email_value(value):
            if not email:
                email = value
            continue
        name_parts.append(value)

    return RosterRow(
        first_name=name_parts[0] if len(name_parts) > 0 else "",
        last_name=name_parts[1] if len(name_parts) > 1 else "",
        middle_name=name_parts[2] if len(name_parts) > 2 else "",
        email=email,
    )


def _detect_header(first_row):
    """Return the column map if this row looks like a header, else None.

    Two matched fields is the threshold: one could easily be a student
    actually named "Email", and treating a data row as a header would
    silently drop a real student.
    """
    column_map = {}
    for field, variations in HEADER_VARIATIONS.items():
        normalized = [v.lower().replace(" ", "_") for v in variations]
        for index, cell in enumerate(first_row):
            if cell in variations or cell.lower().replace(" ", "_") in normalized:
                column_map[field] = index
                break

    return column_map if len(column_map) >= 2 else None


def read_rows(*, input_file=None, raw_data=None):
    """Decode an upload or a pasted range into raw cell lists.

    The size check runs BEFORE `.read()`: the point of a cap is to avoid
    pulling an unbounded upload into worker memory, which a check afterwards
    would already have done.
    """
    if input_file is not None:
        if input_file.size > MAX_FILE_BYTES:
            raise RosterImportError(
                "This file is too large. Upload a roster of at most "
                f"{MAX_FILE_BYTES // 1024} KB.",
                field="file",
            )
        try:
            decoded = input_file.read().decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            # A filename is never trusted for control flow: an .xlsx or an
            # ISO-8859-1 export renamed to .csv used to surface as a 500.
            raise RosterImportError(
                "This file isn't readable as UTF-8 text. Export your roster "
                "as a CSV file and try again.",
                field="file",
            ) from exc
        return list(csv.reader(decoded))

    if raw_data:
        delimiter = "\t" if "\t" in raw_data else ","
        return list(csv.reader(io.StringIO(raw_data.strip()), delimiter=delimiter))

    return []


def parse_roster(*, input_file=None, raw_data=None):
    """Return (rows, total_raw_rows). Pure parsing - no DB, no email."""
    raw_rows = read_rows(input_file=input_file, raw_data=raw_data)

    if not raw_rows:
        raise RosterImportError("No valid student data found in input")

    if len(raw_rows) > MAX_ROWS:
        raise RosterImportError(
            f"This roster has {len(raw_rows)} rows. Upload at most "
            f"{MAX_ROWS} rows at a time."
        )

    column_map = _detect_header([str(cell).strip() for cell in raw_rows[0]])
    data_rows = raw_rows[1:] if column_map else raw_rows

    parsed = []
    for row in data_rows:
        if not any(row):
            continue
        if column_map:

            def cell(field, row=row):
                index = column_map.get(field)
                if index is not None and index < len(row):
                    return (row[index] or "").strip()
                return ""

            parsed.append(
                RosterRow(
                    first_name=cell("first_name"),
                    last_name=cell("last_name"),
                    middle_name=cell("middle_name"),
                    email=cell("email"),
                )
            )
        else:
            parsed.append(_row_without_headers(row))

    return parsed, len(data_rows)


def _find_existing_student_by_name(*, course, row):
    """Match a named row against a student the course's teacher ALREADY
    teaches.

    Scoping to the teacher is load-bearing. Matching by name across the
    whole user table meant a roster row reading "John Smith" attached some
    other school's John Smith to this course - exposing that student's
    record to a teacher with no relationship to them, and surfacing this
    course in that student's dashboard.

    "Already teaches" means through a course the teacher can still REACH
    (H-38). `course.teacher` stays set after a teacher is removed from a
    school, so owner scoping alone matched their old school's pupils and
    attached those records to the teacher's new course - and this no-email
    path has no cross-school gate of its own. One filter() call, so the
    access rule and the enrollment match the same enrollment row.
    """
    return CustomUser.objects.filter(
        teacher_course_access_q(course.teacher, prefix="enrollments__course__"),
        first_name__iexact=row.first_name,
        last_name__iexact=row.last_name,
        middle_name__iexact=row.middle_name,
        user_type=UserTypes.STUDENT,
    ).first()


def _import_row_with_email(*, course, row):
    """An emailed row goes through exactly the single-add path
    (enrollment.enroll_student_by_email): a new student is created active
    with a generated temporary password and gets the login-credentials
    email; an existing student passes the shared cross-school/staff gate and
    is either enrolled (already onboarded), promoted and re-invited (still
    onboarding, or a legacy never-activated row) or, if someone deactivated
    it, skipped untouched. No activation code is minted, so nothing here
    feeds the code-based student sign-up being retired."""
    # Normalised and matched case-insensitively, so an uppercase variant of
    # an existing address cannot slip past as a "new" student - see
    # enrollment.normalize_email.
    email = normalize_email(row.email)
    existing = find_account_by_email(email)

    # The roster's own contract: an already-enrolled row is "skipped", not
    # "failed" (the single add treats it as an error).
    if (
        existing is not None
        and StudentCourse.objects.filter(student=existing, course=course).exists()
    ):
        return {
            "name": row.display_name,
            "status": "skipped",
            "error": "Already enrolled",
        }, False

    try:
        student, invited = enroll_student_by_email(
            course=course,
            email=email,
            first_name=row.first_name,
            middle_name=row.middle_name,
            last_name=row.last_name,
        )
    except AccountDisabledError as exc:
        # A deactivated account is left exactly as it is (no reactivation,
        # no email, no enrollment); the row says so rather than failing.
        return {
            "name": row.display_name,
            "status": "skipped",
            "error": str(exc),
        }, False
    except EnrollmentError as exc:
        return {
            "name": row.display_name,
            "status": "failed",
            "error": str(exc),
        }, None

    if invited:
        # New account, or one that has never signed in: a fresh temporary
        # password went out by email.
        return {
            "name": student.get_full_name(),
            "status": "invited",
            "type": "invitation",
        }, True
    # An existing student who has signed in: enrolled as-is, password and
    # sessions untouched, told they were added.
    return {
        "name": student.get_full_name(),
        "status": "enrolled",
        "type": "existing_student",
    }, True


def _import_row_without_email(*, course, row):
    student = _find_existing_student_by_name(course=course, row=row)

    if student is not None:
        if StudentCourse.objects.filter(student=student, course=course).exists():
            return {
                "name": student.get_full_name(),
                "status": "skipped",
                "error": "Already enrolled",
            }, False

        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
            auto_added=True,
        )
        return {
            "name": student.get_full_name(),
            "status": "enrolled",
            "type": "direct_add",
        }, True

    serializer = DirectAddStudentSerializer(
        data={
            "first_name": row.first_name,
            "middle_name": row.middle_name,
            "last_name": row.last_name,
        },
        context={"course": course},
    )
    if not serializer.is_valid():
        return {
            "name": row.display_name,
            "status": "failed",
            "error": next(iter(serializer.errors.values()))[0],
        }, None

    student = serializer.save()
    return {
        "name": student.get_full_name(),
        "status": "enrolled",
        "type": "direct_add",
    }, True


def import_roster(*, course, rows, total_processed):
    """Apply parsed rows to a course, one independent transaction per row.

    Per-row isolation is deliberate: a roster is entered by hand, so a bad
    row is expected. One failure reports itself and the rest still import.
    """
    results = []
    success_count = 0
    failure_count = 0

    for position, row in enumerate(rows, start=1):
        if not row.is_named:
            results.append(
                {
                    "name": row.display_name,
                    "status": "failed",
                    "error": "First and last names are required.",
                }
            )
            failure_count += 1
            continue

        try:
            with transaction.atomic():
                if row.email:
                    result, succeeded = _import_row_with_email(course=course, row=row)
                else:
                    result, succeeded = _import_row_without_email(
                        course=course, row=row
                    )
        except Exception as exc:
            # By position and course, never by the student's name (H-91).
            # The position counts the parsed rows from 1; it is not the
            # spreadsheet's line number (a header or blank lines shift it).
            logger.error(
                "Failed to bulk-add the student in parsed row %d of the import "
                "for course %s",
                position,
                course.id,
                exc_info=exc,
            )
            results.append(
                {
                    "name": row.display_name,
                    "status": "failed",
                    "error": describe_user_error(
                        exc,
                        fallback_message=(
                            "Could not add this student — check the row data "
                            "and try again."
                        ),
                    ),
                }
            )
            failure_count += 1
            continue

        results.append(result)
        if succeeded is True:
            success_count += 1
        elif succeeded is None:
            failure_count += 1

    return {
        "total_processed": total_processed,
        "success_count": success_count,
        "failure_count": failure_count,
        "results": results,
    }
