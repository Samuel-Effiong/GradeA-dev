"""AssignmentProcessingService's extraction entry points.

These three methods - extract_assignment_data, update_assignment_from_extraction
and extract_assignment - had NO coverage at all (services.py lines 771-875),
which matters more than the percentage suggests: they are the seam between this
app and the billed AI extraction call, and they decide what gets written back
onto a teacher's assignment afterwards.

The AI call itself is stubbed here so the logic around it can be asserted
exactly. It is separately exercised against the real provider in
tests_real_extraction.py, which is opt-in because it costs money.

What is pinned here, in order of how expensive the bug would be:

  * `keep_existing_title` really keeps the teacher's title. Getting this
    backwards silently renames every assignment a teacher re-extracts.
  * The extraction timestamps bracket the call, so "how long did extraction
    take" is answerable from the row.
  * `ai_generated` is forced False and `ai_raw_payload` captures what the
    model actually returned - the audit trail for a disputed grade.
  * Course/topic resolution falls back to the existing assignment's, so a
    re-extraction cannot silently move an assignment to another course.
  * A cancelled task is refused BEFORE the billed call, not after.
"""

import uuid
from unittest.mock import ANY, patch

from django.test import TestCase

from assignments.models import Assignment, AssignmentStatus
from assignments.services import AssignmentProcessingService
from classrooms.models import Course, Session, Topic
from students.exceptions import TaskCancelledError
from users.models import CustomUser, UserTypes


def extraction_payload(**overrides):
    """The shape ai_processor.extract_assignment_with_retry returns."""
    payload = {
        "title": "Extracted Title",
        "instructions": "<p>Answer all questions.</p>",
        "total_points": 10,
        "question_count": 1,
        "assignment_type": "OBJECTIVE",
        "questions": [
            {
                "question_number": 1,
                "question_text": "<p>What is 2 + 2?</p>",
                "question_type": "OBJECTIVE",
                "question_image": "",
                "points": 10,
                "blooms_level": "Remember",
                "options": ["3", "4", "5", "6"],
                "rubric": [],
                "model_answer": "4",
            }
        ],
        "potential_issues": [],
        "self_assessment": "<p>Straightforward.</p>",
        "extraction_confidence": 90,
    }
    payload.update(overrides)
    return payload


class ExtractAssignmentDataTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="extract-service@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Extract",
            last_name="Teacher",
        )
        self.session = Session.objects.create(
            name="Extract Session", teacher=self.teacher
        )
        self.course = Course.objects.create(
            name="Extract Course", teacher=self.teacher, session=self.session
        )
        self.topic = Topic.objects.create(name="Extract Topic", course=self.course)
        self.content = [{"type": "text", "text": "extract this"}]

    def _extract(self, mock_ai, **kwargs):
        mock_ai.return_value = extraction_payload()
        return AssignmentProcessingService.extract_assignment_data(
            self.teacher, self.content, **kwargs
        )

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_the_models_output_is_returned_with_provenance_fields_added(self, mock_ai):
        data = self._extract(mock_ai, course=self.course)

        self.assertEqual(data["title"], "Extracted Title")
        self.assertEqual(len(data["questions"]), 1)
        self.assertEqual(data["questions"][0]["model_answer"], "4")
        # Provenance: this came from extraction, not from the generator.
        self.assertIs(data["ai_generated"], False)
        self.assertEqual(data["ai_raw_payload"]["title"], "Extracted Title")
        self.assertEqual(
            data["ai_raw_payload"]["instructions"], "<p>Answer all questions.</p>"
        )
        self.assertEqual(len(data["ai_raw_payload"]["questions"]), 1)

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_the_timestamps_actually_bracket_the_billed_call(self, mock_ai):
        seen = {}

        def record_when_called(*args, **kwargs):
            from django.utils import timezone

            seen["during"] = timezone.now()
            return extraction_payload()

        mock_ai.side_effect = record_when_called

        data = AssignmentProcessingService.extract_assignment_data(
            self.teacher, self.content, course=self.course
        )

        self.assertLessEqual(data["extraction_started_at"], seen["during"])
        self.assertGreaterEqual(data["extraction_completed_at"], seen["during"])

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_keep_existing_title_preserves_the_teachers_own_title(self, mock_ai):
        """
        The expensive-to-get-wrong one: a teacher who renamed their
        assignment and then edits it must not have the model's guess
        overwrite that name.
        """
        assignment = Assignment.objects.create(
            title="The Teacher's Chosen Name",
            course=self.course,
            status=AssignmentStatus.DRAFT,
        )

        data = self._extract(mock_ai, assignment=assignment, keep_existing_title=True)

        self.assertEqual(data["title"], "The Teacher's Chosen Name")

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_without_the_flag_the_extracted_title_wins(self, mock_ai):
        assignment = Assignment.objects.create(
            title="The Teacher's Chosen Name",
            course=self.course,
            status=AssignmentStatus.DRAFT,
        )

        data = self._extract(mock_ai, assignment=assignment, keep_existing_title=False)

        self.assertEqual(data["title"], "Extracted Title")

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_keep_existing_title_does_not_apply_to_an_untitled_assignment(
        self, mock_ai
    ):
        """`keep_existing_title` keeps a title, not an empty string."""
        assignment = Assignment.objects.create(
            title="", course=self.course, status=AssignmentStatus.DRAFT
        )

        data = self._extract(mock_ai, assignment=assignment, keep_existing_title=True)

        self.assertEqual(data["title"], "Extracted Title")

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_course_falls_back_to_the_assignments_own_course(self, mock_ai):
        """
        A re-extraction passes no course. If the fallback broke, the
        serializer would receive no course and the assignment could be
        detached from - or moved out of - the teacher's course.
        """
        assignment = Assignment.objects.create(
            title="Existing", course=self.course, status=AssignmentStatus.DRAFT
        )

        data = self._extract(mock_ai, assignment=assignment)

        self.assertEqual(data["course"], self.course.id)

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_an_explicit_course_overrides_the_assignments_course(self, mock_ai):
        other = Course.objects.create(
            name="Other Course", teacher=self.teacher, session=self.session
        )
        assignment = Assignment.objects.create(
            title="Existing", course=self.course, status=AssignmentStatus.DRAFT
        )

        data = self._extract(mock_ai, assignment=assignment, course=other)

        self.assertEqual(data["course"], other.id)

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_topic_is_carried_over_and_can_be_cleared_deliberately(self, mock_ai):
        assignment = Assignment.objects.create(
            title="Existing",
            course=self.course,
            topic=self.topic,
            status=AssignmentStatus.DRAFT,
        )

        carried = self._extract(mock_ai, assignment=assignment)
        self.assertEqual(carried["topic"], self.topic.id)

        # topic=None means "no change requested", so the existing topic is
        # still carried; only an explicit different topic replaces it.
        again = self._extract(mock_ai, assignment=assignment, topic=None)
        self.assertEqual(again["topic"], self.topic.id)

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_raw_input_is_attached_verbatim_when_supplied(self, mock_ai):
        data = self._extract(mock_ai, course=self.course, raw_input="<p>original</p>")

        self.assertEqual(data["raw_input"], "<p>original</p>")

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_generate_raw_input_produces_parseable_prosemirror_json(self, mock_ai):
        """
        Not just "a string came back": the value must be JSON a frontend
        can load, because it is stored in a TextField and read back by the
        editor. A dict assigned here would be str()-ed into a Python repr
        no JSON parser can read.
        """
        import json

        data = self._extract(mock_ai, course=self.course, generate_raw_input=True)

        document = json.loads(data["raw_input"])
        self.assertEqual(document["type"], "doc")
        self.assertIn("content", document)

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_a_cancelled_task_is_refused_before_the_billed_call(self, mock_ai):
        """
        The check has to come first. Refusing after the call would still
        have spent the credits.
        """
        with patch(
            "assignments.services.ensure_task_not_cancelled",
            side_effect=TaskCancelledError("cancelled"),
        ):
            with self.assertRaises(TaskCancelledError):
                AssignmentProcessingService.extract_assignment_data(
                    self.teacher, self.content, course=self.course
                )

        mock_ai.assert_not_called()

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_the_processing_task_id_is_forwarded_so_cancellation_reaches_the_call(
        self, mock_ai
    ):
        mock_ai.return_value = extraction_payload()
        task_id = str(uuid.uuid4())

        AssignmentProcessingService.extract_assignment_data(
            self.teacher,
            self.content,
            course=self.course,
            processing_task_id=task_id,
            upload=True,
        )

        self.assertEqual(mock_ai.call_args.kwargs["processing_task_id"], task_id)
        self.assertIs(mock_ai.call_args.kwargs["upload"], True)
        self.assertEqual(mock_ai.call_args.kwargs["max_retries"], 3)


class UpdateAssignmentFromExtractionTest(TestCase):
    """
    The write-back half: what actually lands on the row.

    Per project memory the Tiptap editor re-sends the whole document on any
    edit, so this path is always a FULL re-extraction - there is no partial
    or incremental update to support. The last test here pins that
    invariant, so a future "only update changed questions" optimisation
    fails loudly rather than silently half-writing an assignment.
    """

    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="update-extract@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Update",
            last_name="Teacher",
        )
        self.session = Session.objects.create(
            name="Update Session", teacher=self.teacher
        )
        self.course = Course.objects.create(
            name="Update Course", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="Before",
            course=self.course,
            status=AssignmentStatus.DRAFT,
            total_points=5,
            questions=[
                {
                    "question_number": 1,
                    "question_text": "<p>Old question</p>",
                    "question_type": "OBJECTIVE",
                    "question_image": "",
                    "points": 5,
                    "blooms_level": "Remember",
                    "options": ["a", "b"],
                    "rubric": [],
                    "model_answer": "a",
                }
            ],
        )
        self.content = [{"type": "text", "text": "re-extract this"}]

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_the_assignment_row_is_updated_with_the_extracted_values(self, mock_ai):
        mock_ai.return_value = extraction_payload()

        AssignmentProcessingService.update_assignment_from_extraction(
            self.teacher, self.assignment, self.content
        )

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.title, "Extracted Title")
        self.assertEqual(self.assignment.total_points, 10)
        self.assertEqual(len(self.assignment.questions), 1)
        self.assertEqual(
            self.assignment.questions[0]["question_text"], "<p>What is 2 + 2?</p>"
        )
        self.assertIs(self.assignment.ai_generated, False)

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_a_full_replacement_removes_questions_that_are_gone(self, mock_ai):
        """
        THE TIPTAP INVARIANT. The editor re-sends everything, so extraction
        output REPLACES the question list rather than merging into it. If a
        future change made this a partial update, a question the teacher
        deleted would survive - and would still be graded.
        """
        self.assignment.questions = [
            {
                "question_number": 1,
                "question_text": "<p>Keep me</p>",
                "question_type": "OBJECTIVE",
                "question_image": "",
                "points": 5,
                "blooms_level": "Remember",
                "options": ["a", "b"],
                "rubric": [],
                "model_answer": "a",
            },
            {
                "question_number": 2,
                "question_text": "<p>DELETE ME</p>",
                "question_type": "OBJECTIVE",
                "question_image": "",
                "points": 5,
                "blooms_level": "Remember",
                "options": ["c", "d"],
                "rubric": [],
                "model_answer": "c",
            },
        ]
        self.assignment.save()

        # The re-extraction returns only the surviving question.
        mock_ai.return_value = extraction_payload()

        AssignmentProcessingService.update_assignment_from_extraction(
            self.teacher, self.assignment, self.content
        )

        self.assignment.refresh_from_db()
        self.assertEqual(len(self.assignment.questions), 1)
        rendered = str(self.assignment.questions)
        self.assertNotIn("DELETE ME", rendered)

    @patch("assignments.services.notify_students_of_assignment_edit", create=True)
    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_students_are_not_notified_when_there_are_no_submissions(
        self, mock_ai, _mock_notify
    ):
        mock_ai.return_value = extraction_payload()

        with patch(
            "students.services.notify_students_of_assignment_edit"
        ) as mock_notify:
            AssignmentProcessingService.update_assignment_from_extraction(
                self.teacher, self.assignment, self.content
            )

        mock_notify.assert_not_called()

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_students_with_submissions_are_notified_that_it_changed(self, mock_ai):
        """
        A student who already answered must be told the paper moved under
        them. Skipping this is a silent correctness problem, not a UX one.
        """
        from students.models import StudentSubmission

        student = CustomUser.objects.create_user(
            email="update-extract-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Sub",
            last_name="Student",
        )
        StudentSubmission.objects.create(
            assignment=self.assignment, student=student, answers={"q1": "a"}
        )
        mock_ai.return_value = extraction_payload()

        with patch(
            "students.services.notify_students_of_assignment_edit"
        ) as mock_notify:
            AssignmentProcessingService.update_assignment_from_extraction(
                self.teacher, self.assignment, self.content
            )

        mock_notify.assert_called_once_with(self.assignment)

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_invalid_extraction_output_raises_and_leaves_the_row_untouched(
        self, mock_ai
    ):
        """
        The model can return something the serializer rejects. When it
        does, the assignment must be left exactly as it was rather than
        half-written.
        """
        from rest_framework.exceptions import ValidationError

        mock_ai.return_value = extraction_payload(
            questions=[{"question_number": 1, "question_text": "<p>no type</p>"}]
        )

        with self.assertRaises(ValidationError):
            AssignmentProcessingService.update_assignment_from_extraction(
                self.teacher, self.assignment, self.content
            )

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.title, "Before")
        self.assertEqual(
            self.assignment.questions[0]["question_text"], "<p>Old question</p>"
        )

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_the_final_save_is_locked_against_a_racing_cancellation(self, mock_ai):
        mock_ai.return_value = extraction_payload()
        task_id = str(uuid.uuid4())

        with patch(
            "assignments.services.lock_processing_task_for_final_save"
        ) as mock_lock:
            AssignmentProcessingService.update_assignment_from_extraction(
                self.teacher,
                self.assignment,
                self.content,
                processing_task_id=task_id,
            )

        mock_lock.assert_called_once_with(task_id)

    @patch("assignments.services.ai_processor.extract_assignment_with_retry")
    def test_extract_assignment_keeps_the_title_and_returns_a_serializer(self, mock_ai):
        mock_ai.return_value = extraction_payload()

        result = AssignmentProcessingService.extract_assignment(
            self.teacher, self.assignment, self.content
        )

        self.assignment.refresh_from_db()
        # extract_assignment() is the keep_existing_title=True entry point.
        self.assertEqual(self.assignment.title, "Before")
        self.assertEqual(result.data["id"], str(self.assignment.id))
        self.assertEqual(mock_ai.call_args.args[0], self.teacher)
        self.assertEqual(mock_ai.call_args.args[1], ANY)
