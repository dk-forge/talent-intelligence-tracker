"""One bounded read for bytes that arrive from somebody else's server.

WHY THIS EXISTS. The feed catalogue is 600-odd publishers in ~90 countries, and
every one of them is a third party we do not control. `requests.get` without
`stream=True` reads the WHOLE body into memory before it returns, and it
transparently inflates gzip and brotli on the way, so the number of bytes that
land in the collect process is not the number of bytes the publisher sent. A
`Content-Length: 4kB` gzip response can be gigabytes decompressed. The run does
this ~648 times, twice a day, unattended, and the memory ceiling on a GitHub
runner is not generous.

That is not a hypothetical for this catalogue specifically. A domain that
expires and gets taken over is the documented hazard here
(`botswanaguardian.co.bw` became a betting site), and a taken-over domain is
exactly the party that would serve a decompression bomb rather than RSS. So
the same list that makes the domain-drift guard necessary makes this necessary.

The correct pattern already existed one file away, in `press_archive.head_text`:
`stream=True`, then a capped `raw.read(n, decode_content=True)`, then close.
It was written for a different reason (only the `<head>` is wanted) and it was
never generalised. This module IS that pattern, named, and the archive
collector now calls it rather than keeping a second copy.

Two things it does NOT do, said plainly:

* It is not an SSRF guard. Every URL here comes from the catalogue in
  `source_registry` or from a search endpoint we constructed, not from a
  stranger. The sibling tracker's `railway/safe_fetch.py` is the SSRF gate,
  and if a URL a stranger chose ever reaches this module, that gate belongs in
  front of it.
* It does not make a truncated feed a valid feed. A body cut at the cap is
  handed to the parser exactly as it arrives, and a truncated XML document
  fails to parse and is counted as a dead feed. That is the right outcome:
  loud, counted, and not a silently half-read publisher.
"""
import requests
import urllib3

#: Feeds. The largest legitimate feed in the catalogue is comfortably under a
#: megabyte; five is room for a publisher having a bad idea, not room for an
#: attack.
FEED_BYTES = 5_000_000

#: Article heads. `press_archive` reads sharing metadata out of `<head>` and
#: wants no body at all.
HEAD_BYTES = 200_000

#: Small documents whose whole point is to be small: robots.txt, a JSON index.
SMALL_BYTES = 1_000_000


def capped_get(url, *, session=None, headers=None, timeout=30,
               max_bytes=FEED_BYTES, **kwargs):
    """GET `url` and return `(response, body_bytes)`.

    The response is CLOSED before returning and its body is not buffered whole,
    so `response.content` is meaningless afterwards. Read the returned bytes.

    `response` is returned unread so a caller can check `status_code`, `url`
    (the post-redirect landing) and headers BEFORE deciding whether the bytes
    are worth having. That ordering is the point: `national_press` used to read
    the body and then ask whether the domain had drifted.
    """
    http = session or requests
    response = http.get(url, headers=headers, timeout=timeout, stream=True,
                        **kwargs)
    return response, read_capped(response, max_bytes)


def open_capped(url, *, session=None, headers=None, timeout=30, **kwargs):
    """GET `url` streaming, WITHOUT reading it. Returns the response.

    For the caller that must inspect where it landed before spending memory on
    the body: check the response, then call `read_capped`. `capped_get` is this
    plus an immediate read, and is what most callers want.
    """
    http = session or requests
    return http.get(url, headers=headers, timeout=timeout, stream=True, **kwargs)


def read_capped(response, max_bytes=FEED_BYTES):
    """The first `max_bytes` DECOMPRESSED bytes of `response`, then close it.

    `decode_content=True` is the whole reason this is a function rather than a
    slice: a cap applied to the compressed stream is not a cap, because gzip
    and brotli are precisely where a small response becomes a large one.

    FAILURES DURING THE READ ARE RAISED AS `requests.RequestException`. This
    is load-bearing: `raw.read` talks to urllib3 directly, and urllib3 raises
    its OWN exceptions (`ReadTimeoutError`, `ProtocolError`, ...) which are NOT
    `requests.RequestException`. Every caller in this codebase guards its loop
    over third-party hosts with `except requests.RequestException`, which
    covers the request but — before this translation — not the body read. On
    2026-08-05 one publisher (neweralive.na) accepted the connection, returned
    200, and then went silent mid-body: the `ReadTimeoutError` sailed past
    `head_text`'s except clause and killed the entire 19-minute press slice
    (run 31013599896). requests itself does this same translation inside
    `iter_content`; this mirrors its mapping so a slow or dead remote is one
    publisher's recorded outcome, never a run-level crash.
    """
    try:
        raw = getattr(response, "raw", None)
        if raw is not None and hasattr(raw, "read"):
            try:
                try:
                    return raw.read(max_bytes, decode_content=True) or b""
                except TypeError:
                    # A test double, or a urllib3 old enough to lack the kwarg.
                    return raw.read(max_bytes) or b""
            except urllib3.exceptions.ReadTimeoutError as exc:
                raise requests.exceptions.ConnectionError(exc) from exc
            except urllib3.exceptions.DecodeError as exc:
                raise requests.exceptions.ContentDecodingError(exc) from exc
            except urllib3.exceptions.ProtocolError as exc:
                raise requests.exceptions.ChunkedEncodingError(exc) from exc
            except urllib3.exceptions.SSLError as exc:
                raise requests.exceptions.SSLError(exc) from exc
            except (urllib3.exceptions.HTTPError, OSError) as exc:
                # Anything else transport-shaped (socket resets, bare
                # timeouts): still one host's failure, still catchable by the
                # per-host guards.
                raise requests.exceptions.ConnectionError(exc) from exc
        # No raw stream (a stubbed response, or a non-streamed one). Falling
        # back to .content is a whole-body read, which is what this module
        # exists to avoid, so it is capped too rather than trusted.
        return (getattr(response, "content", b"") or b"")[:max_bytes]
    finally:
        try:
            response.close()
        except Exception:
            pass


def capped_text(url, *, encoding="utf-8", **kwargs):
    """`capped_get` decoded. Returns `(response, text)`."""
    response, body = capped_get(url, **kwargs)
    return response, body.decode(encoding, errors="replace")
