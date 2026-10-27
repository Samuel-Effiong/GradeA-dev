# H-263 notes (Hardening Engineer, 4d)

- **The row** (batch 30): upgrade the six packages the owner approved on 2026-10-27, take their entries out of
  `.pip-audit-ignore.toml`, and show the audit clean at the tip. One commit per package.
- **Base:** H-258's tip `9263e9f8` (Senior Manager), so the ignore file and `requirements.txt` do not conflict.
- **Branch** `task/h263-package-upgrades`.

| Commit | Package | From | To | Ids out of the ignore file |
|---|---|---|---|---|
| `21611a99` | gunicorn (`Dockerfile:37` and `requirements.txt`) | 20.0.4 | 22.0.0 | 2 |
| `83da86fb` | pillow | 12.0.0 | 13.0.0 | 56 |
| `9d8c74fd` | PyJWT | 2.10.1 | 2.15.0 | 17 |
| `43d32988` | lxml | 6.0.2 | 6.1.0 | 1 |
| `00e15e9e` | urllib3 | 2.5.0 | 2.8.0 | 6 |
| `d154d031` | djangorestframework | 3.16.1 | 3.18.2 | 5 |

- **The ignore file** goes from 27 packages and 192 ids to 21 packages and 105 ids.
- **The audit at the tip** (pip-audit 2.9.0 through `scripts/pip_audit_gate.py`, before the gate): exit 0, "No
  known vulnerabilities found, 105 ignored". None of the six new versions has an advisory of its own.

## How the changelogs were read

- **Source:** each package's own changelog at the new version, from its sdist on PyPI (gunicorn
  `docs/source/news.rst` and `2021-news.rst`; pillow `docs/releasenotes/12.1.0.rst` to `13.0.0.rst`, its
  `CHANGES.rst` stopping at 11.0.0; PyJWT `CHANGELOG.rst`; lxml `CHANGES.txt`; urllib3 `CHANGES.rst`). DRF's
  sdist carries none, so `docs/community/release-notes.md` at the `3.18.2` tag on GitHub.
- **Read as data only:** nothing was installed or run from them. Where a note was unclear, the new source was
  compared with the installed old version (urllib3, DRF).
- **Every release between the two versions** was read, not only the last.
- **Dependants:** no installed package caps any of the six below its new version (each `Requires-Dist` in the
  shared venv read). All six support Python 3.12; pillow 13.0.0 and lxml 6.1.0 ship cp312 manylinux x86_64
  wheels.

## What touches us, package by package

### gunicorn 20.0.4 to 22.0.0

- **The fix:** 22.0.0 closes CVE-2024-1135 (request smuggling through conflicting `Transfer-Encoding`) and
  other HTTP parser holes.
- **Our CMD** (`Dockerfile`): `--bind --timeout 100 --workers --threads --max-requests 1000 --worker-class
  gthread --keep-alive 5 --max-requests-jitter 200`. Every flag exists in 22.0.0. The timeout sync check
  (`scripts/check_gunicorn_timeout_sync.py`) passes.
- **gthread:** 21.0.0 to 21.2.0 reworked the thread worker and fixed a "bad file descriptor" it introduced;
  22.0.0 comes after those fixes.
- **Stricter HTTP parsing in 22.0.0** (the breaking changes):
  - header names with underscores are now silently dropped. The headers our code reads (`Stripe-Signature`,
    `X-Forwarded-Proto` for `SECURE_PROXY_SSL_HEADER`, `X-Request-ID`) use dashes;
  - a request with both `Transfer-Encoding` and `Content-Length` is refused, as are HTTP versions other than
    1.0 and 1.1. What Railway's edge forwards was not checked;
  - methods are no longer case-folded.
- **Also:** 20.1.0 removed the version from the `Server` header and the `--log-dict-config` flag (not used).
  22.0.0 needs `packaging` (pinned 25.0).
- **No test imports gunicorn.** Its check is a boot of the app (proposed for the gate).

### pillow 12.0.0 to 13.0.0

- **The fix:** 13.0.0 lists 2 critical, 13 high and 20 medium advisories, among them PSD, JPEG, TIFF, BMP
  and decompression-bomb bypasses through `seek()`.
- **Our calls:** `Image.open` (`assignments/services.py:546`, `ai_processor/services.py:6106` and `:6192`),
  `convert`, `Image.new`, `resize` with `Image.LANCZOS` (`ai_processor/tools.py:328`), `Image.MAX_IMAGE_PIXELS`,
  `DecompressionBombError`, `UnidentifiedImageError`. All are kept in 13.0.0. `Image.LANCZOS` is still set
  from the `Resampling` enum (`Image.py:190`).
- **Removed or changed in 12.1 to 13.0, none used here:** Python 3.10; clipping of I-mode images saved as
  PNG; `Image.get_child_images()` (moved to `ImageFile`); `Image._show`; `ImageCms` product name and info;
  `ExifTags.IFD.Makernote`; `getdata()` deprecated (12.1.0); encoding an empty image now raises `ValueError`
  not `SystemError` (12.2.0).
- **13.0.0 behaviour that could show:** `resize` now applies `reducing_gap` to RGBA and LA images (it was
  ignored), and the Linux wheels now link the bundled HarfBuzz. Our resize is of RGB images after `_to_rgb`.
- **H-258's "not checked" cell, now checked:** does an earlier type check refuse a PSD before `Image.open`?
  **No.** `uploaded_file.content_type` is the client's claim and only picks the image branch
  (`IMAGE_FORMATS`); `Image.open(BytesIO(...))` then runs with no `formats=` limit. So a PSD (or any format
  pillow reads) labelled `image/png` reaches its decoder. H-258's "Yes, likely" becomes **Yes**; 13.0.0 fixes
  the PSD advisory.
  - **Proposed, not done here:** `Image.open(..., formats=["JPEG", "PNG", "GIF", "WEBP"])` would refuse every
    other decoder. That is a behaviour change, so it is a new row for a ruling.

### PyJWT 2.10.1 to 2.15.0

- **The fix:** the algorithm-confusion and HMAC-key advisories of 2.13.0 and 2.14.0, the `crit` header check
  (2.12.0), and recursion and JWKS fixes.
- **How we use it:** only through simplejwt 5.5.1 (`pyjwt>=1.7.1`). `SIMPLE_JWT` sets `ALGORITHM = "HS256"`
  and no `SIGNING_KEY`, so it signs with `SECRET_KEY`. No `PyJWKClient`, no issuer or audience. One test
  imports `jwt` directly (`users.tests_token_revocation`).
- **Changes that could touch us:**
  - 2.11.0 warns (`InsecureKeyLengthWarning`) on an HMAC key under 32 bytes. The local `.env` key is 67
    bytes; **production's key length was not checked** (it is in Railway's settings);
  - 2.13.0 refuses an empty HMAC key outright;
  - 2.11.0 requires `iss` to be a string (we set none);
  - stricter compact-JWS decoding (2.14.0): tokens simplejwt itself issued are compact JWS.

### lxml 6.0.2 to 6.1.0

- **The fix:** CVE-2026-41066, external entities resolved by `iterparse()` and `ETCompatXMLParser`. 6.0.3 and
  6.0.4 are memory fixes. No API change.
- **H-258's "not checked" cell, now checked:** does our HTML parsing read entities? **No.** Our code parses
  only HTML: `lxml.html.fromstring` (`assignments/prosemirror_converter.py:665`, `assignments/services.py:286`
  to `:307`) and `BeautifulSoup(content, "lxml")` (`ai_processor/tools.py:250`, lxml's HTML parser). It never
  calls `iterparse`, `ETCompatXMLParser` or an XML parser. The HTML and XML parsers have resolved internal
  entities only since lxml 5.0.

### urllib3 2.5.0 to 2.8.0

- **The fix:** decompression bombs in the streaming API (2.6.0, 2.6.3, 2.7.0), unbounded chained
  `Content-Encoding` (2.6.0), unbounded chunk-size lines and HTTPS-proxy TLS (2.8.0).
- **We use it through requests 2.34.2** (`urllib3<3,>=1.26`), toward Google, MailerLite, Stripe and the URL
  fetcher in `ai_processor/tools.py`.
- **The SSRF guard reaches into urllib3's internals:** `_peer_address` reads `response.raw._connection` (or
  `.connection`), then `.sock.getpeername()`, and fails closed if it cannot. In 2.8.0, `HTTPResponse` still
  sets `self._connection`, still has the `connection` property returning it, and `HTTPConnection` still holds
  the socket in `self.sock` (`response.py`, `connection.py` compared with 2.5.0).
  - **The test that pins this,** `ai_processor.tests_ssrf_guard.PeerIntrospectionAgainstARealConnectionTest`,
    runs only with `CI_REQUIRE_NETWORK=1` (one GET to example.com), so the normal suite skips it. Proposed for
    the gate, by ruling.
- **Other changes:** 2.6.0 wants brotli 1.2.0 or later when brotli is installed (pinned 1.2.1 in H-258);
  `getheader`/`getheaders` were removed in 2.6.0 and restored in 2.6.1; 2.6.3 caps `Retry-After` at 6 hours;
  2.7.0 turns deprecation notices into `FutureWarning`; 2.8.0 parses hosts more strictly and stops applying
  target TLS settings to HTTPS proxies (we use no proxy).

### djangorestframework 3.16.1 to 3.18.2

- **The fix:** GHSA-3547-9m27-7rxg (a compression codec named as the request charset makes a 108-byte body
  allocate about 97 MiB), large `Accept` headers, `TokenProxy` deletion, and 3.17.2's `AdminRenderer` and
  body-size fixes.
- **Needs Django >= 5.2** (pinned 5.2.17). simplejwt, djoser, drf-spectacular and drf-yasg all accept 3.18.
- **coreapi support was removed (3.17.0):** no effect. coreapi is not in `requirements.txt`; drf-yasg imports
  `coreschema` inside a try and is not wired in; the schema class is drf-spectacular's.
- **Two changes reach our API (for a ruling):**
  1. **List-serializer errors become a dict (3.18.0).** A `many=True` serializer's validation errors were a
     list with `{}` for each valid item; now they are `{index: errors}` for the failing items only. The
     custom exception handler (`users/exceptions.py`) passes them through unchanged. Our writable list inputs:
     `rubric` (`assignments/serializers.py:75`), `tasks` (`:914`), `session_breakdown`
     (`classrooms/serializers.py:1206`) and the bulk topic create (`classrooms/views.py:1898`). No test was
     found that pins the list shape. `REST_FRAMEWORK["LIST_SERIALIZER_ERRORS_AS_DICT"] = False` keeps the old
     shape, with a `RemovedInDRF320Warning`.
  2. **JSON and form bodies are held to `DATA_UPLOAD_MAX_MEMORY_SIZE` (3.17.2).** `JSONParser` and
     `FormParser` now read `request.body`, which Django refuses above the limit (`RequestDataTooBig`). We do
     not set it, so the limit is Django's 2.5 MB; until now a JSON body of any size was parsed.
     - No JSON input was found that carries file bytes: uploads are multipart (files spill to disk and are
       capped by `AutoGrader/uploads.py`), `content_b64` is built by the server for the Celery task, and rich
       text drops `data:` images (`prosemirror_converter.py:388`). The roster file is capped at 2 MB.
- **Other changes:** `FloatField` refuses nan and inf (3.18.1); `set_rollback` only rolls back initialized
  connections (3.18.0); `UniqueTogetherValidator` and unique-constraint fixes.

## Proposed for the gate (by ruling)

- **The private venv** `~/Documents/Virtualenvs/AutoGrader_env_h263`, a copy of the shared one with the six pins
  installed, removed after the gate. The shared venv is not touched.
- **Red:** the audit on the base pins with the six entries removed from the ignore file must find exactly the
  87 ids that came out; at the tip it is clean. Planted: one package put back to its old pin at the tip must
  turn the audit red with that package's ids.
- **Per commit, its near suites** (from a search of the test modules):
  - gunicorn: `gunicorn --check-config` and a boot with the Dockerfile's flags on a local port, one request to
    `/health`, then stop;
  - pillow: the 19 modules that open or compress images (`ai_processor.tests`, `assignments.tests_security`,
    `assignments.tests_file_reason_codes`, the students batch modules and others);
  - PyJWT: the 19 auth and token modules (`users.tests_token_revocation`, `users.tests_auth_endpoints`, and
    others);
  - lxml: the 11 sanitizer, ProseMirror and PDF modules;
  - urllib3: `ai_processor.tests_ssrf_guard`, with the live peer test by ruling;
  - DRF: it reaches every view, so the full suite at the tip.
- **The full suite at the tip** with `--parallel 4`, in the private venv.
