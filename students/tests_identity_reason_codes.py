"""
FR-A-06 S6c: the identity codes #1/#2 (08a §1, §4.6; F2/F3).

A teacher's batch upload reads a student name off each paper. When it
can't be attributed, the item fails with its OWN code, instead of the one
uncoded CannotAssociateStudentError for three different situations:

  MISSING_STUDENT_NAME   no name on the paper; a name matching nobody on
                         the roster; a name matching more than one student
  STUDENT_NOT_ON_ROSTER  the name is one of THIS teacher's students who
                         isn't enrolled in this course (pending, withdrawn,
                         or in another of the teacher's courses)

The roster lookup is scoped to the teacher's own reachable courses. A
student of another teacher is never named (tenancy); the paper's own name
is the only name shown.

F3: DUPLICATE_SUBMISSION is defined but never raised. An upload that
overwrote an existing ungraded submission says so with an informational
replaced_existing: true in the item's result (the tracked task's meta,
served by the task-status endpoint).
"""

import ast
import io
import pathlib
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

import assignments.tasks as upload_tasks
from assignments.models import Assignment, AssignmentStatus
from assignments.tests_upload_task_retry_policy import payload
from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from students.exceptions import CannotAssociateStudentError
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    StudentSubmission,
)
from users.models import CustomUser, UserTypes

ENROLLED = EnrollmentStatusType.ENROLLED
PENDING = EnrollmentStatusType.PENDING
WITHDRAWN = EnrollmentStatusType.WITHDRAWN


def png_payload(name):
    buffer = io.BytesIO()
    Image.new("RGB", (160, 90), "white").save(buffer, format="PNG")
    return payload(name, buffer.getvalue(), "image/png")


class IdentityFixture:
    def build(self):
        cache.clear()
        school = School.objects.create(name="S6c School")
        elsewhere = School.objects.create(name="S6c Other School")
        self.teacher = self.user(
            "teacher", UserTypes.TEACHER, "Tess", "Teacher", school
        )
        # The SM's tenancy rule: a colleague in the SAME school, and a teacher
        # in another school. Neither one's students may ever be named.
        colleague = self.user("colleague", UserTypes.TEACHER, "Col", "League", school)
        stranger = self.user("stranger", UserTypes.TEACHER, "Str", "Anger", elsewhere)
        session = Session.objects.create(name="S6c", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Maths", teacher=self.teacher, session=session
        )
        self.other_course = Course.objects.create(
            name="Physics", teacher=self.teacher, session=session
        )
        same_school = Course.objects.create(
            name="Colleague's",
            teacher=colleague,
            session=Session.objects.create(name="C", teacher=colleague),
        )
        other_school = Course.objects.create(
            name="Stranger's",
            teacher=stranger,
            session=Session.objects.create(name="X", teacher=stranger),
        )
        self.assignment = Assignment.objects.create(
            title="Homework",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        self.ada = self.student("Ada", "Lovelace", (self.course, ENROLLED))
        self.student("Grace", "Hopper", (self.course, PENDING))
        self.student("Edsger", "Dijkstra", (self.course, WITHDRAWN))
        self.student("Alan", "Turing", (self.other_course, ENROLLED))
        # Enrolled HERE, with a namesake in the teacher's other course: the
        # unique enrolled match wins (the SM's rule).
        self.hedy = self.student("Hedy", "Lamarr", (self.course, ENROLLED))
        self.student("Hedy", "Lamarr", (self.other_course, PENDING), tag="2")
        self.linus = self.student(
            "Linus", "Torvalds", (same_school, ENROLLED), school=school
        )
        self.ken = self.student(
            "Ken", "Thompson", (other_school, ENROLLED), school=elsewhere
        )
        # Ambiguous on this roster: "Sam Effiong" fuzzy-matches both (two
        # EXACT namesakes can't both enrol in one course).
        self.student("Samuel", "Effiong", (self.course, ENROLLED))
        self.student("Samantha", "Effiong", (self.course, ENROLLED))
        # Two DIFFERENT out-of-roster Barbaras: naming either would be a guess.
        self.student("Barbara", "Liskov", (self.course, PENDING), tag="1")
        self.student("Barbara", "Liskov", (self.other_course, ENROLLED), tag="2")

    def user(self, key, user_type, first, last, school=None):
        return CustomUser.objects.create_user(
            email=f"s6c-{key}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=user_type,
            first_name=first,
            last_name=last,
            school=school,
        )

    def student(self, first, last, *enrolments, tag="", school=None):
        user = self.user(
            f"{first}-{last}{tag}".lower(), UserTypes.STUDENT, first, last, school
        )
        for course, status in enrolments:
            StudentCourse.objects.create(
                student=user, course=course, enrollment_status=status
            )
        return user

    def run_upload(self, student_name, file_name="p07.png", session=None):
        """One teacher batch item through the real task, extraction faked.
        Returns (result, the exception the item failed with or None, row)."""
        session = session or BatchUploadSession.objects.create(
            teacher=self.teacher,
            assignment=self.assignment,
            task_type=BatchUploadType.SUBMISSION,
            total_files=1,
        )
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.BATCH_ANSWER_UPLOAD,
            assignment=self.assignment,
            batch_session=session,
            file_name=file_name,
            celery_task_id=f"celery-{file_name}-{student_name}",
        )
        extracted = {
            "student_name": student_name,
            "answers": [{"question_number": 1, "answer_html": "<p>4</p>"}],
        }
        with patch(
            "students.services.ai_processor.extract_answer_with_retry",
            return_value=extracted,
        ), patch(
            "assignments.tasks.mark_processing_task_failure",
            wraps=upload_tasks.mark_processing_task_failure,
        ) as mark_failure:
            result = upload_tasks.upload_answers_engine_async.apply(
                args=(
                    str(self.assignment.id),
                    png_payload(file_name),
                    "prompt",
                    str(self.teacher.id),
                ),
                kwargs={
                    "processing_task_id": str(tracked.id),
                    "session_id": str(session.id),
                    "file_name": file_name,
                },
            ).get()
        refused = mark_failure.call_args.args[1] if mark_failure.call_args else None
        tracked.refresh_from_db()
        return result, refused, tracked


class TeacherBatchItemsFailWithTheirOwnIdentityCode(IdentityFixture, TestCase):
    def setUp(self):
        self.build()

    def assertRefused(self, student_name, code, **expected_params):
        result, refused, tracked = self.run_upload(student_name)

        self.assertEqual(result["status"], "FAILURE")
        self.assertIsInstance(refused, CannotAssociateStudentError)
        self.assertEqual(getattr(refused, "reason_code", None), code)
        self.assertEqual(refused.params["file_name"], "p07.png")
        for key, value in expected_params.items():
            self.assertEqual(refused.params[key], value)
        # The item shows the coded message, which names the file.
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(tracked.error, str(refused))
        self.assertIn("p07.png", str(refused))
        self.assertFalse(StudentSubmission.objects.exists())
        return refused

    # -- #1 MISSING_STUDENT_NAME ---------------------------------------------

    def test_no_name_on_the_paper(self):
        for blank in ("", "   "):
            with self.subTest(name=repr(blank)):
                refused = self.assertRefused(
                    blank,
                    ReasonCode.MISSING_STUDENT_NAME,
                    name_state="no name was found on the paper",
                )
        self.assertEqual(
            str(refused),
            "We couldn't match p07.png to a student: no name was found on the "
            "paper.",
        )

    def test_a_name_matching_nobody_quotes_the_name_read(self):
        self.assertRefused(
            "Nobody Known",
            ReasonCode.MISSING_STUDENT_NAME,
            name_state='the name "Nobody Known" doesn\'t match anyone on the roster',
        )

    def test_a_name_matching_two_enrolled_students_is_ambiguous(self):
        self.assertRefused(
            "Sam Effiong",
            ReasonCode.MISSING_STUDENT_NAME,
            name_state='the name "Sam Effiong" matches more than one student',
        )

    # -- #2 STUDENT_NOT_ON_ROSTER --------------------------------------------

    def test_a_pending_student_of_this_course_is_not_on_the_roster(self):
        refused = self.assertRefused(
            "Grace Hopper",
            ReasonCode.STUDENT_NOT_ON_ROSTER,
            student_display="Grace Hopper",
        )
        self.assertEqual(
            str(refused),
            "p07.png belongs to Grace Hopper, who isn't enrolled in this course.",
        )
        self.assertIn(
            "roster", REASON_CODES[ReasonCode.STUDENT_NOT_ON_ROSTER].remediation
        )

    def test_a_withdrawn_student_is_not_on_the_roster(self):
        self.assertRefused(
            "edsger dijkstra",
            ReasonCode.STUDENT_NOT_ON_ROSTER,
            student_display="Edsger Dijkstra",
        )

    def test_a_student_from_another_of_the_teachers_courses_is_not_on_the_roster(
        self,
    ):
        self.assertRefused(
            "Alan Turing",
            ReasonCode.STUDENT_NOT_ON_ROSTER,
            student_display="Alan Turing",
        )

    def assertNothingAbout(self, refused, student):
        """Nothing stored about `student` reaches the message or params: not
        their name as stored, email, email local part or id."""
        shown = str(refused) + repr(refused.params)
        for leak in (
            f"{student.first_name} {student.last_name}",
            student.email,
            student.email.split("@")[0],
            str(student.id),
        ):
            self.assertNotIn(leak, shown)

    def test_a_colleagues_student_in_the_same_school_is_never_named(self):
        # Tenancy (SM): only the uploading teacher's own courses, never the
        # school. The paper reads "linus torvalds" (its own casing); only
        # that text may appear, never the stored "Linus Torvalds".
        refused = self.assertRefused(
            "linus torvalds",
            ReasonCode.MISSING_STUDENT_NAME,
            name_state=(
                'the name "linus torvalds" doesn\'t match anyone on the roster'
            ),
        )
        self.assertNotIn("student_display", refused.params)
        self.assertNothingAbout(refused, self.linus)

    def test_another_schools_student_is_never_named(self):
        refused = self.assertRefused(
            "ken thompson",
            ReasonCode.MISSING_STUDENT_NAME,
            name_state='the name "ken thompson" doesn\'t match anyone on the roster',
        )
        self.assertNotIn("student_display", refused.params)
        self.assertNothingAbout(refused, self.ken)

    def test_two_out_of_roster_matches_are_not_a_guess(self):
        refused = self.assertRefused(
            "Barbara Liskov",
            ReasonCode.MISSING_STUDENT_NAME,
            name_state=(
                'the name "Barbara Liskov" doesn\'t match anyone on the roster'
            ),
        )
        self.assertNotIn("student_display", refused.params)

    # -- an enrolled student is still attributed ------------------------------

    def test_an_enrolled_student_is_attributed(self):
        result, refused, tracked = self.run_upload("Ada Lovelace")

        self.assertEqual(result["status"], "SUCCESS", result)
        self.assertIsNone(refused)
        submission = StudentSubmission.objects.get()
        self.assertEqual(submission.student, self.ada)

    def test_a_unique_enrolled_match_wins_over_an_off_roster_namesake(self):
        result, refused, _ = self.run_upload("Hedy Lamarr")

        self.assertEqual(result["status"], "SUCCESS", result)
        self.assertIsNone(refused)
        self.assertEqual(StudentSubmission.objects.get().student, self.hedy)


class AnOverwriteSaysSoInTheItemResult(IdentityFixture, TestCase):
    """F3 (SM addition): the silent overwrite of an ungraded submission
    stays, but the item that did it carries replaced_existing: true."""

    def setUp(self):
        self.build()

    def test_the_first_upload_replaced_nothing(self):
        _, _, tracked = self.run_upload("Ada Lovelace", file_name="first.png")

        self.assertEqual(tracked.status, BackgroundTaskStatus.SUCCESS)
        self.assertIs(tracked.meta.get("replaced_existing"), False)

    def test_a_second_upload_for_the_same_student_replaced_the_first(self):
        self.run_upload("Ada Lovelace", file_name="first.png")
        _, _, tracked = self.run_upload("Ada Lovelace", file_name="second.png")

        self.assertEqual(tracked.status, BackgroundTaskStatus.SUCCESS)
        self.assertIs(tracked.meta.get("replaced_existing"), True)
        self.assertEqual(StudentSubmission.objects.count(), 1)

        # Served where the teacher reads an item's result today.
        client = APIClient()
        client.force_authenticate(user=self.teacher)
        response = client.get(
            reverse("task-task-status", kwargs={"task_id": tracked.celery_task_id})
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertIn("'replaced_existing': True", response.json()["data"]["meta"])


class OneUnattributablePaperDoesNotStopTheOthers(IdentityFixture, TestCase):
    def setUp(self):
        self.build()

    def test_the_other_items_of_the_batch_still_succeed(self):
        session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            assignment=self.assignment,
            task_type=BatchUploadType.SUBMISSION,
            total_files=2,
        )
        failed, refused, _ = self.run_upload("Grace Hopper", "a.png", session)
        succeeded, _, _ = self.run_upload("Ada Lovelace", "b.png", session)

        self.assertEqual(failed["status"], "FAILURE")
        self.assertEqual(refused.reason_code, ReasonCode.STUDENT_NOT_ON_ROSTER)
        self.assertEqual(succeeded["status"], "SUCCESS")

        client = APIClient()
        client.force_authenticate(user=self.teacher)
        response = client.get(
            reverse("task-session-results", kwargs={"session_id": str(session.id)})
        )
        data = response.json()["data"]
        self.assertEqual((data["success_count"], data["failure_count"]), (1, 1))
        [failure] = data["failure_list"]
        self.assertEqual(failure["error"], str(refused))


class DuplicateSubmissionIsDefinedButNeverRaised(TestCase):
    """F3: kept in the catalogue for the frontend's keep/replace buttons, but
    no production code raises it in Epic A."""

    def test_no_production_module_uses_the_code(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        allowed = {"audit/enums.py", "AutoGrader/reason_codes.py"}
        users = []
        for path in root.rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if (
                rel in allowed
                or "/migrations/" in rel
                or rel.startswith(("docs/", "."))
                or "/tests" in rel
                or pathlib.PurePosixPath(rel).name.startswith("test")
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "DUPLICATE_SUBMISSION" not in text:
                continue
            for node in ast.walk(ast.parse(text)):
                name = getattr(node, "attr", None) or getattr(node, "value", None)
                if name == "DUPLICATE_SUBMISSION":
                    users.append(rel)
        self.assertEqual(users, [])
        self.assertIn(ReasonCode.DUPLICATE_SUBMISSION, REASON_CODES)
