"""Epic E1a: response snapshots (plan 09 §4.3, R-2 "no number moves").

Every endpoint and service that carries a grade figure is called on one
fixed fixture, and its output is compared byte for byte with a golden file
recorded on staging 2f00c078, BEFORE any grade site moved into
`classrooms.grade_service`. A planted formula change must turn a snapshot
red (mutation M8).

The fixture runs under a frozen clock, and every UUID in the output is
replaced by a stable label (the object's kind and name). Lists of objects
are put in one canonical order, because several endpoints break ties by
UUID, which is random per run; row ORDER is not a grade figure, and every
value in every row is still compared. Nothing else is normalized.

To re-record (only on a commit whose figures are known good; never to make
a red snapshot green after a site move):

    E1_RECORD_SNAPSHOTS=1 python manage.py test \
        classrooms.tests_e1_snapshots --settings=settings_worktree

Golden files: classrooms/e1_snapshots/<name>.json
"""

import json
import os
import re
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from pathlib import Path

from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.test import TestCase, override_settings
from freezegun import freeze_time
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus, AssignmentTypes
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    SessionOwnerType,
    StudentCourse,
    Topic,
)
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
GOLDEN_DIR = Path(__file__).resolve().parent / "e1_snapshots"
RECORD = os.environ.get("E1_RECORD_SNAPSHOTS") == "1"
NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=dt_timezone.utc)
PASSWORD = "e1-snapshot-pass"  # pragma: allowlist secret
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def _days_ago(days, hour=12):
    return NOW - timedelta(days=days) + timedelta(hours=hour - 12)


class Fixture:
    """One school, two teachers, three courses, eight students, and every
    edge row from plan 09 §4.1. Everything is named, so every UUID gets a
    readable, stable label."""

    def __init__(self):
        self.labels = {}

    def label(self, obj, name):
        self.labels[str(obj.pk).lower()] = name
        return obj

    def user(self, kind, name, *, school=None, **extra):
        user = CustomUser.objects.create_user(
            email=f"{name.lower()}@e1-snapshot.test",
            password=PASSWORD,
            user_type=kind,
            first_name=name,
            last_name="Snap",
            school=school,
            is_active=True,
            **extra,
        )
        return self.label(user, f"user:{name}")

    def build(self):
        self.school = self.label(
            School.objects.create(name="E1 Snapshot School"), "school:E1"
        )
        self.superadmin = self.user(
            UserTypes.SUPER_ADMIN, "Root", is_superuser=True, is_staff=True
        )
        self.admin = self.user(UserTypes.SCHOOL_ADMIN, "Principal", school=self.school)
        self.t1 = self.user(UserTypes.TEACHER, "Tess", school=self.school)
        self.t2 = self.user(UserTypes.TEACHER, "Theo", school=self.school)
        # Individual sessions: the teacher dashboards look a session up by
        # its owning teacher.
        self.term = self.label(
            Session.objects.create(
                name="E1 Fall Tess",
                owner_type=SessionOwnerType.INDIVIDUAL,
                teacher=self.t1,
            ),
            "session:FallTess",
        )
        self.term2 = self.label(
            Session.objects.create(
                name="E1 Fall Theo",
                owner_type=SessionOwnerType.INDIVIDUAL,
                teacher=self.t2,
            ),
            "session:FallTheo",
        )
        self.c1 = self.course(self.t1, "Algebra", self.term)
        self.c2 = self.course(self.t1, "Biology", self.term)
        self.c3 = self.course(self.t2, "Chemistry", self.term2)
        self.topic_a = self.topic(self.c1, "Equations")
        self.topic_b = self.topic(self.c1, "Graphs")
        self.topic_c = self.topic(self.c2, "Cells")

        # Assignments: points 100, 5 (the quiz the plain mean over-weights),
        # NULL (falls back to max_points or nothing), 0, 20, 8.
        a = self.assignment
        # A0 was graded 90 days ago: inside the weekly course summary's
        # "previous" window (60-120 days back), so its previous average has data.
        self.a0 = a(self.c1, "A0 Old", 10, AssignmentTypes.OBJECTIVE, self.topic_a, 100)
        self.a1 = a(self.c1, "A1 Exam", 100, AssignmentTypes.ESSAY, self.topic_a, 30)
        self.a2 = a(self.c1, "A2 Quiz", 5, AssignmentTypes.OBJECTIVE, self.topic_a, 20)
        self.a3 = a(
            self.c1, "A3 NoPts", None, AssignmentTypes.OBJECTIVE, self.topic_b, 10
        )
        self.a4 = a(
            self.c1, "A4 Zero", 0, AssignmentTypes.SHORT_ANSWER, self.topic_b, 5
        )
        self.a5 = a(self.c1, "A5 Future", 20, AssignmentTypes.ESSAY, None, -5)
        self.b1 = a(self.c2, "B1 Lab", 20, AssignmentTypes.ESSAY, self.topic_c, 25)
        self.b2 = a(self.c2, "B2 Quiz", 8, AssignmentTypes.OBJECTIVE, self.topic_c, 12)
        self.b3 = a(self.c2, "B3 Undated", 10, AssignmentTypes.OBJECTIVE, None, None)
        self.k1 = a(self.c3, "K1 Test", 50, AssignmentTypes.OBJECTIVE, None, 15)
        self.k2 = a(
            self.c3,
            "K2 Draft",
            10,
            AssignmentTypes.OBJECTIVE,
            None,
            3,
            status=AssignmentStatus.DRAFT,
        )

        names = ["Ada", "Ben", "Cy", "Dee", "Eve", "Fay", "Gus", "Hal"]
        s = {n: self.user(UserTypes.STUDENT, n) for n in names}
        self.students = s
        # Ada is in two courses of the same teacher (R3's cross-course case);
        # Hal is withdrawn from Algebra; Gus is in Chemistry only.
        for name in ["Ada", "Ben", "Cy", "Dee", "Eve", "Fay"]:
            self.enrol(s[name], self.c1)
        self.enrol(s["Hal"], self.c1, EnrollmentStatusType.WITHDRAWN)
        for name in ["Ada", "Ben", "Eve"]:
            self.enrol(s[name], self.c2)
        for name in ["Gus", "Cy"]:
            self.enrol(s[name], self.c3)

        sub = self.submit
        sub(s["Ada"], self.a0, "5.55", 10, "55.50", 90)
        sub(s["Ben"], self.a0, "7.00", 10, "70.00", 91)
        # Ada: strong on the exam, weak on the small quiz (weighted != plain mean).
        sub(s["Ada"], self.a1, "92.00", None, "92.00", 28)
        sub(s["Ada"], self.a2, "1.00", 5, "20.00", 18)
        sub(s["Ada"], self.a3, "7.00", 10, "70.00", 9)  # NULL total, stored max
        sub(s["Ada"], self.b1, "15.00", 20, "75.00", 24)
        sub(s["Ada"], self.b2, "1.00", None, "12.50", 11)  # 12.5: rounding edge
        # Ben: unpublished exam (staff sees it, student must not), a score
        # above the maximum (clamp), and the zero-point assignment.
        sub(s["Ben"], self.a1, "40.00", 100, "40.00", 27, published=False)
        sub(s["Ben"], self.a2, "6.00", 5, "120.00", 19)
        sub(s["Ben"], self.a4, "3.00", None, "50.00", 4)  # both 0/NULL: no weight
        sub(s["Ben"], self.b1, "10.00", 20, "50.00", 23)
        # Cy: score NULL with graded_at set; a percentage without graded_at.
        sub(s["Cy"], self.a1, None, 100, None, 26)
        sub(s["Cy"], self.a2, "4.00", 5, "80.00", 17, graded=False)
        sub(s["Cy"], self.k1, "45.00", 50, "90.00", 14)
        # Dee: a negative adjustment (clamp low), and NULL/NULL weight.
        sub(s["Dee"], self.a1, "-5.00", 100, "0.00", 25)
        sub(s["Dee"], self.a3, "5.00", None, "50.00", 8)
        # Dee's quiz was re-marked out of 10 on a 5-point assignment: the
        # stored max_points and total_points both set and different, so the
        # weight rule's precedence (stored maximum first) is visible.
        sub(s["Dee"], self.a2, "9.00", 10, "90.00", 15)
        # Eve: a declining run, so the trend and at-risk paths fire.
        sub(s["Eve"], self.a1, "95.00", 100, "95.00", 29)
        sub(s["Eve"], self.a2, "3.00", 5, "60.00", 16)
        sub(s["Eve"], self.a3, "2.00", 10, "20.00", 7)
        sub(s["Eve"], self.b1, "19.00", 20, "95.00", 22)
        sub(s["Eve"], self.b2, "2.00", 8, "25.00", 10)
        sub(s["Eve"], self.b3, "1.00", 10, "10.00", 2)
        # Fay: nothing submitted (missing-work risk). Hal (withdrawn): a
        # submission that must not count wherever withdrawn rows are excluded.
        sub(s["Hal"], self.a1, "10.00", 100, "10.00", 24)
        # Gus: one ungraded submission, one graded on a draft assignment.
        sub(s["Gus"], self.k1, "20.00", 50, "40.00", 13, graded=False)
        sub(s["Gus"], self.k2, "9.00", 10, "90.00", 2)
        return self

    def course(self, teacher, name, term):
        course = Course.objects.create(
            name=name, teacher=teacher, session=term, is_active=True
        )
        return self.label(course, f"course:{name}")

    def topic(self, course, name):
        return self.label(
            Topic.objects.create(name=name, course=course), f"topic:{name}"
        )

    def assignment(
        self,
        course,
        title,
        points,
        kind,
        topic,
        due_days_ago,
        status=AssignmentStatus.PUBLISHED,
    ):
        assignment = Assignment.objects.create(
            course=course,
            title=title,
            total_points=points,
            assignment_type=kind,
            topic=topic,
            status=status,
            due_date=None if due_days_ago is None else _days_ago(due_days_ago),
            extraction_confidence=80,
        )
        return self.label(assignment, f"assignment:{title}")

    def enrol(self, student, course, status_=EnrollmentStatusType.ENROLLED):
        enrollment = StudentCourse.objects.create(
            student=student, course=course, enrollment_status=status_
        )
        return self.label(enrollment, f"enrollment:{student.first_name}/{course.name}")

    def submit(
        self,
        student,
        assignment,
        score,
        max_points,
        pct,
        days_ago,
        *,
        published=True,
        graded=True,
    ):
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers={"q1": "a"},
            score=None if score is None else Decimal(score),
            max_points=max_points,
            score_percentage=None if pct is None else Decimal(pct),
            graded_at=_days_ago(days_ago, hour=15) if graded else None,
            is_published=published,
            grading_confidence=70 + days_ago % 20,
            extraction_confidence=60 + days_ago % 30,
        )
        # submission_date is auto_now_add: set it after the fact so the
        # trend has a time axis.
        StudentSubmission.objects.filter(pk=submission.pk).update(
            submission_date=_days_ago(days_ago, hour=9)
        )
        return self.label(
            submission, f"submission:{student.first_name}/{assignment.title}"
        )


@override_settings(CACHES=LOCMEM)
class GradeFigureSnapshotTest(TestCase):
    """Byte-for-byte snapshots of every grade-bearing output."""

    maxDiff = None
    fx: Fixture

    @classmethod
    def setUpTestData(cls):
        with freeze_time(NOW):
            cls.fx = Fixture().build()

    def setUp(self):
        cache.clear()
        self.freezer = freeze_time(NOW)
        self.freezer.start()
        self.addCleanup(self.freezer.stop)

    # -- plumbing ---------------------------------------------------------

    def normalize(self, text):
        labels = self.fx.labels

        def swap(match):
            return labels.get(match.group(0).lower(), "<unlabelled-uuid>")

        return UUID_RE.sub(swap, text)

    @classmethod
    def canonical(cls, value):
        if isinstance(value, dict):
            return {key: cls.canonical(item) for key, item in value.items()}
        if isinstance(value, list):
            items = [cls.canonical(item) for item in value]
            if items and all(isinstance(item, dict) for item in items):
                items.sort(key=lambda item: json.dumps(item, sort_keys=True))
            return items
        return value

    def check(self, name, text):
        text = self.normalize(text)
        text = json.dumps(
            self.canonical(json.loads(text)), indent=1, ensure_ascii=False
        )
        path = GOLDEN_DIR / f"{name}.json"
        if RECORD:
            GOLDEN_DIR.mkdir(exist_ok=True)
            path.write_text(text + "\n", encoding="utf-8")
            return
        self.assertTrue(path.exists(), f"no golden file {path.name}; record it first")
        self.assertEqual(text, path.read_text(encoding="utf-8").rstrip("\n"), name)

    def get(self, user, path, name):
        cache.clear()
        client = APIClient()
        client.force_authenticate(user)
        response = client.get(f"/api/v1/{path}")
        self.assertEqual(response.status_code, 200, f"{name}: {response.content[:500]}")
        # Pretty-printed with the response's own key order, so a diff shows
        # the figure that moved; the values are the response's own bytes.
        body = json.dumps(json.loads(response.content), indent=1, ensure_ascii=False)
        self.check(name, body)

    def dump(self, name, payload):
        body = json.dumps(payload, indent=1, cls=DjangoJSONEncoder, ensure_ascii=False)
        self.check(name, body)

    # -- super admin: Avg(final_grade) sites (views.py:856, 960, 1046) ----

    def test_super_admin_schools(self):
        self.get(self.fx.superadmin, "super-admin/dashboard/schools", "super_schools")

    def test_super_admin_teachers(self):
        self.get(self.fx.superadmin, "super-admin/dashboard/teachers", "super_teachers")

    def test_super_admin_students(self):
        self.get(self.fx.superadmin, "super-admin/dashboard/students", "super_students")

    # -- school admin (views.py:2356, 2539, 2636, 2907; R6; P8 rigor) -----

    def test_school_admin_summary(self):
        self.get(self.fx.admin, "school-admin/dashboard/summary", "school_summary")

    def test_school_admin_course_performance(self):
        self.get(
            self.fx.admin,
            "school-admin/dashboard/course-performance",
            "school_course_performance",
        )

    def test_school_admin_unit_performance(self):
        self.get(
            self.fx.admin,
            "school-admin/dashboard/unit-performance",
            "school_unit_performance",
        )

    def test_school_admin_students(self):
        self.get(self.fx.admin, "school-admin/dashboard/students", "school_students")

    def test_school_admin_course_overview_chart(self):
        self.get(
            self.fx.admin,
            "school-admin/dashboard/course-overview-chart",
            "school_course_overview_chart",
        )

    def test_school_admin_teachers(self):
        self.get(self.fx.admin, "school-admin/dashboard/teachers", "school_teachers")

    def test_school_admin_teacher_detail(self):
        self.get(
            self.fx.admin,
            f"school-admin/dashboard/teachers/{self.fx.t1.id}",
            "school_teacher_detail",
        )

    def test_school_admin_at_risk_trend(self):
        self.get(
            self.fx.admin,
            "school-admin/dashboard/at-risk-trend",
            "school_at_risk_trend",
        )

    # -- teacher (P2, P3, P4, R4, P5, all-students, my-students R3) ------

    def test_teacher_overview(self):
        self.get(
            self.fx.t1,
            f"teacher-admin/dashboard/overview/{self.fx.term.id}",
            "teacher_overview",
        )

    def test_teacher_course_analytics(self):
        for course in (self.fx.c1, self.fx.c2):
            self.get(
                self.fx.t1,
                f"teacher-admin/dashboard/courses/{course.id}",
                f"teacher_courses_{course.name.lower()}",
            )

    def test_teacher_assignment_analytics(self):
        for assignment in (
            self.fx.a1,
            self.fx.a2,
            self.fx.a3,
            self.fx.a4,
            self.fx.b1,
            self.fx.b2,
        ):
            slug = assignment.title.split()[0].lower()
            self.get(
                self.fx.t1,
                f"teacher-admin/dashboard/assignments/{assignment.id}",
                f"teacher_assignment_{slug}",
            )

    def test_teacher_course_students(self):
        for course in (self.fx.c1, self.fx.c2):
            self.get(
                self.fx.t1,
                f"teacher-admin/dashboard/students/{course.id}",
                f"teacher_students_{course.name.lower()}",
            )

    def test_teacher_all_students(self):
        self.get(
            self.fx.t1,
            f"teacher-admin/dashboard/all-students/{self.fx.term.id}",
            "teacher_all_students",
        )

    def test_teacher_my_students(self):
        self.get(self.fx.t1, "student-course/my-students", "teacher_my_students")

    def test_teacher_enrollment_list(self):
        self.get(self.fx.t1, "student-course", "teacher_enrollments")

    # -- student (P6, overview means, R2) --------------------------------

    def test_student_course_summary(self):
        for course in (self.fx.c1, self.fx.c2):
            self.get(
                self.fx.students["Ada"],
                f"student-admin/dashboard/summary/{course.id}",
                f"student_summary_{course.name.lower()}",
            )

    def test_student_overview(self):
        for name in ("Ada", "Ben", "Eve"):
            self.get(
                self.fx.students[name],
                "student-admin/dashboard/overview",
                f"student_overview_{name.lower()}",
            )

    def test_student_enrollment_list_released_grade(self):
        for name in ("Ada", "Ben"):
            self.get(
                self.fx.students[name],
                "student-course",
                f"student_enrollments_{name.lower()}",
            )

    # -- services called directly (services.py:241, 394, 483, 513, 1103,
    #    1135, 1245, 1518; rigor.py:270) -----------------------------------

    def test_student_weekly_summary_service(self):
        from dashboard.services import StudentWeeklySummaryService

        for name in ("Ada", "Eve"):
            self.dump(
                f"svc_student_weekly_{name.lower()}",
                StudentWeeklySummaryService().build_student_summary(
                    self.fx.students[name], as_of=NOW
                ),
            )

    def test_weekly_course_summary_service(self):
        from dashboard.services import WeeklyCourseSummaryService

        for course in (self.fx.c1, self.fx.c2):
            self.dump(
                f"svc_course_weekly_{course.name.lower()}",
                WeeklyCourseSummaryService().build_course_summary(
                    course, as_of=NOW, window_days=60
                ),
            )

    def test_teacher_ai_context_service(self):
        from dashboard.services import TeacherAIContextService

        self.dump(
            "svc_teacher_ai_context",
            TeacherAIContextService().build(self.fx.t1, now=NOW),
        )

    def test_school_admin_weekly_summary_service(self):
        from dashboard.services import SchoolAdminWeeklySummaryService

        self.dump(
            "svc_school_weekly",
            SchoolAdminWeeklySummaryService().build_school_summary(
                self.fx.school, as_of=NOW
            ),
        )

    def test_teacher_performance_stats_and_rigor(self):
        from dashboard.rigor import build_rigor_by_teacher
        from dashboard.services import TeacherPerformanceStatsService

        teachers = [self.fx.t1, self.fx.t2]
        stats = TeacherPerformanceStatsService().build(teachers, now=NOW)
        rigor = build_rigor_by_teacher([t.id for t in teachers])
        self.dump(
            "svc_teacher_stats_rigor",
            {
                "stats": {str(t.id): stats[t.id] for t in teachers},
                "rigor": {str(t.id): rigor[t.id] for t in teachers},
            },
        )
