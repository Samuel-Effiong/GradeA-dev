"""
Mechanical verification of grading evidence quotes.

The grading prompt (GRADING_ASSIGNMENT_PROMPT_5) requires the model to
justify every points-awarding evaluation with `evidence_quotes` —
verbatim spans copied from the student's answer. This module is the
mechanical check on that pointing: each quote must actually appear in
the answer, or it doesn't count. A grader forced to cite evidence whose
citations are string-matched cannot invent justifications — it is the
cheapest possible "second opinion", costing zero extra model calls.

Verification is normalization-tolerant but never fuzzy: quotes and
answers are canonicalized (HTML stripped, entities unescaped, unicode
folded, case/whitespace collapsed, smart punctuation straightened) and
then matched by EXACT substring. Paraphrases do not verify, by design.

Because HTML can sit either between words ("<p>end</p><p>Start") or
inside them ("<b>photo</b>synthesis"), stripping tags to a space fixes
one case and breaks the other. Both sides are therefore matched under
BOTH strippings (tags→space and tags→empty); a quote verifies if any
combination matches. False negatives fail safe — an unverified quote is
dropped or triggers a re-run, never a wrong grade.

Enforcement policy (see enforce_evidence):
- An evaluation awarding points on a non-empty answer must end up with
  at least one VERIFIED quote. Provided-but-unverifiable quotes are
  fabricated evidence; no quotes at all is unjustified scoring. Both are
  retryable rejections in "strict" mode.
- Awarding points to an EMPTY answer is always a rejection — that is
  hallucinated grading in its purest form, and evidence makes it
  mechanically detectable.
- Zero-score / not-attempted evaluations need no evidence (there is no
  award to justify); any quotes they do carry are still filtered.
- Deterministically-graded evaluations are exempt — their justification
  is the answer-key match itself (ai_processor/objective_grading.py).

Pure functions, no Django imports.
"""

import html as html_module
import math
import re
import unicodedata

# Modes for enforce_evidence, wired to settings.GRADING_EVIDENCE_ENFORCEMENT.
MODE_STRICT = "strict"  # violations are returned for the caller to reject on
MODE_LOG = "log"  # annotate evaluations only; never reject
MODE_OFF = "off"  # leave evaluations completely untouched

_TAG_RE = re.compile(r"<[^>]+>")
# Smart punctuation the model routinely "improves" while quoting.
_PUNCTUATION_MAP = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)


def _canonicalize(text, tag_replacement):
    text = _TAG_RE.sub(tag_replacement, text)
    text = html_module.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_PUNCTUATION_MAP)
    text = text.casefold()
    return " ".join(text.split())


def normalize_for_evidence(text):
    """
    Both canonical forms of a text: tags stripped to a space (protects
    word boundaries between block elements) and to nothing (protects
    words split by inline markup). Returns (spaced, joined).
    """
    if text is None:
        return "", ""
    raw = str(text)
    return _canonicalize(raw, " "), _canonicalize(raw, "")


def quote_appears_in_answer(quote, answer_html):
    """True when the quote appears verbatim in the answer under any
    combination of the two canonical forms. Empty quotes never verify."""
    quote_spaced, quote_joined = normalize_for_evidence(quote)
    if not quote_spaced and not quote_joined:
        return False
    answer_spaced, answer_joined = normalize_for_evidence(answer_html)
    if not answer_spaced and not answer_joined:
        return False
    for needle in {quote_spaced, quote_joined}:
        if not needle:
            continue
        if needle in answer_spaced or needle in answer_joined:
            return True
    return False


def _coerce_quotes(value):
    """The schema guarantees a list of strings, but legacy/synthetic
    inputs may carry anything — keep only non-empty strings."""
    if not isinstance(value, list):
        return []
    return [q for q in value if isinstance(q, str) and q.strip()]


def _coerce_score(value):
    if isinstance(value, bool):
        return 0.0
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(score) or math.isinf(score):
        return 0.0
    return score


def verify_evaluation_evidence(evaluation, answer_html):
    """
    Verify one evaluation's quotes against one student answer.

    Returns (verified_quotes, unverified_count).
    """
    quotes = _coerce_quotes(evaluation.get("evidence_quotes"))
    verified = [q for q in quotes if quote_appears_in_answer(q, answer_html)]
    return verified, len(quotes) - len(verified)


def enforce_evidence(evaluations, answer_html_by_key, *, mode=MODE_STRICT, key_fn=str):
    """
    Verify and annotate every LLM evaluation in place; return the list of
    violation descriptions (empty when everything is acceptable).

    Args:
        evaluations: parsed question_evaluation dicts (mutated in place).
        answer_html_by_key: {key_fn(question_number): answer_html} for the
            questions the model was asked to grade. A question absent from
            the map is treated as having an empty answer.
        mode: MODE_STRICT returns violations for the caller to raise on;
            MODE_LOG annotates but returns []; MODE_OFF does nothing.
        key_fn: question_number normalizer — pass
            AIProcessor._question_number_key so int/str numbering can
            never mis-join an answer to its evaluation.

    In strict mode a violation means the RESPONSE is rejected (the caller
    re-runs the model call), mirroring how completeness failures behave:
    fabricated or absent justification is treated exactly as seriously as
    a missing question.
    """
    if mode == MODE_OFF:
        return []

    violations = []

    for evaluation in evaluations:
        if not isinstance(evaluation, dict):
            continue
        if evaluation.get("graded_by") == "deterministic":
            continue

        number = evaluation.get("question_number")
        answer_html = answer_html_by_key.get(key_fn(number), "")
        answer_spaced, _ = normalize_for_evidence(answer_html)
        answer_is_empty = not answer_spaced

        provided = _coerce_quotes(evaluation.get("evidence_quotes"))
        verified, unverified_count = verify_evaluation_evidence(evaluation, answer_html)
        score = _coerce_score(evaluation.get("score_awarded"))

        # Annotate: only verified quotes survive; the drop is recorded.
        evaluation["evidence_quotes"] = verified
        if unverified_count:
            evaluation["unverified_evidence_count"] = unverified_count

        violation = None
        if score > 0 and answer_is_empty:
            violation = (
                f"question {number!r}: {score:g} point(s) awarded to an "
                f"empty/blank answer"
            )
        elif score > 0 and provided and not verified:
            violation = (
                f"question {number!r}: none of the {len(provided)} evidence "
                f"quote(s) appear in the student's answer (fabricated "
                f"evidence)"
            )
        elif score > 0 and not provided:
            violation = (
                f"question {number!r}: {score:g} point(s) awarded with no "
                f"evidence quotes"
            )

        evaluation["evidence_verified"] = violation is None

        if violation is not None and mode == MODE_STRICT:
            violations.append(violation)

    return violations
