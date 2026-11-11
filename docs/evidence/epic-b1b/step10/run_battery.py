"""B1b-10 battery runner (NOTES §10.1). From the worktree root:

    run_battery.py check MANIFEST            every edit's old text occurs exactly once
    run_battery.py run MANIFEST OUTDIR [--from NAME]

Each plant: apply its edits; run its killer label alone, serial, under flock,
with BILLING_HOLDS_ENABLED, BILLING_HOLDS_DRAINING, RUN_LOAD_TESTS,
CI_REQUIRE_REDIS and CI_REQUIRE_NETWORK unset; restore each file with git
checkout and check its sha256 against HEAD. The run stops at the first
survivor, mismatch or restore failure (SM ruling Q10-2: report before going on).
"""

import hashlib
import json
import os
import re
import subprocess
import sys

PY = os.path.expanduser("~/Documents/Virtualenvs/h284_deps_env/bin/python")
LOCK = os.path.expanduser("~/.machine-fullsuite.lock")
UNSET = (
    "BILLING_HOLDS_ENABLED",
    "BILLING_HOLDS_DRAINING",
    "RUN_LOAD_TESTS",
    "CI_REQUIRE_REDIS",
    "CI_REQUIRE_NETWORK",
)


def edits(plant):
    if "edits" in plant:
        return plant["edits"]
    return [{"file": plant["file"], "old": plant["old"], "new": plant["new"]}]


def head_sha256(path):
    blob = subprocess.run(
        ["git", "show", f"HEAD:{path}"], capture_output=True, check=True
    ).stdout
    return hashlib.sha256(blob).hexdigest()


def check(manifest):
    bad = 0
    for plant in manifest:
        if plant.get("status") == "retired":
            print(f"{plant['name']:12s} RETIRED")
            continue
        counts = []
        texts = {}
        for e in edits(plant):
            texts.setdefault(e["file"], open(e["file"]).read())
            counts.append(texts[e["file"]].count(e["old"]))
            texts[e["file"]] = texts[e["file"]].replace(e["old"], e["new"], 1)
        ok = all(c == 1 for c in counts)
        bad += not ok
        print(f"{plant['name']:12s} {'ok ' if ok else 'BAD'} counts={counts}")
    return bad


def run_one(plant, outdir, sha):
    files = sorted({e["file"] for e in edits(plant)})
    for e in edits(plant):
        text = open(e["file"]).read()
        assert text.count(e["old"]) == 1, (plant["name"], e["file"], "anchor")
        open(e["file"], "w").write(text.replace(e["old"], e["new"], 1))
    diff = subprocess.run(
        ["git", "diff", "--", *files], capture_output=True, text=True
    ).stdout
    env = {k: v for k, v in os.environ.items() if k not in UNSET}
    env.update(plant.get("env") or {})
    cmd = ["flock", LOCK, PY, "manage.py", "test", "--noinput", *plant["label"].split()]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    subprocess.run(["git", "checkout", "-q", "--", *files], check=True)
    restored = all(
        hashlib.sha256(open(f, "rb").read()).hexdigest() == head_sha256(f)
        for f in files
    )
    out = proc.stdout + proc.stderr
    log = os.path.join(outdir, f"{plant['name']}_{sha}.log")
    with open(log, "w") as fh:
        fh.write(
            f"B1b-10 battery {plant['name']} (NOTES §10.1): ENV {', '.join(UNSET)} unset; serial; at {sha}\n"
        )
        fh.write(
            f"label: {plant['label']}\nenv set: {plant.get('env') or 'none'}\ndeclared: {plant['expected']}\n"
        )
        fh.write(diff)
        fh.write(out)
        fh.write(
            f"\nrestored: {'sha256 MATCHES HEAD' if restored else 'sha256 MISMATCH'} for {', '.join(files)}\n"
        )
    ran = re.findall(r"^Ran (\d+) tests?", out, re.M)
    summary = re.findall(r"^(OK.*|FAILED \(.*\))$", out, re.M)
    failed = re.findall(r"^(?:FAIL|ERROR): \w+ \(([\w.]+)\)", out, re.M)
    frags = plant.get("fragments") or (
        [plant["fragment"]] if plant.get("fragment") else []
    )
    if isinstance(frags, str):
        frags = [frags]
    missing = [f for f in frags if f and f not in out]
    got = f"Ran {ran[-1] if ran else '?'}, {summary[-1] if summary else '?'}"
    declared = plant["expected"]
    if declared.startswith("Ran") and "OK" in declared:
        as_declared = got.startswith(declared.split(" (declared")[0])
    else:
        as_declared = got == declared and not missing
    return {
        "name": plant["name"],
        "group": plant.get("group"),
        "declared": declared,
        "got": got,
        "failed": failed,
        "fragments_missing": missing,
        "restored": restored,
        "as_declared": as_declared,
        "rc": proc.returncode,
        "log": log,
    }


def main():
    cmd, manifest_path = sys.argv[1], sys.argv[2]
    manifest = json.load(open(manifest_path))
    if cmd == "check":
        sys.exit(1 if check(manifest) else 0)
    outdir = sys.argv[3]
    start = sys.argv[sys.argv.index("--from") + 1] if "--from" in sys.argv else None
    sha = subprocess.run(
        ["git", "log", "-1", "--format=%h"], capture_output=True, text=True
    ).stdout.strip()
    summary_path = os.path.join(outdir, f"battery_summary_{sha}.jsonl")
    running = start is None
    for plant in manifest:
        if not running:
            running = plant["name"] == start
            if not running:
                continue
        if plant.get("status") == "retired":
            continue
        result = run_one(plant, outdir, sha)
        with open(summary_path, "a") as fh:
            fh.write(json.dumps(result) + "\n")
        flag = "ok" if result["as_declared"] and result["restored"] else "STOP"
        print(
            f"{flag:4s} {result['name']:12s} declared [{result['declared']}] "
            f"got [{result['got']}] missing={result['fragments_missing']}",
            flush=True,
        )
        if flag == "STOP":
            sys.exit(3)


main()
