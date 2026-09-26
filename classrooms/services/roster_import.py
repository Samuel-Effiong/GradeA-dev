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
from django.utils import timezone

from AutoGrader.error_messages import describe_user_error
from users.models import ACTIVATION_TOKEN_VALIDITY, CustomUser, UserTypes
from users.services import otp_manager

from ..models import EnrollmentStatusType, StudentCourse
from ..serializers import DirectAddStudentSerializer
from . import notifications
from .enrollment import (
    EnrollmentError,
    check_existing_account_may_join,
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
    """
    return CustomUser.objects.filter(
        first_name__iexact=row.first_name,
        last_name__iexact=row.last_name,
        middle_name__iexact=row.middle_name,
        user_type=UserTypes.STUDENT,
        enrollments__course__teacher=course.teacher,
    ).first()


def _import_row_with_email(*, course, row):
    # Normalised and matched case-insensitively, so an uppercase variant of
    # an existing address cannot slip past as a "new" student - see
    # enrollment.normalize_email.
    email = normalize_email(row.email)
    student = find_account_by_email(email)
    is_new = student is None

    if student is not None:
        # One shared gate for every path that attaches an existing account
        # to a course: refuses staff accounts, and refuses an account that
        # belongs to a different school. The bulk path used to apply
        # neither, so a row carrying a teacher's address enrolled that
        # teacher as a student, and a row carrying another school's
        # student pulled them across the tenancy boundary.
        try:
            check_existing_account_may_join(student, course)
        except EnrollmentError as exc:
            return {
                "name": row.display_name,
                "status": "failed",
                "error": str(exc),
            }, None

    if is_new:
        student = CustomUser.objects.create(
            email=email,
            first_name=row.first_name,
            middle_name=row.middle_name,
            last_name=row.last_name,
            user_type=UserTypes.STUDENT,
            is_active=False,
            school=course.teacher.school,
            activation_token=otp_manager.generate_otp(),
            activation_expires=timezone.now() + ACTIVATION_TOKEN_VALIDITY,
        )

    if StudentCourse.objects.filter(student=student, course=course).exists():
        return {
            "name": row.display_name,
            "status": "skipped",
            "error": "Already enrolled",
        }, False

    StudentCourse.objects.create(
        student=student,
        course=course,
        enrollment_status=(
            EnrollmentStatusType.PENDING if is_new else EnrollmentStatusType.ENROLLED
        ),
        auto_added=False,
    )
    notifications.send_bulk_enrollment_email(student, course)
    return {
        "name": student.get_full_name(),
        "status": "invited",
        "type": "invitation",
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

    for row in rows:
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
            logger.error(
                "Failed to bulk-add student %s %s",
                row.first_name,
                row.last_name,
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
