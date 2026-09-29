"""H-1: freshness of the status-summary cache family added on beta.

Beta commit 24c0a7b ("Add unified assignment status-summary endpoint")
added `StudentAdminDashboardView.status_summary` (URL name
`student-status-summary`), which caches for 15 minutes under

* `studentadmins:user_id__<student>:view__status_summary:all`
* `studentadmins:user_id__<student>:view__status_summary:course__<course>`
  (with `?course=<id>`).

It arrived after Stage 3's targeted invalidation was built and wrote both
keys with a RAW `cache.set`, so no generation bump could reach them; only
the legacy `*studentadmin*` wildcard cleared them. H-1 step 4 put both keys
on `versioned_key(..., [(SCOPE_USER, student.id)])`, exactly like the
sibling overview/summary/assignments keys (gap G2), and then removed the
wildcards.

History: this suite first ran every cell twice, with the wildcards live
(all FRESH) and with them patched out (STALE on every path - the step-4
prerequisite, docs/evidence/H1_STAGE3_TARGETED_INVALIDATION_EVIDENCE.md
§8). The wildcards no longer exist, so there is one mode: the real code.
Every cell is FRESH on the generation bump alone.

Every test also reads two controls:

* an unrelated student (different teacher, different course), both
  variants, which must be UNAFFECTED in every cell and never SPURIOUS;
* the same student's `student-overview`, which reports the same four
  counts from the same `_assignment_status_counts` helper under the same
  `usr(student)` scope. It is FRESH in every row.

A key probe inside each mutation records, for every warmed entry, whether
the live (generation-versioned) key moved and whether the pre-write entry
physically survived: the student's key must move and the unrelated
student's must not, and every old entry is left in place (nothing is
deleted; a superseded entry is simply unreachable and ages out).

Write paths that cannot change this response, and so have no row here:

* AI grading (`grade`, `grade-async`, auto-grade on due date, the grading
  claim) and the manual `update-grade` override: `assignments_graded`
  counts only RELEASED grades (`is_published=True` and a score), and
  grading never publishes. The other three counts depend only on whether
  a submission row exists.
* A re-submission to an assignment already submitted: the row already
  exists, so no count moves.
* `activate_pending_enrollments_on_login` (PENDING -> ENROLLED, a
  signal-free `.update()`): `StudentCourse.objects.active()` excludes only
  WITHDRAWN, so a PENDING enrolment is already counted.
* The P4 repair commands (`bulk_update` of titles, questions, rigor):
  none of those columns is read by the counts.

Not a write path, so not a matrix row: a due date passing moves an
assignment from Not Submitted to Overdue with no write at all. Only the
15-minute TTL bounds that, under either mechanism, and it applies to the
versioned overview equally.

Fixtures follow the Stage 3 rule (plan §0): rows are created directly with
exactly the fields production sets; every mutation runs through the real
endpoint or the service function the endpoint calls. The only thing
patched is the external AI extraction call on the submit path.

Real Redis + real Postgres.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.cache_generation import SCOPE_USER, versioned_key
from AutoGrader.tests_cache_matrix_support import (
    FRESH,
    STALE,
    UNAFFECTED,
    FreshnessMatrixMixin,
    Read,
)
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import GradingState, StudentSubmission
from students.services import upload_answers_engine
from users.models import UserTypes

User = get_user_model()

STUDENT_ALL = "student status-summary (all)"
STUDENT_COURSE = "student status-summary (?course=)"
ISOLATION_ALL = "unrelated student status-summary (all)"
ISOLATION_COURSE = "unrelated student status-summary (?course=)"
OVERVIEW_CONTROL = "student overview (versioned control)"

EXTRACTED_ANSWERS = {"answers": [{"question_number": 1, "answer_html": "<p>one</p>"}]}


def question(number=1):
    return {
        "question_number": number,
        "question_text": f"Q{number}",
        "question_type": "OBJECTIVE",
        "points": 10,
        "options": ["one", "two"],
        "rubric": [],
        "model_answer": "one",
    }


def make_active_user(email, user_type, first_name):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
    )


def status_summary_key(student, course=None):
    """The live key `status_summary` would read right now (dashboard/views.py):
    the base key versioned on the student's current generation."""
    if course is None:
        base = f"studentadmins:user_id__{student.id}:view__status_summary:all"
    else:
        base = (
            f"studentadmins:user_id__{student.id}"
            f":view__status_summary:course__{course.id}"
        )
    return versioned_key(base, [(SCOPE_USER, student.id)])


def verdicts(student_all, student_course, overview=FRESH):
    """Expected verdict per read. The unrelated student is UNAFFECTED in
    every cell of both modes, so it is fixed here rather than per test."""
    return {
        STUDENT_ALL: student_all,
        STUDENT_COURSE: student_course,
        ISOLATION_ALL: UNAFFECTED,
        ISOLATION_COURSE: UNAFFECTED,
        OVERVIEW_CONTROL: overview,
    }


class StatusSummaryMatrixBase(FreshnessMatrixMixin):
    """Fixture, write paths and the per-case runner.

    Defines no `test_*` method, so the loader collects nothing from it;
    only the concrete class below (which adds TransactionTestCase) runs.
    """

    reset_sequences = True

    def setUp(self):
        cache.clear()

        now = timezone.now()
        future = now + timedelta(days=7)
        past = now - timedelta(days=2)

        self.teacher = make_active_user("ss-teacher@x.test", UserTypes.TEACHER, "SST")
        self.other_teacher = make_active_user(
            "ss-other-teacher@x.test", UserTypes.TEACHER, "SSOtherT"
        )
        self.student = make_active_user("ss-student@x.test", UserTypes.STUDENT, "SSS")
        self.other_student = make_active_user(
            "ss-other-student@x.test", UserTypes.STUDENT, "SSOtherS"
        )

        self.session = Session.objects.create(name="SS term", teacher=self.teacher)
        self.course_a = Course.objects.create(
            name="SS course A", teacher=self.teacher, session=self.session
        )
        self.course_b = Course.objects.create(
            name="SS course B", teacher=self.teacher, session=self.session
        )
        self.enrollment_a = StudentCourse.objects.create(
            student=self.student,
            course=self.course_a,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        # Course A, as the student sees it: submitted 1, not submitted 1,
        # overdue 1, graded 0 (the graded grade is not yet released).
        self.a_open = Assignment.objects.create(
            title="SS open",
            course=self.course_a,
            status=AssignmentStatus.PUBLISHED,
            due_date=future,
            questions=[question()],
        )
        self.a_overdue = Assignment.objects.create(
            title="SS overdue",
            course=self.course_a,
            status=AssignmentStatus.PUBLISHED,
            due_date=past,
            questions=[question()],
        )
        self.a_graded = Assignment.objects.create(
            title="SS graded",
            course=self.course_a,
            status=AssignmentStatus.PUBLISHED,
            due_date=future,
            questions=[question()],
        )
        self.a_draft = Assignment.objects.create(
            title="SS draft",
            course=self.course_a,
            status=AssignmentStatus.DRAFT,
            due_date=future,
            questions=[question()],
        )
        # Graded, not yet published: the fields a finished grading run
        # writes (students/services.py `_populate_and_save_grade`).
        self.graded_submission = StudentSubmission.objects.create(
            assignment=self.a_graded,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "one"}],
            graded_at=now,
            score=Decimal("8.0"),
            score_percentage=Decimal("80.0"),
            max_points=10,
            grading_state=GradingState.DONE,
            is_published=False,
        )

        # Course B: the student is not enrolled yet.
        Assignment.objects.create(
            title="SS course B open",
            course=self.course_b,
            status=AssignmentStatus.PUBLISHED,
            due_date=future,
            questions=[question()],
        )

        # The unrelated tenant: another teacher, another course.
        self.other_session = Session.objects.create(
            name="SS other term", teacher=self.other_teacher
        )
        self.course_c = Course.objects.create(
            name="SS course C", teacher=self.other_teacher, session=self.other_session
        )
        StudentCourse.objects.create(
            student=self.other_student,
            course=self.course_c,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        for title, due in (("SS C open", future), ("SS C overdue", past)):
            Assignment.objects.create(
                title=title,
                course=self.course_c,
                status=AssignmentStatus.PUBLISHED,
                due_date=due,
                questions=[question()],
            )

        self.url = reverse("student-status-summary")

    # ----------------------------------------------------------------- reads

    def reads(self, variant_course):
        return [
            Read(STUDENT_ALL, self.student, self.url),
            Read(
                STUDENT_COURSE,
                self.student,
                f"{self.url}?course={variant_course.id}",
            ),
            Read(ISOLATION_ALL, self.other_student, self.url),
            Read(
                ISOLATION_COURSE,
                self.other_student,
                f"{self.url}?course={self.course_c.id}",
            ),
            Read(OVERVIEW_CONTROL, self.student, reverse("student-overview")),
        ]

    def raw_keys(self, variant_course):
        return {
            STUDENT_ALL: status_summary_key(self.student),
            STUDENT_COURSE: status_summary_key(self.student, variant_course),
            ISOLATION_ALL: status_summary_key(self.other_student),
            ISOLATION_COURSE: status_summary_key(self.other_student, self.course_c),
        }

    # ----------------------------------------------------------- write paths

    def as_teacher(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        return client

    def patch_assignment(self, assignment, payload):
        response = self.as_teacher().patch(
            reverse("assignment-detail", args=[assignment.pk]), payload, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)

    def set_enrollment_status(self, status):
        response = self.as_teacher().patch(
            reverse("student-course-detail", args=[self.enrollment_a.pk]),
            {"enrollment_status": status},
        )
        self.assertEqual(response.status_code, 200, response.content)

    def publish_draft_assignment(self):
        self.patch_assignment(self.a_draft, {"status": "PUBLISHED"})

    def student_submits(self):
        # The service `upload-answers` calls; only the external AI
        # extraction is stubbed. It creates the row with save(), which
        # fires the StudentSubmission receivers exactly as production does.
        with patch(
            "students.services.ai_processor.extract_answer_with_retry",
            return_value=EXTRACTED_ANSWERS,
        ):
            upload_answers_engine(
                self.a_open, [{"type": "text", "text": "one"}], self.student
            )
        self.assertTrue(
            StudentSubmission.objects.filter(
                assignment=self.a_open, student=self.student
            ).exists()
        )

    def teacher_publishes_grade(self):
        response = self.as_teacher().post(
            reverse(
                "student-submission-publish-grade", args=[self.graded_submission.pk]
            )
        )
        self.assertEqual(response.status_code, 200, response.content)

    def teacher_publishes_all_grades(self):
        response = self.as_teacher().post(
            reverse("assignment-publish-all-grades", args=[self.a_graded.pk])
        )
        self.assertEqual(response.status_code, 200, response.content)

    def teacher_extends_overdue_due_date(self):
        self.patch_assignment(
            self.a_overdue,
            {"due_date": (timezone.now() + timedelta(days=10)).isoformat()},
        )

    def teacher_unpublishes_assignment(self):
        self.patch_assignment(self.a_open, {"status": "UNPUBLISHED"})

    def teacher_deletes_assignment(self):
        response = self.as_teacher().delete(
            reverse("assignment-detail", args=[self.a_open.pk])
        )
        self.assertEqual(response.status_code, 204, response.content)

    def teacher_enrolls_student_in_course_b(self):
        response = self.as_teacher().post(
            reverse("course-students", kwargs={"pk": self.course_b.pk}),
            {"email": self.student.email},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def teacher_withdraws_student(self):
        self.set_enrollment_status("WITHDRAWN")

    def teacher_removes_student(self):
        response = self.as_teacher().delete(
            reverse(
                "course-remove-student",
                kwargs={"pk": self.course_a.pk, "student_id": self.student.pk},
            )
        )
        self.assertEqual(response.status_code, 200, response.content)

    def teacher_deactivates_course(self):
        response = self.as_teacher().patch(
            reverse("course-detail", args=[self.course_a.pk]),
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def withdraw_publish_then_reenroll(self):
        self.set_enrollment_status("WITHDRAWN")
        self.publish_draft_assignment()
        self.set_enrollment_status("ENROLLED")

    # ---------------------------------------------------------------- runner

    def run_case(self, label, variant_course, mutate, expected):
        """Run one matrix row and assert the full observed verdict map.

        Also probes, for every status-summary entry warmed before the write:

        * the live key: the student's must move to a new generation (that is
          what makes the cached answer FRESH), the unrelated student's must
          not (isolation, asserted on the key, not only on the payload);
        * the pre-write entry: physically left in place - nothing is
          deleted any more, it is simply unreachable.
        """
        probe = {}

        def mutate_and_probe():
            keys_before = self.raw_keys(variant_course)
            present_before = {
                name: cache.get(key) is not None for name, key in keys_before.items()
            }
            mutate()
            keys_after = self.raw_keys(variant_course)
            for name, key in keys_before.items():
                probe[name] = (
                    present_before[name],
                    cache.get(key) is not None,
                    keys_after[name] != key,
                )

        result = self.run_matrix(label, self.reads(variant_course), mutate_and_probe)
        observed = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(observed, expected, "\n" + result.table())

        for outcome in result.outcomes:
            if outcome.verdict == STALE:
                # STALE here means the pre-write payload, byte for byte.
                self.assertEqual(outcome.cached_after, outcome.before, result.table())

        for name, (was_present, is_present, key_moved) in probe.items():
            self.assertEqual(
                key_moved,
                name in (STUDENT_ALL, STUDENT_COURSE),
                f"{label}: live key for {name!r} "
                f"{'moved' if key_moved else 'did not move'}",
            )
            if was_present:
                self.assertTrue(
                    is_present,
                    f"{label}: pre-write entry for {name!r} was deleted; "
                    "nothing should delete cache entries any more",
                )
        return result


class StatusSummaryFreshnessTests(StatusSummaryMatrixBase, TransactionTestCase):
    """The real code, with no wildcard left anywhere.

    Formerly the step-4 prerequisite: with raw keys and the wildcards
    patched out, every cell whose truth moved was STALE here. Step 4 built
    both keys with `versioned_key(..., [(SCOPE_USER, student.id)])`, as G2
    did for the sibling overview/summary keys, so every cell is FRESH on the
    generation bump alone. The runner also proves HOW: the student's live
    key moved to a new generation while the pre-write entry physically
    survived (nothing was deleted), and the unrelated student's key did not
    move at all. `run_matrix` additionally fails any mutation that issues a
    keyspace SCAN other than the PDF exact-prefix clear.
    """

    def test_publish_draft_assignment_is_fresh(self):
        """A newly published assignment appears (G1 fan-out bumps the student)."""
        self.run_case(
            "publish draft assignment",
            self.course_a,
            self.publish_draft_assignment,
            verdicts(FRESH, FRESH),
        )

    def test_student_submits_is_fresh(self):
        """The student's own submission is counted (submission receiver)."""
        self.run_case(
            "student submits",
            self.course_a,
            self.student_submits,
            verdicts(FRESH, FRESH),
        )

    def test_teacher_publishes_grade_is_fresh(self):
        """A released grade is counted as Graded (`invalidate_submission_caches`)."""
        self.run_case(
            "publish grade",
            self.course_a,
            self.teacher_publishes_grade,
            verdicts(FRESH, FRESH),
        )

    def test_teacher_publishes_all_grades_is_fresh(self):
        """publish-all-grades is counted as Graded (bulk invalidation, G3)."""
        self.run_case(
            "publish all grades",
            self.course_a,
            self.teacher_publishes_all_grades,
            verdicts(FRESH, FRESH),
        )

    def test_teacher_extends_due_date_is_fresh(self):
        """An extended assignment leaves Overdue (G1 fan-out)."""
        self.run_case(
            "extend due date",
            self.course_a,
            self.teacher_extends_overdue_due_date,
            verdicts(FRESH, FRESH),
        )

    def test_teacher_unpublishes_assignment_is_fresh(self):
        """An unpublished assignment stops being counted (G1 fan-out)."""
        self.run_case(
            "unpublish assignment",
            self.course_a,
            self.teacher_unpublishes_assignment,
            verdicts(FRESH, FRESH),
        )

    def test_teacher_deletes_assignment_is_fresh(self):
        """A deleted assignment stops being counted (G1 fan-out on post_delete)."""
        self.run_case(
            "delete assignment",
            self.course_a,
            self.teacher_deletes_assignment,
            verdicts(FRESH, FRESH),
        )

    def test_enrolment_into_new_course_is_fresh(self):
        """`all` gains the new course's work (enrolment receiver bumps the
        student). `?course=B` goes 404 -> 200; a 404 is never cached."""
        self.run_case(
            "enrol into course B",
            self.course_b,
            self.teacher_enrolls_student_in_course_b,
            verdicts(FRESH, FRESH),
        )

    def test_withdrawal_is_fresh(self):
        """`all` stops counting the course the student left; `?course=A`
        goes 200 -> 404 (its access check precedes the cache lookup)."""
        self.run_case(
            "withdraw",
            self.course_a,
            self.teacher_withdraws_student,
            verdicts(FRESH, FRESH),
        )

    def test_removal_from_course_is_fresh(self):
        """Same shape as withdrawal, via the enrolment row's post_delete."""
        self.run_case(
            "remove from course",
            self.course_a,
            self.teacher_removes_student,
            verdicts(FRESH, FRESH),
        )

    def test_course_deactivation_is_fresh(self):
        """`all` stops counting a deactivated course (G5 `_course_scopes`
        fan-out); `?course=A` 404s via the pre-cache access check."""
        self.run_case(
            "deactivate course",
            self.course_a,
            self.teacher_deactivates_course,
            verdicts(FRESH, FRESH),
        )

    def test_withdraw_publish_reenrol_is_fresh(self):
        """The case the raw keys got wrong even behind the access check: the
        `?course=` entry cached before withdrawal was served again on
        re-enrolment, without the assignment published meanwhile. Versioned,
        the re-enrolment (and the withdrawal before it) moved the student's
        generation, so the orphaned entry is unreachable."""
        self.run_case(
            "withdraw, publish, re-enrol",
            self.course_a,
            self.withdraw_publish_then_reenroll,
            verdicts(FRESH, FRESH),
        )
