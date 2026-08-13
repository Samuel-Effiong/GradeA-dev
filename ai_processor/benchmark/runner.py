"""
Executes the benchmark dataset through the real grading pipeline.

Three modes:

    live    real OpenRouter calls, real credits. Produces the accuracy
            numbers.
    record  a live run that also captures every model response to
            recordings/, so it can be replayed for free afterwards.
    replay  no network, no credits, fully deterministic. Serves recorded
            responses. This is what the nightly job runs, and it catches
            regressions in OUR code — by construction it cannot detect
            the model itself changing.

## Why it does not create StudentSubmission rows by default

classrooms/signals.py::update_student_course_final_grade fires on every
submission save and recalculates the enrolled student's course final
grade. Persisting benchmark submissions into a production database would
therefore corrupt real students' grades. The default path calls
`extract_grade_with_retry` directly and touches no submission tables at
all. `--persist` opts into the full grade_engine path for local runs
where exercising persistence is the point.

## Replay keying

Recorded responses are keyed by a hash of what actually determines a
model reply: the system prompt, the user prompt, and the model override
(the second-opinion pass reuses the same batch prompt with a different
model, so the override must be part of the key). A prompt with no
recording raises rather than silently falling through to a live call —
a replay that quietly costs money would be worse than a failing one.
"""

import gzip
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import override_settings

from ai_processor.benchmark import submissions as submissions_module
from ai_processor.benchmark.dataset import ASSIGNMENTS, STUDENTS

RECORDINGS_DIR = Path(__file__).resolve().parent / "recordings"

MODE_LIVE = "live"
MODE_RECORD = "record"
MODE_REPLAY = "replay"
MODES = (MODE_LIVE, MODE_RECORD, MODE_REPLAY)


class MissingRecordingError(RuntimeError):
    """Replay was asked for a prompt that was never recorded."""


def _prompt_text(prompt):
    """
    Flatten a prompt argument to text for hashing.

    Grading prompts are either a plain string or the OpenAI content-part
    list ([{"type": "text", "text": ...}]); both appear in the pipeline.
    """
    if prompt is None:
        return ""
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        parts = []
        for item in prompt:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("type") or "")
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(prompt)


def request_key(system_prompt, user_prompt, override_model=None):
    digest = hashlib.sha256()
    digest.update(_prompt_text(system_prompt).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(_prompt_text(user_prompt).encode("utf-8"))
    digest.update(b"\x00")
    digest.update((override_model or "").encode("utf-8"))
    return digest.hexdigest()[:32]


def _fake_response(payload):
    """Rebuild the minimal response object the pipeline actually reads."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = payload["content"]
    response.choices[0].message.tool_calls = None
    response.usage.total_tokens = payload.get("total_tokens", 0)
    response.model = payload.get("model") or "replay"
    return response


class _Tape:
    """Records and/or replays __ai_model responses."""

    def __init__(self, mode, directory=RECORDINGS_DIR):
        self.mode = mode
        self.directory = Path(directory)
        self.entries = {}
        self.tokens = 0
        self.calls = 0
        if mode == MODE_REPLAY:
            self.entries = self._load()
        elif mode == MODE_RECORD and self._path().exists():
            # MERGE, don't overwrite. A run that aborts partway (e.g. one
            # submission failing after its retries) leaves gaps, and
            # re-recording the whole dataset to fill one gap costs a full
            # billed pass. Loading first makes
            # `--mode record --assignment X --student Y` a cheap top-up.
            self.entries = self._load()

    def _path(self):
        # Gzipped: the raw JSON is ~770KB of model prose, which trips the
        # repo's 500KB large-file guard. Compressed it is an order of
        # magnitude smaller, and `gzip -dc` still makes it readable by hand.
        return self.directory / "responses.json.gz"

    def _load(self):
        path = self._path()
        if not path.exists():
            raise MissingRecordingError(
                f"No recordings at {path}. Run "
                "`manage.py grading_benchmark --mode record` first (this makes "
                "real, billed model calls)."
            )
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.entries, indent=2, sort_keys=True)
        # mtime=0 so an unchanged recording set produces a byte-identical
        # file and does not show up as a spurious diff.
        with gzip.GzipFile(self._path(), "wb", mtime=0) as raw:
            raw.write(payload.encode("utf-8"))

    def wrap(self, original):
        """Return a replacement for AIProcessor.__ai_model."""

        def replacement(inner_self, *args, **kwargs):
            # execute_graded_task calls __ai_model with system_prompt and
            # user_prompt POSITIONAL (services.py:3277, :3357) and only
            # sub_models/override_model as keywords. Reading them from
            # kwargs alone would hash None for every call and collapse the
            # whole run onto a single recording key.
            system_prompt = args[0] if len(args) > 0 else kwargs.get("system_prompt")
            user_prompt = args[1] if len(args) > 1 else kwargs.get("user_prompt")
            override_model = args[7] if len(args) > 7 else kwargs.get("override_model")
            key = request_key(system_prompt, user_prompt, override_model)
            self.calls += 1

            if self.mode == MODE_REPLAY:
                payload = self.entries.get(key)
                if payload is None:
                    raise MissingRecordingError(
                        f"No recorded response for prompt {key}. The dataset or "
                        "a prompt file changed since the last recording — "
                        "re-record with `--mode record`. Refusing to fall "
                        "through to a live (billed) call."
                    )
                self.tokens += payload.get("total_tokens", 0)
                return _fake_response(payload)

            response = original(inner_self, *args, **kwargs)
            tokens = getattr(getattr(response, "usage", None), "total_tokens", 0) or 0
            self.tokens += tokens
            if self.mode == MODE_RECORD:
                self.entries[key] = {
                    "content": response.choices[0].message.content,
                    "total_tokens": tokens,
                    "model": getattr(response, "model", None),
                    # Kept for humans debugging a stale recording; never
                    # read back.
                    "_override_model": override_model,
                }
            return response

        return replacement


def _benchmark_settings():
    """
    Settings pinned for every benchmark run.

    The QA sample draws one RNG value per submission, which would make
    both the cost and the replay key set non-deterministic. Pinning it to
    0 leaves the deterministic triggers (low confidence, model
    self-flag, points >= 15) firing normally — and several benchmark
    essays are >= 15 points, so second-opinion coverage stays meaningful
    without doubling the bill.

    The answer cache (ai_processor/grading_cache.py) is deliberately
    turned OFF here, even though it's on by default in production. The
    'twin' student exists specifically to measure whether the MODEL
    grades an identical answer identically — with the cache on, 'twin'
    would just be served 'strong's cached evaluation, and the consistency
    check would trivially pass by cache mechanics rather than by anything
    the model actually did. That would hide exactly the signal this
    benchmark exists to surface.
    """
    return {
        "GRADING_SECOND_OPINION_SAMPLE_RATE": 0,
        "GRADING_DETERMINISTIC_OBJECTIVE": True,
        "GRADING_RESPONSE_SCHEMA_ENABLED": True,
        "GRADING_EVIDENCE_ENFORCEMENT": "strict",
        "GRADING_ANSWER_CACHE_ENABLED": False,
    }


def iter_cases(assignment_keys=None, student_keys=None):
    """(assignment, student) pairs to run, in a stable order."""
    for assignment in ASSIGNMENTS:
        if assignment_keys and assignment.key not in assignment_keys:
            continue
        for student in STUDENTS:
            if student_keys and student.key not in student_keys:
                continue
            yield assignment, student


def execute_benchmark(
    user,
    mode=MODE_REPLAY,
    assignment_keys=None,
    student_keys=None,
    recordings_dir=RECORDINGS_DIR,
    progress=None,
):
    """
    Grade every (assignment, student) pair and return the raw run.

    `user` must be a TEACHER with an active subscription and a credit
    wallet — see billing/tests/test_execute_graded_task.py's fixture, and
    note the flat +20,000 credit baseline in estimate_total_token.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    from ai_processor.services import AIProcessor, ai_processor

    tape = _Tape(mode, recordings_dir)
    # Name-mangled private method: mypy can't see it, but this is the
    # transport seam the whole record/replay design hangs off.
    original = AIProcessor._AIProcessor__ai_model  # type: ignore[attr-defined]
    results = []

    with override_settings(**_benchmark_settings()):
        with patch.object(AIProcessor, "_AIProcessor__ai_model", tape.wrap(original)):
            for assignment, student in iter_cases(assignment_keys, student_keys):
                specs = submissions_module.answers_for(assignment.key, student.key)
                answers = submissions_module.submission_answers_json(
                    assignment.key, student.key
                )
                if progress:
                    progress(assignment, student)

                started = time.monotonic()
                before = tape.tokens
                record = {
                    "assignment_key": assignment.key,
                    "student_key": student.key,
                    "assignment": assignment,
                    "specs": specs,
                    "grading": None,
                    "error": None,
                }
                try:
                    record["grading"] = ai_processor.extract_grade_with_retry(
                        user,
                        assignment.as_assignment_questions(),
                        answers,
                    )
                except Exception as exc:  # surfaced per-case, never fatal
                    record["error"] = f"{type(exc).__name__}: {exc}"
                record["elapsed_seconds"] = round(time.monotonic() - started, 2)
                record["tokens"] = tape.tokens - before
                results.append(record)

    if mode == MODE_RECORD:
        tape.save()

    return {
        "mode": mode,
        "results": results,
        "model_calls": tape.calls,
        "total_tokens": tape.tokens,
        "recordings": str(Path(recordings_dir)),
    }
