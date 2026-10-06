"""
Cross-student consistency cache for LLM-graded questions.

Two students who submit byte-identical answers to the same question are,
today, two fully independent model calls. Temperature is pinned to 0, but
that only makes matching answers *usually* consistent — OpenRouter
fallback routing means it is not a guarantee (see MAIN_MODEL /
GRADING_FALLBACK_MODELS in services.py), and even a genuinely
deterministic model gives you no defence against the two calls landing
on different rubric levels for reasons that have nothing to do with the
student.

This module makes it a guarantee by construction instead: before an
LLM-eligible question+answer pair is sent to the model, look up a prior
evaluation for the EXACT same question content and the EXACT same answer
text. A hit is reused verbatim; a miss is graded normally and the result
is written back for the next identical submission. Two identical inputs
now always produce the identical output, because they're the same
lookup — not two separate model calls that merely tend to agree.

Pure content-addressing, no heuristics: the cache key is a hash of
everything that is SENT to the AI for one question (BE-I-04 slice B): the
whole question as serialised into the prompt, the student's answer text,
the assignment's title and instructions, the teacher's extra instructions
as spliced, the grading prompt's version and the grading settings'
version, plus the model that would be doing the grading. Edit any of them
and the key changes, so an edit can never serve a stale cached grade —
there is nothing to invalidate by hand. Before v2 the key held six fields
of the question and the answer only, and a teacher's edited instructions
were answered with grades made under the old ones for up to three days.

The answer as sent is its text, its `answer_status` and its
`transcription_notes` (a delta after the Checker's reading, 2026-10-06).

What the key does NOT hold, stated limits: the other questions of the
paper, the other answers, and the answer's place in a batch; and the
answer's `source_page`, `confidence` and own copy of `question_text`,
which differ from student to student for the same text.

What is stored is an envelope this module writes: the evaluation, and
beside it the model that answered. A reused answer is marked from the
envelope, never from a marker inside the evaluation.

Storage is whatever django.core.cache.cache resolves to (Redis in every
deployed environment, see AutoGrader/settings.py CACHES) with a TTL
(GRADING_ANSWER_CACHE_TTL_SECONDS) rather than permanent storage, so this
never becomes an unbounded store — just long enough to cover a
grade-all run across a whole class.

Deliberately NOT used for: deterministic-tier evaluations (already exact
by construction — caching them buys nothing), or second-opinion calls
(those exist specifically to be an independent read; consulting a cache
written by a DIFFERENT model would defeat the point). See
services.py::_partition_cached / _store_cache_evaluations for how the
grading pipeline wires this in, including the rule that a question whose
cached grade later drew a second-opinion disagreement is never written
to the cache in the first place — reusing a disputed grade for a future
student would silently spread an unresolved disagreement rather than
surfacing it for review again.
"""

import hashlib
import json
import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "grading_answer_cache"
# Bump this to invalidate every cached entry at once.
# v2 (BE-I-04 slice B): the key now holds everything that is sent to the AI
# for one question, and the stored value is an envelope. Entries written
# under v1 are never found again; they expire by their own lifetime.
CACHE_VERSION = "v2"


#: `graded_by` when the provider did not say which model answered.
UNNAMED_MODEL = "llm"


def _enabled():
    return bool(getattr(settings, "GRADING_ANSWER_CACHE_ENABLED", True))


def _ttl():
    return getattr(settings, "GRADING_ANSWER_CACHE_TTL_SECONDS", 60 * 60 * 24 * 3)


def _normalize_answer(answer_html):
    # Deliberately minimal: strip only. Two answers that differ by even a
    # single word must NOT collide into the same cache entry, so this
    # stays far short of the tag-stripping/casefolding normalization
    # objective_grading.py uses for its own, very different purpose
    # (matching against a fixed set of options). Whitespace-only edges are
    # the one difference safe to ignore, since they can never change
    # meaning.
    return (answer_html or "").strip()


@dataclass(frozen=True)
class MatchContext:
    """Everything at the level of the assignment and of the run that is
    sent to the AI with a question, fixed once per run.

    `custom_instructions` is the teacher's extra instructions AS SPLICED
    into the prompt: empty when the feature is switched off or the teacher
    wrote none, so an edit that changes nothing sent changes no key.
    """

    assignment_id: str
    assignment_title: str
    assignment_instructions: str
    custom_instructions: str
    prompt_version: str
    config_version: str

    @classmethod
    def for_run(cls, assignment_model, run, custom_instructions):
        return cls(
            assignment_id=str(getattr(assignment_model, "id", None) or ""),
            assignment_title=getattr(assignment_model, "title", None) or "",
            assignment_instructions=(
                getattr(assignment_model, "instructions", None) or ""
            ),
            custom_instructions=custom_instructions or "",
            prompt_version=run.prompt_version,
            config_version=run.config.version,
        )


def _question_as_sent(question):
    """The WHOLE question, as the grading prompt serialises it: every
    field, not a chosen few. A field added to questions later is part of
    the match without anyone remembering to list it here."""
    return json.dumps(question, sort_keys=True, default=str)


def _said(value):
    """What an answer's `answer_status` or `transcription_notes` says, for
    the key. "Nothing said" is one thing however it is written: a field
    that is missing, None, empty or only whitespace gives the same key.
    Outer whitespace is not compared; an inner difference is. A value that
    is not text is serialised, never refused: a grading must not fail over
    the key."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, sort_keys=True, default=str)


def build_cache_key(
    question,
    answer_html,
    *,
    model_name,
    context,
    answer_status=None,
    transcription_notes=None,
):
    """One key per (question as sent, answer as sent, assignment context,
    prompt version, settings version, intended model).

    The answer as sent is its text, its `answer_status` and its
    `transcription_notes`: two answers with the same text, one blank and
    one "not found in the document", or one clean and one "partly
    illegible", tell the AI different things and must not share a grade.

    STATED LIMITS. The key does not hold:
    * the other questions of the paper, the other answers, or the answer's
      place in a batch, all of which the AI also sees in the same call;
    * the answer's `source_page`, its `confidence`, or its own copy of
      `question_text`, which are sent too. They differ from student to
      student for the same text, so matching on them would end all reuse.

    The release is deliberately absent (it is recorded beside the settings
    version, never inside it), so a deploy does not empty the store.

    The parts are hashed as one JSON list, so no two neighbouring parts
    can run together and nothing inside a part can pass for a boundary.
    """
    parts = [
        CACHE_VERSION,
        model_name or "",
        context.assignment_id,
        context.prompt_version,
        context.config_version,
        context.assignment_title,
        context.assignment_instructions,
        context.custom_instructions,
        _question_as_sent(question),
        _normalize_answer(answer_html),
        _said(answer_status),
        _said(transcription_notes),
    ]
    digest = hashlib.sha256(json.dumps(parts).encode("utf-8"))
    return f"{CACHE_KEY_PREFIX}:{digest.hexdigest()}"


def _open_envelope(stored):
    """(evaluation, served_model) from a stored value, or None for anything
    that is not an envelope this module wrote. Never trusts a shape."""
    if not isinstance(stored, dict) or set(stored) != {"evaluation", "served_model"}:
        return None
    evaluation = stored["evaluation"]
    served_model = stored["served_model"]
    if not isinstance(evaluation, dict):
        return None
    if served_model is not None and not isinstance(served_model, str):
        return None
    return evaluation, served_model


def get_cached_evaluation(
    question,
    answer_html,
    *,
    model_name,
    context,
    answer_status=None,
    transcription_notes=None,
):
    """Returns the saved evaluation for reuse, or None on a miss, when the
    store is switched off, on a backend error, or when what is stored is
    not an envelope.

    The returned evaluation is a copy marked by OUR code: `from_cache` is
    True and `graded_by` is the envelope's `served_model` (or "llm" when
    the provider named none). Whatever markers sit inside the stored
    evaluation are overwritten.

    Never raises: a cache backend hiccup degrades to "grade it fresh",
    never to a failed submission.
    """
    if not _enabled():
        return None
    key = build_cache_key(
        question,
        answer_html,
        model_name=model_name,
        context=context,
        answer_status=answer_status,
        transcription_notes=transcription_notes,
    )
    try:
        stored = cache.get(key)
    except Exception:
        logger.exception(
            "[Grading] answer cache read failed — grading this question fresh."
        )
        return None
    opened = _open_envelope(stored)
    if opened is None:
        return None
    evaluation, served_model = opened
    reused = dict(evaluation)
    reused["from_cache"] = True
    reused["graded_by"] = served_model or UNNAMED_MODEL
    return reused


def store_evaluation(
    question,
    answer_html,
    evaluation,
    *,
    model_name,
    served_model,
    context,
    answer_status=None,
    transcription_notes=None,
):
    """Writes one evaluation to the cache, in an envelope with the model
    that answered beside it (None when the provider named none). Never
    raises."""
    if not _enabled():
        return
    key = build_cache_key(
        question,
        answer_html,
        model_name=model_name,
        context=context,
        answer_status=answer_status,
        transcription_notes=transcription_notes,
    )
    kept = {k: v for k, v in evaluation.items() if k != "from_cache"}
    envelope = {"evaluation": kept, "served_model": served_model}
    try:
        cache.set(key, envelope, timeout=_ttl())
    except Exception:
        logger.exception(
            "[Grading] answer cache write failed — continuing without caching "
            "this evaluation."
        )
