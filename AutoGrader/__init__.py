# H-89: before anything else in the package can log. Every process that
# loads AutoGrader.settings imports this package first, so the log record
# factory that scrubs addresses is in place from the first record.
from .log_scrubbing import install as _install_log_scrubbing

_install_log_scrubbing()

from .celery import app as celery_app  # noqa: E402

__all__ = ("celery_app",)
