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


@shared_task(bind=True, max_retries=0)
@single_instance(max_hold=beat_locks.HOURLY)
def purge_upload_quotes(self):
    """Hourly (upload quote, plan rev 3 section 7): expire open quotes past
    their expiry, delete the files of expired and cancelled quotes, drop the
    rows of ended quotes after the replay window."""
    from students.upload_quotes import purge_due

    return purge_due()


@shared_task(bind=True, max_retries=500)
def measure_quote_file(self, file_id):
    """Zero-credit measure of one upload quote file, on the `quote_measure`
    queue, at most UPLOAD_QUOTE_MEASURE_CONCURRENCY at once
    (students.upload_quote_measure)."""
    from students import kept_files, upload_quote_measure

    if not upload_quote_measure.acquire():
        raise self.retry(countdown=upload_quote_measure.RETRY_COUNTDOWN)
    try:
        upload_quote_measure.do_measure(file_id)
    except kept_files.KeptFileUnavailable:
        raise self.retry(
            countdown=upload_quote_measure.STORAGE_RETRY_COUNTDOWN
        ) from None
    finally:
        upload_quote_measure.release()


@shared_task(bind=True, max_retries=0)
@single_instance(max_hold=beat_locks.EVERY_5_MIN, ttl=beat_locks.EVERY_5_MIN_TTL)
def requeue_stuck_quote_measures(self):
    """Every 5 minutes: ask again for quote files stored and unmeasured for
    over 10 minutes (3 attempts), and raise the alarm on a quote still
    measuring after 30."""
    from students.upload_quote_measure import requeue_stuck

    return requeue_stuck()
