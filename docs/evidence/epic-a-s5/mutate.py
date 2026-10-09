"""Epic A S5: apply each mutant, run the S5 tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

MW = "AutoGrader/middleware.py"
RC = "AutoGrader/request_context.py"
EM = "audit/emitter.py"
SVC = "ai_processor/services.py"
TASKS = "assignments/tasks.py"
META = "audit/metadata.py"
CODES = "AutoGrader/reason_codes.py"

MUTANTS = {
    # --- part 0 (X-5): the trace id is always the server's ---
    # X5 (R3): S6a's coded body echoes the client's id as its reference.
    "X5_reference_echoes_the_client_id": (
        CODES,
        '        "reference": get_request_id(),\n',
        '        "reference": __import__("AutoGrader.request_context", fromlist=["_"])'
        ".get_client_request_id() or get_request_id(),\n",
    ),
    "X1_inbound_id_adopted": (
        MW,
        "        request_id = generate_request_id()\n",
        "        request_id = request.headers.get(REQUEST_ID_HEADER) or generate_request_id()\n",
    ),
    "X2_client_id_any_text": (
        RC,
        "        return str(uuid.UUID(value))\n",
        "        return value\n",
    ),
    "X3_client_id_not_logged": (
        RC,
        '        record.client_request_id = get_client_request_id() or "-"\n',
        '        record.client_request_id = "-"\n',
    ),
    "X4_audit_client_id_is_the_server_id": (
        EM,
        '        request, "client_request_id", None\n',
        '        request, "request_id", None\n',
    ),
    # --- part 1: the AI call ---
    "T1_no_request_id_header": (
        SVC,
        '                "X-Request-ID": str(trace_id),\n',
        "",
    ),
    "T2_trace_id_not_the_server_trace": (
        SVC,
        "        trace_id = resolve_trace_id()\n",
        "        trace_id = uuid.uuid4()\n",
    ),
    "T3_no_log_line": (
        SVC,
        "        finally:\n            _log_ai_call(\n",
        "        finally:\n            (lambda **fields: None)(\n",
    ),
    "T4_prompt_text_logged": (
        SVC,
        "        finally:\n            _log_ai_call(\n",
        '        finally:\n            logger.info("prompt %s", system_prompt)\n'
        "            _log_ai_call(\n",
    ),
    "T5_failure_logged_as_ok": (
        SVC,
        "            outcome = type(exc).__name__\n",
        '            outcome = "ok"\n',
    ),
    "T6_attempt_always_1": (
        SVC,
        "            return int(current_task.request.retries or 0) + 1\n",
        "            return 1\n",
    ),
    # --- part 1: prompt versions ---
    "P1_prompt_version_optional": (
        SVC,
        '            raise ValueError("execute_graded_task requires a prompt_version")\n',
        "            pass\n",
    ),
    "P2_a_caller_omits_it": (
        SVC,
        "                prompt_version=GRADE_FORMATTER.version,\n",
        "",
    ),
    "P3_version_ignores_the_text": (
        SVC,
        '    return f"{Path(filename).stem}:{digest}"\n',
        '    return f"{Path(filename).stem}:00000000"\n',
    ),
    "P4_grading_completed_without_prompt_version": (
        TASKS,
        "                # S5 (NFR-OBS-04): the exact grading prompt behind this grade.\n"
        '                "prompt_version": GRADING_ASSIGNMENT_PROMPT.version,\n',
        "",
    ),
    "P5_grading_failed_prompt_version_not_allowed": (
        META,
        '            "model",\n            "prompt_version",\n            "feature",\n',
        '            "model",\n            "feature",\n',
    ),
    # v2's V1 / V2 (VERIFICATION_v2_424ca49): a grading site's VALUE.
    "V1_grading_site_passes_none": (
        SVC,
        "            response = self.execute_graded_task(\n"
        "                prompt_version=GRADING_ASSIGNMENT_PROMPT.version,\n",
        "            response = self.execute_graded_task(\n"
        "                prompt_version=None,\n",
    ),
    "V2_grading_site_passes_another_prompt": (
        SVC,
        "            response = self.execute_graded_task(\n"
        "                prompt_version=GRADING_ASSIGNMENT_PROMPT.version,\n",
        "            response = self.execute_graded_task(\n"
        "                prompt_version=ANSWERS_EXTRACTION_PROMPT.version,\n",
    ),
}

TESTS = [
    "ai_processor.tests_ai_call_trace",
    "audit.tests_trace_server_owned",
    "AutoGrader.tests_middleware",
    "AutoGrader.tests_request_context",
    "audit.tests_emitter",
    "assignments.tests_grading_audit_events",
    "billing.tests.test_execute_graded_task",
    "AutoGrader.tests_reason_codes",
]

originals: dict = {}
results = {}
try:
    for name, (path, a, b) in MUTANTS.items():
        src = originals.setdefault(path, open(path).read())
        assert src.count(a) == 1, f"{name}: anchor found {src.count(a)} times"
        open(path, "w").write(src.replace(a, b, 1))
        p = subprocess.run(
            [sys.executable, "manage.py", "test", *TESTS]
            + ["--settings=settings_worktree", "--keepdb", "--noinput"],
            capture_output=True,
            text=True,
            env={**os.environ, "EXEMPT_EMAIL_DOMAINS": ""},
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(path, "w").write(src)
finally:
    for path, src in originals.items():
        open(path, "w").write(src)

with open("docs/evidence/epic-a-s5/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
