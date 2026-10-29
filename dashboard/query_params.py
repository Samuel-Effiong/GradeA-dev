"""A dashboard query parameter the caller got wrong (H-265).

Its own small module so AutoGrader.error_messages can import it without
loading dashboard.views."""


class QueryParamError(ValueError):
    """A bad query parameter; its text names the parameter and is written
    to be shown."""
