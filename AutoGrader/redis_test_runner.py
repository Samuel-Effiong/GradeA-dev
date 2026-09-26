"""Test runner that keeps Redis clean; see `AutoGrader/redis_test_hygiene.py`."""

from django.test.runner import DiscoverRunner

from AutoGrader.redis_test_hygiene import redis_test_hygiene


class RedisHygieneRunner(DiscoverRunner):
    def run_tests(self, *args, **kwargs):
        with redis_test_hygiene():
            return super().run_tests(*args, **kwargs)
