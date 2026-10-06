"""What one grading run carries from its start to its end (BE-I-04).

A run is several AI calls and can take minutes. Two things are fixed when
it starts and must not move under it:

* `config`: ONE reading of the grading settings
  (ai_processor/grading_config.py). The grading code reads its settings
  from it; the saved-answer lookup and store use it; so does every attempt
  of the retry loop. A setting changed half-way cannot label a grade with
  settings it was not made under.
* `prompt_version`: the grading prompt's version as it was at the start.

And one thing is gathered as it goes: WHICH MODEL ANSWERED, as our code
read it from each provider response. Never anything the AI wrote about
itself in a reply.

* `keep_answers(model, count)`: a kept reply freshly marked `count`
  answers.
* `keep_call(model)`: a kept fresh call that marked no answer (the summary
  call of a long paper).
* `keep_reused(model)`: one answer taken from the saved-answer store; the
  model is the one that first produced it.
* `keep_second_opinion(model)`: a second-opinion call. Recorded apart: it
  does not set the score, so it is in neither the label nor the backup
  measurement.

ONLY KEPT REPLIES COUNT. `begin_attempt()` empties what was gathered, and
the grading service calls it at the start of every attempt, so a whole
attempt that failed leaves nothing behind; a reply that is rejected and
asked again is never kept in the first place. STATED LIMIT: the label
says which models produced the grade that was saved, not every model that
was called. Discarded calls remain in the per-call log line only.

From that the run derives:

* `label()`: the six columns of the grade's label;
* `audit_models()`: three lists for the audit entry, for a person to read
  (each name cut to what an audit item may hold);
* `fresh_backup_used()`: one word for the fresh calls only, which the
  backup measurement reads. It is worked out on the exact names, before
  any cut, by the same rule as the label's flag.

`AIProcessor.extract_grade_with_retry` is handed the run, or starts one,
above its retry loop, and passes it down as a keyword argument. It never
travels inside the grading dictionary, which is saved whole as the
submission's feedback and sent on to another AI call.

FIRST FORM OF THE GRADING RECORD. A later stage improves on it: a table of
grading runs, built beside re-grading or feedback editing and filled from
these fields (students/grading_label.py).
"""

from collections import Counter
from dataclasses import dataclass, field

from ai_processor.grading_config import GradingConfig

#: Kept equal to the words in students/grading_label.py (a test compares
#: them); not imported, so ai_processor stays free of the students app.
STRICTNESS_NOT_YET_SET = "not_yet_set"
MODEL_DETERMINISTIC = "deterministic"
MODEL_UNKNOWN = "unknown"
FALLBACK_YES = "yes"
FALLBACK_NO = "no"
FALLBACK_UNKNOWN = "unknown"
FALLBACK_NOT_APPLICABLE = "not_applicable"
#: `fresh_backup_used()` when the run made no fresh call.
NO_FRESH_CALL = "no_fresh_call"

#: Column lengths of the label (students.StudentSubmission).
MODEL_MAX_LENGTH = 255
VERSION_MAX_LENGTH = 128
#: What one item of an audit list may hold (audit.metadata.MAX_ITEM_STRING).
AUDIT_ITEM_MAX_LENGTH = 64


def audit_model_name(model):
    """A model's name as an audit list shows it: the explicit word for a
    model the provider did not name; no "@", which audit metadata would
    drop the whole entry's key for; cut to what an audit item may hold.
    For a person to read. Nothing is classified from this."""
    if not isinstance(model, str) or not model:
        return MODEL_UNKNOWN
    return model.replace("@", "(at)")[:AUDIT_ITEM_MAX_LENGTH]


@dataclass
class GradingRun:
    config: GradingConfig
    prompt_version: str
    #: The model of each freshly marked answer, one item per answer; None
    #: for a model the provider did not name.
    fresh_answers: list = field(default_factory=list)
    #: The model of each kept fresh call that marked no answer.
    other_calls: list = field(default_factory=list)
    #: The first model of each reused answer.
    reused_answers: list = field(default_factory=list)
    #: The model of each second-opinion call.
    second_opinions: list = field(default_factory=list)

    @classmethod
    def start(cls):
        # Local import: ai_processor.services imports this module.
        from ai_processor import services

        return cls(
            config=GradingConfig.read(),
            prompt_version=services.GRADING_ASSIGNMENT_PROMPT.version,
        )

    # -- gathering ---------------------------------------------------------

    def begin_attempt(self):
        """Forget what was gathered; keep the reading. Called at the start
        of every attempt."""
        self.fresh_answers.clear()
        self.other_calls.clear()
        self.reused_answers.clear()
        self.second_opinions.clear()

    @staticmethod
    def _name(model):
        return model if isinstance(model, str) and model else None

    def keep_answers(self, model, count):
        self.fresh_answers.extend([self._name(model)] * max(int(count), 0))

    def keep_call(self, model):
        self.other_calls.append(self._name(model))

    def keep_reused(self, model):
        self.reused_answers.append(self._name(model))

    def keep_second_opinion(self, model):
        self.second_opinions.append(self._name(model))

    # -- deriving ----------------------------------------------------------

    def _backup_used(self, models):
        """yes / no / unknown for a collection of exact model names (None
        for an unnamed one), by the SM's ruling: any backup is "yes", also
        beside an unknown; otherwise any model the provider did not name,
        or that is neither the main model nor a backup, is "unknown";
        otherwise "no". The lists are the run's reading, not live."""
        main = self.config.get("MAIN_MODEL")
        backups = set(self.config.get("GRADING_FALLBACK_MODELS"))
        if any(model in backups for model in models if model is not None):
            return FALLBACK_YES
        if any(model is None or model != main for model in models):
            return FALLBACK_UNKNOWN
        return FALLBACK_NO

    def _grading_model(self):
        """The model that marked the most answers, fresh and reused
        together. On a tie: the main model if it is among the leaders,
        else the first by plain alphabetical order of the provider's exact
        text; an unnamed model loses every tie to a named one. Depends
        only on the counts, never on the order of arrival."""
        votes = Counter(self.fresh_answers + self.reused_answers)
        if not votes:
            # No answer was marked by an AI. A kept call with no answer
            # cannot name the grader of anything.
            return MODEL_DETERMINISTIC
        most = max(votes.values())
        leaders = [model for model, count in votes.items() if count == most]
        named = sorted(model for model in leaders if model is not None)
        main = self.config.get("MAIN_MODEL")
        if main in named:
            return main
        if named:
            return named[0]
        return MODEL_UNKNOWN

    def label(self):
        """The six columns of the label (students.grading_label.LABEL_FIELDS)."""
        everything = self.fresh_answers + self.other_calls + self.reused_answers
        if everything:
            fallback_used = self._backup_used(everything)
        else:
            fallback_used = FALLBACK_NOT_APPLICABLE
        return {
            "grading_prompt_version": self.prompt_version[:VERSION_MAX_LENGTH],
            "grading_config_version": self.config.version,
            "grading_strictness": STRICTNESS_NOT_YET_SET,
            "grading_model": self._grading_model()[:MODEL_MAX_LENGTH],
            "grading_fallback_used": fallback_used,
            "grading_release": self.config.release,
        }

    def fresh_backup_used(self):
        """One word for the run's FRESH calls only (answers and the summary
        call; not reused answers, not second opinions), classified on the
        exact names: yes, no, unknown, or no_fresh_call. The backup
        measurement reads this and nothing else (SM ruling, 2026-10-06)."""
        fresh = self.fresh_answers + self.other_calls
        if not fresh:
            return NO_FRESH_CALL
        return self._backup_used(fresh)

    def audit_models(self):
        """Three lists for the audit entry: the distinct names, sorted."""

        def names(models):
            return sorted({audit_model_name(model) for model in models})

        return {
            "models_served": names(self.fresh_answers + self.other_calls),
            "models_reused": names(self.reused_answers),
            "models_second_opinion": names(self.second_opinions),
        }
