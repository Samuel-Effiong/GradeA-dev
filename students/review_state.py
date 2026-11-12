"""
The teacher's review queue: what puts a submission in it, how high it
ranks, and what of its reasons a teacher is sent.

One place, so grading, an upload's flags, an unreadable paper and the R1
cleanup migration (0037) rank a submission the same way. No model imports:
the migration imports this module.

R1 (owner, 2026-11-12): the second AI's disagreement no longer puts a
submission in the queue. Its result stays in feedback['second_opinion'] for
our own checks (grading_eval, benchmark); its review entries, still on rows
graded before R1, are neither ranked nor sent to a teacher.
"""

from ai_processor.extraction_flags import ASSIGNMENT_LEVEL, LEVEL_OF, QUESTION_LEVEL
from ai_processor.extraction_flags import REVIEW_REASON_TYPE as EXTRACTION_FLAG
from ai_processor.extraction_flags import TIER_OF as EXTRACTION_FLAG_TIER

ANSWER_NOT_FOUND_REASON = "answer_not_found"
ANSWERS_UNREADABLE = "answers_unreadable"
#: `left_out` of an answers_unreadable reason when nothing could be read.
ALL_LEFT_OUT = "all"
AI_REPLY_CORRECTED = "ai_reply_corrected"

#: The reasons the second AI used to file (before R1). Kept on old rows for
#: the record; they rank nothing and a teacher is not sent them.
DISAGREEMENT_REASON_TYPES = frozenset(
    {"grader_disagreement", "second_opinion_unavailable"}
)

# Review-queue ordering. review_severity used to store the raw
# gap_fraction, which silently mis-ordered the queue: a disagreement was
# "critical" when the two graders were >= 2 rubric levels apart EVEN IF the
# point gap was small, and ordering by raw fraction buried it below a
# moderate one.
#
# The fix is a tier-weighted key: tier picks the band, gap_fraction only
# orders WITHIN a band. Bands are a third of the 0-1 range each, so a
# critical always outranks any moderate, which always outranks any
# borderline, and the value still fits the existing FloatField.
_TIER_BASE = {
    "critical": 2 / 3,
    "moderate": 1 / 3,
    "borderline": 0.0,
}
# Worst-first, for denormalising the per-submission review_tier.
_TIER_RANK = {"critical": 3, "moderate": 2, "borderline": 1}


def review_sort_key(tier, gap_fraction):
    """Tier-weighted 0-1 sort key for the review queue (see _TIER_BASE)."""
    # An unmeasurable gap is never treated as mild: it sorts mid-band
    # rather than at the bottom of it.
    fraction = 0.5 if gap_fraction is None else gap_fraction
    try:
        fraction = min(1.0, max(0.0, float(fraction)))
    except (TypeError, ValueError):
        fraction = 0.5
    # An unrecognised/missing tier is treated as moderate: never downgrade
    # what we can't measure.
    base = _TIER_BASE.get(tier, _TIER_BASE["moderate"])
    return round(base + fraction / 3, 6)


def worst_tier(tiers):
    """The most severe of `tiers`, or None."""
    ranked = [(_TIER_RANK.get(tier, 0), tier) for tier in tiers if tier]
    if not ranked:
        return None
    return max(ranked)[1]


def extraction_flag_tier(flag):
    """The tier of one upload flag ({kind, ...})."""
    return EXTRACTION_FLAG_TIER.get(flag.get("kind"), "moderate")


def _tier_and_key(reason):
    """(tier, sort key) of one stored reason, or None when it ranks
    nothing: a teacher's resolution, or the second AI's (R1)."""
    if not isinstance(reason, dict):
        return None
    kind = reason.get("type")
    if not kind or kind in DISAGREEMENT_REASON_TYPES:
        return None
    if kind in (ANSWER_NOT_FOUND_REASON, ANSWERS_UNREADABLE):
        # We may not have the student's work at all: always critical, and
        # always at the very top.
        return "critical", review_sort_key("critical", 1.0)
    if kind == EXTRACTION_FLAG:
        tier = extraction_flag_tier(reason)
        return tier, review_sort_key(tier, 1.0 if tier == "critical" else None)
    # ai_reply_corrected, and any reason this code does not know: moderate,
    # nothing measures how far off it may be.
    return "moderate", review_sort_key("moderate", None)


def review_state(reasons):
    """(review_severity, review_tier) for a submission's stored reasons:
    (None, None) when none of them puts it in the queue."""
    ranked = [
        found
        for found in map(_tier_and_key, reasons if isinstance(reasons, list) else [])
        if found
    ]
    if not ranked:
        return None, None
    return max(key for _, key in ranked), worst_tier([tier for tier, _ in ranked])


def in_queue(reasons):
    """True when any of the reasons puts the submission in the queue."""
    return review_state(reasons)[1] is not None


def reason_level(reason):
    """The level of one reason about the upload, as its flag says it: the
    paper ("assignment") or one question ("question"); None for a reason
    that is about neither."""
    kind = reason.get("type")
    if kind == EXTRACTION_FLAG:
        return LEVEL_OF.get(reason.get("kind"))
    if kind == ANSWERS_UNREADABLE:
        # The whole paper, or only some of its answers (a count).
        return (
            ASSIGNMENT_LEVEL
            if reason.get("left_out") == ALL_LEFT_OUT
            else QUESTION_LEVEL
        )
    if kind == ANSWER_NOT_FOUND_REASON:
        return QUESTION_LEVEL
    return None


def teacher_review_reasons(reasons):
    """The reasons as a teacher is sent them: without the second AI's
    (R1), each one about the upload carrying its level. None when nothing
    is left. The stored list is not changed."""
    if not isinstance(reasons, list):
        return reasons
    sent = []
    for reason in reasons:
        if isinstance(reason, dict):
            if reason.get("type") in DISAGREEMENT_REASON_TYPES:
                continue
            level = reason_level(reason)
            if level is not None:
                reason = {**reason, "level": level}
        sent.append(reason)
    return sent or None


def answers_unreadable(reasons):
    """Some or all of the stored answers could not be read (H-165): a
    question-level problem, or the whole paper's, and either one puts the
    submission in the teacher's "Needs review" (R1)."""
    return any(
        isinstance(reason, dict) and reason.get("type") == ANSWERS_UNREADABLE
        for reason in (reasons if isinstance(reasons, list) else [])
    )
