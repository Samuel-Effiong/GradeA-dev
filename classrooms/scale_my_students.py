"""Scale measurement for `my-students` (Gate 6). Not part of the test suite.

The file name does not match `tests*.py`, so discovery skips it. Run it
explicitly, always against the local test database:

    python manage.py test classrooms.scale_my_students \
        --settings=settings_worktree --noinput

It builds one school at 1/10 scale (600 students) and measures, then grows
the SAME database to full scale (6,000 students) and measures again. Each
size records, per request shape: query count, p50/p95 latency over repeated
requests, and peak Python allocation (tracemalloc) during one request. The
JSON result is printed between SCALE-RESULT markers for the evidence doc.

Workload per 600 students (multiplied by 10 at full scale):
  * school teachers: 6, each with 5 courses (30 courses), 8 published
    assignments per course;
  * individual (school-less) teachers: 4, each with 5 courses, 8 assignments;
  * every student (school_id NULL, as in production) is enrolled in 3 school
    courses and 1 individual teacher's course: 2,400 enrollments; exactly
    one student in ten has one of those school courses with the measured
    teacher;
  * 4 graded submissions per enrollment: 9,600 submissions.
  The measured teacher is school teacher #0, whose roster grows 10x with
  the school. Its students are shared with other school teachers and with
  individual teachers - the shape the scoping fix is about.

Bulk rows are written with the field values the production writers set
(`_create_pending_student` / `_create_enrollment` / the grading pipeline),
via bulk_create for speed; that skips post_save receivers, so each
measured-teacher enrollment's `final_grade` is then derived by the same
function the receiver calls (`_recalculate_final_grade`).
"""

import json
import random
import statistics
import time
import tracemalloc

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from classrooms.signals import _recalculate_final_grade
from students.models import StudentSubmission
from users.models import UserTypes

User = get_user_model()
URL = reverse("student-course-my-students")

REPEATS = 30
SCHOOL_TEACHERS_PER_600 = 6
INDIVIDUAL_TEACHERS_PER_600 = 4
COURSES_PER_TEACHER = 5
ASSIGNMENTS_PER_COURSE = 8
SUBMISSIONS_PER_ENROLLMENT = 4


class MyStudentsScale(TransactionTestCase):
    def setUp(self):
        self.rng = random.Random(20260917)  # nosec - fixture shuffling only
        self.school = School.objects.create(name="Scale School")
        self.seq = 0
        self.school_courses = []
        self.individual_courses = []
        self.students = []

    def make_teacher(self, school):
        self.seq += 1
        teacher = User.objects.create_user(
            email=f"scale-t{self.seq}@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
        )
        teacher.user_type = UserTypes.TEACHER
        teacher.is_active = True
        teacher.school = school
        teacher.first_name, teacher.last_name = f"T{self.seq}", "Scale"
        teacher.save()
        return teacher

    def add_teachers(self, count, school, bucket):
        for _ in range(count):
            teacher = self.make_teacher(school)
            session = Session.objects.create(name=f"S{self.seq}", teacher=teacher)
            for c in range(COURSES_PER_TEACHER):
                course = Course.objects.create(
                    name=f"Course {self.seq}-{c}",
                    description=f"Description {self.seq}-{c}",
                    teacher=teacher,
                    session=session,
                )
                Assignment.objects.bulk_create(
                    Assignment(
                        title=f"A{self.seq}-{c}-{a}",
                        course=course,
                        teacher=teacher,
                        status=AssignmentStatus.PUBLISHED,
                    )
                    for a in range(ASSIGNMENTS_PER_COURSE)
                )
                bucket.append(course)
            if not hasattr(self, "measured_teacher"):
                self.measured_teacher = teacher

    def grow(self, students):
        """Add `students` students plus teachers/courses in proportion."""
        factor = students // 600
        self.add_teachers(
            SCHOOL_TEACHERS_PER_600 * factor, self.school, self.school_courses
        )
        self.add_teachers(
            INDIVIDUAL_TEACHERS_PER_600 * factor, None, self.individual_courses
        )
        measured_courses = list(self.measured_teacher.courses.all())

        start = len(self.students)
        new = User.objects.bulk_create(
            User(
                email=f"scale-s{start + i}@x.test",
                user_type=UserTypes.STUDENT,
                is_active=True,
                first_name=f"S{start + i}",
                last_name="Student",
            )
            for i in range(students)
        )
        self.students.extend(new)

        enrollments = []
        other_school_courses = [
            c for c in self.school_courses if c not in measured_courses
        ]
        for index, student in enumerate(new):
            school = self.rng.sample(other_school_courses, 3)
            # Exactly a tenth of the students are the measured teacher's, so
            # its roster grows 10x with the school; the rest never are.
            if index % 10 == 0:
                school[0] = measured_courses[index % len(measured_courses)]
            for course in (*school, self.rng.choice(self.individual_courses)):
                enrollments.append(
                    StudentCourse(
                        student=student,
                        course=course,
                        enrollment_status=EnrollmentStatusType.ENROLLED,
                    )
                )
        StudentCourse.objects.bulk_create(enrollments, batch_size=5000)

        assignments_by_course = {}
        for assignment in Assignment.objects.all().only("id", "course_id"):
            assignments_by_course.setdefault(assignment.course_id, []).append(
                assignment
            )
        now = timezone.now()
        submissions = []
        for enrollment in enrollments:
            for assignment in self.rng.sample(
                assignments_by_course[enrollment.course_id],
                SUBMISSIONS_PER_ENROLLMENT,
            ):
                submissions.append(
                    StudentSubmission(
                        student=enrollment.student,
                        assignment=assignment,
                        answers={},
                        score=self.rng.randint(0, 100),
                        max_points=100,
                        graded_at=now,
                    )
                )
        StudentSubmission.objects.bulk_create(submissions, batch_size=5000)

        for enrollment in StudentCourse.objects.filter(
            course__teacher=self.measured_teacher
        ).values_list("student_id", "course_id"):
            _recalculate_final_grade(*enrollment)

    def shapes(self):
        course = self.measured_teacher.courses.order_by("name").first()
        return {
            "default_page": {},
            "page_size_100": {"page_size": 100},
            "last_page_100": {"page_size": 100, "page": "last"},
            "filter_course": {"enrollments__course": str(course.id)},
            "filter_session": {"enrollments__course__session": str(course.session_id)},
            "search": {"search": "S1"},
        }

    def measure(self):
        client = APIClient()
        client.force_authenticate(self.measured_teacher)
        roster = (
            User.objects.filter(enrollments__course__teacher=self.measured_teacher)
            .distinct()
            .count()
        )
        result = {
            "students_total": User.objects.filter(user_type=UserTypes.STUDENT).count(),
            "enrollments_total": StudentCourse.objects.count(),
            "submissions_total": StudentSubmission.objects.count(),
            "courses_total": Course.objects.count(),
            "measured_teacher_roster": roster,
            "shapes": {},
        }
        for name, params in self.shapes().items():
            with CaptureQueriesContext(connection) as captured:
                response = client.get(URL, params)
            self.assertEqual(response.status_code, 200, name)
            queries = len(captured)
            rows = len(response.data["results"])

            timings = []
            for _ in range(REPEATS):
                t0 = time.perf_counter()
                client.get(URL, params)
                timings.append((time.perf_counter() - t0) * 1000)
            timings.sort()

            tracemalloc.start()
            client.get(URL, params)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            result["shapes"][name] = {
                "params": params,
                "rows": rows,
                "count": response.data["count"],
                "queries": queries,
                "p50_ms": round(statistics.median(timings), 1),
                "p95_ms": round(timings[int(len(timings) * 0.95) - 1], 1),
                "peak_alloc_kb": round(peak / 1024),
            }
        return result

    def test_query_count_constant_across_10x(self):
        timings = {}
        t0 = time.perf_counter()
        self.grow(600)
        timings["build_600_s"] = round(time.perf_counter() - t0)
        small = self.measure()

        t0 = time.perf_counter()
        self.grow(5400)
        timings["build_to_6000_s"] = round(time.perf_counter() - t0)
        large = self.measure()

        out = {"small": small, "large": large, "build": timings}
        print("SCALE-RESULT-BEGIN")
        print(json.dumps(out, indent=2))
        print("SCALE-RESULT-END")

        self.assertGreaterEqual(large["students_total"], 6000)
        self.assertEqual(
            large["measured_teacher_roster"], 10 * small["measured_teacher_roster"]
        )
        for name, shape in large["shapes"].items():
            self.assertLessEqual(
                shape["queries"],
                small["shapes"][name]["queries"],
                f"{name}: queries grew {small['shapes'][name]['queries']} -> "
                f"{shape['queries']} across a 10x larger school",
            )
