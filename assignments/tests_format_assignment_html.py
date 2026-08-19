"""
Regression coverage for AssignmentProcessingService.format_assignment_standard_html.

The fields this function reads can legitimately be None, not just absent:
Assignment.title/.instructions/.total_points/.questions are all null=True on
the model, and per-question fields come from AI extraction, which can emit an
explicit `null`. Before this batch, `dict.get(key, default)` only substituted
on a *missing* key, so an explicit None either:

  - crashed the whole document (`None.upper()`, `None.title()`, iterating a
    None `questions` list, `None.replace(...)` in the due_date parse), or
  - rendered the literal text "None" into a document a student/teacher sees,
    silently and without any error signal.

These tests pin the fixed behaviour: every case below must produce a valid,
"None"-free (except where the literal word "None"/"none" is genuine content)
document rather than raising or leaking the sentinel.

Run with:
    python manage.py test assignments.tests_format_assignment_html
"""

import json

from django.test import SimpleTestCase

from assignments.prosemirror_converter import PROSEMIRROR_SCHEMA
from assignments.services import AssignmentProcessingService as A
from assignments.services import _list_or_empty, _none_default, _parse_due_date


def find_nodes(node, node_type):
    found = []
    if node.get("type") == node_type:
        found.append(node)
    for child in node.get("content", []):
        found.extend(find_nodes(child, node_type))
    return found


def text_of(node):
    if node.get("type") == "text":
        return node.get("text", "")
    return "".join(text_of(child) for child in node.get("content", []))


def base_question(**overrides):
    question = {
        "question_number": 1,
        "points": 10,
        "question_text": "<p>Explain osmosis.</p>",
        "question_type": "ESSAY",
    }
    question.update(overrides)
    return question


def base_data(**overrides):
    data = {
        "title": "<h1>Biology Midterm</h1>",
        "instructions": "<p>Answer all questions.</p>",
        "total_points": 10,
        "questions": [base_question()],
    }
    data.update(overrides)
    return data


class NoneCoalescingHelpersTest(SimpleTestCase):
    def test_none_default_replaces_only_none(self):
        self.assertEqual(_none_default(None, "fallback"), "fallback")

    def test_none_default_preserves_falsy_non_none_values(self):
        for value in (0, "", False, [], {}):
            with self.subTest(value=value):
                self.assertEqual(_none_default(value, "fallback"), value)

    def test_none_default_preserves_truthy_values(self):
        self.assertEqual(_none_default("hi", "fallback"), "hi")

    def test_list_or_empty_on_none(self):
        self.assertEqual(_list_or_empty(None, "field"), [])

    def test_list_or_empty_on_non_list(self):
        for value in ("a string", 42, {"a": 1}):
            with self.subTest(value=value):
                self.assertEqual(_list_or_empty(value, "field"), [])

    def test_list_or_empty_passes_through_a_real_list(self):
        self.assertEqual(_list_or_empty([1, 2], "field"), [1, 2])

    def test_parse_due_date_on_none_or_empty(self):
        self.assertIsNone(_parse_due_date(None))
        self.assertIsNone(_parse_due_date(""))

    def test_parse_due_date_on_malformed_input(self):
        for value in ("not-a-date", 12345, object(), [2026, 1, 1]):
            with self.subTest(value=value):
                self.assertIsNone(_parse_due_date(value))

    def test_parse_due_date_on_valid_iso_string(self):
        self.assertEqual(_parse_due_date("2026-03-05T00:00:00Z"), "March 05, 2026")

    def test_parse_due_date_without_timezone(self):
        self.assertEqual(_parse_due_date("2026-03-05T00:00:00"), "March 05, 2026")


class DocumentLevelNoneHandlingTest(SimpleTestCase):
    """Fields read once per document: title, instructions, total_points, questions."""

    def test_none_title_does_not_leak_the_word_none(self):
        html = A.format_assignment_standard_html(base_data(title=None))
        self.assertNotIn("None", html)

    def test_none_instructions_does_not_leak_the_word_none(self):
        html = A.format_assignment_standard_html(base_data(instructions=None))
        self.assertNotIn("None", html)

    def test_none_total_points_does_not_leak_the_word_none(self):
        html = A.format_assignment_standard_html(base_data(total_points=None))
        self.assertNotIn("None", html)
        self.assertIn("Total Marks:</strong> 0", html)

    def test_zero_total_points_is_preserved_not_blanked(self):
        html = A.format_assignment_standard_html(base_data(total_points=0))
        self.assertIn("Total Marks:</strong> 0", html)

    def test_missing_total_points_key_defaults_to_zero(self):
        data = base_data()
        del data["total_points"]
        html = A.format_assignment_standard_html(data)
        self.assertIn("Total Marks:</strong> 0", html)

    def test_none_questions_does_not_raise(self):
        html = A.format_assignment_standard_html(base_data(questions=None))
        self.assertIn("Assignment Questions", html)

    def test_missing_questions_key_does_not_raise(self):
        data = base_data()
        del data["questions"]
        html = A.format_assignment_standard_html(data)
        self.assertIn("Assignment Questions", html)

    def test_non_list_questions_does_not_raise(self):
        html = A.format_assignment_standard_html(base_data(questions="not a list"))
        self.assertIn("Assignment Questions", html)

    def test_empty_dict_input_does_not_raise(self):
        html = A.format_assignment_standard_html({})
        self.assertIn("Assignment Questions", html)
        self.assertNotIn("None", html)


class DueDateHandlingTest(SimpleTestCase):
    def test_none_due_date_omits_the_due_date_line(self):
        html = A.format_assignment_standard_html(base_data(due_date=None))
        self.assertNotIn("Due Date", html)

    def test_malformed_due_date_omits_the_line_instead_of_raising(self):
        html = A.format_assignment_standard_html(base_data(due_date="not-a-date"))
        self.assertNotIn("Due Date", html)
        self.assertNotIn("None", html)

    def test_non_string_due_date_omits_the_line_instead_of_raising(self):
        html = A.format_assignment_standard_html(base_data(due_date=12345))
        self.assertNotIn("Due Date", html)

    def test_valid_due_date_is_rendered(self):
        html = A.format_assignment_standard_html(
            base_data(due_date="2026-03-05T00:00:00Z")
        )
        self.assertIn("Due Date", html)
        self.assertIn("March 05, 2026", html)


class QuestionLevelNoneHandlingTest(SimpleTestCase):
    """Fields read per-question: question_text, question_type, options, rubric, etc."""

    def test_none_question_text_does_not_leak_the_word_none(self):
        html = A.format_assignment_standard_html(
            base_data(questions=[base_question(question_text=None)])
        )
        self.assertNotIn("None", html)

    def test_none_question_type_does_not_raise(self):
        html = A.format_assignment_standard_html(
            base_data(questions=[base_question(question_type=None)])
        )
        self.assertIn("Question 1", html)

    def test_missing_question_type_does_not_raise(self):
        question = base_question()
        del question["question_type"]
        html = A.format_assignment_standard_html(base_data(questions=[question]))
        self.assertIn("Question 1", html)

    def test_non_string_question_type_does_not_raise(self):
        html = A.format_assignment_standard_html(
            base_data(questions=[base_question(question_type=123)])
        )
        self.assertIn("Question 1", html)

    def test_none_question_number_and_points_do_not_leak_none(self):
        html = A.format_assignment_standard_html(
            base_data(questions=[base_question(question_number=None, points=None)])
        )
        self.assertNotIn("None", html)

    def test_zero_points_on_a_question_is_preserved(self):
        html = A.format_assignment_standard_html(
            base_data(questions=[base_question(points=0)])
        )
        self.assertIn("(0 marks)", html)

    def test_non_dict_question_entries_are_skipped_not_raised(self):
        html = A.format_assignment_standard_html(
            base_data(questions=["not a dict", None, 42, base_question()])
        )
        self.assertIn("Question 1", html)
        self.assertEqual(html.count("<strong>Question"), 1)

    def test_all_non_dict_question_entries_still_returns_a_document(self):
        html = A.format_assignment_standard_html(base_data(questions=["bad", None, 42]))
        self.assertIn("Assignment Questions", html)
        self.assertNotIn("<strong>Question", html)


class OptionsNoneHandlingTest(SimpleTestCase):
    def test_none_options_on_objective_question_does_not_raise(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[base_question(question_type="OBJECTIVE", options=None)]
            )
        )
        self.assertIn("Question 1", html)

    def test_non_list_options_does_not_raise_or_iterate_characters(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[base_question(question_type="OBJECTIVE", options="AB")]
            )
        )
        # A string was previously iterated character-by-character by
        # `enumerate(options)`, rendering "<li>A</li><li>B</li>" instead of
        # being treated as the malformed, not-really-a-list value it is.
        self.assertNotIn("<li>A</li>", html)
        self.assertNotIn("<li>B</li>", html)
        self.assertNotIn("<ul>", html)

    def test_none_item_within_options_does_not_leak_none(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        question_type="OBJECTIVE",
                        options=[None, "<p>Real option</p>"],
                    )
                ]
            )
        )
        self.assertIn("Real option", html)
        self.assertNotIn("None", html)

    def test_real_options_still_render(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        question_type="OBJECTIVE",
                        options=["<p>Choice A</p>", "<p>Choice B</p>"],
                    )
                ]
            )
        )
        self.assertIn("Choice A", html)
        self.assertIn("Choice B", html)


class OptionsListStructureTest(SimpleTestCase):
    """
    Regression: options were rendered as bare <p> tags inside a <div
    style="padding-left:25px">. `div` has no schema node, so it was silently
    dropped on conversion, its children hoisted to the top level as flat,
    unindented, ungrouped paragraphs - indentation the student never actually
    saw. Options are now a real <ul>/<li> list, which the schema does support
    (bullet_list/list_item, via add_list_nodes), so grouping and indentation
    survive conversion and come from the editor's own list styling.
    """

    def test_options_render_as_a_bullet_list_in_the_html(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        question_type="OBJECTIVE",
                        options=["A) one", "B) two"],
                    )
                ]
            )
        )
        self.assertIn("<ul>", html)
        self.assertIn("<li>A) one</li>", html)
        self.assertIn("<li>B) two</li>", html)
        self.assertNotIn("padding-left", html)

    def test_options_convert_to_a_single_bullet_list_with_one_item_per_option(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        question_type="OBJECTIVE",
                        options=["A) one", "B) two", "C) three", "D) four"],
                    )
                ]
            )
        )
        doc = json.loads(A.html_to_prosemirror_text(html))

        lists = find_nodes(doc, "bullet_list")
        items = find_nodes(doc, "list_item")

        self.assertEqual(len(lists), 1)
        self.assertEqual(len(items), 4)
        self.assertEqual(
            [text_of(item) for item in items],
            ["A) one", "B) two", "C) three", "D) four"],
        )

    def test_option_order_is_preserved(self):
        """Option order carries the correct answer's position - must not shuffle."""
        options = [f"{letter}) option" for letter in "ABCDEFGH"]
        html = A.format_assignment_standard_html(
            base_data(
                questions=[base_question(question_type="OBJECTIVE", options=options)]
            )
        )
        doc = json.loads(A.html_to_prosemirror_text(html))
        items = find_nodes(doc, "list_item")

        self.assertEqual([text_of(item) for item in items], options)

    def test_option_html_formatting_is_preserved_inside_the_list_item(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        question_type="OBJECTIVE",
                        options=["<strong>Bold option</strong>"],
                    )
                ]
            )
        )
        doc = json.loads(A.html_to_prosemirror_text(html))
        items = find_nodes(doc, "list_item")

        self.assertEqual(len(items), 1)
        found_marks = set()

        def walk(node):
            for mark in node.get("marks", []):
                found_marks.add(mark["type"])
            for child in node.get("content", []):
                walk(child)

        walk(items[0])
        self.assertIn("strong", found_marks)
        self.assertIn("Bold option", text_of(items[0]))

    def test_a_single_option_still_produces_a_valid_list(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(question_type="OBJECTIVE", options=["Only one"])
                ]
            )
        )
        doc = json.loads(A.html_to_prosemirror_text(html))

        self.assertEqual(len(find_nodes(doc, "bullet_list")), 1)
        self.assertEqual(len(find_nodes(doc, "list_item")), 1)

    def test_no_options_list_is_emitted_for_a_non_objective_question(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        question_type="ESSAY", options=["should", "be", "ignored"]
                    )
                ]
            )
        )
        self.assertNotIn("<ul>", html)

    def test_no_options_list_is_emitted_when_options_is_empty(self):
        html = A.format_assignment_standard_html(
            base_data(questions=[base_question(question_type="OBJECTIVE", options=[])])
        )
        self.assertNotIn("<ul>", html)

    def test_hostile_option_content_is_still_sanitised(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        question_type="OBJECTIVE",
                        options=['<img src=x onerror="alert(1)">A) danger'],
                    )
                ]
            )
        )
        self.assertNotIn("onerror", html)
        self.assertIn("A) danger", html)

    def test_options_with_hostile_answer_from_a_real_end_to_end_document(self):
        """
        Mirrors the shape of a live chemistry MCQ (see the production sample
        that surfaced this bug): a paragraph question followed by lettered
        options, going through the full render -> sanitize -> convert path.
        """
        data = base_data(
            title="<h1>Physical Chemistry — Assessment</h1>",
            questions=[
                {
                    "question_number": 1,
                    "points": 3,
                    "question_text": "<p>What is the conjugate base of H2SO4?</p>",
                    "question_type": "OBJECTIVE",
                    "options": [
                        "A) SO4^2-",
                        "B) HSO4^-",
                        "C) H3SO4^+",
                        "D) H2SO3",
                    ],
                }
            ],
        )
        html = A.format_assignment_standard_html(data)
        doc = json.loads(A.html_to_prosemirror_text(html))

        items = find_nodes(doc, "list_item")
        self.assertEqual(len(items), 4)
        self.assertEqual(text_of(items[1]), "B) HSO4^-")

        def check_known(node):
            node_type = node.get("type")
            if node_type != "text":
                self.assertIn(node_type, PROSEMIRROR_SCHEMA.nodes, node_type)
            for mark in node.get("marks", []):
                self.assertIn(mark["type"], PROSEMIRROR_SCHEMA.marks, mark["type"])
            for child in node.get("content", []):
                check_known(child)

        check_known(doc)


class RubricNoneHandlingTest(SimpleTestCase):
    def test_none_rubric_does_not_raise(self):
        html = A.format_assignment_standard_html(
            base_data(questions=[base_question(rubric=None)])
        )
        self.assertIn("Question 1", html)
        self.assertNotIn("Marking Guide", html)

    def test_non_list_rubric_does_not_raise(self):
        html = A.format_assignment_standard_html(
            base_data(questions=[base_question(rubric="not a list")])
        )
        self.assertNotIn("Marking Guide", html)

    def test_non_dict_rubric_entries_are_skipped_not_raised(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        rubric=[
                            "bad entry",
                            None,
                            {"level": "good", "points": 8, "description": "Solid"},
                        ]
                    )
                ]
            )
        )
        self.assertIn("Solid", html)
        self.assertIn("Good", html)  # .title()-cased
        # One header <tr> plus exactly one data row for the single valid entry.
        self.assertEqual(html.count("<tr>"), 2)
        self.assertEqual(html.count("<td>"), 2)  # level + description cells

    def test_none_rubric_level_does_not_leak_none(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        rubric=[{"level": None, "points": 5, "description": "d"}]
                    )
                ]
            )
        )
        self.assertNotIn(">None<", html)

    def test_none_rubric_points_does_not_leak_none(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        rubric=[{"level": "good", "points": None, "description": "d"}]
                    )
                ]
            )
        )
        self.assertNotIn(">None<", html)

    def test_none_rubric_description_does_not_leak_none(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        rubric=[{"level": "good", "points": 5, "description": None}]
                    )
                ]
            )
        )
        self.assertNotIn(">None<", html)

    def test_zero_rubric_points_is_preserved(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        rubric=[{"level": "poor", "points": 0, "description": "d"}]
                    )
                ]
            )
        )
        self.assertIn('<td align="center">0</td>', html)

    def test_rubric_is_omitted_when_include_rubric_is_false(self):
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        rubric=[{"level": "good", "points": 5, "description": "d"}],
                        model_answer="<p>The answer.</p>",
                    )
                ]
            ),
            include_rubric=False,
        )
        self.assertNotIn("Marking Guide", html)
        self.assertNotIn("The answer.", html)

    def test_genuine_word_none_as_a_rubric_level_is_not_mistaken_for_the_bug(self):
        """
        'none'.title() == 'None' is correct, expected behaviour - the fix
        must not treat a real rubric level literally named "none" as if it
        were the missing-value sentinel.
        """
        html = A.format_assignment_standard_html(
            base_data(
                questions=[
                    base_question(
                        rubric=[{"level": "none", "points": 0, "description": "d"}]
                    )
                ]
            )
        )
        self.assertIn("<td>None</td>", html)


class ModelAnswerNoneHandlingTest(SimpleTestCase):
    def test_none_model_answer_is_simply_omitted(self):
        html = A.format_assignment_standard_html(
            base_data(questions=[base_question(model_answer=None)])
        )
        self.assertNotIn("Model Answer", html)
        self.assertNotIn("None", html)

    def test_real_model_answer_renders_when_include_rubric_true(self):
        html = A.format_assignment_standard_html(
            base_data(questions=[base_question(model_answer="<p>Correct answer</p>")])
        )
        self.assertIn("Correct answer", html)


class ExistingBehaviourIsUnchangedTest(SimpleTestCase):
    """
    A well-formed document (the common case) must render identically to
    before this hardening pass - no new stray text, no dropped content.
    """

    def test_a_complete_well_formed_assignment_renders_everything(self):
        data = {
            "title": "<h1>Final Exam</h1>",
            "instructions": "<p>Read carefully.</p>",
            "total_points": 25,
            "due_date": "2026-05-01T00:00:00Z",
            "questions": [
                {
                    "question_number": 1,
                    "points": 10,
                    "question_text": "<p>Define entropy.</p>",
                    "question_type": "ESSAY",
                    "rubric": [
                        {"level": "excellent", "points": 10, "description": "Full"},
                        {"level": "poor", "points": 2, "description": "Weak"},
                    ],
                    "model_answer": "<p>Entropy is...</p>",
                },
                {
                    "question_number": 2,
                    "points": 15,
                    "question_text": "<p>Pick one.</p>",
                    "question_type": "OBJECTIVE",
                    "options": ["<p>Option A</p>", "<p>Option B</p>"],
                },
            ],
        }
        html = A.format_assignment_standard_html(data)

        for expected in (
            "Final Exam",
            "Read carefully.",
            "Total Marks:</strong> 25",
            "May 01, 2026",
            "Define entropy.",
            "Excellent",
            "Entropy is...",
            "Pick one.",
            "Option A",
            "Option B",
        ):
            self.assertIn(expected, html)
        self.assertNotIn("None", html)

    def test_student_facing_version_hides_rubric_and_model_answer(self):
        data = {
            "title": "t",
            "instructions": "i",
            "total_points": 10,
            "questions": [
                {
                    "question_number": 1,
                    "points": 10,
                    "question_text": "<p>Q</p>",
                    "question_type": "ESSAY",
                    "rubric": [
                        {"level": "good", "points": 10, "description": "Secret"}
                    ],
                    "model_answer": "<p>Secret answer</p>",
                }
            ],
        }
        html = A.format_assignment_standard_html(data, include_rubric=False)
        self.assertNotIn("Secret", html)

    def test_omitting_the_document_header_skips_title_and_instructions(self):
        html = A.format_assignment_standard_html(
            base_data(title="Should not appear", instructions="Nor this"),
            include_document_header=False,
        )
        self.assertNotIn("Should not appear", html)
        self.assertNotIn("Nor this", html)
        self.assertIn("Assignment Questions", html)

    def test_hostile_field_values_are_still_sanitised(self):
        html = A.format_assignment_standard_html(
            base_data(
                title="<script>alert(1)</script>Title",
                questions=[
                    base_question(question_text='<img src=x onerror="alert(1)">Q')
                ],
            )
        )
        self.assertNotIn("<script", html)
        self.assertNotIn("onerror", html)
        self.assertIn("Title", html)
        self.assertIn("Q", html)


class ConversionPipelineIntegrationTest(SimpleTestCase):
    """
    The whole point of hardening this function is that a malformed AI
    extraction must not take down html_to_prosemirror_text either - this
    exercises the full render -> convert path end to end.
    """

    def test_a_document_with_none_fields_survives_the_full_pipeline(self):
        data = {
            "title": None,
            "instructions": None,
            "total_points": None,
            "due_date": "garbage",
            "questions": [
                {
                    "question_number": None,
                    "points": None,
                    "question_text": None,
                    "question_type": None,
                    "options": None,
                    "rubric": [{"level": None, "points": None, "description": None}],
                    "model_answer": None,
                },
                "a malformed entry that is not even a dict",
                None,
            ],
        }
        html = A.format_assignment_standard_html(data)
        payload = A.html_to_prosemirror_text(html)

        self.assertNotIn("None", html)
        self.assertIsNotNone(payload)
