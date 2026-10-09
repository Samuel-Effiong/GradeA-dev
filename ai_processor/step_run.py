"""What one AI step carries (AI-call record, beside BE-I-04's GradingRun).

A step is one kind of AI call that affects a grade and is not the grading
itself: reading an assignment, generating one, reading a student's answers,
the blank-answer re-check, the feedback wording. A `StepRun` gathers, as
our code read it from the provider's replies, WHICH MODEL answered. Never
anything the AI wrote about itself.

* `keep(model, count, prompt_version)`: a kept reply that supplied `count`
  items, and the version of the prompt it answered (read where the call is
  made). A reply votes once for its prompt version.
* `model(main)`: the model of the step, by BE-I-04's rule
  (ai_processor/vote.py); None when no reply was kept.
* `prompt_version()`: the version most kept replies used (same rule); None
  when none was given.
* `begin_attempt()`: empties what was gathered; called at the start of every
  attempt, so a try that was thrown away leaves nothing.
* `mark_call_left()` / `mark_reply_received(model)`: two facts recorded at
  the provider call itself (AIProcessor.__ai_model), never inferred from the
  kind of exception that came later.

THE SCOPE. No slice edits `AIProcessor.execute_graded_task`. A step's own
function opens `with run.scope():` around its call; `__ai_model` reads the
open run (`current_step_run()`) and marks it where the call leaves. A call
for a task type that needs a run, made with none open, fails loudly
(`require_step_scope`); never a silent unlabelled step. STATED LIMIT: a
future caller of `__ai_model` that forgets the scope loses its marks; the
task types that need one are named in ENFORCED_STEP_TASK_TYPES and a static
test covers their callers.

The run never travels inside the dictionary an AI reply became.

Imports nothing from the project but ai_processor.vote. The words are kept
equal to students/step_label.py by a test (ai_processor does not import the
students app).
"""

import contextvars
from contextlib import contextmanager

from ai_processor.vote import majority_model

UNLABELLED = "unlabelled"
MODEL_UNKNOWN = "unknown"
NOT_RUN = "not_run"
FAILED = "failed"
CONFIRMED_BLANK = "confirmed_blank"
FOUND_WRITING = "found_writing"
SOURCE_EXTRACTED = "extracted"
SOURCE_GENERATED = "generated"

#: A provider name that equals one of these is recorded as "unknown".
FIXED_MODEL_WORDS = frozenset({UNLABELLED, MODEL_UNKNOWN, NOT_RUN})

#: The task types of the steps that carry a StepRun (the grading travels by
#: its own `run=` and is not here).
STEP_TASK_TYPES = frozenset(
    {"extract_assignment", "generate_assignment", "extract_answer", "formatted_grade"}
)
#: The task types for which a missing scope is an error. Empty in slice 0
#: (no behaviour changes); each later slice adds its own, in the commit that
#: makes every caller of that task type open the scope.
ENFORCED_STEP_TASK_TYPES = frozenset()

_CURRENT: contextvars.ContextVar["StepRun | None"] = contextvars.ContextVar(
    "ai_step_run", default=None
)


class StepScopeMissingError(RuntimeError):
    """A call for a task type that needs a StepRun was made with none open.
    The message is a fixed phrase and the task type; nothing else."""


def current_step_run():
    """The StepRun whose scope is open in this context, or None."""
    return _CURRENT.get()


def require_step_scope(task_type, enforced=None):
    """The open StepRun. Raises StepScopeMissingError if `task_type` needs
    one and none is open; returns None when it is not enforced and none is
    open."""
    if enforced is None:
        enforced = ENFORCED_STEP_TASK_TYPES
    run = _CURRENT.get()
    if run is None and task_type in enforced:
        raise StepScopeMissingError(
            f"a {task_type} call was made with no step run open"
        )
    return run


def _name(model):
    """The provider's exact text, or None for an unnamed model or one that
    equals a fixed word of ours."""
    if isinstance(model, str) and model and model not in FIXED_MODEL_WORDS:
        return model
    return None


class StepRun:
    def __init__(self, step):
        self.step = step
        self._votes = {}
        self._prompts = {}
        self._kept = False
        self.call_left = False
        self.reply_received = False
        self.reply_model = None

    def begin_attempt(self):
        """Forget what was gathered; keep the step's name."""
        self._votes = {}
        self._prompts = {}
        self._kept = False
        self.call_left = False
        self.reply_received = False
        self.reply_model = None

    # -- gathering ---------------------------------------------------------

    def keep(self, model, count=1, prompt_version=None):
        """A kept reply that supplied `count` items, by `model`, answering
        the prompt of version `prompt_version`."""
        self._kept = True
        name = _name(model)
        count = max(int(count), 0)
        if count:
            self._votes[name] = self._votes.get(name, 0) + count
        if isinstance(prompt_version, str) and prompt_version:
            self._prompts[prompt_version] = self._prompts.get(prompt_version, 0) + 1

    def has_kept_reply(self):
        return self._kept

    def votes(self):
        return dict(self._votes)

    def prompt_version(self):
        """The version of the prompt most kept replies answered, or None."""
        return majority_model(self._prompts, None)

    # -- the two facts recorded at the provider call -----------------------

    def mark_call_left(self):
        self.call_left = True

    def mark_reply_received(self, model):
        if not self.call_left:
            raise RuntimeError("a reply cannot be received before the call left")
        self.reply_received = True
        self.reply_model = _name(model) or MODEL_UNKNOWN

    # -- deriving ----------------------------------------------------------

    def model(self, main):
        """None when no reply was kept (the caller chooses its word);
        otherwise the provider's text by the shared rule, or "unknown" when
        no reply holds an item or every leader is unnamed."""
        if not self._kept:
            return None
        return majority_model(self._votes, main) or MODEL_UNKNOWN

    # -- the scope ---------------------------------------------------------

    @contextmanager
    def scope(self):
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)
