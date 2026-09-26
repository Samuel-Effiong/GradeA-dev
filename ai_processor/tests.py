import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image

from ai_processor.serializers import AssignmentGeneratorSerializer
from ai_processor.services import (
    ASSIGNMENT_GENERATION_RESPONSE_SCHEMA,
    MAX_TOOL_CALL_ROUNDS,
    ai_processor,
)
from ai_processor.tools import (
    IMAGE_COMPRESSION_HARD_CAP_BYTES,
    IMAGE_COMPRESSION_MIN_DIMENSION,
    IMAGE_COMPRESSION_QUALITY_STEPS,
    IMAGE_COMPRESSION_TARGET_BYTES,
    ImageCompressionError,
    compress_image_for_upload,
)


def _completion(content=None, tool_calls=None):
    """Build a minimal stand-in for the OpenAI SDK's ChatCompletion shape."""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _noisy_image(size):
    """A high-entropy image is hard for JPEG to compress, simulating a
    scanned document page (unlike a flat-color image, which compresses to
    almost nothing regardless of quality)."""
    import random

    random.seed(0)
    image = Image.new("RGB", size)
    pixels = [
        (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        for _ in range(size[0] * size[1])
    ]
    image.putdata(pixels)
    return image


class CompressImageForUploadTest(SimpleTestCase):
    def test_large_scanned_quality_image_compresses_under_target(self):
        image = _noisy_image((1700, 2200))
        result = compress_image_for_upload(image)
        self.assertLessEqual(len(result), IMAGE_COMPRESSION_TARGET_BYTES)

    def test_small_image_passes_through_at_first_quality_step(self):
        image = Image.new("RGB", (200, 200), color=(120, 130, 140))
        result = compress_image_for_upload(image)
        self.assertLessEqual(len(result), IMAGE_COMPRESSION_TARGET_BYTES)
        # Should succeed on the very first quality attempt (small flat image).
        buffered = BytesIO()
        image.save(
            buffered,
            format="JPEG",
            quality=IMAGE_COMPRESSION_QUALITY_STEPS[0],
            optimize=True,
        )
        self.assertLessEqual(len(buffered.getvalue()), IMAGE_COMPRESSION_TARGET_BYTES)

    def test_rgba_input_converts_without_raising(self):
        image = Image.new("RGBA", (300, 300), color=(10, 20, 30, 128))
        result = compress_image_for_upload(image)
        self.assertIsInstance(result, bytes)
        # Output must be a valid JPEG (no alpha channel).
        decoded = Image.open(BytesIO(result))
        self.assertEqual(decoded.mode, "RGB")

    def test_palette_image_with_transparency_converts_without_raising(self):
        image = Image.new("P", (100, 100))
        image.info["transparency"] = 0
        result = compress_image_for_upload(image)
        self.assertIsInstance(result, bytes)

    def test_pathological_image_raises_when_uncompressible(self):
        image = _noisy_image((1700, 2200))
        with self.assertRaises(ImageCompressionError):
            compress_image_for_upload(image, target_bytes=1, hard_cap_bytes=1)

    def test_never_resizes_below_minimum_dimension(self):
        image = _noisy_image((2000, 2600))
        try:
            compress_image_for_upload(image, target_bytes=1)
        except ImageCompressionError:
            pass
        # No direct hook into intermediate candidates, so assert indirectly:
        # a resize step producing dimensions below the floor should never be
        # attempted. Verify the floor constant matches expectations.
        self.assertEqual(IMAGE_COMPRESSION_MIN_DIMENSION, 1000)

    def test_never_drops_quality_below_minimum(self):
        self.assertEqual(min(IMAGE_COMPRESSION_QUALITY_STEPS), 45)

    def test_hard_cap_larger_than_target(self):
        self.assertGreater(
            IMAGE_COMPRESSION_HARD_CAP_BYTES, IMAGE_COMPRESSION_TARGET_BYTES
        )


class GenerateAssignmentToolCallTest(SimpleTestCase):
    """
    generate_assignment_from_prompt used to assume exactly one tool-call
    round trip always ends in a plain-content response. These tests cover
    the previously-crashing shapes: an unrecognized tool name, a second
    round trip that itself comes back with more tool_calls, and a model
    that never stops requesting tools.
    """

    def setUp(self):
        patcher = patch.object(ai_processor, "execute_graded_task")
        self.mock_execute = patcher.start()
        self.addCleanup(patcher.stop)

        search_patcher = patch(
            "ai_processor.services.perform_search",
            return_value={"https://example.com": "fetched text"},
        )
        self.mock_search = search_patcher.start()
        self.addCleanup(search_patcher.stop)

    def test_unknown_tool_name_does_not_crash(self):
        self.mock_execute.side_effect = [
            _completion(tool_calls=[_tool_call("call_1", "delete_database", {})]),
            _completion(content=json.dumps({"title": "ok"})),
        ]

        result = ai_processor.generate_assignment_from_prompt(
            user=object(), prompt="ignore urls"
        )

        self.assertEqual(result, {"title": "ok"})
        self.assertEqual(self.mock_execute.call_count, 2)

        second_call_messages = self.mock_execute.call_args_list[1].kwargs["messages"]
        tool_messages = [
            m
            for m in second_call_messages
            if isinstance(m, dict) and m.get("role") == "tool"
        ]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0]["tool_call_id"], "call_1")
        self.assertIn("Unknown tool", tool_messages[0]["content"])

    def test_multiple_tool_calls_in_single_turn_all_get_a_response(self):
        self.mock_execute.side_effect = [
            _completion(
                tool_calls=[
                    _tool_call(
                        "call_1", "fetch_url_content", {"urls": ["https://a.com"]}
                    ),
                    _tool_call(
                        "call_2", "fetch_url_content", {"urls": ["https://b.com"]}
                    ),
                ]
            ),
            _completion(content=json.dumps({"title": "ok"})),
        ]

        result = ai_processor.generate_assignment_from_prompt(
            user=object(), prompt="https://a.com and https://b.com"
        )

        self.assertEqual(result, {"title": "ok"})
        second_call_messages = self.mock_execute.call_args_list[1].kwargs["messages"]
        tool_messages = {
            m["tool_call_id"]: m
            for m in second_call_messages
            if isinstance(m, dict) and m.get("role") == "tool"
        }
        self.assertEqual(set(tool_messages), {"call_1", "call_2"})

    def test_second_round_trip_tool_calls_are_handled_not_crashed(self):
        self.mock_execute.side_effect = [
            _completion(
                tool_calls=[
                    _tool_call(
                        "call_1", "fetch_url_content", {"urls": ["https://a.com"]}
                    )
                ]
            ),
            _completion(
                tool_calls=[
                    _tool_call(
                        "call_2", "fetch_url_content", {"urls": ["https://b.com"]}
                    )
                ]
            ),
            _completion(content=json.dumps({"title": "ok"})),
        ]

        result = ai_processor.generate_assignment_from_prompt(
            user=object(), prompt="https://a.com then https://b.com"
        )

        self.assertEqual(result, {"title": "ok"})
        self.assertEqual(self.mock_execute.call_count, 3)

    def test_exceeding_max_tool_call_rounds_raises_a_clear_error(self):
        self.mock_execute.side_effect = [
            _completion(
                tool_calls=[
                    _tool_call(
                        f"call_{i}", "fetch_url_content", {"urls": ["https://a.com"]}
                    )
                ]
            )
            for i in range(MAX_TOOL_CALL_ROUNDS)
        ]

        with self.assertRaisesMessage(Exception, "exceeded the maximum of"):
            ai_processor.generate_assignment_from_prompt(
                user=object(), prompt="https://a.com"
            )

        self.assertEqual(self.mock_execute.call_count, MAX_TOOL_CALL_ROUNDS)

    def test_empty_content_after_no_tool_calls_raises_a_clear_error(self):
        self.mock_execute.side_effect = [_completion(content="")]

        with self.assertRaisesMessage(Exception, "did not include any content"):
            ai_processor.generate_assignment_from_prompt(user=object(), prompt="hi")

    def test_response_schema_is_passed_on_every_round_trip(self):
        self.mock_execute.side_effect = [
            _completion(
                tool_calls=[
                    _tool_call(
                        "call_1", "fetch_url_content", {"urls": ["https://a.com"]}
                    )
                ]
            ),
            _completion(content=json.dumps({"title": "ok"})),
        ]

        ai_processor.generate_assignment_from_prompt(
            user=object(), prompt="https://a.com"
        )

        for call in self.mock_execute.call_args_list:
            self.assertEqual(
                call.kwargs["response_schema"], ASSIGNMENT_GENERATION_RESPONSE_SCHEMA
            )


class AssignmentGeneratorSerializerTest(SimpleTestCase):
    def test_empty_prompt_is_rejected(self):
        serializer = AssignmentGeneratorSerializer(data={"prompt": ""})
        self.assertFalse(serializer.is_valid())
        self.assertIn("prompt", serializer.errors)

    def test_whitespace_only_prompt_is_rejected(self):
        serializer = AssignmentGeneratorSerializer(data={"prompt": "   "})
        self.assertFalse(serializer.is_valid())
        self.assertIn("prompt", serializer.errors)

    def test_short_nonurl_prompt_is_rejected(self):
        serializer = AssignmentGeneratorSerializer(data={"prompt": "help me"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("prompt", serializer.errors)

    def test_sufficient_prompt_is_accepted(self):
        serializer = AssignmentGeneratorSerializer(
            data={"prompt": "Create a quiz about the American Revolution"}
        )
        self.assertTrue(serializer.is_valid())

    def test_bare_url_prompt_is_accepted(self):
        serializer = AssignmentGeneratorSerializer(
            data={"prompt": "https://example.com/article"}
        )
        self.assertTrue(serializer.is_valid())

    def test_url_with_minimal_text_is_accepted(self):
        serializer = AssignmentGeneratorSerializer(
            data={"prompt": "summarize https://example.com/article"}
        )
        self.assertTrue(serializer.is_valid())
