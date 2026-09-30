"""S7b author mutants.

Run from the S7b worktree at its gated commit: `python mutate.py [NAME ...]`,
or `python mutate.py check` to confirm every anchor matches exactly once.
"""

import hashlib
import os
import re
import subprocess
import sys

NEW = "students.tests_item_retry"
IR = "students/item_retry.py"
UV = "users/views.py"
M = {
    "R1_any_code_retried": (
        IR,
        "        or not _retryable_as_it_is(item.reason_code)\n",
        "",
    ),
    "R2_uploads_retried": (
        IR,
        "    if item.task_type in UPLOAD_ITEM_TYPES:\n",
        "    if False:\n",
    ),
    "R3_claim_unfenced": (
        IR,
        (
            "        pk=item.pk,\n"
            "        status=BackgroundTaskStatus.FAILURE,\n"
            "        reason_code=item.reason_code,\n"
            "        retry_count=item.retry_count,\n"
            "    ).update("
        ),
        ("        pk=item.pk,\n" "    ).update("),
    ),
    "R4_count_not_bumped": (
        IR,
        '        retry_count=F("retry_count") + 1,\n',
        "",
    ),
    "R5_code_not_cleared": (
        IR,
        ('        reason_code="",\n' '        error="",\n'),
        '        error="",\n',
    ),
    "R6_any_teachers_session": (
        UV,
        "            BatchUploadSession, id=_uuid_or_404(session_id), teacher=request.user\n",
        "            BatchUploadSession, id=_uuid_or_404(session_id)\n",
    ),
    "R7_item_not_in_session": (
        UV,
        "            BackgroundProcessingTask, id=_uuid_or_404(item_id), batch_session=session\n",
        "            BackgroundProcessingTask, id=_uuid_or_404(item_id)\n",
    ),
    "R8_no_audit": (
        IR,
        ("    emit(\n" "        AuditAction.GRADING_REQUESTED,"),
        ("    (lambda *a, **k: None)(\n" "        AuditAction.GRADING_REQUESTED,"),
    ),
    "R9_codes_filter_ignored": (
        IR,
        "        wanted = not reason_codes or item.reason_code in reason_codes\n",
        "        wanted = True\n",
    ),
    "R10_trace_not_refreshed": (
        IR,
        "        trace_id=resolve_trace_id(),\n",
        "",
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
