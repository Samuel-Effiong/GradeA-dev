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

LaTeX-cosmetic desugaring (see _desugar_latex): a live benchmark run
found that on LaTeX-heavy answers the model routinely re-typesets its
own quote while composing it — turning `$H_2$` into "H2", `\frac{a}{b}`
into "a/b", `\rightarrow` into "→" — which is faithful reproduction of
the MEANING but fails a literal string match. 21 real graded submissions
were used to separate "the model reformatted the same fact" from "the
model invented a fact": subscript/superscript markers, `\text{}`-style
wrappers, `$`/`$$` delimiters, and a fixed list of 1:1 symbol synonyms
(arrows, Δ, ×, etc.) are folded before comparison, on both quote and
answer, in both directions — so it does not matter which side used which
notation. `\frac{a}{b}` is expanded to "a/b" only when neither operand
has a top-level `+` or binary `-` (i.e. only where the division reads
unambiguously without the fraction bar); a numerator or denominator
containing one is left untouched; equating "(a+1)/b" with the ambiguous
"a+1/b" would be a genuine rewrite, not decoration, and this module's
whole purpose is refusing to guess. A "..." or "…" inside a quote is
still never accepted as one span — see EVIDENCE in
GRADING_ASSIGNMENT_PROMPT_5 — but existing quotes that do it anyway are
split into fragments and each is verified independently, since that is
what the ellipsis already claims: several separate excerpts, not one.

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
# U+2212 (MINUS SIGN) is included because NFKC decomposes the superscript
# minus U+207B into it -- a charge like "Nu^-" and the model's own "Nu⁻"
# would otherwise land on two different minus-like codepoints.
_PUNCTUATION_MAP = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
        " ": " ",
    }
)

_ELLIPSIS_RE = re.compile(r"\s*(?:\.\.\.|…)\s*")

# --- LaTeX-cosmetic desugaring -------------------------------------------
_DOLLAR_RE = re.compile(r"\${1,2}")
_SLASH_SPACING_RE = re.compile(r"\s*/\s*")
_TEXT_WRAPPER_RE = re.compile(
    r"\\(?:text|mathrm|mathbf|mathit|operatorname)\{([^{}]*)\}"
)
_SUBSCRIPT_BRACED_RE = re.compile(r"_\{([^{}]*)\}")
_SUBSCRIPT_BARE_RE = re.compile(r"_([A-Za-z0-9])")
_SUPERSCRIPT_BRACED_RE = re.compile(r"\^\{([^{}]*)\}")
_SUPERSCRIPT_BARE_RE = re.compile(r"\^([A-Za-z0-9+-])")
_UNICODE_SUPERSCRIPT_MAP = str.maketrans(
    {
        "⁰": "0",
        "¹": "1",
        "²": "2",
        "³": "3",
        "⁴": "4",
        "⁵": "5",
        "⁶": "6",
        "⁷": "7",
        "⁸": "8",
        "⁹": "9",
        "⁻": "-",
        "⁺": "+",
    }
)
_UNICODE_SUBSCRIPT_MAP = str.maketrans(
    {
        "₀": "0",
        "₁": "1",
        "₂": "2",
        "₃": "3",
        "₄": "4",
        "₅": "5",
        "₆": "6",
        "₇": "7",
        "₈": "8",
        "₉": "9",
    }
)
# 1:1 LaTeX command <-> glyph synonyms with zero semantic ambiguity -- a
# \rightarrow can only ever mean the arrow. Deliberately NOT here: \frac
# (see _expand_safe_fracs), \sin/\ln/\log and similar function names (a
# dropped backslash there is a different, unobserved failure mode -- not
# fixing hypothetical problems), and anything where the LaTeX and a
# plausible plain-text rendering could disagree on meaning.
_LATEX_SYMBOLS = (
    (r"\rightarrow", "→"),
    (r"\to", "→"),
    (r"\Rightarrow", "→"),
    (r"\Delta", "Δ"),
    (r"\delta", "δ"),
    (r"\times", "×"),
    (r"\cdot", "·"),
    (r"\pm", "±"),
    (r"\leq", "≤"),
    (r"\geq", "≥"),
    (r"\approx", "≈"),
)
# A LaTeX command name is a decorative artifact of macro syntax, not part
# of the mathematics -- "\Delta H" and "\DeltaH" both just mean "the
# change in H", and models are inconsistent about which they reproduce
# when de-lexing. Once the command is folded to its glyph, the ONE space
# that could only ever have been the macro's name-terminator is stripped
# on both sides so both spellings converge, regardless of which one the
# model happened to use for this particular quote.
_GLYPH_TRAILING_SPACE_RE = re.compile(r"(?<=[→Δδ×·±≤≥≈])\s+")


def _match_braced_group(text, open_brace_index):
    """
    text[open_brace_index] is a '{'; return (inner_text, index_after_the_
    matching_'}'), or (None, open_brace_index) if it never closes.
    Handles nested braces so `\\frac{a}{\\frac{b}{c}}` scans correctly.
    """
    depth = 0
    for i in range(open_brace_index, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index + 1 : i], i + 1
    return None, open_brace_index


def _is_safe_frac_operand(operand):
    """
    True when `operand` has no TOP-LEVEL '+' or binary '-' -- i.e.
    dropping the fraction bar around it cannot change how it reads.
    "(a+1)/b" is unambiguous; "a+1/b" is not, since normal order of
    operations reads that as "a + (1/b)". A leading '-' is a sign, not
    an operator, and is exempt.
    """
    depth = 0
    for index, char in enumerate(operand):
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth -= 1
        elif depth == 0 and char == "+":
            return False
        elif depth == 0 and char == "-" and index != 0:
            return False
    return True


def _expand_safe_fracs(text):
    """
    `\\frac{a}{b}` -> "a/b", only where that reading is unambiguous (see
    _is_safe_frac_operand). An eligible fraction nested inside another is
    expanded too; an ineligible one is left completely untouched,
    `\\frac` and all, rather than guessed at.
    """
    out = []
    i, n = 0, len(text)
    token = "\\frac"
    while i < n:
        if (
            text.startswith(token, i)
            and i + len(token) < n
            and text[i + len(token)] == "{"
        ):
            numerator, after_num = _match_braced_group(text, i + len(token))
            if numerator is not None and after_num < n and text[after_num] == "{":
                denominator, after_den = _match_braced_group(text, after_num)
                if (
                    denominator is not None
                    and _is_safe_frac_operand(numerator)
                    and _is_safe_frac_operand(denominator)
                ):
                    out.append(_expand_safe_fracs(numerator))
                    out.append("/")
                    out.append(_expand_safe_fracs(denominator))
                    i = after_den
                    continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _desugar_latex(text):
    text = _expand_safe_fracs(text)
    text = _TEXT_WRAPPER_RE.sub(r"\1", text)
    text = _SUBSCRIPT_BRACED_RE.sub(r"\1", text)
    text = _SUBSCRIPT_BARE_RE.sub(r"\1", text)
    text = _SUPERSCRIPT_BRACED_RE.sub(r"\1", text)
    text = _SUPERSCRIPT_BARE_RE.sub(r"\1", text)
    text = text.translate(_UNICODE_SUPERSCRIPT_MAP)
    text = text.translate(_UNICODE_SUBSCRIPT_MAP)
    for latex, glyph in _LATEX_SYMBOLS:
        text = text.replace(latex, glyph)
    text = _GLYPH_TRAILING_SPACE_RE.sub("", text)
    text = _DOLLAR_RE.sub("", text)
    text = _SLASH_SPACING_RE.sub("/", text)
    return text


def _canonicalize(text, tag_replacement):
    text = _TAG_RE.sub(tag_replacement, text)
    text = html_module.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = _desugar_latex(text)
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


def _quote_span_appears_in_answer(quote, answer_html):
    """The single-span check: does this exact text appear verbatim,
    under either tag-stripping strategy? No ellipsis handling here —
    see quote_appears_in_answer for that."""
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


def quote_appears_in_answer(quote, answer_html):
    """
    True when the quote appears verbatim in the answer under any
    combination of the two canonical forms. Empty quotes never verify.

    The grading prompt forbids joining separate excerpts with "..." —
    a quote is supposed to be one continuous span (see EVIDENCE in
    GRADING_ASSIGNMENT_PROMPT_5) — but models do it anyway often enough
    that a quote containing "..." or "…" is treated as what it's
    actually claiming: several separate excerpts, not one contiguous
    span. Each non-empty fragment must independently verify; this is
    strictly a compatibility read of what an existing "..." already
    means, not a new leniency; a quote WITHOUT an ellipsis is still
    exactly as strict a single-span check as before.
    """
    if _ELLIPSIS_RE.search(quote):
        fragments = [f for f in _ELLIPSIS_RE.split(quote) if f.strip()]
        return bool(fragments) and all(
            _quote_span_appears_in_answer(fragment, answer_html)
            for fragment in fragments
        )
    return _quote_span_appears_in_answer(quote, answer_html)


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
