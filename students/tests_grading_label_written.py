"""BE-I-04 slice C: the label is written with the grade.

When a grading finishes, the six label columns are saved by the SAME UPDATE
as the score, so a grade and its label cannot disagree and a grade cannot
be missing its label. Held here, on the real save:

  * the six columns after a grading, from what the run kept;
  * one UPDATE, not two;
  * a failed grading leaves no label; a re-grade replaces it;
  * the old grade-all job goes the same way;
  * a teacher's manual change of a grade does not touch the label;
  * the formatting job saves only its own field;
  * the audit entry carries the settings version, the strictness and the
    three lists of models, and the backup measurement reads the fresh
    calls only.

The provider is not called: `students.services.ai_processor` is replaced,
and the stand-in fills the run it is handed, as the grading service does.
Every model name in a stand-in is a real string, or None on purpose
(rule 14).
"""

import ast
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from ai_processor import services as ai_services
from ai_processor.grading_run import GradingRun
from assignments.models import Assignment
from audit import emitter as audit_emitter
from audit import history
from audit import metadata as audit_metadata
from audit.enums import AuditAction, AuditOutcome
from classrooms.models import Course, Session
from students import grading_label
from students import services as student_services
from students.grading_label import LABEL_FIELDS, UNLABELLED
from students.models import StudentSubmission
from students.services import grade_engine
from users.models import CustomUser, UserTypes

MAIN = ai_services.MAIN_MODEL
BACKUP = ai_services.GRADING_FALLBACK_MODELS[0]
TABLE = StudentSubmission._meta.db_table
QUOTED_TABLE = '"' + TABLE + '"'


def _assigned(column):
    """How an UPDATE names a column it sets: `"column" =`."""
    return '"' + column + '" ='


GRADING = {
    "grading_summary": {"total_score": 8, "max_total_points": 10, "percentage": 80.0},
    "grading_confidence": 90,
    "question_evaluations": [],
}


def _a_grading_that_kept(*kept):
    """A stand-in for `extract_grade_with_retry`. `kept` is a list of
    (method name, arguments) applied to the run it is handed."""

    def grade(*args, **kwargs):
        run = kwargs["run"]
        for method, arguments in kept:
            getattr(run, method)(*arguments)
        return dict(GRADING)

    return grade


def _label_in_the_database(submission):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT %s FROM %s WHERE id = %%s" % (", ".join(LABEL_FIELDS), TABLE),
            [submission.pk],
        )
        return dict(zip(LABEL_FIELDS, cursor.fetchone(), strict=True))


class _GradingCase(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="label-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.student = CustomUser.objects.create_user(
            email="label-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(name="C", teacher=self.teacher, session=session)
        self.assignment = Assignment.objects.create(
            title="A",
            course=course,
            questions=[{"question_number": 1, "points": 10, "model_answer": "4"}],
        )
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "x"}],
        )
        for target in (
            "students.services.student_summary_async",
            "students.services.launch_processing_task",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("students.services.ai_processor")
        self.ai = patcher.start()
        self.addCleanup(patcher.stop)

    def grade(self, *kept):
        self.ai.extract_grade_with_retry.side_effect = _a_grading_that_kept(*kept)
        with self.captureOnCommitCallbacks(execute=True):
            return grade_engine(self.teacher, self.submission)


class TheLabelIsSavedWithTheGradeTest(_GradingCase):
    def test_the_grading_service_is_handed_a_run(self):
        self.grade(("keep_answers", (MAIN, 1)))
        run = self.ai.extract_grade_with_retry.call_args.kwargs.get("run")
        self.assertIsInstance(run, GradingRun)

    def test_the_six_columns_after_a_grading_by_the_main_model(self):
        self.grade(("keep_answers", (MAIN, 1)))
        run = self.ai.extract_grade_with_retry.call_args.kwargs["run"]
        label = _label_in_the_database(self.submission)
        self.assertEqual(
            label,
            {
                "grading_prompt_version": ai_services.GRADING_ASSIGNMENT_PROMPT.version,
                "grading_config_version": run.config.version,
                "grading_strictness": grading_label.STRICTNESS_NOT_YET_SET,
                "grading_model": MAIN,
                "grading_fallback_used": grading_label.FALLBACK_NO,
                "grading_release": run.config.release,
            },
        )
        self.assertRegex(label["grading_config_version"], r"\Acfg:[0-9a-f]{12}\Z")
        self.assertNotIn(UNLABELLED, label.values())

    def test_a_backup_model_is_flagged(self):
        self.grade(("keep_answers", (MAIN, 1)), ("keep_call", (BACKUP,)))
        label = _label_in_the_database(self.submission)
        self.assertEqual(label["grading_model"], MAIN)
        self.assertEqual(label["grading_fallback_used"], grading_label.FALLBACK_YES)

    def test_a_model_the_provider_did_not_name_is_unknown_never_a_guess(self):
        self.grade(("keep_answers", (None, 1)))
        label = _label_in_the_database(self.submission)
        self.assertEqual(label["grading_model"], grading_label.MODEL_UNKNOWN)
        self.assertEqual(label["grading_fallback_used"], grading_label.FALLBACK_UNKNOWN)

    def test_a_grading_with_no_ai_call_is_deterministic(self):
        self.grade()
        label = _label_in_the_database(self.submission)
        self.assertEqual(label["grading_model"], grading_label.MODEL_DETERMINISTIC)
        self.assertEqual(
            label["grading_fallback_used"], grading_label.FALLBACK_NOT_APPLICABLE
        )
        # The instructions version in force is recorded all the same (F5).
        self.assertEqual(
            label["grading_prompt_version"],
            ai_services.GRADING_ASSIGNMENT_PROMPT.version,
        )

    def test_a_wholly_reused_grading_names_the_model_that_first_answered(self):
        self.grade(("keep_reused", (BACKUP,)))
        label = _label_in_the_database(self.submission)
        self.assertEqual(label["grading_model"], BACKUP)
        self.assertEqual(label["grading_fallback_used"], grading_label.FALLBACK_YES)

    def test_the_label_and_the_score_are_one_update(self):
        self.ai.extract_grade_with_retry.side_effect = _a_grading_that_kept(
            ("keep_answers", (MAIN, 1))
        )
        with CaptureQueriesContext(connection) as queries:
            with self.captureOnCommitCallbacks(execute=True):
                grade_engine(self.teacher, self.submission)
        updates = [
            q["sql"]
            for q in queries.captured_queries
            if q["sql"].startswith("UPDATE") and QUOTED_TABLE in q["sql"]
        ]
        with_the_score = [sql for sql in updates if '"score" =' in sql]
        self.assertEqual(len(with_the_score), 1, updates)
        for name in LABEL_FIELDS:
            with self.subTest(name=name):
                self.assertIn(_assigned(name), with_the_score[0])
        with_a_label_column = [
            sql for sql in updates if any(_assigned(n) in sql for n in LABEL_FIELDS)
        ]
        self.assertEqual(with_a_label_column, with_the_score)

    def test_the_instance_returned_carries_the_label_too(self):
        submission = self.grade(("keep_answers", (MAIN, 1)))
        self.assertEqual(submission.grading_model, MAIN)
        self.assertEqual(
            {n: getattr(submission, n) for n in LABEL_FIELDS},
            _label_in_the_database(self.submission),
        )

    def test_a_grading_that_fails_leaves_no_label(self):
        self.ai.extract_grade_with_retry.side_effect = RuntimeError("provider down")
        with self.assertRaises(RuntimeError):
            grade_engine(self.teacher, self.submission)
        self.assertEqual(
            set(_label_in_the_database(self.submission).values()), {UNLABELLED}
        )

    def test_a_second_grading_replaces_the_label(self):
        """First form of the record: a re-grade overwrites the label with
        the newer run's, as it overwrites the score."""
        self.grade(("keep_answers", (BACKUP, 1)))
        self.assertEqual(
            _label_in_the_database(self.submission)["grading_model"], BACKUP
        )
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            grading_state="IDLE"
        )
        self.submission.refresh_from_db()
        self.grade(("keep_answers", (MAIN, 1)))
        label = _label_in_the_database(self.submission)
        self.assertEqual(label["grading_model"], MAIN)
        self.assertEqual(label["grading_fallback_used"], grading_label.FALLBACK_NO)

    def test_the_label_never_goes_into_the_feedback(self):
        """The grading result is saved whole as the feedback and is sent on
        to another AI call: the label travels beside it, never inside."""
        self.grade(("keep_answers", (MAIN, 1)))
        self.submission.refresh_from_db()
        text = str(self.submission.feedback)
        for name in LABEL_FIELDS:
            with self.subTest(name=name):
                self.assertNotIn(name, text)
        self.assertNotIn("cfg:", text)


class TheOldGradeAllJobTest(_GradingCase):
    def test_it_labels_what_it_grades(self):
        from assignments.tasks import grade_all_submissions

        self.ai.extract_grade_with_retry.side_effect = _a_grading_that_kept(
            ("keep_answers", (MAIN, 1))
        )
        with self.captureOnCommitCallbacks(execute=True):
            result = grade_all_submissions.apply(
                args=(self.teacher.id, self.assignment.id)
            )
        self.assertTrue(result.successful(), result.result)
        label = _label_in_the_database(self.submission)
        self.assertEqual(label["grading_model"], MAIN)
        self.assertNotIn(UNLABELLED, label.values())


class AManualChangeLeavesTheLabelTest(_GradingCase):
    def test_a_teachers_change_of_the_score_does_not_touch_the_label(self):
        from billing.models import CreditBucket, CreditBucketType, CreditWallet

        label = {name: f"kept-{index}" for index, name in enumerate(LABEL_FIELDS)}
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            score=8,
            ai_score=8,
            max_points=10,
            score_percentage=80,
            graded_at=timezone.now(),
            feedback=dict(GRADING),
            **label,
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        client = APIClient()
        client.force_authenticate(user=self.teacher)
        url = reverse(
            "student-submission-update-grade", kwargs={"pk": self.submission.pk}
        )
        with patch("students.views.formatted_grade_async"):
            response = client.patch(url, {"score": 6}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.submission.refresh_from_db()
        self.assertEqual(float(self.submission.score or 0), 6.0)
        self.assertEqual(_label_in_the_database(self.submission), label)


def _function(path, name):
    tree = ast.parse((Path(settings.BASE_DIR) / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path}")


class TheFormattingJobTest(SimpleTestCase):
    """It runs after a slow AI call on a row read before it. Saving the
    whole row would write an old label, or the placeholder, over one
    saved meanwhile."""

    def test_every_save_in_it_names_only_its_own_field(self):
        function = _function("assignments/tasks.py", "format_grade")
        saves = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "save"
        ]
        self.assertTrue(saves)
        for call in saves:
            fields = [k.value for k in call.keywords if k.arg == "update_fields"]
            self.assertEqual(len(fields), 1, ast.unparse(call))
            self.assertEqual(ast.literal_eval(fields[0]), ["formatted_grade"])


class TheFoundersSentenceTest(SimpleTestCase):
    def test_the_function_that_writes_the_label_says_it(self):
        docstring = " ".join(
            (student_services._populate_and_save_grade.__doc__ or "").split()
        )
        self.assertIn("first form of the grading record", docstring.lower())
        self.assertIn("a later stage improves on it", docstring.lower())
        self.assertIn("table of grading runs", docstring.lower())

    def test_the_six_names_are_among_the_fields_the_grading_save_writes(self):
        self.assertTrue(
            set(LABEL_FIELDS) <= set(student_services.GRADING_RESULT_FIELDS)
        )


class TheAuditEntryTest(_GradingCase):
    def emitted_metadata(self, *kept):
        before = history.snapshot(self.submission)
        submission = self.grade(*kept)
        with patch.object(student_services, "emit") as emit:
            student_services.emit_grading_completed(
                submission, actor=self.teacher, before=before
            )
        return emit.call_args.kwargs["metadata"]

    def test_it_carries_the_settings_version_and_the_strictness(self):
        metadata = self.emitted_metadata(("keep_answers", (MAIN, 1)))
        self.assertRegex(metadata["grading_config_version"], r"\Acfg:[0-9a-f]{12}\Z")
        self.assertEqual(metadata["strictness"], grading_label.STRICTNESS_NOT_YET_SET)
        self.assertEqual(
            metadata["prompt_version"], ai_services.GRADING_ASSIGNMENT_PROMPT.version
        )

    def test_it_carries_the_three_lists_of_models(self):
        metadata = self.emitted_metadata(
            ("keep_answers", (MAIN, 2)),
            ("keep_call", (BACKUP,)),
            ("keep_reused", (MAIN,)),
            ("keep_second_opinion", ("second/model",)),
        )
        self.assertEqual(metadata["models_served"], sorted([BACKUP, MAIN]))
        self.assertEqual(metadata["models_reused"], [MAIN])
        self.assertEqual(metadata["models_second_opinion"], ["second/model"])
        self.assertEqual(metadata["fresh_backup_used"], "yes")

    def test_its_model_is_the_labels_model(self):
        metadata = self.emitted_metadata(
            ("keep_answers", (BACKUP, 3)), ("keep_call", (MAIN,))
        )
        self.assertEqual(metadata["model"], BACKUP)

    def test_the_three_lists_are_permitted_for_this_entry_and_pass_validation(self):
        allowed = audit_metadata.METADATA_ALLOWLIST[AuditAction.GRADING_COMPLETED]
        for key in (
            "models_served",
            "models_reused",
            "models_second_opinion",
            "fresh_backup_used",
        ):
            with self.subTest(key=key):
                self.assertIn(key, allowed)
                self.assertIn(key, audit_metadata.ALLOWED_KEYS)

    def test_an_entry_is_stored_with_the_lists(self):
        from audit.models import AuditEvent

        before = history.snapshot(self.submission)
        submission = self.grade(("keep_answers", (MAIN, 1)), ("keep_reused", (BACKUP,)))
        student_services.emit_grading_completed(
            submission, actor=self.teacher, before=before
        )
        event = AuditEvent.objects.filter(
            action=AuditAction.GRADING_COMPLETED, target_id=self.submission.id
        ).latest("occurred_at")
        self.assertEqual(event.metadata["models_served"], [MAIN])
        self.assertEqual(event.metadata["models_reused"], [BACKUP])
        self.assertEqual(event.metadata["models_second_opinion"], [])
        # The fresh calls were the main model's; the backup's answer was
        # reused, so the label says "yes" and this key says "no".
        self.assertEqual(event.metadata["fresh_backup_used"], "no")
        self.assertEqual(
            _label_in_the_database(self.submission)["grading_fallback_used"],
            grading_label.FALLBACK_YES,
        )
        self.assertEqual(
            event.metadata["strictness"], grading_label.STRICTNESS_NOT_YET_SET
        )


class TheBackupMeasurementTest(SimpleTestCase):
    """The rate counts only gradings that made at least one fresh AI call,
    and it is read from ONE key of the audit entry, `fresh_backup_used`,
    which the run works out on the exact model names before anything is
    cut (SM ruling, 2026-10-06). The three lists are for a person to read
    and are not what the rate is computed from. "unknown" is counted
    apart, not in the rate."""

    def samples(self, **metadata):
        with patch.object(audit_emitter, "audit_metrics") as metrics:
            audit_emitter._emit_alertable_metrics(
                AuditAction.GRADING_COMPLETED,
                AuditOutcome.SUCCESS,
                {"metadata": metadata},
            )
        return {
            call.args[0]: call.args[1]
            for call in metrics.distribution.call_args_list
            if call.args[0] in ("model_fallback_rate", "model_unknown_rate")
        }

    def test_no_backup_among_the_fresh_calls_is_a_zero(self):
        self.assertEqual(
            self.samples(fresh_backup_used="no"),
            {"model_fallback_rate": 0.0, "model_unknown_rate": 0.0},
        )

    def test_a_backup_among_the_fresh_calls_is_a_one(self):
        self.assertEqual(
            self.samples(fresh_backup_used="yes")["model_fallback_rate"], 1.0
        )

    def test_no_fresh_call_gives_no_sample(self):
        self.assertEqual(self.samples(fresh_backup_used="no_fresh_call"), {})

    def test_unknown_is_counted_apart_and_not_in_the_rate(self):
        self.assertEqual(
            self.samples(fresh_backup_used="unknown"), {"model_unknown_rate": 1.0}
        )

    def test_the_rate_is_not_read_from_the_lists(self):
        """The lists name a backup; the key says the fresh calls had none
        (the backup's answer was reused, or it gave a second opinion)."""
        samples = self.samples(
            fresh_backup_used="no",
            models_served=[MAIN],
            models_reused=[BACKUP],
            models_second_opinion=[BACKUP],
        )
        self.assertEqual(
            samples, {"model_fallback_rate": 0.0, "model_unknown_rate": 0.0}
        )

    def test_the_old_single_model_does_not_override_the_key(self):
        samples = self.samples(fresh_backup_used="yes", model=MAIN)
        self.assertEqual(samples["model_fallback_rate"], 1.0)

    def test_lists_without_the_key_give_no_sample_from_the_lists(self):
        self.assertEqual(self.samples(models_served=[BACKUP]), {})

    def test_an_entry_from_before_the_key_is_still_measured_by_its_one_model(self):
        """Entries written by code older than this slice carry only
        `model`; the measurement keeps its old reading for those."""
        self.assertEqual(self.samples(model=BACKUP).get("model_fallback_rate"), 1.0)
        self.assertEqual(self.samples(model=MAIN).get("model_fallback_rate"), 0.0)

    def test_a_long_backup_name_is_still_a_one_in_the_rate(self):
        """Classified on the exact name, before any cut: a backup whose
        name is longer than an audit item must not fall out of the rate."""
        backup = "backup/" + "b" * 143
        with patch.object(ai_services, "GRADING_FALLBACK_MODELS", [backup]):
            run = GradingRun.start()
            run.keep_answers(backup, 1)
            samples = self.samples(fresh_backup_used=run.fresh_backup_used())
        self.assertEqual(samples.get("model_fallback_rate"), 1.0)
