"""Mutation testing for the answer-extraction benchmark.

A benchmark that only ever passes proves nothing about whether it would
fail. Each mutation below removes ONE protection - from production code or
from the benchmark itself - and the runner checks that the tests notice.

    python -m ai_processor.benchmark.answers.mutations \\
        --root /path/to/a/COPY/of/the/tree \\
        --settings <settings module with its own TEST database name> \\
        --report ai_processor/benchmark/answers/reports/mutations.json

OUTCOMES

KILLED           a test assertion failed with the mutant in place.
                 Runs use --failfast, so the test recorded is the FIRST one
                 Django ran that failed - proof that an assertion caught
                 the mutant, not a list of every test that would.
KILLED-BY-CRASH  the run failed without any FAIL/ERROR line (import error,
                 system check). Inconclusive: it shows the code broke, not
                 that an assertion caught the missing protection.
SURVIVED         every test passed with the protection removed. Re-run
                 against the full label set before being reported, so a
                 survivor is never an artefact of a narrow label choice.
ANCHOR-MISSING   the code to mutate was not found exactly once. The
                 mutation is stale and must be updated, never skipped.

SAFETY - WHY THIS REFUSES THE SHARED WORKING TREE

A mutant is live on disk for as long as its tests take. In a checkout that
other sessions are also running tests from, that is a mutant somebody
else's run can import. So --root must not contain a .git directory: copy
the tree first (README.md has the command). Every mutated file is restored
from an in-memory copy and verified by sha256 before the next mutation; a
mismatch stops the whole run.

Bytecode is never written (PYTHONDONTWRITEBYTECODE=1). A same-size mutant
written in the same second as the restore before it would otherwise be
able to run the PREVIOUS file's cached bytecode and "survive" by accident.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SERVICES = "ai_processor/services.py"

CHUNKED = ("    def _extract_answers_chunked(", "    def extract_answer_image(")
SINGLE = ("    def extract_answer_image(", "    def _answer_extraction_schema(")
RETRY = ("    def extract_answer_with_retry(", "    def _question_number_key(")
PAIRING = ("    def _pair_question_with_answers(", "    def _grade_question_batch(")
MERGE = ("_NO_INFORMATION_RANK = ", "# Raised from 5 to 10 on 2026-08-21")
SPLITTER = ("    def _split_into_chunks(", "    def _split_prosemirror_into_chunks(")
NOTE = ("    def _build_answer_chunk_note(", "    def _slim_assignment_context(")

BENCHMARK = (
    "ai_processor.tests_answer_benchmark_scenarios",
    "ai_processor.tests_answer_benchmark_failures",
    "ai_processor.tests_answer_benchmark_inputs",
    "ai_processor.tests_answer_benchmark_grading",
    "ai_processor.tests_answer_chunk_merge",
)
CONCURRENCY = ("ai_processor.tests_answer_benchmark_concurrency",)
UNIT = (
    "ai_processor.tests_chunked_extraction_contract",
    "ai_processor.tests_answer_completeness",
    "ai_processor.tests_answer_extraction_gate",
    "ai_processor.tests_blank_verification",
    "ai_processor.tests_pdf_service_concurrency",
    "ai_processor.tests_ssrf_guard",
)
FULL = BENCHMARK + CONCURRENCY + UNIT
DEFAULT_LABELS = BENCHMARK + UNIT


@dataclass(frozen=True)
class Mutation:
    id: str
    title: str
    #: The spec line (section 32) or regression this mutation stands for.
    stands_for: str
    file: str
    old: str
    new: str
    #: (start marker, end marker) bounding where `old` must occur once.
    scope: tuple[str, str] | None = None
    labels: tuple[str, ...] = DEFAULT_LABELS


MUTATIONS: tuple[Mutation, ...] = (
    # -- schema ------------------------------------------------------------
    Mutation(
        "M01",
        "chunked path sends no schema",
        "remove extraction schema; Regression 2",
        SERVICES,
        "response_schema=self._answer_extraction_schema(),",
        "response_schema=None,",
        CHUNKED,
    ),
    Mutation(
        "M02",
        "single-call path sends no schema",
        "remove extraction schema",
        SERVICES,
        "response_schema=self._answer_extraction_schema(),",
        "response_schema=None,",
        SINGLE,
    ),
    Mutation(
        "M03",
        "BLANK removed from the status enum",
        "remove BLANK enum",
        "ai_processor/extraction_schemas.py",
        "ANSWER_STATUSES = (ANSWERED, BLANK, ILLEGIBLE, NOT_FOUND_IN_DOCUMENT)",
        "ANSWER_STATUSES = (ANSWERED, ILLEGIBLE, NOT_FOUND_IN_DOCUMENT)",
    ),
    Mutation(
        "M04",
        "NOT_FOUND_IN_DOCUMENT removed from the status enum",
        "remove NOT_FOUND enum",
        "ai_processor/extraction_schemas.py",
        "ANSWER_STATUSES = (ANSWERED, BLANK, ILLEGIBLE, NOT_FOUND_IN_DOCUMENT)",
        "ANSWER_STATUSES = (ANSWERED, BLANK, ILLEGIBLE)",
    ),
    # -- chunking and page ranges -----------------------------------------
    Mutation(
        "M05",
        "answer chunker ignores the configured chunk size",
        "ignore pages_per_chunk",
        SERVICES,
        "image_contents, ANSWERS_EXTRACTION_PAGES_PER_CHUNK",
        "image_contents, 2",
        CHUNKED,
    ),
    Mutation(
        "M06",
        "assignment chunker reverts to the hard-coded CHUNK_SIZE",
        "ignore pages_per_chunk; Regression 3",
        SERVICES,
        "chunks = self._split_into_chunks(image_contents, pages_per_chunk)",
        "chunks = self._split_into_chunks(image_contents, CHUNK_SIZE)",
    ),
    Mutation(
        "M07",
        "claimed page range shifted +1",
        "shift page range by +1; Regression 4",
        SERVICES,
        "chunk_index * ANSWERS_EXTRACTION_PAGES_PER_CHUNK + 1",
        "chunk_index * ANSWERS_EXTRACTION_PAGES_PER_CHUNK + 2",
        CHUNKED,
    ),
    Mutation(
        "M08",
        "claimed page range shifted -1",
        "shift page range by -1; Regression 4",
        SERVICES,
        "chunk_index * ANSWERS_EXTRACTION_PAGES_PER_CHUNK + 1",
        "chunk_index * ANSWERS_EXTRACTION_PAGES_PER_CHUNK + 0",
        CHUNKED,
    ),
    Mutation(
        "M09",
        "final chunk dropped",
        "drop final chunk",
        SERVICES,
        "        total_chunks = len(chunks)\n",
        "        chunks = chunks[:-1] if len(chunks) > 1 else chunks\n"
        "        total_chunks = len(chunks)\n",
        CHUNKED,
    ),
    Mutation(
        "M10",
        "first chunk dropped",
        "drop first chunk",
        SERVICES,
        "        total_chunks = len(chunks)\n",
        "        chunks = chunks[1:] if len(chunks) > 1 else chunks\n"
        "        total_chunks = len(chunks)\n",
        CHUNKED,
    ),
    Mutation(
        "M11",
        "last chunk sent twice",
        "duplicate a chunk",
        SERVICES,
        "        total_chunks = len(chunks)\n",
        "        chunks = chunks + chunks[-1:]\n        total_chunks = len(chunks)\n",
        CHUNKED,
    ),
    Mutation(
        "M12",
        "splitter accepts a negative size again (drops every page)",
        "invalid chunk size rejected",
        SERVICES,
        "        if chunk_size < 1:",
        "        if False:",
        SPLITTER,
    ),
    # -- status collapse ---------------------------------------------------
    Mutation(
        "M13",
        "completeness placeholder says BLANK",
        "convert NOT_FOUND -> BLANK; Regression 5",
        "ai_processor/answer_completeness.py",
        '        "answer_status": NOT_FOUND_IN_DOCUMENT,',
        '        "answer_status": BLANK,',
    ),
    Mutation(
        "M14",
        "grading pairing placeholder says BLANK",
        "convert NOT_FOUND -> BLANK; Regression 5",
        SERVICES,
        '"answer_status": NOT_FOUND_IN_DOCUMENT,',
        '"answer_status": BLANK,',
        PAIRING,
    ),
    Mutation(
        "M15",
        "merge trusts a BLANK the model cannot place",
        "convert NOT_FOUND -> BLANK (AE-800, AE-802)",
        SERVICES,
        "rank == _LOCATED_EMPTY_RANK and not _located_in_chunk(",
        "False and not _located_in_chunk(",
        MERGE,
    ),
    Mutation(
        "M16",
        "merge ties no longer favour NOT_FOUND",
        "order-independent merge (AE-802 reversed)",
        SERVICES,
        '            and new.get("answer_status") == NOT_FOUND_IN_DOCUMENT',
        "            and False",
        MERGE,
    ),
    Mutation(
        "M17",
        "merge reverts to the empty-html rule",
        "BLANK -> NOT_FOUND collapse (AE-600, AE-601)",
        SERVICES,
        "    if new_rank > existing_rank:\n",
        "    if new_rank > existing_rank and new_html:\n",
        MERGE,
    ),
    Mutation(
        "M18",
        "answer split across chunks keeps only its first half",
        "cross-chunk answer (AE-205)",
        SERVICES,
        "        and new_chunk != existing_chunk",
        "        and False",
        MERGE,
    ),
    Mutation(
        "M19",
        "later chunks told every question was already found",
        "later-chunk answers skipped (AE-804)",
        SERVICES,
        "already_found = [",
        "already_found = list(found_answers.keys()) or [",
        CHUNKED,
    ),
    # -- isolation ---------------------------------------------------------
    Mutation(
        "M20",
        "shared PDFService singleton restored",
        "remove per-upload service isolation; Regression 1",
        "assignments/services.py",
        "                images = PDFService(uploaded_file).extract()\n",
        "                from ai_processor.services import pdf_service\n\n"
        "                pdf_service.set_uploaded_file(uploaded_file)\n"
        "                images = pdf_service.extract()\n",
        labels=DEFAULT_LABELS + CONCURRENCY,
    ),
    Mutation(
        "M21",
        "connected-peer SSRF check disabled",
        "disable connected-peer SSRF check",
        "ai_processor/tools.py",
        "    _assert_address_is_public(peer, hostname)",
        "    return None",
    ),
    # -- failure handling --------------------------------------------------
    Mutation(
        "M22",
        "a chunk that never succeeds is skipped",
        "swallow provider error; Regression 8",
        SERVICES,
        "            if chunk_result is None:\n",
        "            if chunk_result is None:\n                continue\n",
        CHUNKED,
    ),
    Mutation(
        "M23",
        "cancellation retried by extract_answer_with_retry",
        "retry cancellation; Regression 7",
        SERVICES,
        "            except TaskCancelledError:",
        "            except ZeroDivisionError:",
        RETRY,
    ),
    Mutation(
        "M24",
        "cancellation retried inside the chunk loop",
        "retry cancellation; Regression 7",
        SERVICES,
        "                except TaskCancelledError:",
        "                except ZeroDivisionError:",
        CHUNKED,
    ),
    Mutation(
        "M25",
        "single-call path wraps credit/cancel errors again",
        "error type preserved on both paths",
        SERVICES,
        "        except (\n"
        "            AIFeatureNotAvailableError,\n"
        "            InsufficientCreditsError,\n"
        "            TaskCancelledError,\n"
        "        ):",
        "        except (ZeroDivisionError,):",
        SINGLE,
    ),
    Mutation(
        "M26",
        "exhausted retries lose their cause",
        "failure classification; Regression 8",
        SERVICES,
        "        ) from last_error",
        "        )",
        RETRY,
    ),
    Mutation(
        "M27",
        "failed chunk loses its cause",
        "failure classification; Regression 8",
        SERVICES,
        "                ) from last_chunk_error",
        "                )",
        CHUNKED,
    ),
    Mutation(
        "M28",
        "empty submission billed again",
        "missing document rejected",
        SERVICES,
        "        if not content:",
        "        if False:",
        RETRY,
    ),
    # -- duplicates and ordering -----------------------------------------
    Mutation(
        "M29",
        "completeness gate keeps every duplicate",
        "remove duplicate protection",
        "ai_processor/answer_completeness.py",
        "        repaired.append(entry)",
        "        repaired.extend(entries)",
    ),
    Mutation(
        "M30",
        "chunked merge result no longer sorted",
        "remove final result ordering",
        SERVICES,
        "for q_num in sorted(found_answers.keys(), key=safe_sort_key)",
        "for q_num in found_answers.keys()",
        CHUNKED,
    ),
    Mutation(
        "M31",
        "completeness gate output in reverse order",
        "remove final result ordering",
        "ai_processor/answer_completeness.py",
        "    for key in question_keys:",
        "    for key in reversed(question_keys):",
    ),
    # -- the benchmark itself ---------------------------------------------
    Mutation(
        "M32",
        "generated PDFs no longer byte-deterministic",
        "determinism (section 25)",
        "ai_processor/benchmark/answers/documents.py",
        "pdf.save(buffer, no_new_id=True)",
        "pdf.save(buffer)",
        labels=CONCURRENCY,
    ),
    # -- the no-truncation requirement -----------------------------------
    Mutation(
        "M33",
        "chunk note stops asking for the continuation of a found answer",
        "split answer silently truncated at a chunk boundary (AE-803, AE-805, AE-806)",
        SERVICES,
        '"If an answer to any of those questions CONTINUES on these "',
        '"Those answers do NOT need to be extracted again. "',
        NOTE,
    ),
    Mutation(
        "M34",
        "chunked merge keys on the model's raw question label again",
        "question-number style switch re-bills the script (live AE-912, AE-921)",
        SERVICES,
        "answer = _relabel_answer(answer, declared_labels)",
        "answer = answer",
        CHUNKED,
    ),
    Mutation(
        "M35",
        "single-call answers no longer relabelled before the completeness gate",
        "a real answer dropped as numbering drift on the final attempt",
        SERVICES,
        '_relabel_answer(entry, labels) for entry in result["answers"]',
        'entry for entry in result["answers"]',
        RETRY,
    ),
)


@dataclass
class TestRun:
    exit_code: int
    seconds: float
    ran: int | None
    failures: list[str] = field(default_factory=list)
    tail: str = ""


@dataclass
class Outcome:
    id: str
    title: str
    stands_for: str
    file: str
    status: str
    targeted: TestRun | None = None
    full: TestRun | None = None
    detail: str = ""


_FAILURE_LINE = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([^)\s]+)", re.MULTILINE)
_RAN_LINE = re.compile(r"^Ran (\d+) tests?", re.MULTILINE)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def locate(text: str, mutation: Mutation) -> tuple[int, int] | None:
    """The (start, end) span of `old` within scope, or None unless unique."""
    start, end = 0, len(text)
    if mutation.scope:
        start = text.find(mutation.scope[0])
        if start < 0:
            return None
        end = text.find(mutation.scope[1], start + 1)
        if end < 0:
            return None
    region = text[start:end]
    if region.count(mutation.old) != 1:
        return None
    offset = start + region.index(mutation.old)
    return offset, offset + len(mutation.old)


def run_tests(root: Path, labels, settings: str, pythonpath: str, timeout: int):
    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = settings
    env["PYTHONPATH"] = os.pathsep.join(p for p in (pythonpath, str(root)) if p)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [
        sys.executable,
        "manage.py",
        "test",
        "--keepdb",
        "--noinput",
        "--failfast",
        *labels,
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = completed.stdout + completed.stderr
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        partial = [
            part.decode("utf-8", "replace") if isinstance(part, bytes) else part
            for part in (exc.stdout, exc.stderr)
            if part
        ]
        output = f"TIMEOUT after {timeout}s\n" + "".join(partial)
        exit_code = -1
    ran = _RAN_LINE.search(output)
    return TestRun(
        exit_code=exit_code,
        seconds=round(time.monotonic() - started, 1),
        ran=int(ran.group(1)) if ran else None,
        failures=sorted({match.group(2) for match in _FAILURE_LINE.finditer(output)}),
        tail=output[-1500:] if exit_code else "",
    )


def classify(run: TestRun) -> str:
    if run.exit_code == 0:
        return "SURVIVED"
    return "KILLED" if run.failures else "KILLED-BY-CRASH"


def apply_and_test(root, mutation, settings, pythonpath, timeout) -> Outcome:
    path = root / mutation.file
    original = path.read_bytes()
    original_digest = sha256(original)
    text = original.decode("utf-8")
    outcome = Outcome(
        mutation.id, mutation.title, mutation.stands_for, mutation.file, "ERROR"
    )

    span = locate(text, mutation)
    if span is None:
        outcome.status = "ANCHOR-MISSING"
        outcome.detail = "the code to mutate was not found exactly once in scope"
        return outcome

    mutated = text[: span[0]] + mutation.new + text[span[1] :]
    try:
        path.write_text(mutated, encoding="utf-8")
        if sha256(path.read_bytes()) == original_digest:
            raise RuntimeError(f"{mutation.id}: mutant identical to original")
        outcome.targeted = run_tests(
            root, mutation.labels, settings, pythonpath, timeout
        )
        outcome.status = classify(outcome.targeted)
        if outcome.status == "SURVIVED" and tuple(mutation.labels) != FULL:
            outcome.full = run_tests(root, FULL, settings, pythonpath, timeout)
            outcome.status = classify(outcome.full)
    finally:
        path.write_bytes(original)
        restored = sha256(path.read_bytes())
        if restored != original_digest:
            raise SystemExit(
                f"RESTORE FAILED for {mutation.file} after {mutation.id}: "
                f"{restored} != {original_digest}. Stop and restore by hand."
            )
    return outcome


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--pythonpath", default="")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--only", default="", help="comma-separated ids")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if (root / ".git").exists():
        parser.error(
            f"{root} is a git checkout. Mutations are live on disk while tests "
            "run; copy the tree first (see the module docstring)."
        )

    selected = [
        m
        for m in MUTATIONS
        if not args.only or m.id in {i.strip() for i in args.only.split(",")}
    ]

    print(f"baseline: {len(FULL)} modules, unmutated", flush=True)
    baseline = run_tests(root, FULL, args.settings, args.pythonpath, args.timeout)
    if baseline.exit_code != 0:
        print(baseline.tail)
        print("BASELINE FAILED - refusing to interpret any mutation result.")
        return 2

    outcomes = []
    for mutation in selected:
        outcome = apply_and_test(
            root, mutation, args.settings, args.pythonpath, args.timeout
        )
        outcomes.append(outcome)
        run = outcome.full or outcome.targeted
        killers = ", ".join((run.failures if run else [])[:3])
        print(
            f"{outcome.id} {outcome.status:<15} {outcome.title}"
            + (f"  <- {killers}" if killers else ""),
            flush=True,
        )

    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "baseline": asdict(baseline),
                "counts": counts,
                "mutations": [asdict(outcome) for outcome in outcomes],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"counts: {counts}\nreport: {args.report}")
    return 0 if counts.get("KILLED", 0) == len(outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
