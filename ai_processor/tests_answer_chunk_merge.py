"""The chunked answer merge, and the error handling around extraction.

Found by the answer-extraction benchmark (ai_processor/benchmark/answers)
and pinned here at unit level, where a failure names the rule rather than
a scenario.

1. A BLANK on a page after the first chunk came out NOT_FOUND_IN_DOCUMENT
   (AE-600, AE-601). Chunk 1 reports every question it cannot see as not
   found; the merge only upgraded an entry whose answer_html was empty to
   one whose answer_html was not, and a BLANK is empty too - so the chunk
   that never saw the page beat the chunk that did.

2. The fix could not simply rank BLANK above NOT_FOUND. The last chunk's
   note tells the model to "mark any question that was not found in any
   chunk as genuinely skipped", which a model can satisfy by writing BLANK
   for a question it never saw. Trusting that turns an absent answer into
   a silent zero. So an empty verdict only counts when source_page says
   where the question was found (AE-800, AE-801, AE-802).

3. An answer written across the chunk seam kept only its first half
   (AE-205). The second chunk's transcription was discarded because an
   answer "had already been found".

4. From chunk 2 on, the note listed EVERY question as "already extracted"
   and "does NOT need to be extracted again" - because chunk 1 returns an
   entry, mostly not-found, for every question (AE-804).

5. The single-call path wrapped InsufficientCreditsError,
   AIFeatureNotAvailableError and TaskCancelledError in a bare Exception.
   An out-of-credits teacher was retried three times and then shown a
   generic failure; the chunked path already raised them intact.

6. Exhausted retries raised a fresh Exception with no cause, so
   classify_infra_error could not tell a timeout from anything else.

7. Even with the merge joining split answers (3), the note told later chunks
   an already-found question "does NOT need to be extracted again". The
   real model obeyed and dropped the continuation (live AE-905). The note
   now asks for it. Requirement: a student's answer must never be silently
   truncated because it crosses a chunk boundary.
"""

import copy
import json
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from ai_processor import services
from ai_processor.benchmark.answers.provider import _Response, already_found_in
from ai_processor.extraction_schemas import (
    ANSWERED,
    BLANK,
    ILLEGIBLE,
    NOT_FOUND_IN_DOCUMENT,
)
from ai_processor.services import (
    _canonical_question_key,
    _declared_question_labels,
    _located_in_chunk,
    _merge_chunk_answer,
    _merge_rank,
    _relabel_answer,
)
from AutoGrader.error_messages import classify_infra_error, describe_user_error
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import InsufficientCreditsError
from students.exceptions import TaskCancelledError


def entry(status, html="", source_page=None, notes="", number="Q1"):
    return {
        "question_number": number,
        "question_text": "text",
        "source_page": source_page,
        "transcription_notes": notes,
        "answer_html": html,
        "answer_status": status,
        "confidence": 90,
    }


def merge(existing, existing_origin, new, new_origin):
    kept, origin = _merge_chunk_answer(existing, existing_origin, new, new_origin)
    return kept, origin


class LocatedInChunkTest(SimpleTestCase):
    def test_absolute_page_inside_the_chunk(self):
        self.assertTrue(_located_in_chunk(5, 4, 6, 3))

    def test_page_counted_from_one_within_the_chunk(self):
        self.assertTrue(_located_in_chunk(2, 4, 6, 3))

    def test_page_outside_both_conventions(self):
        self.assertFalse(_located_in_chunk(9, 4, 6, 3))

    def test_null_means_never_located(self):
        self.assertFalse(_located_in_chunk(None, 4, 6, 3))

    def test_non_integers_are_not_a_location(self):
        for value in ("5", 5.0, True, [5]):
            with self.subTest(value=value):
                self.assertFalse(_located_in_chunk(value, 4, 6, 3))


class MergeRankTest(SimpleTestCase):
    def test_answered_outranks_everything_and_needs_no_location(self):
        self.assertEqual(_merge_rank(entry(ANSWERED, "<p>x</p>"), 4, 6, 3), 3)

    def test_located_blank_and_illegible_carry_information(self):
        self.assertEqual(_merge_rank(entry(BLANK, source_page=5), 4, 6, 3), 2)
        self.assertEqual(_merge_rank(entry(ILLEGIBLE, "?", source_page=5), 4, 6, 3), 2)

    def test_an_empty_verdict_nobody_can_place_is_no_information(self):
        self.assertEqual(_merge_rank(entry(BLANK), 4, 6, 3), 1)
        self.assertEqual(_merge_rank(entry(ILLEGIBLE, "?"), 4, 6, 3), 1)

    def test_not_found_and_unknown_statuses_are_no_information(self):
        self.assertEqual(_merge_rank(entry(NOT_FOUND_IN_DOCUMENT), 1, 3, 3), 1)
        self.assertEqual(_merge_rank(entry("MAYBE"), 1, 3, 3), 1)

    def test_non_dict_ranks_below_everything(self):
        self.assertEqual(_merge_rank(None, 1, 3, 3), 0)


class QuestionLabelTest(SimpleTestCase):
    """
    The live model wrote `1` in one chunk and `Q1` in the next for the same
    question. Treated as two questions, that cost a full re-read of the
    script up to three times (AE-912, AE-921).
    """

    def test_every_spelling_of_a_number_is_one_key(self):
        for value in (3, "3", " 3 ", "Q3", "q3", "Q 3", "Q.3", "Question 3"):
            with self.subTest(value=value):
                self.assertEqual(_canonical_question_key(value), 3)

    def test_any_other_label_is_kept_exactly(self):
        for value in ("1(a)", "Q1(a)", "Part B", "3b"):
            with self.subTest(value=value):
                self.assertEqual(_canonical_question_key(value), value)

    def test_an_answer_takes_the_assignments_own_label(self):
        q_numbered = _declared_question_labels([{"question_number": "Q1"}])
        self.assertEqual(
            _relabel_answer({"question_number": "1"}, q_numbered)["question_number"],
            "Q1",
        )
        int_numbered = _declared_question_labels([{"question_number": 1}])
        self.assertEqual(
            _relabel_answer({"question_number": "Q1"}, int_numbered)["question_number"],
            1,
        )

    def test_an_assignment_numbering_both_1_and_q1_is_never_merged(self):
        labels = _declared_question_labels(
            [{"question_number": 1}, {"question_number": "Q1"}]
        )
        self.assertNotIn(1, labels)
        answer = {"question_number": "Q1"}
        self.assertIs(_relabel_answer(answer, labels), answer)

    def test_unknown_questions_and_non_dicts_pass_through(self):
        labels = _declared_question_labels([{"question_number": "Q1"}])
        stray = {"question_number": "Q9"}
        self.assertIs(_relabel_answer(stray, labels), stray)
        self.assertIsNone(_relabel_answer(None, labels))

    def test_the_input_answer_is_not_mutated(self):
        labels = _declared_question_labels([{"question_number": "Q1"}])
        answer = {"question_number": "1", "answer_html": "<p>x</p>"}
        _relabel_answer(answer, labels)
        self.assertEqual(answer["question_number"], "1")


class MergeChunkAnswerTest(SimpleTestCase):
    def test_a_located_blank_in_a_later_chunk_beats_not_found(self):
        """AE-600/601: the chunk that held the page wins."""
        kept, _ = merge(
            entry(NOT_FOUND_IN_DOCUMENT), (0, 1), entry(BLANK, source_page=4), (1, 2)
        )
        self.assertEqual(kept["answer_status"], BLANK)

    def test_not_found_in_a_later_chunk_does_not_erase_a_located_blank(self):
        kept, _ = merge(
            entry(BLANK, source_page=3), (0, 2), entry(NOT_FOUND_IN_DOCUMENT), (1, 1)
        )
        self.assertEqual(kept["answer_status"], BLANK)

    def test_an_unplaced_blank_never_beats_not_found_in_either_order(self):
        """AE-800/802: the result must not depend on which chunk came first."""
        for first, second in (
            ((entry(BLANK), (0, 1)), (entry(NOT_FOUND_IN_DOCUMENT), (1, 1))),
            ((entry(NOT_FOUND_IN_DOCUMENT), (0, 1)), (entry(BLANK), (1, 1))),
        ):
            with self.subTest(first=first[0]["answer_status"]):
                kept, _ = merge(first[0], first[1], second[0], second[1])
                self.assertEqual(kept["answer_status"], NOT_FOUND_IN_DOCUMENT)

    def test_an_answer_beats_a_located_blank(self):
        kept, _ = merge(
            entry(BLANK, source_page=3), (0, 2), entry(ANSWERED, "<p>W</p>"), (1, 3)
        )
        self.assertEqual(kept["answer_status"], ANSWERED)

    def test_an_answer_split_across_chunks_keeps_both_halves_in_page_order(self):
        """AE-205."""
        kept, _ = merge(
            entry(ANSWERED, "<p>The major component is</p>"),
            (0, 3),
            entry(ANSWERED, "<p>Nitrogen</p>"),
            (1, 3),
        )
        self.assertEqual(
            kept["answer_html"], "<p>The major component is</p>\n<p>Nitrogen</p>"
        )
        self.assertEqual(kept["answer_status"], ANSWERED)
        self.assertIn("page-chunk boundary", kept["transcription_notes"])

    def test_a_half_already_contained_is_not_repeated(self):
        kept, _ = merge(
            entry(ANSWERED, "<p>A</p>\n<p>B</p>"),
            (0, 3),
            entry(ANSWERED, "<p>B</p>"),
            (1, 3),
        )
        self.assertEqual(kept["answer_html"], "<p>A</p>\n<p>B</p>")

    def test_an_illegible_half_keeps_the_joined_answer_under_review(self):
        kept, _ = merge(
            entry(ILLEGIBLE, "<p>?rd</p>", source_page=3),
            (0, 2),
            entry(ANSWERED, "<p>Nitrogen</p>"),
            (1, 3),
        )
        self.assertEqual(kept["answer_status"], ILLEGIBLE)
        self.assertIn("Nitrogen", kept["answer_html"])

    def test_duplicates_inside_one_chunk_are_not_joined(self):
        kept, _ = merge(
            entry(ANSWERED, "<p>A</p>"), (0, 3), entry(ANSWERED, "<p>B</p>"), (0, 3)
        )
        self.assertEqual(kept["answer_html"], "<p>A</p>")

    def test_inputs_are_not_mutated(self):
        existing = entry(ANSWERED, "<p>A</p>", notes="first")
        new = entry(ANSWERED, "<p>B</p>", notes="second")
        before = (copy.deepcopy(existing), copy.deepcopy(new))
        merge(existing, (0, 3), new, (1, 3))
        self.assertEqual((existing, new), before)


def page_blocks(count):
    return [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,P{index}#BENCHPAGE{index}"},
            "bytes": f"BENCHPAGE{index}",
        }
        for index in range(1, count + 1)
    ]


def payload(answers):
    return _Response(
        json.dumps(
            {
                "student_name": "",
                "student_name_raw": None,
                "student_id": "",
                "answers": answers,
                "extraction_confidence": 90,
                "feedback": "",
            }
        )
    )


def new_processor():
    return services.AIProcessor.__new__(services.AIProcessor)


class AlreadyFoundNoteTest(SimpleTestCase):
    """AE-804: the note must not tell later chunks to skip their own pages."""

    def test_only_questions_with_a_transcription_are_listed(self):
        notes = []

        def provider(**kwargs):
            for message in kwargs["messages"]:
                if isinstance(message["content"], list):
                    notes.append(
                        " ".join(
                            block.get("text", "")
                            for block in message["content"]
                            if block.get("type") == "text"
                        )
                    )
            return payload(
                [
                    entry(ANSWERED, "<p>Reykjavik</p>", source_page=1, number="Q1"),
                    entry(BLANK, source_page=2, number="Q2"),
                    entry(NOT_FOUND_IN_DOCUMENT, number="Q3"),
                ]
            )

        processor = new_processor()
        with patch.object(processor, "execute_graded_task", provider), patch.object(
            processor, "_verify_blank_answers", lambda *a, **k: a[2]
        ):
            processor._extract_answers_chunked(None, page_blocks(4), "[]")

        self.assertEqual(len(notes), 2)
        self.assertEqual(already_found_in(notes[1]), frozenset({"Q1"}))

    def test_the_note_asks_for_the_continuation_of_an_already_found_answer(self):
        """
        The wording that dropped a split answer's second half must not come
        back, and the note must ask for the continuation instead.
        """
        note = new_processor()._build_answer_chunk_note(
            chunk_index=1,
            total_chunks=2,
            page_start=4,
            page_end=6,
            total_pages=6,
            already_found_question_numbers=["Q3", "Q1"],
        )
        self.assertIn("CONTINUES on these pages", note)
        self.assertIn("same question_number", note)
        self.assertNotIn("do NOT need to be extracted again", note)
        self.assertNotIn("QQ", note)
        self.assertEqual(already_found_in(note), frozenset({"Q1", "Q3"}))

    def test_the_first_chunk_lists_nothing_and_asks_for_no_continuation(self):
        note = new_processor()._build_answer_chunk_note(
            chunk_index=0,
            total_chunks=2,
            page_start=1,
            page_end=3,
            total_pages=6,
            already_found_question_numbers=[],
        )
        self.assertEqual(already_found_in(note), frozenset())
        self.assertNotIn("CONTINUES on these pages", note)


class ErrorsKeepTheirTypeTest(SimpleTestCase):
    """Regressions 7 and 8, on both paths."""

    PATHS = (("single-call", 1), ("chunked", 3))

    def _run(self, pages, failure):
        calls = []

        def provider(**kwargs):
            calls.append(kwargs)
            raise failure

        processor = new_processor()
        with patch.object(processor, "execute_graded_task", provider):
            try:
                processor.extract_answer_with_retry(
                    None, page_blocks(pages), "[]", max_retries=3
                )
            except BaseException as exc:  # noqa: B036 - asserted below
                return exc, len(calls)
        self.fail("extraction returned instead of raising")

    def test_credit_and_access_errors_are_raised_intact_after_one_call(self):
        for label, pages in self.PATHS:
            for failure in (
                InsufficientCreditsError("Refill your wallet to continue"),
                AIFeatureNotAvailableError("AI access denied: plan"),
            ):
                with self.subTest(path=label, error=type(failure).__name__):
                    exc, calls = self._run(pages, failure)
                    self.assertIs(type(exc), type(failure))
                    self.assertEqual(calls, 1)
                    self.assertEqual(describe_user_error(exc), str(failure))

    def test_a_cancellation_is_never_retried(self):
        for label, pages in self.PATHS:
            with self.subTest(path=label):
                exc, calls = self._run(
                    pages, TaskCancelledError("Task cancelled by user.")
                )
                self.assertIsInstance(exc, TaskCancelledError)
                self.assertEqual(calls, 1)

    def test_exhausted_retries_keep_the_real_failure_as_the_cause(self):
        for label, pages in self.PATHS:
            with self.subTest(path=label):
                exc, _ = self._run(
                    pages, requests.exceptions.Timeout("provider timed out")
                )
                self.assertIn("attempts failed", str(exc))
                self.assertIsNotNone(exc.__cause__)
                self.assertIsNotNone(classify_infra_error(exc))
                self.assertIn("timed out", classify_infra_error(exc))
