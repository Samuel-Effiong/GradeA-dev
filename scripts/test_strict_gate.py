#!/usr/bin/env python
"""
Self-tests and script mutants for scripts/strict_gate.py.

    python scripts/test_strict_gate.py                  # self-tests
    python scripts/test_strict_gate.py --mutants OUTDIR  # mutation battery

The end-to-end tests run the real gate script against a throwaway git repo
whose ``manage.py``, ``check_migration_safety.py`` and ``pre-commit`` are
stubs driven by a JSON scenario. Everything except Postgres, Redis and the
Django suite is real: git worktrees, fingerprints, systemd-inhibit, evidence
files. No test touches a real database or Redis.

The mutation battery copies the gate script to a scratch directory, applies
one fault per copy, and runs these self-tests against the copy
(``STRICT_GATE_SCRIPT``). Every mutant must make at least one test fail. The
tracked script is never edited; its sha256 is checked against ``HEAD`` after
the battery.

This file is deliberately outside Django's test discovery (``scripts`` is not
a package), so it does not change the application suite's test count.
"""

import argparse
import concurrent.futures
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = Path(os.environ.get("STRICT_GATE_SCRIPT", HERE / "strict_gate.py"))


def load_gate():
    spec = importlib.util.spec_from_file_location("strict_gate_under_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load_gate()
FLAKE = gate.KNOWN_FLAKE


def stub_manage_source():
    return STUB_MANAGE_TEMPLATE.replace("@@FLAKE@@", repr(FLAKE))


STUB_MANAGE_TEMPLATE = r'''
"""Stub manage.py driven by $STUB_SCENARIO. Records every call."""
import json
import os
import re
import sys
from pathlib import Path

FLAKE = @@FLAKE@@
scenario = json.loads(os.environ["STUB_SCENARIO"])
state = Path(os.environ["STUB_STATE"])
db = os.environ.get("STRICT_GATE_TEST_DB", "")
with open(state / "calls.jsonl", "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "db": db}) + "\n")
m = re.search(r"_r(\d+)", db)
run = int(m.group(1)) if m else 0
flake_db = db.endswith("_flake")


def per_run(key, default):
    value = scenario.get(key, default)
    if isinstance(value, dict):
        return value.get(str(run), default)
    return value


cmd = sys.argv[1]
if cmd == "shell":
    code = sys.argv[sys.argv.index("-c") + 1]
    if "strict-gate-probe:targets" in code:
        n = len(list(state.glob("targets-*")))
        (state / f"targets-{n}").write_text("")
        targets = {
            "environment": "local",
            "database": {"host": "127.0.0.1", "port": "5432"},
            "cache": {"host": "127.0.0.1", "port": "6379"},
            "celery_broker": {"host": "127.0.0.1", "port": "6379"},
            "celery_result_backend": {"host": "127.0.0.1", "port": "6379"},
        }
        bad_from = scenario.get("bad_targets_from_probe")
        if bad_from is not None and n >= bad_from:
            targets.update(scenario["bad_targets"])
        print("strict_gate_targets=" + json.dumps(targets))
    elif "strict-gate-probe:infra" in code:
        (state / "infra-probed").write_text("")
        print("strict_gate_postgres=PostgreSQL 99 (stub)")
        print("strict_gate_redis_round_trip=ok")
    elif "strict-gate-probe:pg" in code:
        print("68 objects imported automatically")
        if not scenario.get("pg_silent"):
            exists = (state / f"db-{db}").exists() or (
                scenario.get("db_exists_before") and not flake_db
                and not (state / f"ran-{db}").exists()
            )
            conns = 0 if flake_db else per_run("leave_connections", 0)
            if not (state / f"ran-{db}").exists():
                conns = 0
            print(f"strict_gate_pg_database_rows={int(bool(exists))}")
            print(f"strict_gate_pg_stat_activity_connections={conns}")
    sys.exit(0)
if cmd == "check":
    print("System check identified no issues (0 silenced).")
    sys.exit(per_run("check_exit", 0))
if cmd == "makemigrations":
    print("No changes detected")
    sys.exit(per_run("makemigrations_exit", 0))
if cmd == "test":
    (state / f"ran-{db}").write_text("")
    print(f"Creating test database for alias 'default' ('{db}')...")
    (state / f"db-{db}").write_text("")
    klass, method = FLAKE.rsplit(".", 1)
    if flake_db:
        print(f"{method} ({FLAKE}) ... ok")
        print("-" * 70)
        print("Ran 1 test in 0.5s")
        print("")
        print("OK")
        print("Destroying test database for alias 'default'...")
        (state / f"db-{db}").unlink()
        sys.exit(scenario.get("flake_rerun_exit", 0))
    flake = per_run("flake_fails", False)
    print("test_a (app.tests.T.test_a) ... ok")
    print("test_b (app.tests.T.test_b)")
    print("Docstring line ... ok")
    print("test_c (app.tests.T.test_c) ... skipped 'opt-in'")
    print(f"{method} ({FLAKE}) ... {'FAIL' if flake else 'ok'}")
    if flake:
        print("=" * 70)
        print(f"FAIL: {method} ({FLAKE})")
        print("AssertionError: thread still alive")
    print("-" * 70)
    print("Ran 4 tests in 1.234s")
    print("")
    print("FAILED (failures=1, skipped=1)" if flake else "OK (skipped=1)")
    if per_run("other_sessions", False):
        print("There is 1 other session using the database.")
    if per_run("touch_tracked", False):
        with open("README.md", "a") as f:
            f.write("changed by the suite\n")
    if per_run("create_untracked", False):
        Path("stray.txt").write_text("x")
    if not per_run("no_destroy_line", False):
        print("Destroying test database for alias 'default'...")
    if not per_run("leave_db", False):
        (state / f"db-{db}").unlink()
    sys.exit(per_run("suite_exit", 1 if flake else 0))
print("stub: unknown command", sys.argv)
sys.exit(99)
'''

STUB_SAFETY = """import json, os, sys
sys.exit(json.loads(os.environ["STUB_SCENARIO"]).get("safety_exit", 0))
"""

STUB_PRECOMMIT = """#!/usr/bin/env python
import json, os, sys
print("stub pre-commit", sys.argv[1:])
sys.exit(json.loads(os.environ["STUB_SCENARIO"]).get("precommit_exit", 0))
"""

DEPLOYED_ENV = (
    "ENVIRONMENT=local\n"
    "DATABASE_URI=postgresql://deployed-db.example.net:49129/railway\n"
    "DEPLOYED_TEST_REDIS_URL=redis://shinkansen.proxy.rlwy.net:20169\n"
    "REDIS_PROD_URL=redis://prod-redis.example.net:30847\n"
)

LOCAL_TARGETS = {
    "environment": "local",
    "database": {"host": "127.0.0.1", "port": "5432"},
    "cache": {"host": "127.0.0.1", "port": "6379"},
    "celery_broker": {"host": "127.0.0.1", "port": "6379"},
    "celery_result_backend": {"host": "localhost", "port": "6379"},
}

LAX = ["--max-heavy", "100000", "--min-disk-gb", "0", "--min-ram-gb", "0"]


class StubRepo:
    """A throwaway git repo plus the directories one gate needs."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="strict-gate-selftest-"))
        self.root = self.tmp / "repo"
        self.state = self.tmp / "state"
        self.bin = self.tmp / "bin"
        for d in (self.root / "scripts", self.state, self.bin):
            d.mkdir(parents=True)
        (self.root / "manage.py").write_text(stub_manage_source())
        (self.root / "scripts" / "check_migration_safety.py").write_text(STUB_SAFETY)
        (self.root / "README.md").write_text("stub\n")
        (self.root / ".gitignore").write_text(
            ".env\nsettings_worktree.py\n__pycache__/\n"
        )
        (self.root / ".env").write_text(DEPLOYED_ENV)
        pc = self.bin / "pre-commit"
        pc.write_text(STUB_PRECOMMIT)
        pc.chmod(0o755)
        self.git("init", "-q", "-b", "beta")
        self.git("-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".")
        self.git(
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "stub",
        )
        self.sha = self.git("rev-parse", "HEAD")
        self.evidence = self.tmp / "evidence"
        self.worktree = self.tmp / "gated"
        self.logs = self.tmp / "logs"

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=True
        ).stdout.strip()

    def gate(
        self,
        scenario=None,
        extra=(),
        runs=1,
        name="selftest",
        env_extra=None,
        wrapper=(),
    ):
        env = dict(os.environ)
        env.pop("STRICT_GATE_INHIBITED", None)
        env.update(env_extra or {})
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env["STUB_SCENARIO"] = json.dumps(scenario or {})
        env["STUB_STATE"] = str(self.state)
        args = [
            *wrapper,
            sys.executable,
            str(SCRIPT),
            "run",
            self.sha,
            name,
            "--runs",
            str(runs),
            "--worktree-dir",
            str(self.worktree),
            "--log-dir",
            str(self.logs),
            "--evidence-dir",
            str(self.evidence),
            *LAX,
            *extra,
        ]
        self.result = subprocess.run(
            args,
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        return self.result.returncode

    def summary(self):
        return json.loads((self.evidence / "summary.json").read_text())

    def stub_calls(self):
        path = self.state / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def suite_calls(self):
        return [c for c in self.stub_calls() if c["argv"][:1] == ["test"]]

    def landed(self, ref_sha=None):
        if ref_sha:
            self.git("update-ref", "refs/heads/beta", ref_sha)
        return subprocess.run(
            [sys.executable, str(SCRIPT), "verify-landed", str(self.evidence)],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

    def cleanup(self):
        subprocess.run(
            ["git", "worktree", "unlock", str(self.worktree)],
            cwd=self.root,
            capture_output=True,
            check=False,
        )
        shutil.rmtree(self.tmp, ignore_errors=True)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.repo = StubRepo()
        self.addCleanup(self.repo.cleanup)

    def explain(self):
        r = self.repo.result
        return f"exit {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"

    def assertFailedWith(self, fragment, scenario, runs=1):
        code = self.repo.gate(scenario, runs=runs)
        self.assertEqual(code, 1, self.explain())
        summary = self.repo.summary()
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertTrue(
            any(fragment in r for r in summary["verdict_reasons"]),
            summary["verdict_reasons"],
        )
        self.assertIn(
            "| 10. Final Production Gate | FAIL |",
            (self.repo.evidence / "EVIDENCE.md").read_text(),
        )
        return summary

    # The clean path.

    def test_two_clean_runs_pass_on_fresh_unique_dbs(self):
        code = self.repo.gate(runs=2)
        self.assertEqual(code, 0, self.explain())
        s = self.repo.summary()
        self.assertEqual(s["verdict"], "PASS")
        self.assertEqual(s["commit"], self.repo.sha)
        self.assertEqual(len(s["runs"]), 2)
        dbs = [r["test_db"] for r in s["runs"]]
        self.assertEqual(len(set(dbs)), 2)
        for run in s["runs"]:
            self.assertEqual(run["reasons"], [])
            self.assertEqual(run["fingerprint_before"], run["fingerprint_after"])
            self.assertEqual(run["fingerprint_before"]["head"], self.repo.sha)
            self.assertNotEqual(
                run["fingerprint_before"]["tracked_content_sha256"], gate.EMPTY_SHA256
            )
            names = [st["name"] for st in run["steps"]]
            self.assertEqual(
                names,
                [
                    "pre-commit run --all-files",
                    "check_migration_safety.py",
                    "manage.py check",
                    "makemigrations --check",
                    "full suite",
                ],
            )
            self.assertEqual(run["suite"]["ran"], 4)
            self.assertTrue(run["suite"]["per_test_consistent"], run["suite"])
        suites = self.repo.suite_calls()
        self.assertEqual([c["db"] for c in suites], dbs)
        for call in suites:
            self.assertIn("--noinput", call["argv"])
            self.assertNotIn("--keepdb", call["argv"])
        self.assertTrue(s["sleep_inhibitor_confirmed"])
        self.assertIn("handle-lid-switch", gate.INHIBIT_WHAT)
        listing = gzip.decompress(
            (self.repo.evidence / "inhibitor.txt.gz").read_bytes()
        ).decode()
        self.assertIn(gate.INHIBIT_WHAT, listing)
        self.assertIsInstance(s["prelaunch"]["test_processes"], list)
        self.assertIn(
            "Pre-launch slot accounting",
            (self.repo.evidence / "EVIDENCE.md").read_text(),
        )
        self.assertFalse(self.repo.worktree.exists(), "clean gate worktree is removed")
        sums = (self.repo.evidence / "RAW_LOG_SHA256SUMS.txt").read_text()
        self.assertIn("r2-5-full-suite.log", sums)
        self.assertTrue((self.repo.evidence / "r2-5-full-suite.log.gz").exists())
        stub = (self.repo.evidence / "EVIDENCE.md").read_text()
        self.assertIn("| 10. Final Production Gate | PARTIAL |", stub)
        self.assertIn("landed-SHA check pending", stub)
        self.assertEqual(stub.count("| NOT RUN | owner to supply |"), 9)

    def test_verify_landed_passes_only_when_beta_equals_gated_sha(self):
        self.assertEqual(self.repo.gate(runs=2), 0, self.explain())
        ok = self.repo.landed()
        self.assertEqual(ok.returncode, 0, ok.stderr)
        stub = (self.repo.evidence / "EVIDENCE.md").read_text()
        self.assertIn("| 10. Final Production Gate | PASS |", stub)
        self.repo.git(
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "moved",
        )
        moved = self.repo.landed()
        self.assertEqual(moved.returncode, 1, moved.stdout)
        self.assertFalse(self.repo.summary()["landing"]["match"])
        self.assertIn(
            "| 10. Final Production Gate | FAIL |",
            (self.repo.evidence / "EVIDENCE.md").read_text(),
        )

    def test_single_run_pass_is_only_partial_for_gate_10(self):
        self.assertEqual(self.repo.gate(runs=1), 0, self.explain())
        self.assertEqual(self.repo.landed().returncode, 0)
        stub = (self.repo.evidence / "EVIDENCE.md").read_text()
        self.assertIn("| 10. Final Production Gate | PARTIAL |", stub)
        self.assertIn("only 1 run", stub)

    def test_parallel_pilot_is_never_a_gate_pass(self):
        code = self.repo.gate(extra=["--parallel", "2"])
        self.assertEqual(code, 3, self.explain())
        self.assertEqual(self.repo.summary()["verdict"], "PILOT_CLEAN")
        self.assertIn(
            "| 10. Final Production Gate | NOT RUN |",
            (self.repo.evidence / "EVIDENCE.md").read_text(),
        )

    # Non-zero exits.

    def test_each_nonzero_exit_fails_the_gate(self):
        for key, name in [
            ("precommit_exit", "pre-commit"),
            ("safety_exit", "check_migration_safety"),
            ("check_exit", "manage.py check"),
            ("makemigrations_exit", "makemigrations"),
            ("suite_exit", "full suite exited 1"),
        ]:
            with self.subTest(key=key):
                repo = StubRepo()
                self.addCleanup(repo.cleanup)
                self.repo = repo
                self.assertFailedWith(name, {key: 1})

    def test_suite_exit_ok_but_failed_summary_fails(self):
        self.assertFailedWith(
            "suite result is 'FAILED'",
            {"flake_fails": True, "suite_exit": 0, "flake_rerun_exit": 0},
        )

    # The known flake.

    def test_flake_failure_fails_gate_even_when_rerun_alone_passes(self):
        s = self.assertFailedWith("known flake", {"flake_fails": True})
        rerun = s["runs"][0]["flake_rerun"]
        self.assertEqual(rerun["exit"], 0)
        self.assertEqual(rerun["result"], "OK")
        self.assertTrue(rerun["test_db"].endswith("_flake"))
        labels = [
            c["argv"] for c in self.repo.suite_calls() if c["db"].endswith("_flake")
        ]
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0][-1], FLAKE)

    def test_second_run_failure_fails_gate(self):
        s = self.assertFailedWith("run 2", {"flake_fails": {"2": True}}, runs=2)
        self.assertEqual(s["runs"][0]["reasons"], [])

    def test_first_run_failure_stops_and_fails(self):
        s = self.assertFailedWith("only 1 of 2 runs", {"check_exit": {"1": 1}}, runs=2)
        self.assertEqual(len(s["runs"]), 1)

    # Fingerprints.

    def test_tracked_file_changed_during_run_fails(self):
        self.assertFailedWith("fingerprint changed", {"touch_tracked": True})
        self.assertTrue(self.repo.worktree.exists(), "dirty worktree is kept")

    def test_untracked_file_created_during_run_fails(self):
        self.assertFailedWith("porcelain_lines", {"create_untracked": True})

    # Postgres and teardown.

    def test_db_left_behind_fails(self):
        self.assertFailedWith("pg_after", {"leave_db": True})

    def test_connections_left_behind_fail(self):
        self.assertFailedWith("pg_after", {"leave_connections": 2})

    def test_other_sessions_line_fails(self):
        self.assertFailedWith("other sessions", {"other_sessions": True})

    def test_missing_destroy_line_fails(self):
        self.assertFailedWith("Destroying", {"no_destroy_line": True})

    def test_silent_pg_probe_aborts_not_zero(self):
        self.assertEqual(self.repo.gate({"pg_silent": True}), 2, self.explain())
        self.assertEqual(self.repo.summary()["verdict"], "ABORTED")
        self.assertEqual(self.repo.suite_calls(), [])

    def test_existing_test_db_before_run_aborts(self):
        self.assertEqual(self.repo.gate({"db_exists_before": True}), 2, self.explain())
        self.assertIn(
            "already exists", " ".join(self.repo.summary()["verdict_reasons"])
        )
        self.assertEqual(self.repo.suite_calls(), [])

    # Refusals that must create nothing.

    def assertRefusedCleanly(self):
        self.assertEqual(self.repo.result.returncode, 2, self.explain())
        self.assertIn("GATE REFUSED", self.repo.result.stderr + self.repo.result.stdout)
        self.assertFalse(self.repo.worktree.exists())
        self.assertFalse(self.repo.evidence.exists())
        self.assertEqual(self.repo.stub_calls(), [])

    def test_keepdb_is_refused_before_anything_runs(self):
        for bad in (
            ["--test-arg=--keepdb"],
            ["--test-arg=--keepdb=1"],
            ["--test-arg=-k", "--test-arg=billing"],
            ["--test-arg=app.tests"],
            ["--test-arg=--tag=fast"],
        ):
            with self.subTest(bad=bad):
                self.repo.gate(extra=bad)
                self.assertEqual(self.repo.result.returncode, 2, self.explain())
                self.assertFalse(self.repo.worktree.exists())
                self.assertEqual(self.repo.stub_calls(), [])

    def test_prelaunch_guard_refuses_low_disk(self):
        self.repo.gate(extra=["--min-disk-gb", "999999999"])
        self.assertRefusedCleanly()

    def test_prelaunch_guard_refuses_low_ram(self):
        self.repo.gate(extra=["--min-ram-gb", "999999999"])
        self.assertRefusedCleanly()

    def test_prelaunch_guard_refuses_when_slots_are_full(self):
        self.repo.gate(extra=["--max-heavy", "0"])
        self.assertRefusedCleanly()

    def test_missing_sleep_inhibitor_aborts_before_any_run(self):
        # Pretend the re-exec already happened; no inhibitor is actually held.
        code = self.repo.gate(env_extra={"STRICT_GATE_INHIBITED": "1"})
        self.assertEqual(code, 2, self.explain())
        self.assertIn(
            "systemd-inhibit", " ".join(self.repo.summary()["verdict_reasons"])
        )
        self.assertEqual(self.repo.stub_calls(), [])

    def test_a_sleep_only_lock_is_not_accepted_as_protection(self):
        # Held under a lock with our WHO and our PID but WITHOUT
        # handle-lid-switch: a closed lid would still suspend, so refuse.
        wrapper = [
            "systemd-inhibit",
            "--what=sleep:idle",
            f"--who={gate.INHIBIT_WHO}",
            "--why=self-test: sleep-only lock",
        ]
        code = self.repo.gate(env_extra={"STRICT_GATE_INHIBITED": "1"}, wrapper=wrapper)
        self.assertEqual(code, 2, self.explain())
        self.assertIn(
            "systemd-inhibit", " ".join(self.repo.summary()["verdict_reasons"])
        )
        self.assertEqual(self.repo.stub_calls(), [])

    # Deployed infrastructure (HARD RULE).

    def assertRefusesDeployed(
        self, bad, from_probe=0, runs=1, fragment="REFUSING deployed"
    ):
        code = self.repo.gate(
            {"bad_targets_from_probe": from_probe, "bad_targets": bad}, runs=runs
        )
        self.assertEqual(code, 2, self.explain())
        s = self.repo.summary()
        self.assertEqual(s["verdict"], "ABORTED")
        self.assertIn(fragment, " ".join(s["verdict_reasons"]))
        return s

    def test_refuses_deployed_database_uri(self):
        self.assertRefusesDeployed(
            {"database": {"host": "deployed-db.example.net", "port": "49129"}},
            fragment="DATABASE_URI",
        )
        self.assertFalse((self.repo.state / "infra-probed").exists())
        self.assertEqual(self.repo.suite_calls(), [])

    def test_refuses_deployed_redis_host_even_on_another_port(self):
        s = self.assertRefusesDeployed(
            {"cache": {"host": "shinkansen.proxy.rlwy.net", "port": "1"}},
            fragment="shinkansen.proxy.rlwy.net is a named deployed host",
        )
        self.assertEqual(self.repo.suite_calls(), [])
        self.assertEqual(len(s["target_checks"]), 1)

    def test_refuses_every_named_deployed_host_by_name(self):
        for host in sorted(gate.DEPLOYED_HOSTS):
            for role in ("database", "cache", "celery_broker", "celery_result_backend"):
                with self.subTest(host=host, role=role):
                    problems = gate.deployed_target_problems(
                        dict(LOCAL_TARGETS, **{role: {"host": host, "port": "9"}}), {}
                    )
                    self.assertIn(
                        f"{role} target {host} is a named deployed host", problems
                    )

    def test_named_hosts_cover_beta_and_sandbox(self):
        for host in (
            "shinkansen.proxy.rlwy.net",
            "sandbox-grade-automator-production.up.railway.app",
            "thomas.proxy.rlwy.net",
            "iriguchi.proxy.rlwy.net",
        ):
            with self.subTest(host=host):
                self.assertIn(host, gate.DEPLOYED_HOSTS)
                problems = gate.deployed_target_problems(
                    dict(LOCAL_TARGETS, database={"host": host, "port": "5432"}), {}
                )
                self.assertIn(
                    f"database target {host} is a named deployed host", problems
                )

    def test_sandbox_env_keys_are_denied_by_host_and_port(self):
        env = {
            "SANDBOX_DATABASE_URI": "postgresql://sandbox-db.example:1111/x",
            "SANDBOX_REDIS_URL": "redis://sandbox-redis.example:2222",
            "SANDBOX_SERVER": "https://sandbox-app.example",
        }
        for key, target in (
            ("SANDBOX_DATABASE_URI", {"host": "sandbox-db.example", "port": "1111"}),
            ("SANDBOX_REDIS_URL", {"host": "sandbox-redis.example", "port": "2222"}),
            ("SANDBOX_SERVER", {"host": "sandbox-app.example", "port": "443"}),
        ):
            with self.subTest(key=key):
                problems = gate.deployed_target_problems(
                    dict(LOCAL_TARGETS, cache=target), env
                )
                self.assertTrue(any(f"({key})" in p for p in problems), problems)

    def test_refuses_any_non_local_target(self):
        self.assertRefusesDeployed(
            {"celery_broker": {"host": "other.example.org", "port": "6379"}},
            fragment="is not local",
        )
        self.assertEqual(self.repo.suite_calls(), [])

    def test_refuses_non_local_environment(self):
        self.assertRefusesDeployed({"environment": "prod"}, fragment="ENVIRONMENT")

    def test_rechecks_targets_immediately_before_the_suite(self):
        # Probe 0 = start, 1 = before run 1, 2 = before the suite of run 1.
        self.assertRefusesDeployed(
            {"cache": {"host": "deployed-db.example.net", "port": "49129"}},
            from_probe=2,
        )
        self.assertEqual(self.repo.suite_calls(), [])
        self.assertTrue((self.repo.state / "infra-probed").exists())

    def test_rechecks_targets_before_each_run(self):
        self.assertRefusesDeployed(
            {"database": {"host": "deployed-db.example.net", "port": "49129"}},
            from_probe=3,
            runs=2,
        )
        self.assertEqual(len(self.repo.suite_calls()), 1)


class PureLogic(unittest.TestCase):
    def test_default_suite_command_is_valid_and_complete(self):
        argv = gate.suite_command("python", 1)
        gate.validate_suite_command(argv)
        self.assertIn("--noinput", argv)
        self.assertNotIn("--keepdb", argv)

    def test_validate_rejects_db_reuse_and_narrowing(self):
        for extra in (
            ["--keepdb"],
            ["--keepdb=true"],
            ["-k", "x"],
            ["--tag", "x"],
            ["--exclude-tag=x"],
            ["--failfast"],
            ["billing"],
        ):
            with self.subTest(extra=extra):
                with self.assertRaises(gate.GateAbort):
                    gate.validate_suite_command(gate.suite_command("python", 1, extra))

    def test_parse_suite_log_reads_failures_and_counts(self):
        log = textwrap.dedent(
            """\
            test_x (a.T.test_x) ... ok
            test_y (a.T.test_y)
            Doc ... FAIL
            ======================================================================
            FAIL: test_y (a.T.test_y)
            ----------------------------------------------------------------------
            Ran 2 tests in 3.5s

            FAILED (failures=1, skipped=0)
            """
        )
        parsed = gate.parse_suite_log(log)
        self.assertEqual(parsed["ran"], 2)
        self.assertEqual(parsed["result"], "FAILED")
        self.assertEqual(
            parsed["failures"],
            [{"kind": "FAIL", "test": "a.T.test_y", "method": "test_y"}],
        )
        self.assertEqual(parsed["per_test"], {"a.T.test_x": "ok", "a.T.test_y": "FAIL"})

    def test_evaluate_counts_flake_as_failure(self):
        run = {
            "steps": [{"name": "full suite", "exit": 1}],
            "suite": {
                "ran": 1,
                "result": "FAILED",
                "counts": {},
                "failures": [{"kind": "FAIL", "test": FLAKE, "method": "m"}],
                "other_sessions_lines": 0,
                "destroying_lines": 1,
            },
            "pg_before": {"pg_database_rows": 0, "connections": 0},
            "pg_after": {"pg_database_rows": 0, "connections": 0},
            "fingerprint_before": {"porcelain_lines": 0},
            "fingerprint_after": {"porcelain_lines": 0},
            "flake_rerun": {"exit": 0},
        }
        reasons = gate.evaluate_run(run)
        self.assertTrue(any("known flake" in r for r in reasons), reasons)

    def test_pg_probe_without_result_raises(self):
        with self.assertRaises(gate.GateAbort):
            gate.parse_pg_probe("68 objects imported automatically\n")

    def test_deployed_targets_detected_from_env_values(self):
        env = {
            "DATABASE_URI": "postgresql://db.deployed:49129/x",
            "DEPLOYED_TEST_REDIS_URL": "redis://shinkansen.proxy.rlwy.net:20169",
        }
        local: dict = {"environment": "local"}
        for role in ("database", "cache", "celery_broker", "celery_result_backend"):
            local[role] = {"host": "127.0.0.1", "port": "5432"}
        self.assertEqual(gate.deployed_target_problems(local, env), [])
        bad = dict(local, database={"host": "db.deployed", "port": "49129"})
        self.assertTrue(
            any("DATABASE_URI" in p for p in gate.deployed_target_problems(bad, env))
        )
        redis = dict(
            local, celery_broker={"host": "shinkansen.proxy.rlwy.net", "port": "20169"}
        )
        self.assertTrue(
            any(
                "(DEPLOYED_TEST_REDIS_URL)" in p
                for p in gate.deployed_target_problems(redis, env)
            )
        )
        self.assertTrue(
            any("(DATABASE_URI)" in p for p in gate.deployed_target_problems(bad, env))
        )


def proc(pid, argv, cwd="/repo", ppid=1):
    return {
        "pid": pid,
        "ppid": ppid,
        "cwd": cwd,
        "argv": ["python", "manage.py", "test", *argv],
    }


OURS = "/repo/.git"


def ours_or_other(cwd):
    return OURS if cwd.startswith("/repo") else "/elsewhere/.git"


class SlotAccounting(unittest.TestCase):
    """The board's heavy-slot definition (Senior Manager ruling, option A)."""

    def slots(self, processes):
        return gate.slot_accounting(processes, ours_or_other, OURS)

    def test_full_run_counts_its_parallel_n(self):
        for argv, expected in (
            (["--settings=settings_worktree", "--noinput"], 1),
            (["--settings", "settings_worktree", "--noinput", "-v", "2"], 1),
            (["--noinput", "--parallel", "4", "-v", "2"], 4),
            (["--noinput", "--parallel=3"], 3),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(self.slots([proc(10, argv)])[0], expected)

    def test_mutation_worker_counts_one(self):
        total, rows = self.slots([proc(10, ["app.tests_x", "--keepdb", "--noinput"])])
        self.assertEqual(total, 1)
        self.assertIn("mutation worker", rows[0]["why"])

    def test_targeted_run_without_keepdb_counts_zero(self):
        total, rows = self.slots(
            [proc(10, ["app.tests_x", "billing.tests", "--noinput"])]
        )
        self.assertEqual(total, 0)
        self.assertIn("targeted", rows[0]["why"])

    def test_other_project_counts_zero_toward_cap(self):
        total, rows = self.slots(
            [proc(10, ["--noinput", "--parallel", "8"], cwd="/crm")]
        )
        self.assertEqual(total, 0)
        self.assertIn("other project", rows[0]["why"])

    def test_other_project_still_blocked_by_ram_and_disk_floors(self):
        total, _ = self.slots([proc(10, ["--noinput"], cwd="/crm")])
        self.assertEqual(total, 0)
        ram = gate.prelaunch_problems(
            total, 1, 6, disk=50, min_disk=3, ram=1.5, min_ram=2
        )
        disk = gate.prelaunch_problems(
            total, 1, 6, disk=2, min_disk=3, ram=9, min_ram=2
        )
        self.assertTrue(any("RAM" in p for p in ram), ram)
        self.assertTrue(any("disk" in p for p in disk), disk)

    def test_parallel_workers_are_not_double_counted(self):
        root = proc(10, ["--noinput", "--parallel", "4"])
        workers = [
            proc(11 + i, ["--noinput", "--parallel", "4"], ppid=10) for i in range(4)
        ]
        total, rows = self.slots([root, *workers])
        self.assertEqual(total, 4)
        self.assertEqual([r["slots"] for r in rows], [4, 0, 0, 0, 0])

    def test_unreadable_cwd_is_counted_as_ours(self):
        total, _ = self.slots([proc(10, ["--noinput"], cwd=None)])
        self.assertEqual(total, 1)

    def test_cap_breach_refuses_and_boundary_is_allowed(self):
        self.assertEqual(gate.prelaunch_problems(5, 1, 6, 50, 3, 9, 2), [])
        self.assertTrue(gate.prelaunch_problems(6, 1, 6, 50, 3, 9, 2))
        self.assertTrue(gate.prelaunch_problems(3, 4, 6, 50, 3, 9, 2))

    def test_every_process_is_listed_with_slots_and_reason(self):
        processes = [
            proc(10, ["--noinput"]),
            proc(20, ["a.tests", "--keepdb"]),
            proc(30, ["a.tests"]),
            proc(40, ["--noinput"], cwd="/crm"),
        ]
        total, rows = self.slots(processes)
        self.assertEqual(total, 2)
        self.assertEqual([r["pid"] for r in rows], [10, 20, 30, 40])
        self.assertEqual([r["slots"] for r in rows], [1, 1, 0, 0])
        for row in rows:
            self.assertTrue(row["why"])
            self.assertIn("manage.py test", row["argv"])


# Mutants.

MUTANTS = [
    (
        "M01 remove the --keepdb/partial-suite guard",
        'if arg.split("=", 1)[0] in FORBIDDEN_SUITE_ARGS:',
        "if False:",
    ),
    (
        "M02 skip the early guard call",
        "            validate_suite_command(\n"
        "                suite_command(sys.executable, args.parallel, args.test_arg)\n"
        "            )\n",
        "",
    ),
    (
        "M03 skip the fingerprint compare",
        "reasons += compare_fingerprints(\n"
        '            run["fingerprint_before"], run["fingerprint_after"]\n'
        "        )",
        "pass",
    ),
    ("M04 ignore a non-zero step exit", 'if step["exit"] != 0:', "if False:"),
    (
        "M05 count a flake as a pass when its rerun passes",
        'run["reasons"] = evaluate_run(run)',
        'run["reasons"] = [] if run.get("flake_rerun", {}).get("exit") == 0 else evaluate_run(run)',
    ),
    ("M06 accept a non-OK suite summary", 'if suite["result"] != "OK":', "if False:"),
    (
        "M07 add --keepdb to the suite command",
        '        "--noinput",\n        "--parallel",',
        '        "--noinput",\n        "--keepdb",\n        "--parallel",',
    ),
    (
        "M08 ignore a DB row or connection left behind",
        'elif pg["pg_database_rows"] or pg["connections"]:',
        "elif False:",
    ),
    (
        "M09 treat a silent Postgres probe as zero",
        '    if not rows or not conns:\n        raise GateAbort(f"Postgres probe gave no result:\\n{output}")\n',
        '    if not rows or not conns:\n        return {"pg_database_rows": 0, "connections": 0}\n',
    ),
    (
        "M10 ignore 'other sessions' lines",
        'if suite["other_sessions_lines"]:',
        "if False:",
    ),
    (
        "M11 ignore a missing teardown line",
        'if suite["destroying_lines"] < 1:',
        "if False:",
    ),
    (
        "M12 run only one of --runs",
        "for index in range(1, args.runs + 1):",
        "for index in range(1, 2):",
    ),
    (
        "M13 ignore the pre-launch refusal",
        'if self.summary["prelaunch"]["refused"]:',
        "if False:",
    ),
    (
        "M14 ignore an existing test DB before the run",
        'if run["pg_before"]["pg_database_rows"]:',
        "if False:",
    ),
    ("M15 report a pilot as a gate PASS", "    if parallel != 1:\n", "    if False:\n"),
    (
        "M16 verify-landed always matches",
        '"match": ref_sha == summary["commit"]',
        '"match": True',
    ),
    (
        "M17 deployed guard: drop the deny-list match",
        "            if host == dhost and (port == dport or not dport or not port):",
        "            if False:",
    ),
    (
        "M18 deployed guard: drop the local-only allow-list",
        "        if host not in LOCAL_HOSTS:",
        "        if False:",
    ),
    (
        "M19 deployed guard: drop the named deployed Redis host",
        "        if host in DEPLOYED_HOSTS:",
        "        if False:",
    ),
    (
        "M19a deployed guard: beta Redis host not named",
        '        "shinkansen.proxy.rlwy.net",\n',
        "",
    ),
    (
        "M19b deployed guard: sandbox app host not named",
        '        "sandbox-grade-automator-production.up.railway.app",\n',
        "",
    ),
    (
        "M19c deployed guard: sandbox Postgres host not named",
        '        "thomas.proxy.rlwy.net",\n',
        "",
    ),
    (
        "M19d deployed guard: sandbox Redis host not named",
        '        "iriguchi.proxy.rlwy.net",\n',
        "",
    ),
    (
        "M19e deployed guard: SANDBOX_DATABASE_URI key not denied",
        '    "SANDBOX_DATABASE_URI",\n',
        "",
    ),
    (
        "M19f deployed guard: SANDBOX_REDIS_URL key not denied",
        '    "SANDBOX_REDIS_URL",\n',
        "",
    ),
    (
        "M19g deployed guard: SANDBOX_SERVER key not denied",
        '    "SANDBOX_SERVER",\n',
        "",
    ),
    ("M19h deployed guard: DATABASE_URI key not denied", '    "DATABASE_URI",\n', ""),
    (
        "M19i deployed guard: DEPLOYED_TEST_REDIS_URL key not denied",
        '    "DEPLOYED_TEST_REDIS_URL",\n',
        "",
    ),
    (
        "M20 deployed guard: no refusal on problems",
        '        if problems:\n            raise GateAbort("REFUSING deployed infrastructure: "',
        '        if False:\n            raise GateAbort("REFUSING deployed infrastructure: "',
    ),
    (
        "M21 deployed guard: no recheck immediately before the suite",
        '        self.guard_targets(f"{r}-before-suite")\n',
        "",
    ),
    (
        "M22 deployed guard: no recheck before each run",
        '        self.guard_targets(f"{r}-before")\n',
        "",
    ),
    (
        "M23 deployed guard: environment not checked",
        '    if targets.get("environment") != "local":',
        "    if False:",
    ),
    (
        "M24 FAIL exits 0",
        '{"PASS": 0, "FAIL": 1, "PILOT_CLEAN": 3}',
        '{"PASS": 0, "FAIL": 0, "PILOT_CLEAN": 3}',
    ),
    (
        "M25 single run reported as full Gate 10 PASS",
        '        if summary["runs_requested"] < 2:',
        "        if False:",
    ),
    (
        "M27 never re-exec under systemd-inhibit",
        '    if os.environ.get(INHIBIT_ENV) != "1":',
        "    if False:",
    ),
    (
        "S01 full run counts 1 not N",
        'return parallel, f"full-suite run',
        'return 1, f"full-suite run',
    ),
    (
        "S02 targeted run counts 1",
        'return 0, "targeted module run',
        'return 1, "targeted module run',
    ),
    (
        "S03 other project counted on our cap",
        "    if not is_ours:\n",
        "    if False:\n",
    ),
    (
        "S04 mutation worker counts 0",
        'return 1, "mutation worker',
        'return 0, "mutation worker',
    ),
    ("S05 cap breach ignored", "    if used + requested > cap:", "    if False:"),
    (
        "S06 cap off by one",
        "    if used + requested > cap:",
        "    if used + requested >= cap:",
    ),
    ("S07 RAM floor skipped", "    if ram < min_ram:", "    if False:"),
    ("S08 disk floor skipped", "    if disk < min_disk:", "    if False:"),
    (
        "S09 parallel workers double-counted",
        '    if proc["ppid"] in root_pids:',
        "    if False:",
    ),
    (
        "S10 option value read as a test label",
        "        elif arg in TEST_OPTIONS_WITH_VALUE:\n            i += 1\n",
        "",
    ),
    (
        "S11 unreadable cwd treated as another project",
        "is_ours = common is None or common == our_common_dir",
        "is_ours = common == our_common_dir",
    ),
    ("S12 process list not recorded", '        "test_processes": rows,\n', ""),
    (
        "M28 lid-close not inhibited (sleep lock only)",
        'INHIBIT_WHAT = "sleep:idle:handle-lid-switch"',
        'INHIBIT_WHAT = "sleep:idle"',
    ),
    (
        "M29 confirmation ignores which lock is held",
        "            and INHIBIT_WHAT in line.split()\n",
        "",
    ),
    (
        "M26 inhibitor lock not required",
        "        if not ok:\n            raise GateAbort",
        "        if False:\n            raise GateAbort",
    ),
]


def run_mutant(mutant, pristine, workdir):
    name, old, new = mutant
    text = pristine.decode()
    if text.count(old) != 1:
        return {
            "mutant": name,
            "status": "INVALID",
            "detail": f"pattern found {text.count(old)} times",
        }
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower())[:40]
    target = Path(workdir) / slug / "strict_gate.py"
    target.parent.mkdir(parents=True)
    target.write_text(text.replace(old, new))
    env = dict(os.environ, STRICT_GATE_SCRIPT=str(target))
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=3600,
    )
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+) \(", result.stderr, re.M)))
    (target.parent / "selftest.log").write_text(result.stdout + result.stderr)
    return {
        "mutant": name,
        "status": "KILLED" if result.returncode != 0 else "SURVIVED",
        "exit": result.returncode,
        "failing_tests": failed,
        "log_sha256": hashlib.sha256(
            (result.stdout + result.stderr).encode()
        ).hexdigest(),
    }


def mutation_battery(outdir, workers):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=False)
    tracked = HERE / "strict_gate.py"
    pristine = tracked.read_bytes()
    before = hashlib.sha256(pristine).hexdigest()
    with tempfile.TemporaryDirectory(prefix="strict-gate-mutants-") as workdir:
        with concurrent.futures.ThreadPoolExecutor(workers) as pool:
            results = list(
                pool.map(lambda m: run_mutant(m, pristine, workdir), MUTANTS)
            )
        for m in MUTANTS:
            slug = re.sub(r"[^a-z0-9]+", "-", m[0].lower())[:40]
            log = Path(workdir) / slug / "selftest.log"
            if log.exists():
                shutil.copy(log, outdir / f"{slug}.log")
    after = hashlib.sha256(tracked.read_bytes()).hexdigest()
    head = subprocess.run(
        ["git", "show", "HEAD:scripts/strict_gate.py"],
        cwd=HERE,
        capture_output=True,
        check=False,
    ).stdout
    report = {
        "script_sha256_before": before,
        "script_sha256_after": after,
        "script_sha256_at_HEAD": hashlib.sha256(head).hexdigest() if head else None,
        "tracked_script_untouched": before == after,
        "mutants": results,
    }
    (outdir / "mutants.json").write_text(json.dumps(report, indent=2) + "\n")
    for r in results:
        print(
            f"{r['status']:9} {r['mutant']}  {r.get('failing_tests', r.get('detail'))}"
        )
    ok = all(r["status"] == "KILLED" for r in results) and before == after
    print(
        f"{sum(r['status'] == 'KILLED' for r in results)}/{len(results)} killed; "
        f"tracked script untouched: {before == after}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutants", metavar="OUTDIR")
    parser.add_argument("--workers", type=int, default=4)
    opts, rest = parser.parse_known_args()
    if opts.mutants:
        sys.exit(mutation_battery(opts.mutants, opts.workers))
    unittest.main(argv=[sys.argv[0], *rest])
