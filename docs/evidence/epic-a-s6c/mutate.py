"""S6c author mutants (identity codes #1/#2).

Run from the S6b worktree at its gated commit: `python mutate.py [NAME ...]`,
or `python mutate.py check` to confirm every anchor matches exactly once.
"""

import hashlib
import os
import re
import subprocess
import sys

NEW = "students.tests_identity_reason_codes"
OLD = "students.tests_proxy_upload_attribution"
SVC = "students/services.py"
MUTANTS = {
    # C1 the off-roster lookup removed: a teacher's own pending student is "unknown".
    "C1_no_off_roster_lookup": (
        SVC,
        "    elsewhere = _name_matches(_teachers_own_students(teacher or course.teacher), name)\n",
        "    elsewhere = []\n",
        [NEW, OLD],
    ),
    # C2 tenancy widened: every student, not the teacher's own.
    "C2_lookup_every_student": (
        SVC,
        (
            "    return CustomUser.objects.filter(\n"
            '        teacher_course_access_q(teacher, prefix="enrollments__course__"),\n'
            "        user_type=UserTypes.STUDENT,\n"
            "    ).distinct()\n"
        ),
        "    return CustomUser.objects.filter(user_type=UserTypes.STUDENT).distinct()\n",
        [NEW],
    ),
    # C3 an ambiguous enrolled match is guessed (the first one wins).
    "C3_ambiguity_guessed": (
        SVC,
        "    if len(matches) > 1:\n",
        "    if False:\n",
        [NEW, OLD],
    ),
    # C4 two off-roster namesakes: one is named anyway.
    "C4_off_roster_guessed": (
        SVC,
        "    if len(elsewhere) == 1:\n",
        "    if elsewhere:\n",
        [NEW],
    ),
    # C5 the task no longer passes the file name (messages say "the paper").
    "C5_file_name_dropped": (
        "assignments/tasks.py",
        '            file_name=file_name or getattr(uploaded_file, "name", None),\n',
        "",
        [NEW],
    ),
    # C6 replaced_existing never set.
    "C6_replaced_existing_false": (
        SVC,
        '            upload_outcome["replaced_existing"] = not created\n',
        '            upload_outcome["replaced_existing"] = False\n',
        [NEW],
    ),
    # C7 the roster widened to any enrolment status.
    "C7_roster_any_status": (
        SVC,
        (
            '        enrollments__enrollment_status="ENROLLED",\n'
            "    ).distinct()\n"
            "    matches = _name_matches(enrolled, name)\n"
        ),
        ("    ).distinct()\n" "    matches = _name_matches(enrolled, name)\n"),
        [NEW, OLD],
    ),
}


def sha(b):
    return hashlib.sha256(b).hexdigest()


if sys.argv[1:] == ["check"]:
    for n, (p, o, _, _) in MUTANTS.items():
        print(n, open(p).read().count(o))
    sys.exit()

for name in sys.argv[1:] or MUTANTS:
    path, old, new, mods = MUTANTS[name]
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
                *mods,
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
