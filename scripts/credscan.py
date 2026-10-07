#!/usr/bin/env python3
"""Whole-tree scan of one git revision for password-shaped values.

Usage: python scripts/credscan.py <repo> <rev> [--all] [--lines]

It reads the tree of <rev> through `git archive` (not the working files, and
not the history) and finds two forms:

1. an address with a password part: a scheme, then a user name, a colon, the
   password, an at sign and the host;
2. an assignment NAME=value or NAME: value where NAME contains PASS, PWD,
   SECRET, TOKEN or KEY (`export` and quotes optional).

It also compares every literal value found with the percent-decoded and
percent-encoded forms of every other, to catch one secret written two ways.

The output is MASKED: kind, line count, value length, shape class, file. No
value is ever printed or written. By default it lists literal hits outside
test files and counts the rest; `--all` lists every row; `--lines` adds a
value-free hit list (file, line, name).

Exempt by file class and name, never by value (SM ruling 2026-10-05):
literal assignments in TEST files (a path with /tests/, /tests_, /test_,
/testing/, conftest, or a file named tests.py) are counted, not listed; the
repository's standard fixture password and named stand-in keys live there.
Address-form hits are listed everywhere, tests too.

It opens .gz, .xz, .bz2, .tar (also .tar.gz, .tgz, .tar.xz, .txz, .tar.bz2,
.tbz2) and .zip, nested up to three deep.
"""

import bz2
import collections
import gzip
import io
import lzma
import re
import subprocess
import sys
import tarfile
import urllib.parse
import zipfile

URL = re.compile(rb"://[^/\s:@'\"]*:([^/\s@'\"]+)@")
ASSIGN = re.compile(
    rb"(?i)(?<![A-Za-z0-9_])([A-Za-z0-9_]*(?:PASS|PWD|SECRET|TOKEN|KEY)[A-Za-z0-9_]*)"
    rb"[ \t]*(?:=|:)[ \t]*(?:(['\"])([^'\"\n]{1,200})\2|([^\s'\"#,;)]{1,200}))"
)
STRONG_KEY = re.compile(rb"(API|ACCESS|PRIVATE|SIGNING|ENCRYPTION|AUTH|SECRET)_?KEY")
PLACE = {
    "postgres",
    "password",
    "pass",
    "pw",
    "secret",
    "changeme",
    "test",
    "admin",
    "user",
    "example",
    "xxx",
    "redacted",
    "your_password",
    "yourpassword",
    "pwd",
    "guest",
    "root",
    "dummy",
    "fake",
    "passwd",
    "token",
    "none",
    "null",
    "true",
    "false",
    "str",
    "string",
    "bool",
    "int",
    "required",
    "optional",
    "todo",
    "...",
    "key",
    "value",
    "masked",
}
CODE = (
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".json",
    ".lock",
    ".toml",
    ".cfg",
    ".ini",
    ".po",
    ".svg",
)
TAR_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".tar.xz", ".txz", ".tar.bz2", ".tbz2")
#: How many archives deep the scan opens.
MAX_DEPTH = 3
#: A line longer than this is not scanned.
LONG_LINE = 4000
#: A file with a NUL byte this early is taken for binary and not read.
BINARY_PROBE = 4096

#: The value-free hit list: (file, line, name, value length, shape).
HITS: list = []


def shape(v: bytes, quoted: bool, name: str) -> str:
    """Class a value by its form, without keeping it."""
    s = v.decode("utf-8", "replace")
    if (
        not s
        or s.startswith(("$", "{", "<", "%", "[", "("))
        or "${" in s
        or "{{" in s
        or set(s) <= set("*xX.-_")
    ):
        return "variable-or-mask"
    if s.lower().strip("<>[]{}") in PLACE:
        return "placeholder-word"
    if not quoted and name.lower().endswith(CODE):
        # An unquoted right-hand side in source code is a name or a call.
        return "code-expression"
    if not quoted and (
        re.search(r"[().\[\]]", s)
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s)
        and len(s) < 6
    ):
        return "code-expression"
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", s):
        return "UPPERCASE-NAME"
    return "LITERAL"


def is_test(name: str) -> bool:
    """Whether a path is a test file, by its name alone."""
    n = name.lower()
    base = n.split("!")[0].rsplit("/", 1)[-1]
    return (
        "/tests/" in n
        or "/tests_" in n
        or "/test_" in n
        or n.startswith(("tests/", "tests_", "test_"))
        or "conftest" in n
        or "/testing/" in n
        or base in ("tests.py", "test.py")
    )


def open_archive(name, data, rows, values, depth):
    """Scan the members of an archive; True if `data` was one and was opened."""
    low = name.lower()
    if depth >= MAX_DEPTH:
        return False
    try:
        if low.endswith(TAR_SUFFIXES):
            with tarfile.open(fileobj=io.BytesIO(data)) as t:
                for m in t.getmembers():
                    f = t.extractfile(m) if m.isfile() else None
                    if f:
                        scan(f"{name}!{m.name}", f.read(), rows, values, depth + 1)
            return True
        if low.endswith(".gz"):
            scan(name + "!(gunzipped)", gzip.decompress(data), rows, values, depth + 1)
            return True
        if low.endswith(".xz"):
            scan(name + "!(unxz)", lzma.decompress(data), rows, values, depth + 1)
            return True
        if low.endswith(".bz2"):
            scan(name + "!(bunzipped)", bz2.decompress(data), rows, values, depth + 1)
            return True
        if low.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for n in z.namelist():
                    scan(f"{name}!{n}", z.read(n), rows, values, depth + 1)
            return True
    except (
        Exception
    ) as exc:  # noqa: B902 - any unreadable archive is reported, then read as text
        rows[("archive", name, "UNREADABLE:" + type(exc).__name__, 0)] += 1
    return False


def scan_line(name, lineno, line, rows, values):
    """Record every hit on one line."""
    for m in URL.finditer(line):
        sh = shape(m.group(1), True, name)
        rows[("url", name, sh, len(m.group(1)))] += 1
        HITS.append((name, lineno, "(url password position)", len(m.group(1)), sh))
        if sh == "LITERAL":
            values[m.group(1)].add(name)
    for m in ASSIGN.finditer(line):
        v, quoted = (
            (m.group(3), True) if m.group(3) is not None else (m.group(4), False)
        )
        sh = shape(v, quoted, name)
        nm = m.group(1).upper()
        strong = any(w in nm for w in (b"PASS", b"PWD", b"SECRET", b"TOKEN")) or bool(
            STRONG_KEY.search(nm)
        )
        if not strong:
            rows[("assign", "(names with KEY only, e.g. cache keys)", sh, 0)] += 1
            continue
        rows[("assign", name, sh, len(v))] += 1
        if sh == "LITERAL" and not is_test(name):
            HITS.append(
                (name, lineno, m.group(1).decode("ascii", "replace"), len(v), sh)
            )
            values[v].add(name)


def scan(name, data, rows, values, depth=0):
    """Scan one file's bytes, opening it first if it is an archive."""
    if open_archive(name, data, rows, values, depth):
        return
    if b"\0" in data[:BINARY_PROBE]:
        return
    for lineno, line in enumerate(data.split(b"\n"), 1):
        if len(line) > LONG_LINE:
            continue
        scan_line(name, lineno, line, rows, values)


def report(rev, n, rows, values, show_all, out):
    """Write the masked table. No value is written."""
    out.write(f"# {rev}: {n} files scanned (archives opened); every value masked\n")
    out.write("kind | lines | length | shape | file\n")
    for (kind, name, sh, ln), c in sorted(
        rows.items(), key=lambda kv: (kv[0][0] != "url", kv[0][1])
    ):
        if (
            show_all
            or kind in ("url", "archive")
            or (sh == "LITERAL" and not is_test(name))
        ):
            out.write(f"{kind:6s} | {c:5d} | {ln:6d} | {sh} | {name}\n")
    t_lit = sum(
        c
        for (k, nme, sh, _), c in rows.items()
        if k == "assign" and sh == "LITERAL" and is_test(nme)
    )
    other = sum(
        c for (k, _, sh, _), c in rows.items() if k == "assign" and sh != "LITERAL"
    )
    out.write(
        f"# not listed: {t_lit} literal assignment lines in test files; "
        f"{other} assignment lines of non-literal shape\n"
    )
    # One secret in two forms: compare the decoded and encoded forms of the literal values.
    forms = collections.defaultdict(set)
    for v, names in values.items():
        for f in {
            v,
            urllib.parse.unquote_to_bytes(v),
            urllib.parse.quote_from_bytes(v, safe="").encode(),
        }:
            forms[f] |= {(v, nme) for nme in names}
    pairs = set()
    for vs in forms.values():
        if len({v for v, _ in vs}) > 1:
            pairs.add(tuple(sorted({nme for _, nme in vs})))
    out.write(
        f"# same value in encoded and decoded form across lines: {len(pairs)} group(s)\n"
    )
    for g in sorted(pairs):
        out.write("#   " + "  <->  ".join(g) + "\n")


def report_lines(out):
    """Write the hit list: where, never what."""
    out.write(
        "# hit list: file | line | name | value length | shape   (no value is written)\n"
    )
    for name, lineno, nm, ln, sh in sorted(HITS):
        out.write(f"{name} | {lineno} | {nm} | {ln} | {sh}\n")


def main(argv, out):
    """Scan <repo> at <rev> and write the report to `out`."""
    repo, rev = argv[0], argv[1]
    rows, values = collections.Counter(), collections.defaultdict(set)
    arch = subprocess.run(  # nosec B603 B607 - git, with a fixed argument list
        ["git", "-C", repo, "archive", "--format=tar", rev],
        capture_output=True,
        check=True,
    ).stdout
    n = 0
    with tarfile.open(fileobj=io.BytesIO(arch)) as t:
        for m in t.getmembers():
            f = t.extractfile(m) if m.isfile() else None
            if f:
                n += 1
                scan(m.name, f.read(), rows, values)
    report(rev, n, rows, values, "--all" in argv, out)
    if "--lines" in argv:
        report_lines(out)


if __name__ == "__main__":
    main(sys.argv[1:], sys.stdout)
