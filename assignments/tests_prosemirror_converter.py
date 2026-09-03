"""
Tests for assignments.prosemirror_converter.

These lock in the behaviour of the HTML -> ProseMirror conversion, which sits
on the write path for every assignment and every graded submission. They run as
SimpleTestCase: the converter is pure and touches no database.
"""

import json

from django.test import SimpleTestCase
from prosemirror.model import Schema
from prosemirror.schema.basic import schema as basic_schema

from assignments.prosemirror_converter import (
    CONVERSION_CACHE_SIZE,
    PROSEMIRROR_SCHEMA,
    ProseMirrorConversionError,
    _cached_prosemirror_text,
    build_schema,
    html_to_prosemirror_json,
    html_to_prosemirror_text,
    safe_style,
    sanitize_editor_html,
    text_align_from_style,
)
from assignments.services import AssignmentProcessingService


def blocks(doc):
    return doc.get("content", [])


def text_of(node):
    """Concatenate every text leaf under a node."""
    if node.get("type") == "text":
        return node.get("text", "")
    return "".join(text_of(child) for child in node.get("content", []))


def doc_text(doc):
    return "".join(text_of(block) for block in blocks(doc))


def find_nodes(node, node_type):
    found = []
    if node.get("type") == node_type:
        found.append(node)
    for child in node.get("content", []):
        found.extend(find_nodes(child, node_type))
    return found


def marks_on(doc, text):
    """Every mark type applied to the text leaf equal to `text`."""
    result = []

    def walk(node):
        if node.get("type") == "text" and node.get("text") == text:
            result.extend(m["type"] for m in node.get("marks", []))
        for child in node.get("content", []):
            walk(child)

    walk(doc)
    return result


class HeadingLevelTest(SimpleTestCase):
    """
    Regression: every heading collapsed to level 1.

    prosemirror-py replaces rule.attrs with the getAttrs return value, so the
    statically declared {"level": i} was discarded and `level` fell back to its
    default of 1 for h1 through h6 alike.
    """

    def test_each_heading_level_is_preserved(self):
        html = "".join(f"<h{i}>Head {i}</h{i}>" for i in range(1, 7))
        doc = html_to_prosemirror_json(html)

        headings = find_nodes(doc, "heading")
        self.assertEqual(len(headings), 6)
        self.assertEqual([h["attrs"]["level"] for h in headings], [1, 2, 3, 4, 5, 6])
        self.assertEqual(
            [text_of(h) for h in headings],
            [f"Head {i}" for i in range(1, 7)],
        )

    def test_heading_level_survives_alongside_alignment(self):
        doc = html_to_prosemirror_json('<h4 style="text-align:center">C</h4>')
        heading = find_nodes(doc, "heading")[0]

        self.assertEqual(heading["attrs"]["level"], 4)
        self.assertEqual(heading["attrs"]["textAlign"], "center")

    def test_the_document_renderers_headings_are_not_flattened(self):
        """The two real producers both rely on multi-level headings."""
        html = AssignmentProcessingService.format_assignment_standard_html(
            {
                "title": "<h1>Term Test</h1>",
                "instructions": "<p>Answer all.</p>",
                "total_points": 20,
                "questions": [
                    {
                        "question_number": 1,
                        "points": 10,
                        "question_text": "<p>Define osmosis.</p>",
                        "question_type": "ESSAY",
                    }
                ],
            }
        )
        doc = html_to_prosemirror_json(html)
        levels = {h["attrs"]["level"] for h in find_nodes(doc, "heading")}

        self.assertIn(2, levels, "the 'Assignment Questions' h2 was flattened")


class TextAlignTest(SimpleTestCase):
    def test_alignment_is_read_from_style(self):
        doc = html_to_prosemirror_json('<p style="text-align:right">r</p>')
        self.assertEqual(blocks(doc)[0]["attrs"]["textAlign"], "right")

    def test_default_alignment_when_absent(self):
        doc = html_to_prosemirror_json("<p>plain</p>")
        self.assertEqual(blocks(doc)[0]["attrs"]["textAlign"], "left")

    def test_declaration_without_a_colon_does_not_raise(self):
        """
        Regression: `style.split(":")[1]` raised IndexError, which surfaced as a
        conversion failure *after* the AI call for that grading run was billed.
        """
        doc = html_to_prosemirror_json('<p style="text-align">x</p>')
        self.assertEqual(doc_text(doc), "x")
        self.assertEqual(blocks(doc)[0]["attrs"]["textAlign"], "left")

    def test_similar_property_names_do_not_match(self):
        self.assertEqual(
            text_align_from_style({"style": "-webkit-text-align-last: right"}),
            "left",
        )

    def test_unknown_alignment_value_falls_back(self):
        self.assertEqual(text_align_from_style({"style": "text-align: bogus"}), "left")

    def test_alignment_is_case_and_space_insensitive(self):
        self.assertEqual(
            text_align_from_style({"style": "  TEXT-ALIGN :  CENTER  "}), "center"
        )

    def test_missing_style_attribute(self):
        self.assertEqual(text_align_from_style({}), "left")


class InlineFormattingTest(SimpleTestCase):
    """
    Regression: sup/sub/u passed the sanitiser then dissolved into bare text,
    turning x² into x2 and H₂O into H2O in answers that are then graded.
    """

    def test_superscript_and_subscript_are_marks_not_lost_formatting(self):
        doc = html_to_prosemirror_json("<p>x<sup>2</sup> and H<sub>2</sub>O</p>")

        self.assertIn("superscript", marks_on(doc, "2"))
        self.assertIn("subscript", marks_on(doc, "2"))
        self.assertEqual(len(find_nodes(doc, "text")), 5)

    def test_underline_is_preserved(self):
        doc = html_to_prosemirror_json("<p><u>under</u></p>")
        self.assertEqual(marks_on(doc, "under"), ["underline"])

    def test_strikethrough_variants(self):
        """
        Mark name is "strike", not "strikethrough": Tiptap's stock
        @tiptap/extension-strike registers under that name, and a document
        must use the same mark names as the editor loading it.
        """
        for tag in ("s", "del", "strike"):
            with self.subTest(tag=tag):
                doc = html_to_prosemirror_json(f"<p><{tag}>gone</{tag}></p>")
                self.assertEqual(marks_on(doc, "gone"), ["strike"])

    def test_basic_marks_still_work(self):
        doc = html_to_prosemirror_json(
            "<p><strong>b</strong><em>i</em><code>c</code></p>"
        )
        self.assertEqual(marks_on(doc, "b"), ["strong"])
        self.assertEqual(marks_on(doc, "i"), ["em"])
        self.assertEqual(marks_on(doc, "c"), ["code"])

    def test_b_and_i_map_onto_strong_and_em(self):
        doc = html_to_prosemirror_json("<p><b>b</b><i>i</i></p>")
        self.assertEqual(marks_on(doc, "b"), ["strong"])
        self.assertEqual(marks_on(doc, "i"), ["em"])

    def test_superscript_and_subscript_exclude_each_other(self):
        doc = html_to_prosemirror_json("<p><sup><sub>x</sub></sup></p>")
        applied = marks_on(doc, "x")
        self.assertNotEqual(applied, ["superscript", "subscript"])
        self.assertLessEqual(len(applied), 1)


class MathBlockTest(SimpleTestCase):
    """
    Regression: sanitize_ai_html allows class="math-block" on a span and allows
    no style at all, while textStyle captured only style - so the two mechanisms
    were exactly disjoint and math blocks lost their identity.
    """

    def test_math_block_class_survives_conversion(self):
        doc = html_to_prosemirror_json('<p><span class="math-block">E=mc^2</span></p>')
        self.assertEqual(marks_on(doc, "E=mc^2"), ["textStyle"])
        span = find_nodes(doc, "text")[0]
        self.assertEqual(span["marks"][0]["attrs"]["class"], "math-block")

    def test_math_block_survives_the_full_sanitise_then_convert_path(self):
        sanitized = AssignmentProcessingService.sanitize_ai_html(
            '<p><span class="math-block">a+b</span></p>'
        )
        doc = html_to_prosemirror_json(sanitized)
        text_node = find_nodes(doc, "text")[0]
        self.assertEqual(text_node["marks"][0]["attrs"]["class"], "math-block")

    def test_plain_span_gets_no_mark(self):
        doc = html_to_prosemirror_json("<p><span>plain</span></p>")
        self.assertEqual(marks_on(doc, "plain"), [])


class TableTest(SimpleTestCase):
    """Regression: colspan/rowspan were allowlisted then dropped by the schema."""

    def test_colspan_and_rowspan_are_preserved(self):
        doc = html_to_prosemirror_json(
            "<table><thead><tr><th colspan='3'>H</th></tr></thead>"
            "<tbody><tr><td rowspan='2'>a</td><td>b</td></tr></tbody></table>"
        )
        header = find_nodes(doc, "table_header")[0]
        cells = find_nodes(doc, "table_cell")

        self.assertEqual(header["attrs"]["colspan"], 3)
        self.assertEqual(cells[0]["attrs"]["rowspan"], 2)
        self.assertEqual(cells[1]["attrs"]["colspan"], 1)

    def test_absent_spans_default_to_one(self):
        doc = html_to_prosemirror_json("<table><tr><td>a</td></tr></table>")
        cell = find_nodes(doc, "table_cell")[0]
        self.assertEqual((cell["attrs"]["colspan"], cell["attrs"]["rowspan"]), (1, 1))

    def test_malformed_and_hostile_spans_fall_back_to_one(self):
        for value in ("abc", "-4", "0", "999999999", "", "2.5"):
            with self.subTest(value=value):
                doc = html_to_prosemirror_json(
                    f"<table><tr><td colspan='{value}'>a</td></tr></table>"  # noqa: B907
                )
                cell = find_nodes(doc, "table_cell")[0]
                self.assertEqual(cell["attrs"]["colspan"], 1)

    def test_thead_and_tbody_wrappers_do_not_lose_rows(self):
        doc = html_to_prosemirror_json(
            "<table><thead><tr><th>H</th></tr></thead>"
            "<tbody><tr><td>1</td></tr><tr><td>2</td></tr></tbody></table>"
        )
        self.assertEqual(len(find_nodes(doc, "table_row")), 3)

    def test_nested_tables_are_handled(self):
        doc = html_to_prosemirror_json(
            "<table><tr><td><table><tr><td>x</td></tr></table></td></tr></table>"
        )
        self.assertEqual(len(find_nodes(doc, "table")), 2)
        self.assertEqual(doc_text(doc), "x")

    def test_empty_table_does_not_raise(self):
        doc = html_to_prosemirror_json("<table></table>")
        self.assertEqual(len(find_nodes(doc, "table")), 1)

    def test_rubric_table_from_the_real_renderer_keeps_its_cells(self):
        html = AssignmentProcessingService.format_assignment_standard_html(
            {
                "title": "T",
                "instructions": "I",
                "total_points": 10,
                "questions": [
                    {
                        "question_number": 1,
                        "points": 10,
                        "question_text": "<p>Q</p>",
                        "question_type": "ESSAY",
                        "rubric": [
                            {"level": "excellent", "points": 10, "description": "Full"},
                            {"level": "poor", "points": 2, "description": "Weak"},
                        ],
                    }
                ],
            },
            include_rubric=True,
        )
        doc = html_to_prosemirror_json(html)

        self.assertEqual(len(find_nodes(doc, "table_header")), 3)
        self.assertEqual(len(find_nodes(doc, "table_cell")), 6)
        self.assertIn("Excellent", doc_text(doc))


class SecurityTest(SimpleTestCase):
    """
    The converter sanitises its own input. It is reached from four modules with
    AI-derived HTML, and its output is stored and later rendered in a teacher's
    browser, so the boundary is enforced here rather than assumed of callers.
    """

    def test_script_tag_and_its_source_are_removed(self):
        doc = html_to_prosemirror_json("<p>a</p><script>alert(1)</script><p>b</p>")
        self.assertEqual(doc_text(doc), "ab")

    def test_style_element_source_is_removed(self):
        doc = html_to_prosemirror_json("<style>body{color:red}</style><p>hi</p>")
        self.assertEqual(doc_text(doc), "hi")

    def test_iframe_is_removed(self):
        doc = html_to_prosemirror_json(
            '<p>x</p><iframe src="https://evil.test"></iframe>'
        )
        self.assertEqual(doc_text(doc), "x")
        self.assertEqual(find_nodes(doc, "image"), [])

    def test_event_handler_attributes_are_removed(self):
        doc = json.dumps(html_to_prosemirror_json('<p onclick="alert(1)">x</p>'))
        self.assertNotIn("onclick", doc)
        self.assertNotIn("alert", doc)

    def test_javascript_href_drops_the_link_mark(self):
        doc = html_to_prosemirror_json('<p><a href="javascript:alert(1)">click</a></p>')
        self.assertEqual(doc_text(doc), "click")
        self.assertEqual(marks_on(doc, "click"), [])

    def test_data_uri_href_drops_the_link_mark(self):
        doc = html_to_prosemirror_json(
            '<p><a href="data:text/html;base64,PHNjcmlwdD4=">x</a></p>'
        )
        self.assertEqual(marks_on(doc, "x"), [])

    def test_safe_links_are_kept(self):
        for href in ("https://ok.test/a", "http://ok.test/a", "mailto:t@ok.test"):
            with self.subTest(href=href):
                fragment = f'<p><a href="{href}">x</a></p>'  # noqa: B907
                doc = html_to_prosemirror_json(fragment)
                self.assertEqual(marks_on(doc, "x"), ["link"])
                link = find_nodes(doc, "text")[0]["marks"][0]
                self.assertEqual(link["attrs"]["href"], href)

    def test_image_with_a_hostile_src_is_dropped(self):
        for src in (
            "javascript:alert(1)",
            "data:image/png;base64,AAA",
            "/relative.png",
            "x",
        ):
            with self.subTest(src=src):
                fragment = f'<p><img src="{src}">t</p>'  # noqa: B907
                doc = html_to_prosemirror_json(fragment)
                self.assertEqual(find_nodes(doc, "image"), [])
                self.assertEqual(doc_text(doc), "t")

    def test_legitimate_question_image_survives(self):
        """format_assignment_standard_html really does emit <img>."""
        doc = html_to_prosemirror_json(
            '<p><img src="https://cdn.test/q1.png" alt="Question 1 image"></p>'
        )
        image = find_nodes(doc, "image")[0]
        self.assertEqual(image["attrs"]["src"], "https://cdn.test/q1.png")
        self.assertEqual(image["attrs"]["alt"], "Question 1 image")

    def test_style_value_that_could_break_out_of_an_attribute_is_dropped(self):
        doc = html_to_prosemirror_json(
            "<p style='color:red\" onload=\"alert(1)'>hi</p>"
        )
        self.assertIsNone(blocks(doc)[0]["attrs"]["style"])
        self.assertNotIn("onload", json.dumps(doc))

    def test_url_and_expression_in_style_are_dropped(self):
        """
        Two layers act here and either outcome is acceptable, so the assertion
        is on what must never survive rather than on which layer caught it:
        bleach's CSS sanitiser drops declarations whose *property* is not
        allowlisted (`background` -> gone, `color:red` -> kept), and safe_style
        discards the whole attribute when a value looks like it could break out.
        """
        for style in (
            "background-color:url(https://evil.test/x)",
            "width:expression(alert(1))",
            "color:red;background:url(javascript:1)",
        ):
            with self.subTest(style=style):
                fragment = f'<p style="{style}">x</p>'  # noqa: B907
                doc = html_to_prosemirror_json(fragment)
                surviving = blocks(doc)[0]["attrs"]["style"] or ""
                self.assertNotIn("url(", surviving)
                self.assertNotIn("javascript", surviving)
                self.assertNotIn("expression", surviving)

    def test_benign_style_is_kept(self):
        doc = html_to_prosemirror_json('<p style="color:#333">x</p>')
        self.assertIn("color", blocks(doc)[0]["attrs"]["style"])

    def test_safe_style_helper(self):
        self.assertIsNone(safe_style({}))
        self.assertIsNone(safe_style({"style": "   "}))
        self.assertIsNone(safe_style({"style": 'color:red"'}))
        self.assertEqual(safe_style({"style": "color:red"}), "color:red")

    def test_sanitize_editor_html_removes_disallowed_markup(self):
        cleaned = sanitize_editor_html('<p>ok</p><object data="x"></object>')
        self.assertNotIn("object", cleaned)
        self.assertIn("<p>ok</p>", cleaned)


class SchemaIsolationTest(SimpleTestCase):
    """
    Regression: `marks_spec = basic_schema.spec["marks"]` took a reference to
    prosemirror-py's module-level dict and updated it in place, permanently
    reconfiguring every other schema built in the process.
    """

    def test_building_the_schema_does_not_mutate_the_library_spec(self):
        marks_before = dict(basic_schema.spec["marks"])
        nodes_before = dict(basic_schema.spec["nodes"])
        link_before = dict(basic_schema.spec["marks"]["link"])

        build_schema()
        html_to_prosemirror_json("<p>x</p>")

        self.assertEqual(set(basic_schema.spec["marks"]), set(marks_before))
        self.assertEqual(set(basic_schema.spec["nodes"]), set(nodes_before))
        self.assertEqual(basic_schema.spec["marks"]["link"], link_before)

    def test_a_vanilla_schema_is_unaffected_by_our_customisations(self):
        vanilla = Schema(
            {
                "nodes": dict(basic_schema.spec["nodes"]),
                "marks": dict(basic_schema.spec["marks"]),
            }
        )
        self.assertNotIn("textStyle", vanilla.marks)
        self.assertNotIn("superscript", vanilla.marks)

    def test_the_shared_schema_is_built_once(self):
        self.assertIsInstance(PROSEMIRROR_SCHEMA, Schema)
        self.assertIn("superscript", PROSEMIRROR_SCHEMA.marks)
        self.assertIn("table_cell", PROSEMIRROR_SCHEMA.nodes)

    def test_repeated_conversions_are_deterministic(self):
        html = "<h3>t</h3><p>a<sup>1</sup></p><table><tr><td>c</td></tr></table>"
        first = html_to_prosemirror_text(html)
        for _ in range(5):
            self.assertEqual(html_to_prosemirror_text(html), first)


class SerialisationTest(SimpleTestCase):
    """
    Regression: submission callers assigned the dict to a TextField, so Django
    coerced it with str() and stored an unparseable Python repr.
    """

    def test_text_entry_point_returns_parseable_json(self):
        payload = html_to_prosemirror_text("<p>hello</p>")

        self.assertIsInstance(payload, str)
        self.assertEqual(json.loads(payload)["type"], "doc")
        self.assertNotIn("'", payload)

    def test_text_and_json_entry_points_agree(self):
        html = "<h2>t</h2><p>b</p>"
        self.assertEqual(
            json.loads(html_to_prosemirror_text(html)),
            html_to_prosemirror_json(html),
        )

    def test_none_and_true_are_serialised_as_json_not_python(self):
        payload = html_to_prosemirror_text("<p>x</p>")
        self.assertIn("null", payload)
        self.assertNotIn("None", payload)

    def test_service_facade_matches_the_module_functions(self):
        html = "<p>via the service</p>"
        self.assertEqual(
            AssignmentProcessingService.html_to_prosemirror_json(html),
            html_to_prosemirror_json(html),
        )
        self.assertEqual(
            AssignmentProcessingService.html_to_prosemirror_text(html),
            html_to_prosemirror_text(html),
        )


class InputValidationTest(SimpleTestCase):
    def test_empty_and_non_string_input_raises_value_error(self):
        for value in ("", "   ", "\n\t", None, 42, {}, []):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    html_to_prosemirror_json(value)  # type: ignore[arg-type]

    def test_conversion_error_still_subclasses_runtime_error(self):
        self.assertTrue(issubclass(ProseMirrorConversionError, RuntimeError))

    def test_control_characters_do_not_break_parsing(self):
        doc = html_to_prosemirror_json("<p>a\x00b\x08c</p>")
        self.assertEqual(doc_text(doc), "abc")

    def test_tabs_and_newlines_collapse_the_way_html_says_they_should(self):
        """
        Tabs and newlines are legal XML and must not be stripped as control
        characters, but inline content has `whitespace: normal`, so they
        collapse to a single space - the same thing a browser does. What
        matters is that the character is not lost outright.
        """
        self.assertEqual(doc_text(html_to_prosemirror_json("<p>a\tb</p>")), "a b")
        self.assertEqual(doc_text(html_to_prosemirror_json("<p>a\nb</p>")), "a b")

    def test_plain_text_without_markup_becomes_a_paragraph(self):
        doc = html_to_prosemirror_json("just words")
        self.assertEqual(blocks(doc)[0]["type"], "paragraph")
        self.assertEqual(doc_text(doc), "just words")

    def test_stray_closing_tag_cannot_escape_the_wrapper(self):
        doc = html_to_prosemirror_json("</div><p>after</p>")
        self.assertEqual(doc_text(doc), "after")

    def test_unclosed_tags_are_recovered(self):
        doc = html_to_prosemirror_json("<p>a<strong>b")
        self.assertEqual(doc_text(doc), "ab")

    def test_html_entities_are_decoded(self):
        doc = html_to_prosemirror_json("<p>a &amp; b &lt;c&gt;</p>")
        self.assertEqual(doc_text(doc), "a & b <c>")

    def test_unicode_is_preserved(self):
        doc = html_to_prosemirror_json("<p>Δ 温度 café — ✓</p>")
        self.assertEqual(doc_text(doc), "Δ 温度 café — ✓")
        self.assertEqual(
            json.loads(html_to_prosemirror_text("<p>Δ</p>"))["type"], "doc"
        )


class StructuralFidelityTest(SimpleTestCase):
    def test_lists_are_converted(self):
        doc = html_to_prosemirror_json("<ul><li>a</li><li>b</li></ul>")
        self.assertEqual(len(find_nodes(doc, "list_item")), 2)

    def test_ordered_list_start_is_kept(self):
        doc = html_to_prosemirror_json("<ol start='3'><li>a</li></ol>")
        self.assertEqual(find_nodes(doc, "ordered_list")[0]["attrs"]["order"], 3)

    def test_nested_lists(self):
        doc = html_to_prosemirror_json("<ul><li>a<ul><li>b</li></ul></li></ul>")
        self.assertEqual(len(find_nodes(doc, "bullet_list")), 2)

    def test_line_breaks_and_rules(self):
        doc = html_to_prosemirror_json("<p>a<br>b</p><hr>")
        self.assertEqual(len(find_nodes(doc, "hard_break")), 1)
        self.assertEqual(len(find_nodes(doc, "horizontal_rule")), 1)

    def test_blockquote_and_code_block(self):
        doc = html_to_prosemirror_json("<blockquote><p>q</p></blockquote><pre>x</pre>")
        self.assertEqual(len(find_nodes(doc, "blockquote")), 1)
        self.assertEqual(len(find_nodes(doc, "code_block")), 1)

    def test_every_node_and_mark_in_a_document_is_known_to_the_schema(self):
        html = AssignmentProcessingService.format_assignment_standard_html(
            {
                "title": "<h1>T</h1>",
                "instructions": "<p>I</p>",
                "total_points": 10,
                "due_date": "2026-01-01T00:00:00Z",
                "questions": [
                    {
                        "question_number": 1,
                        "points": 5,
                        "question_text": "<p>Q<sup>1</sup></p>",
                        "question_type": "OBJECTIVE",
                        "options": ["<p>A</p>", "<p>B</p>"],
                        "question_image": "https://cdn.test/i.png",
                        "rubric": [{"level": "good", "points": 5, "description": "d"}],
                        "model_answer": "<p>A</p>",
                    }
                ],
            }
        )
        doc = html_to_prosemirror_json(html)

        def check(node):
            node_type = node.get("type")
            if node_type != "text":
                self.assertIn(node_type, PROSEMIRROR_SCHEMA.nodes, node_type)
            for mark in node.get("marks", []):
                self.assertIn(mark["type"], PROSEMIRROR_SCHEMA.marks, mark["type"])
            for child in node.get("content", []):
                check(child)

        check(doc)
        self.assertEqual(len(find_nodes(doc, "image")), 1)


class AiHtmlAllowlistTest(SimpleTestCase):
    """
    sanitize_ai_html is the allowlist for AI-authored *fragments*, and shares
    raw-text-element stripping with the converter.
    """

    def test_script_source_is_not_left_behind_as_prose(self):
        cleaned = AssignmentProcessingService.sanitize_ai_html(
            "<p>a</p><script>alert(1)</script>"
        )
        self.assertNotIn("alert", cleaned)
        self.assertIn("<p>a</p>", cleaned)

    def test_style_source_is_not_left_behind_as_prose(self):
        cleaned = AssignmentProcessingService.sanitize_ai_html(
            "<style>body{color:red}</style><p>a</p>"
        )
        self.assertNotIn("color:red", cleaned)

    def test_ordinary_formatting_is_untouched(self):
        cleaned = AssignmentProcessingService.sanitize_ai_html(
            "<p>H<sub>2</sub>O <strong>bold</strong></p>"
        )
        self.assertIn("<sub>2</sub>", cleaned)
        self.assertIn("<strong>bold</strong>", cleaned)

    def test_math_block_class_is_still_allowed(self):
        cleaned = AssignmentProcessingService.sanitize_ai_html(
            '<span class="math-block">x</span>'
        )
        self.assertIn('class="math-block"', cleaned)

    def test_non_strings_pass_through_unchanged(self):
        for value in (None, 42, [], {}):
            with self.subTest(value=value):
                self.assertEqual(
                    AssignmentProcessingService.sanitize_ai_html(value), value
                )

    def test_control_characters_are_removed(self):
        self.assertEqual(
            AssignmentProcessingService.clean_xml_text("a\x00b\x1fc"), "abc"
        )


class ConversionCacheTest(SimpleTestCase):
    """
    html_to_prosemirror_text memoises, because AssignmentSerializer regenerates
    the student-facing document on every read.
    """

    def setUp(self):
        _cached_prosemirror_text.cache_clear()

    def test_identical_html_is_converted_once(self):
        html = "<h2>Cached</h2><p>body</p>"

        first = html_to_prosemirror_text(html)
        second = html_to_prosemirror_text(html)

        self.assertEqual(first, second)
        self.assertEqual(_cached_prosemirror_text.cache_info().hits, 1)
        self.assertEqual(_cached_prosemirror_text.cache_info().misses, 1)

    def test_different_html_is_not_confused(self):
        a = html_to_prosemirror_text("<h2>A</h2>")
        b = html_to_prosemirror_text("<h2>B</h2>")

        self.assertNotEqual(a, b)
        self.assertIn("A", a)
        self.assertIn("B", b)
        self.assertEqual(_cached_prosemirror_text.cache_info().misses, 2)

    def test_edited_content_produces_a_different_document(self):
        """No invalidation needed: changed content is a different cache key."""
        before = html_to_prosemirror_text("<p>Question one</p>")
        after = html_to_prosemirror_text("<p>Question one, revised</p>")

        self.assertNotEqual(before, after)
        self.assertIn("revised", after)

    def test_the_cache_is_bounded(self):
        for i in range(CONVERSION_CACHE_SIZE + 10):
            html_to_prosemirror_text(f"<p>doc {i}</p>")

        self.assertLessEqual(
            _cached_prosemirror_text.cache_info().currsize, CONVERSION_CACHE_SIZE
        )

    def test_invalid_input_raises_value_error_not_a_cache_type_error(self):
        for value in ({}, [], None, "", "   "):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    html_to_prosemirror_text(value)  # type: ignore[arg-type]

    def test_a_cached_result_is_an_immutable_string(self):
        """
        Guards the reason the *string* form is cached rather than the dict:
        callers cannot mutate a shared cached document.
        """
        html = "<p>shared</p>"
        self.assertIsInstance(html_to_prosemirror_text(html), str)
        self.assertEqual(
            json.loads(html_to_prosemirror_text(html)),
            html_to_prosemirror_json(html),
        )
