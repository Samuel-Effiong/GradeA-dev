"""One reading of the grading settings, and its version (BE-I-04).

A grade's label says which combination of grading settings was switched on
when it was made. `GradingConfig.read()` reads every setting that can change
a grade ONCE; `version` turns that reading into one short code. If any of
them changes, the code changes.

FIRST FORM OF THE GRADING RECORD. A later stage improves on it: a table of
grading runs, built beside re-grading or feedback editing and filled from
these fields (see students/grading_label.py).

Read once per grading run. A run is several AI calls and can take minutes;
a setting changed half-way must not label a grade with settings it was not
made under, and the saved-answer lookup and store of one run must agree.
The caller takes one reading at the start and passes it on.

What the version covers, and what it does not:

* GRADE_SHAPING_SETTINGS, read from Django settings, and the code constants
  in CODE_CONSTANTS, read from ai_processor.services.
* NOT the saved-answer store's switch and lifetime: they decide whether an
  answer is reused, not what a grade is. Listed in NOT_GRADE_SHAPING so the
  omission is a decision and not an oversight.
* The temperature of the provider calls (`AI_TEMPERATURE`), since slice B
  (SM ruling, 2026-10-06). The release does not stand in for it: a release
  changes on every deploy, so it cannot say that a grade moved because the
  temperature moved.
* NOT what is written directly in the code: the instruction text outside
  the versioned prompt file, the reply schemas, the retry counts. No list
  can cover those. `release` is the backstop: it names the release that
  was running. It is recorded BESIDE the version and is never
  part of it, so the same settings give the same version on every release,
  and a deploy never empties the saved-answer store.

The version uses a colon. Audit metadata drops anything shaped like an
email address, so a version written with "@" would silently vanish.

Import-light on purpose: ai_processor.services is imported inside the one
function that needs its constants.
"""

import hashlib
import json
from dataclasses import dataclass

from django.conf import settings

#: Every Django setting that can change a grade or its review flags.
#: ai_processor/tests_grading_config.py fails when a GRADING_* setting is
#: added to settings.py and is in neither this tuple nor NOT_GRADE_SHAPING.
GRADE_SHAPING_SETTINGS = (
    "GRADING_CUSTOM_INSTRUCTIONS_ENABLED",
    "GRADING_DETERMINISTIC_OBJECTIVE",
    "GRADING_DISAGREEMENT_CRITICAL_FRACTION",
    "GRADING_DISAGREEMENT_MODERATE_FRACTION",
    "GRADING_EVIDENCE_ENFORCEMENT",
    "GRADING_MAX_IMAGES_PER_CALL",
    "GRADING_RESPONSE_SCHEMA_ENABLED",
    "GRADING_SECOND_OPINION_ENABLED",
    "GRADING_SECOND_OPINION_HIGH_POINTS",
    "GRADING_SECOND_OPINION_MIN_CONFIDENCE",
    "GRADING_SECOND_OPINION_MODELS",
    "GRADING_SECOND_OPINION_ON_BORDERLINE",
    "GRADING_SECOND_OPINION_SAMPLE_RATE",
    "GRADING_SECOND_OPINION_SUBJECTIVE_TYPES",
)

#: GRADING_* settings deliberately left out of the version, with the reason.
NOT_GRADE_SHAPING = {
    "GRADING_ANSWER_CACHE_ENABLED": "whether a saved answer is reused, not what a grade is",
    "GRADING_ANSWER_CACHE_TTL_SECONDS": "how long a saved answer is kept",
    "GRADING_RELEASE_ID": "recorded beside the version, never inside it",
}

#: Module-level names in ai_processor.services that shape a grade.
CODE_CONSTANTS = (
    "AI_CONFIDENCE_THRESHOLD",
    "AI_TEMPERATURE",
    "GRADING_FALLBACK_MODELS",
    "GRADING_QUESTIONS_PER_CHUNK",
    "MAIN_MODEL",
)

VERSION_PREFIX = "cfg:"
HASH_LENGTH = 12
#: grading_release is a CharField(64).
RELEASE_MAX_LENGTH = 64
#: Kept equal to students.grading_label.RELEASE_NONE (a test compares them);
#: not imported, so this module stays free of the students app.
RELEASE_NONE = "none"


def _frozen(value):
    """Lists and sets as tuples, so a reading cannot be changed afterwards.
    Order is kept: the order of a model list is part of the behaviour."""
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(_frozen(item) for item in value))
    return value


def _release():
    release = getattr(settings, "GRADING_RELEASE_ID", "")
    release = release.strip() if isinstance(release, str) else ""
    return release[:RELEASE_MAX_LENGTH] or RELEASE_NONE


@dataclass(frozen=True)
class GradingConfig:
    """One reading. `values` is a tuple of (name, value) pairs, sorted by
    name, over GRADE_SHAPING_SETTINGS and CODE_CONSTANTS."""

    values: tuple
    release: str

    @classmethod
    def read(cls):
        # Local import: see the module docstring.
        from ai_processor import services

        pairs = [
            (name, _frozen(getattr(settings, name))) for name in GRADE_SHAPING_SETTINGS
        ]
        pairs += [(name, _frozen(getattr(services, name))) for name in CODE_CONSTANTS]
        return cls(values=tuple(sorted(pairs)), release=_release())

    def get(self, name):
        """The value this reading holds for `name`. KeyError for a name the
        reading does not cover, so a caller cannot quietly read a setting
        the version knows nothing about."""
        for key, value in self.values:
            if key == name:
                return value
        raise KeyError(name)

    @property
    def version(self):
        blob = json.dumps(self.values, separators=(",", ":"), default=str)
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        return f"{VERSION_PREFIX}{digest[:HASH_LENGTH]}"
