"""The answer-extraction benchmark report.

Runs every catalogued scenario through the deterministic pipeline, then
folds in the mutation and live-provider results where those reports
exist, and writes one machine-readable and one human-readable report.

Nothing is inferred: a section whose source report does not exist says
"not run" rather than showing zeros that look like a result.
"""

from __future__ import annotations

import importlib
import json
import time
import unittest
from pathlib import Path
from typing import Any

from ai_processor.services import ANSWERS_EXTRACTION_PAGES_PER_CHUNK

from .documents import expected_chunks
from .harness import describe, full_check, run_scenario
from .scenarios import SCENARIOS

REPORT_DIR = Path(__file__).resolve().parent / "reports"

FAILURE_MODULES = (
    "ai_processor.tests_answer_benchmark_failures",
    "ai_processor.tests_answer_benchmark_inputs",
    "ai_processor.tests_answer_chunk_merge",
)
CONCURRENCY_MODULES = ("ai_processor.tests_answer_benchmark_concurrency",)
INTEGRATION_MODULES = ("ai_processor.tests_answer_benchmark_grading",)

#: Every extraction defect found so far, mapped to the scenarios, tests and
#: mutations that keep it from coming back (spec section 31).
REGRESSIONS = (
    {
        "id": "R1",
        "defect": "Shared PDFService singleton: one student's pages could be "
        "extracted for another student's submission.",
        "scenarios": [],
        "tests": [
            "ai_processor.tests_pdf_service_concurrency",
            "ai_processor.tests_answer_benchmark_concurrency.CrossStudentIsolationTest",
            "ai_processor.tests_answer_benchmark_concurrency"
            ".ConcurrentChunkedExtractionTest",
        ],
        "mutations": ["M20"],
    },
    {
        "id": "R2",
        "defect": "Chunked answer path sent no structured-output schema.",
        "scenarios": ["every chunked scenario (harness.check_schema_sent)"],
        "tests": ["ai_processor.tests_chunked_extraction_contract"],
        "mutations": ["M01", "M02"],
    },
    {
        "id": "R3",
        "defect": "Chunker ignored the configured pages_per_chunk.",
        "scenarios": ["AE-101", "AE-102", "AE-103", "AE-104", "AE-105"],
        "tests": [
            "ai_processor.tests_answer_benchmark_scenarios"
            ".ThresholdDerivedBoundaryTest",
            "ai_processor.tests_chunked_extraction_contract",
        ],
        "mutations": ["M05", "M06"],
    },
    {
        "id": "R4",
        "defect": "Claimed page range derived from the caller, not the chunk.",
        "scenarios": ["AE-204", "every chunked scenario (check_claimed_ranges)"],
        "tests": ["ai_processor.tests_chunked_extraction_contract"],
        "mutations": ["M07", "M08"],
    },
    {
        "id": "R5",
        "defect": "NOT_FOUND_IN_DOCUMENT collapsed into BLANK (a silent zero).",
        "scenarios": [
            "AE-003",
            "AE-203",
            "AE-300",
            "AE-304",
            "AE-305",
            "AE-800",
            "AE-802",
        ],
        "tests": [
            "ai_processor.tests_answer_benchmark_failures.MalformedModelOutputTest",
            "ai_processor.tests_answer_benchmark_grading",
            "ai_processor.tests_answer_chunk_merge.MergeChunkAnswerTest",
        ],
        "mutations": ["M13", "M14", "M15", "M16"],
    },
    {
        "id": "R6",
        "defect": "Null answer_html after successful provider calls.",
        "scenarios": [],
        "tests": [
            "ai_processor.tests_answer_benchmark_failures.MalformedModelOutputTest"
            ".test_a_null_answer_is_never_scored_as_a_blank"
        ],
        "mutations": [],
    },
    {
        "id": "R7",
        "defect": "Cancellation retried as if it were a transient failure.",
        "scenarios": [],
        "tests": [
            "ai_processor.tests_answer_benchmark_failures.CancellationTest",
            "ai_processor.tests_answer_chunk_merge.ErrorsKeepTheirTypeTest",
        ],
        "mutations": ["M23", "M24", "M25"],
    },
    {
        "id": "R8",
        "defect": "Extraction failure swallowed or reported without its cause.",
        "scenarios": [],
        "tests": [
            "ai_processor.tests_answer_benchmark_failures.PersistentChunkFailureTest",
            "ai_processor.tests_answer_chunk_merge.ErrorsKeepTheirTypeTest",
        ],
        "mutations": ["M22", "M26", "M27"],
    },
    {
        "id": "N1",
        "defect": "A BLANK on a page after the first chunk came out "
        "NOT_FOUND_IN_DOCUMENT (found by this benchmark).",
        "scenarios": ["AE-600", "AE-601", "AE-801"],
        "tests": ["ai_processor.tests_answer_chunk_merge.MergeChunkAnswerTest"],
        "mutations": ["M17"],
    },
    {
        "id": "N2",
        "defect": "An answer split across chunks kept only its first half "
        "(found by this benchmark).",
        "scenarios": ["AE-205", "AE-206"],
        "tests": [
            "ai_processor.tests_answer_chunk_merge.MergeChunkAnswerTest",
            "ai_processor.tests_answer_benchmark_grading",
        ],
        "mutations": ["M18"],
    },
    {
        "id": "N3",
        "defect": "Later chunks were told every question was already "
        "extracted (found by this benchmark).",
        "scenarios": ["AE-804"],
        "tests": ["ai_processor.tests_answer_chunk_merge.AlreadyFoundNoteTest"],
        "mutations": ["M19"],
    },
    {
        "id": "N4",
        "defect": "Single-call path hid credit, access and cancellation "
        "error types (found by this benchmark).",
        "scenarios": [],
        "tests": ["ai_processor.tests_answer_chunk_merge.ErrorsKeepTheirTypeTest"],
        "mutations": ["M25"],
    },
    {
        "id": "N5",
        "defect": "An empty submission was still billed and returned as a "
        "success (found by this benchmark).",
        "scenarios": [],
        "tests": ["ai_processor.tests_answer_benchmark_inputs.EmptySubmissionTest"],
        "mutations": ["M28"],
    },
    {
        "id": "N6",
        "defect": "A negative chunk size silently dropped every page "
        "(found by this benchmark).",
        "scenarios": [],
        "tests": ["ai_processor.tests_answer_benchmark_inputs.ChunkSizeContractTest"],
        "mutations": ["M12"],
    },
    {
        "id": "N7",
        "defect": "Benchmark PDFs were not byte-deterministic (a harness "
        "defect, found by the determinism suite).",
        "scenarios": [],
        "tests": ["ai_processor.tests_answer_benchmark_concurrency.DeterminismTest"],
        "mutations": ["M32"],
    },
    {
        "id": "N8",
        "defect": "The chunk note told later chunks an already-found question "
        "'does NOT need to be extracted again', and the real model dropped the "
        "continuation of a split answer (confirmed live on AE-905).",
        "scenarios": [
            "AE-803",
            "AE-805",
            "AE-806",
            "AE-807",
            "live AE-905",
            "live AE-915",
            "live AE-916",
        ],
        "tests": [
            "ai_processor.tests_answer_chunk_merge.AlreadyFoundNoteTest",
            "ai_processor.tests_answer_benchmark_live",
        ],
        "mutations": ["M33"],
    },
    {
        "id": "N9",
        "defect": "The chunk merge keyed answers on the model's raw question "
        "label, and the model switches between `1` and `Q1` from chunk to "
        "chunk: one question became two, the completeness gate re-read and "
        "re-billed the whole script (live AE-912: 12 calls, 189,526 tokens), "
        "and on a single call the final attempt dropped real answers as "
        "numbering drift.",
        "scenarios": ["live AE-912", "live AE-921", "live AE-905"],
        "tests": [
            "ai_processor.tests_answer_chunk_merge.QuestionLabelTest",
            "ai_processor.tests_answer_benchmark_failures.QuestionNumberStyleTest",
        ],
        "mutations": ["M34", "M35"],
    },
)


def _test_ids(module_names) -> list[str]:
    ids = []
    for name in module_names:
        suite = unittest.defaultTestLoader.loadTestsFromModule(
            importlib.import_module(name)
        )
        stack: list[Any] = [suite]
        while stack:
            item = stack.pop()
            if isinstance(item, unittest.TestSuite):
                stack.extend(item)
            else:
                ids.append(item.id())
    return sorted(ids)


def deterministic_results() -> list[dict]:
    results = []
    for scenario in SCENARIOS:
        run = run_scenario(scenario)
        problems = full_check(run, scenario)
        if scenario.known_gap:
            status = "KNOWN-GAP" if problems else "GAP-CLOSED"
        else:
            status = "FAIL" if problems else "PASS"
        entry = {
            "id": scenario.id,
            "name": scenario.name,
            "category": scenario.category,
            "tags": list(scenario.tags),
            "pages": run.page_count,
            "pages_per_chunk": run.pages_per_chunk,
            "execution_path": run.execution_path,
            "chunk_count": run.chunk_count,
            "status": status,
            "model_quirks": list(scenario.model_quirks),
            "known_gap": scenario.known_gap,
            "expectations": [
                {"question": e.question, "status": e.status, "pages": list(e.pages)}
                for e in scenario.expectations
            ],
            "elapsed_seconds": round(run.elapsed_seconds, 3),
        }
        if status != "PASS":
            chunked = run.extra.get("expected_execution_path") == "chunked"
            entry["diagnostics"] = {
                "problems": problems,
                "document_fixture": f"documents.build_document({scenario.id})",
                "provider_fixture": "provider.FakeProvider"
                f"(model_quirks={list(scenario.model_quirks)})",
                "expected_page_ranges": (
                    expected_chunks(run.page_count, run.pages_per_chunk)
                    if chunked
                    else [list(range(1, run.page_count + 1))]
                ),
                "run": run.as_report_dict(),
                "describe": describe(run, scenario, problems),
            }
        results.append(entry)
    return results


def _coverage(results) -> dict:
    rows: dict[str, list[int]] = {}

    def bump(label, ok):
        row = rows.setdefault(label, [0, 0])
        row[0] += int(ok)
        row[1] += 1

    for result in results:
        ok = result["status"] == "PASS"
        for expectation in result["expectations"]:
            bump(expectation["status"], ok)
        if result["execution_path"] == "chunked":
            bump("CHUNKING", ok)
            bump("PAGE ATTRIBUTION", ok)
        if result["category"] == "boundary" or "boundary" in result["tags"]:
            bump("BOUNDARY", ok)
        if "cross-chunk" in result["tags"]:
            bump("CROSS-CHUNK", ok)
        bump(f"category: {result['category']}", ok)
    return {label: {"passed": p, "total": t} for label, (p, t) in rows.items()}


def _read_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_report(mutations_path: Path, live_path: Path) -> dict:
    started = time.perf_counter()
    results = deterministic_results()
    mutations = _read_json(mutations_path)
    live = _read_json(live_path)

    def count(predicate):
        return sum(1 for r in results if predicate(r))

    statuses = [r["status"] for r in results]
    summary = {
        "total_scenarios": len(results),
        "passed": statuses.count("PASS"),
        "failed": statuses.count("FAIL"),
        "known_gaps": statuses.count("KNOWN-GAP"),
        "gaps_closed": statuses.count("GAP-CLOSED"),
        "skipped": 0,
        "deterministic_scenarios": len(results),
        "real_provider_scenarios": (live or {}).get("documents", "not run"),
        "single_call_scenarios": count(lambda r: r["execution_path"] == "single-call"),
        "chunked_scenarios": count(lambda r: r["execution_path"] == "chunked"),
        "multi_page_scenarios": count(lambda r: r["pages"] > 1),
        "cross_boundary_scenarios": count(
            lambda r: "cross-chunk" in r["tags"] or r["category"] == "boundary"
        ),
        "blank_scenarios": count(
            lambda r: any(e["status"] == "BLANK" for e in r["expectations"])
        ),
        "not_found_scenarios": count(
            lambda r: any(
                e["status"] == "NOT_FOUND_IN_DOCUMENT" for e in r["expectations"]
            )
        ),
        "handwritten_scenarios": count(lambda r: "handwritten-synthetic" in r["tags"]),
        "failure_injection_tests": len(_test_ids(FAILURE_MODULES)),
        "concurrency_tests": len(_test_ids(CONCURRENCY_MODULES)),
        "integration_tests": len(_test_ids(INTEGRATION_MODULES)),
        "mutation_scenarios": (len(mutations["mutations"]) if mutations else "not run"),
        "mutations_killed": (
            mutations["counts"].get("KILLED", 0) if mutations else "not run"
        ),
        "production_chunk_size": ANSWERS_EXTRACTION_PAGES_PER_CHUNK,
    }
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "summary": summary,
        "coverage": _coverage(results),
        "scenarios": results,
        "mutations": mutations,
        "live": live,
        "regressions": list(REGRESSIONS),
    }


def render_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# Answer-extraction benchmark report",
        "",
        f"Generated {report['generated_at']} "
        f"(deterministic run took {report['elapsed_seconds']}s). "
        "Regenerate with `python manage.py answer_extraction_benchmark`.",
        "",
        "## Summary",
        "",
        "```text",
    ]
    labels = (
        ("Total scenarios", "total_scenarios"),
        ("Passed", "passed"),
        ("Failed", "failed"),
        ("Known gaps (reported, not passed)", "known_gaps"),
        ("Known gaps now passing", "gaps_closed"),
        ("Skipped", "skipped"),
        ("Real-provider scenarios", "real_provider_scenarios"),
        ("Deterministic scenarios", "deterministic_scenarios"),
        ("Single-call scenarios", "single_call_scenarios"),
        ("Chunked scenarios", "chunked_scenarios"),
        ("Multi-page scenarios", "multi_page_scenarios"),
        ("Cross-boundary scenarios", "cross_boundary_scenarios"),
        ("BLANK scenarios", "blank_scenarios"),
        ("NOT_FOUND scenarios", "not_found_scenarios"),
        ("Handwritten (synthetic) scenarios", "handwritten_scenarios"),
        ("Failure-injection tests", "failure_injection_tests"),
        ("Concurrency tests", "concurrency_tests"),
        ("Grading-integration tests", "integration_tests"),
        ("Mutation scenarios", "mutation_scenarios"),
        ("Mutations killed", "mutations_killed"),
        ("Production pages per chunk", "production_chunk_size"),
    )
    width = max(len(label) for label, _ in labels) + 2
    lines += [f"{label + ':':<{width}}{s[key]}" for label, key in labels]
    lines += ["```", "", "## Coverage by category", "", "```text"]
    for label, row in sorted(report["coverage"].items()):
        lines.append(f"{label:<32}{row['passed']:>4}/{row['total']}")
    lines += [
        "```",
        "",
        "Status rows count expectations; the others count scenarios. A known "
        "gap counts against its row - it is not a pass.",
        "",
        "## Scenarios",
        "",
        "| ID | Name | Category | Pages | Path | Chunks | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["scenarios"]:
        lines.append(
            f"| {r['id']} | {r['name']} | {r['category']} | {r['pages']} | "
            f"{r['execution_path']} | {r['chunk_count']} | {r['status']} |"
        )

    not_passing = [r for r in report["scenarios"] if r["status"] != "PASS"]
    lines += ["", "## Diagnostics for every scenario that did not pass", ""]
    if not not_passing:
        lines.append("None.")
    for r in not_passing:
        lines += [f"### {r['id']} {r['name']} - {r['status']}", ""]
        if r["known_gap"]:
            lines += [f"Known gap: {r['known_gap']}", ""]
        lines += ["```text", r["diagnostics"]["describe"].strip(), "```", ""]

    lines += ["", "## Mutation testing", ""]
    mutations = report["mutations"]
    if not mutations:
        lines.append("Not run for this report.")
    else:
        lines += [
            f"Counts: {mutations['counts']}. Generated {mutations['generated_at']}.",
            "",
            "Each mutant runs with --failfast, so the last column is the FIRST "
            "test Django ran that failed (database-backed suites run first). "
            "Other tests may catch the same mutant; this column is proof that "
            "at least one assertion did, not a list of every one that would.",
            "",
            "| ID | Protection removed | Stands for | Outcome | First failing test |",
            "|---|---|---|---|---|",
        ]
        for m in mutations["mutations"]:
            run = m.get("full") or m.get("targeted") or {}
            caught = ", ".join((run.get("failures") or [])[:2]) or "-"
            lines.append(
                f"| {m['id']} | {m['title']} | {m['stands_for']} | "
                f"{m['status']} | {caught} |"
            )

    lines += ["", "## Real-provider results", ""]
    live = report["live"]
    if not live:
        lines.append("Not run for this report (opt-in: RUN_REAL_AI=1).")
    else:
        lines += [
            f"Generated {live['generated_at']}.",
            "",
            "| ID | Pages | Path | Chunks | Retries | Discrepancies | Seconds "
            "| Credits | Credit invariant | Model |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for rec in live["records"]:
            lines.append(
                f"| {rec['scenario']} | {rec['pages']} | {rec['execution_path']} | "
                f"{rec['chunk_count']} | {rec['retry_extraction_calls']} | "
                f"{len(rec['discrepancies'])} | {rec['elapsed_seconds']} | "
                f"{rec['credits']['consumed']:.0f} | "
                f"{rec['credits']['invariant_holds']} | "
                f"{', '.join(rec['model_config']['served_by'])} |"
            )
        for rec in live["records"]:
            if rec["discrepancies"] or rec["known_gap"]:
                lines += ["", f"### {rec['scenario']} {rec['name']}", ""]
                if rec["known_gap"]:
                    lines += [f"Known gap: {rec['known_gap']}", ""]
                lines += [f"- {d}" for d in rec["discrepancies"]] or ["- none"]

    lines += [
        "",
        "## Regression mapping",
        "",
        "| ID | Defect | Scenarios | Tests | Mutations |",
        "|---|---|---|---|---|",
    ]
    for reg in report["regressions"]:
        lines.append(
            f"| {reg['id']} | {reg['defect']} | {', '.join(reg['scenarios']) or '-'} "
            f"| {'<br>'.join(reg['tests'])} | {', '.join(reg['mutations']) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def write_report(report: dict, directory: Path = REPORT_DIR) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "benchmark_report.json"
    markdown_path = directory / "BENCHMARK_REPORT.md"
    json_path.write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
