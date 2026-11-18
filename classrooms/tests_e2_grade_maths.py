"""Epic E2: grade categories, relative weights, excused and missing work
(FR-E-03..08, FR-E-12/13; plan docs/evidence/epic-e2/STEP0_PLAN.md §3).

The maths lives in `classrooms.grade_service` only (FR-E-06). Formula
(SM 2026-11-18, plan correction 1):

* category grade = Σ(relative_weight × score) / Σ(relative_weight × points)
  × 100, so with every relative weight 1 it is today's points-weighted
  formula, and a 5-point quiz never counts as much as a 100-point test;
* final grade = Σ(category weight × category grade) / Σ weight, over the
  categories that have graded work (Q4); clamped 0-100, half-up 2dp;
* uncategorized work counts nothing in a course with categories (Q3);
* excused work counts on neither side; missing work (published, a due date
  set and past, no submission, not excused) counts as 0 only when the
  course says so (Q2).

Property tests use seeded random corpora (no new dependency); a failing
seed is named in the message.
"""

import random
from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django_celery_beat.models import PeriodicTask

from assignments.models import AssignmentStatus
from audit.enums import AuditAction
from audit.models import AuditEvent
from classrooms import grade_categories, grade_service
from classrooms.grade_service import GradeRow, points_weighted, weighted_grade
from classrooms.models import CourseCategory, StudentCourse

from .tests_e1_grade_parity import _DbCorpusMixin
from .tests_e2_fixtures import (
    categories_off,
    categories_on,
    enroll,
    future,
    grade,
    make_assignment,
    make_course,
    make_student,
    make_teacher,
    past,
    stored_grade,
)

D = Decimal
SEEDS = range(40)


def _rng(seed):
    return random.Random(20_000 + seed)


def _random_rows(rng, category_ids):
    rows = []
    for _ in range(rng.randint(0, 9)):
        points = rng.choice([None, 0, 1, 5, 10, 20, 37, 100])
        score = (
            None
            if rng.random() < 0.1
            else D(str(round(rng.uniform(0, (points or 10) * 1.2), 2)))
        )
        rows.append(
            GradeRow(
                category_id=rng.choice(category_ids),
                relative_weight=rng.choice([D("1"), D("0.5"), D("2"), D("3.25")]),
                score=score,
                points=points,
            )
        )
    return rows


class WeightedGradeFormulaTest(SimpleTestCase):
    """The pure formula, no database."""

    def test_no_categories_unit_weights_is_todays_formula(self):
        for seed in SEEDS:
            rng = _rng(seed)
            rows = [
                row._replace(relative_weight=D("1"))
                for row in _random_rows(rng, [None])
            ]
            self.assertEqual(
                weighted_grade(rows, None).final,
                points_weighted((r.score, r.points) for r in rows),
                f"seed {seed}",
            )

    def test_fr_e_05_relative_weight_two_counts_twice(self):
        rows = [
            GradeRow("exam", D("2"), D("90"), 100),  # final exam
            GradeRow("exam", D("1"), D("60"), 100),  # midterm
        ]
        result = weighted_grade(rows, {"exam": D("100")})
        self.assertEqual(result.final, D("80.00"))

    def test_points_still_count_inside_a_category(self):
        # 1/5 on a quiz and 90/100 on a test, same relative weight: the test
        # dominates (not the plain mean of 20% and 90%).
        rows = [
            GradeRow("hw", D("1"), D("1"), 5),
            GradeRow("hw", D("1"), D("90"), 100),
        ]
        self.assertEqual(weighted_grade(rows, {"hw": D("100")}).final, D("86.67"))

    def test_empty_category_weight_is_shared_q4(self):
        rows = [
            GradeRow("a", D("1"), D("80"), 100),
            GradeRow("b", D("1"), D("50"), 100),
        ]
        cats = {"a": D("50"), "b": D("30"), "c": D("20")}
        # (50 × 80 + 30 × 50) / 80
        self.assertEqual(weighted_grade(rows, cats).final, D("68.75"))

    def test_uncategorized_work_counts_nothing_q3(self):
        rows = [
            GradeRow("a", D("1"), D("80"), 100),
            GradeRow("b", D("1"), D("50"), 100),
            GradeRow(None, D("1"), D("0"), 100),
        ]
        cats = {"a": D("50"), "b": D("30"), "c": D("20")}
        self.assertEqual(weighted_grade(rows, cats).final, D("68.75"))

    def test_nothing_graded_is_none(self):
        self.assertIsNone(weighted_grade([], None).final)
        self.assertIsNone(weighted_grade([], {"a": D("100")}).final)
        self.assertIsNone(
            weighted_grade([GradeRow(None, D("1"), D("5"), 10)], {"a": D("100")}).final
        )

    def test_only_zero_weight_categories_with_work_is_none(self):
        rows = [GradeRow("b", D("1"), D("5"), 10)]
        self.assertIsNone(weighted_grade(rows, {"a": D("100"), "b": D("0")}).final)

    def test_rows_that_cannot_be_weighted_are_skipped(self):
        rows = [
            GradeRow("a", D("1"), D("8"), 10),
            GradeRow("a", D("1"), None, 10),
            GradeRow("a", D("1"), D("3"), 0),
            GradeRow("a", D("1"), D("3"), None),
        ]
        self.assertEqual(weighted_grade(rows, {"a": D("100")}).final, D("80.00"))

    def test_properties_over_random_corpora(self):
        for seed in SEEDS:
            rng = _rng(seed)
            ids = ["a", "b", "c", None]
            cats = {
                "a": D(str(rng.choice([0, 10, 25, 50]))),
                "b": D(str(rng.choice([10, 25, 50]))),
                "c": D(str(rng.choice([0, 25]))),
            }
            rows = _random_rows(rng, ids)
            result = weighted_grade(rows, cats)
            # idempotent and order-free
            self.assertEqual(result, weighted_grade(rows, cats), f"seed {seed}")
            shuffled = list(rows)
            rng.shuffle(shuffled)
            self.assertEqual(
                result.final, weighted_grade(shuffled, cats).final, f"seed {seed}"
            )
            # scaling every relative weight changes nothing
            scaled = [r._replace(relative_weight=r.relative_weight * 3) for r in rows]
            self.assertEqual(
                result.final, weighted_grade(scaled, cats).final, f"seed {seed}"
            )
            if result.final is None:
                continue
            # the documented scale
            self.assertGreaterEqual(result.final, D("0"), f"seed {seed}")
            self.assertLessEqual(result.final, D("100"), f"seed {seed}")
            # FR-E-13: the breakdown sums to the final grade exactly
            self.assertEqual(
                sum(c.contribution for c in result.categories),
                result.final,
                f"seed {seed}",
            )


class PlainCourseUnchangedTest(_DbCorpusMixin, TestCase):
    """A course with no categories, no excuses and missing work not counted
    gets exactly today's number with the flag on."""

    def test_flag_on_matches_flag_off_on_seeded_corpora(self):
        for seed in range(12):
            _, courses, _, students = self.build_db_corpus(seed)
            for student in students:
                for course in courses:
                    with self.settings(GRADE_CATEGORIES_ENABLED=False):
                        before = grade_service.course_grade(student.id, course.id)
                    with self.settings(GRADE_CATEGORIES_ENABLED=True):
                        after = grade_service.course_grade(student.id, course.id)
                    self.assertEqual(before, after, f"seed {seed}")


class _CategoryCourse(TestCase):
    """Exam 60 / Homework 40; exam 70/100, hw1 9/10, hw2 10/20.
    Homework = 19/30 = 63.33..; final = 0.6 × 70 + 0.4 × 63.33.. = 67.33."""

    def setUp(self):
        self.teacher = make_teacher()
        self.course = make_course(self.teacher)
        self.student = make_student()
        enroll(self.student, self.course)
        grade_categories.set_course_categories(
            self.course,
            [
                {"name": "Exam", "weight_percent": "60"},
                {"name": "Homework", "weight_percent": "40"},
            ],
            actor=self.teacher,
        )
        self.exam = CourseCategory.objects.get(course=self.course, name="Exam")
        self.hw = CourseCategory.objects.get(course=self.course, name="Homework")
        self.exam1 = make_assignment(self.course, "Exam 1", category=self.exam)
        self.hw1 = make_assignment(
            self.course, "HW 1", total_points=10, category=self.hw
        )
        self.hw2 = make_assignment(
            self.course, "HW 2", total_points=20, category=self.hw
        )
        grade(self.student, self.exam1, 70)
        grade(self.student, self.hw1, 9)
        grade(self.student, self.hw2, 10)

    def grade_changes(self):
        return AuditEvent.objects.filter(
            action=AuditAction.GRADE_CHANGE, target_type="StudentCourse"
        )


@categories_on
class CategoryCourseGradeTest(_CategoryCourse):
    def test_stored_grade_uses_categories(self):
        self.assertEqual(stored_grade(self.student, self.course), D("67.33"))
        self.assertEqual(
            grade_service.course_grade(self.student.id, self.course.id), D("67.33")
        )

    def test_released_grade_counts_released_work_only(self):
        self.hw2.submissions.update(is_published=False)
        subs = list(
            self.student.submissions.select_related("assignment").filter(
                assignment__course=self.course
            )
        )
        # Homework = 9/10 = 90; 0.6 × 70 + 0.4 × 90 = 78
        self.assertEqual(
            grade_service.released_course_grade(subs, self.course.id), D("78.00")
        )

    def test_uncategorized_assignment_counts_nothing(self):
        loose = make_assignment(self.course, "Loose")
        grade(self.student, loose, 0)
        self.assertEqual(stored_grade(self.student, self.course), D("67.33"))

    def test_breakdown_sums_to_the_final_grade(self):
        loose = make_assignment(self.course, "Loose")
        breakdown = grade_service.grade_breakdown(self.student.id, self.course.id)
        self.assertEqual(breakdown["final_grade"], D("67.33"))
        self.assertEqual(
            sum(D(c["contribution"]) for c in breakdown["categories"]), D("67.33")
        )
        by_name = {c["name"]: c for c in breakdown["categories"]}
        self.assertEqual(by_name["Exam"]["grade"], D("70.00"))
        self.assertEqual(by_name["Homework"]["grade"], D("63.33"))
        self.assertEqual(
            [a["assignment_id"] for a in breakdown["uncategorized"]], [str(loose.id)]
        )

    def test_preview_writes_nothing(self):
        events = AuditEvent.objects.count()
        preview = grade_service.preview_course_grades(
            self.course.id,
            [
                {"id": str(self.exam.id), "name": "Exam", "weight_percent": "50"},
                {"id": str(self.hw.id), "name": "Homework", "weight_percent": "50"},
            ],
        )
        row = preview["students"][0]
        self.assertEqual(row["student_id"], str(self.student.id))
        self.assertEqual(row["current"], D("67.33"))
        self.assertEqual(row["proposed"], D("66.67"))
        self.assertEqual(preview["class_average"]["proposed"], D("66.67"))
        self.assertEqual(stored_grade(self.student, self.course), D("67.33"))
        self.assertEqual(
            list(
                CourseCategory.objects.filter(course=self.course)
                .order_by("name")
                .values_list("weight_percent", flat=True)
            ),
            [D("60.00"), D("40.00")],
        )
        self.assertEqual(AuditEvent.objects.count(), events)


@categories_on
class RecalculationTriggerTest(_CategoryCourse):
    """FR-E-07: each change recomputes the stored grade in one transaction,
    with a GRADE_CHANGE carrying before and after."""

    def test_new_weights_recompute_and_are_audited(self):
        grade_categories.set_course_categories(
            self.course,
            [
                {"id": str(self.exam.id), "name": "Exam", "weight_percent": "50"},
                {"id": str(self.hw.id), "name": "Homework", "weight_percent": "50"},
            ],
            actor=self.teacher,
        )
        self.assertEqual(stored_grade(self.student, self.course), D("66.67"))
        change = self.grade_changes().order_by("occurred_at").last()
        assert change is not None
        self.assertEqual(change.before, {"final_grade": "67.33"})
        self.assertEqual(change.after, {"final_grade": "66.67"})
        cats = (
            AuditEvent.objects.filter(action=AuditAction.GRADE_CATEGORIES_CHANGE)
            .order_by("occurred_at")
            .last()
        )
        assert cats is not None
        self.assertEqual(cats.before["weight_percents"], ["60.00", "40.00"])
        self.assertEqual(cats.after["weight_percents"], ["50.00", "50.00"])

    def test_recalculation_is_idempotent(self):
        before = self.grade_changes().count()
        self.assertEqual(grade_categories.recalculate_course(self.course.id), [])
        self.assertEqual(grade_categories.recalculate_course(self.course.id), [])
        self.assertEqual(self.grade_changes().count(), before)

    def test_relative_weight_change_recomputes(self):
        self.hw1.relative_weight = D("3")
        self.hw1.save()
        # Homework = (3 × 9 + 10) / (3 × 10 + 20) = 74; 42 + 29.6 = 71.60
        self.assertEqual(stored_grade(self.student, self.course), D("71.60"))

    def test_category_change_recomputes(self):
        self.hw2.category = self.exam
        self.hw2.save()
        # Exam = 80/120 = 66.67; Homework = 90; 40 + 36 = 76.00
        self.assertEqual(stored_grade(self.student, self.course), D("76.00"))

    def test_total_points_change_recomputes(self):
        self.hw2.total_points = 10
        self.hw2.save()
        # Homework = 19/20 = 95; 42 + 38 = 80.00
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))

    def test_excuse_and_its_removal_recompute_and_are_audited(self):
        grade_categories.excuse_work(self.hw2, self.student, actor=self.teacher)
        # Homework = 9/10 = 90; 42 + 36 = 78.00
        self.assertEqual(stored_grade(self.student, self.course), D("78.00"))
        self.assertTrue(
            AuditEvent.objects.filter(action=AuditAction.WORK_EXCUSED).exists()
        )
        change = self.grade_changes().order_by("occurred_at").last()
        assert change is not None
        self.assertEqual(change.before, {"final_grade": "67.33"})
        self.assertEqual(change.after, {"final_grade": "78.00"})

        grade_categories.remove_excuse(self.hw2, self.student, actor=self.teacher)
        self.assertEqual(stored_grade(self.student, self.course), D("67.33"))
        self.assertTrue(
            AuditEvent.objects.filter(action=AuditAction.EXCUSE_REMOVED).exists()
        )

    def test_cross_course_grade_leaves_out_excused_work(self):
        grade_categories.excuse_work(self.hw2, self.student, actor=self.teacher)
        subs = list(self.student.submissions.select_related("assignment"))
        # 70 + 9 out of 100 + 10; hw2 excused
        self.assertEqual(grade_service.cross_course_grade(subs), D("71.82"))


@categories_off
class FlagOffKeepsTodayTest(_CategoryCourse):
    def test_total_points_change_does_not_recompute_while_dark(self):
        stored = stored_grade(self.student, self.course)
        self.hw2.total_points = 10
        self.hw2.save()
        self.assertEqual(stored_grade(self.student, self.course), stored)

    def test_no_missing_work_task_while_dark(self):
        self.course.missing_counts_as_zero = True
        self.course.save()
        make_assignment(self.course, "Later", due_date=future())
        self.assertFalse(
            PeriodicTask.objects.filter(name__startswith="missing-work-").exists()
        )


@categories_on
class MissingWorkTest(TestCase):
    """Q2: missing work counts as 0 only when the course says so."""

    def setUp(self):
        self.teacher = make_teacher()
        self.course = make_course(self.teacher)
        self.student = make_student()
        enroll(self.student, self.course)
        done = make_assignment(self.course, "Done", due_date=past())
        grade(self.student, done, 80)
        self.missed = make_assignment(self.course, "Missed", due_date=past())
        make_assignment(self.course, "Not due", due_date=future())
        make_assignment(self.course, "No due date")
        make_assignment(
            self.course, "Draft", due_date=past(), status=AssignmentStatus.DRAFT
        )

    def test_missing_work_doesnt_count_by_default(self):
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))

    def test_course_setting_counts_missing_work_as_zero(self):
        self.course.missing_counts_as_zero = True
        self.course.save()
        # 80 / 200: only "Missed" is missing
        self.assertEqual(stored_grade(self.student, self.course), D("40.00"))
        breakdown = grade_service.grade_breakdown(self.student.id, self.course.id)
        self.assertEqual(breakdown["missing_count"], 1)

    def test_excused_missing_work_doesnt_count(self):
        self.course.missing_counts_as_zero = True
        self.course.save()
        grade_categories.excuse_work(self.missed, self.student, actor=self.teacher)
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))

    def test_toggling_the_setting_off_recomputes(self):
        self.course.missing_counts_as_zero = True
        self.course.save()
        self.course.missing_counts_as_zero = False
        self.course.save()
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))


@categories_on
class MissingWorkDueDateTaskTest(TestCase):
    """SM ruling 2026-11-18 (D2 exception): one clocked task per published
    assignment with a due date in a missing-counts course, kept in sync the
    way reminders are, recomputes the course when the due date passes."""

    def setUp(self):
        self.teacher = make_teacher()
        self.course = make_course(self.teacher)
        self.course.missing_counts_as_zero = True
        self.course.save()
        self.student = make_student()
        enroll(self.student, self.course)
        done = make_assignment(self.course, "Done", due_date=past())
        grade(self.student, done, 80)
        self.later = make_assignment(self.course, "Later", due_date=future(2))

    def task(self, assignment=None):
        name = grade_categories.missing_work_task_name((assignment or self.later).id)
        return PeriodicTask.objects.filter(name=name).select_related("clocked").first()

    def test_task_is_made_at_the_due_date(self):
        task = self.task()
        self.assertIsNotNone(task)
        self.assertEqual(task.clocked.clocked_time, self.later.due_date)
        self.assertTrue(task.one_off)
        self.assertEqual(task.task, "classrooms.tasks.recalculate_missing_work")

    def test_no_task_when_the_course_doesnt_count_missing_work(self):
        other = make_course(self.teacher)
        later = make_assignment(other, "Elsewhere", due_date=future())
        self.assertIsNone(self.task(later))

    def test_firing_changes_the_stored_grade(self):
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))
        # The due date passes: no save happens.
        type(self.later).objects.filter(pk=self.later.pk).update(due_date=past())
        from classrooms.tasks import recalculate_missing_work

        recalculate_missing_work(str(self.later.id))
        self.assertEqual(stored_grade(self.student, self.course), D("40.00"))

    def test_double_run_is_idempotent(self):
        type(self.later).objects.filter(pk=self.later.pk).update(due_date=past())
        from classrooms.tasks import recalculate_missing_work

        recalculate_missing_work(str(self.later.id))
        changes = AuditEvent.objects.filter(action=AuditAction.GRADE_CHANGE).count()
        recalculate_missing_work(str(self.later.id))
        self.assertEqual(stored_grade(self.student, self.course), D("40.00"))
        self.assertEqual(
            AuditEvent.objects.filter(action=AuditAction.GRADE_CHANGE).count(), changes
        )

    def test_a_task_for_a_course_that_no_longer_qualifies_does_nothing(self):
        type(self.later).objects.filter(pk=self.later.pk).update(due_date=past())
        type(self.course).objects.filter(pk=self.course.pk).update(
            missing_counts_as_zero=False
        )
        from classrooms.tasks import recalculate_missing_work

        recalculate_missing_work(str(self.later.id))
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))

    def test_new_due_date_retimes_the_task(self):
        new_due = self.later.due_date + timedelta(days=3)
        self.later.due_date = new_due
        self.later.save()
        self.assertEqual(self.task().clocked.clocked_time, new_due)

    def test_unpublish_removes_the_task(self):
        self.later.status = AssignmentStatus.UNPUBLISHED
        self.later.save()
        self.assertIsNone(self.task())

    def test_clearing_the_due_date_removes_the_task(self):
        self.later.due_date = None
        self.later.save()
        self.assertIsNone(self.task())

    def test_course_toggle_off_removes_the_task(self):
        self.course.missing_counts_as_zero = False
        self.course.save()
        self.assertIsNone(self.task())

    def test_course_toggle_on_makes_the_task(self):
        self.course.missing_counts_as_zero = False
        self.course.save()
        self.course.missing_counts_as_zero = True
        self.course.save()
        self.assertIsNotNone(self.task())

    def test_delete_removes_the_task(self):
        name = grade_categories.missing_work_task_name(self.later.id)
        self.later.delete()
        self.assertFalse(PeriodicTask.objects.filter(name=name).exists())

    def test_a_due_date_already_past_makes_no_task(self):
        gone = make_assignment(self.course, "Gone", due_date=past())
        self.assertIsNone(self.task(gone))


class EnrollmentStillGuardedTest(TestCase):
    """The recompute writes only through the enrollment row it locks."""

    @categories_on
    def test_recalculate_course_skips_unenrolled_students(self):
        teacher = make_teacher()
        course = make_course(teacher)
        self.assertEqual(grade_categories.recalculate_course(course.id), [])
        self.assertFalse(StudentCourse.objects.filter(course=course).exists())
