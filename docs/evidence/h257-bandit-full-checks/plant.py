# flake8: noqa: E501,B950
"""H-257 Bandit on every check (Hardening Engineer, 4d): planted findings the hook must catch.

Each plant edits one file (anchor found exactly once) or adds a scratch file,
runs `pre-commit run bandit --files <file>` with output straight to its own
log and stdin from the null device (rule 18), and restores the file from the
saved source with its sha256 checked (the scratch file is deleted).

Judged: CAUGHT = the hook exits 1, its output reads "Failed", and the issues it
lists are EXACTLY the written ones (Bandit test id and file, with a count).
Anything else is reported as what it is (MISSED; DIFFERS with the issues
named). EXPECTED was written before any run.

`python -I plant.py --check` only counts the anchors.
"""

import collections
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path("docs/evidence/h257-bandit-full-checks")
OUT = pathlib.Path(os.environ.get("PLANT_RESULTS", HERE / "plant_results.json"))
LOGS = pathlib.Path(os.environ.get("PLANT_LOGS", HERE / "plant_logs"))
PRE_COMMIT = os.environ.get("PRE_COMMIT", "pre-commit")

SCRATCH = "h257_planted_scratch.py"
EXCLUDE = (
    '        exclude: "tests/|examples/|docs/|setup.py|(^|/)tests?_[^/]*\\\\.py$"\n'
)

# id: (what is planted, file edited, old, new, file the hook is run on)
PLANTS = {
    "P1": (
        "an md5 without usedforsecurity",
        "users/mixins.py",
        "                query_params.encode(), usedforsecurity=False\n",
        "                query_params.encode()\n",
        "users/mixins.py",
    ),
    "P2": (
        "a requests.post without a timeout",
        "users/views.py",
        "                    token_url, data=payload, timeout=GOOGLE_TOKEN_EXCHANGE_TIMEOUT\n",
        "                    token_url, data=payload\n",
        "users/views.py",
    ),
    "P3": (
        "a nosec line with its marker removed",
        "AutoGrader/log_scrubbing.py",
        'SECRET = "[secret]"  # nosec B105  # the scrubber\'s replacement text, not a password\n',
        'SECRET = "[secret]"\n',
        "AutoGrader/log_scrubbing.py",
    ),
    "P4": (
        "the test-module pattern stops matching (the exclude is read)",
        ".pre-commit-config.yaml",
        EXCLUDE,
        '        exclude: "tests/|examples/|docs/|setup.py"\n',
        "classrooms/test_views.py",
    ),
    "P5": (
        "a hardcoded password in a scratch file",
        SCRATCH,
        None,
        '"""H-257 planted finding; deleted after the run."""\n\npassword = "hunter2-planted"\n',
        SCRATCH,
    ),
    "P6": (
        "the B404 skip removed (the skip is read)",
        "pyproject.toml",
        "skips = ['B404']\n",
        "",
        "scripts/credscan.py",
    ),
    "P7": (
        "two ids joined by a comma (Bandit keeps the last)",
        "scripts/check_migration_safety.py",
        "# nosec B603 B607  # fixed git argv\n",
        "# nosec B603,B607  # fixed git argv\n",
        "scripts/check_migration_safety.py",
    ),
}

#: Written before any run: the issues the hook must list, (test id, file): count.
EXPECTED = {
    "P1": {("B324", "users/mixins.py"): 1},
    "P2": {("B113", "users/views.py"): 1},
    "P3": {("B105", "AutoGrader/log_scrubbing.py"): 1},
    "P4": {("B106", "classrooms/test_views.py"): 11},
    "P5": {("B105", SCRATCH): 1},
    "P6": {("B404", "scripts/credscan.py"): 1},
    "P7": {("B603", "scripts/check_migration_safety.py"): 1},
}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def issues(text):
    found = collections.Counter()
    for test_id, path in re.findall(
        r">> Issue: \[(B\d+):[^\]]*\].*?\n.*?Location: \./([^:]+):", text, re.S
    ):
        found[(test_id, path)] += 1
    return found


def main():
    assert list(PLANTS) == list(EXPECTED)
    originals = {}
    for pid, (_what, path, old, new, _target) in PLANTS.items():
        if old is None:
            assert not os.path.exists(path), f"{pid}: {path} exists"
            continue
        source = originals.setdefault(path, open(path).read())
        found = source.count(old)
        assert found == 1, f"{pid}: anchor found {found} times"
    if "--check" in sys.argv:
        print(len(PLANTS), "plants: every anchor found once")
        return 0
    LOGS.mkdir(parents=True, exist_ok=True)
    results = {}
    for pid, (what, path, old, new, target) in PLANTS.items():
        log = LOGS / f"{pid}.txt"
        try:
            if old is None:
                open(path, "w").write(new)
            else:
                open(path, "w").write(originals[path].replace(old, new, 1))
            with open(log, "w") as out, open(os.devnull) as devnull:
                p = subprocess.run(
                    [PRE_COMMIT, "run", "bandit", "--files", target],
                    stdin=devnull,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                )
        finally:
            if old is None:
                os.remove(path)
            else:
                open(path, "w").write(originals[path])
        if old is None:
            assert not os.path.exists(path), f"{pid}: scratch file left"
        else:
            assert sha(open(path).read()) == sha(
                originals[path]
            ), f"{pid}: {path} not restored"
        text = log.read_text(errors="replace")
        got = issues(text)
        want = collections.Counter(EXPECTED[pid])
        if p.returncode == 0:
            status = "MISSED"
        elif "Failed" in text and got == want:
            status = "CAUGHT"
        else:
            status = "DIFFERS"
        results[pid] = {
            "what": what,
            "file": path,
            "hook_run_on": target,
            "status": status,
            "exit": p.returncode,
            "expected": {f"{k[0]} {k[1]}": v for k, v in want.items()},
            "found": {f"{k[0]} {k[1]}": v for k, v in got.items()},
        }
        print(pid, results[pid], flush=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    for status in ("CAUGHT", "MISSED", "DIFFERS"):
        print(f"{status}:", [k for k, v in results.items() if v["status"] == status])
    return 0 if all(v["status"] == "CAUGHT" for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
