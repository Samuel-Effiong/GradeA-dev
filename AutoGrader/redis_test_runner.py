"""Test runner that keeps Redis clean (`redis_test_hygiene`) and blocks real
outbound network calls (`network_guard`, H-39)."""

from django.test.runner import DiscoverRunner

from AutoGrader.network_guard import block_real_network_calls
from AutoGrader.redis_test_hygiene import redis_test_hygiene


class RedisHygieneRunner(DiscoverRunner):
    def run_tests(self, *args, **kwargs):
        with redis_test_hygiene(), block_real_network_calls():
            return super().run_tests(*args, **kwargs)
