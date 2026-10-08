"""What is left of the wildcard-invalidation helpers: nothing.

`AutoGrader/cache_utils.py` held `delete_cache_patterns` (a best-effort
`cache.delete_pattern` loop) and `batched_cache_invalidation` (coalescing
those sweeps inside a block). The tests that used to live here pinned their
behaviour. H-1 step 4 deleted the module, because generation bumps
(`AutoGrader/cache_generation.py`) are now the only cache invalidation;
see docs/evidence/H1_STEP4_WILDCARD_REMOVAL_EVIDENCE.md. The Redis settings
the one remaining pattern delete (the PDF cache's exact-prefix clear) still
relies on are kept below.
"""

import importlib

from django.conf import settings
from django.test import SimpleTestCase


class WildcardHelpersAreGoneTest(SimpleTestCase):
    def test_cache_utils_module_no_longer_exists(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("AutoGrader.cache_utils")

    def test_no_signal_module_still_defines_a_wildcard_helper(self):
        for name in (
            "classrooms.signals",
            "users.signals",
            "students.signals",
            "assignments.signals",
        ):
            module = importlib.import_module(name)
            for attr in (
                "delete_cache_patterns",
                "batched_cache_invalidation",
                "SUBMISSION_CACHE_PATTERNS",
                "_warned_backend_lacks_delete_pattern",
            ):
                self.assertFalse(
                    hasattr(module, attr), f"{name}.{attr} is back (H-1 step 4)"
                )


class RedisCacheSettingsTest(SimpleTestCase):
    """Lock down the settings that keep the PDF cache's delete_pattern fast
    and scoped (the one pattern delete H-1 kept)."""

    def test_scan_itersize_is_configured_far_above_default(self):
        # django-redis defaults to 10 keys per SCAN round trip, which makes
        # every delete_pattern a keyspace crawl on remote Redis.
        self.assertGreaterEqual(settings.DJANGO_REDIS_SCAN_ITERSIZE, 10_000)

    def test_cache_keys_are_prefixed_away_from_celery_keys(self):
        self.assertTrue(settings.CACHES["default"].get("KEY_PREFIX"))
