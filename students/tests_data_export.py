"""NFR-CMP-04 step 2: a student's data export (SM go 2026-11-08).

`GET students/<id>/data-export` answers a zip of the student's own data,
built from `students.student_data_map` and the field lists in
`students.data_export`. The rulings pinned here are in
docs/evidence/nfr-cmp-04-student-data/NOTES.md, "Step 2":

* D2: no self-service. D6: a JSON zip.
* Who may call (one place, `EXPORT_CALLERS`): a real super admin (any
  student), a school admin (a student of their own school; never when
  either school is empty, R6), and a teacher for the courses they own
  (R5, owner 2026-11-08: `TEACHER_EXPORT_ALLOWED`, on; a course has one
  teacher, its owner). Any other caller, target or id gets json_404, the
  same answer as an unknown URL.
* Q1 (H-130 parity): a grade the teacher has not released is not in the
  export, in any form. Q2: no review flags. Q3: an unreadable profile
  image does not stop the export; it is logged and listed as missing.
* Every name in the zip is the owner's approved wording (2026-11-08,
  team/approved_wording_student_data_2026-11-08.md), pinned verbatim in
  `APPROVED_NAMES` and `APPROVED_README`. The other tests read names from
  `EXPORT_NAMES`. The profile picture keeps its stored file's extension.
"""

import importlib
import io
import json
import zipfile
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from ai_processor.models import ChatMessage, ChatSession
from audit.enums import AuditAction
from audit.metadata import ALLOWED_KEYS, METADATA_ALLOWLIST
from audit.models import AuditEvent
from audit.tests_route_coverage import write_routes
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    SessionOwnerType,
    StudentCourse,
)
from dashboard.models import StudentRiskAlertState
from students.models import BackgroundProcessingTask, StudentSubmission
from students.student_data_map import STUDENT_DATA_MAP
from tags._c1_test_fixtures import World, client_for
from users.models import (
    PasswordChangeOTP,
    PasswordResetOTP,
    Settings,
    UserActivity,
    UserGoogleCredentials,
    UserTypes,
)

# Not built yet when the expectations are committed (red first).
data_export = importlib.import_module("students.data_export")

API = "/api/v1/"
flag_on = override_settings(STUDENT_DATA_TOOLS_ENABLED=True)
flag_off = override_settings(STUDENT_DATA_TOOLS_ENABLED=False)

# Secret values planted on the student; none may appear in the zip.
SECRETS = {
    "token": "a" * 39 + "1",
    "reset_otp": "804512",
    "change_otp": "771903",
    "google_access": "ya29.planted-google-access-token",
    "google_refresh": "1//planted-google-refresh-token",
    "outstanding": "planted.outstanding.jwt-token-value",
    "activation": "planted-activation-token-value",
}
UNPUBLISHED_FEEDBACK = "UNRELEASED-FEEDBACK-must-not-leave"
OTHER_STUDENT_ANSWER = "OTHER-STUDENT-ANSWER-must-not-leave"
OTHER_STUDENT_CHAT = "OTHER-STUDENT-CHAT-must-not-leave"
IMAGE_BYTES = b"\x89PNG\r\n\x1a\nplanted-image-bytes"
#: The profile picture's name in the zip: the approved base plus the stored
#: file's extension (the fixture stores face.png).
PICTURE = "profile-picture.png"

#: The owner's approved wording, verbatim (2026-11-08).
APPROVED_NAMES = {
    "download": "student-data.zip",
    "readme": "README.txt",
    "manifest": "manifest.json",
    "profile": "profile.json",
    "profile_image": "profile-picture",
    "classrooms.StudentCourse": "enrollments.json",
    "students.StudentSubmission": "submissions.json",
    "dashboard.StudentRiskAlertState": "at-risk-status.json",
    "students.BackgroundProcessingTask": "file-uploads.json",
    "ai_processor.ChatSession": "assistant-chats.json",
    "users.UserActivity": "activity.json",
    "users.Settings": "settings.json",
    "messages": "messages",
    "manifest_counts": "record_counts",
    "manifest_scope": "scope",
    "manifest_generated_at": "created_at",
    "manifest_missing": "missing_files",
}
APPROVED_README = (
    "This download holds the data we keep about one student, as of the time"
    " in manifest.json.\n"
    "Each .json file lists one kind of record: enrollments, submissions,"
    " at-risk status, file uploads, assistant chats, activity and settings."
    " profile.json holds the student's account details, and profile-picture"
    " is their profile picture, if they set one.\n"
    "If a teacher made this download, it covers only that teacher's"
    " courses.\n"
    "A grade appears only after the teacher has released it.\n"
    "Sign-in and security details are never included.\n"
    "If a file is listed under missing_files in manifest.json, it couldn't be"
    " read when this download was made. Try again later.\n"
)


def url(student_id):
    return f"{API}students/{student_id}/data-export"


def unzip(response):
    """{name: bytes} of the zip a response carries."""
    archive = zipfile.ZipFile(io.BytesIO(b"".join(response_bytes(response))))
    return {name: archive.read(name) for name in archive.namelist()}


def response_bytes(response):
    if getattr(response, "streaming", False):
        return list(response.streaming_content)
    return [response.content]


def name(key):
    return data_export.EXPORT_NAMES[key]


def on_a_school_session(world, teacher, course):
    """H-38: the course sits in a SCHOOL session of the student's school and
    the teacher is a member of that school."""
    session = Session.objects.create(
        name=f"School term {world.uid()}",
        owner_type=SessionOwnerType.SCHOOL,
        school=world.school,
    )
    Course.objects.filter(pk=course.pk).update(session=session)
    get_user_model().objects.filter(pk=teacher.pk).update(school=world.school)
    teacher.refresh_from_db()


def remove_from_school(teacher):
    get_user_model().objects.filter(pk=teacher.pk).update(school=None)
    teacher.refresh_from_db()


class _World(World):
    """C1's world plus what a student's export reads."""

    def build(self):
        super().build()
        self.student.school = self.school
        self.student.activation_token = SECRETS["activation"]
        self.student.bio = "Likes cells"
        self.student.save()
        self.other_school = School.objects.create(name=f"Other {self.uid()}")
        self.other_admin = self.user(
            UserTypes.SCHOOL_ADMIN, "Ola", school=self.other_school
        )
        self.schoolless_admin = self.user(UserTypes.SCHOOL_ADMIN, "Nia")
        # The second student shares the course, an assignment and a chat
        # course: none of their rows may reach the first one's export.
        self.classmate = self.user(UserTypes.STUDENT, "Kemi", school=self.school)
        StudentCourse.objects.create(
            student=self.classmate,
            course=self.course_a,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        # The student is also in teacher B's course (another teacher).
        StudentCourse.objects.create(
            student=self.student,
            course=self.course_b,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        now = timezone.now()
        self.published = StudentSubmission.objects.create(
            assignment=self.a1,
            student=self.student,
            answers={"1": "Mitochondria"},
            score=Decimal("8"),
            score_percentage=Decimal("80"),
            max_points=10,
            feedback={"overall_performance_analysis": {"summary": "Good work"}},
            graded_at=now,
            is_published=True,
        )
        self.unpublished = StudentSubmission.objects.create(
            assignment=self.a2,
            student=self.student,
            answers={"1": "Alleles"},
            score=Decimal("3"),
            score_percentage=Decimal("30"),
            max_points=7,
            feedback={
                "overall_performance_analysis": {"summary": UNPUBLISHED_FEEDBACK}
            },
            graded_at=now,
            is_published=False,
        )
        self.in_b = StudentSubmission.objects.create(
            assignment=self.b1, student=self.student, answers={"1": "Newton"}
        )
        self.classmates = StudentSubmission.objects.create(
            assignment=self.a1,
            student=self.classmate,
            answers={"1": OTHER_STUDENT_ANSWER},
        )
        self.chat_a = ChatSession.objects.create(
            user=self.student, course=self.course_a
        )
        ChatMessage.objects.create(session=self.chat_a, role="user", content="Hi A")
        self.chat_b = ChatSession.objects.create(
            user=self.student, course=self.course_b
        )
        ChatMessage.objects.create(session=self.chat_b, role="user", content="Hi B")
        other_chat = ChatSession.objects.create(
            user=self.classmate, course=self.course_a
        )
        ChatMessage.objects.create(
            session=other_chat, role="user", content=OTHER_STUDENT_CHAT
        )
        UserActivity.objects.create(user=self.student)
        UserActivity.objects.create(user=self.classmate)
        Settings.objects.filter(user=self.student).update(theme="dark")
        StudentRiskAlertState.objects.create(
            student=self.student, school=self.school, is_at_risk=True
        )
        BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            assignment=self.a1,
            submission=self.published,
            file_name="cells.pdf",
        )
        # Sign-in and security data: never exported.
        Token.objects.create(user=self.student, key=SECRETS["token"])
        PasswordResetOTP.objects.create(user=self.student, code=SECRETS["reset_otp"])
        PasswordChangeOTP.objects.create(user=self.student, code=SECRETS["change_otp"])
        UserGoogleCredentials.objects.create(
            user=self.student,
            access_token=SECRETS["google_access"],
            refresh_token=SECRETS["google_refresh"],
            token_expiry=now,
        )
        OutstandingToken.objects.create(
            user=self.student,
            jti="planted-jti",
            token=SECRETS["outstanding"],
            expires_at=now,
        )
        return self


class _ExportCase(TestCase):
    def setUp(self):
        self.storage = InMemoryStorage()
        field = get_user_model()._meta.get_field("profile_image")
        patcher = mock.patch.object(field, "storage", self.storage)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.w = _World().build()
        self.w.student.profile_image.save(
            "face.png", ContentFile(IMAGE_BYTES), save=True
        )

    def export(self, caller, student=None):
        target = student or self.w.student
        return client_for(caller).get(url(target.pk))

    def files(self, caller=None, student=None):
        response = self.export(caller or self.w.superadmin, student)
        self.assertEqual(response.status_code, 200, response_bytes(response)[:1])
        return unzip(response)

    def rows(self, files, label):
        return json.loads(files[name(label)])

    def assertNotFound(self, response):
        """The same json_404 as an unknown URL, and as an unknown student."""
        unknown_url = client_for(None).get(f"{API}no-such-route-anywhere")
        unknown_id = client_for(self.w.superadmin).get(url(self.w.course_a.pk))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, unknown_url.content)
        self.assertEqual(unknown_id.content, unknown_url.content)


def _exported_models():
    """Every model whose rows go into the export, with ChatMessage."""
    labels = {
        label
        for (label, _), entry in STUDENT_DATA_MAP.items()
        if entry.export and entry.match != "OWN_ROW"
    }
    return labels | {"users.CustomUser", "ai_processor.ChatMessage"}


class FieldListGuardTests(TestCase):
    """Every field of every exported model is decided: exported,
    conditional (until published) or left out with a reason."""

    def test_every_field_is_decided_exactly_once(self):
        for label in sorted(_exported_models()):
            with self.subTest(label=label):
                model = apps.get_model(label)
                every = {f.name for f in model._meta.concrete_fields}
                out = set(data_export.EXPORT_FIELDS.get(label, ()))
                cond = set(data_export.EXPORT_CONDITIONAL.get(label, ()))
                left = set(data_export.EXPORT_OMITTED.get(label, {}))
                self.assertFalse(out & cond, label)
                self.assertFalse(out & left, label)
                self.assertFalse(cond & left, label)
                self.assertEqual(out | cond | left, every, label)

    def test_every_left_out_field_says_why(self):
        for label, fields in data_export.EXPORT_OMITTED.items():
            for field_name, why in fields.items():
                with self.subTest(field=(label, field_name)):
                    self.assertTrue(why.strip())

    def test_no_field_list_for_a_table_that_is_not_exported(self):
        not_exported = {
            label for (label, _), e in STUDENT_DATA_MAP.items() if not e.export
        }
        for registry in (
            data_export.EXPORT_FIELDS,
            data_export.EXPORT_CONDITIONAL,
            data_export.EXPORT_OMITTED,
        ):
            with self.subTest(registry=sorted(registry)[:1]):
                self.assertFalse(set(registry) & not_exported)

    def test_the_q1_q2_declarations(self):
        sub = "students.StudentSubmission"
        self.assertEqual(
            set(data_export.EXPORT_CONDITIONAL[sub]),
            {
                "score",
                "score_percentage",
                "feedback",
                "formatted_grade",
                "graded_at",
                "was_regraded",
                "regraded_at",
            },
        )
        for field_name in (
            "ai_score",
            "ai_feedback",
            "ai_graded_at",
            "needs_review",
            "review_reasons",
            "review_severity",
            "review_tier",
            "pending_result",
            "pending_graded_at",
        ):
            with self.subTest(field=field_name):
                self.assertIn(field_name, data_export.EXPORT_OMITTED[sub])
        for field_name in ("ai_summary", "ai_summary_generated_at"):
            with self.subTest(field=field_name):
                self.assertIn(
                    field_name, data_export.EXPORT_OMITTED["classrooms.StudentCourse"]
                )

    def test_no_secret_field_is_exported(self):
        user = data_export.EXPORT_FIELDS["users.CustomUser"]
        for field_name in (
            "password",
            "activation_token",
            "token_epoch",
            "failed_login_attempts",
            "locked_until",
        ):
            with self.subTest(field=field_name):
                self.assertNotIn(field_name, user)

    def test_names_are_the_approved_wording(self):
        self.assertTrue(data_export.EXPORT_NAMES_APPROVED)
        self.assertEqual(data_export.EXPORT_NAMES, APPROVED_NAMES)
        self.assertEqual(data_export.README_TEXT, APPROVED_README)
        names = list(data_export.EXPORT_NAMES.values())
        self.assertEqual(len(names), len(set(names)))


@flag_on
class ExportContentTests(_ExportCase):
    def test_every_exported_table_has_its_file_even_when_empty(self):
        ChatSession.objects.filter(user=self.w.student).delete()
        files = self.files()
        for (label, _), entry in STUDENT_DATA_MAP.items():
            if not entry.export or entry.match == "OWN_ROW":
                continue
            with self.subTest(label=label):
                self.assertIn(name(label), files)
                self.assertIsInstance(self.rows(files, label), list)
        self.assertEqual(self.rows(files, "ai_processor.ChatSession"), [])

    def test_every_file_is_json_except_the_image(self):
        files = self.files()
        for file_name, content in files.items():
            if file_name in (PICTURE, name("readme")):
                continue
            with self.subTest(file=file_name):
                json.loads(content)

    def test_the_profile_picture_keeps_its_extension(self):
        self.assertEqual(self.files()[PICTURE], IMAGE_BYTES)

    def test_a_picture_without_an_extension_has_none(self):
        # The stored name is "<email name>_<date>_<ext>": with a dot in the
        # email name and no extension, a plain splitext would find a false
        # one (".ext_<date>_").
        self.w.student.email = "no.ext@example.com"
        self.w.student.profile_image.save("face", ContentFile(IMAGE_BYTES), save=True)
        files = self.files()
        self.assertEqual(files["profile-picture"], IMAGE_BYTES)
        self.assertNotIn(PICTURE, files)

    def test_no_sign_in_or_security_value_is_in_the_zip(self):
        files = self.files()
        blob = b"".join(files.values())
        hashed = get_user_model().objects.get(pk=self.w.student.pk).password
        for label, secret in list(SECRETS.items()) + [("password", hashed)]:
            with self.subTest(secret=label):
                self.assertNotIn(secret.encode(), blob)

    def test_only_the_students_own_rows(self):
        files = self.files()
        blob = b"".join(files.values())
        for value in (
            OTHER_STUDENT_ANSWER,
            OTHER_STUDENT_CHAT,
            str(self.w.classmate.pk),
            str(self.w.classmates.pk),
        ):
            with self.subTest(value=value):
                self.assertNotIn(value.encode(), blob)
        self.assertEqual(
            {row["id"] for row in self.rows(files, "students.StudentSubmission")},
            {str(self.w.published.pk), str(self.w.unpublished.pk), str(self.w.in_b.pk)},
        )
        self.assertEqual(len(self.rows(files, "users.UserActivity")), 1)

    def test_chat_messages_are_nested_under_their_session(self):
        sessions = self.rows(self.files(), "ai_processor.ChatSession")
        texts = sorted(
            message["content"] for s in sessions for message in s[name("messages")]
        )
        self.assertEqual(texts, ["Hi A", "Hi B"])

    def test_a_grade_not_yet_released_is_not_in_the_export(self):  # Q1
        files = self.files()
        rows = {r["id"]: r for r in self.rows(files, "students.StudentSubmission")}
        hidden = rows[str(self.w.unpublished.pk)]
        for field_name in data_export.EXPORT_CONDITIONAL["students.StudentSubmission"]:
            with self.subTest(field=field_name):
                self.assertIsNone(hidden[field_name])
        # H-133: the assignment's total, not the row's own maximum (7).
        self.assertEqual(hidden["max_points"], self.w.a2.total_points)
        blob = b"".join(files.values())
        self.assertNotIn(UNPUBLISHED_FEEDBACK.encode(), blob)
        self.assertNotIn(b"ai_score", blob)

    def test_a_released_grade_is_in_the_export(self):  # Q1
        rows = {
            r["id"]: r for r in self.rows(self.files(), "students.StudentSubmission")
        }
        shown = rows[str(self.w.published.pk)]
        self.assertEqual(Decimal(shown["score"]), Decimal("8"))
        self.assertEqual(Decimal(shown["score_percentage"]), Decimal("80"))
        self.assertIsNotNone(shown["graded_at"])
        self.assertEqual(shown["max_points"], 10)
        self.assertIn("Good work", json.dumps(shown["feedback"]))

    def test_the_final_grade_counts_released_work_only(self):  # Q1
        StudentCourse.objects.filter(
            student=self.w.student, course=self.w.course_a
        ).update(final_grade=Decimal("55.00"))
        rows = self.rows(self.files(), "classrooms.StudentCourse")
        grade = {r["course"]: r["final_grade"] for r in rows}[str(self.w.course_a.pk)]
        self.assertEqual(Decimal(grade), Decimal("80.00"))

    def test_the_profile_holds_the_listed_fields_only(self):
        profile = json.loads(self.files()[name("profile")])
        self.assertEqual(
            set(profile), set(data_export.EXPORT_FIELDS["users.CustomUser"])
        )
        self.assertEqual(profile["email"], self.w.student.email)

    def test_an_unreadable_image_is_listed_as_missing(self):  # Q3
        with mock.patch.object(
            self.storage, "open", side_effect=OSError("storage down")
        ), self.assertLogs("students.data_export", level="ERROR"):
            files = self.files()
        self.assertNotIn(PICTURE, files)
        manifest = json.loads(files[name("manifest")])
        self.assertEqual(manifest[name("manifest_missing")], [PICTURE])
        self.assertIn(name("profile"), files)


@flag_on
class CallerMatrixTests(_ExportCase):
    def test_a_super_admin_may_export_any_student(self):
        self.assertEqual(self.export(self.w.superadmin).status_code, 200)

    def test_an_account_that_is_not_a_real_super_admin_is_refused(self):
        half = self.w.user(UserTypes.SUPER_ADMIN, "Hal")  # no is_superuser
        flagged = self.w.user(UserTypes.TEACHER, "Fay", is_superuser=True)
        for caller in (half, flagged):
            with self.subTest(caller=caller.email):
                self.assertNotFound(self.export(caller))

    def test_a_school_admin_may_export_a_student_of_their_school(self):
        self.assertEqual(self.export(self.w.school_admin).status_code, 200)

    def test_a_school_admins_export_is_complete(self):  # Q4
        files = self.files(self.w.school_admin)
        ids = {r["id"] for r in self.rows(files, "students.StudentSubmission")}
        self.assertIn(str(self.w.in_b.pk), ids)

    def test_a_school_admin_of_another_school_is_refused(self):
        self.assertNotFound(self.export(self.w.other_admin))

    def test_a_school_admin_with_no_school_is_refused(self):  # R6
        self.assertNotFound(self.export(self.w.schoolless_admin))

    def test_a_student_with_no_school_is_refused_to_a_school_admin(self):  # R6
        get_user_model().objects.filter(pk=self.w.student.pk).update(school=None)
        self.assertNotFound(self.export(self.w.school_admin))
        self.assertEqual(self.export(self.w.superadmin).status_code, 200)

    def test_the_student_may_not_export_themselves(self):  # D2
        self.assertNotFound(self.export(self.w.student))

    def test_another_student_is_refused(self):
        self.assertNotFound(self.export(self.w.classmate))

    def test_teacher_export_is_on(self):  # R5, owner 2026-11-08
        self.assertTrue(data_export.TEACHER_EXPORT_ALLOWED)

    @mock.patch.object(data_export, "TEACHER_EXPORT_ALLOWED", False)
    def test_the_one_switch_turns_teachers_off(self):
        self.assertNotFound(self.export(self.w.teacher))

    def test_the_target_must_be_a_student(self):
        self.assertNotFound(self.export(self.w.superadmin, student=self.w.teacher))
        self.assertNotFound(self.export(self.w.superadmin, student=self.w.school_admin))

    def test_a_refusal_writes_no_event(self):
        self.export(self.w.other_admin)
        self.assertFalse(
            AuditEvent.objects.filter(action=data_export.EXPORT_AUDIT_ACTION).exists()
        )

    def test_anonymous_gets_the_unknown_url_answer(self):  # SM (2)
        # Not 401: that would tell an anonymous caller the route exists.
        self.assertNotFound(client_for(None).get(url(self.w.student.pk)))

    def test_a_bad_token_gets_the_unknown_url_answer(self):  # SM (2)
        client = client_for(None)
        client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        self.assertNotFound(client.get(url(self.w.student.pk)))

    def test_head_is_refused_and_writes_no_event(self):  # SM (1)
        # HEAD would run get(): build the zip and record an export that
        # nobody downloaded.
        response = client_for(self.w.superadmin).head(url(self.w.student.pk))
        self.assertEqual(response.status_code, 405)
        self.assertFalse(
            AuditEvent.objects.filter(action=data_export.EXPORT_AUDIT_ACTION).exists()
        )


@flag_on
class TeacherTests(_ExportCase):
    """R5 (owner, 2026-11-08): a teacher exports the student's data in the
    courses they own, and nothing else."""

    def test_a_teacher_gets_their_own_courses_only(self):
        files = self.files(self.w.teacher)
        subs = {r["id"] for r in self.rows(files, "students.StudentSubmission")}
        self.assertEqual(subs, {str(self.w.published.pk), str(self.w.unpublished.pk)})
        courses = {r["course"] for r in self.rows(files, "classrooms.StudentCourse")}
        self.assertEqual(courses, {str(self.w.course_a.pk)})
        chats = {r["course"] for r in self.rows(files, "ai_processor.ChatSession")}
        self.assertEqual(chats, {str(self.w.course_a.pk)})

    def test_a_teacher_gets_no_data_outside_courses(self):
        files = self.files(self.w.teacher)
        for label in ("users.UserActivity", "users.Settings"):
            with self.subTest(label=label):
                self.assertNotIn(name(label), files)
        self.assertNotIn(PICTURE, files)
        profile = json.loads(files[name("profile")])
        self.assertEqual(set(profile), set(data_export.TEACHER_PROFILE_FIELDS))

    def test_a_student_outside_the_teachers_courses_is_refused(self):
        StudentCourse.objects.filter(
            student=self.w.student, course=self.w.course_a
        ).delete()
        self.assertNotFound(self.export(self.w.teacher))

    def test_a_teacher_of_another_course_in_the_same_school_is_a_stranger(self):
        # Same school as the student, a course of their own, the student not
        # in it: the same json_404 as a teacher from nowhere.
        colleague = self.w.user(UserTypes.TEACHER, "Col", school=self.w.school)
        self.w.course(colleague, "Chemistry")
        stranger = self.w.user(UserTypes.TEACHER, "Str")
        self.assertNotFound(self.export(colleague))
        self.assertNotFound(self.export(stranger))
        self.assertEqual(self.export(colleague).content, self.export(stranger).content)

    def test_the_other_courses_teacher_sees_only_that_course(self):
        files = self.files(self.w.other_teacher)
        subs = {r["id"] for r in self.rows(files, "students.StudentSubmission")}
        self.assertEqual(subs, {str(self.w.in_b.pk)})

    def test_a_teacher_removed_from_the_school_loses_export_reach(self):  # H-38
        on_a_school_session(self.w, self.w.teacher, self.w.course_a)
        self.assertEqual(self.export(self.w.teacher).status_code, 200)
        remove_from_school(self.w.teacher)
        self.assertNotFound(self.export(self.w.teacher))

    def test_a_teachers_export_says_its_scope(self):
        self.export(self.w.teacher)
        event = AuditEvent.objects.get(action=data_export.EXPORT_AUDIT_ACTION)
        self.assertEqual(event.metadata["scope"], "courses")


@flag_on
class AuditTests(_ExportCase):
    def test_one_event_per_export_naming_no_one(self):
        self.export(self.w.school_admin)
        events = AuditEvent.objects.filter(action=data_export.EXPORT_AUDIT_ACTION)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.target_id, self.w.student.pk)
        self.assertEqual(event.actor_id, self.w.school_admin.pk)
        self.assertEqual(event.action, AuditAction.DATA_EXPORT)  # SM (1)
        self.assertEqual(event.target_type, "CustomUser")  # SM G3
        self.assertEqual(
            set(event.metadata),
            {"file_count", "file_size_bytes", "item_count", "scope"},
            event.metadata,
        )
        self.assertNotIn(self.w.student.email, json.dumps(event.metadata))
        self.assertEqual(event.metadata["scope"], "school")

    def test_the_scope_key_passes_the_allow_list(self):  # SM (1)
        allowed = METADATA_ALLOWLIST[AuditAction.DATA_EXPORT]
        self.assertEqual(
            allowed, {"file_count", "file_size_bytes", "item_count", "scope"}
        )
        self.assertTrue(allowed <= ALLOWED_KEYS)


class FlagTests(_ExportCase):
    @flag_off
    def test_off_by_default_everyone_gets_the_unknown_url_answer(self):
        unknown = client_for(None).get(f"{API}no-such-route-anywhere")
        for caller in (None, self.w.superadmin, self.w.school_admin, self.w.student):
            with self.subTest(caller=getattr(caller, "email", "anonymous")):
                response = client_for(caller).get(url(self.w.student.pk))
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.content, unknown.content)

    def test_the_setting_defaults_to_off(self):
        from django.conf import settings

        self.assertIs(getattr(settings, "STUDENT_DATA_TOOLS_ENABLED", None), False)

    def test_the_route_is_read_only(self):
        # The write-route sweep (and CONCEALED_ROUTES) covers write routes;
        # this one answers GET only, so there is nothing for it to miss.
        names = {name for name, _method in write_routes()}
        self.assertNotIn("student-data-export", names)
