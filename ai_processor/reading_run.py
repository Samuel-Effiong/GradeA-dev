"""What one reading of a paper carries (AI-call record, slice 2).

A reading is the extraction of a student's answers (one call, or a call per
part of a long paper), the completeness check, and the blank-answer re-check.
A `ReadingRun` holds two `StepRun`s (ai_processor/step_run.py), the reading
and the re-check, which are different steps with their own model, and the
one reading of the settings (ai_processor/reading_config.py).

* The MODEL of the reading is a vote over the answers AS SAVED (ruling 5):
  each saved answer votes for the model of the reply that supplied it. The
  link from an answer to its reply is kept BESIDE the answers, here, by the
  canonical question key; never a key inside an answer. A placeholder the
  completeness check inserted came from no model and has no vote; an answer
  the check dropped is not in the list and has none; an answer a reply
  repeated has one origin and votes once; an answer joined from two parts of
  a long paper counts once for each part's model.
* The RE-CHECK's pair (result, model) is built by ONE function
  (`recheck_pair`) from the two marks made at the provider call and from
  whether the reply was used. "not_run" means only that no call left.
* `label_fields(saved)` gives the six column values. The only writer of the
  columns, `students.reading_label`, takes them from here.

The run never travels inside the dictionary an AI reply became.
"""

from ai_processor.step_run import (
    CONFIRMED_BLANK,
    FAILED,
    FOUND_WRITING,
    MODEL_UNKNOWN,
    NOT_RUN,
    StepRun,
    _name,
)
from ai_processor.vote import majority_model

#: Column lengths (students.StudentSubmission, migration 0032).
MODEL_MAX = 255
PROMPT_VERSION_MAX = 128
CONFIG_VERSION_MAX = 128
RELEASE_MAX = 64


def _key(value):
    """The one key for every way a question number is spelled (3, "3",
    "Q3", "Question 3")."""
    from ai_processor.services import _canonical_question_key

    return _canonical_question_key(value)


def recheck_pair(step, used):
    """The re-check's (result, model) from its StepRun and what became of
    its reply: `used` is None when the reply was not used, otherwise
    "confirmed_blank" or "found_writing". The only place a pair is made."""
    if not step.call_left:
        return NOT_RUN, NOT_RUN
    model = step.reply_model if step.reply_received else MODEL_UNKNOWN
    return (used or FAILED), (model or MODEL_UNKNOWN)


class ReadingRun:
    def __init__(self):
        self.reading = StepRun("answers")
        self.recheck = StepRun("answers_recheck")
        self.config = None
        self._origins = {}
        self._used = None

    def begin_attempt(self):
        """Forget what the last try gathered; keep the settings reading."""
        self.reading.begin_attempt()
        self.recheck.begin_attempt()
        self._origins = {}
        self._used = None

    # -- the reading ---------------------------------------------------------

    def keep_reply(self, model, prompt_version):
        """A reply that was read and merged, by `model`, answering the prompt
        of version `prompt_version`. Holds no items: the answers come in
        `keep_answers`."""
        self.reading.keep(model, 0, prompt_version)

    def keep_answers(self, answers, model):
        """These answers were supplied by the reply of `model` (None when it
        named none). Replaces any earlier origin of the same question."""
        name = _name(model)
        for answer in answers:
            if isinstance(answer, dict) and answer.get("question_number") is not None:
                self._origins[_key(answer["question_number"])] = (name,)

    def add_origin(self, answer, model):
        """This answer is also made of a part supplied by `model`."""
        if not isinstance(answer, dict) or answer.get("question_number") is None:
            return
        key = _key(answer["question_number"])
        name = _name(model)
        known = self._origins.get(key, ())
        if name not in known:
            self._origins[key] = known + (name,)

    def votes(self, saved):
        """{model or None: votes} over the answers as saved. An answer joined
        from two parts counts once for each part's model, so the sum may be
        larger than the number of answers (a ruling of the Senior Manager,
        8 October 2026): both models read part of it."""
        votes = {}
        for answer in saved or []:
            if not isinstance(answer, dict) or answer.get("question_number") is None:
                continue
            for name in self._origins.get(_key(answer["question_number"]), ()):
                votes[name] = votes.get(name, 0) + 1
        return votes

    def reading_model(self, saved, main=None):
        """The model of the reading: the vote over `saved`, the answers as
        they are saved. "unknown" when no saved answer has a reply behind it
        or every leader is unnamed."""
        if main is None:
            from ai_processor import services

            main = services.MAIN_MODEL
        return majority_model(self.votes(saved), main) or MODEL_UNKNOWN

    # -- the re-check --------------------------------------------------------

    def mark_recheck_used(self, found_writing):
        """The re-check's reply was read and used."""
        if not self.recheck.reply_received:
            raise RuntimeError("a reply cannot be used before it was received")
        self._used = FOUND_WRITING if found_writing else CONFIRMED_BLANK

    def recheck_pair(self):
        return recheck_pair(self.recheck, self._used)

    # -- the label -----------------------------------------------------------

    def label_fields(self, saved):
        """The six column values for a reading whose saved answers are
        `saved`."""
        if self.config is None:
            raise RuntimeError("the reading's settings were never taken")
        result, model = self.recheck_pair()
        return {
            "answers_model": self.reading_model(saved)[:MODEL_MAX],
            "answers_prompt_version": (self.reading.prompt_version() or MODEL_UNKNOWN)[
                :PROMPT_VERSION_MAX
            ],
            "answers_config_version": self.config.version[:CONFIG_VERSION_MAX],
            "answers_release": self.config.release[:RELEASE_MAX],
            "answers_recheck_model": model[:MODEL_MAX],
            "answers_recheck_result": result,
        }
