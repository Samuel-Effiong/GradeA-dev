"""
Parallel, disposable mutation battery for the free-plan activation fix.

    python docs/evidence/free_plan_activation/mutation/run_battery.py \
        <commit> <workers> <out_dir>

For each worker: a detached worktree at <commit> (never a working tree with
uncommitted work), its own .env symlink and uniquely named test database, and
a clean baseline run that must pass before any mutant counts. Mutants are
applied by exact text replacement (asserted to match once), the covering
tests run, and the file is restored from `git show <commit>:<path>` and
verified by sha256 against the committed blob. Workers are removed at the end.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from mutants import MUTANTS  # noqa: E402

TEST_LABELS = [
    "billing.tests.test_free_plan_activation_security",
    "billing.tests.test_endpoint_permissions",
    "users.tests_signals_and_edges",
]

commit, workers, out_dir = sys.argv[1], int(sys.argv[2]), sys.argv[3]
root = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
).stdout.strip()
main_checkout = os.path.join(os.path.dirname(root), "Grade-Automator-Plus")
os.makedirs(out_dir, exist_ok=True)


def sh(args, cwd, **kw):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, **kw)


def blob(path):
    return sh(["git", "show", f"{commit}:{path}"], root, check=True).stdout


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def run_tests(wt, log_path):
    start = time.time()
    with open(log_path, "w") as log:
        proc = subprocess.run(
            [
                "python",
                "manage.py",
                "test",
                *TEST_LABELS,
                "--settings=settings_worktree",
                "--noinput",
                "--keepdb",
            ],
            cwd=wt,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    failed = []
    with open(log_path) as log:
        for line in log:
            if line.startswith(("FAIL: ", "ERROR: ")):
                failed.append(line.strip())
    return proc.returncode, round(time.time() - start, 1), failed


def make_worker(i):
    wt = os.path.join(os.path.dirname(root), f"Grade-Automator-Plus-fpmut-{i}")
    sh(["git", "worktree", "add", "--detach", wt, commit], root, check=True)
    os.symlink(os.path.join(main_checkout, ".env"), os.path.join(wt, ".env"))
    with open(os.path.join(wt, "settings_worktree.py"), "w") as f:
        f.write(
            "from AutoGrader.settings import *  # noqa\n"
            "from AutoGrader.settings import DATABASES\n\n"
            'DATABASES["default"].setdefault("TEST", {})\n'
            f'DATABASES["default"]["TEST"]["NAME"] = "test_fpmut_{i}"\n'
        )
    return wt


results = {}
lock = threading.Lock()


def worker(i, queue):
    wt = make_worker(i)
    code, secs, failed = run_tests(wt, os.path.join(out_dir, f"baseline-w{i}.log"))
    with lock:
        results[f"baseline-w{i}"] = {"exit": code, "seconds": secs, "failed": failed}
    if code != 0:
        print(f"worker {i}: BASELINE FAILED, skipping its mutants", flush=True)
        return wt
    for mid, guard, path, old, new in queue:
        original = blob(path)
        assert original.count(old) == 1, f"{mid}: expected one match in {path}"
        target = os.path.join(wt, path)
        with open(target, "w") as f:
            f.write(original.replace(old, new, 1))
        code, secs, failed = run_tests(wt, os.path.join(out_dir, f"{mid}.log"))
        restored = sh(["git", "show", f"{commit}:{path}"], wt, check=True).stdout
        with open(target, "w") as f:
            f.write(restored)
        with open(target) as f:
            restored_ok = sha256(f.read()) == sha256(original)
        clean = sh(["git", "status", "--porcelain", "--", path], wt).stdout == ""
        entry = {
            "guard": guard,
            "file": path,
            "worker": i,
            "exit": code,
            "killed": code != 0,
            "seconds": secs,
            "failing_tests": failed,
            "restored_sha256_matches_commit": restored_ok,
            "git_status_clean_after_restore": clean,
        }
        with lock:
            results[mid] = entry
        print(
            f"{mid} {'KILLED' if code else 'SURVIVED'} "
            f"({len(failed)} failing, {secs}s, restore ok={restored_ok and clean})",
            flush=True,
        )
    return wt


queues = [MUTANTS[i::workers] for i in range(workers)]
threads, wts = [], [None] * workers


def run(i):
    wts[i] = worker(i, queues[i])


for i in range(workers):
    t = threading.Thread(target=run, args=(i,))
    t.start()
    threads.append(t)
for t in threads:
    t.join()

summary = {
    "commit": commit,
    "workers": workers,
    "test_labels": TEST_LABELS,
    "mutants": len(MUTANTS),
    "killed": sum(1 for k, v in results.items() if k.startswith("M") and v["killed"]),
    "survived": sorted(
        k for k, v in results.items() if k.startswith("M") and not v["killed"]
    ),
    "restores_verified": all(
        v["restored_sha256_matches_commit"] and v["git_status_clean_after_restore"]
        for k, v in results.items()
        if k.startswith("M")
    ),
    "results": dict(sorted(results.items())),
}
with open(os.path.join(out_dir, "battery_results.json"), "w") as f:
    json.dump(summary, f, indent=2)

for wt in wts:
    if wt:
        sh(["git", "worktree", "remove", "--force", wt], root)
sh(["git", "worktree", "prune"], root)
print(
    json.dumps(
        {k: summary[k] for k in ("mutants", "killed", "survived", "restores_verified")}
    )
)
