"""AI-call record, slice 2: the label reaches the row on both save routes.

From the real entry points (a student's upload; a teacher's edit of the typed
text) through the real extraction and the real save to the row, with only the
provider call replaced:

  * every route that saves a reading leaves a COMPLETE label;
  * a re-read replaces the label, in the same single UPDATE as the answers
    (counted: never a second save for the label);
  * "unknown" when the provider names no model; the re-check's pair reaches
    the row as the extraction built it;
  * an extraction that fails leaves the row and its old label alone;
  * a grade, written later, does not touch the six columns.

Every provider reply has a real string model, or None on purpose (rule 14).
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from ai_processor import services
from ai_processor.exceptions import ProviderFailureError
from students.models import StudentSubmission
from students.services import update_submission_from_raw_text, upload_answers_engine
from students.tests_post_grading_submission_lock import _classroom, _submission

READING_LABEL_FIELDS = (
    "answers_model",
    "answers_prompt_version",
    "answers_config_version",
    "answers_release",
    "answers_recheck_model",
    "answers_recheck_result",
)
TEXT = {"type": "text", "text": "the paper"}
IMAGE = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,PAGE"}}
PROMPT = services.ANSWERS_EXTRACTION_PROMPT.version


def entry(html="4", status="ANSWERED"):
    return {
        "question_number": 1,
        "answer_html": html,
        "answer_status": status,
        "source_page": 1,
    }


def reply(answers, model, *, text=None):
    body = {"student_name": "", "answers": answers}
    message = SimpleNamespace(content=text if text is not None else json.dumps(body))
    fields = {
        "choices": [SimpleNamespace(message=message)],
        "usage": SimpleNamespace(total_tokens=10),
    }
    if model is not None:
        fields["model"] = model
    return SimpleNamespace(**fields)


def findings(*numbers):
    items = [
        {
            "question_number": n,
            "observed": "marks",
            "page": 1,
            "verbatim_fragment": "x",
            "content_found": True,
        }
        for n in numbers
    ]
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps({"findings": items}))
            )
        ],
        usage=SimpleNamespace(total_tokens=10),
        model="check/model",
    )


class FakeProvider:
    def __init__(self, *outs):
        self.outs = list(outs)
        self.requests = []
        self.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
        )

    def create(self, **request):
        self.requests.append(request)
        out = self.outs[min(len(self.requests), len(self.outs)) - 1]
        if isinstance(out, BaseException):
            raise out
        return out


def label_of(submission):
    submission.refresh_from_db()
    return {name: getattr(submission, name) for name in READING_LABEL_FIELDS}


def updates_of_the_row(captured):
    return [
        q["sql"]
        for q in captured.captured_queries
        if q["sql"].lstrip().upper().startswith('UPDATE "STUDENTS_STUDENTSUBMISSION"')
    ]


def inserts_of_the_row(captured):
    return [
        q["sql"]
        for q in captured.captured_queries
        if q["sql"]
        .lstrip()
        .upper()
        .startswith('INSERT INTO "STUDENTS_STUDENTSUBMISSION"')
    ]


class Base(TestCase):
    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = _classroom("rl")
        patcher = patch.object(
            services.AIProcessor, "estimate_total_token", return_value=10
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def provide(self, provider):
        patcher = patch.object(services.ai_processor, "client", provider.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        return provider


class AFirstUploadTest(Base):
    def test_a_first_upload_leaves_a_complete_label_in_the_one_insert(self):
        self.provide(FakeProvider(reply([entry()], "served/model")))
        with CaptureQueriesContext(connection) as captured:
            submission = upload_answers_engine(self.assignment, [TEXT], self.student)
        self.assertEqual(len(inserts_of_the_row(captured)), 1)
        self.assertEqual(updates_of_the_row(captured), [])
        label = label_of(submission)
        self.assertEqual(label["answers_model"], "served/model")
        self.assertEqual(label["answers_prompt_version"], PROMPT)
        self.assertRegex(label["answers_config_version"], r"^rcfg:[0-9a-f]{12}$")
        self.assertTrue(label["answers_release"])
        self.assertEqual(label["answers_recheck_result"], "not_run")
        self.assertEqual(label["answers_recheck_model"], "not_run")
        self.assertNotIn("unlabelled", label.values())

    def test_a_provider_that_names_no_model_leaves_unknown(self):
        self.provide(FakeProvider(reply([entry()], None)))
        submission = upload_answers_engine(self.assignment, [TEXT], self.student)
        self.assertEqual(label_of(submission)["answers_model"], "unknown")

    def test_the_rechecks_pair_reaches_the_row(self):
        blank = entry("", "BLANK")
        self.provide(FakeProvider(reply([blank], "read/model"), findings(1)))
        submission = upload_answers_engine(self.assignment, [TEXT, IMAGE], self.student)
        label = label_of(submission)
        self.assertEqual(label["answers_model"], "read/model")
        self.assertEqual(label["answers_recheck_result"], "found_writing")
        self.assertEqual(label["answers_recheck_model"], "check/model")

    def test_a_recheck_that_failed_reaches_the_row_as_failed(self):
        blank = entry("", "BLANK")
        self.provide(
            FakeProvider(reply([blank], "read/model"), RuntimeError("provider down"))
        )
        submission = upload_answers_engine(self.assignment, [TEXT, IMAGE], self.student)
        label = label_of(submission)
        self.assertEqual(label["answers_recheck_result"], "failed")
        self.assertEqual(label["answers_recheck_model"], "unknown")


class AReUploadTest(Base):
    def test_a_re_read_replaces_the_label_in_the_one_update(self):
        first = FakeProvider(reply([entry("4")], "first/model"))
        with patch.object(services.ai_processor, "client", first.client):
            submission = upload_answers_engine(self.assignment, [TEXT], self.student)
        before = label_of(submission)
        self.assertEqual(before["answers_model"], "first/model")

        second = self.provide(FakeProvider(reply([entry("5")], "second/model")))
        with CaptureQueriesContext(connection) as captured:
            again = upload_answers_engine(self.assignment, [TEXT], self.student)
        self.assertEqual(again.pk, submission.pk)
        updates = updates_of_the_row(captured)
        self.assertEqual(len(updates), 1, updates)
        for name in READING_LABEL_FIELDS:
            self.assertIn('"%s"' % name, updates[0])
        self.assertIn('"answers"', updates[0])
        after = label_of(again)
        self.assertEqual(after["answers_model"], "second/model")
        self.assertEqual(len(second.requests), 1)

    def test_a_row_made_before_the_columns_existed_gets_its_first_label(self):
        submission = _submission(
            self.assignment, self.student, graded=False, attempts=0
        )
        self.assertEqual(label_of(submission)["answers_model"], "unlabelled")
        self.provide(FakeProvider(reply([entry()], "served/model")))
        upload_answers_engine(self.assignment, [TEXT], self.student)
        self.assertEqual(label_of(submission)["answers_model"], "served/model")

    def test_an_extraction_that_fails_leaves_the_row_and_its_label_alone(self):
        self.provide(FakeProvider(reply([entry("4")], "first/model")))
        submission = upload_answers_engine(self.assignment, [TEXT], self.student)
        before = label_of(submission)
        answers = StudentSubmission.objects.get(pk=submission.pk).answers

        self.provide(FakeProvider(RuntimeError("provider down")))
        with self.assertRaises(ProviderFailureError):
            upload_answers_engine(self.assignment, [TEXT], self.student)
        self.assertEqual(label_of(submission), before)
        self.assertEqual(
            StudentSubmission.objects.get(pk=submission.pk).answers, answers
        )


class ATeachersEditTest(Base):
    def test_an_edit_is_a_new_reading_and_replaces_the_label_in_one_update(self):
        submission = _submission(self.assignment, self.student, graded=False)
        StudentSubmission.objects.filter(pk=submission.pk).update(
            answers_model="first/model",
            answers_recheck_model="not_run",
            answers_recheck_result="not_run",
        )
        self.assertEqual(label_of(submission)["answers_model"], "first/model")

        self.provide(FakeProvider(reply([entry("six")], "edit/model")))
        with CaptureQueriesContext(connection) as captured:
            update_submission_from_raw_text(self.teacher, submission, "six")
        updates = updates_of_the_row(captured)
        self.assertEqual(len(updates), 1, updates)
        for name in READING_LABEL_FIELDS:
            self.assertIn('"%s"' % name, updates[0])
        label = label_of(submission)
        self.assertEqual(label["answers_model"], "edit/model")
        self.assertEqual(label["answers_recheck_result"], "not_run")
        self.assertEqual(label["answers_recheck_model"], "not_run")

    def test_an_edit_whose_reading_failed_keeps_the_old_label(self):
        submission = _submission(self.assignment, self.student, graded=False)
        before = label_of(submission)
        self.provide(FakeProvider(RuntimeError("provider down")))
        with self.assertRaises(ProviderFailureError):
            update_submission_from_raw_text(self.teacher, submission, "six")
        self.assertEqual(label_of(submission), before)


class AGradeDoesNotTouchTheReadingTest(Base):
    def test_the_grading_update_does_not_list_the_six_columns(self):
        from students.services import GRADING_RESULT_FIELDS

        self.assertEqual(set(GRADING_RESULT_FIELDS) & set(READING_LABEL_FIELDS), set())
