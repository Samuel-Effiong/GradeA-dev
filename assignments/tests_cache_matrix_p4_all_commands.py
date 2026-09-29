"""H-1 Stage 3, P4: explicit coverage for all four data-repair commands.

`tests_cache_matrix_p4.py` proves `strip_html_from_assignment_titles`
end-to-end. The other three repair commands write the same way (a
`bulk_update` that fires no signal) and got the same fix
(`bump_assignment_course_scopes_bulk` after each batch), so each one gets
its own proof here:

* Wiring, for all four commands: repairing one assignment bumps the
  owning teacher, an enrolled student, the course and the school, and
  leaves a second school's teacher, student, course and school
  untouched. The command's query count is also the same for 1 and 10
  repaired assignments. That check exists because
  `backfill_assignment_rigor` loads rows with `.only(...)`, and reading
  a deferred `course_id` for the bump cost one query per assignment
  until `course` was added to that list.
* Freshness, where a cached read shows the repair:
  `backfill_assignment_rigor` and `repair_question_blooms_levels` change
  the rigor columns behind the school admin's teacher dashboard, which is
  keyed on the school's generation.

`strip_duplicate_option_letters` has no freshness test on purpose. Every
read that shows options renders them through
`_strip_leading_option_letter`, which already removes every leading
marker, so no cached payload differs before and after that repair. Its
coverage is the wiring test.

Real Redis + real Postgres.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.db.models.signals import pre_save
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from assignments.models import Assignment, AssignmentStatus
from assignments.signals import sanitize_assignment_title
from AutoGrader.cache_generation import (
    SCOPE_COURSE,
    SCOPE_SCHOOL,
    SCOPE_USER,
    get_generation,
)
from AutoGrader.tests_cache_matrix_support import UNAFFECTED, FreshnessMatrixMixin, Read
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from users.models import UserTypes

User = get_user_model()


def question(*, blooms="Apply", options=None, model_answer=""):
    payload = {
        "question_number": 1,
        "question_text": "Question 1",
        "question_type": "OBJECTIVE",
        "points": 10,
        "options": options if options is not None else ["one", "two"],
        "rubric": [],
        "model_answer": model_answer,
    }
    if blooms is not None:
        payload["blooms_level"] = blooms
    return payload


class RepairCommandFixture(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.school, self.admin, self.teacher, self.student, self.course = self._tenant(
            "a"
        )
        (
            self.other_school,
            self.other_admin,
            self.other_teacher,
            self.other_student,
            self.other_course,
        ) = self._tenant("b")
        # The other tenant's assignment is healthy, so no command has a
        # reason to touch it or its scopes.
        Assignment.objects.create(
            title="Healthy assignment",
            course=self.other_course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question()],
        )

    def _tenant(self, tag):
        school = School.objects.create(name=f"P4 school {tag}")
        admin = User.objects.create_user(
            email=f"p4all-admin-{tag}@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            is_active=True,
            first_name=f"P4Admin{tag}",
            last_name="Admin",
            school=school,
        )
        teacher = User.objects.create_user(
            email=f"p4all-teacher-{tag}@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
            first_name=f"P4Teacher{tag}",
            last_name="Teacher",
            school=school,
        )
        student = User.objects.create_user(
            email=f"p4all-student-{tag}@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=True,
            first_name=f"P4Student{tag}",
            last_name="Student",
        )
        session = Session.objects.create(name=f"P4 term {tag}", teacher=teacher)
        course = Course.objects.create(
            name=f"P4 course {tag}", teacher=teacher, session=session
        )
        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        return school, admin, teacher, student, course

    # --- one broken assignment per command, in the shape each repairs ---

    def _broken_for_backfill(self, n):
        ids = [
            Assignment.objects.create(
                title=f"Drifted {i}",
                course=self.course,
                status=AssignmentStatus.PUBLISHED,
                questions=[question()],
            ).pk
            for i in range(n)
        ]
        # The drift the command exists for: rigor columns written by a path
        # that skipped the pre_save hook.
        Assignment.objects.filter(pk__in=ids).update(
            rigor_demand=None, rigor_standards=None, rigor_blooms_coverage=None
        )

    def _broken_for_blooms(self, n):
        for i in range(n):
            Assignment.objects.create(
                title=f"Lost blooms {i}",
                course=self.course,
                status=AssignmentStatus.PUBLISHED,
                questions=[question(blooms=None)],
                ai_raw_payload={"questions": [question(blooms="Apply")]},
            )

    def _broken_for_options(self, n):
        for i in range(n):
            Assignment.objects.create(
                title=f"Doubled markers {i}",
                course=self.course,
                status=AssignmentStatus.PUBLISHED,
                questions=[
                    question(options=["A. A) one", "B) two"], model_answer="A) one")
                ],
            )

    def _broken_for_titles(self, n):
        # Rows from before the title sanitizer existed; disconnect it for
        # these writes only.
        pre_save.disconnect(sanitize_assignment_title, sender=Assignment)
        try:
            for i in range(n):
                Assignment.objects.create(
                    title=f"<p>Tagged {i}</p>",
                    course=self.course,
                    status=AssignmentStatus.PUBLISHED,
                    questions=[question()],
                )
        finally:
            pre_save.connect(sanitize_assignment_title, sender=Assignment)


COMMANDS = {
    "backfill_assignment_rigor": "_broken_for_backfill",
    "repair_question_blooms_levels": "_broken_for_blooms",
    "strip_duplicate_option_letters": "_broken_for_options",
    "strip_html_from_assignment_titles": "_broken_for_titles",
}


class RepairCommandWiringTests(RepairCommandFixture):
    def _generations(self):
        return {
            "teacher": get_generation(SCOPE_USER, self.teacher.pk),
            "student": get_generation(SCOPE_USER, self.student.pk),
            "course": get_generation(SCOPE_COURSE, self.course.pk),
            "school": get_generation(SCOPE_SCHOOL, self.school.pk),
            "other teacher": get_generation(SCOPE_USER, self.other_teacher.pk),
            "other student": get_generation(SCOPE_USER, self.other_student.pk),
            "other course": get_generation(SCOPE_COURSE, self.other_course.pk),
            "other school": get_generation(SCOPE_SCHOOL, self.other_school.pk),
        }

    def _assert_wired(self, command):
        getattr(self, COMMANDS[command])(1)
        before = self._generations()
        call_command(command, stdout=StringIO())
        after = self._generations()

        moved = sorted(k for k in before if after[k] != before[k])
        self.assertEqual(
            moved,
            ["course", "school", "student", "teacher"],
            f"{command}: expected exactly the owning tenant's scopes to move",
        )

    def test_backfill_assignment_rigor_bumps_only_the_owning_tenant(self):
        self._assert_wired("backfill_assignment_rigor")

    def test_repair_question_blooms_levels_bumps_only_the_owning_tenant(self):
        self._assert_wired("repair_question_blooms_levels")

    def test_strip_duplicate_option_letters_bumps_only_the_owning_tenant(self):
        self._assert_wired("strip_duplicate_option_letters")

    def test_strip_html_from_assignment_titles_bumps_only_the_owning_tenant(self):
        self._assert_wired("strip_html_from_assignment_titles")


class RepairCommandQueryFlatnessTests(RepairCommandFixture):
    def _queries_for(self, command, n):
        Assignment.objects.filter(course=self.course).delete()
        getattr(self, COMMANDS[command])(n)
        with CaptureQueriesContext(connection) as queries:
            call_command(command, stdout=StringIO())
        return len(queries.captured_queries)

    def _assert_flat(self, command):
        one = self._queries_for(command, 1)
        ten = self._queries_for(command, 10)
        self.assertEqual(
            one,
            ten,
            f"{command}: {one} queries for 1 repaired assignment but {ten} for 10",
        )

    def test_backfill_assignment_rigor_query_count_is_flat(self):
        self._assert_flat("backfill_assignment_rigor")

    def test_repair_question_blooms_levels_query_count_is_flat(self):
        self._assert_flat("repair_question_blooms_levels")

    def test_strip_duplicate_option_letters_query_count_is_flat(self):
        self._assert_flat("strip_duplicate_option_letters")

    def test_strip_html_from_assignment_titles_query_count_is_flat(self):
        self._assert_flat("strip_html_from_assignment_titles")


class RigorRepairFreshnessTests(FreshnessMatrixMixin, RepairCommandFixture):
    def reads(self):
        url = reverse("school-admin-teacher-performance")
        return [
            Read("school admin's teacher dashboard", self.admin, url),
            Read("other school admin's teacher dashboard", self.other_admin, url),
        ]

    def _assert_fresh(self, command):
        getattr(self, COMMANDS[command])(1)
        result = self.run_matrix(
            f"{command} (P4 fixed)",
            self.reads(),
            lambda: call_command(command, stdout=StringIO()),
        )
        self.assert_no_stale(
            result, expect_changed=["school admin's teacher dashboard"]
        )
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(
            verdicts["other school admin's teacher dashboard"],
            UNAFFECTED,
            result.table(),
        )

    def test_backfill_assignment_rigor_refreshes_the_school_dashboard(self):
        self._assert_fresh("backfill_assignment_rigor")

    def test_repair_question_blooms_levels_refreshes_the_school_dashboard(self):
        self._assert_fresh("repair_question_blooms_levels")
