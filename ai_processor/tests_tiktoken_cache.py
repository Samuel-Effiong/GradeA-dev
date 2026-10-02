"""
Guard test for the vendored tiktoken cache (ai_processor/tiktoken_cache/).

Every real token-counting test (ai_processor.services encoding calls, the
extraction benchmark's chunk-threshold guard in
tests_extraction_benchmark_runner.py) depends on tiktoken.get_encoding
being able to load its BPE data. That data isn't bundled with tiktoken --
without either a live fetch or the vendored copy this repo carries, every
one of those tests fails, and the failure looks like an AI-pipeline bug
rather than what it actually is.

This test proves the vendored copy is what AutoGrader/settings.py's
TIKTOKEN_CACHE_DIR wiring says it is, without touching the network itself:

  * if the vendored file is ABSENT, skip cleanly (not a failure) -- the
    settings wiring still lets tiktoken fetch live if the network is up,
    and this repo's tests should not force a network dependency just to
    prove that fact;
  * if the vendored file is PRESENT but its sha256 doesn't match
    tiktoken's own expected_hash for cl100k_base, FAIL loudly -- a silent
    fallback here would hide a corrupted or swapped-out vendored file
    behind what looks like a passing suite;
  * if it matches, also load it through tiktoken itself and encode a
    trivial string, proving the vendored bytes are not just
    hash-correct but actually usable by the version of tiktoken pinned
    in requirements.

IMPORTANT ORDERING NOTE: tiktoken.load.read_file_cached checks the exact
same hash on every call and SILENTLY deletes-and-refetches a mismatched
file -- it self-heals. That means calling tiktoken.get_encoding() on a
corrupted vendored file doesn't just tolerate the corruption, it erases
the evidence (a live re-download replaces the bad file with a good one).
So every test method here checks the hash itself, by reading the raw
bytes, BEFORE it ever imports tiktoken or calls get_encoding -- never
rely on unittest's method-name ordering to keep a hash-only test ahead
of one that touches tiktoken; a test runner is not obliged to preserve
that order, and a later reader could easily add a tiktoken call to what
looks like the "hash check" test without realizing it breaks this.

See ai_processor/tiktoken_cache/README.md for what's vendored and why.
"""

import hashlib
import os

from django.conf import settings
from django.test import SimpleTestCase

# (encoding name, source URL, tiktoken's own expected sha256) for every
# encoding this repo vendors. Values are copied from
# tiktoken_ext/openai_public.py (part of the tiktoken package), not
# invented here -- see the README for how to reproduce them.
_VENDORED_ENCODINGS = [
    (
        "cl100k_base",
        "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken",
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",  # pragma: allowlist secret
    ),
]


class VendoredTiktokenCacheTest(SimpleTestCase):
    def _cache_path(self, blob_url):
        cache_dir = getattr(settings, "TIKTOKEN_CACHE_DIR", None) or os.environ.get(
            "TIKTOKEN_CACHE_DIR"
        )
        self.assertTrue(
            cache_dir,
            "TIKTOKEN_CACHE_DIR is not set by AutoGrader/settings.py -- the "
            "vendoring wiring itself is missing, not just the file.",
        )
        assert cache_dir is not None  # narrows for mypy; assertTrue proved it above
        cache_key = hashlib.sha1(blob_url.encode()).hexdigest()
        return os.path.join(cache_dir, cache_key)

    def test_settings_points_tiktoken_cache_dir_at_the_vendored_directory(self):
        expected = os.path.join(
            str(settings.BASE_DIR), "ai_processor", "tiktoken_cache"
        )
        self.assertEqual(os.environ.get("TIKTOKEN_CACHE_DIR"), expected)
        self.assertEqual(settings.TIKTOKEN_CACHE_DIR, expected)

    def _read_and_verify_or_skip(self, name, blob_url, expected_hash):
        """
        Reads the vendored file's raw bytes and checks its hash BEFORE
        anything here imports tiktoken. tiktoken.get_encoding would check
        the same hash itself -- but it also silently deletes and
        re-fetches on a mismatch, which would erase the evidence of
        corruption before we could observe it. See the ordering note in
        this module's docstring.

        Returns the verified path. Skips (not fails) if the file is
        simply absent; fails via assertEqual if it's present with the
        wrong hash.
        """
        path = self._cache_path(blob_url)
        if not os.path.exists(path):
            self.skipTest(
                f"{name} is not vendored at {path}. This is not a "
                "failure by itself: tiktoken.get_encoding will still "
                f"fetch it live if the network reaches {blob_url}. To "
                "vendor it (recommended so tests don't depend on live "
                "network), see ai_processor/tiktoken_cache/README.md."
            )
        with open(path, "rb") as f:
            data = f.read()
        actual_hash = hashlib.sha256(data).hexdigest()
        self.assertEqual(
            actual_hash,
            expected_hash,
            f"{path} exists but its sha256 ({actual_hash}) does not "
            f"match tiktoken's own expected hash for {name} "
            f"({expected_hash}). This is a corrupted or swapped "
            "vendored file, not a missing one -- replace it. Do not run "
            "tiktoken.get_encoding on it to 'fix' this: tiktoken's own "
            "cache loader silently deletes and re-fetches a mismatched "
            "file, which would hide the corruption instead of fixing it "
            "here. See ai_processor/tiktoken_cache/README.md.",
        )
        return path

    def test_vendored_encodings_are_present_and_hash_correct(self):
        for name, blob_url, expected_hash in _VENDORED_ENCODINGS:
            with self.subTest(encoding=name):
                self._read_and_verify_or_skip(name, blob_url, expected_hash)

    def test_vendored_cl100k_base_loads_and_encodes(self):
        name, blob_url, expected_hash = _VENDORED_ENCODINGS[0]
        # Verify the raw bytes ourselves first (see the ordering note in
        # this module's docstring) -- only once that's confirmed do we
        # let tiktoken anywhere near the file.
        self._read_and_verify_or_skip(name, blob_url, expected_hash)

        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode("Grade Automator Plus")
        self.assertGreater(
            len(tokens),
            0,
            "cl100k_base loaded from the vendored file but produced no "
            "tokens for a non-empty string.",
        )
        self.assertEqual(encoding.decode(tokens), "Grade Automator Plus")
