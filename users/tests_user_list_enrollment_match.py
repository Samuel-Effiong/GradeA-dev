"""
Brief 6, owner rulings (2026-11-11, via SM -cf), beyond the docs:

1. `?ordering=username` was a 500: CustomUser has no username field
   (`username = None`). The key is removed, so it is ignored like any
   unknown key and leaves the Swagger enum.
2. The enrollment filters are checked TOGETHER: enrollments__course,
   enrollments__course__session and enrollments__enrollment_status, when
   two or more are given, must
   match the SAME enrollment (still only enrollments the requester may
   see). Each one used alone behaves as before.
3. enrollments__enrollment_status__in is removed (owner): it is ignored
   like any unknown parameter.
"""

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status

from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from users.models import UserTypes

from .tests_user_list_sorting import Base

WITHDRAWN = EnrollmentStatusType.WITHDRAWN
ENROLLED = EnrollmentStatusType.ENROLLED
COMPLETED = EnrollmentStatusType.COMPLETED


class UsernameOrderingTests(Base):
    def test_ordering_by_username_is_ignored_not_a_500(self):
        self.person()
        for value in ("username", "-username", "-signals,username"):
            with self.subTest(value):
                response = self.get_list(ordering=value)
                self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_the_example_in_the_docs_works(self):
        self.person()
        self.assertEqual(
            self.get_list(ordering="-signals,email").status_code, status.HTTP_200_OK
        )


class EnrollmentMatchBase(Base):
    """Two teachers' courses, A and B, each in its own session.

    - both: ENROLLED in A, WITHDRAWN from B;
    - left_a: WITHDRAWN from A;
    - done_b: ENROLLED in A, COMPLETED in B.
    """

    def setUp(self):
        super().setUp()
        self.teacher_a = self.person("teacher.a.match@x.example")
        self.teacher_b = self.person("teacher.b.match@x.example")
        self.course_a = self.make_course(self.teacher_a, "A")
        self.course_b = self.make_course(self.teacher_b, "B")
        self.both = self.student(
            "both.match@x.example", {"A": ENROLLED, "B": WITHDRAWN}
        )
        self.left_a = self.student("left.a.match@x.example", {"A": WITHDRAWN})
        self.done_b = self.student(
            "done.b.match@x.example", {"A": ENROLLED, "B": COMPLETED}
        )

    def make_course(self, teacher, name):
        session = Session.objects.create(name=f"Term {name}", teacher=teacher)
        return Course.objects.create(
            name=f"Course {name}", description="x", teacher=teacher, session=session
        )

    def student(self, email, statuses):
        user = self.person(email, user_type=UserTypes.STUDENT)
        courses = {"A": self.course_a, "B": self.course_b}
        for name, enrollment_status in statuses.items():
            StudentCourse.objects.create(
                student=user, course=courses[name], enrollment_status=enrollment_status
            )
        return user

    def emails(self, **params):
        return sorted(row["email"] for row in self.rows(**params))


class EnrollmentFiltersMatchTogetherTests(EnrollmentMatchBase):
    def test_course_and_status_match_the_same_enrollment(self):
        # The owner's case: withdrawn from B only must not count for A.
        self.assertEqual(
            self.emails(
                enrollments__course=self.course_a.pk,
                enrollments__enrollment_status=WITHDRAWN,
            ),
            ["left.a.match@x.example"],
        )

    def test_session_and_status_match_the_same_enrollment(self):
        self.assertEqual(
            self.emails(
                enrollments__course__session=self.course_a.session_id,
                enrollments__enrollment_status=WITHDRAWN,
            ),
            ["left.a.match@x.example"],
        )

    def test_course_and_session_match_the_same_enrollment(self):
        # SM -98: course A sits in session A, so "course A + session B" on
        # one enrollment matches nobody; checked separately it listed both
        # and done_b (in A, and in some course of session B).
        self.assertEqual(
            self.emails(
                enrollments__course=self.course_a.pk,
                enrollments__course__session=self.course_b.session_id,
            ),
            [],
        )


class StatusInRemovedTests(EnrollmentMatchBase):
    """Owner, 2026-11-11: enrollments__enrollment_status__in is removed. It
    is ignored like any unknown parameter."""

    def test_the_schema_no_longer_lists_it(self):
        from .tests_user_list_docs import list_parameters

        self.assertNotIn("enrollments__enrollment_status__in", list_parameters(self))

    def test_passing_it_neither_errors_nor_filters(self):
        everyone = self.emails()
        for value in (WITHDRAWN, f"{WITHDRAWN},{COMPLETED}", "NOT_A_STATUS"):
            with self.subTest(value):
                self.assertEqual(
                    self.emails(enrollments__enrollment_status__in=value), everyone
                )


class EnrollmentFiltersAloneTests(EnrollmentMatchBase):
    """Each filter alone: as before the change (green on the base too)."""

    def test_course_alone(self):
        self.assertEqual(
            self.emails(enrollments__course=self.course_a.pk),
            [
                "both.match@x.example",
                "done.b.match@x.example",
                "left.a.match@x.example",
            ],
        )

    def test_session_alone(self):
        self.assertEqual(
            self.emails(enrollments__course__session=self.course_b.session_id),
            ["both.match@x.example", "done.b.match@x.example"],
        )

    def test_status_alone(self):
        self.assertEqual(
            self.emails(enrollments__enrollment_status=WITHDRAWN),
            ["both.match@x.example", "left.a.match@x.example"],
        )


class EnrollmentFiltersStayInsideWhatYouCanSeeTests(EnrollmentMatchBase):
    def test_a_teacher_cannot_match_another_teachers_enrollment_together(self):
        # Teacher A asks about `both`'s withdrawal, which is in B's course:
        # together or alone, A only looks through A's own enrollments, so it
        # answers like a value that matches nothing (404).
        client = self.as_user(self.teacher_a)
        url = reverse("user-detail", kwargs={"pk": self.both.pk})
        # Else the 404 below proves nothing.
        self.assertEqual(client.get(url).status_code, status.HTTP_200_OK)
        # The retrieve cache ignores query params (known, backlog): without
        # this the next GET is answered from the cached 200.
        cache.clear()
        response = client.get(
            url,
            {
                "enrollments__course__session": self.course_b.session_id,
                "enrollments__enrollment_status": WITHDRAWN,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
