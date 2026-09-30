"""Bulk roster import from a CSV file or a pasted spreadsheet range.

Two concerns, kept apart on purpose:
  * `parse_roster` turns bytes or pasted text into a list of RosterRow. It
    touches no database and no email - it can be tested on strings alone.
  * `import_roster` decides what each parsed row means for a course.

Callers must have already scoped `course` to the requesting teacher.

Epic A S7d (QA catalogue section D, approved 2026-09-30): a refusal of the
whole request is a coded error (ROSTER_*, FILE_TOO_LARGE), and every row that
isn't added carries its `row` number and a ROW_* code.
"""

import csv
import io
import logging
from dataclasses import dataclass

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction

from audit.enums import ReasonCode
from AutoGrader.reason_codes import CodedError, coded_entry
from AutoGrader.uploads import file_name_of, validate_upload_size
from users.models import CustomUser, UserTypes

from ..models import EnrollmentStatusType, StudentCourse, teacher_course_access_q
from ..serializers import DirectAddStudentSerializer
from .enrollment import (
    CROSS_SCHOOL_REJECTION_MESSAGE,
    NOT_A_STUDENT_MESSAGE,
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


#: A name's length rule, as the direct-add serializer and the user model
#: hold it (ROW_NAME_INVALID). Checked before either path touches the
#: database, so a 151-character name is refused, not a failed INSERT.
NAME_MIN_LENGTH = 2
NAME_MAX_LENGTH = 150

#: EnrollmentError texts that are a row's own code, not a generic failure.
#: The texts are the shared constants (H-71's is carried byte-identical from
#: beta), so the mapping is by identity with them, never by a substring.
ENROLLMENT_REFUSAL_CODES = {
    NOT_A_STUDENT_MESSAGE: ReasonCode.ROW_STAFF_EMAIL,
    CROSS_SCHOOL_REJECTION_MESSAGE: ReasonCode.ROW_OTHER_SCHOOL,
}


class RosterImportError(CodedError):
    """Input the caller must fix before anything can be imported: a coded
    refusal of the whole request (ROSTER_NO_INPUT, ROSTER_EMPTY,
    ROSTER_FILE_UNREADABLE, ROSTER_TOO_MANY_ROWS), answered with its own
    status and envelope by the exception handler.

    Distinct from a per-row failure, which is reported in the result rather
    than raised - one bad row must not abort the other 1999.
    """


@dataclass
class RosterRow:
    first_name: str = ""
    last_name: str = ""
    middle_name: str = ""
    email: str = ""
    #: The row as the teacher sees it in their sheet: its position among the
    #: data rows, blank rows counted (SM ruling Q3). With a header, row 1 is
    #: the file's line 2.
    row: int = 0

    @property
    def display_name(self):
        return f"{self.first_name} {self.last_name}".strip() or "Unknown"

    @property
    def is_named(self):
        return bool(self.first_name and self.last_name)

    @property
    def names_are_valid(self):
        """First and last name 2-150 characters; a middle name (an initial
        is fine) at most 150."""
        return (
            all(
                NAME_MIN_LENGTH <= len(name) <= NAME_MAX_LENGTH
                for name in (self.first_name, self.last_name)
            )
            and len(self.middle_name) <= NAME_MAX_LENGTH
        )

    @property
    def duplicate_key(self):
        """What makes a later row a repeat of this one, within one request
        (SM ruling Q5): the same address after normalize_email, or, with no
        address, the same first, middle and last name, case-insensitively."""
        if self.email:
            return ("email", normalize_email(self.email))
        return (
            "name",
            self.first_name.casefold(),
            self.middle_name.casefold(),
            self.last_name.casefold(),
        )


def _is_email_value(value):
    candidate = (value or "").strip()
    if not candidate:
        return False
    try:
        validate_email(candidate)
        return True
    except DjangoValidationError:
        return False


def _row_without_headers(row, number=0):
    """Map a headerless row by position, detecting the email by its value.

    Order is first, last, middle - the email may sit in any column. An
    invalid address is taken as a name part, so ROW_EMAIL_INVALID applies to
    a header-mapped email column only (SM ruling Q7, a documented limit).
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
        row=number,
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
        # FILE_TOO_LARGE (413, catalogue D1): the shared coded refusal, with
        # the sizes as ints and the message formatted ("2 MB").
        validate_upload_size(input_file, MAX_FILE_BYTES)
        try:
            decoded = input_file.read().decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            # A filename is never trusted for control flow: an .xlsx or an
            # ISO-8859-1 export renamed to .csv used to surface as a 500.
            raise RosterImportError(
                ReasonCode.ROSTER_FILE_UNREADABLE,
                params={"file_name": file_name_of(input_file)},
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
        raise RosterImportError(ReasonCode.ROSTER_EMPTY)

    if len(raw_rows) > MAX_ROWS:
        raise RosterImportError(
            ReasonCode.ROSTER_TOO_MANY_ROWS,
            params={"row_count": len(raw_rows), "max_rows": MAX_ROWS},
        )

    column_map = _detect_header([str(cell).strip() for cell in raw_rows[0]])
    data_rows = raw_rows[1:] if column_map else raw_rows

    parsed = []
    for number, row in enumerate(data_rows, start=1):
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
                    row=number,
                )
            )
        else:
            parsed.append(_row_without_headers(row, number))

    # A header alone, or only blank rows: nothing to import (SM ruling Q4).
    if not parsed:
        raise RosterImportError(ReasonCode.ROSTER_EMPTY)

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


SUCCEEDED, FAILED, SKIPPED = "succeeded", "failed", "skipped"


def _added(row, student, status, kind):
    return {
        "row": row.row,
        "name": student.get_full_name(),
        "status": status,
        "type": kind,
    }, SUCCEEDED


def _not_added(row, code, outcome, **params):
    """A row that wasn't added: its coded entry (catalogue D2). The message
    comes from the approved template and these params only (QA-ERR-03)."""
    error = CodedError(code, params={"row": row.row, **params})
    status = "skipped" if outcome == SKIPPED else "failed"
    return (
        coded_entry(error, row=row.row, name=row.display_name, status=status),
        outcome,
    )


def _full_display(row):
    return " ".join(
        part for part in (row.first_name, row.middle_name, row.last_name) if part
    )


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
        return _not_added(
            row,
            ReasonCode.ROW_ALREADY_ENROLLED,
            SKIPPED,
            student_display=row.display_name,
        )

    try:
        student, invited = enroll_student_by_email(
            course=course,
            email=email,
            first_name=row.first_name,
            middle_name=row.middle_name,
            last_name=row.last_name,
        )
    except AccountDisabledError:
        # A deactivated account is left exactly as it is (no reactivation,
        # no email, no enrollment); the row says so rather than failing.
        return _not_added(row, ReasonCode.ROW_ACCOUNT_DISABLED, SKIPPED)
    except EnrollmentError as exc:
        # The staff and cross-school refusals are the row's own codes, both
        # neutral (no role, no school). Anything else is the generic
        # ROW_FAILED: its text stays in the log, never in the row.
        code = ENROLLMENT_REFUSAL_CODES.get(exc.args[0] if exc.args else "")
        if code is None:
            logger.warning(
                "Bulk-add refused row %s of course %s: %s", row.row, course.id, exc
            )
            code = ReasonCode.ROW_FAILED
        return _not_added(row, code, FAILED)

    if invited:
        # New account, or one that has never signed in: a fresh temporary
        # password went out by email.
        return _added(row, student, "invited", "invitation")
    # An existing student who has signed in: enrolled as-is, password and
    # sessions untouched, told they were added.
    return _added(row, student, "enrolled", "existing_student")


def _import_row_without_email(*, course, row):
    student = _find_existing_student_by_name(course=course, row=row)

    if student is not None:
        if StudentCourse.objects.filter(student=student, course=course).exists():
            return _not_added(
                row,
                ReasonCode.ROW_ALREADY_ENROLLED,
                SKIPPED,
                student_display=row.display_name,
            )

        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
            auto_added=True,
        )
        return _added(row, student, "enrolled", "direct_add")

    # Another student of this course with exactly this name: a new account
    # would be indistinguishable from them (the direct-add serializer's own
    # rule, checked here so the row gets its code).
    if StudentCourse.find_name_conflicts(
        course=course,
        first_name=row.first_name,
        last_name=row.last_name,
        middle_name=row.middle_name,
    ).exists():
        return _not_added(
            row,
            ReasonCode.ROW_NAME_CLASH,
            FAILED,
            student_display=_full_display(row),
        )

    serializer = DirectAddStudentSerializer(
        data={
            "first_name": row.first_name,
            "middle_name": row.middle_name,
            "last_name": row.last_name,
        },
        context={"course": course},
    )
    if not serializer.is_valid():
        logger.warning(
            "Bulk-add row %s of course %s failed validation on %s",
            row.row,
            course.id,
            sorted(serializer.errors),
        )
        return _not_added(row, ReasonCode.ROW_FAILED, FAILED)

    student = serializer.save()
    return _added(row, student, "enrolled", "direct_add")


def _refusal_before_import(row):
    """The checks that need no database, in the order a teacher fixes them:
    (code, outcome) or None."""
    if not row.names_are_valid:
        return ReasonCode.ROW_NAME_INVALID, {}
    if row.email:
        try:
            validate_email(row.email.strip())
        except DjangoValidationError:
            # Nothing is created and nothing is queued for this row.
            return ReasonCode.ROW_EMAIL_INVALID, {"email": row.email}
    return None


def import_roster(*, course, rows, total_processed):
    """Apply parsed rows to a course, one independent transaction per row.

    Per-row isolation is deliberate: a roster is entered by hand, so a bad
    row is expected. One failure reports itself and the rest still import.

    Every row that isn't added says why with a ROW_* code and its `row`
    number (catalogue D2). success_count + failure_count + skipped_count is
    the number of non-blank rows; total_processed still counts the blank
    ones, as before.
    """
    results = []
    counts = {SUCCEEDED: 0, FAILED: 0, SKIPPED: 0}
    first_seen = {}

    for row in rows:
        if not row.is_named:
            result, outcome = _not_added(row, ReasonCode.ROW_NAME_MISSING, FAILED)
        elif row.duplicate_key in first_seen:
            # A repeat within this request creates and sends nothing, even
            # when the first occurrence failed (SM ruling Q5).
            result, outcome = _not_added(
                row,
                ReasonCode.ROW_DUPLICATE,
                SKIPPED,
                first_row=first_seen[row.duplicate_key],
            )
        else:
            first_seen[row.duplicate_key] = row.row
            refusal = _refusal_before_import(row)
            if refusal is not None:
                code, params = refusal
                result, outcome = _not_added(row, code, FAILED, **params)
            else:
                result, outcome = _import_one(course, row)
        results.append(result)
        counts[outcome] += 1

    return {
        "total_processed": total_processed,
        "success_count": counts[SUCCEEDED],
        "failure_count": counts[FAILED],
        "skipped_count": counts[SKIPPED],
        "results": results,
    }


def _import_one(course, row):
    """One row in its own transaction: a failure, even a database error,
    rolls back this row alone and reports it as ROW_FAILED, with no
    exception text in the row."""
    try:
        with transaction.atomic():
            if row.email:
                return _import_row_with_email(course=course, row=row)
            return _import_row_without_email(course=course, row=row)
    except Exception as exc:
        logger.error(
            "Failed to bulk-add student at row %s of course %s",
            row.row,
            course.id,
            exc_info=exc,
        )
        return _not_added(row, ReasonCode.ROW_FAILED, FAILED)
