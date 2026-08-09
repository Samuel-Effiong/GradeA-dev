"""
Deterministic (Tier 0) grading for OBJECTIVE questions.

OBJECTIVE questions carry their own ground truth on the assignment — an
`options` array and a full-text `model_answer` — and are scored
all-or-nothing (see GRADING_ASSIGNMENT_PROMPT_4.txt "OBJECTIVE questions
have an empty rubric array — they are scored full-points-or-zero"). For
answers that match an option unambiguously, an LLM adds cost and a
nonzero error rate to what is a string comparison. This module does that
comparison in Python.

THE CORE SAFETY INVARIANT: this grader only CLAIMS a question it can
match unambiguously — it never forces a zero on doubt. Every uncertain
case (conflicting letter/text, a model_answer that isn't among the
options, paraphrased answers, multi-select, math equivalence) returns
AMBIGUOUS and is graded by the LLM exactly as before. Adding this tier
can therefore only remove error relative to the status quo; the worst
case for any individual question is today's behavior.

Pure functions only — no Django/ORM imports — so the matching logic is
unit-testable without a database. The pipeline glue lives in
ai_processor/services.py::_grade_student_submission_impl.
"""

import html as html_module
import re
import string
import unicodedata

# ── Match outcomes ────────────────────────────────────────────────────────
# Claimed outcomes (graded deterministically):
CORRECT = "CORRECT"
INCORRECT = "INCORRECT"
NOT_ATTEMPTED = "NOT_ATTEMPTED"
# Deferred outcomes (left to the LLM):
AMBIGUOUS = "AMBIGUOUS"  # objective, but not safely matchable
NOT_APPLICABLE = "NOT_APPLICABLE"  # not an OBJECTIVE question at all

CLAIMED_OUTCOMES = frozenset({CORRECT, INCORRECT, NOT_ATTEMPTED})

_TAG_RE = re.compile(r"<[^>]+>")
# A letter prefix must be a single letter followed by a real delimiter —
# ")", "]", ".", ":", or "-" — optionally wrapped in "(...)". Requiring the
# delimiter is what keeps "Berlin" from parsing as prefix "B" + "erlin".
_LETTER_PREFIX_RE = re.compile(r"^\(?([a-z])[)\].:\-]\s*")
# Tokens like "a", "b" used to detect multi-select answers ("A and C").
_MULTI_LETTER_RE = re.compile(r"\b([a-z])\b(?:\s*[,&+]\s*|\s+and\s+)\b([a-z])\b")

_TRUE_SYNONYMS = frozenset({"t", "true", "correct", "yes"})
_FALSE_SYNONYMS = frozenset({"f", "false", "incorrect", "no"})


def normalize_text(value):
    """
    Canonical form used for every comparison in this module. Order
    matters: tags must go before entity unescaping can't resurrect them,
    NFKC before casefold so compatibility characters (², ﬁ, full-width)
    fold consistently.
    """
    if value is None:
        return ""
    text = str(value)
    # Tags strip to EMPTY, not space: "H<sub>2</sub>O" must equal a
    # plain-text "H2O"/"H₂O" answer. Both sides of every comparison pass
    # through this same function, so block-tag word-merging (rare in
    # short option strings) affects both sides identically — and any
    # residual mismatch fails safe to AMBIGUOUS, never to a wrong grade.
    text = _TAG_RE.sub("", text)
    text = html_module.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.casefold()
    text = " ".join(text.split())
    return text.strip().rstrip(".,;").strip()


def split_letter_prefix(text):
    """
    ("b", "berlin") for "b) berlin"; ("b", "") for a bare "b" or "b.";
    (None, text) when there is no unambiguous letter prefix. Expects
    already-normalized text.
    """
    if not text:
        return None, ""
    # A bare single letter (optionally "b." → normalize_text already
    # stripped the trailing dot) is a pure letter answer.
    if len(text) == 1 and text in string.ascii_lowercase:
        return text, ""
    match = _LETTER_PREFIX_RE.match(text)
    if match:
        return match.group(1), text[match.end() :].strip()
    return None, text


class OptionIndex:
    """
    Normalized lookup over a question's options.

    Letters are resolved through the options' own embedded prefixes when
    they carry any ("A) H2O" style, common in extracted assignments) and
    positionally (A = options[0]) otherwise. When BOTH schemes exist and
    disagree for a letter, that letter is unresolvable (AMBIGUOUS) rather
    than guessed.
    """

    __slots__ = ("by_text", "by_letter", "texts")

    def __init__(self, by_text, by_letter, texts):
        self.by_text = by_text  # normalized body text -> option position
        self.by_letter = by_letter  # letter -> option position (or None if conflicted)
        self.texts = texts  # position -> normalized body text


def build_option_index(options):
    """Returns an OptionIndex, or None when the option set cannot support
    unambiguous matching (not a list, <2 options, an empty option, or two
    options that collide after normalization)."""
    if not isinstance(options, list) or len(options) < 2:
        return None

    texts = []
    embedded_letters = []
    for option in options:
        normalized = normalize_text(option)
        letter, body = split_letter_prefix(normalized)
        # An option that is itself a bare letter ("A") has no body to
        # match a full-text answer against — treat the letter as the body.
        if letter is not None and not body:
            body = letter
        embedded_letters.append(letter)
        texts.append(body if letter is not None else normalized)

    if any(not text for text in texts):
        return None
    if len(set(texts)) != len(texts):
        return None  # duplicate options after normalization: no unique match

    by_text = {text: position for position, text in enumerate(texts)}

    by_letter = {}
    for position in range(len(texts)):
        positional = string.ascii_lowercase[position] if position < 26 else None
        embedded = embedded_letters[position]
        for letter in {positional, embedded} - {None}:
            if letter in by_letter and by_letter[letter] != position:
                by_letter[letter] = None  # conflict: letter is unresolvable
            else:
                by_letter.setdefault(letter, position)

    return OptionIndex(by_text=by_text, by_letter=by_letter, texts=texts)


# resolve_to_option sentinel results
_NO_MATCH = object()
_AMBIGUOUS = object()


def resolve_to_option(answer_text, index):
    """
    Resolve normalized answer text to an option position, or _NO_MATCH /
    _AMBIGUOUS. Deliberately exact-match only — substring and fuzzy
    matching are the LLM's job, not this module's.
    """
    letter, body = split_letter_prefix(answer_text)

    body_position = index.by_text.get(body) if body else None

    if letter is not None:
        letter_position = index.by_letter.get(letter, _NO_MATCH)
        if letter_position is None:  # known letter, but conflicted mapping
            return _AMBIGUOUS
        if body:
            if letter_position is _NO_MATCH:
                # "z) berlin" — letter resolves nowhere; trust the body if
                # it matches exactly, otherwise nothing matched.
                return body_position if body_position is not None else _NO_MATCH
            if body_position is None:
                # Letter resolves but its accompanying text matches no
                # option ("b) londn" typo, or free text after a letter) —
                # the letter alone might be right, but the conflict makes
                # it unsafe to claim.
                return _AMBIGUOUS
            if body_position != letter_position:
                # "b) london" when b = berlin: letter and text point at
                # DIFFERENT options — the classic unsafe case.
                return _AMBIGUOUS
            return letter_position
        # Bare letter answer.
        if letter_position is _NO_MATCH:
            # Single letter that maps to no option. If it exactly equals
            # an option's text (a "T"/"F" bank), match that; else nothing.
            return body_position if body_position is not None else _NO_MATCH
        return letter_position

    if body_position is not None:
        return body_position
    return _NO_MATCH


def _true_false_positions(index):
    """(true_position, false_position) when the option set is exactly a
    True/False pair, else None."""
    if len(index.texts) != 2:
        return None
    lowered = list(index.texts)
    true_position = false_position = None
    for position, text in enumerate(lowered):
        if text in _TRUE_SYNONYMS:
            true_position = position
        elif text in _FALSE_SYNONYMS:
            false_position = position
    if true_position is None or false_position is None:
        return None
    return true_position, false_position


def _coerce_points(value):
    try:
        points = float(value)
    except (TypeError, ValueError):
        return None
    if points != points or points in (float("inf"), float("-inf")):  # NaN/inf
        return None
    return points


def match_objective_answer(question, answer_html):
    """
    The claim decision for one question. Returns one of CORRECT /
    INCORRECT / NOT_ATTEMPTED (claimed — safe to grade in Python) or
    AMBIGUOUS / NOT_APPLICABLE (deferred to the LLM).
    """
    if not isinstance(question, dict):
        return NOT_APPLICABLE
    if str(question.get("question_type", "")).strip().upper() != "OBJECTIVE":
        return NOT_APPLICABLE

    points = _coerce_points(question.get("points"))
    if points is None or points <= 0:
        return AMBIGUOUS  # don't deterministically award weird point values

    index = build_option_index(question.get("options"))
    if index is None:
        return AMBIGUOUS  # malformed/duplicate/too-few options

    # The answer key itself must resolve to exactly one option — a typo'd
    # or absent model_answer means we have no trustworthy ground truth.
    key_text = normalize_text(question.get("model_answer"))
    if not key_text:
        return AMBIGUOUS
    correct_position = resolve_to_option(key_text, index)
    if correct_position in (_NO_MATCH, _AMBIGUOUS):
        return AMBIGUOUS

    answer_text = normalize_text(answer_html)
    if not answer_text:
        # Unambiguous: nothing was answered. Mirrors the LLM pipeline's
        # own placeholder behavior for missing answers.
        return NOT_ATTEMPTED

    # Multi-select shapes ("a and c", "b, d") have special rules in the
    # grading prompt (all-or-nothing across several options) — defer.
    if _MULTI_LETTER_RE.search(answer_text):
        return AMBIGUOUS

    tf = _true_false_positions(index)
    if tf is not None:
        true_position, false_position = tf
        if answer_text in _TRUE_SYNONYMS:
            return CORRECT if correct_position == true_position else INCORRECT
        if answer_text in _FALSE_SYNONYMS:
            return CORRECT if correct_position == false_position else INCORRECT

    answered_position = resolve_to_option(answer_text, index)
    if answered_position is _AMBIGUOUS:
        return AMBIGUOUS
    if answered_position is _NO_MATCH:
        # Free text that isn't verbatim any option — could be a paraphrase
        # or a mathematically equivalent form. The LLM judges those.
        return AMBIGUOUS

    return CORRECT if answered_position == correct_position else INCORRECT


def build_objective_evaluation(question, answer_html, match):
    """
    A question_evaluation dict shaped identically to the LLM's output
    contract (GRADING_ASSIGNMENT_PROMPT_4.txt), so nothing downstream can
    tell the two apart. `graded_by: "deterministic"` marks provenance for
    the future eval loop.
    """
    if match not in CLAIMED_OUTCOMES:
        raise ValueError(f"Cannot build an evaluation for unclaimed match {match!r}")

    points = _coerce_points(question.get("points")) or 0
    model_answer = question.get("model_answer", "")
    max_points = int(points) if float(points).is_integer() else points

    if match == CORRECT:
        score = max_points
        level = "correct"
        rationale = "Deterministic match: the answer is exactly the correct option."
        feedback = "Correct — well done."
    elif match == INCORRECT:
        score = 0
        level = "incorrect"
        rationale = (
            "Deterministic match: the answer is a different option than the "
            "correct one."
        )
        feedback = f"Incorrect. The correct answer is: {model_answer}"
    else:  # NOT_ATTEMPTED
        score = 0
        level = "not_attempted"
        rationale = "No answer was submitted for this question."
        feedback = (
            f"This question was not attempted. The correct answer is: "
            f"{model_answer}"
        )

    return {
        "question_number": question.get("question_number"),
        "question_text": question.get("question_text", ""),
        "question_type": "OBJECTIVE",
        "max_points": max_points,
        "student_answer": answer_html or "",
        "model_answer": model_answer,
        "score_awarded": score,
        "level_achieved": level,
        # Shape parity with LLM evaluations (which carry mechanically
        # verified evidence — see ai_processor/evidence.py). A
        # deterministic grade's justification IS the answer-key match, so
        # its evidence is vacuously verified with no quotes needed.
        "evidence_quotes": [],
        "evidence_verified": True,
        "flag_for_review": None,
        "evaluation_rationale": rationale,
        "strengths": [],
        "weaknesses": [],
        "improvement_suggestions": [],
        "feedback_for_student": feedback,
        "graded_by": "deterministic",
    }
