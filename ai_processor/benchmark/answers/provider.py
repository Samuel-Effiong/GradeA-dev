"""A deterministic stand-in for the answer-extraction model.

WHAT THIS IS FOR, AND WHAT IT IS NOT

The deterministic suite has to answer "does OUR pipeline handle what the
model returns correctly" without spending credits or depending on model
behaviour. That needs a provider whose output is a known function of its
input. This is that provider.

It is emphatically NOT a claim about model quality. It reads the ground
truth out of the scenario - it does not look at the rasterized pages at
all - so a green deterministic run says the pipeline assembled, attributed
and classified correctly, and says nothing whatever about whether the real
model can read a page. That is what the live suite is for, and the two are
reported separately.

WHAT MAKES IT USEFUL RATHER THAN CIRCULAR

Three things. First, it answers PER CHUNK, seeing only the pages that
chunk was actually given - so if the pipeline sends the wrong pages, or
claims the wrong range, the provider's answer legitimately changes and the
expectation fails. That is the mechanism by which a page-attribution bug
is caught.

Second, an answer that spans pages is transcribed PER PAGE: a chunk that
holds only the first half returns only the first half. A provider that
returned the whole answer from either chunk would make a merge that drops
the second half look correct - which is exactly how that defect first
survived this benchmark.

Third, it can be told to misbehave in the specific ways a real provider
does - both transport failures (timeout, 429, 500, malformed JSON) and
plausible MODEL choices that the prompt permits (`Scenario.model_quirks`),
so the pipeline's handling of each is a test rather than a hope.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

import requests

from ai_processor.extraction_schemas import (
    ANSWERED,
    BLANK,
    BLANK_VERIFICATION_RESPONSE_SCHEMA,
    ILLEGIBLE,
    NOT_FOUND_IN_DOCUMENT,
)

#: Model behaviours a scenario can opt into. Each is something the current
#: prompt or chunk note arguably invites, so the merge has to survive it.
MODEL_QUIRKS = {
    # Follows the chunk note's already-found instruction literally. Asked
    # for the continuation of a found answer, it transcribes exactly the
    # part on these pages; told a found question needs no more extraction
    # (the wording before 2026-09-14), it reports NOT_FOUND for it.
    "obeys-already-found",
    # The last chunk's note says "mark any question that was not found in
    # any chunk as genuinely skipped". A model can read that as BLANK for
    # questions it never saw.
    "last-chunk-blanks-unseen",
    # source_page counted from 1 within the chunk, not the whole document.
    "relative-source-page",
    # source_page left null on empty entries even where the question was
    # located.
    "null-source-page",
}


class ProviderTimeout(requests.exceptions.Timeout):
    """A provider call that never came back."""


class ProviderHTTPError(Exception):
    """A provider call that came back with an HTTP error status."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"provider returned HTTP {status}")


@dataclass
class ProviderBehaviour:
    """
    How the fake provider should misbehave, and on which calls.

    Every field is keyed by 1-based call index where it makes sense, so a
    scenario can say "fail the second chunk twice, then succeed" - which
    is the shape that actually exercises retry and partial-failure paths.
    """

    #: call index -> exception to raise instead of answering.
    raise_on: dict[int, BaseException] = field(default_factory=dict)
    #: call index -> raw string to return instead of valid JSON.
    raw_on: dict[int, str] = field(default_factory=dict)
    #: question numbers the provider silently omits from every response.
    omit_questions: tuple[str, ...] = ()
    #: question numbers the provider reports twice in one response.
    duplicate_questions: tuple[str, ...] = ()
    #: drop this required field from every answer entry.
    drop_field: str | None = None
    #: put this value in answer_status instead of a valid one.
    bad_status: str | None = None
    #: emit answer_html as JSON null.
    null_answer_html: bool = False
    #: return the answers array in reverse order.
    reverse_order: bool = False
    #: answer a question that is not in the assignment at all.
    inject_unknown_question: str | None = None
    #: fabricate an answer for a question that has none on the page.
    fabricate_for: tuple[str, ...] = ()
    #: questions the BLANK re-read claims to find writing for. This is
    #: the one transition _verify_blank_answers may make
    #: (BLANK -> NOT_FOUND_IN_DOCUMENT), so it needs its own knob.
    blank_verification_finds: tuple[str, ...] = ()
    #: called with the RecordedCall before the provider answers - the hook
    #: the cancellation suite uses to cancel "during" a specific call.
    on_call: Callable | None = None
    #: call index -> "bare" or "prefixed": how that response writes question
    #: numbers. The live model switched between `1` and `Q1` from one chunk
    #: to the next for the same question.
    label_style: dict[int, str] = field(default_factory=dict)


@dataclass
class RecordedCall:
    """One provider call, as the pipeline made it."""

    index: int
    #: The page numbers the pipeline actually sent, read back out of the
    #: image payload markers - this is the ground truth for attribution.
    pages_sent: tuple[int, ...]
    #: The page range the pipeline CLAIMED in its chunk note.
    pages_claimed: tuple[int, int] | None
    response_schema: object
    task_type: str | None
    feature: str | None
    note: str
    processing_task_id: object = None
    #: Which pipeline stage made this call. The blank re-read is a
    #: SEPARATE billed call, not a chunk; conflating the two makes every
    #: chunk-count assertion wrong as soon as a scenario has a blank.
    kind: str = "extraction"
    #: Question numbers the chunk note told the model were already found.
    already_found: frozenset = frozenset()
    #: Whether that note asked for the continuation of a found answer.
    continuation_invited: bool = False


_PAGE_MARKER = re.compile(r"BENCHPAGE(\d+)")
_CLAIMED_RANGE = re.compile(r"pages (\d+) to (\d+)")
_ALREADY_FOUND = re.compile(
    r"already found on earlier pages: (.*?)\.(?:\s|$)", re.DOTALL
)
#: The wording before 2026-09-14, still parsed so that a revert (or a
#: mutant) restoring it is modelled faithfully instead of silently ignored.
_ALREADY_FOUND_LEGACY = re.compile(
    r"do NOT need to be extracted again: (.*?)\. Focus on", re.DOTALL
)
#: Present only when the note asks for the continuation of a found answer.
CONTINUATION_MARKER = "CONTINUES on these pages"


def already_found_in(note: str) -> frozenset:
    """
    The question labels a chunk note lists as already found.

    Current wording: "already found on earlier pages: Q1, Q3." Legacy
    wording: "do NOT need to be extracted again: QQ1. Q3." - that rendering
    doubled the first label's prefix, so one leading "Q" is stripped there.
    """
    match = _ALREADY_FOUND.search(note or "")
    if match:
        return frozenset(
            token.strip() for token in match.group(1).split(",") if token.strip()
        )
    match = _ALREADY_FOUND_LEGACY.search(note or "")
    if not match:
        return frozenset()
    tokens = [token.strip() for token in match.group(1).split(". ")]
    if tokens and tokens[0].startswith("Q"):
        tokens[0] = tokens[0][1:]
    return frozenset(token for token in tokens if token)


def invites_continuation(note: str) -> bool:
    """Whether the note asks for the continuation of an already-found answer."""
    return CONTINUATION_MARKER in (note or "")


class FakeProvider:
    """
    Drop-in for `AIProcessor.execute_graded_task`.

    Install with `patch.object(processor, "execute_graded_task", provider)`.
    Every call is recorded; `provider.calls` is the audit trail the
    chunk-invariant and attribution tests assert against.
    """

    def __init__(self, scenario, behaviour: ProviderBehaviour | None = None):
        self.scenario = scenario
        self.behaviour = behaviour or ProviderBehaviour()
        self.calls: list[RecordedCall] = []
        self._current_index = 0
        unknown = set(getattr(scenario, "model_quirks", ())) - MODEL_QUIRKS
        if unknown:
            raise ValueError(f"unknown model quirks {sorted(unknown)}")

    # -- introspection helpers ------------------------------------------

    @property
    def call_count(self) -> int:
        """Every provider call, including blank re-reads (billing view)."""
        return len(self.calls)

    @property
    def extraction_calls(self) -> list[RecordedCall]:
        return [call for call in self.calls if call.kind == "extraction"]

    @property
    def verification_calls(self) -> list[RecordedCall]:
        return [call for call in self.calls if call.kind == "blank_verification"]

    @property
    def pages_sent_per_call(self) -> list[tuple[int, ...]]:
        return [call.pages_sent for call in self.extraction_calls]

    def all_pages_sent(self) -> list[int]:
        return [page for call in self.calls for page in call.pages_sent]

    # -- the callable itself --------------------------------------------

    def __call__(self, **kwargs):
        index = len(self.calls) + 1
        note, pages_sent = self._read_payload(kwargs)
        schema = kwargs.get("response_schema")
        kind = (
            "blank_verification"
            if schema is BLANK_VERIFICATION_RESPONSE_SCHEMA
            else "extraction"
        )

        call = RecordedCall(
            index=index,
            pages_sent=pages_sent,
            pages_claimed=self._read_claimed_range(note),
            response_schema=schema,
            task_type=kwargs.get("task_type"),
            feature=kwargs.get("feature"),
            note=note,
            processing_task_id=kwargs.get("processing_task_id"),
            kind=kind,
            already_found=already_found_in(note),
            continuation_invited=invites_continuation(note),
        )
        self.calls.append(call)
        self._current_index = index

        if self.behaviour.on_call is not None:
            self.behaviour.on_call(call)

        failure = self.behaviour.raise_on.get(index)
        if failure is not None:
            raise failure

        raw = self.behaviour.raw_on.get(index)
        if raw is not None:
            return _Response(raw)

        if kind == "blank_verification":
            return _Response(json.dumps(self._blank_verification_payload()))

        return _Response(
            json.dumps(
                self._payload_for(
                    pages_sent, call.already_found, call.continuation_invited
                )
            )
        )

    def _blank_verification_payload(self) -> dict:
        """
        The narrow re-read of claimed blanks. Reports writing only for
        questions the scenario says it should - so by default a genuine
        blank is confirmed blank, and a scenario can opt in to the one
        transition this step may make.
        """
        findings = []
        for number in self.behaviour.blank_verification_finds:
            findings.append(
                {
                    "question_number": number,
                    "observed": "faint writing in the margin",
                    "page": 1,
                    "verbatim_fragment": "...partial...",
                    "content_found": True,
                }
            )
        return {"findings": findings}

    # -- payload construction -------------------------------------------

    def _read_payload(self, kwargs) -> tuple[str, tuple[int, ...]]:
        """
        Recover the note text and the page numbers actually sent.

        Pages are identified by the BENCHPAGE<n> marker the harness puts
        in each image block, so this reflects the payload the pipeline
        built rather than anything the harness told the provider.
        """
        blocks: list = []
        messages = kwargs.get("messages")
        if messages:
            for message in messages:
                content = message.get("content")
                if isinstance(content, list):
                    blocks.extend(content)
                elif isinstance(content, str):
                    blocks.append({"type": "text", "text": content})
        prompt = kwargs.get("user_prompt")
        if isinstance(prompt, list):
            blocks.extend(prompt)
        elif isinstance(prompt, str):
            blocks.append({"type": "text", "text": prompt})

        note_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        pages: list[int] = []
        for block in blocks:
            if block.get("type") != "image_url":
                continue
            url = (block.get("image_url") or {}).get("url", "")
            marker = _PAGE_MARKER.search(url) or _PAGE_MARKER.search(
                str(block.get("bytes", ""))
            )
            if marker:
                pages.append(int(marker.group(1)))
        return "\n".join(note_parts), tuple(pages)

    def _read_claimed_range(self, note: str) -> tuple[int, int] | None:
        match = _CLAIMED_RANGE.search(note)
        if not match:
            return None
        return (int(match.group(1)), int(match.group(2)))

    def _styled_label(self, label):
        """The question label as this call's `label_style` writes it."""
        style = self.behaviour.label_style.get(self._current_index)
        text = str(label)
        if style == "bare" and text[:1] in ("Q", "q") and text[1:].isdigit():
            return text[1:]
        if style == "prefixed" and text.isdigit():
            return f"Q{text}"
        return label

    def _payload_for(
        self,
        pages_sent: tuple[int, ...],
        already_found,
        continuation_invited: bool = False,
    ) -> dict:
        """
        The answers a model would return for exactly these pages, shaped by
        the scenario's model quirks and then mutated per `behaviour`.
        """
        behaviour = self.behaviour
        quirks = set(getattr(self.scenario, "model_quirks", ()))
        visible = set(pages_sent)
        chunk_pages = sorted(visible)
        is_last_chunk = self.scenario.page_count in visible
        obeyed = already_found if "obeys-already-found" in quirks else frozenset()
        answers = []

        for expectation in self.scenario.expectations:
            number = expectation.question
            if number in behaviour.omit_questions:
                continue

            located = [page for page in expectation.pages if page in visible]
            fabricate = number in behaviour.fabricate_for
            source_page = located[0] if located else None

            if str(expectation.model_question_number) in obeyed:
                # Literal-minded: asked for a continuation, it transcribes the
                # part of this answer on these pages; not asked, it reports
                # nothing for a question the note says was already found.
                continuation = (
                    [
                        (page, text)
                        for page, text in expectation.fragments
                        if page in visible
                    ]
                    if continuation_invited
                    else []
                )
                if continuation:
                    html = "<p>" + " ".join(t for _p, t in continuation) + "</p>"
                    status, source_page = ANSWERED, continuation[0][0]
                else:
                    html, status, source_page = "", NOT_FOUND_IN_DOCUMENT, None
            elif located or fabricate:
                # What the model says on seeing the page, which a scenario
                # may set apart from what the pipeline must finally output.
                reported = expectation.model_status or expectation.status
                if reported == ANSWERED or fabricate:
                    fragments = [
                        text for page, text in expectation.fragments if page in visible
                    ]
                    text = (
                        " ".join(fragments)
                        if fragments
                        else (expectation.answer or "fabricated")
                    )
                    html, status = f"<p>{text}</p>", ANSWERED
                elif reported == BLANK:
                    html, status = "", BLANK
                elif reported == ILLEGIBLE:
                    html, status = "", ILLEGIBLE
                else:
                    html, status = "", NOT_FOUND_IN_DOCUMENT
            elif is_last_chunk and "last-chunk-blanks-unseen" in quirks:
                html, status = "", BLANK
            else:
                # Not on the pages this chunk was given. A well-behaved
                # model reports it as not found HERE; the pipeline's merge
                # is what must reconcile that across chunks.
                html, status = "", NOT_FOUND_IN_DOCUMENT

            if source_page is not None:
                if "null-source-page" in quirks and status != ANSWERED:
                    source_page = None
                elif "relative-source-page" in quirks:
                    source_page = chunk_pages.index(source_page) + 1

            entry = {
                "question_number": self._styled_label(
                    expectation.model_question_number
                ),
                "question_text": expectation.question_text,
                "source_page": source_page,
                "transcription_notes": "",
                "answer_html": None if behaviour.null_answer_html else html,
                "answer_status": behaviour.bad_status or status,
                "confidence": 90,
            }
            if behaviour.drop_field:
                entry.pop(behaviour.drop_field, None)
            answers.append(entry)
            if number in behaviour.duplicate_questions:
                answers.append(dict(entry))

        if behaviour.inject_unknown_question:
            answers.append(
                {
                    "question_number": behaviour.inject_unknown_question,
                    "question_text": "not in the assignment",
                    "source_page": None,
                    "transcription_notes": "",
                    "answer_html": "<p>stray</p>",
                    "answer_status": ANSWERED,
                    "confidence": 50,
                }
            )

        if behaviour.reverse_order:
            answers.reverse()

        first_chunk = 1 in visible
        return {
            "student_name": self.scenario.student_name if first_chunk else "",
            "student_name_raw": None,
            "student_id": self.scenario.student_id if first_chunk else "",
            "answers": answers,
            "extraction_confidence": 90,
            "feedback": "",
        }


# --------------------------------------------------------------------------
# Minimal SDK-shaped response
# --------------------------------------------------------------------------


class _Message:
    __slots__ = ("content",)

    def __init__(self, content):
        self.content = content


class _Choice:
    __slots__ = ("message",)

    def __init__(self, content):
        self.message = _Message(content)


class _Usage:
    __slots__ = ("total_tokens",)

    def __init__(self, total_tokens=1234):
        self.total_tokens = total_tokens


class _Response:
    __slots__ = ("choices", "model", "usage")

    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.model = "benchmark/deterministic"
        self.usage = _Usage()


def timeout_error() -> BaseException:
    return ProviderTimeout("provider timed out")


def http_error(status: int) -> BaseException:
    return ProviderHTTPError(status)


def connection_error() -> BaseException:
    return requests.exceptions.ConnectionError("provider unreachable")


def builder(scenario, behaviour: ProviderBehaviour | None = None) -> Callable:
    return FakeProvider(scenario, behaviour)
