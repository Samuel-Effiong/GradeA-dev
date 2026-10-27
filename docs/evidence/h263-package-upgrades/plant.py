# flake8: noqa: E501
"""H-263 the six upgrades (Hardening Engineer, 4d): planted pins the audit must catch.

Each plant puts ONE package back to its old pin in requirements.txt, leaving its
advisories out of .pip-audit-ignore.toml (where the upgrade removed them), runs
the gate runner with output straight to its own log and stdin from the null
device (rule 18), and restores requirements.txt from the saved source with its
sha256 checked.

Judged: CAUGHT = the runner exits 1 and the advisory ids pip-audit names are
EXACTLY the written ones for that package. Anything else is reported as what it
is (MISSED; DIFFERS with the ids named). EXPECTED was written before any run:
each list is the ids that package's commit took out of the ignore file, read
from `git show <base>:.pip-audit-ignore.toml`, not from a run.

`python -I plant.py --check` only reads the expected sets and the anchors.

    PIP_AUDIT_PY=<the pip-audit venv's python> python -I plant.py
"""

import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path("docs/evidence/h263-package-upgrades")
OUT = pathlib.Path(os.environ.get("PLANT_RESULTS", HERE / "plant_results.json"))
LOGS = pathlib.Path(os.environ.get("PLANT_LOGS", HERE / "plant_logs"))
PIP_AUDIT_PY = os.environ.get("PIP_AUDIT_PY", sys.executable)
BASE = os.environ.get("PLANT_BASE", "9263e9f8")
REQ = "requirements.txt"

#: package: (its line in requirements.txt, the old pin, the new pin)
PLANTS = {
    "A1": ("gunicorn", "20.0.4", "22.0.0"),
    "A2": ("pillow", "12.0.0", "13.0.0"),
    "A3": ("PyJWT", "2.10.1", "2.15.0"),
    "A4": ("lxml", "6.0.2", "6.1.0"),
    "A5": ("urllib3", "2.5.0", "2.8.0"),
    "A6": ("djangorestframework", "3.16.1", "3.18.2"),
}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def base_ignore_ids():
    """{package: [ids]} for the six, read from the ignore file as at the base."""
    text = subprocess.run(
        ["git", "show", f"{BASE}:.pip-audit-ignore.toml"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = {}
    for package, _old, _new in PLANTS.values():
        name = package.lower()
        blocks = re.findall(
            r'\[\[ignore\]\]\npackage = "'
            + re.escape(name)
            + r'"  #.*?\nreview_by = [0-9-]+\n',
            text,
            re.S,
        )
        assert len(blocks) == 1, f"{name}: {len(blocks)} ignore blocks at {BASE}"
        ids = re.findall(r'^\s+"([A-Z]+-[0-9A-Za-z-]+)",$', blocks[0], re.M)
        assert ids, f"{name}: no ids at {BASE}"
        found[name] = sorted(ids)
    return found


#: Written before any run: the ids pip-audit must name, by package.
EXPECTED = base_ignore_ids()


def reported_ids(text):
    """The advisory ids pip-audit's table names, whatever the column widths."""
    return sorted(set(re.findall(r"\b((?:PYSEC|GHSA|OSV)-[0-9A-Za-z-]+)\b", text)))


def main():
    source = open(REQ).read()
    for pid, (package, old, new) in PLANTS.items():
        assert (
            source.count(f"\n{package}=={new}\n") == 1
        ), f"{pid}: {package}=={new} not found once"
        assert (
            source.count(f"\n{package}=={old}\n") == 0
        ), f"{pid}: {package}=={old} still pinned"
    if "--check" in sys.argv:
        for name, ids in EXPECTED.items():
            print(f"{name}: {len(ids)} ids expected")
        print(len(PLANTS), "plants: every anchor found once")
        return 0
    LOGS.mkdir(parents=True, exist_ok=True)
    results = {}
    for pid, (package, old, new) in PLANTS.items():
        log = LOGS / f"{pid}.txt"
        try:
            open(REQ, "w").write(
                source.replace(f"\n{package}=={new}\n", f"\n{package}=={old}\n", 1)
            )
            with open(log, "w") as out, open(os.devnull) as devnull:
                p = subprocess.run(
                    [PIP_AUDIT_PY, "scripts/pip_audit_gate.py"],
                    stdin=devnull,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    timeout=1800,
                )
        finally:
            open(REQ, "w").write(source)
        assert sha(open(REQ).read()) == sha(source), f"{pid}: {REQ} not restored"
        text = log.read_text(errors="replace")
        got = reported_ids(text)
        want = EXPECTED[package.lower()]
        if p.returncode == 0:
            status = "MISSED"
        elif got == want:
            status = "CAUGHT"
        else:
            status = "DIFFERS"
        results[pid] = {
            "package": package,
            "put_back_to": old,
            "status": status,
            "exit": p.returncode,
            "expected_ids": len(want),
            "found_ids": len(got),
            "missing": [i for i in want if i not in got],
            "extra": [i for i in got if i not in want],
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
