"""
Ground truth for the ASSIGNMENT-extraction benchmark.

WHY A ROUND TRIP

Every case here is defined by the questions we EXPECT, not by a document.
The document is generated FROM those questions through the pipeline's own
renderer (AssignmentProcessingService.format_assignment_standard_html ->
html_to_prosemirror_text), which is byte-for-byte how a real assignment
reaches the editor and how the editor hands it back on an edit.

That makes each case a fidelity test of the loop teachers actually live
in: *if a teacher opens this assignment and saves it, do they get the same
assignment back?* Today that loop is the ONLY way to edit an assignment -
the frontend editor is free-form and resends the whole document, so every
edit is a full AI re-extraction - which makes round-trip fidelity the
single property the editing experience rests on.

WHAT THE CASES ARE FOR

Each one pins a specific way the extraction prompt is known or suspected
to lose teacher intent:

  six_level_rubric   The prompt says "rubric: Array of 4 performance
                     levels" (ASSIGNMENT_EXTRACTION_PROMPT_4_PROSE line
                     642) while also saying "parse each data row" (line
                     434). A teacher who adds levels 5 and 6 is betting on
                     the second instruction. This case settles which wins.

  custom_level_names The same prompt says "Always use lowercase level
                     names: excellent, good, fair, poor" (line 647) -
                     an instruction to RENAME a teacher's own levels.

  six_option_mcq     Options carry no count rule anywhere in the prompt,
                     so this should pass. It is here to catch a
                     REGRESSION if someone ever adds one.

  no_rubric          The one case where generating exactly 4 levels IS
                     correct: an open-ended question that arrived with no
                     rubric at all.

  latex_maths        Math notation survives HTML -> ProseMirror -> HTML.

  mixed_hybrid       Objective and open-ended in one assignment, where
                     the empty-rubric/empty-options rules for each type
                     have to hold simultaneously.

Everything here is plain data, no Django, no network - so the expectations
are checkable (see iter_extraction_dataset_errors) without a database.
"""

from dataclasses import dataclass, field

#: Level names outside the prompt's canonical set. Their survival is the
#: whole point of the custom_level_names case.
CUSTOM_LEVELS = ("outstanding", "proficient", "developing", "beginning", "absent")


@dataclass(frozen=True)
class ExtractionCase:
    """One assignment, and what re-extracting it must give back."""

    key: str
    title: str
    instructions: str
    questions: list = field(default_factory=list)
    #: Free text: what this case is guarding, read by whoever is staring
    #: at a benchmark failure at 2am.
    guards: str = ""
    #: Fields whose exact preservation is asserted. Keys not listed are
    #: scored but never gate the run - extraction legitimately rewrites
    #: prose (question_text is re-emitted as HTML), and demanding
    #: byte-equality there would make the benchmark fail on nothing.
    strict_fields: tuple = ("question_type", "points", "option_count", "rubric_levels")

    @property
    def total_points(self):
        return sum(q["points"] for q in self.questions)

    def as_assignment(self):
        """The dict shape format_assignment_standard_html consumes."""
        return {
            "title": self.title,
            "instructions": self.instructions,
            "total_points": self.total_points,
            "questions": [dict(q) for q in self.questions],
        }

    def question(self, number):
        for question in self.questions:
            if question["question_number"] == number:
                return question
        raise KeyError(f"{self.key} has no question {number}")


def _rubric(*pairs):
    """(level, points) pairs -> rubric dicts with usable descriptions.

    Descriptions are deliberately distinct and specific: a rubric whose
    levels all read alike gives the model nothing to choose between, and
    a benchmark built on one would measure our fixture rather than the
    extraction.
    """
    return [
        {
            "level": level,
            "points": points,
            "description": f"<p>{description}</p>",
        }
        for level, points, description in pairs
    ]


def _open(number, text, points, rubric, blooms="Analyze", qtype="SHORT-ANSWER"):
    return {
        "question_number": number,
        "question_text": f"<p>{text}</p>",
        "question_type": qtype,
        "question_image": "",
        "points": points,
        "blooms_level": blooms,
        "options": [],
        "rubric": rubric,
        "model_answer": "<p>A complete, correct response.</p>",
    }


def _objective(number, text, points, options, answer, blooms="Remember"):
    return {
        "question_number": number,
        "question_text": f"<p>{text}</p>",
        "question_type": "OBJECTIVE",
        "question_image": "",
        "points": points,
        "blooms_level": blooms,
        "options": list(options),
        "rubric": [],
        "model_answer": f"<p>{answer}</p>",
    }


# ── Case 1: a rubric with more than four levels ──────────────────────────
SIX_LEVEL_RUBRIC = ExtractionCase(
    key="six_level_rubric",
    title="Cell Biology — Extended Response",
    instructions="<p>Answer in full sentences.</p>",
    guards=(
        "A teacher-authored SIX-level rubric must come back with six "
        "levels and the same points ladder. The extraction prompt "
        "simultaneously instructs 'Array of 4 performance levels' and "
        "'parse each data row'; this is the case that decides which one "
        "the model actually follows."
    ),
    questions=[
        _open(
            1,
            "Explain the role of the mitochondrion in cellular respiration.",
            10,
            _rubric(
                (
                    "excellent",
                    10,
                    "Names oxidative phosphorylation, the electron transport chain AND the ATP yield.",
                ),
                (
                    "very good",
                    8,
                    "Names oxidative phosphorylation and the electron transport chain, no ATP figures.",
                ),
                (
                    "good",
                    6,
                    "States that the mitochondrion produces ATP during respiration, no mechanism.",
                ),
                (
                    "fair",
                    4,
                    "States that the mitochondrion produces energy, no terminology.",
                ),
                (
                    "weak",
                    2,
                    "Identifies it as an organelle, links it only vaguely to energy.",
                ),
                ("poor", 0, "Incorrect, irrelevant, or absent."),
            ),
        ),
    ],
)

# ── Case 2: level names outside the canonical four ───────────────────────
CUSTOM_LEVEL_NAMES = ExtractionCase(
    key="custom_level_names",
    title="History — Source Analysis",
    instructions="<p>Use the sources provided.</p>",
    guards=(
        "Level NAMES a teacher chose must survive. The prompt says "
        "'Always use lowercase level names: excellent, good, fair, poor', "
        "which is an instruction to rename someone else's rubric."
    ),
    strict_fields=(
        "question_type",
        "points",
        "option_count",
        "rubric_levels",
        "rubric_level_names",
    ),
    questions=[
        _open(
            1,
            "Assess the reliability of Source A as evidence of public opinion.",
            20,
            _rubric(
                (
                    "outstanding",
                    20,
                    "Weighs provenance, purpose and audience, with sustained cross-reference.",
                ),
                (
                    "proficient",
                    15,
                    "Weighs provenance and purpose, some cross-reference.",
                ),
                (
                    "developing",
                    10,
                    "Describes the source and asserts reliability without weighing it.",
                ),
                ("beginning", 5, "Paraphrases the source with no evaluation."),
                ("absent", 0, "No relevant response."),
            ),
            blooms="Evaluate",
            qtype="ESSAY",
        ),
    ],
)

# ── Case 3: more options than the usual four ─────────────────────────────
SIX_OPTION_MCQ = ExtractionCase(
    key="six_option_mcq",
    title="Cell Biology — Multiple Choice",
    instructions="<p>Choose one answer per question.</p>",
    guards=(
        "Six options must come back as six, with their text intact and "
        "NO leading letter labels (the renderer adds those from position; "
        "a stored label produces 'A. A) ...'). Options carry no count rule "
        "in the prompt, so this case exists to catch a regression if one "
        "is ever introduced."
    ),
    questions=[
        _objective(
            1,
            "Which organelle synthesises proteins?",
            5,
            [
                "Ribosome",
                "Lysosome",
                "Golgi apparatus",
                "Smooth endoplasmic reticulum",
                "Peroxisome",
                "Centriole",
            ],
            "Ribosome",
        ),
    ],
)

# ── Case 4: no rubric supplied — generation IS correct here ──────────────
NO_RUBRIC = ExtractionCase(
    key="no_rubric",
    title="Geography — Short Answer",
    instructions="<p>Answer briefly.</p>",
    guards=(
        "An open-ended question that arrives WITHOUT a rubric is the one "
        "case where inventing four levels is the right behaviour. Pinning "
        "it stops a fix for the six-level case from swinging too far and "
        "leaving genuinely rubric-less questions ungradeable."
    ),
    strict_fields=("question_type", "points", "option_count"),
    questions=[
        {
            "question_number": 1,
            "question_text": "<p>Describe two causes of coastal erosion.</p>",
            "question_type": "SHORT-ANSWER",
            "question_image": "",
            "points": 8,
            "blooms_level": "Understand",
            "options": [],
            "rubric": [],
            "model_answer": "<p>Hydraulic action and abrasion.</p>",
        }
    ],
)

# ── Case 5: LaTeX through the HTML/ProseMirror round trip ────────────────
LATEX_MATHS = ExtractionCase(
    key="latex_maths",
    title="Algebra — Quadratics",
    instructions="<p>Show all working.</p>",
    guards=(
        "Math delimiters must survive HTML -> ProseMirror -> HTML. A "
        "broken delimiter turns a formula into visible garbage in the "
        "student's paper and in the grading prompt alike."
    ),
    questions=[
        _open(
            1,
            r"Solve $x^2 - 5x + 6 = 0$ and verify one root by substitution.",
            10,
            _rubric(
                ("excellent", 10, r"Both roots found ($x=2$, $x=3$) and one verified."),
                ("good", 7, "Both roots found, no verification."),
                (
                    "fair",
                    4,
                    "One root found, or correct method with an arithmetic slip.",
                ),
                ("poor", 0, "No usable working."),
            ),
            blooms="Apply",
        ),
    ],
)

# ── Case 6: both question types in one assignment ────────────────────────
MIXED_HYBRID = ExtractionCase(
    key="mixed_hybrid",
    title="Physics — Mixed Paper",
    instructions="<p>Answer all questions.</p>",
    guards=(
        "OBJECTIVE questions must come back with options and an EMPTY "
        "rubric; open-ended ones with a rubric and NO options. The two "
        "rules have to hold at once, in one document, without bleeding "
        "into each other."
    ),
    questions=[
        _objective(
            1,
            "What is the SI unit of force?",
            2,
            ["Newton", "Joule", "Watt", "Pascal"],
            "Newton",
        ),
        _open(
            2,
            "Explain why a body in circular motion is accelerating.",
            8,
            _rubric(
                (
                    "excellent",
                    8,
                    "Velocity is a vector; direction changes, so acceleration is non-zero.",
                ),
                (
                    "good",
                    5,
                    "States direction changes without naming velocity as a vector.",
                ),
                ("fair", 2, "Asserts acceleration without explanation."),
                ("poor", 0, "Incorrect or absent."),
            ),
            blooms="Understand",
        ),
        _objective(
            3,
            "Which quantity is scalar?",
            2,
            ["Speed", "Velocity", "Force", "Momentum"],
            "Speed",
        ),
    ],
)


EXTRACTION_CASES = [
    SIX_LEVEL_RUBRIC,
    CUSTOM_LEVEL_NAMES,
    SIX_OPTION_MCQ,
    NO_RUBRIC,
    LATEX_MATHS,
    MIXED_HYBRID,
]
EXTRACTION_CASES_BY_KEY = {case.key: case for case in EXTRACTION_CASES}

VALID_QUESTION_TYPES = {"OBJECTIVE", "ESSAY", "SHORT-ANSWER"}
VALID_BLOOMS = {
    "Remember",
    "Understand",
    "Apply",
    "Analyze",
    "Evaluate",
    "Create",
}


def iter_extraction_dataset_errors():
    """
    Self-validation, mirroring dataset.iter_dataset_errors.

    A benchmark whose OWN ground truth is malformed reports failures that
    belong to the fixture, and the natural response to those is to relax
    the assertion - which is how an instrument quietly stops measuring.
    """
    seen_keys = set()
    for case in EXTRACTION_CASES:
        where = f"{case.key}"

        if case.key in seen_keys:
            yield f"{where}: duplicate case key."
        seen_keys.add(case.key)

        if not case.questions:
            yield f"{where}: has no questions."
        if not case.guards.strip():
            yield f"{where}: has no `guards` note explaining what it protects."

        numbers = [q.get("question_number") for q in case.questions]
        if len(set(numbers)) != len(numbers):
            yield f"{where}: duplicate question_number in {numbers}."

        for question in case.questions:
            number = question.get("question_number")
            spot = f"{where} Q{number}"

            qtype = question.get("question_type")
            if qtype not in VALID_QUESTION_TYPES:
                yield f"{spot}: invalid question_type {qtype!r}."

            blooms = question.get("blooms_level")
            if blooms not in VALID_BLOOMS:
                yield f"{spot}: invalid blooms_level {blooms!r}."

            points = question.get("points")
            if not isinstance(points, (int, float)) or points <= 0:
                yield f"{spot}: points must be a positive number, got {points!r}."

            options = question.get("options") or []
            rubric = question.get("rubric") or []

            if qtype == "OBJECTIVE":
                if rubric:
                    yield f"{spot}: OBJECTIVE questions must have an empty rubric."
                if len(options) < 2:
                    yield f"{spot}: OBJECTIVE needs at least 2 options."
                # The renderer derives letters from position; a label baked
                # into the text renders twice ("A. A) ...").
                for option in options:
                    stripped = str(option).strip()
                    if (
                        len(stripped) > 1
                        and stripped[0].isalpha()
                        and stripped[1] in ")."
                    ):
                        yield f"{spot}: option {option!r} carries a leading letter label."
            else:
                if options:
                    yield f"{spot}: open-ended questions must have no options."

            if rubric:
                points_ladder = [level.get("points") for level in rubric]
                if points_ladder != sorted(points_ladder, reverse=True):
                    yield (f"{spot}: rubric points must descend, got {points_ladder}.")
                if points_ladder[0] != points:
                    yield (
                        f"{spot}: top rubric level is {points_ladder[0]}, but the "
                        f"question is worth {points}."
                    )
                if len({level.get("level") for level in rubric}) != len(rubric):
                    yield f"{spot}: duplicate rubric level names."
                for level in rubric:
                    if not (level.get("description") or "").strip():
                        yield f"{spot}: rubric level {level.get('level')!r} has no description."
