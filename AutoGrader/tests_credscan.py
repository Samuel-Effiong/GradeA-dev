"""
H-137: the credential pattern check (scripts/credscan.py).

The tool is a checker, so the worst thing it can do is skip something in
silence. These tests plant a pattern and assert it is found, or that what
was not read is said.

Nothing in this file is an address with a password part or a NAME=value
secret as written: every planted string is put together from parts when the
tests run, so the tool, the secrets hook and a reader all see harmless
pieces. No database is used.
"""

import bz2
import collections
import gzip
import importlib.util
import io
import lzma
import os
import tarfile
import zipfile

from django.conf import settings
from django.test import SimpleTestCase

TOOL = os.path.join(settings.BASE_DIR, "scripts", "credscan.py")

#: A made-up 10-character value.
WORD = "q7" + "Zk" + "3vP" + "x9T"
PLANTED_URL = (
    "scheme" + ":" + "//" + "someone" + ":" + WORD + "@" + "host.invalid/db"
).encode()
PLANTED_ASSIGN = ("API_" + "TOKEN" + " = " + '"' + WORD + '"').encode()
CLEAN = b"nothing to see\nRan 3 tests in 0.001s\n\nOK\n"
BODY = CLEAN + PLANTED_URL + b"\n" + PLANTED_ASSIGN + b"\n"


def fresh():
    """Load the tool anew, so its module-level hit list starts empty."""
    spec = importlib.util.spec_from_file_location("credscan_under_test", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tar_of(name, body, mode):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as t:
        info = tarfile.TarInfo(name)
        info.size = len(body)
        t.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def zip_of(name, body):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, body)
    return buf.getvalue()


FORMS = {
    "run.log": BODY,
    "run.log.gz": gzip.compress(BODY),
    "run.log.xz": lzma.compress(BODY),
    "run.log.bz2": bz2.compress(BODY),
    "logs.tar": tar_of("inner/run.log", BODY, "w"),
    "logs.tar.gz": tar_of("inner/run.log", BODY, "w:gz"),
    "logs.tar.xz": tar_of("inner/run.log", BODY, "w:xz"),
    "logs.tar.bz2": tar_of("inner/run.log", BODY, "w:bz2"),
    "logs.zip": zip_of("inner/run.log", BODY),
    "nested.tar.gz": tar_of("inner/run.log.xz", lzma.compress(BODY), "w:gz"),
}


class ScanCase(SimpleTestCase):
    """Helpers: scan some bytes under a non-test path and count what was found."""

    PATH = "docs/evidence/x/"

    def scan(self, name, data):
        tool = fresh()
        rows, values = collections.Counter(), collections.defaultdict(set)
        tool.scan(self.PATH + name, data, rows, values)
        return tool, rows, values

    def counts(self, name, data):
        """(address hits, literal assignment hits, archive rows)."""
        _tool, rows, _values = self.scan(name, data)
        url = sum(c for (kind, *_), c in rows.items() if kind == "url")
        literal = sum(
            c
            for (kind, _n, shape, _l), c in rows.items()
            if kind == "assign" and shape == "LITERAL"
        )
        archive = sum(c for (kind, *_), c in rows.items() if kind == "archive")
        return url, literal, archive


class ArchiveFormsTests(ScanCase):
    """Every archive form the tool claims to open is opened."""

    def test_the_planted_patterns_are_found_in_every_form(self):
        for name, data in FORMS.items():
            with self.subTest(form=name):
                self.assertEqual(self.counts(name, data), (1, 1, 0))

    def test_a_clean_body_gives_nothing_in_every_form(self):
        for name, data in {
            "run.log.xz": lzma.compress(CLEAN),
            "run.log.gz": gzip.compress(CLEAN),
        }.items():
            with self.subTest(form=name):
                self.assertEqual(self.counts(name, data), (0, 0, 0))

    def test_a_broken_xz_is_reported_not_skipped(self):
        _url, _literal, archive = self.counts(
            "run.log.xz", b"\xfd7zXZ\x00 this is not a whole xz stream"
        )
        self.assertEqual(archive, 1)


class MaskingTests(ScanCase):
    """No value leaves the tool."""

    def test_the_value_is_never_in_the_rows_or_the_hit_list(self):
        tool, rows, _values = self.scan("run.log.xz", FORMS["run.log.xz"])
        self.assertNotIn(WORD, repr(dict(rows)))
        self.assertNotIn(WORD, repr(tool.HITS))

    def test_the_value_is_never_in_the_report(self):
        tool, rows, values = self.scan("run.log", BODY)
        out = io.StringIO()
        tool.report("abc123", 1, rows, values, True, out)
        tool.report_lines(out)
        text = out.getvalue()
        self.assertIn("docs/evidence/x/run.log", text)
        self.assertIn("LITERAL", text)
        self.assertNotIn(WORD, text)

    def test_the_hit_list_names_the_file_and_the_line(self):
        tool, _rows, _values = self.scan("run.log", BODY)
        where = sorted((name, line) for name, line, *_ in tool.HITS)
        self.assertEqual(
            where, [("docs/evidence/x/run.log", 5), ("docs/evidence/x/run.log", 6)]
        )


class FileClassTests(ScanCase):
    """Test files are exempt for literal assignments, never for addresses."""

    def test_a_literal_assignment_in_a_test_file_is_counted_but_not_listed(self):
        tool = fresh()
        rows, values = collections.Counter(), collections.defaultdict(set)
        tool.scan("billing/tests/test_x.py", PLANTED_ASSIGN + b"\n", rows, values)
        self.assertEqual(sum(rows.values()), 1)
        self.assertEqual(tool.HITS, [])
        out = io.StringIO()
        tool.report("abc123", 1, rows, values, False, out)
        self.assertNotIn("billing/tests/test_x.py", out.getvalue())
        self.assertIn(
            "# not listed: 1 literal assignment lines in test files", out.getvalue()
        )

    def test_an_address_in_a_test_file_is_listed(self):
        tool = fresh()
        rows, values = collections.Counter(), collections.defaultdict(set)
        tool.scan("billing/tests/test_x.py", PLANTED_URL + b"\n", rows, values)
        out = io.StringIO()
        tool.report("abc123", 1, rows, values, False, out)
        self.assertIn("billing/tests/test_x.py", out.getvalue())

    def test_one_value_written_two_ways_is_reported_as_a_group(self):
        odd = "p%" + "40" + "ss" + "Zk3" + "vPx9"
        plain = "p" + "@" + "ss" + "Zk3" + "vPx9"
        tool = fresh()
        rows, values = collections.Counter(), collections.defaultdict(set)
        tool.scan(
            "a.env", ("DB_" + "PASSWORD" + "=" + plain + "\n").encode(), rows, values
        )
        tool.scan(
            "b.md",
            ("scheme" + ":" + "//" + "u" + ":" + odd + "@" + "h.invalid/x\n").encode(),
            rows,
            values,
        )
        out = io.StringIO()
        tool.report("abc123", 2, rows, values, False, out)
        self.assertIn("across lines: 1 group(s)", out.getvalue())
        self.assertIn("a.env  <->  b.md", out.getvalue())


def nested_tar(levels):
    """BODY as run.log inside `levels` tar files, one in the other."""
    data, name = BODY, "run.log"
    for level in range(levels, 0, -1):
        data = tar_of(name, data, "w")
        name = f"level{level}.tar"
    return data


class LongLineTests(ScanCase):
    """A line over 4000 characters is scanned, and said to be long.

    Run logs are stored compressed and hold very long lines, so this was the
    likeliest real miss (v2's note on H-136).
    """

    PAD = b"lorem ipsum " * 400  # 4800 characters, no pattern in it

    def test_a_pattern_far_along_a_long_line_is_found(self):
        line = self.PAD + PLANTED_URL + b" and " + PLANTED_ASSIGN + b"\n"
        self.assertEqual(self.counts("run.log", line), (1, 1, 0))

    def test_a_pattern_at_the_start_of_a_long_line_is_found(self):
        line = PLANTED_ASSIGN + b" then " + PLANTED_URL + b" " + self.PAD + b"\n"
        self.assertEqual(self.counts("run.log", line), (1, 1, 0))

    def test_a_pattern_after_one_long_unbroken_run_is_found(self):
        line = b"A" * 50000 + b" " + PLANTED_ASSIGN + b" " + b"B" * 50000 + b"\n"
        self.assertEqual(self.counts("run.log", line), (0, 1, 0))

    def test_one_hit_is_counted_once_however_many_words_surround_it(self):
        # The name holds two of the words the scan looks for, and more stand near it.
        name = "SECRET_" + "KEY"
        line = (
            self.PAD
            + b"key token " * 5
            + (name + ' = "' + WORD + '"').encode()
            + b" pass key"
            + b"\n"
        )
        self.assertEqual(self.counts("run.log", line), (0, 1, 0))

    def test_the_hit_on_a_long_line_carries_its_line_number(self):
        tool, _rows, _values = self.scan(
            "run.log", CLEAN + self.PAD + PLANTED_ASSIGN + b"\n"
        )
        self.assertEqual(
            [(name, line) for name, line, *_ in tool.HITS],
            [("docs/evidence/x/run.log", 5)],
        )

    def test_a_long_line_is_said_to_be_long_in_the_report(self):
        tool, rows, values = self.scan(
            "run.log", self.PAD + b"\n" + self.PAD + b"\nshort\n"
        )
        self.assertEqual(
            sum(c for (kind, *_), c in rows.items() if kind == "longline"), 2
        )
        out = io.StringIO()
        tool.report("abc123", 1, rows, values, False, out)
        self.assertIn("longline |     2 |", out.getvalue())
        self.assertIn("docs/evidence/x/run.log", out.getvalue())

    def test_a_line_of_exactly_the_limit_is_not_called_long(self):
        _tool, rows, _values = self.scan("run.log", b"z" * 4000 + b"\n")
        self.assertEqual(
            sum(c for (kind, *_), c in rows.items() if kind == "longline"), 0
        )


class NestingTests(ScanCase):
    """An archive nested deeper than the tool opens is said not to be opened."""

    def test_three_archives_deep_is_opened(self):
        self.assertEqual(self.counts("logs.tar", nested_tar(3)), (1, 1, 0))

    def test_a_fourth_archive_is_reported_as_not_opened(self):
        _tool, rows, _values = self.scan("logs.tar", nested_tar(4))
        not_opened = [
            (name, shape) for (kind, name, shape, _l) in rows if kind == "archive"
        ]
        self.assertEqual(
            not_opened,
            [
                (
                    "docs/evidence/x/logs.tar!level2.tar!level3.tar!level4.tar",
                    "NOT-OPENED:nested-too-deep",
                )
            ],
        )

    def test_the_archive_not_opened_is_listed_in_the_default_report(self):
        tool, rows, values = self.scan("logs.tar", nested_tar(4))
        out = io.StringIO()
        tool.report("abc123", 1, rows, values, False, out)
        self.assertIn(
            "NOT-OPENED:nested-too-deep | docs/evidence/x/logs.tar!level2.tar!level3.tar!level4.tar",
            out.getvalue(),
        )


class BinaryFileTests(ScanCase):
    """A file passed over because it looks binary is counted, not passed over in silence."""

    def test_a_file_with_an_early_nul_byte_is_counted_as_not_read(self):
        tool, rows, values = self.scan("blob.bin", b"\0\0\0" + BODY)
        self.assertEqual(self.counts("blob.bin", b"\0\0\0" + BODY)[:2], (0, 0))
        self.assertEqual(
            [name for (kind, name, _s, _l) in rows if kind == "binary"],
            ["docs/evidence/x/blob.bin"],
        )
        out = io.StringIO()
        tool.report("abc123", 1, rows, values, False, out)
        self.assertIn(
            "# not read: 1 file(s) with a NUL byte in the first 4096 bytes",
            out.getvalue(),
        )
        self.assertNotIn("blob.bin", out.getvalue())

    def test_the_files_not_read_are_named_when_every_row_is_asked_for(self):
        tool, rows, values = self.scan("blob.bin", b"\0\0\0" + BODY)
        out = io.StringIO()
        tool.report("abc123", 1, rows, values, True, out)
        self.assertIn("NOT-READ | docs/evidence/x/blob.bin", out.getvalue())

    def test_a_nul_byte_after_the_first_4096_bytes_does_not_stop_the_scan(self):
        self.assertEqual(self.counts("run.log", BODY + b"y" * 4096 + b"\0"), (1, 1, 0))

    def test_a_report_with_nothing_unread_says_zero(self):
        tool, rows, values = self.scan("run.log", BODY)
        out = io.StringIO()
        tool.report("abc123", 1, rows, values, False, out)
        self.assertIn(
            "# not read: 0 file(s) with a NUL byte in the first 4096 bytes",
            out.getvalue(),
        )
