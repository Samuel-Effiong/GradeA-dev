from rest_framework.pagination import PageNumberPagination


class StandardPageNumberPagination(PageNumberPagination):
    """PageNumberPagination with a client-configurable page size."""

    page_size_query_param = "page_size"
    max_page_size = 100
