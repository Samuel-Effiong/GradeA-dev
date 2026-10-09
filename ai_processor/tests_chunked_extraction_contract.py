"""The chunked extraction paths must honour the same contracts as the
single-call ones.

Chunking is not a variant of extraction - it is where the hardest real
documents go. A submission only chunks once it is several pages long, i.e.
exactly the multi-page handwritten scripts where an answer is most likely
to be mis-read or dropped. Both defects pinned here were in that path, and
both were invisible because the path had no tests: `services.py` sat at
59.1% coverage with lines ~706-1032 (assignment chunking) and ~1295-1507
(answer chunking) entirely uncovered.

WHAT IS PINNED

1. The chunked ANSWER path sends the structured-output schema.

   The single-call path passed `response_schema=self._answer_extraction_schema()`;
   the chunked path passed nothing. That schema is what makes
   `answer_status` mandatory, and extraction_schemas.py exists to make
   "the student wrote nothing" distinguishable from "we did not find the
   student's work". Without it `_stamp_answer_provenance` falls back to
   `infer_answer_status()`, which returns BLANK and NEVER
   NOT_FOUND_IN_DOCUMENT - so a LOST answer is scored zero and never
   reaches the review queue.

   The kill switch (ANSWER_EXTRACTION_SCHEMA_ENABLED) logs loudly when it
   turns the schema off, precisely because "a safety check that can be
   disabled without leaving a trace is one that stops existing". The
   chunked path disabled it silently, with the switch still on.

2. The chunked ASSIGNMENT path tells the model the truth about which
   pages it is looking at.

   It split the document on CHUNK_SIZE (2) while the note and the log
   line both reported `pages_per_chunk` (3 from the caller). So the model
   was told "you are processing pages 1 to 3" while being handed 2 pages,
   and the claimed and actual ranges drifted further apart with every
   chunk. The note is the model's only statement of where it is in the
   document; a wrong one invites it to look for questions that are not
   there and to mis-number the ones that are.
"""

import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from ai_processor.extraction_schemas import ANSWER_EXTRACTION_RESPONSE_SCHEMA
from ai_processor.services import ANSWERS_EXTRACTION_PAGES_PER_CHUNK, AIProcessor


def image_pages(count):
    """`count` image content blocks, shaped as the pipeline builds them."""
    return [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,PAGE{index}"},
            "bytes": f"PAGE{index}",
        }
        for index in range(1, count + 1)
    ]


class _FakeMessage:
    __slots__ = ("content",)

    def __init__(self, content):
        self.content = content


class _FakeChoice:
    __slots__ = ("message",)

    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    """Minimal stand-in for the OpenAI SDK response object."""

    def __init__(self, payload):
        self.choices = [_FakeChoice(json.dumps(payload))]
        self.model = "test/model"


class ChunkedAnswerExtractionSendsTheSchemaTest(SimpleTestCase):
    ANSWER_PAYLOAD = {
        "student_name": "A Student",
        "student_name_raw": None,
        "student_id": "",
        "answers": [
            {
                "question_number": 1,
                "question_text": "Q1",
                "source_page": 1,
                "transcription_notes": "",
                "answer_html": "<p>an answer</p>",
                "answer_status": "ANSWERED",
                "confidence": 90,
            }
        ],
        "extraction_confidence": 90,
        "feedback": "",
    }

    def _run_chunked(self):
        """Drive the chunked answer path, capturing every AI call's kwargs."""
        processor = AIProcessor()
        calls = []

        def capture(**kwargs):
            calls.append(kwargs)
            return _FakeResponse(self.ANSWER_PAYLOAD)

        with patch.object(processor, "execute_graded_task", side_effect=capture):
            processor._extract_answers_chunked(
                user=object(),
                # Two full chunks, so this is genuinely the chunked path.
                image_contents=image_pages(ANSWERS_EXTRACTION_PAGES_PER_CHUNK * 2),
                assignment=json.dumps([{"question_number": 1, "question_text": "Q1"}]),
            )
        return calls

    def test_every_chunk_call_carries_the_answer_extraction_schema(self):
        calls = self._run_chunked()

        self.assertGreaterEqual(len(calls), 2, "expected a genuinely chunked run")
        for index, kwargs in enumerate(calls):
            with self.subTest(chunk=index):
                self.assertEqual(
                    kwargs.get("response_schema"),
                    ANSWER_EXTRACTION_RESPONSE_SCHEMA,
                    "a chunked answer-extraction call went out with no "
                    "structured-output schema - answer_status becomes "
                    "optional and a lost answer turns into a silent zero",
                )

    def test_the_schema_requires_answer_status(self):
        """
        The property that makes the assertion above worth making: it is
        `answer_status` specifically that keeps a lost answer separable
        from a blank one.
        """
        answer_schema = ANSWER_EXTRACTION_RESPONSE_SCHEMA["schema"]["properties"][
            "answers"
        ]["items"]

        self.assertIn("answer_status", answer_schema["required"])
        self.assertIn(
            "NOT_FOUND_IN_DOCUMENT",
            answer_schema["properties"]["answer_status"]["enum"],
        )
        self.assertIn("BLANK", answer_schema["properties"]["answer_status"]["enum"])

    @override_settings(ANSWER_EXTRACTION_SCHEMA_ENABLED=False)
    def test_the_documented_kill_switch_still_works_on_the_chunked_path(self):
        """
        Turning the schema off must remain a deliberate, logged act - not
        something the chunked path does on its own. With the switch off
        the call carries no schema, and `_answer_extraction_schema` is the
        thing that logs about it.
        """
        with self.assertLogs("ai_processor.services", level="WARNING") as captured:
            calls = self._run_chunked()

        self.assertTrue(all(c.get("response_schema") is None for c in calls))
        self.assertTrue(
            any("ANSWER_EXTRACTION_SCHEMA_ENABLED" in line for line in captured.output),
            "disabling the schema must leave a trace in the logs",
        )


class ChunkedAssignmentNoteReportsTheRealPagesTest(SimpleTestCase):
    ASSIGNMENT_PAYLOAD = {
        "title": "T",
        "instructions": "",
        "questions": [
            {
                "question_number": 1,
                "question_text": "Q",
                "question_type": "ESSAY",
                "points": 5,
            }
        ],
    }

    def _notes_for(self, page_count, pages_per_chunk):
        """The chunk notes actually sent, paired with the pages sent with them."""
        processor = AIProcessor()
        sent = []

        def capture(**kwargs):
            blocks = kwargs.get("user_prompt") or []
            note = next(
                (b["text"] for b in blocks if b.get("type") == "text"),
                "",
            )
            images = [b for b in blocks if b.get("type") == "image_url"]
            sent.append((note, len(images)))
            return _FakeResponse(self.ASSIGNMENT_PAYLOAD)

        with patch.object(processor, "execute_graded_task", side_effect=capture):
            processor._extract_assignment_chunked(
                user=object(),
                image_contents=image_pages(page_count),
                pages_per_chunk=pages_per_chunk,
            )
        return sent

    def test_the_claimed_page_range_matches_the_pages_actually_sent(self):
        """
        The defect this pins: the note claimed a `pages_per_chunk`-wide
        range while the split used a different constant, so the claim and
        the payload disagreed - and drifted further apart each chunk.
        """
        page_count, pages_per_chunk = 7, 3
        sent = self._notes_for(page_count, pages_per_chunk)

        self.assertEqual(len(sent), 3, "7 pages at 3/chunk is 3 chunks")

        expected_start = 1
        for index, (note, image_count) in enumerate(sent):
            with self.subTest(chunk=index):
                expected_end = expected_start + image_count - 1
                self.assertIn(
                    f"pages {expected_start} to {expected_end}",
                    note,
                    f"chunk {index + 1} was sent {image_count} page(s) but "
                    f"its note does not say pages {expected_start} to "
                    f"{expected_end}",
                )
                self.assertIn(f"{page_count}-page document", note)
                expected_start = expected_end + 1

        # And every page is accounted for exactly once.
        self.assertEqual(sum(count for _, count in sent), page_count)

    def test_a_final_short_chunk_is_described_honestly(self):
        """
        The last chunk is usually smaller. Claiming a full-width range
        there tells the model to look for pages that do not exist.
        """
        sent = self._notes_for(page_count=5, pages_per_chunk=3)

        self.assertEqual([count for _, count in sent], [3, 2])
        self.assertIn("pages 1 to 3", sent[0][0])
        self.assertIn("pages 4 to 5", sent[1][0])
        self.assertNotIn("pages 4 to 6", sent[1][0])

    def test_the_caller_supplied_chunk_size_is_honoured(self):
        """
        `pages_per_chunk` was accepted and then ignored in favour of a
        module constant, so a caller could not actually change it.
        """
        for pages_per_chunk, expected_chunks in ((2, 3), (3, 2), (6, 1)):
            with self.subTest(pages_per_chunk=pages_per_chunk):
                sent = self._notes_for(6, pages_per_chunk)
                self.assertEqual(len(sent), expected_chunks)
                self.assertLessEqual(max(count for _, count in sent), pages_per_chunk)
