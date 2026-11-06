# flake8: noqa
"""H-273 mutation battery (SM, 2026-11-02). Each mutant is one exact
string replacement in one file; the file is restored from a saved copy and
its sha256 checked after every mutant. Run from the worktree root:
    python docs/evidence/h273-audit-refusal-detail/mutants.py
"""

import hashlib
import pathlib
import subprocess
import sys

TESTS = [
    "audit.tests_h273_request_detail",
    "audit.tests_h273_query_filters",
    "audit.tests_admin_action",
    "audit.tests_state_change",
    "AutoGrader.tests_middleware",
    "AutoGrader.tests_request_context",
    "students.tests_batch_item_results",
]
M = [
    (
        "1",
        "audit/request_detail.py",
        '            metadata.setdefault("route", route)',
        "            pass",
    ),
    (
        "2",
        "audit/request_detail.py",
        '    if label:\n        metadata["reason_label"] = label',
        "    pass",
    ),
    (
        "3",
        "users/exceptions.py",
        "                drf_response._audit_fields = safe[:20]",
        "                pass",
    ),
    ("4", "users/exceptions.py", "        drf_response._audit_exc = exc\n", "\n"),
    (
        "5",
        "audit/filters.py",
        "        return queryset.filter(trace_id=_normalise_uuid(value))",
        "        return queryset",
    ),
    (
        "6",
        "audit/filters.py",
        '    target_id = django_filters.UUIDFilter(field_name="target_id")',
        '    target_id = django_filters.UUIDFilter(field_name="target_id", method=lambda qs, n, v: qs)',
    ),
    (
        "7",
        "audit/serializers.py",
        "        return _TARGET_LABEL.get(obj.target_type)",
        "        return None",
    ),
    (
        "8",
        "audit/serializers.py",
        "        return _TARGET_LABEL.get(obj.target_type)",
        '        return _TARGET_LABEL.get(obj.target_type, "")',
    ),
    (
        "9",
        "audit/serializers.py",
        "        return school_names.get(obj.target_id)",
        "        return None",
    ),
    (
        "10",
        "audit/request_detail.py",
        '    metadata.setdefault("method", request.method)\n',
        "",
    ),
    (
        "11",
        "audit/request_detail.py",
        '    metadata.setdefault("http_status", response.status_code)\n',
        "",
    ),
    (
        "12",
        "audit/request_detail.py",
        '            metadata.setdefault("route", route)',
        '            metadata.setdefault("route", request.path)',
    ),
    (
        "13",
        "audit/request_detail.py",
        "        label = _exc_to_label(exc)\n",
        "        label = str(exc)\n",
    ),
    (
        "14",
        "audit/admin_action.py",
        "        reason_code=stashed_reason_code(response),",
        "        reason_code=None,",
    ),
    (
        "14b",
        "audit/request_audit.py",
        "            reason_code=stashed_reason_code(response),",
        "            reason_code=None,",
    ),
    (
        "15",
        "audit/request_detail.py",
        "    return _status_to_label(response.status_code)",
        "    return None",
    ),
    ("16", "audit/serializers.py", '    "Assignment": "Assignment",\n', ""),
    (
        "17",
        "audit/request_detail.py",
        '    metadata["reason_label"] = "PERMISSION_DENIED"',
        "    pass",
    ),
    (
        "18",
        "audit/views.py",
        "            events, allowed_school_ids=self._allowed_school_ids",
        "            events[:1], allowed_school_ids=self._allowed_school_ids",
    ),
    (
        "19",
        "users/exceptions.py",
        "        reason = reason_of(exc)\n        if reason is not None:\n            response._audit_reason_code = reason[0].value",
        "        response._audit_reason_code = exc.reason_code.value",
    ),
    (
        "20",
        "users/exceptions.py",
        '                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", k)',
        "                    and True",
    ),
    (
        "21",
        "AutoGrader/request_context.py",
        "    return str(uuid.uuid4())",
        "    return uuid.uuid4().hex",
    ),
    (
        "22",
        "students/item_results.py",
        '"reference": str(task.trace_id) if task.trace_id else None',
        '"reference": task.trace_id.hex if task.trace_id else None',
    ),
    (
        "23",
        "audit/filters.py",
        "        return uuid.UUID(value.strip())",
        "        return uuid.UUID(value)",
    ),
    (
        "24",
        "audit/views.py",
        "        self._allowed_school_ids = frozenset({school_id})",
        "        pass",
    ),
    (
        "25",
        "users/exceptions.py",
        "            drf_response._audit_reason_code = ReasonCode(reason_code).value",
        "            pass",
    ),
    (
        "26",
        "users/exceptions.py",
        "            drf_response._audit_reason_code = ReasonCode.RATE_LIMITED.value",
        "            pass",
    ),
]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


ONLY = set(sys.argv[1:])
results, bad = [], False
for mid, path, old, new in M:
    if ONLY and mid not in ONLY:
        continue
    p = pathlib.Path(path)
    orig = p.read_bytes()
    h = sha(p)
    text = orig.decode()
    if text.count(old) != 1:
        results.append((mid, "NOT-APPLIED (anchor count %d)" % text.count(old)))
        bad = True
        continue
    p.write_text(text.replace(old, new))
    try:
        r = subprocess.run(
            [
                sys.executable,
                "manage.py",
                "test",
                *TESTS,
                "--settings=settings_worktree",
                "--noinput",
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        tail = [
            l for l in r.stderr.splitlines() if l.startswith(("Ran ", "OK", "FAILED"))
        ]
        verdict = "KILLED" if r.returncode != 0 else "SURVIVED"
    finally:
        p.write_bytes(orig)
    ok = sha(p) == h
    if not ok:
        bad = True
    results.append(
        (
            mid,
            f"{verdict} | {' '.join(tail)} | restore sha {'OK' if ok else 'MISMATCH'}",
        )
    )
    print(mid, results[-1][1], flush=True)
print("\nSUMMARY")
for mid, v in results:
    print(f"M-H273-{mid}: {v}")
sys.exit(1 if bad or any("SURVIVED" in v for _, v in results) else 0)
