"""The answer-extraction scenario catalogue.

Every scenario has a stable id (AE-NNN) that the regression mapping in
README.md refers to. Ids are never reused or renumbered, so a failure in a
six-month-old CI log still identifies the same case.

A scenario is DATA: a document to build, a question set, and the expected
status of every question. The suites turn that into assertions; nothing
here imports the pipeline, so the expectations cannot accidentally be
derived from the implementation they are checking.

EXPECTATION DISCIPLINE

`status` is a hard, exact assertion - it is the safety contract
(ANSWERED / BLANK / NOT_FOUND_IN_DOCUMENT) and the whole reason this
package exists. `answer` is the semantic content, asserted after
normalisation so that punctuation or wrapping differences from a real
model do not fail a correct extraction. `pages` is where the content
genuinely sits in the document, and drives page-attribution checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ai_processor.extraction_schemas import ANSWERED, BLANK, NOT_FOUND_IN_DOCUMENT

from .documents import (
    Document,
    Ink,
    Line,
    Page,
    answer_line,
    blank_answer_lines,
    padded_to,
    question_line,
    spread_pages,
)

# Distinctive enough that a model answering from priors could not produce
# them, and distinct enough from each other that a cross-contamination is
# unmistakable.
BANK = [
    ("Q1", "What is the capital city of Iceland?", "Reykjavik"),
    ("Q2", "Name the largest moon of Saturn.", "Titan"),
    ("Q3", "Which gas is about 78% of Earth's atmosphere?", "Nitrogen"),
    ("Q4", "What is the chemical symbol for tungsten?", "W"),
    ("Q5", "In which year did Apollo 11 land?", "1969"),
    ("Q6", "What is the square root of 289?", "17"),
    ("Q7", "Who wrote the novel Beloved?", "Toni Morrison"),
    ("Q8", "What is the SI unit of capacitance?", "Farad"),
    ("Q9", "Which river flows through Budapest?", "Danube"),
    ("Q10", "What is 15 percent of 240?", "36"),
    ("Q11", "Name the deepest oceanic trench.", "Mariana Trench"),
    ("Q12", "Which element has atomic number 26?", "Iron"),
]


@dataclass(frozen=True)
class Expectation:
    question: str
    status: str
    answer: str | None = None
    #: 1-based pages the content genuinely occupies in the document.
    pages: tuple[int, ...] = ()
    question_text: str = ""
    #: What the model reports as question_number. Differs from `question`
    #: only for the irregular-numbering scenarios.
    model_question_number: object = None
    #: (page, text) for an answer written across pages. A chunk holding
    #: only some of those pages transcribes only those parts, and every
    #: part must survive into the final answer.
    fragments: tuple[tuple[int, str], ...] = ()
    #: What the MODEL reports for this question on a page that shows it,
    #: where that differs from what the PIPELINE must finally output. Empty
    #: means the two agree - the ordinary case.
    model_status: str = ""

    def __post_init__(self):
        if self.model_question_number is None:
            object.__setattr__(self, "model_question_number", self.question)


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    category: str
    document: Document
    expectations: tuple[Expectation, ...]
    #: Questions the assignment declares. Usually the same as the
    #: expectations, but a not-found scenario declares MORE than the
    #: document contains - that asymmetry is the point.
    questions: tuple[dict, ...] = ()
    student_name: str = "Sam Tester"
    student_id: str = "S-1001"
    #: Overrides the production chunk size for this scenario.
    pages_per_chunk: int | None = None
    notes: str = ""
    tags: tuple[str, ...] = field(default=())
    #: Model behaviours the fake provider should exhibit; see
    #: provider.MODEL_QUIRKS.
    model_quirks: tuple[str, ...] = ()
    #: Non-empty when this scenario encodes the CORRECT behaviour and the
    #: pipeline is known not to meet it yet. The runner reports it as a
    #: known gap - never as a pass, and never silently skipped.
    known_gap: str = ""

    @property
    def page_count(self) -> int:
        return self.document.page_count


def _questions_for(expectations) -> tuple[dict, ...]:
    return tuple(
        {
            "question_number": expectation.model_question_number,
            "question_text": expectation.question_text or expectation.question,
            "question_type": "SHORT-ANSWER",
            "question_image": "",
            "points": 5,
            "blooms_level": "Remember",
            "options": [],
            "rubric": [],
            "model_answer": expectation.answer or "",
        }
        for expectation in expectations
    )


def _simple(
    scenario_id,
    name,
    category,
    *,
    answered=(),
    blank=(),
    not_found=(),
    per_page=1,
    pad_to=None,
    ink=Ink.TYPED,
    noise=0.0,
    rotation=0,
    figure_on=(),
    pages_per_chunk=None,
    notes="",
    tags=(),
    model_quirks=(),
    known_gap="",
):
    """
    Build a scenario from question-number lists.

    `answered` and `blank` are rendered onto pages; `not_found` questions
    are declared by the assignment but never appear in the document, which
    is exactly the situation that must not collapse into BLANK.
    """
    by_number = {number: (text, answer) for number, text, answer in BANK}
    entries: list[tuple[str, str, str | None]] = []
    order: list[tuple[str, str, str | None, str]] = []
    for number in answered:
        text, answer = by_number[number]
        entries.append((number, text, answer))
        order.append((number, ANSWERED, answer, text))
    for number in blank:
        text, answer = by_number[number]
        entries.append((number, text, None))
        order.append((number, BLANK, None, text))

    # Keep document order stable: sort by the numeric part of the label.
    entries.sort(key=lambda item: int(item[0][1:]))
    order.sort(key=lambda item: int(item[0][1:]))

    document = spread_pages(
        scenario_id,
        per_page,
        entries,
        ink=ink,
        noise=noise,
        rotation=rotation,
        figure_on=figure_on,
    )
    if pad_to:
        document = padded_to(document, pad_to)

    # Which page each question landed on.
    page_of: dict[str, int] = {}
    for page_index, page in enumerate(document.pages, start=1):
        for line in page.lines:
            if line.question and line.question not in page_of:
                page_of[line.question] = page_index

    expectation_list = [
        Expectation(
            question=number,
            status=status,
            answer=answer,
            pages=(page_of[number],) if number in page_of else (),
            question_text=text,
        )
        for number, status, answer, text in order
    ]
    for number in not_found:
        text, answer = by_number[number]
        expectation_list.append(
            Expectation(
                question=number,
                status=NOT_FOUND_IN_DOCUMENT,
                answer=None,
                pages=(),
                question_text=text,
            )
        )
    expectation_list.sort(key=lambda e: int(e.question[1:]))
    expectations = tuple(expectation_list)

    return Scenario(
        id=scenario_id,
        name=name,
        category=category,
        document=document,
        expectations=expectations,
        questions=_questions_for(expectations),
        pages_per_chunk=pages_per_chunk,
        notes=notes,
        tags=tags,
        model_quirks=model_quirks,
        known_gap=known_gap,
    )


# ==========================================================================
# Catalogue
# ==========================================================================

SCENARIOS: list[Scenario] = []


def _add(scenario: Scenario) -> Scenario:
    if any(existing.id == scenario.id for existing in SCENARIOS):
        raise ValueError(f"duplicate scenario id {scenario.id}")
    SCENARIOS.append(scenario)
    return scenario


# -- Baseline / single-call ------------------------------------------------

_add(
    _simple(
        "AE-001",
        "single-page-basic",
        "baseline",
        answered=("Q1", "Q2"),
        per_page=2,
        notes="One page, both answered. The single-call path.",
        tags=("single-call", "answered"),
    )
)

_add(
    _simple(
        "AE-002",
        "single-page-blank",
        "status",
        answered=("Q1",),
        blank=("Q2",),
        per_page=2,
        notes="A deliberate blank beside an answered question.",
        tags=("single-call", "blank"),
    )
)

_add(
    _simple(
        "AE-003",
        "single-page-not-found",
        "status",
        answered=("Q1",),
        not_found=("Q5",),
        per_page=1,
        notes="Q5 is declared by the assignment and absent from the "
        "document. It must not become BLANK.",
        tags=("single-call", "not-found"),
    )
)

_add(
    _simple(
        "AE-004",
        "two-page-below-threshold",
        "boundary",
        answered=("Q1", "Q2"),
        per_page=1,
        notes="Two pages: immediately below the chunking threshold, "
        "so this must take the single-call path.",
        tags=("single-call", "boundary"),
    )
)

# -- The chunking size matrix ---------------------------------------------
# Page counts either side of, and well past, the production threshold.

for _pages in (3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20):
    _add(
        _simple(
            f"AE-0{10 + _pages:02d}",
            f"size-matrix-{_pages}-pages",
            "chunking",
            answered=tuple(number for number, _t, _a in BANK[: min(_pages, 12)]),
            per_page=1,
            pad_to=_pages,
            notes=f"{_pages}-page document. Exercises chunk count, coverage and "
            "the evenness of the final chunk.",
            tags=("chunked" if _pages >= 3 else "single-call", "size-matrix"),
        )
    )

# -- Explicit chunk-size sweep on one fixed document -----------------------

for _size in (1, 2, 3, 4, 5):
    _add(
        _simple(
            f"AE-1{_size:02d}",
            f"chunk-size-{_size}",
            "chunking",
            answered=tuple(number for number, _t, _a in BANK[:9]),
            per_page=1,
            pages_per_chunk=_size,
            notes=f"Nine pages at {_size} page(s) per chunk. Proves the "
            "implementation does not assume one particular chunk size.",
            tags=("chunked", "chunk-size-sweep"),
        )
    )

# -- Chunk boundaries ------------------------------------------------------

_add(
    _simple(
        "AE-200",
        "answer-immediately-before-boundary",
        "boundary",
        answered=("Q1", "Q2", "Q3", "Q4"),
        per_page=1,
        notes="With 3 pages/chunk the seam falls after page 3, so Q3 "
        "sits immediately before it.",
        tags=("chunked", "boundary"),
    )
)

_add(
    _simple(
        "AE-201",
        "answer-immediately-after-boundary",
        "boundary",
        answered=("Q1", "Q2", "Q3", "Q4", "Q5"),
        per_page=1,
        notes="Q4 is the first question of the second chunk.",
        tags=("chunked", "boundary"),
    )
)

_add(
    _simple(
        "AE-202",
        "blank-exactly-on-boundary",
        "boundary",
        answered=("Q1", "Q2", "Q4"),
        blank=("Q3",),
        per_page=1,
        notes="A deliberate blank on the last page of chunk 1. The "
        "blank must survive the merge as BLANK, not be "
        "overwritten by chunk 2's not-found view of it.",
        tags=("chunked", "boundary", "blank"),
    )
)

_add(
    _simple(
        "AE-203",
        "not-found-at-boundary",
        "boundary",
        answered=("Q1", "Q2", "Q3", "Q4"),
        not_found=("Q9",),
        per_page=1,
        notes="An absent question alongside a boundary: no chunk has "
        "it, so every chunk reports not-found and the merge must "
        "keep it that way.",
        tags=("chunked", "boundary", "not-found"),
    )
)

_add(
    _simple(
        "AE-204",
        "uneven-final-chunk",
        "boundary",
        answered=tuple(n for n, _t, _a in BANK[:7]),
        per_page=1,
        notes="Seven pages at 3/chunk: 3+3+1. The short final chunk "
        "must be described honestly, not as a full-width range.",
        tags=("chunked", "boundary"),
    )
)


def _cross_boundary_scenario() -> Scenario:
    """
    Q3's answer starts on page 3 and continues on page 4 - across the
    chunk seam at 3 pages/chunk. It must stay ONE logical answer.
    """
    scenario_id = "AE-205"
    pages = (
        Page(lines=(question_line("Q1", BANK[0][1]), answer_line("Q1", BANK[0][2]))),
        Page(lines=(question_line("Q2", BANK[1][1]), answer_line("Q2", BANK[1][2]))),
        Page(
            lines=(
                question_line("Q3", BANK[2][1]),
                answer_line("Q3", "The major component is"),
                Line("   (continued overleaf)", question="Q3"),
            )
        ),
        Page(
            lines=(
                Line("   ...continued from previous page", question="Q3"),
                answer_line("Q3", BANK[2][2]),
            )
        ),
        Page(lines=(question_line("Q4", BANK[3][1]), answer_line("Q4", BANK[3][2]))),
    )
    expectations = (
        Expectation("Q1", ANSWERED, BANK[0][2], (1,), BANK[0][1]),
        Expectation("Q2", ANSWERED, BANK[1][2], (2,), BANK[1][1]),
        Expectation(
            "Q3",
            ANSWERED,
            BANK[2][2],
            (3, 4),
            BANK[2][1],
            fragments=((3, "The major component is"), (4, BANK[2][2])),
        ),
        Expectation("Q4", ANSWERED, BANK[3][2], (5,), BANK[3][1]),
    )
    return Scenario(
        id=scenario_id,
        name="answer-spans-chunk-boundary",
        category="boundary",
        document=Document(scenario_id, pages),
        expectations=expectations,
        questions=_questions_for(expectations),
        notes="Q3 spans pages 3-4, i.e. across the seam at 3 pages/chunk. "
        "Chunk 1 sees only the opening, chunk 2 only the conclusion; "
        "the merge must produce one answer, not two halves or a "
        "not-found.",
        tags=("chunked", "boundary", "cross-chunk"),
    )


_add(_cross_boundary_scenario())


def _cross_page_within_chunk() -> Scenario:
    """The same spanning shape, but inside one chunk - the control case."""
    scenario_id = "AE-206"
    pages = (
        Page(
            lines=(
                question_line("Q1", BANK[0][1]),
                answer_line("Q1", "The capital is"),
                Line("   (continued overleaf)", question="Q1"),
            )
        ),
        Page(
            lines=(
                Line("   ...continued", question="Q1"),
                answer_line("Q1", BANK[0][2]),
            )
        ),
        Page(lines=(question_line("Q2", BANK[1][1]), answer_line("Q2", BANK[1][2]))),
    )
    expectations = (
        Expectation(
            "Q1",
            ANSWERED,
            BANK[0][2],
            (1, 2),
            BANK[0][1],
            fragments=((1, "The capital is"), (2, BANK[0][2])),
        ),
        Expectation("Q2", ANSWERED, BANK[1][2], (3,), BANK[1][1]),
    )
    return Scenario(
        id=scenario_id,
        name="answer-spans-pages-within-one-chunk",
        category="boundary",
        document=Document(scenario_id, pages),
        expectations=expectations,
        questions=_questions_for(expectations),
        notes="Control for AE-205: the same cross-page answer entirely "
        "inside one chunk. If AE-205 fails and this passes, the "
        "defect is in chunk assembly rather than page handling.",
        tags=("chunked", "cross-page"),
    )


_add(_cross_page_within_chunk())

# -- Status matrix ---------------------------------------------------------

_add(
    _simple(
        "AE-300",
        "all-three-states-together",
        "status",
        answered=("Q1", "Q2", "Q4"),
        blank=("Q3",),
        not_found=("Q5", "Q6"),
        per_page=2,
        notes="The canonical mixed case: answered, deliberately blank, "
        "and genuinely absent, in one document.",
        tags=("status", "answered", "blank", "not-found"),
    )
)

_add(
    _simple(
        "AE-301",
        "blank-between-answered",
        "status",
        answered=("Q1", "Q3"),
        blank=("Q2",),
        per_page=3,
        notes="A blank surrounded by answers on the same page.",
        tags=("status", "blank"),
    )
)

_add(
    _simple(
        "AE-302",
        "blank-at-end-of-page",
        "status",
        answered=("Q1",),
        blank=("Q2",),
        per_page=2,
        notes="Blank as the last thing on a page.",
        tags=("status", "blank"),
    )
)

_add(
    _simple(
        "AE-303",
        "all-blank",
        "status",
        blank=("Q1", "Q2", "Q3"),
        per_page=1,
        notes="A script the student handed in untouched. Every answer "
        "BLANK, none NOT_FOUND - we have the work, it is empty.",
        tags=("status", "blank", "chunked"),
    )
)

_add(
    _simple(
        "AE-304",
        "all-not-found",
        "status",
        not_found=("Q1", "Q2", "Q3"),
        answered=("Q7",),
        per_page=1,
        notes="The assignment declares questions the script does not "
        "contain at all.",
        tags=("status", "not-found"),
    )
)

_add(
    _simple(
        "AE-305",
        "document-ends-before-later-questions",
        "status",
        answered=("Q1", "Q2"),
        not_found=("Q8", "Q9", "Q10"),
        per_page=1,
        notes="The script simply stops. The tail must be NOT_FOUND, "
        "which routes to a human, not BLANK, which scores zero.",
        tags=("status", "not-found"),
    )
)

# -- Numbering variants ----------------------------------------------------


def _numbering(scenario_id, name, labels, notes, tags=("numbering",)):
    entries = []
    expectation_list: list[Expectation] = []
    for index, label in enumerate(labels):
        _n, text, answer = BANK[index]
        entries.append((label, text, answer))
        expectation_list.append(
            Expectation(
                question=label,
                status=ANSWERED,
                answer=answer,
                pages=(index + 1,),
                question_text=text,
                model_question_number=label,
            )
        )
    document = spread_pages(scenario_id, 1, entries)
    expectations = tuple(expectation_list)
    return Scenario(
        id=scenario_id,
        name=name,
        category="numbering",
        document=document,
        expectations=expectations,
        questions=_questions_for(expectations),
        notes=notes,
        tags=tags,
    )


_add(
    _numbering(
        "AE-400", "numbering-sequential", ("Q1", "Q2", "Q3", "Q4"), "The ordinary case."
    )
)
_add(
    _numbering(
        "AE-401",
        "numbering-with-gaps",
        ("Q1", "Q2", "Q4", "Q5"),
        "A paper that skips Q3 entirely. The gap is in the "
        "assignment too, so nothing should be reported missing.",
    )
)
_add(
    _numbering(
        "AE-402",
        "numbering-double-digit",
        ("Q10", "Q11", "Q12"),
        "Two-digit labels: string vs int comparison is a real "
        "source of silent join failures here.",
    )
)

_SUBQ = ("1(a)", "1(b)", "2(a)", "2(b)")
_add(
    _numbering(
        "AE-403",
        "numbering-subquestions",
        _SUBQ,
        "Sub-question labels. _question_number_key keeps these as "
        "strings; this pins that they round-trip rather than being "
        "coerced to 1 and colliding.",
        tags=("numbering", "supported"),
    )
)

# -- Multi-question pages --------------------------------------------------

_add(
    _simple(
        "AE-500",
        "many-questions-one-page",
        "layout",
        answered=("Q1", "Q2", "Q3", "Q4", "Q5", "Q6"),
        per_page=6,
        notes="Six questions on a single page.",
        tags=("layout", "single-call"),
    )
)

_add(
    _simple(
        "AE-501",
        "mixed-status-dense-page",
        "layout",
        answered=("Q1", "Q3", "Q5"),
        blank=("Q2", "Q4"),
        not_found=("Q11",),
        per_page=5,
        notes="Dense page mixing answered and blank, plus an absent "
        "question. Association must stay correct.",
        tags=("layout", "status"),
    )
)

_add(
    _simple(
        "AE-502",
        "two-per-page-across-chunks",
        "layout",
        answered=tuple(n for n, _t, _a in BANK[:8]),
        per_page=2,
        notes="Two questions per page over four pages, so a chunk "
        "seam falls between question pairs.",
        tags=("layout", "chunked"),
    )
)

# -- Difficult pages -------------------------------------------------------

_add(
    _simple(
        "AE-600",
        "handwritten-synthetic",
        "difficult",
        answered=("Q1", "Q2", "Q3"),
        blank=("Q4",),
        per_page=1,
        ink=Ink.HANDWRITTEN,
        notes="SYNTHETIC irregular writing - not real handwriting. "
        "Exercises the pipeline on hard input; makes no claim "
        "about the model's handwriting OCR.",
        tags=("difficult", "handwritten-synthetic", "chunked"),
    )
)

_add(
    _simple(
        "AE-601",
        "noisy-scan",
        "difficult",
        answered=("Q1", "Q2", "Q3"),
        blank=("Q4",),
        per_page=1,
        noise=0.7,
        notes="Speckle, streaks and reduced contrast over the text.",
        tags=("difficult", "noisy", "chunked"),
    )
)

_add(
    _simple(
        "AE-602",
        "rotated-pages",
        "difficult",
        answered=("Q1", "Q2", "Q3"),
        per_page=1,
        rotation=90,
        notes="Pages rotated 90 degrees, as a misfed scanner produces.",
        tags=("difficult", "rotated", "chunked"),
    )
)

_add(
    _simple(
        "AE-603",
        "image-heavy",
        "difficult",
        answered=("Q1", "Q2", "Q3", "Q4"),
        per_page=1,
        figure_on=(0, 1, 2, 3),
        notes="A figure on every page: larger rasterized payloads.",
        tags=("difficult", "image-heavy", "chunked"),
    )
)

_add(
    _simple(
        "AE-604",
        "noisy-and-handwritten",
        "difficult",
        answered=("Q1", "Q2"),
        blank=("Q3",),
        not_found=("Q8",),
        per_page=1,
        ink=Ink.HANDWRITTEN,
        noise=0.5,
        notes="Both degradations together, with all three statuses.",
        tags=("difficult", "handwritten-synthetic", "noisy"),
    )
)

# -- Hallucination pressure ------------------------------------------------


def _tempting_neighbour() -> Scenario:
    """
    Q3 has no answer written, but the correct answer to Q3 appears
    elsewhere on the page as part of another question's text. A model that
    borrows from context would fill it in; the pipeline must not present
    that as the student's work.
    """
    scenario_id = "AE-700"
    pages = (
        Page(
            lines=(
                question_line(
                    "Q1", "Which gas is mostly used in lightbulbs? Nitrogen is common."
                ),
                answer_line("Q1", "Argon"),
                question_line("Q3", BANK[2][1]),
                *blank_answer_lines("Q3"),
            )
        ),
    )
    expectations = (
        Expectation(
            "Q1",
            ANSWERED,
            "Argon",
            (1,),
            "Which gas is mostly used in lightbulbs? Nitrogen is common.",
        ),
        Expectation("Q3", BLANK, None, (1,), BANK[2][1]),
    )
    return Scenario(
        id=scenario_id,
        name="tempting-neighbouring-answer",
        category="hallucination",
        document=Document(scenario_id, pages),
        expectations=expectations,
        questions=_questions_for(expectations),
        notes="The text of Q1 contains 'Nitrogen', which is the correct "
        "answer to Q3 - and Q3 is blank. Borrowing it would invent a "
        "student answer out of page furniture.",
        tags=("hallucination", "blank"),
    )


_add(_tempting_neighbour())

_add(
    _simple(
        "AE-701",
        "absent-question-with-answered-neighbours",
        "hallucination",
        answered=("Q1", "Q2", "Q3"),
        not_found=("Q12",),
        per_page=3,
        notes="Q12 is absent while its neighbours are answered - the "
        "shape most likely to tempt a fabricated entry.",
        tags=("hallucination", "not-found"),
    )
)

# -- Chunk merge under realistic model behaviour ---------------------------
# Each of these is a choice the current prompt or chunk note permits. The
# merge has to reach the right answer anyway, because none of them is a
# model malfunction the pipeline is entitled to blame.

_add(
    _simple(
        "AE-800",
        "last-chunk-blanks-questions-it-never-saw",
        "merge",
        answered=("Q1", "Q3", "Q4"),
        blank=("Q2",),
        not_found=("Q9",),
        per_page=1,
        model_quirks=("last-chunk-blanks-unseen",),
        notes="The last chunk's note says to 'mark any question that was "
        "not found in any chunk as genuinely skipped'. A model that "
        "writes BLANK for Q9 - which is in no page at all - must "
        "not turn an absent answer into a silent zero. Q2, a real "
        "blank the first chunk located, must stay BLANK.",
        tags=("chunked", "merge", "not-found", "blank"),
    )
)

_add(
    _simple(
        "AE-801",
        "blank-with-chunk-relative-source-page",
        "merge",
        answered=("Q1", "Q2", "Q3", "Q5"),
        blank=("Q4",),
        per_page=1,
        model_quirks=("relative-source-page",),
        notes="Q4 is blank on page 4, the first page of chunk 2. The "
        "model numbers pages within the chunk, so it reports "
        "source_page=1. That is still a claim to have located the "
        "question and must keep the blank a BLANK.",
        tags=("chunked", "merge", "blank"),
    )
)


def _unplaceable_blank() -> Scenario:
    """
    The model reports Q4 as BLANK but cannot say where it saw it; the
    pipeline must output NOT_FOUND_IN_DOCUMENT. `model_status` carries the
    first half, `status` the second - without the split the fake provider
    would itself say NOT_FOUND and the scenario would prove nothing.
    """
    base = _unplaceable_blank_base()
    expectations = tuple(
        (
            replace(expectation, status=NOT_FOUND_IN_DOCUMENT, model_status=BLANK)
            if expectation.question == "Q4"
            else expectation
        )
        for expectation in base.expectations
    )
    return replace(
        base, expectations=expectations, questions=_questions_for(expectations)
    )


def _unplaceable_blank_base() -> Scenario:
    return _simple(
        "AE-802",
        "blank-the-model-cannot-place",
        "merge",
        answered=("Q1", "Q2", "Q3"),
        blank=("Q4",),
        per_page=1,
        model_quirks=("null-source-page",),
        notes="The page-owning chunk says BLANK but gives source_page "
        "null - the prompt's own signal for 'never located'. An "
        "empty verdict nobody can place is not trusted over "
        "not-found, so this routes to review. Documented trade: "
        "a genuine blank here costs a teacher a glance, rather "
        "than an absent answer costing a student a grade.",
        tags=("chunked", "merge", "not-found"),
    )


_add(_unplaceable_blank())

_add(
    replace(
        _cross_boundary_scenario(),
        id="AE-803",
        name="split-answer-with-a-model-that-follows-the-chunk-note",
        category="merge",
        model_quirks=("obeys-already-found",),
        tags=("chunked", "cross-chunk", "merge", "split-answer"),
        notes="AE-205's page 3|4 answer, with a model that does exactly what "
        "the chunk note says. Until 2026-09-14 the note said Q3 'does NOT "
        "need to be extracted again', and the second half was lost - the "
        "real model did the same (live AE-905). The note now asks for the "
        "continuation. Requirement: a student's answer must never be "
        "silently truncated because it crosses a chunk boundary.",
    )
)

_add(
    _simple(
        "AE-804",
        "later-chunk-answers-with-an-obedient-model",
        "merge",
        answered=("Q1", "Q2", "Q3", "Q4", "Q5", "Q6"),
        per_page=1,
        model_quirks=("obeys-already-found",),
        notes="Chunk 1 returns an entry for every question, most of them "
        "not-found. The note for chunk 2 used to list ALL of those "
        "as 'already extracted', so a model that obeys it skipped "
        "Q4-Q6 - every answer on its own pages.",
        tags=("chunked", "merge", "answered"),
    )
)

# -- Split answers: the no-truncation requirement ---------------------------
# A student's answer must never be silently truncated because it crosses a
# chunk boundary. Each of these uses the literal-minded model, so the
# chunk note's own wording is what is under test.


def _answer_across_three_chunks() -> Scenario:
    """One answer running from page 3 (chunk 1) through page 7 (chunk 3)."""
    scenario_id = "AE-805"
    pages = (
        Page(lines=(question_line("Q1", BANK[0][1]), answer_line("Q1", BANK[0][2]))),
        Page(lines=(question_line("Q2", BANK[1][1]), answer_line("Q2", BANK[1][2]))),
        Page(
            lines=(
                question_line("Q3", BANK[2][1]),
                answer_line("Q3", "The main gas in the air"),
                Line("   (continued overleaf)", question="Q3"),
            )
        ),
        Page(lines=(Line("   (Q3 continued - rough working)", question="Q3"),)),
        Page(
            lines=(
                answer_line("Q3", "makes up about four fifths of it and"),
                Line("   (continued overleaf)", question="Q3"),
            )
        ),
        Page(
            lines=(Line("   (Q3 continued - sketch of the atmosphere)", question="Q3"),)
        ),
        Page(lines=(answer_line("Q3", "that gas is Nitrogen"),)),
        Page(lines=(question_line("Q4", BANK[3][1]), answer_line("Q4", BANK[3][2]))),
    )
    expectations = (
        Expectation("Q1", ANSWERED, BANK[0][2], (1,), BANK[0][1]),
        Expectation("Q2", ANSWERED, BANK[1][2], (2,), BANK[1][1]),
        Expectation(
            "Q3",
            ANSWERED,
            BANK[2][2],
            (3, 4, 5, 6, 7),
            BANK[2][1],
            fragments=(
                (3, "The main gas in the air"),
                (5, "makes up about four fifths of it and"),
                (7, "that gas is Nitrogen"),
            ),
        ),
        Expectation("Q4", ANSWERED, BANK[3][2], (8,), BANK[3][1]),
    )
    return Scenario(
        id=scenario_id,
        name="answer-spans-three-chunks",
        category="merge",
        document=Document(scenario_id, pages),
        expectations=expectations,
        questions=_questions_for(expectations),
        model_quirks=("obeys-already-found",),
        notes="Eight pages at 3 per chunk. Q3 is still 'already found' when "
        "chunks 2 and 3 run, so each must be asked for - and return - its "
        "part. All three parts must survive, in page order.",
        tags=("chunked", "cross-chunk", "merge", "split-answer"),
    )


def _continuation_shares_a_page() -> Scenario:
    """Page 4 holds the end of Q3, all of Q4, and a blank Q5."""
    scenario_id = "AE-806"
    pages = (
        Page(lines=(question_line("Q1", BANK[0][1]), answer_line("Q1", BANK[0][2]))),
        Page(lines=(question_line("Q2", BANK[1][1]), answer_line("Q2", BANK[1][2]))),
        Page(
            lines=(
                question_line("Q3", BANK[2][1]),
                answer_line("Q3", "The major component is"),
                Line("   (continued overleaf)", question="Q3"),
            )
        ),
        Page(
            lines=(
                Line("   ...continued from previous page", question="Q3"),
                answer_line("Q3", BANK[2][2]),
                question_line("Q4", BANK[3][1]),
                answer_line("Q4", BANK[3][2]),
                question_line("Q5", BANK[4][1]),
                *blank_answer_lines("Q5"),
            )
        ),
        Page(lines=(question_line("Q6", BANK[5][1]), answer_line("Q6", BANK[5][2]))),
    )
    expectations = (
        Expectation("Q1", ANSWERED, BANK[0][2], (1,), BANK[0][1]),
        Expectation("Q2", ANSWERED, BANK[1][2], (2,), BANK[1][1]),
        Expectation(
            "Q3",
            ANSWERED,
            BANK[2][2],
            (3, 4),
            BANK[2][1],
            fragments=((3, "The major component is"), (4, BANK[2][2])),
        ),
        Expectation("Q4", ANSWERED, BANK[3][2], (4,), BANK[3][1]),
        Expectation("Q5", BLANK, None, (4,), BANK[4][1]),
        Expectation("Q6", ANSWERED, BANK[5][2], (5,), BANK[5][1]),
        Expectation("Q9", NOT_FOUND_IN_DOCUMENT, None, (), BANK[8][1]),
    )
    return Scenario(
        id=scenario_id,
        name="continuation-shares-a-page-with-other-answers",
        category="merge",
        document=Document(scenario_id, pages),
        expectations=expectations,
        questions=_questions_for(expectations),
        model_quirks=("obeys-already-found",),
        notes="The continuation of Q3 sits above a full answer (Q4) and a "
        "deliberate blank (Q5) on the first page of chunk 2, with Q9 absent "
        "altogether. The continuation must reach Q3 and only Q3, and every "
        "other status must be untouched.",
        tags=("chunked", "cross-chunk", "merge", "split-answer", "status"),
    )


_add(_answer_across_three_chunks())
_add(_continuation_shares_a_page())

_add(
    _simple(
        "AE-807",
        "answer-ends-exactly-at-the-chunk-boundary",
        "merge",
        answered=("Q7", "Q8", "Q9", "Q11", "Q12"),
        per_page=1,
        model_quirks=("obeys-already-found",),
        notes="Q9's answer ends on the last page of chunk 1 and Q11 starts "
        "chunk 2. Chunk 2 is invited to continue Q7-Q9; with nothing of "
        "theirs on its pages, no continuation may be invented and nothing "
        "may be joined onto Q9.",
        tags=("chunked", "boundary", "merge", "split-answer"),
    )
)

SCENARIOS_BY_ID = {scenario.id: scenario for scenario in SCENARIOS}


def by_tag(tag: str) -> list[Scenario]:
    return [scenario for scenario in SCENARIOS if tag in scenario.tags]


def by_category(category: str) -> list[Scenario]:
    return [scenario for scenario in SCENARIOS if scenario.category == category]


def categories() -> list[str]:
    return sorted({scenario.category for scenario in SCENARIOS})
