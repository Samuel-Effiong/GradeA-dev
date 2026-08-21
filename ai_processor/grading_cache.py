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
everything that determines the correct grade (the question's text, type,
points, options, rubric and model_answer) plus the student's answer text
plus the model that would be doing the grading. Edit a question's rubric
and its fingerprint changes, so a rubric edit can never serve a stale
cached grade — there is nothing to invalidate by hand.

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

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "grading_answer_cache"
# Bump this to invalidate every cached entry at once (e.g. after a
# grading-prompt rewrite changes how a given input should be scored).
CACHE_VERSION = "v1"


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


def _question_fingerprint(question):
    """Canonical content that determines the correct grade for this
    question, independent of key ordering or unrelated fields like
    blooms_level/additional_notes."""
    payload = {
        "question_text": question.get("question_text", ""),
        "question_type": question.get("question_type", ""),
        "points": question.get("points"),
        "options": question.get("options") or [],
        "rubric": question.get("rubric") or [],
        "model_answer": question.get("model_answer", ""),
    }
    return json.dumps(payload, sort_keys=True, default=str)


def build_cache_key(question, answer_html, *, model_name, assignment_id=None):
    digest = hashlib.sha256()
    for part in (
        CACHE_VERSION,
        model_name or "",
        str(assignment_id or ""),
        _question_fingerprint(question),
        _normalize_answer(answer_html),
    ):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return f"{CACHE_KEY_PREFIX}:{digest.hexdigest()}"


def get_cached_evaluation(question, answer_html, *, model_name, assignment_id=None):
    """Returns the stored evaluation dict, or None on a miss/disabled/error.

    Never raises — a cache backend hiccup degrades to "grade it fresh",
    never to a failed submission.
    """
    if not _enabled():
        return None
    key = build_cache_key(
        question, answer_html, model_name=model_name, assignment_id=assignment_id
    )
    try:
        return cache.get(key)
    except Exception:
        logger.exception(
            "[Grading] answer cache read failed — grading this question fresh."
        )
        return None


def store_evaluation(
    question, answer_html, evaluation, *, model_name, assignment_id=None
):
    """Writes one evaluation to the cache. Never raises."""
    if not _enabled():
        return
    key = build_cache_key(
        question, answer_html, model_name=model_name, assignment_id=assignment_id
    )
    stored = dict(evaluation)
    stored["from_cache"] = True
    try:
        cache.set(key, stored, timeout=_ttl())
    except Exception:
        logger.exception(
            "[Grading] answer cache write failed — continuing without caching "
            "this evaluation."
        )
