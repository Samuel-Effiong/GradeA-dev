"""What one grading run carries from its start to its end (BE-I-04).

A run is several AI calls and can take minutes. Two things are fixed when
it starts and must not move under it:

* `config`: ONE reading of the grading settings
  (ai_processor/grading_config.py). The saved-answer lookup and the
  saved-answer store of a run use this same reading, and so does every
  attempt of the retry loop. A setting changed half-way cannot file an
  answer under one version and look for it under another.
* `prompt_version`: the grading prompt's version as it was at the start.

`AIProcessor.extract_grade_with_retry` starts the run, above its retry
loop, and passes it down as a keyword argument. It never travels inside
the grading dictionary, which is saved whole as the submission's feedback
and sent on to another AI call.

FIRST FORM OF THE GRADING RECORD. A later stage improves on it: a table of
grading runs, built beside re-grading or feedback editing and filled from
these fields (students/grading_label.py). The label itself, and the list of
models that answered, are added to this object in the next slice.
"""

from dataclasses import dataclass

from ai_processor.grading_config import GradingConfig


@dataclass(frozen=True)
class GradingRun:
    config: GradingConfig
    prompt_version: str

    @classmethod
    def start(cls):
        # Local import: ai_processor.services imports this module.
        from ai_processor import services

        return cls(
            config=GradingConfig.read(),
            prompt_version=services.GRADING_ASSIGNMENT_PROMPT.version,
        )
