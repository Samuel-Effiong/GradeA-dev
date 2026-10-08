#!/usr/bin/env python
"""
Strict final gate (Verification Doctrine Gate 10, hardening H10) for one commit.

    python scripts/strict_gate.py run <commit> <run-name> [--runs 2]
    python scripts/strict_gate.py verify-landed docs/evidence/<run-name>

``run`` performs the owner's procedure end to end with no manual steps:

 1. Pre-launch guard: heavy-run slots, free disk and free RAM. Refuses to start
    when any is short.
 2. Re-executes itself under ``systemd-inhibit --what=sleep:idle:handle-lid-switch``
    and confirms that exact lock is registered.
 3. A detached, locked worktree at the commit, with a ``settings_worktree.py``
    that takes its test database name from the environment, so every run gets
    a fresh, uniquely named database.
 4. For each of ``--runs`` consecutive runs:
    fingerprint before (HEAD, sha256 of ``git ls-files -s``, sha256 over every
    tracked file's content, ``git status --porcelain`` line count); no
    ``pg_database`` row and 0 connections for the run's DB; exit 0 required
    from ``pre-commit run --all-files``, ``check_migration_safety.py``,
    ``manage.py check``, ``makemigrations --check`` and the full suite with
    ``--noinput`` and NO ``--keepdb``; then no DB row, 0 connections, no
    "other sessions" line, and a fingerprint identical to the one before.
 5. Any failure fails the gate, the known flaky test included. When that test
    fails it is rerun alone on its own fresh DB and both runs are recorded,
    but the rerun never changes the verdict.
 6. Complete, unfiltered logs, each with line count and sha256; gzip copies,
    ``summary.json`` and an ``EVIDENCE.md`` stub (10-gate table, Gate 10 filled
    from the logs) in ``docs/evidence/<run-name>/`` of the checkout the script
    is run from.

``verify-landed`` checks, after landing, that ``git rev-parse beta`` equals
the gated SHA (H10.3) and records the result in the same evidence directory.

Exit codes: 0 gate PASS / landed SHA matches; 1 gate FAIL / SHA mismatch;
2 aborted or refused; 3 a clean pilot run (``--parallel`` other than 1), which
is evidence only and never a gate pass.

Self-tests and script mutants: ``scripts/test_strict_gate.py``.
"""

import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

# sha256 of empty input: a fingerprint equal to this hashed nothing.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # pragma: allowlist secret

# Board rule "Known flake". A failure here is still a failure.
KNOWN_FLAKE = (
    "billing.tests.test_overage_purchase_integrity."
    "ConcurrentOverageDeliveryTests."
    "test_concurrent_purchases_by_different_teachers_stay_separate"
)

# Anything that would make the suite run less than everything, or reuse a DB.
FORBIDDEN_SUITE_ARGS = (
    "--keepdb",
    "-k",
    "--tag",
    "--exclude-tag",
    "--failfast",
    "--debug-mode",
    "--pdb",
)

# Deployed infrastructure. The suite FLUSHes Redis and creates/drops databases,
# so pointing it at any of these would wipe deployed data. The gate refuses
# unless every target is local AND none matches these (board HARD RULE).
DEPLOYED_ENV_KEYS = (
    "DATABASE_URI",
    "DATABASE_URI_DEV",
    "REDIS_PROD_URL",
    "REDIS_DEV_URL",
    "DEPLOYED_TEST_REDIS_URL",
    "DEPLOYED_TEST_SERVER",
    "SANDBOX_SERVER",
    "SANDBOX_DATABASE_URI",
    "SANDBOX_REDIS_URL",
)
# Named explicitly as well, so a missing or renamed .env key cannot open a hole:
# the deployed beta Redis, and the disposable sandbox's app, Postgres and Redis.
DEPLOYED_HOSTS = frozenset(
    {
        "shinkansen.proxy.rlwy.net",
        "sandbox-grade-automator-production.up.railway.app",
        "thomas.proxy.rlwy.net",
        "iriguchi.proxy.rlwy.net",
    }
)
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})

INHIBIT_ENV = "STRICT_GATE_INHIBITED"
TEST_DB_ENV = "STRICT_GATE_TEST_DB"
INHIBIT_WHO = "strict-gate"
# A sleep lock alone does NOT stop a lid-close suspend: logind's
# LidSwitchIgnoreInhibited defaults to yes, so the lid switch ignores every
# inhibitor except handle-lid-switch. This machine sets HandleLidSwitch=suspend,
# so without it a closed lid freezes a gate that believes it is protected.
INHIBIT_WHAT = "sleep:idle:handle-lid-switch"

SETTINGS_WORKTREE = '''"""
Strict gate settings, written by scripts/strict_gate.py. Gitignored.

The test database name comes from the environment so every gate run gets its
own fresh, uniquely named database. Missing it is an error, never a default.
"""

import os

from AutoGrader.settings import *  # noqa: F401,F403
from AutoGrader.settings import DATABASES

_name = os.environ.get("{env}")
if not _name:
    raise RuntimeError("{env} is not set; refusing to pick a test DB name")
DATABASES["default"].setdefault("TEST", {{}})
DATABASES["default"]["TEST"]["NAME"] = _name
'''.format(
    env=TEST_DB_ENV
)

PG_PROBE = """# strict-gate-probe:pg
from django.db import connection
name = {name!r}
pattern = "^" + name + "(_[0-9]+)?$"
with connection.cursor() as c:
    c.execute("select count(*) from pg_database where datname ~ %s", [pattern])
    print("strict_gate_pg_database_rows=%d" % c.fetchone()[0])
    c.execute("select count(*) from pg_stat_activity where datname ~ %s", [pattern])
    print("strict_gate_pg_stat_activity_connections=%d" % c.fetchone()[0])
"""

# Reads settings only; opens no DB or Redis connection.
TARGETS_PROBE = """# strict-gate-probe:targets
import json
from urllib.parse import urlsplit
from django.conf import settings

def url(value):
    value = value[0] if isinstance(value, (list, tuple)) else value
    parts = urlsplit(str(value))
    return {"host": parts.hostname or "", "port": str(parts.port or "")}

db = settings.DATABASES["default"]
targets = {
    "environment": str(getattr(settings, "ENVIRONMENT", "")),
    "database": {"host": str(db.get("HOST") or ""), "port": str(db.get("PORT") or "")},
    "cache": url(settings.CACHES["default"]["LOCATION"]),
    "celery_broker": url(getattr(settings, "CELERY_BROKER_URL", "") or ""),
    "celery_result_backend": url(getattr(settings, "CELERY_RESULT_BACKEND", "") or ""),
}
print("strict_gate_targets=" + json.dumps(targets, sort_keys=True))
"""

INFRA_PROBE = """# strict-gate-probe:infra
import sys
import django
from django.conf import settings
from django.core.cache import cache
from django.db import connection
with connection.cursor() as c:
    c.execute("select version()")
    print("strict_gate_postgres=" + c.fetchone()[0].split(",")[0])
print("strict_gate_python=" + sys.version.split()[0])
print("strict_gate_django=" + django.get_version())
print("strict_gate_cache_backend=" + settings.CACHES["default"]["BACKEND"])
cache.set("strict-gate-probe", "ok", 10)
print("strict_gate_redis_round_trip=" + str(cache.get("strict-gate-probe")))
cache.delete("strict-gate-probe")
"""


class GateAbort(Exception):
    """The gate cannot produce a verdict (broken setup, bad input, interrupted)."""


class GateRefused(GateAbort):
    """Refused before creating anything: no worktree, logs or evidence."""


def _raise_interrupt(*_):
    raise KeyboardInterrupt("SIGTERM")


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sh(args, cwd=None, env=None):
    """Run a command, capturing text output. Never raises on non-zero exit."""
    return subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


def git(cwd, *args):
    result = sh(["git", *args], cwd=cwd)
    if result.returncode != 0:
        raise GateAbort(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- pure logic


def sanitize(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def test_db_name(run_name, sha, index, suffix=""):
    """Unique per run name, commit and run index. Postgres caps names at 63."""
    return f"test_g_{sanitize(run_name)[:24]}_{sha[:7]}_r{index}{suffix}"


def suite_command(python, parallel, extra=()):
    return [
        python,
        "manage.py",
        "test",
        "--settings=settings_worktree",
        "--noinput",
        "--parallel",
        str(parallel),
        "-v",
        "2",
        *extra,
    ]


def validate_suite_command(argv, full_suite=True):
    """Refuse any command that reuses a DB or runs less than the whole suite."""
    for arg in argv:
        if arg.split("=", 1)[0] in FORBIDDEN_SUITE_ARGS:
            raise GateAbort(f"forbidden test argument {arg!r} in {argv}")
    if "--noinput" not in argv:
        raise GateAbort(f"test command lacks --noinput: {argv}")
    if full_suite:
        tail = argv[argv.index("test") + 1 :]
        skip_next = False
        for arg in tail:
            if skip_next:
                skip_next = False
                continue
            if arg in ("--parallel", "-v"):
                skip_next = True
                continue
            if not arg.startswith("-"):
                raise GateAbort(f"full suite must have no test labels: {argv}")


def read_env_file(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip().removeprefix("export ")] = value.strip().strip("'\"")
    return values


def host_port(value):
    parts = urlsplit(value if "://" in value else f"x://{value}")
    try:
        port = str(parts.port or "")
    except ValueError:
        port = ""
    return (parts.hostname or "").lower(), port


def parse_targets(output):
    found = re.findall(r"^strict_gate_targets=(\{.*\})$", output, re.M)
    if len(found) != 1:
        raise GateAbort(f"target probe gave no single result:\n{output}")
    return json.loads(found[0])


def deployed_target_problems(targets, env_values):
    """Why these targets could touch deployed data. Empty means all local."""
    problems = []
    if targets.get("environment") != "local":
        problems.append(f"ENVIRONMENT is {targets.get('environment')!r}, not 'local'")
    deployed = {}
    for key in DEPLOYED_ENV_KEYS:
        if env_values.get(key):
            deployed.setdefault(host_port(env_values[key]), []).append(key)
    for role in ("database", "cache", "celery_broker", "celery_result_backend"):
        target = targets.get(role)
        if not isinstance(target, dict):
            problems.append(f"{role} target missing")
            continue
        host, port = str(target.get("host", "")).lower(), str(target.get("port", ""))
        for (dhost, dport), keys in deployed.items():
            if host == dhost and (port == dport or not dport or not port):
                problems.append(
                    f"{role} target {host}:{port} is deployed ({', '.join(keys)})"
                )
        if host in DEPLOYED_HOSTS:
            problems.append(f"{role} target {host} is a named deployed host")
        if host not in LOCAL_HOSTS:
            problems.append(f"{role} target {host}:{port} is not local")
    return problems


def compare_fingerprints(before, after):
    if before != after:
        changed = sorted(k for k in before if before.get(k) != after.get(k))
        return [f"fingerprint changed during the run: {', '.join(changed)}"]
    return []


def parse_pg_probe(output):
    rows = re.search(r"^strict_gate_pg_database_rows=(\d+)$", output, re.M)
    conns = re.search(r"^strict_gate_pg_stat_activity_connections=(\d+)$", output, re.M)
    if not rows or not conns:
        raise GateAbort(f"Postgres probe gave no result:\n{output}")
    return {"pg_database_rows": int(rows.group(1)), "connections": int(conns.group(1))}


RESULT_TOKEN = re.compile(
    r"(?:^|\.\.\. )(ok|FAIL|ERROR|skipped(?: .*)?|expected failure|unexpected success)$"
)
TEST_HEADER = re.compile(r"^(\w+) \(([\w.]+)\)")


def parse_suite_log(text):
    """Summarise a ``manage.py test -v 2`` log. Only facts found in the log."""
    ran = re.findall(r"^Ran (\d+) tests? in ([\d.]+)s$", text, re.M)
    final = re.findall(r"^(OK|FAILED)(?: \((.*)\))?$", text, re.M)
    counts = {}
    if final:
        for part in (final[-1][1] or "").split(","):
            if "=" in part:
                key, value = part.strip().split("=")
                counts[key] = int(value)
    failures = []
    for kind, method, where in re.findall(
        r"^(FAIL|ERROR): (\w+) \(([\w.]+)\)", text, re.M
    ):
        failures.append({"kind": kind, "test": where, "method": method})

    per_test = {}
    current = None
    for line in text.splitlines():
        header = TEST_HEADER.match(line)
        if header and (" ... " in line or line.endswith(")")):
            method, where = header.groups()
            current = (
                f"{method} ({where})"
                if method in ("setUpClass", "tearDownClass", "setUpModule")
                else where
            )
        if current is None:
            continue
        token = RESULT_TOKEN.search(line)
        if token and (" ... " in line or line == token.group(1)):
            outcome = token.group(1)
            per_test[current] = "skipped" if outcome.startswith("skipped") else outcome
            current = None

    tally = {}
    for outcome in per_test.values():
        tally[outcome] = tally.get(outcome, 0) + 1
    ran_n = int(ran[-1][0]) if ran else None
    return {
        "ran": ran_n,
        "seconds": float(ran[-1][1]) if ran else None,
        "result": final[-1][0] if final else None,
        "counts": counts,
        "failures": failures,
        "other_sessions_lines": len(re.findall(r"other session", text, re.I)),
        "destroying_lines": len(re.findall(r"Destroying test database", text)),
        "per_test": per_test,
        "per_test_tally": tally,
        "per_test_consistent": ran_n is not None
        and len(per_test) == ran_n
        and tally.get("skipped", 0) == counts.get("skipped", 0),
    }


def evaluate_run(run):
    """Every reason this run is not clean. An empty list means clean."""
    reasons = []
    for step in run["steps"]:
        if step["exit"] != 0:
            reasons.append(f"{step['name']} exited {step['exit']}")
    suite = run.get("suite")
    if suite is None:
        reasons.append("full suite did not run")
    else:
        if suite["ran"] is None:
            reasons.append("suite log has no 'Ran N tests' line")
        if suite["result"] != "OK":
            reasons.append(f"suite result is {suite['result']!r}, not 'OK'")
        for failure in suite["failures"]:
            flake = (
                " (known flake: counts as a failure)"
                if failure["test"] == KNOWN_FLAKE
                else ""
            )
            reasons.append(f"{failure['kind']}: {failure['test']}{flake}")
        if suite["other_sessions_lines"]:
            reasons.append(f"{suite['other_sessions_lines']} 'other sessions' lines")
        if suite["destroying_lines"] < 1:
            reasons.append("no 'Destroying test database' line")
    for when in ("pg_before", "pg_after"):
        pg = run.get(when)
        if pg is None:
            reasons.append(f"{when} not recorded")
        elif pg["pg_database_rows"] or pg["connections"]:
            reasons.append(
                f"{when}: {pg['pg_database_rows']} DB rows, "
                f"{pg['connections']} connections"
            )
    if run.get("fingerprint_after") is None:
        reasons.append("fingerprint after not recorded")
    else:
        reasons += compare_fingerprints(
            run["fingerprint_before"], run["fingerprint_after"]
        )
    if run["fingerprint_before"]["porcelain_lines"] != 0:
        reasons.append("worktree not clean before the run")
    return reasons


def gate_verdict(runs, runs_requested, parallel):
    reasons = []
    for run in runs:
        reasons += [f"run {run['index']}: {r}" for r in run["reasons"]]
    if len(runs) < runs_requested:
        reasons.append(f"only {len(runs)} of {runs_requested} runs completed")
    if reasons:
        return "FAIL", reasons
    if parallel != 1:
        return "PILOT_CLEAN", [f"--parallel {parallel} is a pilot, not a gate"]
    return "PASS", []


def flake_failed(suite):
    return any(f["test"] == KNOWN_FLAKE for f in suite["failures"])


# ------------------------------------------------------------- host and git


# ``manage.py test`` options that take a separate value, so the value is not
# mistaken for a test label.
TEST_OPTIONS_WITH_VALUE = frozenset(
    {
        "--settings",
        "--pythonpath",
        "-v",
        "--verbosity",
        "-k",
        "--tag",
        "--exclude-tag",
        "--testrunner",
        "-p",
        "--pattern",
        "-t",
        "--top-level-directory",
        "--durations",
        "--shuffle",
    }
)


def scan_test_processes():
    """Every running ``python ... manage.py test`` process on the host."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes().split(b"\0")
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        argv = [a.decode(errors="replace") for a in raw if a]
        if len(argv) < 3 or not Path(argv[0]).name.startswith("python"):
            continue
        if "manage.py" not in argv or "test" not in argv:
            continue
        try:
            cwd = os.readlink(entry / "cwd")
        except OSError:
            cwd = None
        found.append(
            {
                "pid": int(entry.name),
                "ppid": int(stat.rsplit(")", 1)[1].split()[1]),
                "cwd": cwd,
                "argv": argv,
            }
        )
    return found


def parse_test_argv(argv):
    """(labels, parallel, keepdb) for a ``manage.py test`` command line."""
    tail = argv[argv.index("test") + 1 :]
    labels, parallel, keepdb = [], 1, False
    i = 0
    while i < len(tail):
        arg = tail[i]
        name, _, inline = arg.partition("=")
        if arg == "--keepdb":
            keepdb = True
        elif name == "--parallel":
            value = inline
            if not inline and i + 1 < len(tail) and not tail[i + 1].startswith("-"):
                value = tail[i + 1]
                i += 1
            value = value or "auto"
            parallel = (os.cpu_count() or 1) if value == "auto" else int(value)
        elif arg in TEST_OPTIONS_WITH_VALUE:
            i += 1
        elif not arg.startswith("-"):
            labels.append(arg)
        i += 1
    return labels, parallel, keepdb


def classify_test_process(proc, is_ours, root_pids):
    """Slots one process takes from OUR heavy cap, and why (board definition)."""
    if proc["ppid"] in root_pids:
        return 0, f"worker of pid {proc['ppid']}, counted with that run"
    if not is_ours:
        return 0, "other project: not on our cap (RAM/disk floors still apply)"
    labels, parallel, keepdb = parse_test_argv(proc["argv"])
    if not labels:
        return parallel, f"full-suite run: --parallel {parallel} = {parallel} slot(s)"
    if keepdb:
        return 1, "mutation worker (labels + --keepdb): 1 slot"
    return 0, "targeted module run (labels, no --keepdb): 0 slots"


def slot_accounting(processes, common_dir_of, our_common_dir):
    """Classify every process; returns (total slots, auditable per-process rows)."""
    pids = {p["pid"] for p in processes}
    rows = []
    for proc in processes:
        common = common_dir_of(proc["cwd"]) if proc["cwd"] else None
        # An unreadable cwd is counted as ours: over-counting only delays a run.
        is_ours = common is None or common == our_common_dir
        slots, why = classify_test_process(proc, is_ours, pids)
        rows.append(
            {
                "pid": proc["pid"],
                "ppid": proc["ppid"],
                "cwd": proc["cwd"],
                "argv": " ".join(proc["argv"]),
                "slots": slots,
                "why": why,
            }
        )
    return sum(r["slots"] for r in rows), rows


def git_common_dir(cwd):
    result = sh(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=cwd
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def mem_available_gb():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024 / 1024
    raise GateAbort("MemAvailable missing from /proc/meminfo")


def prelaunch_problems(used, requested, cap, disk, min_disk, ram, min_ram):
    """Reasons to refuse. Disk and RAM floors apply whatever uses the resources."""
    problems = []
    if used + requested > cap:
        problems.append(f"{used} heavy slots in use + {requested} > cap {cap}")
    if disk < min_disk:
        problems.append(f"disk free {disk:.1f} GB < {min_disk} GB")
    if ram < min_ram:
        problems.append(f"RAM available {ram:.1f} GB < {min_ram} GB")
    return problems


def prelaunch(args, where, our_common_dir):
    used, rows = slot_accounting(scan_test_processes(), git_common_dir, our_common_dir)
    disk = shutil.disk_usage(where).free / 1024**3
    ram = mem_available_gb()
    return {
        "heavy_slots_in_use": used,
        "slots_requested": args.parallel,
        "max_heavy_slots": args.max_heavy,
        "disk_free_gb": round(disk, 2),
        "min_disk_gb": args.min_disk_gb,
        "ram_available_gb": round(ram, 2),
        "min_ram_gb": args.min_ram_gb,
        "test_processes": rows,
        "refused": prelaunch_problems(
            used,
            args.parallel,
            args.max_heavy,
            disk,
            args.min_disk_gb,
            ram,
            args.min_ram_gb,
        ),
    }


def fingerprint(worktree):
    files = sh(["git", "ls-files", "-z"], cwd=worktree)
    names = [n for n in files.stdout.split("\0") if n]
    if files.returncode != 0 or not names:
        raise GateAbort("fingerprint saw 0 tracked files")
    per_file = []
    for name in names:
        path = Path(worktree, name)
        # A tracked symlink is hashed as its target string, like git does.
        data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        per_file.append(f"{sha256_bytes(data)}  {name}\n")
    content = sha256_bytes("".join(per_file).encode())
    if content == EMPTY_SHA256:
        raise GateAbort("fingerprint hashed empty input")
    index = sh(["git", "ls-files", "-s"], cwd=worktree).stdout
    porcelain = sh(["git", "status", "--porcelain"], cwd=worktree).stdout
    return {
        "head": git(worktree, "rev-parse", "HEAD"),
        "tree": git(worktree, "rev-parse", "HEAD^{tree}"),
        "tracked_files": len(names),
        "index_sha256": sha256_bytes(index.encode()),
        "tracked_content_sha256": content,
        "porcelain_lines": len([ln for ln in porcelain.splitlines() if ln]),
    }


def inhibitor_confirmed():
    listing = sh(["systemd-inhibit", "--list", "--no-pager"]).stdout
    return (
        any(
            line.split()[:1] == [INHIBIT_WHO]
            and str(os.getppid()) in line.split()
            and INHIBIT_WHAT in line.split()
            for line in listing.splitlines()
        ),
        listing,
    )


def suspend_events_since(start):
    result = sh(["journalctl", "--since", start, "--no-pager"])
    if result.returncode != 0:
        return None
    return len(
        re.findall(r"systemd-sleep|PM: suspend|Suspending system", result.stdout)
    )


# ---------------------------------------------------------------- the gate


class Gate:
    def __init__(self, args):
        self.args = args
        self.python = sys.executable
        self.invoking_root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
        common = Path(
            git(Path.cwd(), "rev-parse", "--path-format=absolute", "--git-common-dir")
        )
        self.main_root = common.parent
        self.common_dir = str(common)
        self.sha = git(
            self.invoking_root, "rev-parse", "--verify", f"{args.commit}^{{commit}}"
        )
        self.run_name = args.run_name
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*[a-z0-9]", self.run_name):
            raise GateAbort("run name must be kebab-case")
        parent = self.main_root.parent
        self.worktree = Path(
            args.worktree_dir or parent / f"{self.main_root.name}-gate-{self.run_name}"
        )
        self.log_dir = Path(
            args.log_dir or parent / f"{self.main_root.name}-gate-logs" / self.run_name
        )
        self.evidence = Path(
            args.evidence_dir
            or self.invoking_root / "docs" / "evidence" / self.run_name
        )
        self.summary = {
            "tool": "scripts/strict_gate.py",
            "tool_sha256": sha256_bytes(Path(__file__).read_bytes()),
            "run_name": self.run_name,
            "commit": self.sha,
            "runs_requested": args.runs,
            "parallel": args.parallel,
            "known_flake": KNOWN_FLAKE,
            "started": now(),
            "runs": [],
            "logs": {},
        }

    def log(self, message):
        line = f"[{now()}] {message}"
        print(line, flush=True)
        with open(self.log_dir / "gate.log", "a") as handle:
            handle.write(line + "\n")

    def env(self, db):
        env = dict(os.environ)
        env[TEST_DB_ENV] = db
        return env

    def step(self, run, name, argv, logname, db):
        start = now()
        self.log(f"run {run['index']}: {name}: {' '.join(argv)}")
        path = self.log_dir / logname
        with open(path, "wb") as out:
            rc = subprocess.run(
                argv,
                cwd=self.worktree,
                env=self.env(db),
                stdout=out,
                stderr=subprocess.STDOUT,
                check=False,
            ).returncode
        record = {
            "name": name,
            "argv": argv,
            "exit": rc,
            "started": start,
            "finished": now(),
            "log": logname,
        }
        record.update(self.log_facts(path))
        run["steps"].append(record)
        self.log(f"run {run['index']}: {name}: exit {rc}")
        return record, path

    def log_facts(self, path):
        data = path.read_bytes()
        facts = {"lines": data.count(b"\n"), "sha256": sha256_bytes(data)}
        self.summary["logs"][path.name] = facts
        return facts

    def probe(self, code, db, logname):
        result = sh(
            [
                self.python,
                "manage.py",
                "shell",
                "--settings=settings_worktree",
                "-c",
                code,
            ],
            cwd=self.worktree,
            env=self.env(db),
        )
        (self.log_dir / logname).write_text(result.stdout + result.stderr)
        self.log_facts(self.log_dir / logname)
        return result.stdout + result.stderr

    def pg(self, db, logname):
        return parse_pg_probe(self.probe(PG_PROBE.format(name=db), db, logname))

    def prepare(self):
        if self.evidence.exists():
            raise GateRefused(f"evidence dir {self.evidence} already exists")
        if self.worktree.exists():
            raise GateRefused(f"worktree {self.worktree} already exists")
        if self.log_dir.exists():
            raise GateRefused(f"log dir {self.log_dir} already exists")
        env_file = self.main_root / ".env"
        if not env_file.is_file():
            raise GateRefused(f"no .env at {env_file}")
        self.summary["prelaunch"] = prelaunch(
            self.args, self.main_root.parent, self.common_dir
        )
        if self.summary["prelaunch"]["refused"]:
            raise GateRefused(
                "pre-launch guard: " + "; ".join(self.summary["prelaunch"]["refused"])
            )
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log(f"pre-launch: {json.dumps(self.summary['prelaunch'])}")
        ok, listing = inhibitor_confirmed()
        (self.log_dir / "inhibitor.txt").write_text(listing)
        self.summary["sleep_inhibitor_confirmed"] = ok
        if not ok:
            raise GateAbort("systemd-inhibit sleep:idle lock not found for this gate")
        base = git(
            self.main_root, "rev-parse", "--verify", f"{self.args.base_ref}^{{commit}}"
        )
        self.summary["base_ref"] = self.args.base_ref
        self.summary["base_sha_at_start"] = base
        git(self.main_root, "worktree", "add", "--detach", str(self.worktree), self.sha)
        git(
            self.main_root,
            "worktree",
            "lock",
            "--reason",
            f"strict gate {self.run_name}",
            str(self.worktree),
        )
        (self.worktree / ".env").symlink_to(env_file)
        (self.worktree / "settings_worktree.py").write_text(SETTINGS_WORKTREE)
        if git(self.worktree, "rev-parse", "HEAD") != self.sha:
            raise GateAbort("worktree HEAD is not the gated commit")
        if sh(["git", "symbolic-ref", "-q", "HEAD"], cwd=self.worktree).returncode == 0:
            raise GateAbort("worktree is not detached")
        self.summary["worktree"] = str(self.worktree)
        self.summary["log_dir"] = str(self.log_dir)
        self.summary["evidence_dir"] = str(self.evidence)

    def guard_targets(self, when):
        """HARD RULE: never let the suite or a probe reach deployed data."""
        out = self.probe(
            TARGETS_PROBE,
            test_db_name(self.run_name, self.sha, 0),
            f"targets-{when}.txt",
        )
        targets = parse_targets(out)
        problems = deployed_target_problems(
            targets, read_env_file(self.main_root / ".env")
        )
        self.summary.setdefault("target_checks", []).append(
            {"when": when, "targets": targets, "problems": problems}
        )
        if problems:
            raise GateAbort("REFUSING deployed infrastructure: " + "; ".join(problems))

    def infra(self):
        out = self.probe(
            INFRA_PROBE, test_db_name(self.run_name, self.sha, 0), "infra.txt"
        )
        facts = dict(re.findall(r"^strict_gate_(\w+)=(.*)$", out, re.M))
        self.summary["infrastructure"] = facts
        if not facts.get("postgres", "").startswith("PostgreSQL"):
            raise GateAbort(f"real PostgreSQL not reachable:\n{out}")
        if facts.get("redis_round_trip") != "ok":
            raise GateAbort(
                f"real Redis not reachable through the Django cache:\n{out}"
            )

    def one_run(self, index):
        db = test_db_name(self.run_name, self.sha, index)
        run = {"index": index, "test_db": db, "started": now(), "steps": []}
        self.summary["runs"].append(run)
        r = f"r{index}"
        self.guard_targets(f"{r}-before")
        run["fingerprint_before"] = fingerprint(self.worktree)
        run["pg_before"] = self.pg(db, f"{r}-0-pg-before.txt")
        if run["pg_before"]["pg_database_rows"]:
            raise GateAbort(f"test DB {db} already exists before run {index}")
        self.step(
            run,
            "pre-commit run --all-files",
            ["pre-commit", "run", "--all-files"],
            f"{r}-1-precommit.log",
            db,
        )
        self.step(
            run,
            "check_migration_safety.py",
            [
                self.python,
                "scripts/check_migration_safety.py",
                "--base",
                self.summary["base_sha_at_start"],
            ],
            f"{r}-2-migration-safety.log",
            db,
        )
        self.step(
            run,
            "manage.py check",
            [self.python, "manage.py", "check", "--settings=settings_worktree"],
            f"{r}-3-check.log",
            db,
        )
        self.step(
            run,
            "makemigrations --check",
            [
                self.python,
                "manage.py",
                "makemigrations",
                "--check",
                "--dry-run",
                "--settings=settings_worktree",
            ],
            f"{r}-4-makemigrations.log",
            db,
        )
        argv = suite_command(self.python, self.args.parallel, self.args.test_arg)
        self.guard_targets(f"{r}-before-suite")
        _, path = self.step(run, "full suite", argv, f"{r}-5-full-suite.log", db)
        suite = parse_suite_log(path.read_text(errors="replace"))
        self.write_per_test(suite, f"{r}-5-full-suite.tests.tsv")
        run["suite"] = {k: v for k, v in suite.items() if k != "per_test"}
        run["pg_after"] = self.pg(db, f"{r}-6-pg-after.txt")
        run["fingerprint_after"] = fingerprint(self.worktree)
        if flake_failed(suite):
            run["flake_rerun"] = self.flake_rerun(index)
        run["reasons"] = evaluate_run(run)
        run["finished"] = now()
        self.log(
            f"run {index}: {'CLEAN' if not run['reasons'] else 'NOT CLEAN'} "
            f"{run['reasons']}"
        )
        return not run["reasons"]

    def flake_rerun(self, index):
        """Evidence only: records how the flake behaves alone. Never a pass."""
        db = test_db_name(self.run_name, self.sha, index, "_flake")
        holder = {"index": index, "steps": []}
        self.guard_targets(f"r{index}-before-flake-rerun")
        argv = suite_command(self.python, 1, [KNOWN_FLAKE])
        validate_suite_command(argv, full_suite=False)
        step, path = self.step(
            holder, "known flake alone", argv, f"r{index}-7-flake-rerun.log", db
        )
        suite = parse_suite_log(path.read_text(errors="replace"))
        return {
            "test_db": db,
            "exit": step["exit"],
            "log": step["log"],
            "ran": suite["ran"],
            "result": suite["result"],
            "pg_after": self.pg(db, f"r{index}-7-flake-pg-after.txt"),
            "note": "recorded only; does not change the gate verdict",
        }

    def write_per_test(self, suite, name):
        lines = [
            f"{test}\t{outcome}\n"
            for test, outcome in sorted(suite["per_test"].items())
        ]
        (self.log_dir / name).write_text("".join(lines))
        self.log_facts(self.log_dir / name)

    def finish(self, verdict, reasons):
        self.summary["verdict"] = verdict
        self.summary["verdict_reasons"] = reasons
        self.summary["finished"] = now()
        try:
            self.summary["base_sha_at_end"] = git(
                self.main_root,
                "rev-parse",
                "--verify",
                f"{self.args.base_ref}^{{commit}}",
            )
        except GateAbort as exc:
            self.summary["base_sha_at_end"] = f"unavailable: {exc}"
        self.summary["suspend_events"] = suspend_events_since(self.summary["started"])
        if self.summary["suspend_events"]:
            self.summary["verdict"] = "FAIL"
            reasons.append(
                f"{self.summary['suspend_events']} suspend events during the gate"
            )
        self.log(f"VERDICT: {self.summary['verdict']} {reasons}")
        self.cleanup_worktree()
        self.write_evidence()

    def cleanup_worktree(self):
        if not self.worktree.exists() or self.args.keep_worktree:
            return
        if sh(["git", "status", "--porcelain"], cwd=self.worktree).stdout.strip():
            self.summary["worktree_kept"] = "dirty; kept for investigation"
            return
        sh(["git", "worktree", "unlock", str(self.worktree)], cwd=self.main_root)
        removed = sh(
            ["git", "worktree", "remove", "--force", str(self.worktree)],
            cwd=self.main_root,
        )
        self.summary["worktree_removed"] = removed.returncode == 0

    def write_evidence(self):
        self.evidence.mkdir(parents=True, exist_ok=False)
        sums = []
        for path in sorted(self.log_dir.iterdir()):
            data = path.read_bytes()
            lines = data.count(b"\n")
            sums.append(f"{sha256_bytes(data)}  {lines:>7} lines  {path.name}\n")
            with open(self.evidence / f"{path.name}.gz", "wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                    gz.write(data)
        (self.evidence / "RAW_LOG_SHA256SUMS.txt").write_text("".join(sums))
        (self.evidence / "summary.json").write_text(
            json.dumps(self.summary, indent=2) + "\n"
        )
        (self.evidence / "EVIDENCE.md").write_text(render_stub(self.summary))


def gate10_row(summary):
    verdict = summary.get("verdict")
    landing = summary.get("landing")
    runs = [r for r in summary.get("runs", []) if "reasons" in r and not r["reasons"]]
    last = runs[-1]["suite"] if runs else None
    if verdict == "PASS" and last is not None:
        detail = (
            f"`{summary['commit']}`: {len(runs)} consecutive clean full run(s), "
            f"last `Ran {last['ran']}` {last['result']} {last['counts']}"
        )
        if summary["runs_requested"] < 2:
            status, note = (
                "PARTIAL",
                "; only 1 run (H10.2 needs 2 for security/billing/cache changes)",
            )
        elif not landing:
            status, note = "PARTIAL", "; landed-SHA check pending (`verify-landed`)"
        elif landing["match"]:
            status, note = (
                "PASS",
                f"; `{landing['ref']}` == gated SHA at {landing['checked']}",
            )
        else:
            status, note = (
                "FAIL",
                f"; `{landing['ref']}` is `{landing['ref_sha']}`, not the gated SHA",
            )
        return status, detail + note
    if verdict == "PILOT_CLEAN":
        return (
            "NOT RUN",
            f"pilot at --parallel {summary['parallel']}: evidence only, not a gate",
        )
    status = "FAIL" if verdict == "FAIL" else "BLOCKED"
    return status, f"verdict {verdict}: " + "; ".join(
        summary.get("verdict_reasons", [])
    )


def render_stub(summary):
    status, detail = gate10_row(summary)
    gates = [
        "Baseline / Regression",
        "Mutation",
        "Concurrency",
        "Adversarial / Attack",
        "Failure / Recovery",
        "Stress / Scale",
        "Real Infrastructure",
        "Live / E2E",
        "Security / Isolation",
    ]
    out = [
        f"# Strict gate `{summary['run_name']}` on `{summary['commit']}`",
        "",
        "Generated by `scripts/strict_gate.py`. Every figure below is copied by the",
        "script from the logs listed in `RAW_LOG_SHA256SUMS.txt`. Gates 1-9 belong to",
        "the owners of the change and stay NOT RUN until they add evidence.",
        "",
        "| Gate | Status | Evidence |",
        "|---|---|---|",
    ]
    for number, name in enumerate(gates, 1):
        out.append(f"| {number}. {name} | NOT RUN | owner to supply |")
    out.append(
        f"| 10. Final Production Gate | {status} | {detail} | <!-- gate10-row -->"
    )
    out += ["", "## Completion answers (owner to supply)", ""]
    for question in [
        "What changed",
        "Why it was necessary",
        "What was tested",
        "Which of the 10 gates passed",
        "Which gates remain incomplete",
        "What risks remain",
        "What exact commit contains the verified implementation",
        "Whether the verified commit is the same commit intended for release",
    ]:
        out.append(f"1. **{question}:** NOT SUPPLIED")
    out += [
        "",
        "## Gate 10 record",
        "",
        "| | |",
        "|---|---|",
        f"| Verdict | **{summary.get('verdict')}** |",
        f"| Commit | `{summary['commit']}` |",
        f"| Runs requested / parallel | {summary['runs_requested']} / {summary['parallel']} |",
        f"| Started / finished | {summary['started']} / {summary.get('finished')} |",
        f"| `{summary.get('base_ref')}` at start / end | `{summary.get('base_sha_at_start')}` / "
        f"`{summary.get('base_sha_at_end')}` |",
        f"| Pre-launch guard | `{json.dumps(summary.get('prelaunch'))}` |",
        f"| Sleep inhibitor confirmed | {summary.get('sleep_inhibitor_confirmed')} |",
        f"| Suspend events (journalctl) | {summary.get('suspend_events')} |",
        f"| Infrastructure | `{json.dumps(summary.get('infrastructure'))}` |",
    ]
    for reason in summary.get("verdict_reasons", []):
        out.append(f"| Reason | {reason} |")
    prelaunch_record = summary.get("prelaunch") or {}
    out += [
        "",
        "### Pre-launch slot accounting (every `manage.py test` process evaluated)",
        "",
        "| PID | Parent | Working directory | Slots | Why | Command |",
        "|---|---|---|---|---|---|",
    ]
    for row in prelaunch_record.get("test_processes", []):
        command = row["argv"].replace("|", "\\|")
        out.append(
            f"| {row['pid']} | {row['ppid']} | `{row['cwd']}` | {row['slots']} | "
            f"{row['why']} | `{command}` |"
        )
    for run in summary.get("runs", []):
        out += [
            "",
            f"### Run {run['index']}: test DB `{run['test_db']}`",
            "",
            "| Check | Result |",
            "|---|---|",
            f"| Fingerprint before | `{json.dumps(run.get('fingerprint_before'))}` |",
            f"| Fingerprint after | `{json.dumps(run.get('fingerprint_after'))}` |",
            f"| Postgres before | `{json.dumps(run.get('pg_before'))}` |",
        ]
        for step in run["steps"]:
            out.append(
                f"| `{step['name']}` | exit {step['exit']}; `{step['log']}` "
                f"{step['lines']} lines, sha256 `{step['sha256']}` |"
            )
        suite = run.get("suite") or {}
        out += [
            f"| Suite | Ran {suite.get('ran')} in {suite.get('seconds')}s, "
            f"{suite.get('result')} {suite.get('counts')} |",
            f"| Failures | {suite.get('failures')} |",
            f"| 'other sessions' / 'Destroying' lines | {suite.get('other_sessions_lines')} / "
            f"{suite.get('destroying_lines')} |",
            f"| Per-test parse matches summary | {suite.get('per_test_consistent')} |",
            f"| Postgres after | `{json.dumps(run.get('pg_after'))}` |",
            f"| Known flake rerun alone | `{json.dumps(run.get('flake_rerun'))}` |",
            f"| Run verdict | {'CLEAN' if not run.get('reasons') else run.get('reasons')} |",
        ]
    out += [
        "",
        "## Not covered by this script",
        "",
        "Gate 10 also lists live/E2E verification, final mutation verification,",
        "worker/task cleanup and Redis cleanup. The script does not do these; their",
        "owners record them separately. `verify-landed` records H10.3 after landing.",
        "",
    ]
    landing = summary.get("landing")
    if landing:
        out += ["## Landing check (H10.3)", "", f"`{json.dumps(landing)}`", ""]
    return "\n".join(out)


def run_gate(args):
    if os.environ.get(INHIBIT_ENV) != "1":
        env = dict(os.environ, **{INHIBIT_ENV: "1"})
        argv = [
            "systemd-inhibit",
            f"--what={INHIBIT_WHAT}",
            f"--who={INHIBIT_WHO}",
            f"--why=strict gate {args.run_name} on {args.commit}",
            sys.executable,
            os.path.abspath(__file__),
            *sys.argv[1:],
        ]
        os.execvpe("systemd-inhibit", argv, env)
    gate = Gate(args)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        gate.prepare()
        gate.guard_targets("start")
        gate.infra()
        for index in range(1, args.runs + 1):
            if not gate.one_run(index):
                break
        verdict, reasons = gate_verdict(gate.summary["runs"], args.runs, args.parallel)
        gate.finish(verdict, reasons)
    except GateRefused as exc:
        print(f"GATE REFUSED: {exc}", file=sys.stderr)
        return 2
    except (GateAbort, KeyboardInterrupt) as exc:
        gate.finish("ABORTED", [str(exc) or type(exc).__name__])
        return 2
    return {"PASS": 0, "FAIL": 1, "PILOT_CLEAN": 3}.get(gate.summary["verdict"], 2)


def verify_landed(args):
    evidence = Path(args.evidence_dir)
    summary = json.loads((evidence / "summary.json").read_text())
    ref_sha = git(Path.cwd(), "rev-parse", "--verify", f"{args.ref}^{{commit}}")
    summary["landing"] = {
        "ref": args.ref,
        "ref_sha": ref_sha,
        "gated_sha": summary["commit"],
        "match": ref_sha == summary["commit"],
        "checked": now(),
    }
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    stub = (evidence / "EVIDENCE.md").read_text()
    status, detail = gate10_row(summary)
    row = f"| 10. Final Production Gate | {status} | {detail} | <!-- gate10-row -->"
    stub, replaced = re.subn(
        r"^\| 10\. Final Production Gate \|.*<!-- gate10-row -->$",
        lambda _: row,
        stub,
        flags=re.M,
    )
    if replaced != 1:
        raise GateAbort("EVIDENCE.md has no single Gate 10 row to update")
    stub = stub.split("\n## Landing check (H10.3)")[0].rstrip("\n") + "\n\n"
    stub += f"## Landing check (H10.3)\n\n`{json.dumps(summary['landing'])}`\n"
    (evidence / "EVIDENCE.md").write_text(stub)
    print(json.dumps(summary["landing"]))
    return 0 if summary["landing"]["match"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="gate one commit")
    run.add_argument("commit")
    run.add_argument("run_name")
    run.add_argument(
        "--runs",
        type=int,
        default=2,
        help="consecutive clean full runs required (H10.2; default 2)",
    )
    run.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="anything but 1 is a pilot, never a gate pass",
    )
    run.add_argument("--base-ref", default="beta")
    run.add_argument("--worktree-dir")
    run.add_argument("--log-dir")
    run.add_argument("--evidence-dir")
    run.add_argument("--keep-worktree", action="store_true")
    run.add_argument(
        "--test-arg",
        action="append",
        default=[],
        help="extra manage.py test option, e.g. --test-arg=--timing; "
        "anything reusing the DB or narrowing the suite is refused",
    )
    run.add_argument("--max-heavy", type=int, default=6)
    run.add_argument("--min-disk-gb", type=float, default=3)
    run.add_argument("--min-ram-gb", type=float, default=2)
    landed = sub.add_parser("verify-landed", help="H10.3: ref must equal the gated SHA")
    landed.add_argument("evidence_dir")
    landed.add_argument("--ref", default="beta")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            if args.runs < 1 or args.parallel < 1:
                raise GateAbort("--runs and --parallel must be at least 1")
            # The one --keepdb / partial-suite guard. It runs before anything is
            # created, on exactly the command every run will execute.
            validate_suite_command(
                suite_command(sys.executable, args.parallel, args.test_arg)
            )
            return run_gate(args)
        return verify_landed(args)
    except GateAbort as exc:
        print(f"GATE ABORTED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a crash must never read as PASS (0) or FAIL (1)
        import traceback

        traceback.print_exc()
        print("GATE ABORTED: internal error", file=sys.stderr)
        sys.exit(2)
