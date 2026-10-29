"""
H-257: `UserCacheMixin.get_cache_key` (users/mixins.py) names a list's cache
entry by the MD5 of its query string. Bandit's B324 flags MD5; it secures
nothing here, so the call says `usedforsecurity=False`. That flag changes no
digest: the key for the same query string must stay byte for byte what it
was, or every cached list would be missed once after the deploy.

The digest below was computed before the change, from the same JSON the
mixin builds: '{"page": "2", "search": "x"}'.

Run with:
    python manage.py test users.tests_h257_cache_key_digest
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from users.mixins import UserCacheMixin

DIGEST_BEFORE_H257 = "6b28336181f78eb555d53247f909c6b5"  # pragma: allowlist secret


class Widgets(UserCacheMixin):
    def get_queryset(self):
        return SimpleNamespace(
            model=SimpleNamespace(_meta=SimpleNamespace(model_name="widget"))
        )


class CacheKeyDigestTests(SimpleTestCase):
    def key_for(self, query):
        view = Widgets()
        view.request = SimpleNamespace(  # type: ignore[assignment]
            user=SimpleNamespace(id=7),
            query_params=SimpleNamespace(dict=lambda: query),
        )
        view.kwargs = {}
        # The generation segment is not this test's subject: the base the
        # mixin hands to versioned_key is.
        with patch("users.mixins.versioned_key", side_effect=lambda base, scopes: base):
            return view.get_cache_key("list")

    def test_the_list_key_is_what_it_was(self):
        self.assertEqual(
            self.key_for({"search": "x", "page": "2"}),
            f"widgets:user_id__7:query__{DIGEST_BEFORE_H257}",
        )

    def test_the_order_of_the_query_string_does_not_change_the_key(self):
        self.assertEqual(
            self.key_for({"page": "2", "search": "x"}),
            self.key_for({"search": "x", "page": "2"}),
        )
