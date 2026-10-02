"""
Mutation battery for H-89 (rule 15): the log record factory that scrubs
email addresses and URL credentials from every record's message and
exception text, its switch in settings, and (Y mutants) the Sentry hooks.

One disposable detached worktree at the commit under test. A baseline run
on the unmutated tree must pass first; then each mutant, with the file
restored from the commit's blob and sha256-checked after it. BROKEN (a
load failure) is never counted as a kill.

Rule 17: every run has PYTHONDONTWRITEBYTECODE=1, and the __pycache__
directories of the mutated modules' packages are deleted before the
baseline, before each mutant and after each restore, so a stale .pyc
(same size, same mtime second) can never stand in for the mutant.

    python docs/evidence/h89-log-address-scrubber/run_mutants.py <commit>
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MAIN = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus")
WORKTREE = os.path.join(os.path.dirname(REPO), "Grade-Automator-Plus-h89-mut")
TEST_DB = "test_h89_mut"

LS = "AutoGrader/log_scrubbing.py"
SY = "AutoGrader/sentry_scrubbing.py"
ST = "AutoGrader/settings.py"
TESTS = [
    "AutoGrader.tests_log_scrubbing",
    "billing.tests.test_log_scrubbing_end_to_end",
    "AutoGrader.tests_sentry_scrubbing",
]

MUTANTS = [
    (
        "S1",
        "an address is replaced",
        LS,
        "    return _ADDRESS.sub(EMAIL, _USERINFO.sub(CREDENTIALS, text))\n",
        "    return _USERINFO.sub(CREDENTIALS, text)\n",
        1,
    ),
    (
        "S2",
        "a URL's credentials are replaced, dotless hosts too",
        LS,
        "    return _ADDRESS.sub(EMAIL, _USERINFO.sub(CREDENTIALS, text))\n",
        "    return _ADDRESS.sub(EMAIL, text)\n",
        1,
    ),
    (
        "S3",
        "the exception text is rendered and scrubbed when the record is made",
        LS,
        "                record.exc_text = _scrubbed_exception_text(record.exc_info)\n",
        "                pass\n",
        1,
    ),
    (
        "S4",
        "a message that cannot be scrubbed fails closed (SM's D4)",
        LS,
        "            return _withheld_message(self)\n",
        "            return str(self.msg) + str(self.args)\n",
        1,
    ),
    (
        "S5",
        "the wrapped factory makes the record",
        LS,
        "        record = previous(*args, **kwargs)\n",
        "        record = logging.LogRecord(*args, **kwargs)\n",
        1,
    ),
    (
        "S6",
        "a second install does not wrap again",
        LS,
        "    if getattr(previous, _MARKER, False):\n        return\n",
        "",
        1,
    ),
    (
        "S7",
        "a second install still sets the switch",
        LS,
        "    set_enabled(enabled)\n    previous = logging.getLogRecordFactory()\n",
        "    previous = logging.getLogRecordFactory()\n",
        1,
    ),
    (
        "S8",
        "a record whose class cannot be replaced fails closed",
        LS,
        "                record.msg, record.args = _withheld_message(record), None\n",
        "                pass\n",
        1,
    ),
    (
        "S9",
        "exception text that cannot be rendered fails closed",
        LS,
        '        return f"{name}: {EXCEPTION_WITHHELD}"\n',
        "        return str(exc_info[1])\n",
        1,
    ),
    (
        "S10",
        "override_settings switches the scrubber",
        LS,
        "setting_changed.connect(_follow_the_setting)\n",
        "",
        1,
    ),
    (
        "S11",
        "the scrubber is off while tests run",
        ST,
        "LOG_SCRUB_ADDRESSES = not _TESTS_ARE_RUNNING\n",
        "LOG_SCRUB_ADDRESSES = True\n",
        1,
    ),
    (
        "S12",
        "settings install the factory",
        ST,
        "_install_log_scrubbing(LOG_SCRUB_ADDRESSES)\n",
        "",
        1,
    ),
    (
        "S13",
        "settings pass the switch to the factory",
        ST,
        "_install_log_scrubbing(LOG_SCRUB_ADDRESSES)\n",
        "_install_log_scrubbing(False)\n",
        1,
    ),
    (
        "S14",
        "a switched-off scrubber leaves the text alone",
        LS,
        "        if not _enabled:\n            return super().getMessage()  # type: ignore[misc]\n",
        "",
        1,
    ),
    (
        "S15",
        "text that only looks like an address is left alone",
        LS,
        '\\.[A-Za-z]{2,}"',
        '"',
        1,
    ),
    (
        "Y1",
        "Sentry: the exception values are scrubbed",
        SY,
        '    "exception",\n',
        "",
        1,
    ),
    (
        "Y2",
        "Sentry: a part that cannot be scrubbed fails closed",
        SY,
        "            container[part] = WITHHELD\n",
        "            pass\n",
        1,
    ),
    (
        "Y3",
        "Sentry: the user context is left alone",
        SY,
        '    "extra",\n',
        '    "extra",\n    "user",\n',
        1,
    ),
    (
        "Y4",
        "Sentry: an argument that is not text yet is scrubbed",
        SY,
        "    return scrub(str(value))\n",
        "    return value\n",
        1,
    ),
    (
        "Y5",
        "Sentry: a breadcrumb's data is scrubbed",
        SY,
        '    return _scrub_parts(crumb, ("message", "data"))\n',
        '    return _scrub_parts(crumb, ("message",))\n',
        1,
    ),
    (
        "Y6",
        "Sentry: a log item's attributes are scrubbed",
        SY,
        '    return _scrub_parts(log, ("body", "attributes"))\n',
        '    return _scrub_parts(log, ("body",))\n',
        1,
    ),
    (
        "Y7",
        "Sentry: settings pass before_send",
        ST,
        "            before_send=scrub_event,\n",
        "",
        1,
    ),
    (
        "Y8",
        "Sentry: settings pass before_send_log",
        ST,
        "            before_send_log=scrub_log,\n",
        "",
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


def clear_pycache(worktree):
    """Rule 17: drop the compiled copies of every mutated module."""
    for rel in sorted({m[2] for m in MUTANTS}):
        cache = os.path.join(worktree, os.path.dirname(rel), "__pycache__")
        if os.path.isdir(cache):
            shutil.rmtree(cache)


def run_tests():
    return subprocess.run(
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
        env={
            **os.environ,
            "EXEMPT_EMAIL_DOMAINS": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )


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
        clear_pycache(WORKTREE)
        baseline = run_tests()
        with open(os.path.join(logs, "baseline.log"), "w") as fh:
            fh.write(f"# baseline, commit {commit}, exit {baseline.returncode}\n\n")
            fh.write(baseline.stdout + baseline.stderr)
        print(f"baseline exit={baseline.returncode}", flush=True)
        if baseline.returncode != 0:
            raise SystemExit("baseline is red: no mutant is run")
        rows = []
        for mid, guard, rel, old, new, nth in selected:
            path = os.path.join(WORKTREE, rel)
            pristine = sh("git", "show", f"{commit}:{rel}", cwd=REPO).stdout.encode()
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(replace_nth(text, old, new, nth))
            clear_pycache(WORKTREE)
            started = time.monotonic()
            proc = run_tests()
            elapsed = time.monotonic() - started
            sh("git", "checkout", "--", rel, cwd=WORKTREE)
            clear_pycache(WORKTREE)
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
