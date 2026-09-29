"""H-1 step 4 guard: no wildcard / pattern cache deletes in production code.

H-1 replaced wildcard invalidation (`delete_pattern("*user*")` and friends,
a keyspace SCAN per pattern that destroyed every tenant's cache and
non-cache keys with it) with per-entity generation counters
(`AutoGrader/cache_generation.py`). Step 4 deleted the wildcards. This test
keeps them deleted: it fails if any non-test module calls a pattern or
keyspace-walking cache operation, other than the plan's one documented
exception.

Static (AST), so it needs no database or Redis and fails in seconds,
before any freshness suite runs. The freshness matrix adds the dynamic
half: `run_matrix` fails any mutation that sends a keyspace SCAN other than
the PDF exact-prefix clear (`AutoGrader/tests_cache_matrix_support.py`).

What counts as a wildcard call:

* `delete_pattern(...)` - django-redis SCAN + DEL by glob;
* `delete_cache_patterns(...)` - the removed project helper, by name;
* `iter_keys(...)`, `scan_iter(...)` - keyspace walks;
* `.keys(<arg>)` and `.scan(<arg>)` with an argument - the redis-py KEYS and
  SCAN commands (a dict's `.keys()` takes none, so it is not matched);
* `execute_command("SCAN" | "KEYS", ...)` - the same commands sent raw.

Allowlist (plan §2, `docs/H1_STAGE3_WILDCARD_REMOVAL_PLAN.md`): exactly one
`delete_pattern` in `assignments/pdf_cache.py`, clearing ONE assignment's
own rendered PDFs by exact prefix. Its key is versioned by `updated_at` and
no other family shares the prefix. The guard also pins its shape: the
pattern must be an f-string whose only glob is the trailing `*`.

Test infrastructure is not production code and is excluded by path, each
entry with its reason in TEST_INFRASTRUCTURE; the guard checks that no
production module imports it.
"""

import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

REPO = Path(settings.BASE_DIR)

WILDCARD_CALLS = {"delete_pattern", "delete_cache_patterns", "iter_keys", "scan_iter"}
WILDCARD_CALLS_WITH_ARGS = {"keys", "scan"}
RAW_COMMANDS = {"SCAN", "KEYS"}

#: (path, call name) -> (allowed count, why). The plan's only exception.
ALLOWED = {
    ("assignments/pdf_cache.py", "delete_pattern"): (
        1,
        "plan §2: exact-prefix clear of ONE assignment's own rendered PDFs "
        "(key versioned by updated_at; prefix shared with no other family)",
    ),
}

#: Non-test-named modules that exist only for the test runner.
TEST_INFRASTRUCTURE = {
    "AutoGrader/redis_test_hygiene.py": (
        "scan_iter over the TEST process's own key prefix (gaplus-t<pid>:*) "
        "to clean up after a test run; loaded only by the test runner"
    ),
    "AutoGrader/redis_test_runner.py": "the test runner that loads it",
}


def _is_test_path(rel):
    name = rel.rsplit("/", 1)[-1]
    return (
        "/tests/" in rel or name == "tests.py" or name.startswith(("test_", "tests_"))
    )


def production_python_files():
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if (
            rel.startswith(("docs/", "static/", "media/", "node_modules/", "."))
            or "/migrations/" in rel
            or "site-packages" in rel
            or _is_test_path(rel)
            or rel in TEST_INFRASTRUCTURE
        ):
            continue
        yield rel, path


def _call_name(call):
    func = call.func
    return getattr(func, "attr", None) or getattr(func, "id", None)


def wildcard_calls(tree):
    """(name, line, call node) for every wildcard-style call in a module."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in WILDCARD_CALLS:
            found.append((name, node.lineno, node))
        elif name in WILDCARD_CALLS_WITH_ARGS and isinstance(node.func, ast.Attribute):
            if node.args or node.keywords:
                found.append((name, node.lineno, node))
        elif name == "execute_command" and node.args:
            first = node.args[0]
            command = str(first.value) if isinstance(first, ast.Constant) else ""
            if command.upper() in RAW_COMMANDS:
                found.append((f"execute_command({command})", node.lineno, node))
    return found


def scan_production():
    """{(path, name): [lines]} for every wildcard call in production code."""
    hits = {}
    for rel, path in production_python_files():
        tree = ast.parse(path.read_text(), filename=rel)
        for name, line, _ in wildcard_calls(tree):
            hits.setdefault((rel, name), []).append(line)
    return hits


class NoWildcardInvalidationTests(SimpleTestCase):
    def test_no_production_code_calls_a_wildcard_cache_operation(self):
        hits = scan_production()
        unexpected = {
            f"{rel}: {name}": lines
            for (rel, name), lines in hits.items()
            if (rel, name) not in ALLOWED
        }
        self.assertEqual(
            unexpected,
            {},
            "wildcard / keyspace-walking cache calls in production code. H-1 "
            "removed wildcard invalidation (a SCAN per pattern, destroying "
            "every tenant's cache): invalidate with a generation bump "
            "(AutoGrader/cache_generation.bump_many) and key the cached read "
            "with versioned_key instead.",
        )

    def test_the_allowlisted_exception_is_exactly_as_documented(self):
        hits = scan_production()
        for (rel, name), (count, why) in ALLOWED.items():
            with self.subTest(rel=rel, name=name):
                self.assertEqual(
                    len(hits.get((rel, name), [])),
                    count,
                    f"{rel} should call {name} exactly {count} time(s): {why}",
                )

    def test_the_pdf_clear_stays_an_exact_prefix(self):
        """The exception is only safe while it is one assignment's prefix:
        an f-string of fixed segments ending in a single trailing `*`."""
        tree = ast.parse((REPO / "assignments/pdf_cache.py").read_text())
        [(_, _, call)] = [
            hit for hit in wildcard_calls(tree) if hit[0] == "delete_pattern"
        ]
        pattern = call.args[0]
        self.assertIsInstance(pattern, ast.JoinedStr)
        literal = "".join(
            str(part.value) if isinstance(part, ast.Constant) else "{}"
            for part in pattern.values
        )
        self.assertEqual(literal, "{}:{}:{}:*")
        names = [
            part.value.id
            for part in pattern.values
            if isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name)
        ]
        self.assertEqual(names, ["CACHE_KEY_PREFIX", "CACHE_VERSION", "assignment_id"])

    def test_test_infrastructure_is_not_imported_by_production(self):
        """The path exclusion above is only honest if production never
        loads those modules."""
        for rel, path in production_python_files():
            tree = ast.parse(path.read_text(), filename=rel)
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module] + [
                        f"{node.module}.{alias.name}" for alias in node.names
                    ]
                elif isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                for module in modules:
                    for infra in TEST_INFRASTRUCTURE:
                        dotted = infra[: -len(".py")].replace("/", ".")
                        with self.subTest(rel=rel, module=module):
                            self.assertNotEqual(module, dotted)

    def test_the_scanner_detects_every_wildcard_form(self):
        """Guard on the guard: each form is found in a synthetic module, and
        look-alikes that are not cache operations are not."""
        source = "\n".join(
            [
                "cache.delete_pattern('*user*')",
                "delete_cache_patterns('courses:*')",
                "cache.iter_keys('*')",
                "client.scan_iter(match='*')",
                "client.keys('*')",
                "client.scan(0, match='*')",
                "client.execute_command('SCAN', 0)",
                "client.execute_command('keys', '*')",
                # not wildcard operations:
                "{}.keys()",
                "client.execute_command('GET', 'k')",
                "cache.delete('exact-key')",
            ]
        )
        found = sorted(name for name, _, _ in wildcard_calls(ast.parse(source)))
        self.assertEqual(
            found,
            sorted(
                [
                    "delete_pattern",
                    "delete_cache_patterns",
                    "iter_keys",
                    "scan_iter",
                    "keys",
                    "scan",
                    "execute_command(SCAN)",
                    "execute_command(keys)",
                ]
            ),
        )
