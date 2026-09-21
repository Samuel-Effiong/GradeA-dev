"""Parallel, disposable mutation battery for task/my-students-prefetch-leak.

Usage: python battery.py <commit> <K> <outdir>

Each mutant runs in its own detached worktree at <commit> with its own test
DB. The mutated file is restored from `git show <commit>:<path>` and its
sha256 verified against the commit before the worker is removed.
"""

import concurrent.futures as cf
import hashlib
import json
import os
import re
import subprocess
import sys
import time

MAIN = "/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus"
PARENT = os.path.dirname(MAIN)
PY = "/home/bond-servant-in-training/Documents/Virtualenvs/AutoGrader_env/bin/python"
MODULES = [
    "classrooms.tests_my_students_course_scope",
    "classrooms.tests_my_students_concurrency",
    "classrooms.tests_query_budget",
    "classrooms.tests_security_penetration",
    "users.tests_user_enrollment_filter_oracle",
]

MUTANTS = [
    # --- my-students (classrooms) ---
    (
        "M1",
        "classrooms/views.py",
        "enrollments prefetch loses course__teacher=user",
        "StudentCourse.objects.filter(course__teacher=user)\n",
        "StudentCourse.objects.filter()\n",
    ),
    (
        "M2",
        "classrooms/views.py",
        "submissions prefetch loses assignment__course__teacher=user",
        "StudentSubmission.objects.filter(\n"
        "                            assignment__course__teacher=user\n"
        "                        )",
        "StudentSubmission.objects.filter()",
    ),
    (
        "M3",
        "classrooms/views.py",
        "my-students reverts to the old unscoped filterset_fields",
        "self.filterset_class = MyStudentsFilter",
        'self.filterset_fields = {"enrollments__course": ["exact"], '
        '"enrollments__course__session": ["exact"]}',
    ),
    (
        "M4",
        "classrooms/filters.py",
        "MyStudentsFilter drops the course__teacher=request.user scope",
        "                    course__teacher=self.request.user,\n",
        "",
    ),
    (
        "M5",
        "classrooms/filters.py",
        "my-students session filter ignores the session id",
        "course__session_id=value",
        "course__session__isnull=False",
    ),
    (
        "M6",
        "classrooms/filters.py",
        "my-students course filter ignores the course id",
        "course_id=value",
        "course__isnull=False",
    ),
    (
        "M7",
        "students/serializers.py",
        "_resolve_relevant_course loses its own-teacher fallback (falls to enrollments[0])",
        '                and request.user.user_type == "TEACHER"\n',
        '                and request.user.user_type == "NOBODY"\n',
    ),
    # --- /users/<id> filters (L1) ---
    (
        "M8",
        "users/filters.py",
        "teacher scope removed (Q(course__teacher=user) -> Q())",
        "        return Q(course__teacher=user)\n",
        "        return Q()\n",
    ),
    (
        "M9",
        "users/filters.py",
        "school-admin scope removed",
        "        return Q(course__teacher__school_id=user.school_id)\n",
        "        return Q()\n",
    ),
    (
        "M10",
        "users/views.py",
        "CustomUserViewSet reverts to the old unscoped filterset_fields",
        "    filterset_class = UserEnrollmentFilter\n",
        '    filterset_fields = {"user_type": ["exact"], "school__name": ["exact"], '
        '"enrollments__course": ["exact", "isnull"], '
        '"enrollments__course__session": ["exact"], '
        '"enrollments__enrollment_status": ["exact", "in"]}\n',
    ),
    (
        "M11",
        "users/filters.py",
        "every enrollment filter drops visible_enrollments() (all lookups unscoped)",
        "                visible_enrollments(self.request.user),\n",
        "",
    ),
    (
        "M12",
        "users/filters.py",
        "status__in filter bypasses the scoped helper",
        "            self._has_visible_enrollment(enrollment_status__in=value)\n",
        '            Exists(StudentCourse.objects.filter(student=OuterRef("pk"), '
        "enrollment_status__in=value))\n",
    ),
    (
        "M13",
        "users/filters.py",
        "isnull filter ignores its value",
        "~has_one if value else has_one",
        "has_one",
    ),
    (
        "M14",
        "users/filters.py",
        "student/other fallback scope removed (Q(student=user) -> Q())",
        "    return Q(student=user)\n",
        "    return Q()\n",
    ),
    (
        "M15",
        "users/filters.py",
        "superadmin branch accepts a single flag (and -> or)",
        "    if user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN:\n",
        "    if user.is_superuser or user.user_type == UserTypes.SUPER_ADMIN:\n",
    ),
    (
        "M16",
        "users/filters.py",
        "superadmin loses platform-wide scope (falls through to own-only)",
        "    if user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN:\n        return Q()\n",
        "    if False:\n        return Q()\n",
    ),
]


def sh(cmd, cwd=None, **kw):
    return subprocess.run(
        cmd, cwd=cwd, shell=True, capture_output=True, text=True, **kw
    )


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def run_mutant(commit, outdir, mid, path, desc, old, new):
    wt = f"{PARENT}/Grade-Automator-Plus-57-mut-{mid.lower()}"
    rec = {"id": mid, "file": path, "description": desc, "worktree": wt}
    r = sh(f"git worktree add --detach {wt} {commit}", cwd=MAIN)
    if r.returncode:
        rec["error"] = "worktree add failed: " + r.stderr
        return rec
    try:
        os.symlink(f"{MAIN}/.env", f"{wt}/.env")
        with open(f"{wt}/settings_worktree.py", "w") as f:
            f.write(
                "from AutoGrader.settings import *  # noqa: F401,F403\n"
                "from AutoGrader.settings import DATABASES\n\n"
                'DATABASES["default"].setdefault("TEST", {})\n'
                f'DATABASES["default"]["TEST"]["NAME"] = "test_57_mut_{mid.lower()}"\n'
            )
        target = f"{wt}/{path}"
        pristine = sh(f"git show {commit}:{path}", cwd=MAIN).stdout
        pristine_sha = hashlib.sha256(pristine.encode()).hexdigest()
        src = open(target).read()
        count = src.count(old)
        if count != 1:
            rec["error"] = f"pattern found {count} times, expected 1"
            return rec
        open(target, "w").write(src.replace(old, new))
        rec["mutated_sha256"] = sha(target)
        rec["pristine_sha256"] = pristine_sha
        diff = sh(f"git diff -- {path}", cwd=wt).stdout
        rec["diff"] = diff

        start = time.time()
        log = f"{outdir}/{mid}.log"
        with open(log, "w") as fh:
            proc = subprocess.run(
                f"{PY} manage.py test {' '.join(MODULES)} "
                "--settings=settings_worktree --keepdb --noinput",
                cwd=wt,
                shell=True,
                stdout=fh,
                stderr=subprocess.STDOUT,
            )
        rec["seconds"] = round(time.time() - start)
        rec["exit"] = proc.returncode
        text = open(log).read()
        failed = sorted(
            set(re.findall(r"^(?:FAIL|ERROR): (\S+) \((\S+)\)", text, re.M))
        )
        rec["failed_tests"] = [cls for name, cls in failed]
        ran = re.search(r"^Ran (\d+) tests", text, re.M)
        rec["ran"] = int(ran.group(1)) if ran else None
        load_error = (
            "Failed to import test module" in text
            or re.search(
                r"^(ImportError|SyntaxError|ModuleNotFoundError)\b", text, re.M
            )
            or not ran
        )
        rec["verdict"] = (
            "INVALID (load error)"
            if load_error
            else "KILLED" if proc.returncode and rec["failed_tests"] else "SURVIVED"
        )

        # Restore from the commit's blob and verify.
        open(target, "w").write(pristine)
        rec["restored_sha256"] = sha(target)
        rec["restore_verified"] = rec["restored_sha256"] == pristine_sha
        rec["worktree_clean"] = (
            sh("git status --porcelain --untracked-files=no", cwd=wt).stdout == ""
        )
    finally:
        if rec.get("restore_verified") or "error" in rec:
            sh(f"git worktree remove --force {wt}", cwd=MAIN)
            rec["worktree_removed"] = not os.path.exists(wt)
        else:
            rec["worktree_removed"] = False
    return rec


def main():
    commit, k, outdir = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    os.makedirs(outdir, exist_ok=True)
    with cf.ThreadPoolExecutor(max_workers=k) as ex:
        results = list(ex.map(lambda m: run_mutant(commit, outdir, *m), MUTANTS))
    for r in results:
        r.pop("diff_short", None)
    json.dump(results, open(f"{outdir}/results.json", "w"), indent=2)
    for r in results:
        print(
            r["id"],
            r.get("verdict"),
            r.get("ran"),
            r.get("failed_tests"),
            "restore_ok=",
            r.get("restore_verified"),
            "removed=",
            r.get("worktree_removed"),
            r.get("error", ""),
        )


if __name__ == "__main__":
    main()
