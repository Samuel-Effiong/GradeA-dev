"""
Recording-key tests for the benchmark tape (ai_processor/benchmark/runner.py).

WHY THIS FILE EXISTS

`request_key` is the identity function of the whole record/replay harness: two
calls that hash to the same key are, as far as replay is concerned, the same
call. A key that is too COARSE is the dangerous direction — it does not error,
it silently serves one subject's recorded response for a different subject, and
every number computed downstream is then confidently wrong.

That was a live defect for image prompts. `_prompt_text` reduced any content
part with no "text" key to `item.get("type")`, so every image in the codebase
hashed to the literal string "image_url". Grading survived it by luck
(_question_image_content_blocks emits a "Image for question N:" text part
before each image, so neighbouring text still separated the prompts). Answer
extraction would not have: a submission is a bare list of page images plus an
assignment-context string that is byte-identical for every student sitting the
same assignment, so two students would have collapsed onto one key.

These tests pin both directions: different inputs must not collide, and
text-only prompts must keep the keys they already have on disk.

Run with:
    python manage.py test ai_processor.tests_benchmark_request_key
"""

import hashlib

from django.test import SimpleTestCase

from ai_processor.benchmark.runner import _content_part_text, _prompt_text, request_key


def _image(url):
    return {"type": "image_url", "image_url": {"url": url}}


def _text(text):
    return {"type": "text", "text": text}


class PromptFlatteningTest(SimpleTestCase):
    """_prompt_text / _content_part_text reduction rules."""

    def test_none_flattens_to_empty_string(self):
        self.assertEqual(_prompt_text(None), "")

    def test_plain_string_passes_through(self):
        self.assertEqual(_prompt_text("grade this"), "grade this")

    def test_text_parts_contribute_their_text(self):
        self.assertEqual(_prompt_text([_text("a"), _text("b")]), "a\nb")

    def test_image_part_contributes_a_digest_of_its_url(self):
        url = "data:image/jpeg;base64,AAAA"
        expected = hashlib.sha256(url.encode("utf-8")).hexdigest()
        self.assertEqual(_content_part_text(_image(url)), f"image_url:{expected}")

    def test_image_part_no_longer_reduces_to_its_bare_type(self):
        # The exact defect: "image_url" as the entire contribution.
        self.assertNotEqual(_content_part_text(_image("http://x/a.png")), "image_url")

    def test_unknown_part_type_falls_back_to_its_type(self):
        self.assertEqual(_content_part_text({"type": "input_audio"}), "input_audio")

    def test_part_with_neither_text_nor_type_is_empty(self):
        self.assertEqual(_content_part_text({}), "")

    def test_non_dict_part_is_stringified(self):
        self.assertEqual(_content_part_text(42), "42")

    def test_non_list_non_string_prompt_is_stringified(self):
        self.assertEqual(_prompt_text(7), "7")


class ImageUrlShapeTest(SimpleTestCase):
    """Defensive handling of the image_url payload's own shape."""

    def test_missing_image_url_key_does_not_raise(self):
        self.assertEqual(
            _content_part_text({"type": "image_url"}),
            f"image_url:{hashlib.sha256(b'').hexdigest()}",
        )

    def test_image_url_given_as_bare_string_is_tolerated(self):
        # Not a shape the pipeline emits, but a malformed fixture must not
        # crash a benchmark run that is otherwise fine.
        self.assertEqual(
            _content_part_text({"type": "image_url", "image_url": "http://x/a.png"}),
            f"image_url:{hashlib.sha256(b'http://x/a.png').hexdigest()}",
        )

    def test_extra_keys_alongside_the_url_are_ignored(self):
        # prepare_ai_content attaches a redundant "bytes" key carrying the
        # same base64 payload; it must not perturb the key.
        url = "data:image/jpeg;base64,ZZZZ"
        with_extra = {
            "type": "image_url",
            "image_url": {"url": url},
            "bytes": "ZZZZ",
        }
        self.assertEqual(
            _content_part_text(with_extra), _content_part_text(_image(url))
        )


class RequestKeyCollisionTest(SimpleTestCase):
    """The property that actually protects the benchmark's correctness."""

    def test_different_images_under_identical_text_get_different_keys(self):
        # THE REGRESSION. Two students, same assignment: the text part is
        # identical and only the page images differ. Before the fix these
        # produced the same key and replay served one student's answers for
        # the other.
        context = _text("Assignment context: Q1..Q10")
        student_a = [context, _image("data:image/jpeg;base64,STUDENT_A_PAGE")]
        student_b = [context, _image("data:image/jpeg;base64,STUDENT_B_PAGE")]

        self.assertNotEqual(
            request_key("sys", student_a),
            request_key("sys", student_b),
        )

    def test_identical_images_under_identical_text_get_the_same_key(self):
        # Replay depends on this: re-running the same input must hit.
        prompt = [_text("ctx"), _image("data:image/jpeg;base64,SAME")]
        self.assertEqual(request_key("sys", prompt), request_key("sys", list(prompt)))

    def test_page_order_changes_the_key(self):
        # A submission whose pages arrive reordered is a different input;
        # silently reusing the first ordering's recording would hide it.
        a = _image("data:image/jpeg;base64,PAGE1")
        b = _image("data:image/jpeg;base64,PAGE2")
        self.assertNotEqual(request_key("sys", [a, b]), request_key("sys", [b, a]))

    def test_page_count_changes_the_key(self):
        page = _image("data:image/jpeg;base64,PAGE1")
        self.assertNotEqual(
            request_key("sys", [page]), request_key("sys", [page, page])
        )

    def test_system_prompt_change_changes_the_key(self):
        prompt = [_text("ctx")]
        self.assertNotEqual(
            request_key("sys-v1", prompt), request_key("sys-v2", prompt)
        )

    def test_override_model_change_changes_the_key(self):
        # The second-opinion pass reuses the same prompt with a different
        # model; the override has to stay part of the identity.
        prompt = [_text("ctx")]
        self.assertNotEqual(
            request_key("sys", prompt, override_model=None),
            request_key("sys", prompt, override_model="deepseek/deepseek-v4-pro"),
        )

    def test_text_and_image_parts_cannot_impersonate_each_other(self):
        # A text part whose literal content is the digest string must not
        # collide with the image that produces that digest.
        url = "http://x/a.png"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        self.assertNotEqual(
            request_key("sys", [_image(url)]),
            request_key("sys", [_text(f"image_url:{digest}"), _text("")]),
        )


class RecordingCompatibilityTest(SimpleTestCase):
    """
    The committed grading recordings must keep working.

    Every question in the benchmark dataset has `question_image: ""`, so no
    recorded grading prompt contains an image part, so the text-only path
    below is the only one those recordings ever exercised. Pinning it here
    means a future edit to the flattening rules cannot invalidate
    recordings/responses.json.gz without turning this test red first.
    """

    def test_text_only_prompt_hashes_exactly_as_before_the_image_fix(self):
        # Reproduces the pre-fix algorithm verbatim for text-only input.
        def legacy(prompt):
            if prompt is None:
                return ""
            if isinstance(prompt, str):
                return prompt
            if isinstance(prompt, list):
                parts = []
                for item in prompt:
                    if isinstance(item, dict):
                        parts.append(item.get("text") or item.get("type") or "")
                    else:
                        parts.append(str(item))
                return "\n".join(parts)
            return str(prompt)

        for prompt in (
            None,
            "a plain system prompt",
            [_text("### Questions and Rubrics"), _text("### Student Answers")],
            [{"type": "text", "text": ""}],
            ["a bare string part"],
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(_prompt_text(prompt), legacy(prompt))

    def test_dataset_carries_no_question_images(self):
        # The premise of the compatibility argument above. If a future
        # dataset adds a question image, the recordings must be re-recorded
        # and this test is where that is announced.
        from ai_processor.benchmark.dataset import ASSIGNMENTS

        for assignment in ASSIGNMENTS:
            for question in assignment.as_assignment_questions():
                with self.subTest(
                    assignment=assignment.key, q=question.get("question_number")
                ):
                    self.assertEqual(question.get("question_image", ""), "")
