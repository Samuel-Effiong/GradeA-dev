"""Epic C1 ships dark: with TAGS_ENABLED off, every assignment response is
byte-identical to staging 8aafa47dd, before C1 (plan 11 §3.4, NFR-MNT-05).

The golden files in assignments/c1_snapshots/ were recorded on 8aafa47dd,
before any C1 change, with:

    C1_RECORD_SNAPSHOTS=1 python manage.py test \\
        assignments.tests_c1_dark_snapshot --settings=settings_worktree

Only UUIDs are replaced (by stable labels, since ids are random); every
other byte is the response's own: key order, spacing, values.
"""

import os
import re
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase, override_settings
from freezegun import freeze_time
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    Session,
    StudentCourse,
    Topic,
)
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

GOLDEN_DIR = Path(__file__).resolve().parent / "c1_snapshots"
RECORD = os.environ.get("C1_RECORD_SNAPSHOTS") == "1"
NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=dt_timezone.utc)
PASSWORD = "c1-snapshot-pass"  # pragma: allowlist secret
UUID_RE = re.compile(
    rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


@override_settings(TAGS_ENABLED=False)
class AssignmentResponsesUnchangedWhileDark(TestCase):
    maxDiff = None
    labels: dict[bytes, bytes]
    teacher: CustomUser
    student: CustomUser
    a1: Assignment
    a2: Assignment

    @classmethod
    def setUpTestData(cls):
        with freeze_time(NOW):
            cls.labels = {}

            def label(obj, name):
                cls.labels[str(obj.pk).lower().encode()] = f"<{name}>".encode()
                return obj

            def user(kind, first, last):
                return label(
                    CustomUser.objects.create_user(
                        email=f"{first.lower()}.{last.lower()}@c1snap.test",
                        password=PASSWORD,
                        user_type=kind,
                        first_name=first,
                        last_name=last,
                        is_active=True,
                    ),
                    f"user:{first}",
                )

            cls.teacher = user(UserTypes.TEACHER, "Tess", "Adeyemi")
            cls.student = user(UserTypes.STUDENT, "Sade", "Bello")
            session = label(
                Session.objects.create(name="Autumn", teacher=cls.teacher), "session"
            )
            course = label(
                Course.objects.create(
                    name="Biology", teacher=cls.teacher, session=session
                ),
                "course",
            )
            topic = label(Topic.objects.create(name="Cells", course=course), "topic")
            StudentCourse.objects.create(
                student=cls.student,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            cls.a1 = label(
                Assignment.objects.create(
                    title="Cell structure",
                    course=course,
                    topic=topic,
                    total_points=10,
                    due_date=NOW - timedelta(days=1),
                    status=AssignmentStatus.PUBLISHED,
                ),
                "a1",
            )
            cls.a2 = label(
                Assignment.objects.create(
                    title="Genetics basics",
                    course=course,
                    total_points=20,
                    due_date=NOW + timedelta(days=7),
                    status=AssignmentStatus.PUBLISHED,
                ),
                "a2",
            )
            label(
                StudentSubmission.objects.create(
                    student=cls.student,
                    assignment=cls.a1,
                    answers={},
                    score=Decimal("8"),
                    max_points=10,
                    graded_at=NOW,
                    is_published=True,
                ),
                "submission",
            )

    def setUp(self):
        cache.clear()
        self.freezer = freeze_time(NOW)
        self.freezer.start()
        self.addCleanup(self.freezer.stop)

    def check(self, name, user, path):
        cache.clear()
        client = APIClient()
        client.force_authenticate(user)
        response = client.get(f"/api/v1/{path}")
        self.assertEqual(response.status_code, 200, response.content[:400])
        body = UUID_RE.sub(
            lambda m: self.labels.get(m.group(0).lower(), b"<unlabelled-uuid>"),
            response.content,
        )
        golden = GOLDEN_DIR / f"{name}.json"
        if RECORD:
            GOLDEN_DIR.mkdir(exist_ok=True)
            golden.write_bytes(body + b"\n")
            return
        self.assertTrue(golden.exists(), f"no golden file {golden.name}")
        self.assertEqual(body, golden.read_bytes().rstrip(b"\n"), name)

    def test_teacher_list(self):
        self.check("teacher_list", self.teacher, "assignments")

    def test_teacher_detail(self):
        self.check("teacher_detail", self.teacher, f"assignments/{self.a1.pk}")

    def test_teacher_list_filtered_by_course(self):
        self.check(
            "teacher_list_by_course",
            self.teacher,
            f"assignments?course={self.a1.course_id}",
        )

    def test_student_list(self):
        self.check("student_list", self.student, "assignments")

    def test_student_detail(self):
        self.check("student_detail", self.student, f"assignments/{self.a1.pk}")
