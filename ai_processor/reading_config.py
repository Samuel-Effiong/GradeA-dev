"""One reading of the settings that shape a paper's reading, and its version
(AI-call record, slice 2).

A student's answers are read from the paper by an AI call, checked for
completeness, and the blank ones are read again. The label of that reading
says which combination of settings was switched on. `ReadingConfig.read()`
reads every setting that can change what a reading produces ONCE;
`version` turns that into one short code ("rcfg:" and twelve hex
characters). It is built as `GradingConfig` is for the grading
(ai_processor/grading_config.py).

Read once per extraction, at the top of `extract_answer_with_retry`, and
handed to every site. An extraction is several AI calls and can take
minutes; a setting changed half-way must not split one reading between two
versions. NOTHING outside this module reads an ANSWER_* setting (a test
reads the source).

What the version covers, and what it does not:

* READING_SHAPING_SETTINGS (Django settings) and the code constants named in
  CODE_CONSTANTS, read from ai_processor.services, and the version of the
  re-check's instruction.
* NOT the release: it is recorded BESIDE the version (a paper can be read
  under one release and graded under another) and is never part of it.
* NOT what is written directly in the code (instruction text outside the
  versioned prompt, the reply schemas, retry counts); `release` is the
  backstop for those.

The version uses a colon, not "@": audit metadata drops anything shaped like
an email address.

Import-light on purpose: ai_processor.services is imported inside the one
function that needs its constants.
"""

import hashlib
import json
from dataclasses import dataclass

from django.conf import settings

from ai_processor import grading_config

VERSION_PREFIX = "rcfg:"
HASH_LENGTH = 12
RELEASE_MAX_LENGTH = grading_config.RELEASE_MAX_LENGTH
#: The same word as the grading's (a test compares them).
RELEASE_NONE = grading_config.RELEASE_NONE

#: Every Django setting whose name starts with ANSWER_ and that shapes a
#: reading. ai_processor/tests_reading_config.py fails when an ANSWER_* name
#: is added to settings.py and is in neither this tuple nor READING_NOT_SHAPING.
READING_SHAPING_SETTINGS = (
    "ANSWER_BLANK_VERIFICATION_ENABLED",
    "ANSWER_BLANK_VERIFICATION_MAX_PAGES",
    "ANSWER_BLANK_VERIFICATION_MAX_QUESTIONS",
    "ANSWER_BLANK_VERIFICATION_MODEL",
    "ANSWER_COMPLETENESS_ENFORCEMENT",
    "ANSWER_EXTRACTION_SCHEMA_ENABLED",
)

#: ANSWER_* settings deliberately left out of the version, with the reason.
#: Empty today: the six above are all the settings that start with ANSWER_.
READING_NOT_SHAPING: dict = {}

#: Module-level names in ai_processor.services that shape a reading.
CODE_CONSTANTS = (
    "AI_TEMPERATURE",
    "ANSWERS_EXTRACTION_PAGES_PER_CHUNK",
    "GRADING_FALLBACK_MODELS",
    "IMAGE_FALLBACK_MODELS",
    "MAIN_MODEL",
)
#: The re-check's instruction is versioned like a prompt; its version joins.
RECHECK_INSTRUCTION_NAME = "BLANK_VERIFICATION_INSTRUCTION_VERSION"
#: So is the note sent with the second reading of recovered answers (H-242).
RETRANSCRIPTION_INSTRUCTION_NAME = "RETRANSCRIPTION_INSTRUCTION_VERSION"


def _frozen(value):
    """Lists and sets as tuples, so a reading cannot be changed afterwards.
    Order is kept: the order of a model list is part of the behaviour."""
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(_frozen(item) for item in value))
    return value


@dataclass(frozen=True)
class ReadingConfig:
    """One reading. `values` is a tuple of (name, value) pairs, sorted by
    name."""

    values: tuple
    release: str

    @classmethod
    def read(cls):
        # Local import: see the module docstring.
        from ai_processor import services

        pairs = [
            (name, _frozen(getattr(settings, name)))
            for name in READING_SHAPING_SETTINGS
        ]
        pairs += [(name, _frozen(getattr(services, name))) for name in CODE_CONSTANTS]
        pairs.append(
            (RECHECK_INSTRUCTION_NAME, services.BLANK_VERIFICATION_INSTRUCTION.version)
        )
        pairs.append(
            (
                RETRANSCRIPTION_INSTRUCTION_NAME,
                services.RETRANSCRIPTION_INSTRUCTION.version,
            )
        )
        return cls(values=tuple(sorted(pairs)), release=grading_config._release())

    def get(self, name):
        """The value this reading holds for `name`. KeyError for a name the
        reading does not cover, so a caller cannot quietly read a setting the
        version knows nothing about."""
        for key, value in self.values:
            if key == name:
                return value
        raise KeyError(name)

    @property
    def version(self):
        blob = json.dumps(self.values, separators=(",", ":"), default=str)
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        return f"{VERSION_PREFIX}{digest[:HASH_LENGTH]}"
