# flake8: noqa: E501,B950
"""H-258 the dependency audit in CI (Hardening Engineer, 4d): deliberate breaks, and the tests that must catch each.

Each mutant: the file is edited (anchor found exactly once), __pycache__
removed (rule 17), AutoGrader.tests_pip_audit_gate run (SimpleTestCase only,
no database), output straight to its own log with stdin from the null device
(rule 18), the file restored from the saved source and its sha256 checked.

Judged (rules 18, 19, 22; H-229): KILLED = non-zero exit, the run's own "Ran"
line, no module that failed to load, the failing set EXACTLY the written one,
and every FAIL/ERROR block of each expected test holding that test's written
fragment. Anything else is reported as what it is (SURVIVED; DIFFERS; WRONG
REASON; BROKEN) and the battery ends non-zero. REASON was written before any
run, each traced against every assertion of the module (controls too).

`python -I mutate.py --check` only counts the anchors (and parses the Python
mutants).
"""

import ast
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys

SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree")
HERE = pathlib.Path("docs/evidence/h258-pip-audit-ci")
OUT = pathlib.Path(os.environ.get("MUT_RESULTS", HERE / "mutation_results.json"))
LOGS = pathlib.Path(os.environ.get("MUT_LOGS", HERE / "mutant_logs"))
PYTHON = os.environ.get("MUT_PYTHON", sys.executable)

RUNNER = "scripts/pip_audit_gate.py"
WORKFLOW = ".github/workflows/audit.yml"
IGNORE = ".pip-audit-ignore.toml"
TESTS = ["AutoGrader.tests_pip_audit_gate"]

PAST = "        if review_by < today:\n"
ANYIO = (
    'package = "anyio"  # 4.10.0\nids = [\n    "PYSEC-2026-4024",\n    "PYSEC-2026-4025",\n]\n'
    'reason = "upgrade awaiting the owner\'s ruling (H-258 report)"\n'
)

# id: (what is broken, file, old, new)
MUTANTS = {
    "R1": ("a passed review date accepted", RUNNER, PAST, "        if False:\n"),
    "R2": (
        "an entry refused ON its review date",
        RUNNER,
        PAST,
        "        if review_by <= today:\n",
    ),
    "R3": (
        "an entry without a reason accepted",
        RUNNER,
        "        if not isinstance(reason, str) or not reason.strip():\n",
        "        if False:\n",
    ),
    "R4": (
        "an id listed twice accepted",
        RUNNER,
        "            if advisory in seen:\n",
        "            if False:\n",
    ),
    "R5": (
        "the review date's type not checked",
        RUNNER,
        "        if not isinstance(review_by, datetime.date) or isinstance(\n",
        "        if False and isinstance(\n",
    ),
    "R6": (
        "a wrong pip-audit version accepted",
        RUNNER,
        "    if installed != PIP_AUDIT_VERSION:\n",
        "    if False:\n",
    ),
    "R7": ("the audit not strict", RUNNER, '        "--strict",\n', ""),
    "R8": (
        "the accepted ids not passed to pip-audit",
        RUNNER,
        '        command += ["--ignore-vuln", advisory]\n',
        "        pass\n",
    ),
    "R9": (
        "the weekly schedule dropped",
        WORKFLOW,
        '  schedule:\n    # Mondays 06:17 UTC.\n    - cron: "17 6 * * 1"\n',
        "",
    ),
    "R10": (
        "pip-audit unpinned in the workflow",
        WORKFLOW,
        'run: python -m pip install "pip-audit==2.9.0"\n',
        "run: python -m pip install pip-audit\n",
    ),
    "R11": (
        "pushes to beta not audited",
        WORKFLOW,
        "  push:\n    branches: [main, beta, dev]\n",
        "  push:\n    branches: [main, dev]\n",
    ),
    "R12": (
        "a checked-in entry without its reason",
        IGNORE,
        ANYIO,
        ANYIO.replace(
            'reason = "upgrade awaiting the owner\'s ruling (H-258 report)"\n', ""
        ),
    ),
}

PAST_T = "test_an_entry_past_its_review_date_is_refused"
ON_T = "test_an_entry_on_its_review_date_still_counts"
REASON_T = "test_an_entry_without_a_reason_is_refused"
DATE_T = "test_an_entry_without_a_review_date_is_refused"
TWICE_T = "test_an_id_listed_twice_is_refused"
CMD_T = "test_the_command_audits_the_pinned_requirements_strictly"
VER_T = "test_a_wrong_pip_audit_version_is_refused"
FILE_T = "test_the_checked_in_ignore_file_is_well_formed"
WF_T = "test_the_workflow_runs_the_runner_with_the_pinned_version"

NOT_RAISED = "AssertionError: IgnoreFileError not raised"

#: Written before any run: test -> the fragment each of its blocks must hold.
#: R5: with the type check gone, None or a str meets `review_by < today`.
#: R9: the trigger dict has no "schedule". R11: push's branches lose beta.
REASON = {
    "R1": {PAST_T: NOT_RAISED},
    "R2": {ON_T: "IgnoreFileError: gunicorn: the review date 2026-11-30 has passed"},
    "R3": {REASON_T: NOT_RAISED},
    "R4": {TWICE_T: NOT_RAISED},
    "R5": {DATE_T: "TypeError: '<' not supported between instances of"},
    "R6": {VER_T: NOT_RAISED},
    "R7": {CMD_T: "'--strict' not found in ["},
    "R8": {CMD_T: "[] != ['B-2', 'A-1']"},
    "R9": {WF_T: "KeyError: 'schedule'"},
    "R10": {WF_T: "'pip install \"pip-audit==2.9.0\"' not found in"},
    "R11": {WF_T: "['main', 'dev'] != ['main', 'beta', 'dev']"},
    "R12": {FILE_T: "IgnoreFileError: anyio: no reason"},
}

LOAD_FAILURE = "unittest.loader._FailedTest"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def clear_pycache():
    for d in ("scripts", "AutoGrader"):
        shutil.rmtree(pathlib.Path(d) / "__pycache__", ignore_errors=True)


def blocks_of(text, name):
    blocks = re.split(r"^={70}$", text, flags=re.M)
    return [
        b
        for b in blocks
        if re.match(r"\s*(FAIL|ERROR): " + re.escape(name) + r"[ (]", b)
    ]


def judge(mid, returncode, text):
    expected = REASON[mid]
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
    ran = re.findall(r"^Ran \d+ tests? in .*$", text, re.M)
    ran_line = ran[-1] if ran else None
    wrong = sorted(
        name
        for name, fragment in expected.items()
        if not blocks_of(text, name)
        or not all(fragment in b for b in blocks_of(text, name))
    )
    if returncode == 0:
        status = "SURVIVED"
    elif not ran_line or LOAD_FAILURE in text:
        status = "BROKEN"
    elif set(failed) != set(expected):
        status = "DIFFERS"
    elif wrong:
        status = "WRONG REASON"
    else:
        status = "KILLED"
    return status, ran_line, failed, wrong


def _exit_on_sigterm(signum, _frame):
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    assert list(MUTANTS) == list(REASON), "every mutant named"
    originals = {}
    for mid, (_what, path, old, new) in MUTANTS.items():
        source = originals.setdefault(path, open(path).read())
        found = source.count(old)
        assert found == 1, f"{mid}: anchor found {found} times"
        mutated = source.replace(old, new, 1)
        assert mutated != source, f"{mid}: changes nothing"
        if path.endswith(".py"):
            ast.parse(mutated)
    if "--check" in sys.argv:
        print(len(MUTANTS), "mutants: every anchor found once, the Python ones parse")
        print(" ".join(MUTANTS))
        return 0
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    results = {}
    for mid, (what, path, old, new) in MUTANTS.items():
        original = originals[path]
        log = LOGS / f"{mid}.txt"
        try:
            clear_pycache()
            open(path, "w").write(original.replace(old, new, 1))
            with open(log, "w") as out, open(os.devnull) as devnull:
                p = subprocess.run(
                    [PYTHON, "manage.py", "test", *TESTS]
                    + [f"--settings={SETTINGS}", "--noinput", "--verbosity", "2"],
                    stdin=devnull,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=300,
                )
        finally:
            open(path, "w").write(original)
            clear_pycache()
        assert sha(open(path).read()) == sha(original), f"{mid}: {path} not restored"
        text = log.read_text(errors="replace")
        status, ran_line, failed, wrong = judge(mid, p.returncode, text)
        results[mid] = {
            "what": what,
            "file": path,
            "status": status,
            "exit": p.returncode,
            "ran_line": ran_line,
            "expected": REASON[mid],
            "failing_tests": failed,
            "wrong_reason": wrong,
            "also_failed_not_expected": sorted(set(failed) - set(REASON[mid])),
            "missing": sorted(set(REASON[mid]) - set(failed)),
        }
        print(mid, results[mid], flush=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    for status in ("KILLED", "SURVIVED", "DIFFERS", "WRONG REASON", "BROKEN"):
        print(f"{status}:", [k for k, v in results.items() if v["status"] == status])
    return 0 if all(v["status"] == "KILLED" for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
