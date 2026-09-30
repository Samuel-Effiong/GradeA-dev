"""H-38 tasks namespace: apply each mutant, run its tests, record killers, restore.
Every anchor must occur exactly once in its file: replace(..., 1) on a
non-unique anchor silently mutates the wrong site.
"""

import json
import os
import re
import subprocess
import sys

V = "users/views.py"
T = "assignments/tasks.py"
A = "students/task_access.py"
TASK_GUARD = (
    "        if not processing_task or not teacher_may_reach(request.user, processing_task):\n"
    '            raise NotFound("Tracked task not found for this user.")\n\n'
)
TASK_BARE = (
    "        if not processing_task:\n"
    '            raise NotFound("Tracked task not found for this user.")\n\n'
)
SESSION_GUARD = (
    "        if not teacher_may_reach(request.user, session):\n"
    '            raise Http404("No BatchUploadSession matches the given query.")\n\n'
)

MUTANTS = {
    "M1_helper_always_allows": (
        A,
        "    return teacher_can_reach_course(user, course)\n",
        "    return True\n",
    ),
    "M2_status_unguarded": (
        V,
        TASK_GUARD + "        normalize_processing_task_status(processing_task)\n",
        TASK_BARE + "        normalize_processing_task_status(processing_task)\n",
    ),
    "M3_cancel_unguarded": (
        V,
        TASK_GUARD + "        already_terminal = ",
        TASK_BARE + "        already_terminal = ",
    ),
    "M4_cancel_session_unguarded": (
        V,
        SESSION_GUARD + "        cancellable_tasks = list(\n",
        "        cancellable_tasks = list(\n",
    ),
    "M5_session_results_unguarded": (
        V,
        SESSION_GUARD + "        tracked_tasks = list(\n",
        "        tracked_tasks = list(\n",
    ),
    "M6_grading_chokepoint_unguarded": (
        T,
        "        if not teacher_may_reach(user, submission):\n",
        "        if False:\n",
    ),
    "M7_auto_grade_unguarded": (
        T,
        "        if not teacher_can_reach_course(assignment.course.teacher, assignment.course):\n",
        "        if False:\n",
    ),
    "M8_batch_grading_unguarded": (
        T,
        "        and not teacher_may_reach(batch_user, batch_assignment)\n",
        "        and False\n",
    ),
}

TESTS = [
    "students.tests_h38_tasks_namespace",
    "classrooms.tests_teacher_access_sweep",
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

with open("docs/evidence/h38-tasks-namespace/mutation_results.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
