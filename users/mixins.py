import hashlib
import json
from typing import Any

from django.conf import settings
from django.core.cache import cache
from rest_framework.request import Request
from rest_framework.response import Response

from AutoGrader.cache_generation import SCOPE_USER, versioned_key


class UserCacheMixin:
    """
    Mixin to handle per-user caching for List and Retrieve actions.

    Keys carry the requesting user's cache GENERATION (H-1 stage 2), so a
    mutation invalidates them with a single INCR instead of a wildcard
    `delete_pattern` sweep across the whole keyspace. This one mixin backs
    nine of the project's 35 cache families - every viewset that mixes it in
    across classrooms, students and users - so migrating it moves the
    largest single block of the cache surface at once.

    Per `docs/H1_CACHE_INVALIDATION_DESIGN.md` §3, every family served here
    depends on the requesting user alone: the queryset is already scoped to
    that user, so anything that changes what they can see also bumps their
    generation (see the receivers in classrooms/users signals).

    The old `<model>s:user_id__<id>:...` prefix is retained ahead of the
    generation segment. That is deliberate: the wildcard receivers are still
    live during the migration, so these keys stay reachable by BOTH
    mechanisms until stage 3 removes the old one. It is what makes a
    read-site revert safe.
    """

    request: Request
    kwargs: dict[str, Any]

    def get_cache_key(self, action):
        model_name = self.get_queryset().model._meta.model_name  # type: ignore
        user_id = self.request.user.id

        if action == "retrieve":
            instance_id = self.kwargs.get("pk")
            base = f"{model_name}s:user_id__{user_id}:instance_id__{instance_id}"
        else:
            query_params = json.dumps(self.request.query_params.dict(), sort_keys=True)
            query_hash = hashlib.md5(query_params.encode()).hexdigest()
            base = f"{model_name}s:user_id__{user_id}:query__{query_hash}"

        # The MD5 of the query params is exactly why targeted key deletion
        # was rejected for this architecture: these keys are not enumerable,
        # so they cannot be found and deleted - only versioned past.
        return versioned_key(base, [(SCOPE_USER, user_id)])

    def list(self, request, *args, **kwargs):  # type: ignore
        cache_key = self.get_cache_key("list")
        data = cache.get(cache_key)

        if data is None:
            response = super().list(request, *args, **kwargs)  # type: ignore
            data = response.data
            cache.set(cache_key, data, getattr(settings, "CACHE_TTL", 60 * 5))

        return Response(data)

    def retrieve(self, request, *args, **kwargs):  # type: ignore
        cache_key = self.get_cache_key("retrieve")
        data = cache.get(cache_key)

        if data is None:
            response = super().retrieve(request, *args, **kwargs)  # type: ignore
            data = response.data
            cache.set(cache_key, data, getattr(settings, "CACHE_TTL", 60 * 5))

        return Response(data)
