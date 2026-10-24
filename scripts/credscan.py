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

What it does not read, it says (H-137):

* An archive inside three others is not opened. It is listed as
  NOT-OPENED:nested-too-deep, in every form of the report.
* A file with a NUL byte in its first 4096 bytes is taken for binary and not
  read. The report counts such files; `--all` names them.
* A line longer than 4000 characters is scanned, but not by running the two
  patterns over the whole of it, which can take minutes on one unbroken run
  of letters. Instead each "://" is tried from its own place, reading 600
  characters on, and each of the five words is followed to the start and
  end of the name it stands in (up to 200 characters each way), where the
  assignment pattern is tried once, reading 420 characters past the name's
  end. So on such a line these are NOT seen, and nothing says so beyond the
  `longline` row: a password part longer than about 590 characters; a QUOTED
  value that ends more than 420 characters after its name (spaces, the sign
  and quotes included); and a name with more than 200 characters before or
  after its word. A BARE value that begins inside those 420 characters and
  goes on past them is not recorded as a value: it is listed as
  VALUE-CUT-AT-WINDOW with the length that was read, in every form of the
  report and in the hit list (found by Verifier 2). An assignment standing inside another one's value is
  found there by itself, where a short line would give the outer one only.
  Every such line is counted in a `longline` row, in every form of the
  report.

Limits it does NOT report:

* Text after the end of a compressed stream is not read.
* Archive types it does not claim are not opened: .lzma, .7z, .zst, and a
  zip file under another suffix (.jar, .whl, .docx, .xlsx).
* A compressed file inside a compressed file with no name of its own
  (x.gz.gz) is opened once: the inner stream has no suffix to go by.
* It reads the tree at one revision, not the history.
"""

import bz2
import collections
import gzip
import io
import lzma
import re
import string
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
#: A line longer than this is scanned around its "://" and its words only.
LONG_LINE = 4000
#: On a long line: how far to each side of a word its name is followed, how
#: far after the name's end the value may end, and how far after "://" the at
#: sign may stand.
NAME_BEFORE = 200
VALUE_AFTER = 420
NAME_BYTES = frozenset((string.ascii_letters + string.digits + "_").encode())
ADDRESS_AFTER = 600
#: What ends a bare value in the assignment pattern.
VALUE_ENDS = frozenset(b" \t\r\n\x0b\x0c'\"#,;)")
WORD = re.compile(rb"(?i)PASS|PWD|SECRET|TOKEN|KEY")
ARCHIVE_SUFFIXES = TAR_SUFFIXES + (".gz", ".xz", ".bz2", ".zip")
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
        if low.endswith(ARCHIVE_SUFFIXES):
            rows[("archive", name, "NOT-OPENED:nested-too-deep", 0)] += 1
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


def record_address(name, lineno, value, rows, values):
    """Record one address with a password part."""
    sh = shape(value, True, name)
    rows[("url", name, sh, len(value))] += 1
    HITS.append((name, lineno, "(url password position)", len(value), sh))
    if sh == "LITERAL":
        values[value].add(name)


def record_assignment(name, lineno, m, rows, values):
    """Record one NAME=value match."""
    v, quoted = (m.group(3), True) if m.group(3) is not None else (m.group(4), False)
    sh = shape(v, quoted, name)
    nm = m.group(1).upper()
    strong = any(w in nm for w in (b"PASS", b"PWD", b"SECRET", b"TOKEN")) or bool(
        STRONG_KEY.search(nm)
    )
    if not strong:
        rows[("assign", "(names with KEY only, e.g. cache keys)", sh, 0)] += 1
        return
    rows[("assign", name, sh, len(v))] += 1
    if sh == "LITERAL" and not is_test(name):
        HITS.append((name, lineno, m.group(1).decode("ascii", "replace"), len(v), sh))
        values[v].add(name)


def scan_line(name, lineno, line, rows, values):
    """Record every hit on one line."""
    for m in URL.finditer(line):
        record_address(name, lineno, m.group(1), rows, values)
    for m in ASSIGN.finditer(line):
        record_assignment(name, lineno, m, rows, values)


def scan_long_line(name, lineno, line, rows, values):
    """Record the hits on a line too long to run the patterns over whole.

    Each "://" is tried once, from its own place, so an address cannot be
    found twice.

    Each of the five words is part of a name. The name is followed back to
    its start and the assignment pattern is tried there, once, reading no
    further than VALUE_AFTER past the name's end. Two words can stand in one
    name, so a word inside a name already tried is passed over. The pattern
    is never run over a stretch that belongs to another word: such a stretch
    can end inside a value, and the cut-off value would be recorded in place
    of the whole one (found by Verifier 2, 2026-10-07).
    """
    rows[("longline", name, "scanned-around-its-words", 0)] += 1
    at = line.find(b"://")
    while at != -1:
        m = URL.match(line, at, at + ADDRESS_AFTER)
        if m:
            record_address(name, lineno, m.group(1), rows, values)
        at = line.find(b"://", at + 1)
    tried_to = 0
    for word in WORD.finditer(line):
        if word.start() < tried_to:
            continue
        start = word.start()
        while (
            start > 0
            and word.start() - start < NAME_BEFORE
            and line[start - 1] in NAME_BYTES
        ):
            start -= 1
        end = word.end()
        while (
            end < len(line)
            and end - word.end() < NAME_BEFORE
            and line[end] in NAME_BYTES
        ):
            end += 1
        tried_to = end
        stop = end + VALUE_AFTER
        m = ASSIGN.match(line, start, stop)
        if not m:
            continue
        cut = (
            m.group(4) is not None
            and m.end() == stop
            and stop < len(line)
            and line[stop] not in VALUE_ENDS
        )
        if cut:
            # The bare value goes on past what was read: what was seen is
            # not the value, so it is not recorded as one.
            rows[("cut", name, "VALUE-CUT-AT-WINDOW", len(m.group(4)))] += 1
            HITS.append(
                (
                    name,
                    lineno,
                    m.group(1).decode("ascii", "replace"),
                    len(m.group(4)),
                    "VALUE-CUT-AT-WINDOW",
                )
            )
        else:
            record_assignment(name, lineno, m, rows, values)


def scan(name, data, rows, values, depth=0):
    """Scan one file's bytes, opening it first if it is an archive."""
    if open_archive(name, data, rows, values, depth):
        return
    if b"\0" in data[:BINARY_PROBE]:
        rows[("binary", name, "NOT-READ", 0)] += 1
        return
    for lineno, line in enumerate(data.split(b"\n"), 1):
        if len(line) > LONG_LINE:
            scan_long_line(name, lineno, line, rows, values)
        else:
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
            or kind in ("url", "archive", "longline", "cut")
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
    unread = sum(c for (k, *_), c in rows.items() if k == "binary")
    out.write(
        f"# not read: {unread} file(s) with a NUL byte in the first {BINARY_PROBE} bytes\n"
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
