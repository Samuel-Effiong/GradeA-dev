"""
Mutation battery for H-65, one run at a time per Beat task (rule 15).

One mutant per guard, applied in ONE disposable detached worktree at the
commit under test; the lock module and the catch-up module run against
it, and the file is restored from the commit's blob and sha256-checked
before the next. BROKEN (a load failure) is never counted as a kill.

    python docs/evidence/h65-beat-locks/run_mutants.py <commit>
"""

import argparse
import hashlib
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MAIN = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus")
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h65-mut")
TEST_DB = "test_h65_mut"

BL = "AutoGrader/beat_locks.py"
TESTS = [
    "AutoGrader.tests_beat_locks",
    "billing.tests.test_beat_lock_catch_up",
    # 0b: the adapted overlap helper (a lapsed lock) must still prove the
    # per-row re-checks; M1/M2 remove them.
    "billing.tests.test_overlapping_run_rechecks",
]

MUTANTS = [
    (
        "M1",
        "mid-cycle grant: the next_credit_grant_at re-check (lapsed lock)",
        "billing/services.py",
        "            or user_subscription.next_credit_grant_at > refresh_due_by(now)\n",
        "",
        1,
    ),
    (
        "M2",
        "expire_trial: the active-trial re-check (lapsed lock)",
        "billing/services.py",
        "        if not (locked.is_trial and locked.is_active):\n",
        "        if False:\n",
        1,
    ),
    (
        "L1",
        "the lock is released after the run (0b)",
        BL,
        "                    released = lock.release()\n",
        "                    released = True\n",
        1,
    ),
    (
        "L2",
        "release is compare-and-delete",
        BL,
        "    \"if redis.call('get', KEYS[1]) == ARGV[1] then \"\n"
        "    \"return redis.call('del', KEYS[1]) end return 0\"\n",
        "    \"return redis.call('del', KEYS[1])\"\n",
        1,
    ),
    (
        "L3",
        "the heartbeat's extend is compare-and-extend",
        BL,
        "    \"if redis.call('get', KEYS[1]) == ARGV[1] then \"\n"
        "    \"return redis.call('pexpire', KEYS[1], ARGV[2]) end return 0\"\n",
        "    \"return redis.call('pexpire', KEYS[1], ARGV[2])\"\n",
        1,
    ),
    (
        "L4",
        "a cache error fails CLOSED",
        BL,
        '                return f"{name}: {SKIPPED_CACHE_ERROR}."\n',
        "                return fn(*args, **kwargs)\n",
        1,
    ),
    (
        "L5",
        "a held lock means skip",
        BL,
        "            if not acquired:\n",
        "            if False:\n",
        1,
    ),
    ("L6", "acquire is SET NX", BL, "nx=True, ", "", 1),
    (
        "L7",
        "the heartbeat stops at max_hold",
        BL,
        "            if time.monotonic() - started >= self.lock.max_hold_seconds:\n",
        "            if False:\n",
        1,
    ),
    (
        "L8",
        "the heartbeat runs",
        BL,
        "            heartbeat.start()\n",
        "            pass\n",
        1,
    ),
    (
        "L9",
        "the TTL never exceeds max_hold",
        BL,
        "    ttl_seconds = min(_seconds(ttl), max_hold_seconds)\n",
        "    ttl_seconds = _seconds(ttl)\n",
        1,
    ),
    (
        "L10",
        "an exemption is a scheduled, named choice",
        BL,
        '    "dashboard.tasks.record_concurrent_users": "one sample row per tick",\n',
        "",
        1,
    ),
    (
        "L11",
        "every-5-minute max_hold is below its interval",
        BL,
        "EVERY_5_MIN = timedelta(minutes=4)\n",
        "EVERY_5_MIN = timedelta(minutes=6)\n",
        1,
    ),
    (
        "L12",
        "the watchdog reports the lock store",
        "AutoGrader/beat_health.py",
        "        overdue.append(locks)\n",
        "        pass\n",
        1,
    ),
    (
        "L13",
        "the runner clears locks before every test",
        "AutoGrader/testing/beat_locks.py",
        "            clear_beat_locks()\n",
        "            pass\n",
        1,
    ),
    (
        "L14",
        "a guarded task keeps its lock",
        "billing/tasks.py",
        "@single_instance(max_hold=beat_locks.DAILY)\n"
        "def cleanup_expired_credit_buckets(",
        "def cleanup_expired_credit_buckets(",
        1,
    ),
]

LOAD_FAILURE = ("unittest.loader._FailedTest", "ImportError", "SyntaxError")


def sh(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace_nth(text, old, new, nth):
    start = -1
    for _ in range(nth):
        start = text.find(old, start + 1)
        if start < 0:
            raise SystemExit(f"anchor not found (occurrence {nth}): {old[:60]!r}")
    return text[:start] + new + text[start + len(old) :]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("commit")
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    commit = sh("git", "rev-parse", args.commit, cwd=REPO).stdout.strip()
    only = set(filter(None, args.only.split(",")))
    selected = [m for m in MUTANTS if not only or m[0] in only]

    sh("git", "worktree", "add", "--detach", WORKTREE, commit, cwd=REPO)
    try:
        os.symlink(os.path.join(MAIN, ".env"), os.path.join(WORKTREE, ".env"))
        with open(os.path.join(WORKTREE, "settings_worktree.py"), "w") as fh:
            fh.write(
                "from AutoGrader.settings import *  # noqa: F401,F403\n"
                "from AutoGrader.settings import DATABASES\n\n"
                'DATABASES["default"].setdefault("TEST", {})\n'
                f'DATABASES["default"]["TEST"]["NAME"] = {TEST_DB!r}\n'
            )
        logs = os.path.join(HERE, "logs")
        os.makedirs(logs, exist_ok=True)
        rows = []
        for mid, guard, rel, old, new, nth in selected:
            path = os.path.join(WORKTREE, rel)
            pristine = sh("git", "show", f"{commit}:{rel}", cwd=REPO).stdout.encode()
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(replace_nth(text, old, new, nth))
            started = time.monotonic()
            proc = subprocess.run(
                [
                    sys.executable,
                    "manage.py",
                    "test",
                    *TESTS,
                    "--settings=settings_worktree",
                    "--noinput",
                    "--keepdb",
                ],
                cwd=WORKTREE,
                capture_output=True,
                text=True,
                env={**os.environ, "EXEMPT_EMAIL_DOMAINS": ""},
            )
            elapsed = time.monotonic() - started
            sh("git", "checkout", "--", rel, cwd=WORKTREE)
            with open(path, "rb") as fh:
                restored = sha256(fh.read()) == sha256(pristine)
            output = proc.stdout + proc.stderr
            summary = next(
                (
                    ln
                    for ln in reversed(output.splitlines())
                    if ln.startswith(("FAILED", "OK"))
                ),
                "NO SUMMARY",
            )
            loaded = not any(marker in output for marker in LOAD_FAILURE)
            if proc.returncode == 0:
                status = "SURVIVED"
            elif "Ran " in output and loaded:
                status = "KILLED"
            else:
                status = "BROKEN"
            failing = [
                ln for ln in output.splitlines() if ln.startswith(("FAIL:", "ERROR:"))
            ]
            with open(os.path.join(logs, f"{mid}.log"), "w") as fh:
                fh.write(
                    f"# {mid}: {guard}\n# file: {rel} (occurrence {nth})\n"
                    f"# old: {old!r}\n# new: {new!r}\n# commit: {commit}\n"
                    f"# exit: {proc.returncode}\n# elapsed_s: {elapsed:.1f}\n"
                    f"# restored_sha256_matches_commit_blob: {restored}\n\n"
                )
                fh.write("\n".join(failing) + f"\n\n{summary}\n")
            row = (mid, guard, status, summary, str(restored), f"{elapsed:.1f}")
            rows.append(row)
            print("\t".join(row), flush=True)
            if not restored:
                raise SystemExit(f"{mid}: restore did not match the commit blob")
        with open(os.path.join(HERE, "results.tsv"), "a") as fh:
            for row in rows:
                fh.write("\t".join([commit[:12], *row]) + "\n")
        killed = sum(1 for r in rows if r[2] == "KILLED")
        print(f"\n{killed}/{len(rows)} killed with verified restore")
        return 0 if killed == len(rows) else 1
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", WORKTREE],
            cwd=REPO,
            capture_output=True,
        )
        subprocess.run(["git", "worktree", "prune"], cwd=REPO, capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
