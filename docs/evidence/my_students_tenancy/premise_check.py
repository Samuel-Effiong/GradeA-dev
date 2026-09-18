"""Prove the two premise pins fail when the premise they rest on is broken.

Reuses the mutation harness in `battery.py` (same directory), but the
"mutants" here widen a PREMISE rather than remove a guard: each should make
exactly one test fail - the pin for that premise.

    python premise_check.py        # from this directory, with battery.py beside it
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import battery  # noqa: E402  (path set above so the harness is importable)

battery.MODULES = [
    "classrooms.tests_my_students_course_scope",
    "users.tests_user_enrollment_filter_oracle",
]

battery.MUTANTS = [
    (
        "P1",
        "users/views.py",
        "non-superadmin queryset widened beyond the requester's own row",
        "        return queryset.filter(pk=user.pk)\n",
        "        return queryset\n",
    ),
    (
        "P2",
        "classrooms/views.py",
        "StudentListSerializer served by the list action too",
        '        if self.action == "my_students":\n'
        "            return StudentListSerializer\n",
        '        if self.action in ("my_students", "list"):\n'
        "            return StudentListSerializer\n",
    ),
]


def main():
    commit = sys.argv[1] if len(sys.argv) > 1 else "c44a6b5"
    out = os.path.abspath("premise_checks")
    os.makedirs(out, exist_ok=True)
    results = [battery.run_mutant(commit, out, *m) for m in battery.MUTANTS]
    for record in results:
        record.pop("diff", None)
        print(
            record["id"],
            record["verdict"],
            record["failed_tests"],
            "of",
            record["ran"],
            "restore_ok=",
            record["restore_verified"],
            "removed=",
            record["worktree_removed"],
            record.get("error", ""),
        )
    json.dump(results, open(out + "/results.json", "w"), indent=2)


if __name__ == "__main__":
    main()
