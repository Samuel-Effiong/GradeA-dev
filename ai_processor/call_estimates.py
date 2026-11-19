"""The per-call estimate headroom and the upload item estimate (E2E F1,
option C, owner 2026-11-19).

A call's estimate is its counted input (tiktoken text, image tiles, PDF
pages: AIProcessor.estimate_total_token) plus a headroom for what the count
cannot see: the completion, and the gap between the counted input and the
provider's billed input. It used to be a flat 20,000 for every call, which
made holds about 2.4x the real cost (median; 1.5x-21.6x) and stopped batches
the door had let in.

Where the numbers come from: the batch stress run of 2026-11-19 (117 charged
calls on the real provider; each call record's estimate vs total_tokens). Real
completions were 400-2,600 tokens for every task type, so the p95 of each is
at most 2,600. Per task type, avg actual cost vs counted input:
  extract_answer      73 calls: actual 10,450-17,911 (avg 13,187),
                      counted input about 10,200-12,200, completion avg 983
  extract_assignment  33 calls: actual 15,766-25,345 (avg 20,682),
                      counted input about 14,900-17,200, completion avg 2,614
  grade_assignment     5 calls: actual 8,725 vs counted input 5,994
  formatted_grade      3 calls: actual 3,406 vs counted input 2,276
  student_summary      3 calls: actual 1,013 vs counted input 404
Each headroom covers the largest seen (actual - counted input) with a margin
of at least 1.4x, and is at least 3x the 2,600 completion ceiling where the
output grows with the paper (extraction, grading). A task type with no data
keeps the old, conservative 20,000. Review against the call records
(completion_tokens p95 per task_type, and estimate vs total_tokens) when
real traffic has built up; change the numbers here only.

An estimate is never a charge: every call is charged its real usage after it
returns (an overrun is charged, never failed), so these numbers move holds
and refusals only.
"""

from __future__ import annotations

import math

CALL_HEADROOM = {
    "extract_answer": 8_000,
    "extract_assignment": 12_000,
    "grade_assignment": 8_000,
    "formatted_grade": 4_000,
    "student_summary": 3_000,
}
FALLBACK_CALL_HEADROOM = 20_000

EXTRACT_ANSWER = "extract_answer"
EXTRACT_ASSIGNMENT = "extract_assignment"
MANY_QUESTIONS = 99_999


def call_headroom(task_type):
    """What a call of `task_type` adds to its counted input."""
    return CALL_HEADROOM.get(task_type, FALLBACK_CALL_HEADROOM)


def _split(images, pages_per_call):
    return [
        images[start : start + pages_per_call]
        for start in range(0, len(images), pages_per_call)
    ] or [[]]


def upload_item_estimate(task_type, content, *, assignment=None):
    """What the task's calls for one uploaded file are estimated at, by the
    same function the task's gate asks for each call
    (AIProcessor.estimate_messages_cost), chunk for chunk:

      * an answer file (EXTRACT_ANSWER, `assignment` given): the messages
        extract_answer_image sends (system prompt, the assignment context,
        the roster, the file). One call below
        ANSWERS_EXTRACTION_PAGES_PER_CHUNK pages; past it, one call per chunk
        of that many pages, each with the same context (the chunked path
        sends a slimmer one) and, after the first, a headroom more for the
        answers found so far that it lists back;
      * an assignment file (EXTRACT_ASSIGNMENT): the upload system prompt and
        the content; one call up to ASSIGNMENT_UPLOAD_PAGES_PER_CHUNK pages,
        else one per chunk of that many, each with its chunk note.

    A one-call file's number is exactly the gate's; a chunked file's is at
    least the sum of the gate's numbers for its chunks."""
    from ai_processor.services import (
        ANSWERS_EXTRACTION_PAGES_PER_CHUNK,
        ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS,
        ASSIGNMENT_UPLOAD_PAGES_PER_CHUNK,
        ai_processor,
        assignment_chunk_note,
    )

    images = [
        item
        for item in content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    texts = [item for item in content if item not in images]

    if task_type == EXTRACT_ANSWER:
        from students.services import answer_assignment_context

        context = answer_assignment_context(assignment)
        if len(images) < ANSWERS_EXTRACTION_PAGES_PER_CHUNK:
            chunks = [images]
        else:
            chunks = _split(images, ANSWERS_EXTRACTION_PAGES_PER_CHUNK)
        total = 0
        for index, chunk in enumerate(chunks):
            messages = ai_processor.answer_extraction_messages(
                [*texts, *chunk], context, assignment
            )
            total += ai_processor.estimate_messages_cost(
                None, None, messages, task_type=task_type
            )
            if index:
                total += call_headroom(task_type)
        return total

    if len(images) <= ASSIGNMENT_UPLOAD_PAGES_PER_CHUNK:
        calls = [content]
    else:
        # The chunked path sends each chunk with its note in place of the
        # prompt; the note's question number is counted at its widest.
        pages = len(images)
        calls = [
            [
                {
                    "type": "text",
                    "text": assignment_chunk_note(
                        start + 1, start + len(chunk), pages, MANY_QUESTIONS
                    ),
                },
                *chunk,
            ]
            for start, chunk in zip(
                range(0, pages, ASSIGNMENT_UPLOAD_PAGES_PER_CHUNK),
                _split(images, ASSIGNMENT_UPLOAD_PAGES_PER_CHUNK),
                strict=True,
            )
        ]
    return sum(
        ai_processor.estimate_messages_cost(
            call,
            ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS,
            None,
            task_type=EXTRACT_ASSIGNMENT,
        )
        for call in calls
    )


def calls_for_questions(questions, per_call):
    """How many grading calls a paper of `questions` makes: one up to
    `per_call` questions, else one per batch plus the overall summary."""
    if questions <= per_call:
        return 1
    return math.ceil(questions / per_call) + 1
