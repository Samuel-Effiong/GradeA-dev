# Vendored tiktoken cache

tiktoken has no bundled BPE data: `tiktoken.get_encoding("cl100k_base")`
fetches it over HTTPS from `openaipublic.blob.core.windows.net` on first use
and caches the result under `TIKTOKEN_CACHE_DIR` (see
`tiktoken.load.read_file_cached`), by
`sha1(blob_url).hexdigest()` — **not** by encoding name.

`AutoGrader/settings.py` points `TIKTOKEN_CACHE_DIR` at this directory, so
this file is used directly with no network call, on any machine that has
the repo checked out.

## What's here

| File | Encoding | Source URL | sha256 |
|---|---|---|---|
| `9b5ad71b2ce5302211f9c61530b329a4922fc6a4` | `cl100k_base` | `https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken` | `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7` |

The sha256 above is not something we picked — it's the `expected_hash`
`tiktoken_ext/openai_public.py` (part of the `tiktoken` package itself)
already carries for `cl100k_base`, and `ai_processor/tests_tiktoken_cache.py`
checks the vendored copy against it directly, byte for byte. tiktoken's own
`read_file_cached` also checks it on every load and silently deletes +
re-fetches a mismatched file — so a corrupted copy here fails the test
loudly instead of quietly falling back to a live fetch.

## Why the filename looks random

The filename is `sha1(<the source URL above>).hexdigest()`. That's
tiktoken's own cache-key scheme, not ours — reproduce it yourself with:

```python
import hashlib
hashlib.sha1(
    b"https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
).hexdigest()
```

## Adding another encoding

If a future change needs a different encoding (e.g. `o200k_base` for newer
OpenAI models), vendor it the same way:

1. `python3 -c "import tiktoken; tiktoken.get_encoding('o200k_base')"` on a
   machine with working network — this populates `TIKTOKEN_CACHE_DIR`
   (or the default `<tempdir>/data-gym-cache` if unset) with a
   correctly-named, hash-verified file.
2. Copy that file into this directory.
3. Add a row to the table above (get the URL + expected_hash from the
   relevant function in `tiktoken_ext/openai_public.py`).
4. Extend `ai_processor/tests_tiktoken_cache.py`'s table of encodings to
   verify, and `.pre-commit-config.yaml`'s `check-added-large-files`
   exclude already matches any 40-hex-char filename in this directory, so
   no further hook change should be needed.

## What breaks if this file goes missing or corrupts

Nothing silently. `ai_processor/tests_tiktoken_cache.py` fails loudly (not a
skip) if the file is present-but-wrong-hash, and skips (not fails) with a
clear message only if it's absent entirely — at which point tiktoken falls
back to its own live-fetch-and-cache behavior, so tests still pass on a
machine with real network, they just no longer need this vendoring to do
so.
