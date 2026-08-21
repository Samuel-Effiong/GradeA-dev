from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import SimpleTestCase

from AutoGrader.cache_utils import batched_cache_invalidation, delete_cache_patterns


class DeleteCachePatternsTest(SimpleTestCase):
    def test_deletes_each_pattern_immediately_outside_batch(self):
        with patch("AutoGrader.cache_utils.cache") as mock_cache:
            delete_cache_patterns("users:*", "courses:*")

        self.assertEqual(mock_cache.delete_pattern.call_count, 2)
        deleted = {c.args[0] for c in mock_cache.delete_pattern.call_args_list}
        self.assertEqual(deleted, {"users:*", "courses:*"})

    def test_dedupes_patterns_within_a_call(self):
        with patch("AutoGrader.cache_utils.cache") as mock_cache:
            delete_cache_patterns("users:*", "users:*")

        self.assertEqual(mock_cache.delete_pattern.call_count, 1)

    def test_noop_when_backend_lacks_delete_pattern(self):
        # locmem-style backends have no delete_pattern; must not raise.
        with patch("AutoGrader.cache_utils.cache", new=object()):
            delete_cache_patterns("users:*")

    def test_noop_with_no_patterns(self):
        with patch("AutoGrader.cache_utils.cache") as mock_cache:
            delete_cache_patterns()

        mock_cache.delete_pattern.assert_not_called()

    def test_backend_errors_are_swallowed_and_other_patterns_still_deleted(self):
        mock_cache = MagicMock()
        mock_cache.delete_pattern.side_effect = ConnectionError("redis down")

        with patch("AutoGrader.cache_utils.cache", new=mock_cache):
            with self.assertLogs("AutoGrader.cache_utils", level="ERROR"):
                delete_cache_patterns("users:*", "courses:*")

        # Both patterns attempted despite the first raising.
        self.assertEqual(mock_cache.delete_pattern.call_count, 2)


class BatchedCacheInvalidationTest(SimpleTestCase):
    def test_defers_and_dedupes_until_batch_exit(self):
        with patch("AutoGrader.cache_utils.cache") as mock_cache:
            with batched_cache_invalidation():
                delete_cache_patterns("users:*", "courses:*")
                delete_cache_patterns("users:*", "sessions:*")
                mock_cache.delete_pattern.assert_not_called()

            deleted = {c.args[0] for c in mock_cache.delete_pattern.call_args_list}

        self.assertEqual(mock_cache.delete_pattern.call_count, 3)
        self.assertEqual(deleted, {"users:*", "courses:*", "sessions:*"})

    def test_flushes_on_exception(self):
        # Rows saved before the failure still need their invalidation.
        with patch("AutoGrader.cache_utils.cache") as mock_cache:
            with self.assertRaises(RuntimeError):
                with batched_cache_invalidation():
                    delete_cache_patterns("users:*")
                    raise RuntimeError("boom")

        mock_cache.delete_pattern.assert_called_once_with("users:*")

    def test_nested_batches_flush_once_at_outermost_exit(self):
        with patch("AutoGrader.cache_utils.cache") as mock_cache:
            with batched_cache_invalidation():
                with batched_cache_invalidation():
                    delete_cache_patterns("users:*")
                # Inner exit must not flush.
                mock_cache.delete_pattern.assert_not_called()
                delete_cache_patterns("users:*", "courses:*")
                mock_cache.delete_pattern.assert_not_called()

        self.assertEqual(mock_cache.delete_pattern.call_count, 2)

    def test_batch_state_does_not_leak_after_exit(self):
        with patch("AutoGrader.cache_utils.cache") as mock_cache:
            with batched_cache_invalidation():
                delete_cache_patterns("users:*")
            mock_cache.delete_pattern.reset_mock()

            # Back to immediate mode.
            delete_cache_patterns("courses:*")
            mock_cache.delete_pattern.assert_called_once_with("courses:*")

    def test_empty_batch_deletes_nothing(self):
        with patch("AutoGrader.cache_utils.cache") as mock_cache:
            with batched_cache_invalidation():
                pass

        mock_cache.delete_pattern.assert_not_called()


class RedisCacheSettingsTest(SimpleTestCase):
    """Lock down the settings that keep delete_pattern fast and scoped."""

    def test_scan_itersize_is_configured_far_above_default(self):
        # django-redis defaults to 10 keys per SCAN round trip, which makes
        # every delete_pattern a keyspace crawl on remote Redis.
        self.assertGreaterEqual(settings.DJANGO_REDIS_SCAN_ITERSIZE, 10_000)

    def test_cache_keys_are_prefixed_away_from_celery_keys(self):
        self.assertTrue(settings.CACHES["default"].get("KEY_PREFIX"))
