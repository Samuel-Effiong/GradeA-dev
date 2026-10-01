"""Pure-ast harness: exec the H-73 scanner + RawRedisClientTests without Django."""

import ast
import sys
import tempfile
import unittest
from pathlib import Path

guard, root = sys.argv[1], Path(sys.argv[2])
src = open(guard).read()
tree = ast.parse(src)
keep = {
    "RAW_CLIENT_SOURCES",
    "RAW_WRITE_METHODS",
    "RAW_READ_METHODS",
    "RAW_PLUMBING_METHODS",
    "RAW_CLIENT_USERS",
    "_FUNCTION_NODES",
    "_callee",
    "_dotted",
    "_module_name",
    "_RawClientScan",
    "raw_client_factories",
    "scan_raw_client",
    "_imported_factories",
    "raw_client_uses",
    "F1_EXTRA_WRITES",
    "RawRedisClientTests",
    "_enclosing_functions",
    "production_python_files",
}
body = []
for n in tree.body:
    name = getattr(n, "name", None) or next(
        (t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)), None
    )
    if name in keep:
        body.append(n)
ns = {
    "ast": ast,
    "tempfile": tempfile,
    "Path": Path,
    "REPO": root,
    "SimpleTestCase": unittest.TestCase,
}
exec(compile(ast.Module(body=body, type_ignores=[]), guard, "exec"), ns)
print("live counts:", ns["raw_client_uses"]())
print("table      :", {k: v[:2] for k, v in ns["RAW_CLIENT_USERS"].items()})
suite = unittest.defaultTestLoader.loadTestsFromTestCase(ns["RawRedisClientTests"])
r = unittest.TextTestRunner(verbosity=1, stream=sys.stdout).run(suite)
sys.exit(0 if r.wasSuccessful() else 1)
