"""
Epic A S7d, QA catalogue section D: roster import (bulk-add-students).

D1: every refusal of the whole request is coded - ROSTER_NO_INPUT,
ROSTER_EMPTY (also a header alone or only blank rows, SM ruling Q4),
ROSTER_FILE_UNREADABLE, ROSTER_TOO_MANY_ROWS, and FILE_TOO_LARGE (413, sizes
as ints, checked before the file is read).

D2: every row that isn't added carries its `row` number (its place among the
data rows, blank rows counted - Q3) and a ROW_* code, with the approved text
and nothing from an exception. The new checks: ROW_EMAIL_INVALID (a
header-mapped email column only - Q7) and ROW_DUPLICATE (Q5).

Rule 14: no MagicMock. Outgoing mail is recorded by `Sent`, a real object;
the one patched import step is a real function that runs a real failing
query.
"""

import io
import logging
from contextlib import contextmanager
from unittest.mock import patch

from django.db import connection
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from classrooms.services import MAX_FILE_BYTES, MAX_ROWS, roster_import
from classrooms.services.roster_import import read_rows
from users.models import CustomUser, UserTypes

PASSWORD = "Str0ng-s7d-roster!"  # pragma: allowlist secret
SENTINEL = "SENTINEL_s7d_division"
ROLE_WORDS = ("teacher", "admin", "staff", "super")


@contextmanager
def every_log_line():
    """Every record any logger emits, at every level, as formatted text
    (a real handler on the root logger, not a mock)."""
    lines = []

    class Keep(logging.Handler):
        def emit(self, record):
            lines.append(f"{record.name} {record.getMessage()} {record.__dict__}")

    handler = Keep(level=logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield lines
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


class Sent:
    """Records every send_email_task.delay() (a stand-in, not a MagicMock)."""

    def __init__(self):
        self.calls = []

    def delay(self, *args, **kwargs):
        self.calls.append(kwargs)


class RosterFixture(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="S7d Roster School")
        self.teacher = CustomUser.objects.create_user(
            email="t@s7d-roster.school.edu",
            password=PASSWORD,
            first_name="Tee",
            last_name="Cher",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        self.session = Session.objects.create(name="S7d", teacher=self.teacher)
        self.course = Course.objects.create(
            name="S7d 101", teacher=self.teacher, session=self.session
        )
        self.url = reverse("course-bulk-add-students", kwargs={"pk": self.course.pk})
        self.client.force_authenticate(self.teacher)
        self.sent = Sent()
        patcher = patch(
            "classrooms.services.notifications.send_email_task", new=self.sent
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, raw):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, {"raw_data": raw})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return response

    def rows(self, raw):
        return {entry["row"]: entry for entry in self.post(raw).data["results"]}

    def enrolled(self):
        return StudentCourse.objects.filter(course=self.course).count()

    def sent_to(self):
        return [addr for call in self.sent.calls for addr in call["recipient_list"]]


class RowNumberTests(RosterFixture):
    def test_a_blank_row_still_counts_so_numbers_match_the_sheet(self):
        """Q3: with a header, row 1 is the file's line 2; the blank row 2 is
        skipped but keeps its number."""
        rows = self.rows("first_name,last_name,email\nAnn,One,\n,,\nBob,Two,\nX,,\n")

        self.assertEqual(sorted(rows), [1, 3, 4])
        self.assertEqual(rows[1]["status"], "enrolled")
        self.assertEqual(rows[3]["status"], "enrolled")
        self.assertEqual(rows[4]["reason_code"], "ROW_NAME_MISSING")
        self.assertEqual(
            rows[4]["error"], "Row 4: a first and a last name are required."
        )

    def test_without_a_header_row_1_is_the_first_line(self):
        rows = self.rows("Ann,One\n\nBob,Two\n")

        self.assertEqual(sorted(rows), [1, 3])

    def test_every_added_row_carries_its_number_and_no_code(self):
        rows = self.rows("first_name,last_name\nAnn,One\n")

        self.assertEqual(
            rows[1],
            {"row": 1, "name": "Ann One", "status": "enrolled", "type": "direct_add"},
        )

    def test_the_counts_add_up_to_the_non_blank_rows(self):
        StudentCourse.objects.create(
            student=CustomUser.objects.create_user(
                email="in@s7d.example.org",
                password=PASSWORD,
                first_name="Al",
                last_name="Ready",
                user_type=UserTypes.STUDENT,
            ),
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        data = self.post(
            "first_name,last_name,email\n"
            "Ann,One,\n"  # added
            ",,\n"  # blank
            "Al,Ready,in@s7d.example.org\n"  # skipped
            "X,,\n"  # failed
        ).data

        self.assertEqual(
            (data["success_count"], data["failure_count"], data["skipped_count"]),
            (1, 1, 1),
        )
        self.assertEqual(data["total_processed"], 4)  # blank rows still counted


class RowCodeTests(RosterFixture):
    def assert_coded(self, entry, code, status_, message):
        self.assertEqual(entry["reason_code"], code)
        self.assertEqual(entry["status"], status_)
        self.assertEqual(entry["message"], message)
        self.assertEqual(entry["error"], message)
        self.assertEqual(entry["error_class"], "USER")
        self.assertEqual(entry["params"]["row"], entry["row"])

    def test_a_name_outside_2_to_150_characters_is_refused_on_both_paths(self):
        long = "L" * 151
        rows = self.rows(
            "first_name,last_name,email\n"
            f"{long},Six,six@s7d.example.org\n"  # emailed, 151
            "G,Seven,g7@s7d.example.org\n"  # emailed, 1
            f"Hal,{long},\n"  # no email, 151
            "Ivy,J,\n"  # no email, 1
        )

        for number in (1, 2, 3, 4):
            with self.subTest(row=number):
                self.assert_coded(
                    rows[number],
                    "ROW_NAME_INVALID",
                    "failed",
                    f"Row {number}: each name needs between 2 and 150 characters.",
                )
        self.assertEqual(self.enrolled(), 0)
        self.assertEqual(self.sent.calls, [])

    def test_a_middle_initial_is_still_a_valid_name(self):
        rows = self.rows("first_name,middle_name,last_name\nAnn,J,One\n")

        self.assertEqual(rows[1]["status"], "enrolled")

    def test_an_invalid_email_creates_and_sends_nothing(self):
        before = CustomUser.objects.count()

        rows = self.rows("first_name,last_name,email\nEve,Five,abc\n")

        self.assert_coded(
            rows[1],
            "ROW_EMAIL_INVALID",
            "failed",
            'Row 1: "abc" isn\'t a valid email address.',
        )
        self.assertEqual(rows[1]["params"], {"row": 1, "email": "abc"})
        self.assertEqual(CustomUser.objects.count(), before)
        self.assertEqual(self.sent.calls, [])

    def test_the_invalid_address_reaches_no_log_line(self):
        """SM condition (b): the sync-only email param goes to the requester
        in the row, and nowhere else."""
        address = "s7d-not-an-address@@nowhere"

        with every_log_line() as lines:
            rows = self.rows(f"first_name,last_name,email\nEve,Five,{address}\n")

        self.assertEqual(rows[1]["reason_code"], "ROW_EMAIL_INVALID")
        self.assertEqual(rows[1]["params"]["email"], address)
        self.assertNotIn(address, "\n".join(lines))

    def test_an_enrolled_student_is_skipped_on_both_paths(self):
        student = CustomUser.objects.create_user(
            email="al@s7d.example.org",
            password=PASSWORD,
            first_name="Al",
            last_name="Ready",
            user_type=UserTypes.STUDENT,
            school=self.school,
        )
        StudentCourse.objects.create(
            student=student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        rows = self.rows(
            "first_name,last_name,email\nAl,Ready,al@s7d.example.org\nAl,Ready,\n"
        )

        # Row 2 has no email, so it isn't a repeat of row 1 (Q5 keys an
        # emailed row by its address); it is the same student by name.
        self.assert_coded(
            rows[1],
            "ROW_ALREADY_ENROLLED",
            "skipped",
            "Row 1: Al Ready is already in this course.",
        )
        self.assert_coded(
            rows[2],
            "ROW_ALREADY_ENROLLED",
            "skipped",
            "Row 2: Al Ready is already in this course.",
        )
        self.assertIsNone(rows[1]["remediation"])

    def test_a_new_student_with_an_enrolled_students_name_is_a_clash(self):
        """The direct-add serializer's defensive rule, now a row code. The
        teacher's own-student match normally reaches such a student first
        (and skips the row as already enrolled), so the match is switched
        off to reach the rule itself."""
        twin = CustomUser.objects.create_user(
            email="twin@s7d.example.org",
            password=PASSWORD,
            first_name="Tw",
            middle_name="Ee",
            last_name="In",
            user_type=UserTypes.STUDENT,
        )
        StudentCourse.objects.create(
            student=twin,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        with patch.object(
            roster_import, "_find_existing_student_by_name", new=lambda **_: None
        ):
            rows = self.rows("first_name,middle_name,last_name\nTw,Ee,In\n")

        self.assert_coded(
            rows[1],
            "ROW_NAME_CLASH",
            "failed",
            "Row 1: a student named Tw Ee In is already in this course.",
        )
        self.assertEqual(
            rows[1]["remediation"],
            "Add an email address to tell the two students apart.",
        )
        self.assertEqual(self.enrolled(), 1)

    def test_every_staff_role_answers_identically(self):
        """H-71: a teacher, a school admin and a super admin give the same
        row, apart from its reference; no role, no account_type."""
        staff = (
            CustomUser.objects.create_user(
                email="t2@s7d-roster.school.edu",
                password=PASSWORD,
                user_type=UserTypes.TEACHER,
            ),
            CustomUser.objects.create_user(
                email="sa@s7d-roster.school.edu",
                password=PASSWORD,
                user_type=UserTypes.SCHOOL_ADMIN,
                school=self.school,
            ),
            CustomUser.objects.create_superuser(
                email="root@s7d.gradea.com",
                password=PASSWORD,
                user_type=UserTypes.SUPER_ADMIN,
            ),
        )
        answers = []
        for user in staff:
            row = dict(
                self.rows(f"first_name,last_name,email\nHal,Eight,{user.email}\n")[1]
            )
            row.pop("reference")
            answers.append(row)

        self.assertEqual(answers[0], answers[1])
        self.assertEqual(answers[0], answers[2])
        self.assert_coded(
            answers[0],
            "ROW_STAFF_EMAIL",
            "failed",
            "Row 1: this email can't be added as a student.",
        )
        self.assertEqual(answers[0]["params"], {"row": 1})
        for word in ROLE_WORDS:
            self.assertNotIn(word, answers[0]["message"].lower())
        self.assertEqual(self.enrolled(), 0)

    def test_another_schools_student_names_no_school(self):
        other = School.objects.create(name="S7d SECRET Other School")
        foreign = CustomUser.objects.create_user(
            email="far@s7d.example.org",
            password=PASSWORD,
            first_name="Far",
            last_name="Away",
            user_type=UserTypes.STUDENT,
            school=other,
        )

        rows = self.rows(f"first_name,last_name,email\nFar,Away,{foreign.email}\n")

        self.assert_coded(
            rows[1],
            "ROW_OTHER_SCHOOL",
            "failed",
            "Row 1: this account can't be added to this school. If you believe "
            "this is a mistake, contact your school administrator.",
        )
        self.assertNotIn("SECRET", str(rows[1]))
        self.assertIsNone(rows[1]["remediation"])


class DuplicateRowTests(RosterFixture):
    """Q5: a repeat within one request is skipped, points at the first
    occurrence, and creates and sends nothing."""

    def test_an_email_repeat_in_another_case_and_spacing(self):
        rows = self.rows(
            "first_name,last_name,email\n"
            "Cat,Three,cat@s7d.example.org\n"
            "Cat,Three, CAT@s7d.example.org \n"
        )

        self.assertEqual(rows[1]["status"], "invited")
        self.assertEqual(rows[2]["reason_code"], "ROW_DUPLICATE")
        self.assertEqual(rows[2]["status"], "skipped")
        self.assertEqual(rows[2]["params"], {"row": 2, "first_row": 1})
        self.assertEqual(rows[2]["message"], "Row 2 repeats row 1.")
        self.assertEqual(
            CustomUser.objects.filter(email__iexact="cat@s7d.example.org").count(), 1
        )
        self.assertEqual(self.sent_to().count("cat@s7d.example.org"), 1)

    def test_a_name_repeat_without_an_email(self):
        rows = self.rows("first_name,last_name\nDan,Four\ndan,FOUR\n")

        self.assertEqual(rows[2]["reason_code"], "ROW_DUPLICATE")
        self.assertEqual(rows[2]["params"]["first_row"], 1)
        self.assertEqual(
            CustomUser.objects.filter(
                first_name__iexact="dan", last_name__iexact="four"
            ).count(),
            1,
        )

    def test_a_repeat_of_a_failed_row_points_at_it(self):
        rows = self.rows("first_name,last_name,email\nEve,Five,abc\nEve,Five,abc\n")

        self.assertEqual(rows[1]["reason_code"], "ROW_EMAIL_INVALID")
        self.assertEqual(rows[2]["reason_code"], "ROW_DUPLICATE")
        self.assertEqual(rows[2]["params"]["first_row"], 1)

    def test_the_same_name_with_different_emails_is_not_a_repeat_but_a_clash(self):
        """Different addresses are different students (not ROW_DUPLICATE),
        but one course can't hold two students of exactly the same name
        (StudentCourse.clean), so the second is ROW_NAME_CLASH, and no
        account is created for it."""
        rows = self.rows(
            "first_name,last_name,email\n"
            "Sam,Same,sam1@s7d.example.org\n"
            "Sam,Same,sam2@s7d.example.org\n"
        )

        self.assertEqual(rows[1]["status"], "invited")
        self.assertEqual(rows[2]["reason_code"], "ROW_NAME_CLASH")
        self.assertEqual(
            rows[2]["message"],
            "Row 2: a student named Sam Same is already in this course.",
        )
        self.assertFalse(
            CustomUser.objects.filter(email="sam2@s7d.example.org").exists()
        )
        self.assertNotIn("sam2@s7d.example.org", self.sent_to())

    def test_an_existing_account_with_a_clashing_name_is_refused_and_untouched(self):
        """The model's own check, after the gates: nothing it wrote stays."""
        StudentCourse.objects.create(
            student=CustomUser.objects.create_user(
                email="first@s7d.example.org",
                password=PASSWORD,
                first_name="Kim",
                last_name="Twin",
                user_type=UserTypes.STUDENT,
            ),
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        second = CustomUser.objects.create_user(
            email="second@s7d.example.org",
            password=PASSWORD,
            first_name="Kim",
            last_name="Twin",
            user_type=UserTypes.STUDENT,
            school=self.school,
        )
        password_before = second.password

        rows = self.rows(
            "first_name,last_name,email\nKim,Twin,second@s7d.example.org\n"
        )

        self.assertEqual(rows[1]["reason_code"], "ROW_NAME_CLASH")
        self.assertEqual(self.enrolled(), 1)
        second.refresh_from_db()
        self.assertEqual(second.password, password_before)
        self.assertEqual(self.sent.calls, [])


class RowFailureIsolationTests(RosterFixture):
    def test_a_database_error_in_one_row_fails_only_that_row_generically(self):
        real = roster_import._import_row_without_email

        def one_row_breaks(*, course, row):
            if row.first_name == "Ivy":
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT 1/0 AS {SENTINEL}")
            return real(course=course, row=row)

        with patch.object(
            roster_import, "_import_row_without_email", new=one_row_breaks
        ):
            with self.assertLogs("classrooms.services.roster_import", "ERROR"):
                rows = self.rows("first_name,last_name\nHan,Nine\nIvy,Ten\nJo,Eleven\n")

        self.assertEqual(rows[2]["reason_code"], "ROW_FAILED")
        self.assertEqual(rows[2]["message"], "Row 2: this student couldn't be added.")
        self.assertIs(rows[2]["retryable"], True)
        self.assertTrue(rows[2]["reference"])
        for leak in (SENTINEL, "division", "DataError", "Traceback"):
            self.assertNotIn(leak, str(rows[2]))
        self.assertEqual(
            [rows[1]["status"], rows[3]["status"]], ["enrolled", "enrolled"]
        )
        self.assertEqual(self.enrolled(), 2)

    def test_the_reference_is_the_requests_id(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, {"raw_data": "X,\n"})

        self.assertEqual(
            response.data["results"][0]["reference"], response["X-Request-ID"]
        )


class WholeRequestRefusalTests(RosterFixture):
    def envelope(self, response, status_code):
        self.assertEqual(response.status_code, status_code, response.content)
        return response.json()["error"]["field_errors"]

    def upload(self, data, name="roster.csv"):
        payload = io.BytesIO(data)
        payload.name = name
        return self.client.post(self.url, {"file": payload}, format="multipart")

    def test_no_input(self):
        for body in ({}, {"raw_data": ""}):
            with self.subTest(body=body):
                envelope = self.envelope(
                    self.client.post(self.url, body), status.HTTP_400_BAD_REQUEST
                )
                self.assertEqual(envelope["reason_code"], "ROSTER_NO_INPUT")
                self.assertEqual(
                    envelope["error"],
                    "Upload a roster file or paste your student list.",
                )

    def test_nothing_but_a_header_or_blank_rows_is_empty(self):
        """Q4: it used to be a 200 with an empty result."""
        for data in (b"first_name,last_name,email\n", b"first_name,last_name\n,\n,,\n"):
            with self.subTest(data=data):
                envelope = self.envelope(self.upload(data), status.HTTP_400_BAD_REQUEST)
                self.assertEqual(envelope["reason_code"], "ROSTER_EMPTY")
                self.assertEqual(envelope["error"], "This roster has no student rows.")

    def test_a_file_that_isnt_text(self):
        envelope = self.envelope(
            self.upload("first,last\nJosé,Núñez\n".encode("latin-1"), "pupils.csv"),
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(envelope["reason_code"], "ROSTER_FILE_UNREADABLE")
        self.assertEqual(envelope["params"], {"file_name": "pupils.csv"})
        self.assertEqual(envelope["error"], "pupils.csv isn't readable as text.")

    def test_too_many_rows(self):
        raw = "\n".join(f"First{i},Last{i}" for i in range(MAX_ROWS + 1))

        envelope = self.envelope(
            self.client.post(self.url, {"raw_data": raw}), status.HTTP_400_BAD_REQUEST
        )

        self.assertEqual(envelope["reason_code"], "ROSTER_TOO_MANY_ROWS")
        self.assertEqual(
            envelope["params"], {"row_count": MAX_ROWS + 1, "max_rows": MAX_ROWS}
        )
        self.assertEqual(self.enrolled(), 0)

    def test_too_large_is_a_413_with_int_sizes(self):
        data = b"a,b\n" * (MAX_FILE_BYTES // 4 + 1)

        envelope = self.envelope(
            self.upload(data), status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        )

        self.assertEqual(envelope["reason_code"], "FILE_TOO_LARGE")
        self.assertEqual(
            envelope["params"],
            {
                "file_name": "roster.csv",
                "actual": len(data),
                "limit": MAX_FILE_BYTES,
                "dimension": "bytes",
            },
        )

    def test_the_size_is_checked_before_the_file_is_read(self):
        class Unreadable:
            name = "big.csv"
            size = MAX_FILE_BYTES + 1

            def read(self, *args):
                raise AssertionError("read before the size check")

        from AutoGrader.uploads import PayloadTooLarge

        with self.assertRaises(PayloadTooLarge):
            read_rows(input_file=Unreadable())
