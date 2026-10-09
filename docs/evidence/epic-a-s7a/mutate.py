"""S7a author mutants.

Run from the S7a worktree at its gated commit: `python mutate.py [NAME ...]`,
or `python mutate.py check` to confirm every anchor matches exactly once.
"""

import hashlib
import os
import re
import subprocess
import sys

NEW = "students.tests_batch_item_results"
TT = "students/task_tracking.py"
IR = "students/item_results.py"
SV = "students/views.py"
AV = "assignments/views.py"
AT = "assignments/tasks.py"
M = {
    "S1_code_not_stored": (
        TT,
        "        reason_code=failure_reason_code(error),\n",
        "",
    ),
    "S2_no_item_index_batch_upload": (
        SV,
        (
            '                meta={"step": "Queued for batch answer extraction"},\n'
            "                item_index=item_index,\n"
        ),
        '                meta={"step": "Queued for batch answer extraction"},\n',
    ),
    "S3_batch_upload_refused_whole": (
        SV,
        (
            "            except PayloadTooLarge as exc:\n"
            "                refused = record_refused_item(\n"
        ),
        (
            "            except PayloadTooLarge as exc:\n"
            "                raise\n"
            "                refused = record_refused_item(\n"
        ),
    ),
    "S4_upload_async_refused_whole": (
        AV,
        "            if refusal is not None:\n",
        ("            if refusal is not None:\n" "                raise refusal\n"),
    ),
    "S5_reference_dashed": (
        IR,
        '        "reference": task.trace_id.hex if task.trace_id else None,\n',
        '        "reference": str(task.trace_id) if task.trace_id else None,\n',
    ),
    "S6_uncoded_not_system": (
        IR,
        ("    elif failed:\n" "        error_class = ErrorClass.SYSTEM.value\n"),
        ("    elif False:\n" "        error_class = ErrorClass.SYSTEM.value\n"),
    ),
    "S7_replaced_not_lifted": (
        IR,
        '        "replaced_existing": bool(replaced) if replaced is not None else None,\n',
        '        "replaced_existing": None,\n',
    ),
    "S8_auto_grade_untracked": (
        AT,
        (
            "        _dispatch_tracked_grading(\n"
            "            assignment.course.teacher, assignment, ungraded_submissions, session\n"
            "        )\n"
        ),
        "        pass\n",
    ),
    "S9_no_unclassified_sentinel": (
        IR,
        '    codes = Counter(entry["reason_code"] or UNCLASSIFIED for entry in failures)\n',
        '    codes = Counter(entry["reason_code"] or "" for entry in failures)\n',
    ),
    "S10_no_trace_recorded": (
        TT,
        "        trace_id=resolve_trace_id(),\n",
        "",
    ),
    "S11_scheduled_batch_untracked": (
        AT,
        ("    if submissions.exists():\n" "        # With no session"),
        ("    if False:\n" "        # With no session"),
    ),
}


def sha(b):
    return hashlib.sha256(b).hexdigest()


if sys.argv[1:] == ["check"]:
    for n, (p, o, _) in M.items():
        print(n, open(p).read().count(o))
    sys.exit()

for name in sys.argv[1:] or M:
    path, old, new = M[name]
    pristine = subprocess.check_output(["git", "show", f"HEAD:{path}"])
    src = open(path).read()
    if src.count(old) != 1:
        print(name, "APPLY_FAILED", src.count(old), flush=True)
        continue
    open(path, "w").write(src.replace(old, new))
    try:
        out = subprocess.run(
            [
                "systemd-run",
                "--user",
                "--scope",
                "-q",
                "-p",
                "MemoryMax=6G",
                "-p",
                "MemorySwapMax=0",
                "nice",
                "-n",
                "10",
                "timeout",
                "-k",
                "60",
                "1800",
                sys.executable,
                "manage.py",
                "test",
                NEW,
                "--settings=settings_worktree",
                "--noinput",
                "--keepdb",
                "-v1",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "EXEMPT_EMAIL_DOMAINS": ""},
        )
        o = out.stdout + out.stderr
        failing = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", o, re.M)))
        summary = next(
            (line for line in o.splitlines() if line.startswith(("OK", "FAILED"))),
            f"? rc={out.returncode}",
        )
        print(name, "KILLED" if failing else "SURVIVED", summary, failing, flush=True)
    finally:
        subprocess.run(["git", "checkout", "--", path], check=True)
        print(
            "  restore",
            "ok" if sha(open(path, "rb").read()) == sha(pristine) else "MISMATCH",
            flush=True,
        )
