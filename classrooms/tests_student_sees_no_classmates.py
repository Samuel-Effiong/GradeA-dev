"""
H-147: a student sees nothing of their classmates.

Founder's representative, 2026-10-06: "Student should see nothing of their
classmates." The course answer (list, detail, my-courses) gave a student
the whole active roster: each classmate's id, names, active flag, profile
image, enrolment status and whether their address is a made-up one; only
the address itself was blanked. It also nested the TEACHER's assignment
list, with `submission_count` (how many classmates have handed in) and the
teacher's scheduling of a grading run.

Now, for a student:
  * `students` is the student's own entry and no other;
  * `student_count`, the size of the class as a bare number, is still
    sent: the founder's representative allowed that much;
  * the nested assignments are the student's own view of each assignment
    (the shape the assignment list route gives a student), with no count of
    classmates' work and no scheduling field;
  * a session shows neither its school's id nor the id of the staff
    account that created it.
A teacher's answers are unchanged.

Every "is absent" assertion here sits beside one that the student's own
data IS present, so none can pass on an empty answer (rule 19).

The classroom is built by the helpers of
classrooms/tests_course_payload_student_exposure.py.

Run with:
    python manage.py test classrooms.tests_student_sees_no_classmates
"""

import json
from datetime import timedelta
from types import SimpleNamespace

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from assignments.models import Assignment, AssignmentStatus
from assignments.serializers import (
    AssignmentListSerializer,
    AssignmentListStudentSerializer,
)
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    SessionOwnerType,
    StudentCourse,
)
from classrooms.tests_course_payload_student_exposure import (
    CoursePayloadBase,
    make_user,
)
from classrooms.views import CourseViewSet
from students.models import StudentSubmission
from students.serializers import StudentSerializer
from users.models import UserTypes

#: The teacher's fields of a nested assignment that a student is not sent.
STAFF_ASSIGNMENT_KEYS = (
    "submission_count",
    "extraction_confidence",
    "auto_grade_on_due_date",
    "scheduled_grading_at",
    "grading_task_name",
    "is_grading_scheduled",
)
SCHEDULE_MARKER = "h147-scheduled-grading-task-name"


def plain(value):
    return json.loads(json.dumps(value, default=str))


class StudentCourseAnswerBase(CoursePayloadBase):
    def setUp(self):
        super().setUp()
        self.build_class()
        # The teacher has scheduled a grading run for the published work.
        Assignment.objects.filter(pk=self.published.pk).update(
            scheduled_grading_at=timezone.now() + timedelta(days=1),
            grading_task_name=SCHEDULE_MARKER,
        )
        self.routes = {
            "detail": reverse("course-detail", kwargs={"pk": self.course_id}),
            "list": reverse("course-list"),
            "my-courses": reverse("course-my-courses"),
        }

    def course_as(self, user, route):
        data = self.get_as(user, self.routes[route])
        if route == "detail":
            return data, data
        return data, self.course_in(data, self.course_id)

    def classmate_marks(self):
        """Everything of the classmate that could appear in an answer."""
        return (
            str(self.classmate.id),
            self.classmate.first_name,
            self.classmate.email,
        )


class StudentSeesOnlyTheirOwnEntryTest(StudentCourseAnswerBase):
    def test_the_roster_is_the_students_own_entry_on_every_course_route(self):
        for route in self.routes:
            with self.subTest(route=route):
                whole, course = self.course_as(self.viewer, route)
                roster = course["students"]

                # The student's own entry, in the shape it always had...
                self.assertEqual(len(roster), 1)
                self.assertEqual(str(roster[0]["id"]), str(self.viewer.id))
                self.assertEqual(roster[0]["email"], self.viewer.email)
                self.assertEqual(roster[0]["first_name"], self.viewer.first_name)
                self.assertEqual(
                    list(roster[0].keys()), list(StudentSerializer().fields.keys())
                )
                # ...and nothing of the classmate anywhere in the answer.
                body = json.dumps(plain(whole))
                self.assertIn(self.viewer.first_name, body)
                for mark in self.classmate_marks():
                    self.assertNotIn(mark, body)

    def test_the_class_size_is_sent_as_a_bare_number(self):
        """The founder's representative allowed this much: how many are in
        the class ("e.g., 24 students"), and nothing else about them."""
        for route in self.routes:
            with self.subTest(route=route):
                _, course = self.course_as(self.viewer, route)
                self.assertEqual(course["student_count"], 2)
                self.assertEqual(len(course["students"]), 1)

    def test_a_classmate_of_any_enrolment_status_is_absent(self):
        pending = self.new_student("pending")
        StudentCourse.objects.create(
            student=pending,
            course_id=self.course_id,
            enrollment_status=EnrollmentStatusType.PENDING,
        )
        whole, course = self.course_as(self.viewer, "detail")
        self.assertEqual(
            [str(s["id"]) for s in course["students"]], [str(self.viewer.id)]
        )
        self.assertNotIn(pending.first_name, json.dumps(plain(whole)))

    def test_the_teacher_still_sees_the_whole_roster_and_the_class_size(self):
        for route in ("detail", "list"):
            with self.subTest(route=route):
                _, course = self.course_as(self.teacher, route)
                self.assertEqual(
                    {str(s["id"]) for s in course["students"]},
                    {str(self.viewer.id), str(self.classmate.id)},
                )
                self.assertEqual(
                    {s["email"] for s in course["students"]},
                    {self.viewer.email, self.classmate.email},
                )
                self.assertEqual(course["student_count"], 2)


class StudentSeesTheirOwnViewOfTheAssignmentsTest(StudentCourseAnswerBase):
    def test_the_nested_assignments_are_the_students_own_view(self):
        for route in self.routes:
            with self.subTest(route=route):
                whole, course = self.course_as(self.viewer, route)
                assignments = course["assignments"]

                # The published assignment is there, in the student's shape...
                self.assertEqual(
                    [str(a["id"]) for a in assignments], [str(self.published.id)]
                )
                self.assertEqual(assignments[0]["title"], "Cells quiz")
                self.assertEqual(
                    list(assignments[0].keys()),
                    list(AssignmentListStudentSerializer().fields.keys()),
                )
                self.assertEqual(course["assignment_count"], 1)
                # ...with none of the teacher's fields, and no trace of the
                # scheduled grading run (H-133's rule, on a route H-133 did
                # not cover).
                for key in STAFF_ASSIGNMENT_KEYS:
                    self.assertNotIn(key, assignments[0])
                body = json.dumps(plain(whole))
                self.assertIn("Cells quiz", body)
                self.assertNotIn(SCHEDULE_MARKER, body)
                self.assertNotIn("Unreleased exam", body)

    def test_the_students_own_status_on_an_assignment_is_shown(self):
        """What the student's view is for: their own state on the work."""
        _, course = self.course_as(self.viewer, "detail")
        self.assertEqual(course["assignments"][0]["status"], "NOT SUBMITTED")
        self.assertIsNone(course["assignments"][0]["score"])

    def test_the_teacher_still_gets_the_teachers_assignment_list(self):
        whole, course = self.course_as(self.teacher, "detail")
        self.assertEqual(
            list(course["assignments"][0].keys()),
            list(AssignmentListSerializer().fields.keys()),
        )
        self.assertEqual(
            {a["title"] for a in course["assignments"]},
            {"Cells quiz", "Unreleased exam"},
        )
        self.assertIn(SCHEDULE_MARKER, json.dumps(plain(whole)))


class StudentSeesNoStaffIdsOnASessionTest(StudentCourseAnswerBase):
    """A session's school and the staff account that created it."""

    def setUp(self):
        super().setUp()
        self.school = School.objects.create(name="H-147 school")
        self.admin = make_user("h147-admin@x.test", UserTypes.SCHOOL_ADMIN)
        self.school_session = Session.objects.create(
            name="School term",
            owner_type=SessionOwnerType.SCHOOL,
            school=self.school,
            created_by=self.admin,
        )
        course = Course.objects.create(
            name="School course", teacher=self.teacher, session=self.school_session
        )
        StudentCourse.objects.create(
            student=self.viewer,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        Session.objects.filter(pk=self.session.pk).update(created_by=self.teacher)

    def test_a_student_is_sent_neither_id(self):
        data = self.get_as(self.viewer, reverse("session-list"))
        by_name = {row["name"]: row for row in self.rows(data)}

        # Both of the student's sessions are listed, by name...
        self.assertEqual(set(by_name), {"Fall", "School term"})
        for row in by_name.values():
            self.assertIn("school", row)
            self.assertIn("created_by", row)
            # ...and neither says which school or which staff account.
            self.assertIsNone(row["school"])
            self.assertIsNone(row["created_by"])
        body = json.dumps(plain(data))
        for staff_id in (self.school.id, self.admin.id, self.teacher.id):
            self.assertNotIn(str(staff_id), body)

    def test_the_teacher_still_sees_who_created_their_session(self):
        data = self.get_as(self.teacher, reverse("session-list"))
        own = [row for row in self.rows(data) if row["name"] == "Fall"]
        self.assertEqual(len(own), 1)
        self.assertEqual(str(own[0]["created_by"]), str(self.teacher.id))


class StudentCourseAnswerQueryCountTest(StudentCourseAnswerBase):
    """The student's view of an assignment reads the student's own
    submission. Nested in the course answer it must not ask the database
    once per assignment: the course view loads them all in one query."""

    def measure(self, route):
        cache.clear()
        self.client.force_authenticate(self.viewer)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.routes[route])
        self.assertEqual(response.status_code, 200)
        return len(captured), response.data

    def test_the_count_stays_flat_as_the_assignments_grow(self):
        few = {route: self.measure(route)[0] for route in self.routes}
        for number in range(5):
            self.add_assignment(
                self.course_id, f"More work {number}", AssignmentStatus.PUBLISHED
            )
        for route in self.routes:
            with self.subTest(route=route):
                count, data = self.measure(route)
                course = (
                    data if route == "detail" else self.course_in(data, self.course_id)
                )
                # Six assignments are really in the answer...
                self.assertEqual(len(course["assignments"]), 6)
                # ...for the same number of queries as one.
                self.assertEqual(count, few[route])


class StudentReadsOnlyTheirOwnSubmissionTest(StudentCourseAnswerBase):
    """The nested view of an assignment shows the student's own state on
    it. It is read from submissions the course view loaded beforehand, so
    two things keep a classmate's paper out of it, each tested alone: the
    view loads only the viewer's own, and the serializer reads only the
    requester's own from whatever was loaded."""

    def hand_in(self, student):
        return StudentSubmission.objects.create(
            assignment=self.published,
            student=student,
            answers=[{"question_number": 1, "answer_html": "an answer"}],
            attempt_count=1,
        )

    def status_as(self, user):
        _, course = self.course_as(user, "detail")
        return course["assignments"][0]["status"]

    def test_the_students_own_paper_shows_as_submitted(self):
        self.assertEqual(self.status_as(self.viewer), "NOT SUBMITTED")
        self.hand_in(self.viewer)
        for route in self.routes:
            with self.subTest(route=route):
                _, course = self.course_as(self.viewer, route)
                self.assertEqual(course["assignments"][0]["status"], "SUBMITTED")
                self.assertEqual(course["assignments"][0]["remaining_attempts"], 2)

    def test_a_classmates_paper_changes_nothing_the_student_reads(self):
        before, _ = self.course_as(self.viewer, "detail")
        self.hand_in(self.classmate)
        # The classmate's own answer did change, so a paper was handed in...
        self.assertEqual(self.status_as(self.classmate), "SUBMITTED")
        # ...and the viewer's did not.
        after, _ = self.course_as(self.viewer, "detail")
        self.assertEqual(plain(after), plain(before))

    def test_the_view_loads_only_the_viewers_own_submissions(self):
        own = self.hand_in(self.viewer)
        self.hand_in(self.classmate)
        view = CourseViewSet()
        view.request = SimpleNamespace(user=self.viewer)  # type: ignore[assignment]
        view.action = "retrieve"
        course = view.get_queryset().get(pk=self.course_id)
        loaded = [
            submission.pk
            for assignment in course.assignments.all()
            for submission in assignment.viewer_submissions
        ]
        self.assertEqual(loaded, [own.pk])

    def test_the_serializer_reads_only_the_requesters_own_of_what_was_loaded(self):
        theirs = self.hand_in(self.classmate)
        assignment = Assignment.objects.get(pk=self.published.pk)
        request = SimpleNamespace(user=self.viewer)

        assignment.viewer_submissions = [theirs]  # type: ignore[attr-defined]
        data = AssignmentListStudentSerializer(
            assignment, context={"request": request}
        ).data
        self.assertEqual(data["status"], "NOT SUBMITTED")
        self.assertEqual(data["remaining_attempts"], 3)

        mine = self.hand_in(self.viewer)
        assignment.viewer_submissions = [theirs, mine]  # type: ignore[attr-defined]
        data = AssignmentListStudentSerializer(
            assignment, context={"request": request}
        ).data
        self.assertEqual(data["status"], "SUBMITTED")
        self.assertEqual(data["remaining_attempts"], 2)
