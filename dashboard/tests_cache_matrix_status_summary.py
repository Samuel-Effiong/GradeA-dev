"""H-1 Stage 3: freshness of the status-summary cache family added on beta.

Beta commit 24c0a7b ("Add unified assignment status-summary endpoint")
added `StudentAdminDashboardView.status_summary` (URL name
`student-status-summary`), which caches with a RAW `cache.set` for 15
minutes under

* `studentadmins:user_id__<student>:view__status_summary:all`
* `studentadmins:user_id__<student>:view__status_summary:course__<course>`
  (with `?course=<id>`).

Neither key goes through `versioned_key`, so no generation bump can ever
reach it. It arrived after Stage 3's targeted invalidation was built
(merged into this branch at 0ca0404), so nothing in Stage 3 covers it.
The only thing that clears it is the legacy `*studentadmin*` wildcard,
which is still in the code on the owner's instruction.

This suite records what that means, per (write path x variant), in two
modes:

* `StatusSummaryWildcardsOnTests` - legacy wildcards live: production
  today. Every cell must be FRESH here, or it is a live staleness bug.
* `StatusSummaryWildcardsOffTests` - `legacy_wildcards_disabled()`: plan
  step 4 simulated. The assertions pin the cells that go STALE. They are
  the step-4 prerequisite: this family needs a generation-versioned key
  (or an equivalent bump) before the wildcards can be removed. When that
  lands these tests fail by design and must be flipped to FRESH.

Every test also reads two controls:

* an unrelated student (different teacher, different course), both
  variants, which must be UNAFFECTED in every cell and never SPURIOUS;
* the same student's `student-overview`, which reports the same four
  counts from the same `_assignment_status_counts` helper but IS
  generation-versioned (gap G2). It is FRESH in both modes, which shows
  the OFF-mode staleness belongs to this key family, not to a missing
  bump on the write path.

A key probe inside each mutation records whether the raw status-summary
entries physically survived the write: evicted with the wildcards on,
left in place with them off.

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
from AutoGrader.tests_cache_matrix_support import (
    FRESH,
    STALE,
    UNAFFECTED,
    FreshnessMatrixMixin,
    Read,
    legacy_wildcards_disabled,
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
    """The exact raw key `status_summary` writes (dashboard/views.py)."""
    if course is None:
        return f"studentadmins:user_id__{student.id}:view__status_summary:all"
    return (
        f"studentadmins:user_id__{student.id}"
        f":view__status_summary:course__{course.id}"
    )


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
    only the two concrete classes below (which add TransactionTestCase)
    run.
    """

    reset_sequences = True

    #: Set by the concrete class: True = production today, False = step 4.
    legacy_wildcards_live: bool

    def setUp(self):
        cache.clear()
        if self.legacy_wildcards_live:
            # The wildcard mechanism is only real if the backend can
            # pattern-delete; otherwise the ON column would be vacuous.
            self.assertTrue(
                hasattr(cache, "delete_pattern"),
                "cache backend has no delete_pattern; wildcards cannot run",
            )
        else:
            patched = self.enterContext(legacy_wildcards_disabled())
            self.assertTrue(patched, "no legacy module was patched")

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

        Also asserts, for every raw status-summary key that existed before
        the write, whether it physically survived it: evicted with the
        wildcards live, untouched with them off.
        """
        keys = self.raw_keys(variant_course)
        presence = {}

        def mutate_and_probe():
            before = {name: cache.get(key) is not None for name, key in keys.items()}
            mutate()
            for name, key in keys.items():
                presence[name] = (before[name], cache.get(key) is not None)

        result = self.run_matrix(label, self.reads(variant_course), mutate_and_probe)
        observed = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(observed, expected, "\n" + result.table())

        for outcome in result.outcomes:
            if outcome.verdict == STALE:
                # STALE here means the pre-write payload, byte for byte.
                self.assertEqual(outcome.cached_after, outcome.before, result.table())

        expect_survives = not self.legacy_wildcards_live
        for name, (was_present, is_present) in presence.items():
            if was_present:
                self.assertEqual(
                    is_present,
                    expect_survives,
                    f"{label}: raw key for {name!r} "
                    f"{'survived' if is_present else 'was evicted'}",
                )
        return result


class StatusSummaryWildcardsOnTests(StatusSummaryMatrixBase, TransactionTestCase):
    """Production today: the legacy `*studentadmin*` wildcard is live.

    Every write path that moves the counts runs a receiver or service that
    calls the wildcard, which matches the `studentadmins:` prefix and
    evicts the raw key. Every cell is FRESH; no live staleness.
    """

    legacy_wildcards_live = True

    def test_publish_draft_assignment_is_fresh(self):
        """Teacher publishes a draft in the student's course: Not Submitted +1."""
        self.run_case(
            "ON: publish draft assignment",
            self.course_a,
            self.publish_draft_assignment,
            verdicts(FRESH, FRESH),
        )

    def test_student_submits_is_fresh(self):
        """Student submits: Submitted +1, Not Submitted -1."""
        self.run_case(
            "ON: student submits",
            self.course_a,
            self.student_submits,
            verdicts(FRESH, FRESH),
        )

    def test_teacher_publishes_grade_is_fresh(self):
        """Single publish (`.update()` + `invalidate_submission_caches`): Graded +1."""
        self.run_case(
            "ON: publish grade",
            self.course_a,
            self.teacher_publishes_grade,
            verdicts(FRESH, FRESH),
        )

    def test_teacher_publishes_all_grades_is_fresh(self):
        """publish-all-grades (bulk `.update()` + bulk invalidation): Graded +1."""
        self.run_case(
            "ON: publish all grades",
            self.course_a,
            self.teacher_publishes_all_grades,
            verdicts(FRESH, FRESH),
        )

    def test_teacher_extends_due_date_is_fresh(self):
        """Overdue assignment's due date moved to the future: Overdue -1, Not Submitted +1."""
        self.run_case(
            "ON: extend due date",
            self.course_a,
            self.teacher_extends_overdue_due_date,
            verdicts(FRESH, FRESH),
        )

    def test_teacher_unpublishes_assignment_is_fresh(self):
        """Published -> UNPUBLISHED: Not Submitted -1."""
        self.run_case(
            "ON: unpublish assignment",
            self.course_a,
            self.teacher_unpublishes_assignment,
            verdicts(FRESH, FRESH),
        )

    def test_teacher_deletes_assignment_is_fresh(self):
        """DELETE assignment: Not Submitted -1."""
        self.run_case(
            "ON: delete assignment",
            self.course_a,
            self.teacher_deletes_assignment,
            verdicts(FRESH, FRESH),
        )

    def test_enrolment_into_new_course_is_fresh(self):
        """course-students enrols the student in course B: `all` gains B's work.
        `?course=B` goes 404 -> 200 and was never cached (a 404 is not written)."""
        self.run_case(
            "ON: enrol into course B",
            self.course_b,
            self.teacher_enrolls_student_in_course_b,
            verdicts(FRESH, FRESH),
        )

    def test_withdrawal_is_fresh(self):
        """PATCH enrolment to WITHDRAWN: `all` drops to zero, `?course=A` 200 -> 404."""
        self.run_case(
            "ON: withdraw",
            self.course_a,
            self.teacher_withdraws_student,
            verdicts(FRESH, FRESH),
        )

    def test_removal_from_course_is_fresh(self):
        """DELETE course/<pk>/student/<id> (enrolment row deleted): as withdrawal."""
        self.run_case(
            "ON: remove from course",
            self.course_a,
            self.teacher_removes_student,
            verdicts(FRESH, FRESH),
        )

    def test_course_deactivation_is_fresh(self):
        """PATCH course is_active=false: `all` drops to zero, `?course=A` 200 -> 404."""
        self.run_case(
            "ON: deactivate course",
            self.course_a,
            self.teacher_deactivates_course,
            verdicts(FRESH, FRESH),
        )

    def test_withdraw_publish_reenrol_is_fresh(self):
        """Withdraw, publish while withdrawn, re-enrol: Not Submitted +1 on return."""
        self.run_case(
            "ON: withdraw, publish, re-enrol",
            self.course_a,
            self.withdraw_publish_then_reenroll,
            verdicts(FRESH, FRESH),
        )


class StatusSummaryWildcardsOffTests(StatusSummaryMatrixBase, TransactionTestCase):
    """Plan step 4 simulated: legacy wildcards disabled.

    The status-summary keys are not generation-versioned, so nothing
    clears them. Every cell whose truth moves is STALE for the 15-minute
    TTL, except the `?course=` cells where the student loses access: that
    variant runs its enrolment/is_active check BEFORE the cache lookup, so
    the 404 is served fresh (the orphaned entry stays behind - see the
    re-enrolment test).

    These assertions are the step-4 prerequisite, not a desired state:
    step 4 must add a generation bump for this family (build both keys
    with `versioned_key(..., [(SCOPE_USER, student.id)])`, as G2 did for
    the sibling overview/summary keys) before removing the wildcards.
    When it does, these tests fail by design; flip them to FRESH. The
    versioned overview control is FRESH in every row, proving the write
    paths already bump the student and only the key needs versioning.
    """

    legacy_wildcards_live = False

    def test_publish_draft_assignment_is_stale_step4_prerequisite(self):
        """Step 4 must version this family: a newly published assignment never appears."""
        self.run_case(
            "OFF: publish draft assignment",
            self.course_a,
            self.publish_draft_assignment,
            verdicts(STALE, STALE),
        )

    def test_student_submits_is_stale_step4_prerequisite(self):
        """Step 4 must version this family: the student's own submission is not counted."""
        self.run_case(
            "OFF: student submits",
            self.course_a,
            self.student_submits,
            verdicts(STALE, STALE),
        )

    def test_teacher_publishes_grade_is_stale_step4_prerequisite(self):
        """Step 4 must version this family: a released grade is not counted as Graded."""
        self.run_case(
            "OFF: publish grade",
            self.course_a,
            self.teacher_publishes_grade,
            verdicts(STALE, STALE),
        )

    def test_teacher_publishes_all_grades_is_stale_step4_prerequisite(self):
        """Step 4 must version this family: publish-all-grades not counted as Graded."""
        self.run_case(
            "OFF: publish all grades",
            self.course_a,
            self.teacher_publishes_all_grades,
            verdicts(STALE, STALE),
        )

    def test_teacher_extends_due_date_is_stale_step4_prerequisite(self):
        """Step 4 must version this family: an extended assignment still shows Overdue."""
        self.run_case(
            "OFF: extend due date",
            self.course_a,
            self.teacher_extends_overdue_due_date,
            verdicts(STALE, STALE),
        )

    def test_teacher_unpublishes_assignment_is_stale_step4_prerequisite(self):
        """Step 4 must version this family: an unpublished assignment is still counted."""
        self.run_case(
            "OFF: unpublish assignment",
            self.course_a,
            self.teacher_unpublishes_assignment,
            verdicts(STALE, STALE),
        )

    def test_teacher_deletes_assignment_is_stale_step4_prerequisite(self):
        """Step 4 must version this family: a deleted assignment is still counted."""
        self.run_case(
            "OFF: delete assignment",
            self.course_a,
            self.teacher_deletes_assignment,
            verdicts(STALE, STALE),
        )

    def test_enrolment_into_new_course_all_is_stale_step4_prerequisite(self):
        """Step 4 must version this family: `all` omits the new course's work.
        `?course=B` is FRESH only because a 404 was never cached."""
        self.run_case(
            "OFF: enrol into course B",
            self.course_b,
            self.teacher_enrolls_student_in_course_b,
            verdicts(STALE, FRESH),
        )

    def test_withdrawal_all_is_stale_step4_prerequisite(self):
        """Step 4 must version this family: `all` keeps counting the course the
        student left (counts only, no course content). `?course=A` is FRESH
        (404) only because its access check precedes the cache lookup."""
        self.run_case(
            "OFF: withdraw",
            self.course_a,
            self.teacher_withdraws_student,
            verdicts(STALE, FRESH),
        )

    def test_removal_from_course_all_is_stale_step4_prerequisite(self):
        """Step 4 must version this family: same shape as withdrawal, via row delete."""
        self.run_case(
            "OFF: remove from course",
            self.course_a,
            self.teacher_removes_student,
            verdicts(STALE, FRESH),
        )

    def test_course_deactivation_all_is_stale_step4_prerequisite(self):
        """Step 4 must version this family: `all` keeps counting a deactivated
        course; `?course=A` 404s fresh via the pre-cache access check."""
        self.run_case(
            "OFF: deactivate course",
            self.course_a,
            self.teacher_deactivates_course,
            verdicts(STALE, FRESH),
        )

    def test_reenrolment_resurfaces_orphaned_course_entry_step4_prerequisite(self):
        """Step 4 must version this family: the pre-cache access check does not
        protect `?course=`. The entry orphaned at withdrawal is served again
        on re-enrolment, without the assignment published meanwhile."""
        self.run_case(
            "OFF: withdraw, publish, re-enrol",
            self.course_a,
            self.withdraw_publish_then_reenroll,
            verdicts(STALE, STALE),
        )
