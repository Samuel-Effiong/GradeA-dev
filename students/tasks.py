"""Beat tasks of the students app."""

from celery import shared_task

from AutoGrader import beat_locks
from AutoGrader.beat_locks import single_instance


@shared_task(bind=True, max_retries=0)
@single_instance(max_hold=beat_locks.HOURLY)
def purge_kept_uploads(self):
    """Hourly (Epic B1b-5, 08b §3.2): delete every kept upload file past its
    kept_until, in batches of 200; a failed delete is retried next run, and a
    file a day overdue is counted (kept_upload_purge_overdue)."""
    from students.kept_uploads import purge_due

    return purge_due()
