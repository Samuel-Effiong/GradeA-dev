"""Epic A S4: apply each mutant, run the S4 tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

HIST = "audit/history.py"
META = "audit/metadata.py"
SVC = "students/services.py"
ENR = "classrooms/services/enrollment.py"
UV = "users/views.py"
AV = "assignments/views.py"

MUTANTS = {
    "H1_is_published_untracked": (
        HIST,
        '            "is_published": "is_published",\n',
        "",
    ),
    "H2_unchanged_fields_recorded": (
        HIST,
        "        if before_row is not None and after_row is not None and old == new:\n"
        "            continue\n",
        "",
    ),
    "H3_actor_ignores_the_request": (
        HIST,
        "    return _acting_as.get() or current_request_actor()\n",
        "    return _acting_as.get()\n",
    ),
    "H4_acting_as_ignored": (
        HIST,
        "    return _acting_as.get() or current_request_actor()\n",
        "    return current_request_actor()\n",
    ),
    "H5_every_user_create_recorded": (
        HIST,
        "        if spec.create_filter is not None and not spec.create_filter(instance):\n",
        "        if False:\n",
    ),
    "H6_deletes_not_recorded": (
        HIST,
        "    if spec is None or not spec.record_delete or _suppressed.get():\n",
        "    if True:\n",
    ),
    "H7_bulk_writes_record_nothing": (
        HIST,
        '                _emit(spec, pk, after[pk], change, actor=who, source="bulk")\n',
        "                pass\n",
    ),
    "H8_school_scope_dropped": (
        HIST,
        "        school_id=context_row.get(spec.school) if spec.school else None,\n",
        "        school_id=None,\n",
    ),
    "H9_request_not_passed": (
        HIST,
        "        request=current_request(),\n",
        "        request=None,\n",
    ),
    "B1_before_after_not_narrowed_per_action": (
        META,
        "        if key in permitted:\n"
        "            narrowed[key] = item\n"
        "        else:\n"
        '            problems.append((key, "not allowed in before/after for this action"))\n',
        "        narrowed[key] = item\n",
    ),
    "G1_ai_save_not_suppressed": (
        SVC,
        "    with cancellable_final_save(processing_task_id), history.suppressed():\n",
        "    with cancellable_final_save(processing_task_id):\n",
    ),
    "G2_completed_without_before_after": (
        SVC,
        "        before=changed_before,\n        after=changed_after,\n",
        "",
    ),
    "R1_sign_in_activation_actor_implicit": (
        ENR,
        "        actor=student,\n",
        "",
    ),
    "P1_verify_activation_not_acting_as": (
        UV,
        "        with history.acting_as(user):\n"
        "            user.save()\n"
        "        clear_verify_failures(email)\n",
        "        if True:\n"
        "            user.save()\n"
        "        clear_verify_failures(email)\n",
    ),
    "P2_publish_all_bypasses_history": (
        AV,
        "history.record_bulk(graded_submissions, is_published=True)",
        "graded_submissions.update(is_published=True)",
    ),
    # SM condition: a new, unlisted production use of the audit off-switch.
    "S1_unlisted_suppression": (
        "students/views.py",
        "            grade_before = history.snapshot(submission)\n",
        "            grade_before = history.snapshot(submission)\n"
        "            history.suppressed()\n",
    ),
    "X1_export_not_recorded": (
        AV,
        "            AuditAction.DATA_EXPORT,\n",
        "            AuditAction.ADMIN_ACTION,\n",
    ),
}

TESTS = [
    "audit.tests_history",
    "audit.tests_history_guard",
    "audit.tests_emitter",
    "classrooms.tests_epic_a_roster_audit",
    "users.tests_auth_audit_doors",
    "assignments.tests_grading_audit_events",
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

with open("docs/evidence/epic-a-s4/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
