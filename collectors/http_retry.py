"""One answer to "slow down", shared, capped, and never silent.

WHY THIS EXISTS
---------------
A 429 is the one third-party answer that is neither success nor failure: the
document is there, we are allowed to have it, and we asked too fast. Every
other status has an obvious reading. This one has to be *retried*, and if the
retries run out the slot has to be *recorded*, because the alternative is the
defect this module is named after.

The audit that produced this module found five collectors answering a 429 by
`continue`ing past the query, the page or the document, with no counter, no
print and no health row:

  * `google_news` - one `except requests.RequestException: continue` per
    edition, across ~40 editions. A throttled edition and an edition that
    genuinely had no news are the same observation downstream.
  * `sec_edgar` - the same shape twice, once per phrase and once per document.
  * `sec_form_d` - per-filing XML fetch, and an EFTS retry ladder that was
    5xx-only, so a 429 broke out on the FIRST attempt.
  * `benchmark_chase` - the SEC leg returned `[]` without printing anything.
  * `press_archive` - the CDX path was hardened to say "the archive did not
    answer, which is NOT 'nothing archived'"; the sitemap, robots and head
    paths in the same file still read any non-200 as "nothing there".

A silent drop is worse than a red run and worse than an exception. It reports
`found = N` where the honest number is "N, and M slices we never read", and
`store.report_health` then grades the run on it.

WHAT THIS DOES, AND THE TWO THINGS IT REFUSES TO DO
---------------------------------------------------
`fetch(slot, call)` runs `call()`, and if the response is a rate limit it
sleeps and calls it again. Two rules bound it:

1. **`Retry-After` is honoured and CAPPED.** A server is entitled to say
   "3600". Sleeping through an hour inside a job with a `timeout-minutes` is
   how a run gets killed with nothing committed, which loses far more than the
   one slot. So the wait is `min(Retry-After, RETRY_AFTER_CAP_SECONDS)` and the
   run comes back to that slot tomorrow. A header we cannot parse (an HTTP-date
   form, an empty string) falls back to exponential backoff rather than to
   zero: an unreadable "slow down" is still a "slow down".
2. **Exhaustion is RECORDED, not raised into silence.** When the attempts run
   out the slot is appended to `EXHAUSTED` and a `RateLimited` is raised.
   `RateLimited` deliberately subclasses `requests.RequestException` so that
   every existing per-slot `except ... : continue` keeps its original virtue -
   one throttled edition must not lose the other forty - while the drop itself
   is now in a ledger the run reports. Both halves are needed: making the
   exception escape those handlers would turn one throttled edition into a
   dead run, and that is a worse trade, not a stricter one.

`EXHAUSTED` is process-local and is read once, at the end of the run, by
`run_collect`. It is not a judge and sets no threshold: it reports a count and
the slot labels, and the run is graded `degraded` because a run that could not
read part of its input is not `ok`.

Stdlib plus `requests`, no session state, no adapter mounted anywhere: the
callers already own their sessions and their headers, and this wraps whatever
they already do rather than replacing it.
"""
from __future__ import annotations

import time

import requests

#: The statuses that mean "ask again later" rather than "no". 503 is here with
#: 429 because a shared host under load answers 503 to the same overload a
#: rate limiter answers 429 to, and both are cleared by waiting. 500/502/504
#: are deliberately NOT here: those are somebody's bug or a broken hop, and
#: each collector that wants to retry one already does so on its own terms
#: (sec_form_d's EFTS ladder, for instance, which this module now feeds).
RATE_LIMIT_STATUS = frozenset({429, 503})

#: Three tries is two waits. Enough to ride out a burst limiter, short enough
#: that a persistently throttled source is reported today rather than slept on.
DEFAULT_ATTEMPTS = 3

#: The ceiling on ONE wait. Sized against the tightest budget a collector runs
#: under: `collect-press.yml` allows 120 minutes for ~600 publishers, so 30
#: seconds is a wait a slot can afford and 3600 is not. Do NOT raise this to
#: "be polite" to a server that asked for an hour - the polite answer to an
#: hour is to come back on the next run, which is what recording the slot
#: arranges.
RETRY_AFTER_CAP_SECONDS = 30.0

#: The fallback ladder when the server named no interval we could read.
BACKOFF_SECONDS = 2.0


class RateLimited(requests.RequestException):
    """The slot was not read, and it was not read because we were throttled.

    A `requests.RequestException` ON PURPOSE. See the module docstring: the
    per-slot handlers that keep one bad edition from losing the other forty
    are correct, and this rides inside them. What changes is that the drop is
    in `EXHAUSTED` before it is raised.
    """


#: Slots this process asked for and could not get. `(slot, detail)` pairs, in
#: the order they happened. Read by run_collect at the end of the run.
EXHAUSTED: list[tuple[str, str]] = []


def reset() -> None:
    """Forget this process's throttle ledger. For tests and for a caller that
    runs more than one collector in one process."""
    EXHAUSTED.clear()


def exhausted_detail() -> str:
    """One line naming what was throttled away, or "" when nothing was.

    Deliberately names the SLOTS and not just a count: "3 slice(s) throttled"
    sends a reader to the log, and "es-MX, de-DE, fr-FR" sends them to the
    rate limiter. Bounded, because a badly throttled run could otherwise write
    a health `detail` longer than the column.
    """
    if not EXHAUSTED:
        return ""
    names = [slot for slot, _detail in EXHAUSTED]
    shown = ", ".join(names[:6])
    if len(names) > 6:
        shown += f", +{len(names) - 6} more"
    return (f"{len(names)} slice(s) NOT read, throttled out: {shown}"
            " (retried on the next run)")


def retry_after_seconds(headers, attempt: int, *,
                        cap: float = RETRY_AFTER_CAP_SECONDS,
                        backoff: float = BACKOFF_SECONDS) -> float:
    """How long to wait, honouring `Retry-After` but never past `cap`.

    An HTTP-date `Retry-After` is not parsed on purpose: it is rare, and a
    wrong clock would turn it into either zero (hammering) or an enormous
    number (sleeping through the job). Falling back to the ladder is correct in
    both directions and needs no clock.
    """
    raw = ""
    try:
        raw = (headers or {}).get("Retry-After", "") or ""
    except AttributeError:
        raw = ""
    wait = 0.0
    try:
        wait = float(str(raw).strip())
    except (TypeError, ValueError):
        wait = 0.0
    if wait <= 0:
        wait = backoff * (2 ** attempt)
    return min(wait, cap)


def _status_of(result):
    response = result[0] if isinstance(result, tuple) else result
    return response, getattr(response, "status_code", None)


def _close(response) -> None:
    # A streamed response left open on a retry leaks a connection per attempt.
    try:
        response.close()
    except Exception:
        pass


def fetch(slot: str, call, *, attempts: int = DEFAULT_ATTEMPTS,
          cap: float = RETRY_AFTER_CAP_SECONDS, sleep=time.sleep):
    """`call()` until it is not rate limited. Returns whatever `call` returns.

    `call` must perform exactly ONE request. `slot` is what is lost if this
    gives up - an edition, a phrase, a page, a document URL - and it is the
    string a human reads in the health row, so make it identify the thing and
    not the function.

    A response or a `(response, body)` tuple are both understood, because half
    this codebase fetches through `capped_fetch.capped_get` and half through
    `requests.get` directly.
    """
    last_status = None
    for attempt in range(attempts):
        result = call()
        response, status = _status_of(result)
        if status not in RATE_LIMIT_STATUS:
            return result
        last_status = status
        if attempt == attempts - 1:
            _close(response)
            break
        wait = retry_after_seconds(getattr(response, "headers", None), attempt,
                                   cap=cap)
        _close(response)
        sleep(wait)

    detail = f"HTTP {last_status} after {attempts} attempt(s)"
    EXHAUSTED.append((slot, detail))
    print(f"  THROTTLED  {slot}: {detail}; not read, retried next run")
    raise RateLimited(f"{slot}: {detail}")


def record_throttled(slot: str, status) -> None:
    """Record a rate limit a caller handled itself, without raising.

    For the call sites whose correct behaviour on a 429 is to return empty
    rather than retry - `link_check` is explicit that answering "slow down"
    with "no" is wrong - but which must still not report the slot as read.
    """
    EXHAUSTED.append((slot, f"HTTP {status}"))
    print(f"  THROTTLED  {slot}: HTTP {status}; not read, retried next run")
