from typing import Any

from django.conf import settings
from django.core.cache import cache
from rest_framework.request import Request
from rest_framework.response import Response


class UserCacheMixin:
    """
    Mixin to handle per-user caching for List and Retrieve actions.
    Uses naming conventions compatible with your delete_pattern signals.
    """

    request: Request
    kwargs: dict[str, Any]

    def get_cache_key(self, action):
        model_name = self.get_queryset().model._meta.model_name  # type: ignore
        user_id = self.request.user.id

        if action == "retrieve":
            instance_id = self.kwargs.get("pk")
            return f"{model_name}s:user_id__{user_id}:instance_id__{instance_id}"

        return f"{model_name}s:user_id__{user_id}"

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
