# R1 spike: kept upload size on Cloudinary (option b)

Date: 2026-11-20. Run by the fixes-worker for the SM. Code read at beta
a96b381c0. SDK: cloudinary 1.44.1.

Cloud: the cloud in the local .env. The owner says one cloud is shared by
every environment. We have not verified beta's cloud name.

## Answer

**The cloud refuses any single raw upload over 10 MB (10,485,760 bytes),
chunked upload included.** On today's code, a 10-50 MB file that passes the
per-file limit (default 50 MB) is not kept. The door falls back to the inline
payload, and the item can't be retried without uploading it again.

## Read-only findings (account, before any write)

- Plan: Free. Credits 0.15 of 25 (0.6%).
- Storage 14,779,051 B; bandwidth 149,342,351 B; requests 1720; resources 47;
  objects 59.
- Media limits: `raw_max_size_bytes` 10485760, `image_max_size_bytes`
  10485760, `video_max_size_bytes` 104857600, `image_max_px` 25M,
  `asset_max_total_px` 50M.
- Folders: image upload root 7, image upload `media` 15, raw upload `media` 7,
  raw private `kept_uploads/` 18 (all old-style keys with no environment
  folder; newest 2026-11-19T15:31:38Z). `kept_uploads/beta|prod|staging|spike-r1`
  had 0 each. Root folders: `media`.

## Writes (only under kept_uploads/spike-r1/)

Random bytes, sha256 taken before upload. Timings are from a home connection
and only show scale.

Through `students.kept_files.store` (private raw, the door's own call), with
`deployment_folder` patched to `spike-r1`:

- 9.5 MB (9,961,472 B): stored in 24.8 s; loaded in 10.1 s through
  `kept_files.load`; sha256 matches; 9,961,472 B back.
- 10.5 MB (11,010,048 B): FAILED after 43.8 s. HTTP 400,
  `cloudinary.exceptions.BadRequest`: "File size too large. Got 11010048.
  Maximum is 10485760." The app saw `KeptFileUnavailable("BadRequest")`.
- 40 MB (41,943,040 B): FAILED after 109.9 s. HTTP 400, BadRequest: "File size
  too large. Got 41943040. Maximum is 10485760."
- 50 MB (52,428,800 B): FAILED after 224.9 s. HTTP 400, BadRequest: "File size
  too large. Got 52428800. Maximum is 10485760."

Through `cloudinary.uploader.upload_large` (chunked, 6 MB chunks, private raw):

- 40 MB: FAILED after 21.1 s. HTTP 400, BadRequest: "File size too large. Got
  41943040. Maximum is 10485760."
- 50 MB: FAILED after 26.4 s. HTTP 400, BadRequest: "File size too large. Got
  52428800. Maximum is 10485760."

Chunking does not lift the limit on this plan. The chunked failures arrived
sooner, after the first chunks rather than the whole body.

About the status: the SDK does not put the status on the exception
(`http_code` is empty). The SDK raises `BadRequest` only for HTTP 400
(`cloudinary/api_client/execute_request.py` `EXCEPTION_CODES`, used by
`uploader.py` call_api), so the status was 400.

Cleanup: 1 asset written (the 9.5 MB file), 1 deleted with
`kept_files.delete`; `kept_uploads/spike-r1/` lists 0 afterwards.
`kept_uploads/` still holds its 18; beta/prod/staging still 0. Usage after:
storage, requests, resources and objects unchanged; bandwidth 169,265,295 B;
credits 0.17 (0.68%).

## Inline fallback, from code reading (a96b381c0)

- `students/kept_uploads.py` `DoorFile.__init__` (about lines 70-92) calls
  `kept_files.store` while the request is being handled. `store` wraps any
  storage error as `KeptFileUnavailable` (`students/kept_files.py` store). The
  door logs the WARNING "Kept upload: storage unavailable; sending inline
  (BadRequest).", counts `kept_upload_fallback` and sets `stored = None`.
- `DoorFile.attach` writes no `KeptUpload` row when `stored` is None. The
  task message carries the file as `content_b64`
  (`students/kept_uploads.py` about line 119), about 1.33x the file's size in
  the Redis broker.
- `students/item_retry.py` `upload_retryable` needs a kept file, so a failed
  item from such an upload is not retried in place. The teacher must upload it
  again.
- So for every file over 10 MB, the "kept" promise (BE-B-05/06) does not hold.
  The file is not kept. Each one also counts as a storage
  fault in `kept_upload_fallback`, though storage is up.
- The refusal arrives only after the bytes have been sent to Cloudinary:
  43.8 s for 10.5 MB and 224.9 s for 50 MB here. That time is spent inside the
  request, which is against gunicorn `--timeout 100` (Dockerfile:93). Railway's
  upload bandwidth to Cloudinary is faster but not measured.

## For the decision

- Option: set `MAX_UPLOAD_FILE_SIZE_MB=10` (now an env setting, default 50).
  Then no accepted file can hit this refusal.
- Option: a paid plan or a raised raw limit on the cloud. That is an owner
  step, and beta's cloud should be confirmed first.
- Option: skip `store` for files over the cloud's raw limit, so they go
  inline without the wasted upload. They would still not be kept.
