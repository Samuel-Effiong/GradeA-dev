"""H-279: an AI re-grade of a released grade waits for the teacher.

Owner rulings, 2026-11-02 and 2026-11-03 (relayed by SM):

1. Only the re-graded submission is affected; the assignment is not.
2. While a re-grade is held, the student and every analytic keep the
   released score and feedback. Nothing jumps or vanishes.
3. The teacher sees the held result and approves it with the existing
   publish routes (single and publish-all). The student then gets the
   existing "Updated grade" email, once per approval.

Further rulings:
- A hand edit is itself an approval.
- A second re-grade replaces the held one.
- Approval releases the newest held result.
- A held result is never discarded.
- The AI student summary is dispatched at approval, not at the held save.
- The review queue and teacher_feedback act on the released row only.

Design: docs/evidence/h279-regrade-pending-approval/DESIGN.md (d8e5adcbd).
"""

import copy
import inspect
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings as django_settings
from django.core.cache import cache
from django.urls import reverse
from rest_framework import serializers, status

import ai_processor.tests_reply_repeated_evaluation as paper
import assignments.serializers
import classrooms.serializers
import dashboard.serializers
import students.serializers
from assignments.models import Assignment
from audit.models import AuditEvent
from classrooms.models import StudentCourse
from classrooms.tests_final_grade_zero_score import grading_result, make_user
from students import services
from students.models import StudentSubmission
from students.services import (
    GRADING_RESULT_FIELDS,
    emit_grading_completed,
    grade_engine,
    grading_result_stamp,
)
from students.tests_formatting_task_superseded import SupersededBase
from students.tests_manual_grade_formatted_grade import OLD_STATEMENT
from users.models import Settings

PENDING_NAMES = ("pending_result", "pending_graded_at")
#: Every column a grading run writes, except the run's own state.
RELEASED_COLUMNS = tuple(
    name for name in GRADING_RESULT_FIELDS if name != "grading_state"
)
#: A key only the held result's feedback has, so "this feedback is the held
#: one" can be told from "it is the released one".
HELD_MARK = "h279-held-result"
TITLE = "Practice Questions"


def held_result(score, max_points=10, mark=HELD_MARK):
    result = grading_result(score, max_points)
    result["h279_mark"] = mark
    return result


class HeldBase(SupersededBase):
    """A released paper graded 7, worded, then released. Its student opted
    in to grade emails. The AI re-grades it with `regrade(score)`."""

    def setUp(self):
        super().setUp()
        settings_row, _ = Settings.objects.get_or_create(user=self.student)
        settings_row.notify_grading_complete = True
        settings_row.save()
        Assignment.objects.filter(pk=self.assignment.pk).update(
            questions=[paper.essay(1)]
        )
        self.emails = []
        mail = patch(
            "students.services.send_email_task.delay",
            side_effect=lambda **kwargs: self.emails.append(kwargs),
        )
        mail.start()
        self.addCleanup(mail.stop)
        summary = patch("students.services.student_summary_async")
        self.summary = summary.start()
        self.addCleanup(summary.stop)
        self.released_before = self.released_columns()

    # -- the row ----------------------------------------------------------

    def released_columns(self):
        row = self.row()
        return {name: getattr(row, name) for name in RELEASED_COLUMNS}

    def regrade(self, score, mark=HELD_MARK):
        """An AI re-grade, through the one write every grading path uses."""
        self.submission.refresh_from_db()
        services._populate_and_save_grade(
            self.submission, held_result(score, mark=mark), None
        )

    def regrade_through_the_pipeline(self, score, mark=HELD_MARK, meanwhile=None):
        """grade_engine, the AI call replaced, its after-commit work run."""

        def the_ai_call(*args, **kwargs):
            if meanwhile:
                meanwhile()
            return held_result(score, mark=mark)

        with (
            patch("students.services.ai_processor") as ai,
            patch("students.services._formatted_grade_task") as formatter,
            self.captureOnCommitCallbacks(execute=True),
        ):
            ai.extract_grade_with_retry.side_effect = the_ai_call
            formatter.return_value.delay.return_value.id = "h279-format-task"
            before = services.history.snapshot(self.row())
            graded = grade_engine(self.teacher, self.row())
            emit_grading_completed(graded, actor=self.teacher, before=before)
        return graded, formatter

    def held_score(self):
        return Decimal(str(self.row().pending_result["score"]))

    # -- the routes -------------------------------------------------------

    def publish(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.release()

    def publish_all(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.release_all()

    def teacher_reads(self):
        cache.clear()
        self.client.force_authenticate(self.teacher)
        response = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": self.submission.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def hand_edit(self, score):
        with (
            patch("students.views.formatted_grade_async") as task,
            self.captureOnCommitCallbacks(execute=True),
        ):
            task.delay.return_value.id = "h279-hand-edit-task"
            response = self.patch_grade(score)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response

    # -- what was recorded and sent ----------------------------------------

    def grade_changes(self):
        return AuditEvent.objects.filter(
            action="GRADE_CHANGE", target_id=self.submission.pk
        ).order_by("occurred_at")

    def subjects(self):
        return [email["subject"] for email in self.emails]


# ---------------------------------------------------------------------------
# The held save
# ---------------------------------------------------------------------------


class AReGradeOfAReleasedGradeIsHeld(HeldBase):
    def test_the_fixture_is_released_graded_and_worded(self):
        row = self.row()
        self.assertTrue(row.is_published)
        self.assertEqual(row.score, Decimal("7"))
        self.assertIn(OLD_STATEMENT, row.formatted_grade)
        self.assertIsNone(row.pending_result)
        self.assertIsNone(row.pending_graded_at)

    def test_every_released_column_is_left_as_it_was(self):
        self.regrade(3)

        self.assertEqual(self.released_columns(), self.released_before)

    def test_the_held_result_is_kept_whole(self):
        self.regrade(3)

        row = self.row()
        self.assertEqual(self.held_score(), Decimal("3"))
        self.assertEqual(Decimal(str(row.pending_result["score_percentage"])), 30)
        self.assertEqual(row.pending_result["feedback"]["h279_mark"], HELD_MARK)
        self.assertIsNotNone(row.pending_graded_at)
        self.assertGreater(row.pending_graded_at, row.graded_at)

    def test_the_run_itself_is_finished(self):
        self.regrade(3)

        self.assertEqual(self.row().grading_state, "DONE")

    def test_a_second_regrade_replaces_the_held_one(self):
        self.regrade(3)
        first = self.row().pending_graded_at

        self.regrade(5, mark="second")

        row = self.row()
        self.assertEqual(self.held_score(), Decimal("5"))
        self.assertEqual(row.pending_result["feedback"]["h279_mark"], "second")
        self.assertGreater(row.pending_graded_at, first)
        self.assertEqual(self.released_columns(), self.released_before)

    def test_a_publish_landing_mid_run_still_holds_the_result(self):
        """The decision is made at the save, under the row lock, not when
        the run starts: here the paper is released while the AI call runs."""
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            is_published=False
        )
        unreleased = self.released_columns()

        self.regrade_through_the_pipeline(3, meanwhile=self.release)

        row = self.row()
        self.assertTrue(row.is_published)
        self.assertEqual(self.held_score(), Decimal("3"))
        self.assertEqual(self.released_columns(), unreleased)


class WhatIsNotHeld(HeldBase):
    def test_a_regrade_of_an_unreleased_grade_writes_as_today(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            is_published=False
        )

        self.regrade(3)

        row = self.row()
        self.assertEqual(row.score, Decimal("3"))
        self.assertIsNone(row.formatted_grade)  # H-146, unchanged
        self.assertIsNone(row.pending_result)
        self.assertIsNone(row.pending_graded_at)

    def test_a_first_grading_of_a_published_row_is_not_held(self):
        """No released grade exists to keep (a row published with no grade
        is not reachable through the routes, which refuse it)."""
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            graded_at=None, score=None
        )

        self.regrade(3)

        row = self.row()
        self.assertEqual(row.score, Decimal("3"))
        self.assertIsNone(row.pending_result)


class WhileHeldNothingMoves(HeldBase):
    """Ruling 2: the student and every analytic keep the released grade."""

    def dashboards(self):
        reads = {}
        for user, name, kwargs in (
            (self.student, "student-summary", {"course_id": self.course.pk}),
            (self.student, "student-assignments", {}),
            (self.student, "student-overview", {}),
            (self.teacher, "teacher-admin-students", {"course_id": self.course.pk}),
        ):
            cache.clear()
            self.client.force_authenticate(user)
            response = self.client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(response.status_code, status.HTTP_200_OK, name)
            reads[name] = response.json()
        return reads

    def test_the_student_reads_the_released_grade(self):
        cache.clear()
        before = self.student_reads()

        self.regrade(3)

        cache.clear()
        after = self.student_reads()
        for key in ("score", "score_percentage", "max_points", "formatted_grade"):
            with self.subTest(key=key):
                self.assertEqual(after[key], before[key])
        self.assertIn(OLD_STATEMENT, str(after["formatted_grade"]))

    def test_the_answer_document_the_student_reads_is_unchanged(self):
        on_submission = self.on_the_submission(self.student)
        on_assignment = self.on_the_assignment(self.student)

        self.regrade(3)

        self.assertEqual(self.on_the_submission(self.student), on_submission)
        self.assertEqual(self.on_the_assignment(self.student), on_assignment)

    def test_the_final_grade_does_not_move(self):
        self.assert_final_grade("70.00")

        self.regrade(3)

        self.assert_final_grade("70.00")

    def test_the_dashboards_do_not_move(self):
        before = self.dashboards()

        self.regrade(3)

        self.assertEqual(self.dashboards(), before)

    def test_no_email_and_no_ai_summary_at_the_held_save(self):
        self.regrade_through_the_pipeline(3)

        self.assertEqual(self.emails, [])
        self.summary.delay.assert_not_called()

    def test_the_formatted_grade_task_is_queued_for_the_held_result(self):
        _, formatter = self.regrade_through_the_pipeline(3)

        formatter.return_value.delay.assert_called_once()
        stamp = formatter.return_value.delay.call_args.kwargs["result_stamp"]
        self.assertEqual(stamp, services.held_result_stamp(self.row()))
        self.assertNotEqual(stamp, grading_result_stamp(self.row()))


class TheHeldSaveIsAudited(HeldBase):
    def test_grading_completed_says_held_and_changes_nothing_released(self):
        self.regrade_through_the_pipeline(3)

        event = AuditEvent.objects.filter(
            action="GRADING_COMPLETED", target_id=self.submission.pk
        ).latest("occurred_at")
        self.assertIs(event.metadata.get("held_for_approval"), True)
        self.assertFalse(event.before)
        self.assertFalse(event.after)

    def test_a_grading_that_is_not_held_does_not_say_so(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            is_published=False
        )

        self.regrade_through_the_pipeline(3)

        event = AuditEvent.objects.filter(
            action="GRADING_COMPLETED", target_id=self.submission.pk
        ).latest("occurred_at")
        self.assertNotIn("held_for_approval", event.metadata)

    def test_the_held_save_writes_no_grade_change(self):
        changes = self.grade_changes().count()

        self.regrade_through_the_pipeline(3)

        self.assertEqual(self.grade_changes().count(), changes)


class TheTeacherSeesTheHeldResult(HeldBase):
    def test_on_the_teachers_page(self):
        self.regrade(3)

        data = self.teacher_reads()
        self.assertEqual(Decimal(str(data["score"])), Decimal("7"))
        self.assertEqual(Decimal(str(data["pending_result"]["score"])), Decimal("3"))
        self.assertIsNotNone(data["pending_graded_at"])

    def test_nothing_held_reads_as_null(self):
        data = self.teacher_reads()

        self.assertIsNone(data["pending_result"])
        self.assertIsNone(data["pending_graded_at"])

    def test_the_grade_route_answers_with_the_held_result(self):
        self.client.force_authenticate(self.teacher)
        with (
            patch("students.services.ai_processor") as ai,
            patch("students.services._formatted_grade_task") as formatter,
            self.captureOnCommitCallbacks(execute=True),
        ):
            ai.extract_grade_with_retry.return_value = held_result(3)
            formatter.return_value.delay.return_value.id = "h279-route-task"
            response = self.client.post(
                reverse("student-submission-grade", kwargs={"pk": self.submission.pk})
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(Decimal(str(response.data["score"])), Decimal("7"))
        self.assertEqual(
            Decimal(str(response.data["pending_result"]["score"])), Decimal("3")
        )

    def test_the_teacher_lists_held_rows_with_has_pending_result(self):
        self.regrade(3)
        url = reverse("student-submission-list")
        self.client.force_authenticate(self.teacher)

        for value, expected in (("true", [str(self.submission.pk)]), ("false", [])):
            with self.subTest(has_pending_result=value):
                cache.clear()
                response = self.client.get(
                    url,
                    {
                        "has_pending_result": value,
                        "assignment": str(self.assignment.pk),
                    },
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(
                    [str(row["id"]) for row in response.data["results"]], expected
                )


class AStudentLearnsNothingOfAHeldResult(HeldBase):
    """Q5: the fence on the approved names."""

    def test_the_students_own_page_and_list(self):
        self.regrade(3)
        cache.clear()
        self.client.force_authenticate(self.student)
        detail = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": self.submission.pk})
        ).data
        listed = self.client.get(reverse("student-submission-list")).data["results"]

        for data in [detail, *listed]:
            for name in PENDING_NAMES:
                with self.subTest(name=name):
                    self.assertIsNone(data.get(name))

    def test_a_student_cannot_filter_on_it(self):
        self.regrade(3)
        cache.clear()
        self.client.force_authenticate(self.student)
        for value in ("true", "false"):
            with self.subTest(value=value):
                response = self.client.get(
                    reverse("student-submission-list"), {"has_pending_result": value}
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_no_serializer_but_the_teachers_page_reads_them(self):
        """Static: a serializer may name a pending key only as the teacher's
        detail page, or as a constant null (StudentUploadAnswerSerializer
        mirrors that page's keys)."""
        teachers_page = students.serializers.StudentSubmissionDetailSerializer
        offenders = []
        for module in (
            students.serializers,
            assignments.serializers,
            classrooms.serializers,
            dashboard.serializers,
        ):
            for _, cls in inspect.getmembers(module, inspect.isclass):
                if not issubclass(cls, serializers.BaseSerializer):
                    continue
                if cls.__module__ != module.__name__ or cls is teachers_page:
                    continue
                meta_fields = getattr(getattr(cls, "Meta", None), "fields", ())
                declared = getattr(cls, "_declared_fields", {})
                for name in PENDING_NAMES:
                    field = declared.get(name)
                    constant_null = (
                        isinstance(field, students.serializers.SentAs)
                        and field.value is None
                    )
                    named = name in declared or (
                        isinstance(meta_fields, (list, tuple)) and name in meta_fields
                    )
                    if named and not constant_null:
                        offenders.append(f"{cls.__module__}.{cls.__name__}.{name}")
        self.assertEqual(offenders, [])

    def test_the_names_are_read_nowhere_a_student_route_reads(self):
        """The two columns are read only by the services that hold, promote
        and word a result, and by the teacher's page."""
        allowed = {
            "students/services.py",
            "students/serializers.py",
            "students/views.py",
            "students/models.py",
            "assignments/tasks.py",
        }
        found = set()
        for path in Path(django_settings.BASE_DIR).glob("*/**/*.py"):
            relative = str(path.relative_to(django_settings.BASE_DIR))
            if (
                "/tests" in relative
                or "/migrations/" in relative
                or relative.startswith(("docs/", ".venv", "venv"))
                or path.name.startswith("tests")
            ):
                continue
            text = path.read_text(encoding="utf-8")
            if any(name in text for name in PENDING_NAMES):
                found.add(relative)
        self.assertLessEqual(found, allowed, found - allowed)


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class PublishReleasesTheHeldResult(HeldBase):
    def assert_released_three(self):
        row = self.row()
        self.assertEqual(row.score, Decimal("3"))
        self.assertEqual(row.score_percentage, Decimal("30"))
        self.assertEqual(row.max_points, 10)
        self.assertEqual(row.feedback["h279_mark"], HELD_MARK)
        self.assertIsNone(row.pending_result)
        self.assertIsNone(row.pending_graded_at)
        return row

    def test_single_publish_promotes_it(self):
        self.regrade(3)
        held_at = self.row().pending_graded_at

        self.publish()

        row = self.assert_released_three()
        self.assertEqual(row.graded_at, held_at)
        self.assertTrue(row.is_published)

    def test_publish_all_promotes_it(self):
        self.regrade(3)

        self.publish_all()

        self.assert_released_three()

    def test_the_newest_held_result_is_the_one_released(self):
        """Q1, owner 2026-11-03: approval releases what is held at that
        moment. R1 is replaced by R2 before the publish."""
        self.regrade(3, mark="R1")
        self.regrade(5, mark="R2")
        changes = self.grade_changes().count()

        self.publish()

        row = self.row()
        self.assertEqual(row.score, Decimal("5"))
        self.assertEqual(row.feedback["h279_mark"], "R2")
        self.assertIsNone(row.pending_result)
        self.assertEqual(len(self.emails), 1)
        self.assertEqual(self.grade_changes().count(), changes + 1)

    def test_the_route_answers_with_the_promoted_result(self):
        self.regrade(3)
        self.client.force_authenticate(self.teacher)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse(
                    "student-submission-publish-grade",
                    kwargs={"pk": self.submission.pk},
                )
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(Decimal(str(response.data["score"])), Decimal("3"))
        self.assertIsNone(response.data["pending_result"])

    def test_the_student_reads_it_at_once_though_cached(self):
        self.regrade(3)
        cache.clear()
        self.student_reads()  # cached, as a student with the page open

        self.publish()

        self.assertEqual(Decimal(str(self.student_reads()["score"])), Decimal("3"))

    def test_the_final_grade_follows(self):
        self.regrade(3)

        self.publish()

        self.assert_final_grade("30.00")

    def test_one_grade_change_from_the_released_to_the_promoted(self):
        self.regrade(3)
        changes = self.grade_changes().count()

        self.publish()

        new = list(self.grade_changes())[changes:]
        self.assertEqual(len(new), 1)
        self.assertEqual(Decimal(str(new[0].before["score"])), Decimal("7"))
        self.assertEqual(Decimal(str(new[0].after["score"])), Decimal("3"))
        self.assertEqual(str(new[0].actor_id), str(self.teacher.pk))

    def test_the_ai_summary_is_dispatched_once_per_approval(self):
        """Q3: one at approval, none at the held save, none on a publish
        that promotes nothing."""
        self.regrade_through_the_pipeline(3)
        self.summary.delay.assert_not_called()

        self.publish()
        self.publish()

        self.summary.delay.assert_called_once_with(
            str(self.student.pk), str(self.teacher.pk), str(self.course.pk)
        )

    def test_a_publish_with_nothing_held_changes_nothing(self):
        changes = self.grade_changes().count()

        self.publish()

        self.assertEqual(self.released_columns(), self.released_before)
        self.assertEqual(self.emails, [])
        self.assertEqual(self.grade_changes().count(), changes)
        self.summary.delay.assert_not_called()

    def test_an_unreleased_row_that_somehow_holds_a_result_is_released_with_it(self):
        """SM's guard: nothing unpublishes, but a row that holds a result
        while unpublished must not be released by the first-release flip
        with the result still held beside it."""
        self.regrade(3)
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            is_published=False
        )

        self.publish()

        row = self.assert_released_three()
        self.assertTrue(row.is_published)
        self.assertEqual(self.subjects(), [f"Assignment graded: {TITLE}"])


class AHandEditIsAnApproval(HeldBase):
    def test_the_edit_applies_on_top_of_the_held_result(self):
        self.regrade(3)

        self.hand_edit(4)

        row = self.row()
        self.assertEqual(row.score, Decimal("4"))
        self.assertEqual(row.feedback["h279_mark"], HELD_MARK)
        self.assertEqual(row.feedback["grading_summary"]["total_score"], 4)
        self.assertIsNone(row.pending_result)
        self.assertIsNone(row.pending_graded_at)

    def test_one_grade_change_from_released_to_edited(self):
        self.regrade(3)
        changes = self.grade_changes().count()

        self.hand_edit(4)

        new = list(self.grade_changes())[changes:]
        self.assertEqual(len(new), 1)
        self.assertEqual(Decimal(str(new[0].before["score"])), Decimal("7"))
        self.assertEqual(Decimal(str(new[0].after["score"])), Decimal("4"))


class WhatDoesNotApprove(HeldBase):
    """Q2 and Q4: the review queue and teacher_feedback act on the
    released row only."""

    def test_mark_reviewed_leaves_the_held_result(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            needs_review=True
        )
        self.regrade(3)
        held = self.row().pending_result
        self.client.force_authenticate(self.teacher)

        response = self.client.post(
            reverse(
                "student-submission-mark-reviewed", kwargs={"pk": self.submission.pk}
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        row = self.row()
        self.assertEqual(row.pending_result, held)
        self.assertEqual(row.score, Decimal("7"))
        self.assertFalse(row.needs_review)

    def test_teacher_feedback_leaves_the_held_result(self):
        self.regrade(3)
        held = self.row().pending_result
        self.client.force_authenticate(self.teacher)

        response = self.client.get(
            reverse(
                "student-submission-teacher-feedback", kwargs={"pk": self.submission.pk}
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(self.row().pending_result, held)
        self.assertEqual(self.released_columns(), self.released_before)

    def test_the_review_queue_does_not_list_a_held_flag(self):
        """A held result with a review reason stays inside the held result;
        the queue keeps meaning the released grade."""
        flagged = held_result(3)
        flagged["answers_not_found"] = [
            {"question_number": 1, "answer_status": "not_found", "score_awarded": 0}
        ]
        self.submission.refresh_from_db()
        services._populate_and_save_grade(self.submission, flagged, None)

        row = self.row()
        self.assertFalse(row.needs_review)
        self.assertTrue(row.pending_result["needs_review"])


# ---------------------------------------------------------------------------
# The formatted grade of a held result
# ---------------------------------------------------------------------------


class TheHeldResultsWording(HeldBase):
    def test_its_task_words_the_held_result_and_leaves_the_released_text(self):
        self.regrade(3)

        self.run_task(services.held_result_stamp(self.row()))

        row = self.row()
        self.assertIn(OLD_STATEMENT, row.formatted_grade)
        self.assertIn(
            "You scored 3 out of 10 points", row.pending_result["formatted_grade"]
        )

    def test_the_wording_is_released_with_the_result(self):
        self.regrade(3)
        self.run_task(services.held_result_stamp(self.row()))

        self.publish()

        self.assertIn("You scored 3 out of 10 points", self.stored_formatted())
        self.assertNotIn(OLD_STATEMENT, self.stored_formatted())

    def test_approved_before_its_task_ran_the_task_writes_the_released_row(self):
        self.regrade(3)
        stamp = services.held_result_stamp(self.row())
        self.publish()
        self.assertIsNone(self.stored_formatted())  # H-146: no old sentence

        self.run_task(stamp)

        self.assertIn("You scored 3 out of 10 points", self.stored_formatted())

    def test_approved_during_its_ai_call_the_task_words_the_approved_numbers(self):
        """The task loaded the row while the result was held; the teacher
        approved it during the AI call. The task writes the released row,
        with the numbers the row holds under the lock: the approved ones."""
        self.regrade(3)
        stamp = services.held_result_stamp(self.row())

        self.run_task(stamp, meanwhile=self.publish)

        self.assertIn("You scored 3 out of 10 points", self.stored_formatted())
        self.assertNotIn("You scored 7 out of 10 points", self.stored_formatted())

    def test_a_replaced_held_results_task_writes_nothing(self):
        self.regrade(3)
        first = services.held_result_stamp(self.row())
        self.regrade(5, mark="second")

        self.run_task(first)

        row = self.row()
        self.assertIsNone(row.pending_result.get("formatted_grade"))
        self.assertIn(OLD_STATEMENT, row.formatted_grade)

    def test_the_released_results_own_task_still_words_the_released_result(self):
        released_stamp = grading_result_stamp(self.row())
        self.regrade(3)

        self.run_task(released_stamp)

        row = self.row()
        self.assertIn("You scored 7 out of 10 points", row.formatted_grade)
        self.assertIsNone(row.pending_result.get("formatted_grade"))


# ---------------------------------------------------------------------------
# Notification on approval (N1-N10, approved as written)
# ---------------------------------------------------------------------------


class TheStudentIsToldOncePerApproval(HeldBase):
    def test_n1_single_publish_sends_the_updated_grade_email(self):
        self.regrade(3)

        self.publish()

        self.assertEqual(self.subjects(), [f"Updated grade: {TITLE}"])
        self.assertIn("30", self.emails[0]["message"])
        self.assertNotIn("70", self.emails[0]["message"])

    def test_n2_a_double_click_sends_one(self):
        self.regrade(3)
        changes = self.grade_changes().count()

        self.publish()
        self.publish()

        self.assertEqual(len(self.emails), 1)
        self.assertEqual(self.grade_changes().count(), changes + 1)

    def test_n5_publish_all_twice_sends_one(self):
        self.regrade(3)

        self.publish_all()
        self.publish_all()

        self.assertEqual(self.subjects(), [f"Updated grade: {TITLE}"])

    def test_n7_an_opted_out_student_gets_none_and_the_result_is_still_released(self):
        Settings.objects.filter(user=self.student).update(notify_grading_complete=False)
        self.regrade(3)

        self.publish()

        self.assertEqual(self.emails, [])
        self.assertEqual(self.row().score, Decimal("3"))

    def test_n7_a_placeholder_address_gets_none(self):
        type(self.student).objects.filter(pk=self.student.pk).update(
            email="pupil@student.local"
        )
        self.regrade(3)

        self.publish()

        self.assertEqual(self.emails, [])
        self.assertEqual(self.row().score, Decimal("3"))

    def test_n8_a_hand_edit_of_a_held_row_sends_one(self):
        self.regrade(3)

        self.hand_edit(4)

        self.assertEqual(self.subjects(), [f"Updated grade: {TITLE}"])

    def test_n9_the_held_save_sends_none(self):
        self.regrade(3)
        self.regrade_through_the_pipeline(5)

        self.assertEqual(self.emails, [])

    def test_n10_a_promotion_that_fails_sends_none_and_changes_nothing(self):
        self.regrade(3)
        broken = copy.deepcopy(self.row().pending_result)
        broken["score"] = "not a number"
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            pending_result=broken
        )

        self.client.force_authenticate(self.teacher)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse(
                    "student-submission-publish-grade",
                    kwargs={"pk": self.submission.pk},
                )
            )

        # the route's error answer (not a raised exception: the API's
        # handler answers for it); nothing was released or sent
        self.assertEqual(response.status_code, 500)

        self.assertEqual(self.emails, [])
        self.assertEqual(self.released_columns(), self.released_before)
        self.assertEqual(self.row().pending_result, broken)


class N4PublishAllOverAMix(HeldBase):
    """New, held, released with nothing held, and ungraded, in one call."""

    def setUp(self):
        super().setUp()
        self.others = {}
        for kind in ("new", "released", "ungraded"):
            student = make_user(f"h279-{kind}@x.test", "STUDENT")
            # make_user names every user "Test Student", and a course
            # refuses a second student of the same name.
            student.first_name, student.last_name = kind.title(), "Pupil"
            student.save(update_fields=["first_name", "last_name"])
            StudentCourse.objects.create(
                student=student, course=self.course, enrollment_status="ENROLLED"
            )
            Settings.objects.update_or_create(
                user=student, defaults={"notify_grading_complete": True}
            )
            submission = StudentSubmission.objects.create(
                student=student, assignment=self.assignment, answers={}
            )
            if kind != "ungraded":
                services._populate_and_save_grade(
                    submission, grading_result(8, 10), None
                )
            if kind == "released":
                StudentSubmission.objects.filter(pk=submission.pk).update(
                    is_published=True
                )
            self.others[kind] = submission
        self.regrade(3)
        self.emails.clear()

    def test_each_student_is_told_once_with_the_right_email(self):
        self.publish_all()

        by_recipient = sorted(
            (email["recipient_list"][0], email["subject"]) for email in self.emails
        )
        self.assertEqual(
            by_recipient,
            sorted(
                [
                    ("h279-new@x.test", f"Assignment graded: {TITLE}"),
                    (self.student.email, f"Updated grade: {TITLE}"),
                ]
            ),
        )

    def test_the_held_row_is_promoted_and_the_count_is_as_today(self):
        self.client.force_authenticate(self.teacher)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse(
                    "assignment-publish-all-grades", kwargs={"pk": self.assignment.pk}
                )
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        # graded rows: new, released, held (the fixture's) -> 3, as today
        self.assertEqual(
            response.data["message"], "Successfully published 3 submissions."
        )
        self.assertEqual(self.row().score, Decimal("3"))
        self.assertIsNone(self.row().pending_result)
        self.assertTrue(
            StudentSubmission.objects.get(pk=self.others["new"].pk).is_published
        )
