"""H-158: an AI reply's question numbers are made 1..N before anything is saved.

An assignment's questions were saved with the `question_number` the model
wrote, and nothing made the numbers distinct. Grading pairs a question
with the student's answer BY that number, so two questions numbered alike
were paired with one answer. Both chunked extraction paths already
re-index 1..N; the single-call extraction and the generation did not.

The rule (Senior Manager, 2026-10-07):

- every AI path numbers the questions 1..N, in the order the model gave
  them, before the document is built and before the serializer: in
  `extract_assignment_data`, in the generated draft, and at the save of a
  draft stored before this row;
- on the extraction paths the model's own numbers are kept, as text of
  bounded length, in `ai_raw_payload` under one key of ours (new number
  -> the model's number). `ai_raw_payload` is sent to no client. On
  generation there is no paper whose numbering they would record, and a
  draft's snapshot IS sent to the client: nothing new is stored there;
- when a number changed, one log line: ids and counts, no question text;
- `AssignmentSerializer` refuses a repeated number, as a last defence.
"""

import copy
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import status

from ai_processor import services as ai_services
from ai_processor.services import AIProcessor, ai_processor
from assignments import services
from assignments.models import Assignment, AssignmentGenerationMessage, AssignmentStatus
from assignments.serializers import AssignmentSerializer
from assignments.services import AssignmentProcessingService
from assignments.tests import generated_assignment_payload
from assignments.tests_ai_output_allowlist import (
    EXTRACT,
    GENERATE,
    PNG_1X1,
    AIOutputAllowListFixture,
)
from assignments.tests_extraction_service import extraction_payload
from classrooms.models import Course, Session
from users.models import CustomUser, UserTypes

#: Names this row adds, looked up when a test runs: before the change each
#: test that needs one fails by itself, and the module still loads.
NUMBERER = "number_questions_in_order"
#: Our key inside `ai_raw_payload`, written here as the contract.
MODEL_NUMBERS = "model_question_numbers"
#: The longest text kept of one of the model's own numbers.
KEPT_LENGTH = 32
#: The last defence's sentence, for a repeated 1.
REPEATED_ONE = (
    "Two questions have the same number (1). Each question needs its own number."
)
#: Question texts. The serializer stores them as they are (seen by calling
#: QuestionSerializer on such a question, 2026-10-07).
FIRST = "<p>First of three.</p>"
SECOND = "<p>Second of three.</p>"
THIRD = "<p>Third of three.</p>"
IN_ORDER = [(1, FIRST), (2, SECOND), (3, THIRD)]
#: The model's numbers in most tests: a repeat. And what is kept of them.
REPEAT = [1, 1, 2]
KEPT_OF_REPEAT = {"1": "1", "2": "1", "3": "2"}
#: For a question that carries no `question_number` key at all.
ABSENT = object()


def question(number, text):
    """One question as a model writes it, numbered as given."""
    entry = dict(extraction_payload()["questions"][0], question_text=text)
    if number is not ABSENT:
        entry["question_number"] = number
    else:
        del entry["question_number"]
    return entry


def three(numbers):
    return [
        question(number, text)
        for number, text in zip(numbers, (FIRST, SECOND, THIRD), strict=True)
    ]


def extraction_reply(numbers=REPEAT, **overrides):
    return extraction_payload(
        questions=three(numbers), total_points=30, question_count=3, **overrides
    )


def generation_reply(numbers=REPEAT):
    return dict(
        generated_assignment_payload(),
        questions=three(numbers),
        total_points=30,
        question_count=3,
    )


def numbers_and_texts(questions):
    return [(entry["question_number"], entry["question_text"]) for entry in questions]


def headings(document, heading):
    """How often the document prints a question's heading; the document
    is the JSON text the converter makes, a string."""
    assert isinstance(document, str), type(document)
    return document.count(heading)


def number(questions):
    return getattr(services, NUMBERER)(questions)


class TheNumbererTest(SimpleTestCase):
    def test_numbers_already_in_order_are_left_and_nothing_is_counted(self):
        numbered, kept, changed = number(three([1, 2, 3]))

        self.assertEqual(numbers_and_texts(numbered), IN_ORDER)
        self.assertEqual(kept, {"1": "1", "2": "2", "3": "3"})
        self.assertEqual(changed, 0)

    def test_a_repeat_is_numbered_in_the_models_order(self):
        numbered, kept, changed = number(three(REPEAT))

        self.assertEqual(numbers_and_texts(numbered), IN_ORDER)
        self.assertEqual(kept, KEPT_OF_REPEAT)
        self.assertEqual(changed, 2)

    def test_a_paper_that_starts_at_five_starts_at_one(self):
        numbered, kept, changed = number(three([5, 6, 8]))

        self.assertEqual(numbers_and_texts(numbered), IN_ORDER)
        self.assertEqual(kept, {"1": "5", "2": "6", "3": "8"})
        self.assertEqual(changed, 3)

    def test_a_number_that_is_not_a_positive_integer_is_replaced_like_any_other(
        self,
    ):
        for name, odd, kept_as in (
            ("a string of digits", "2", "2"),
            ("a label", "2a", "2a"),
            ("zero", 0, "0"),
            ("a negative", -2, "-2"),
            ("true", True, "True"),
            ("a float", 2.0, "2.0"),
            ("null", None, None),
            ("no number at all", ABSENT, None),
        ):
            with self.subTest(number=name):
                numbered, kept, changed = number(three([1, odd, 3]))

                self.assertEqual(numbers_and_texts(numbered), IN_ORDER)
                self.assertIs(type(numbered[1]["question_number"]), int)
                self.assertEqual(kept, {"1": "1", "2": kept_as, "3": "3"})
                self.assertEqual(changed, 1)

    def test_a_long_number_from_the_model_is_kept_only_in_part(self):
        long_label = "9" * 100
        self.assertGreater(len(long_label), KEPT_LENGTH)

        _, kept, _ = number(three([1, long_label, 3]))

        self.assertEqual(kept["2"], long_label[:KEPT_LENGTH])
        self.assertEqual(len(kept["2"]), KEPT_LENGTH)

    def test_an_entry_that_is_not_an_object_is_left_where_it_is(self):
        entries = [question(4, FIRST), "not a question", question(4, SECOND)]

        numbered, kept, changed = number(entries)

        self.assertEqual(numbered[1], "not a question")
        self.assertEqual(
            numbers_and_texts([numbered[0], numbered[2]]), [(1, FIRST), (2, SECOND)]
        )
        self.assertEqual(kept, {"1": "4", "2": "4"})
        self.assertEqual(changed, 2)

    def test_the_input_is_not_changed(self):
        entries = three(REPEAT)
        before = copy.deepcopy(entries)

        numbered, _, _ = number(entries)

        self.assertEqual(entries, before)
        self.assertNotEqual(numbers_and_texts(numbered), numbers_and_texts(entries))
        for was, now in zip(entries, numbered, strict=True):
            self.assertIsNot(was, now)

    def test_a_value_that_is_not_a_list_is_returned_as_it_is(self):
        for name, value in (
            ("null", None),
            ("an object", {"question_number": 1}),
            ("a string", "1"),
        ):
            with self.subTest(questions=name):
                self.assertEqual(number(value), (value, {}, 0))


class ExtractionNumbersInOrderTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="numbers-in-order@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Numbers",
            last_name="Teacher",
        )
        session = Session.objects.create(name="Numbers Session", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Numbers Course", teacher=self.teacher, session=session
        )
        self.content = [{"type": "text", "text": "extract this"}]

    def extract(self, reply, **kwargs):
        with patch(EXTRACT, return_value=reply):
            return AssignmentProcessingService.extract_assignment_data(
                self.teacher, self.content, course=self.course, **kwargs
            )

    def test_the_questions_and_the_stored_ai_copy_are_in_order(self):
        data = self.extract(extraction_reply())

        self.assertEqual(numbers_and_texts(data["questions"]), IN_ORDER)
        self.assertEqual(
            numbers_and_texts(data["ai_raw_payload"]["questions"]), IN_ORDER
        )

    def test_the_models_own_numbers_are_kept_in_the_stored_ai_copy(self):
        data = self.extract(extraction_reply())

        self.assertEqual(data["ai_raw_payload"][MODEL_NUMBERS], KEPT_OF_REPEAT)
        self.assertNotIn(MODEL_NUMBERS, data)
        for entry in data["questions"]:
            self.assertNotIn(MODEL_NUMBERS, entry)

    def test_they_are_kept_also_when_nothing_changed(self):
        data = self.extract(extraction_reply([1, 2, 3]))

        self.assertEqual(numbers_and_texts(data["questions"]), IN_ORDER)
        self.assertEqual(
            data["ai_raw_payload"][MODEL_NUMBERS], {"1": "1", "2": "2", "3": "3"}
        )

    def test_the_document_is_built_from_the_numbers_in_order(self):
        data = self.extract(extraction_reply(), generate_raw_input=True)

        # The headings as the builder prints them (seen by calling the
        # builder and the converter on such questions, 2026-10-07).
        document = data["raw_input"]
        self.assertIsInstance(document, str)
        for heading in ("Question 1 (10 marks)", "Question 2 (", "Question 3 ("):
            self.assertEqual(headings(document, heading), 1, heading)

    def test_one_log_line_with_ids_and_counts_and_no_question_text(self):
        with self.assertLogs("assignments.services", level="WARNING") as logs:
            self.extract(extraction_reply())
        logged = "\n".join(logs.output)

        self.assertIn("Question numbers put in order", logged)
        self.assertIn(f"user={self.teacher.id}", logged)
        self.assertIn("questions=3", logged)
        self.assertIn("changed=2", logged)
        self.assertNotIn("of three", logged)

    def test_no_such_log_line_when_nothing_changed(self):
        with self.assertLogs("assignments.services", level="INFO") as logs:
            self.extract(extraction_reply([1, 2, 3]))
        logged = "\n".join(logs.output)

        # The service's own first line, so that there is something to read.
        self.assertIn("Extracting assignment content", logged)
        self.assertNotIn("Question numbers put in order", logged)

    def test_grading_then_pairs_each_question_with_its_own_answer(self):
        answers = [
            {"question_number": n, "answer_html": f"answer {n}"} for n in (1, 2, 3)
        ]

        def paired(questions):
            return [
                (pair["question"]["question_text"], pair["answer"]["answer_html"])
                for pair in ai_processor._pair_question_with_answers(questions, answers)
            ]

        # Control, the fault itself: with the model's numbers two questions
        # are paired with one answer.
        self.assertEqual(
            paired(three(REPEAT)),
            [(FIRST, "answer 1"), (SECOND, "answer 1"), (THIRD, "answer 2")],
        )

        data = self.extract(extraction_reply())

        self.assertEqual(
            paired(data["questions"]),
            [(FIRST, "answer 1"), (SECOND, "answer 2"), (THIRD, "answer 3")],
        )

    @override_settings(
        GRADING_SECOND_OPINION_ENABLED=False,
        GRADING_EVIDENCE_ENFORCEMENT=ai_services.MODE_LOG,
    )
    def test_the_real_grading_pipeline_grades_three_questions_not_two(self):
        """The real pipeline. These are objective questions with an answer
        key ("4"), which the system grades itself: no provider call is
        made, and one would fail the test."""
        cache.clear()
        self.addCleanup(cache.clear)
        answers = [
            {"question_number": n, "answer_html": f"<p>{given}</p>"}
            for n, given in ((1, "4"), (2, "3"), (3, "4"))
        ]

        def graded(questions):
            with patch.object(AIProcessor, "execute_graded_task") as provider:
                provider.side_effect = AssertionError("no provider call expected")
                result = ai_processor.extract_grade_with_retry(
                    MagicMock(), questions, answers, assignment_model=None
                )
            return (
                [
                    (evaluation["question_number"], evaluation["score_awarded"])
                    for evaluation in result["question_evaluations"]
                ],
                result["grading_summary"]["max_total_points"],
            )

        # Control, the fault itself (seen by calling the pipeline so,
        # 2026-10-07): with the model's numbers the paper of three is
        # graded as a paper of two, out of 20.
        self.assertEqual(graded(three(REPEAT)), ([(1, 10), (2, 0)], 20))

        data = self.extract(extraction_reply())

        self.assertEqual(graded(data["questions"]), ([(1, 10), (2, 0), (3, 10)], 30))


class TheExtractionRoutesSaveNumbersInOrderTest(AIOutputAllowListFixture):
    def assert_saved_in_order(self, assignment):
        assignment.refresh_from_db()
        self.assertEqual(numbers_and_texts(assignment.questions), IN_ORDER)
        self.assertEqual(assignment.ai_raw_payload[MODEL_NUMBERS], KEPT_OF_REPEAT)

    @patch(EXTRACT)
    def test_create_by_text(self, mock_ai):
        mock_ai.return_value = extraction_reply()

        response = self.client.post(
            reverse("assignment-list"),
            {
                "course": str(self.course_b.id),
                "raw_input": "1. First 1. Second 2. Third",
                "title": "Numbered By Text",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assert_saved_in_order(
            Assignment.objects.get(course=self.course_b, title="Numbered By Text")
        )

    @patch(EXTRACT)
    def test_edit_by_text(self, mock_ai):
        mock_ai.return_value = extraction_reply()
        self.assignment_b.status = AssignmentStatus.DRAFT
        self.assignment_b.save(update_fields=["status"])

        response = self.client.patch(
            self.detail_url(self.assignment_b),
            {"raw_input": "1. First 1. Second 2. Third"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assert_saved_in_order(self.assignment_b)

    @patch(EXTRACT)
    def test_upload(self, mock_ai):
        mock_ai.return_value = extraction_reply(title="Numbered By Upload")

        response = self.client.post(
            reverse("assignment-upload"),
            {
                "course": str(self.course_b.id),
                "assignments": SimpleUploadedFile(
                    "q.png", PNG_1X1, content_type="image/png"
                ),
            },
            format="multipart",
        )

        self.assertIn(
            response.status_code,
            (status.HTTP_200_OK, status.HTTP_201_CREATED, status.HTTP_202_ACCEPTED),
            response.content[:300],
        )
        assignment = Assignment.objects.get(
            course=self.course_b, title="Numbered By Upload"
        )
        self.assert_saved_in_order(assignment)
        self.assertEqual(headings(assignment.raw_input, "Question 3 ("), 1)
        self.assertEqual(headings(assignment.raw_input, "Question 1 (10 marks)"), 1)

    @patch(EXTRACT)
    def test_a_label_the_serializer_would_refuse_no_longer_costs_the_save(
        self, mock_ai
    ):
        """`2a` is not an integer: until this row the save was refused
        after the paid call."""
        mock_ai.return_value = extraction_reply([1, "2a", 3])
        self.assignment_b.status = AssignmentStatus.DRAFT
        self.assignment_b.save(update_fields=["status"])

        response = self.client.patch(
            self.detail_url(self.assignment_b),
            {"raw_input": "1. First 2a. Second 3. Third"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assignment_b.refresh_from_db()
        self.assertEqual(numbers_and_texts(self.assignment_b.questions), IN_ORDER)
        self.assertEqual(
            self.assignment_b.ai_raw_payload[MODEL_NUMBERS],
            {"1": "1", "2": "2a", "3": "3"},
        )


@patch(GENERATE)
class GenerationNumbersInOrderTest(AIOutputAllowListFixture):
    def generate(self):
        response = self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course_b.id}),
            {"prompt": "Create a three-question biology quiz."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return AssignmentGenerationMessage.objects.get(id=response.data["message_id"])

    def save_draft(self, message):
        response = self.client.post(
            reverse(
                "assignment-save-generated-draft", kwargs={"message_id": message.id}
            ),
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return Assignment.objects.get(id=response.data["id"])

    def test_the_draft_is_in_order_and_so_is_its_document(self, mock_generate):
        mock_generate.return_value = generation_reply()

        with self.assertLogs("assignments.views", level="WARNING") as logs:
            message = self.generate()
        logged = "\n".join(logs.output)

        snapshot = message.assignment_snapshot
        self.assertEqual(numbers_and_texts(snapshot["questions"]), IN_ORDER)
        self.assertEqual(headings(snapshot["raw_input"], "Question 3 ("), 1)
        self.assertEqual(headings(snapshot["raw_input"], "Question 1 (10 marks)"), 1)
        self.assertIn("Question numbers put in order", logged)
        self.assertIn(f"user={self.teacher_b.id}", logged)
        self.assertIn("changed=2", logged)
        self.assertNotIn("of three", logged)

    def test_nothing_of_the_models_numbers_rides_in_the_draft(self, mock_generate):
        """A draft's snapshot is sent to the client."""
        mock_generate.return_value = generation_reply()

        message = self.generate()

        snapshot = message.assignment_snapshot
        self.assertIn("questions", snapshot)
        self.assertNotIn(MODEL_NUMBERS, snapshot)
        self.assertNotIn("ai_raw_payload", snapshot)
        for entry in snapshot["questions"]:
            self.assertNotIn(MODEL_NUMBERS, entry)

    def test_the_saved_assignment_is_in_order(self, mock_generate):
        mock_generate.return_value = generation_reply()

        assignment = self.save_draft(self.generate())

        self.assertEqual(numbers_and_texts(assignment.questions), IN_ORDER)
        self.assertIsNone(assignment.ai_raw_payload)

    def test_a_draft_stored_before_this_row_is_put_in_order_at_its_save(
        self, mock_generate
    ):
        mock_generate.return_value = generation_reply([1, 2, 3])
        message = self.generate()
        # What the generate path stored before this row for such a reply.
        message.assignment_snapshot = {
            **message.assignment_snapshot,
            "questions": three(REPEAT),
        }
        message.save(update_fields=["assignment_snapshot"])
        message.refresh_from_db()
        self.assertEqual(
            [
                entry["question_number"]
                for entry in message.assignment_snapshot["questions"]
            ],
            REPEAT,
        )

        with self.assertLogs("assignments.views", level="WARNING") as logs:
            assignment = self.save_draft(message)
        logged = "\n".join(logs.output)

        self.assertEqual(numbers_and_texts(assignment.questions), IN_ORDER)
        self.assertEqual(headings(assignment.raw_input, "Question 3 ("), 1)
        message.refresh_from_db()
        self.assertEqual(
            numbers_and_texts(message.assignment_snapshot["questions"]), IN_ORDER
        )
        self.assertIn("Question numbers put in order", logged)
        self.assertIn("changed=2", logged)
        self.assertNotIn("of three", logged)


class TheSerializersLastDefenceTest(AIOutputAllowListFixture):
    def serializer(self, numbers):
        return AssignmentSerializer(
            data={**extraction_reply(numbers), "course": str(self.course_b.id)}
        )

    def test_a_repeated_number_is_refused_with_the_sentence(self):
        serializer = self.serializer(REPEAT)

        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            [str(error) for error in serializer.errors["questions"]], [REPEATED_ONE]
        )

    def test_a_number_and_the_same_number_as_text_are_one_number(self):
        serializer = self.serializer([1, "1", 2])

        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            [str(error) for error in serializer.errors["questions"]], [REPEATED_ONE]
        )

    def test_numbers_in_order_pass(self):
        """Control."""
        serializer = self.serializer([1, 2, 3])

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_an_update_that_sends_no_questions_passes(self):
        """Control: the defence reads only questions that were sent."""
        serializer = AssignmentSerializer(
            self.assignment_b, data={"title": "Only The Title"}, partial=True
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)


class OurKeyDoesNotLookLikeATeachersEditTest(AIOutputAllowListFixture):
    """`AssignmentSerializer.update` compares the stored AI copy with the
    teacher's version to mark an assignment as overridden. No path of
    today's code stores a row on which that comparison runs (it needs
    `ai_generated` and a stored copy together; that is H-172). This holds
    that our key would not change its answer on such a row."""

    def setUp(self):
        super().setUp()
        created = self.serializer_for(extraction_reply([1, 2, 3]))
        self.assertTrue(created.is_valid(), created.errors)
        self.assignment = created.save()
        Assignment.objects.filter(pk=self.assignment.pk).update(
            ai_generated=True,
            ai_raw_payload={
                "title": self.assignment.title,
                "instructions": self.assignment.instructions,
                "questions": self.assignment.questions,
                MODEL_NUMBERS: {"1": "5", "2": "6", "3": "7"},
            },
        )
        self.assignment.refresh_from_db()

    def serializer_for(self, reply):
        return AssignmentSerializer(data={**reply, "course": str(self.course_b.id)})

    def update(self, data):
        serializer = AssignmentSerializer(self.assignment, data=data, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.save()

    def test_an_update_that_changes_nothing_is_not_an_override(self):
        self.assertTrue(self.assignment.ai_raw_payload[MODEL_NUMBERS])

        updated = self.update({"extraction_confidence": 91})

        self.assertFalse(updated.was_overridden)

    def test_the_comparison_does_run_on_this_row(self):
        """Control: a changed title IS an override here, so the test above
        has something to decide."""
        updated = self.update({"title": "A Title The Teacher Chose"})

        self.assertTrue(updated.was_overridden)
