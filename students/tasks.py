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
@single_instance(max_hold=beat_locks.EVERY_5_MIN, ttl=beat_locks.EVERY_5_MIN_TTL)
def purge_upload_quotes(self):
    """Every 5 minutes (upload quote, plan rev 3 section 7): expire open quotes past
    their expiry, delete the files of expired and cancelled quotes, drop the
    rows of ended quotes after the replay window."""
    from students.upload_quotes import purge_due

    return purge_due()


@shared_task(bind=True, max_retries=500)
def measure_quote_file(self, file_id):
    """Zero-credit measure of upload quote files, on the `quote_measure`
    queue, at most UPLOAD_QUOTE_MEASURE_CONCURRENCY at once; a run with a slot
    takes the waiting files oldest first (students.upload_quote_measure)."""
    from students import kept_files, upload_quote_measure

    try:
        # No slot: ends at once; the run holding one takes this file.
        upload_quote_measure.run(file_id)
    except kept_files.KeptFileUnavailable:
        raise self.retry(
            countdown=upload_quote_measure.STORAGE_RETRY_COUNTDOWN
        ) from None


#: A store task tries a file this many more times on a storage fault, a
#: minute apart, before the file ends NOT_KEPT.
STORE_MAX_RETRIES = 5
STORE_RETRY_COUNTDOWN = 60
#: A task that finds every store slot taken is sent again after this, up to
#: STORE_MAX_SLOT_WAITS times (then the sweeper asks again). Waiting for a
#: slot is not a storage fault.
STORE_SLOT_COUNTDOWN = 15
STORE_MAX_SLOT_WAITS = 240


@shared_task(bind=True, max_retries=None)
def store_quote_files(self, quote_id, file_ids, faults=0, waits=0):
    """Store the staged bytes of an upload quote's STORING files (the create
    and add-files reply first), on the `quote_measure` queue, several files
    at a time (students.upload_quote_service.store_staged), at most
    UPLOAD_QUOTE_STORE_SLOTS tasks at once. Files the storage did not answer
    for are tried again (`faults`), their bytes still staged; on the last try
    they end NOT_KEPT."""
    from students import upload_quote_service as service

    if not service.acquire_store_slot():
        if waits >= STORE_MAX_SLOT_WAITS:
            return None  # the sweeper asks again; the bytes are staged
        raise self.retry(
            args=(quote_id, file_ids),
            kwargs={"faults": faults, "waits": waits + 1},
            countdown=STORE_SLOT_COUNTDOWN,
        )
    try:
        pending = service.store_staged(
            quote_id, file_ids, final=faults >= STORE_MAX_RETRIES
        )
    finally:
        service.release_store_slot()
    if pending:
        raise self.retry(
            args=(quote_id, pending),
            kwargs={"faults": faults + 1, "waits": waits},
            countdown=STORE_RETRY_COUNTDOWN,
        )
    return None


@shared_task(bind=True, max_retries=0)
@single_instance(max_hold=beat_locks.EVERY_5_MIN, ttl=beat_locks.EVERY_5_MIN_TTL)
def requeue_stuck_quote_measures(self):
    """Every 5 minutes: ask again for quote files stored and unmeasured for
    over 10 minutes (3 attempts), and raise the alarm on a quote still
    measuring after 30. Also the store's net: a STORING file is asked for
    again after 10 minutes while its bytes are staged, and ends NOT_KEPT with
    none staged or after 30."""
    from students.upload_quote_measure import requeue_stuck
    from students.upload_quote_service import sweep_storing

    return {**requeue_stuck(), **sweep_storing()}
