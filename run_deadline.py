"""A collect run's own wall clock, and its answer to being killed.

TWO FAILURES, ONE SHAPE
-----------------------
`collect.yml` gives the run 180 minutes, `collect-press.yml` 120, and
`collect-structured.yml` 180. Those ceilings were added because an unbounded
job sits on a live `OPENROUTER_API_KEY` and on the single `talent-collect`
lock. They bound the hang. They do not bound the LOSS, and until this module
the run had no clock of its own at all.

When `timeout-minutes` fires, GitHub cancels the job. Every commit step in
this repo is guarded `if: ${{ !inputs.dry_run && !cancelled() }}` -- deliberately,
and correctly, for the failure case it was written for. But `cancelled()` is
true for a self-timeout, so the step is SKIPPED, and a self-timeout therefore
discards:

  * every signal the run stored (the database is committed at the end, never
    during),
  * every `seen_urls` mark, so the next run re-fetches and RE-PAYS for the
    same candidates,
  * and the health row, so nothing anywhere says the run happened. The
    collector simply goes quiet, and `staleness.py` notices a day later with
    no cause attached.

That is the sibling tracker's orphaned-`running`-note defect wearing different
clothes. There it was a note left open; here it is a run that leaves nothing
at all, which is the same lie in the other direction: absence read as "no
problem" rather than "we never looked".

SO THERE ARE TWO GUARDS HERE, AND THEY ARE NOT REDUNDANT
---------------------------------------------------------
1. **A DEADLINE the run enforces on itself**, strictly below the workflow's
   `timeout-minutes`. The candidate loop checks it between items and stops
   cleanly: unread candidates are not marked seen, the health row is written
   saying how many were left, `conn.commit()` runs, the process exits 0 and
   the commit step therefore RUNS. Nothing is lost; depth is. That is the
   same trade the budget guard already makes, and it is the only one of the
   two that saves the run's work.

2. **A SIGTERM handler**, for every way a run ends that its own clock cannot
   pre-empt: an eviction from the concurrency group, a superseded push, a
   human pressing cancel, or a deadline that was itself missed because one
   HTTP call hung inside its timeout. The handler cannot rescue the git
   commit -- the step is skipped either way -- but it CAN write the terminal
   health row into the local database, and, crucially, say WHY the run ended.
   It is the difference between "this collector went quiet" and "this
   collector was killed at 17:58 after 94 minutes".

WHY THE HANDLER OPENS ITS OWN CONNECTION
-----------------------------------------
A signal handler runs on the main thread between bytecodes, so it can land
while the run's own connection is mid-statement, and re-entering that
connection is undefined. It opens a second connection with a short busy
timeout instead. If the run happened to be holding a write lock the note
cannot land, and that is reported on stdout rather than retried: a bookkeeper
that hangs inside a grace window is killed with the note unsent, which is the
state it exists to prevent. GitHub's grace after SIGTERM is about 7.5s.

The handler then hands the signal back to the platform's default disposition,
so the exit is still killed-by-SIGTERM (143 by convention) and nothing
downstream sees a run that pretended to succeed.

Stdlib only.
"""
from __future__ import annotations

import functools
import os
import signal
import threading
import time

#: The clock a `collect`-family run gives itself, in seconds. Deliberately a
#: single number rather than one per workflow: it must be strictly below the
#: SMALLEST `timeout-minutes` of every workflow that invokes run_collect.py,
#: which is `collect-press.yml` at 120 minutes. 100 minutes leaves 20 for the
#: pip install, the spend check, ops_status and the multi-attempt merge-and-
#: push, all of which happen OUTSIDE this clock and inside that ceiling.
#: `tests/test_run_deadline.py` re-derives the comparison from the workflow
#: files, so raising a ceiling without re-reading this number goes red.
#:
#: DO NOT ANSWER A DEADLINE-STOPPED RUN BY RAISING THIS. A run that needs more
#: than 100 minutes needs a smaller slice, and the stop is already reported as
#: depth lost rather than as a failure. Raising it walks back toward the
#: silent-loss state, because the workflow ceiling does not move with it.
DEFAULT_BUDGET_SECONDS = 100 * 60

#: Environment override, for a dispatch that genuinely wants a shorter clock
#: (a rehearsal, a one-source run). Only ever read here.
ENV_VAR = "TIT_RUN_BUDGET_SECONDS"

INTERRUPTED_DETAIL = ("interrupted: {signame} after {elapsed:.0f}s "
                      "(job cancelled, evicted or timed out)")

#: How long the whole interrupt handler may take. Well inside GitHub's ~7.5s
#: grace, with room for the process to exit afterwards.
INTERRUPT_BUDGET_SECONDS = 4.0
INTERRUPT_DB_TIMEOUT_SECONDS = 2.0


def budget_seconds() -> float:
    """This run's clock. An unreadable override is the default, not zero."""
    raw = os.environ.get(ENV_VAR, "")
    if raw:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            print(f"run-deadline: {ENV_VAR}={raw!r} is not a number; "
                  f"using {DEFAULT_BUDGET_SECONDS}s")
            return float(DEFAULT_BUDGET_SECONDS)
        if value > 0:
            return value
        print(f"run-deadline: {ENV_VAR}={raw!r} is not positive; "
              f"using {DEFAULT_BUDGET_SECONDS}s")
    return float(DEFAULT_BUDGET_SECONDS)


class Deadline:
    """A monotonic wall clock. `clock` is injectable so a test needs no sleep."""

    def __init__(self, seconds: float | None = None, clock=time.monotonic):
        self.seconds = float(budget_seconds() if seconds is None else seconds)
        self._clock = clock
        self.started = clock()

    def elapsed(self) -> float:
        return self._clock() - self.started

    def remaining(self) -> float:
        return self.seconds - self.elapsed()

    def expired(self) -> bool:
        return self.remaining() <= 0

    def detail(self, unread: int) -> str:
        return (f"DEADLINE: stopped after {self.elapsed():.0f}s of a "
                f"{self.seconds:.0f}s run budget with {unread} candidate(s) "
                f"unread; they are NOT marked seen and are read next run")


# -- the interrupt handler ---------------------------------------------------

_lock = threading.RLock()          # reentrant: the handler runs on this thread
_open: dict[str, object] = {}      # collector -> the note-writer for it
_installed = False


def open_runs() -> dict:
    with _lock:
        return dict(_open)


def mark_open(collector: str, writer) -> None:
    """Declare that `collector` is running, and how to close its note.

    `writer(detail)` must write ONE terminal health row and commit it, using
    its own database connection. It is called from a signal handler.
    """
    with _lock:
        _open[collector] = writer
    _install()


def mark_closed(collector: str) -> None:
    with _lock:
        _open.pop(collector, None)


def close_registrations() -> None:
    """Drop every open registration without writing anything."""
    with _lock:
        _open.clear()


def released(fn):
    """Whatever happens inside, the run's registration is dropped on the way out.

    A registration that outlives its run is an "interrupted" note written
    about a collector that already finished, and `run()` has half a dozen
    early returns (a failed fetch, a bad key, an exhausted balance, an
    exception nobody anticipated). Asking each of them to remember is how one
    of them does not: a leaked `google_news` registration is exactly what a
    full-suite run turned up, from a test that ran the pipeline and returned
    early. A decorator cannot forget.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            close_registrations()
    return wrapper


def _install() -> None:
    global _installed
    if _installed:
        return
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_interrupt)
        except (ValueError, OSError) as exc:
            # Not the main thread, or a platform that refuses. Say so once;
            # the collector still runs, it just cannot answer a kill.
            print(f"run-deadline: could not install a {sig!r} handler ({exc}); "
                  f"an interrupted run will record nothing")
            return
    _installed = True


def close_open_runs_as_interrupted(signame: str, elapsed: float) -> list[str]:
    """Write the terminal note for every open run. Idempotent BY THE DRAIN:
    the set is emptied under the lock before any write, so a second signal
    finds nothing, writes nothing and cannot block the exit."""
    with _lock:
        pending = list(_open.items())
        _open.clear()
    deadline = time.monotonic() + INTERRUPT_BUDGET_SECONDS
    closed = []
    detail = INTERRUPTED_DETAIL.format(signame=signame, elapsed=elapsed)
    for collector, writer in pending:
        if time.monotonic() >= deadline:
            print(f"run-deadline: interrupt budget spent before {collector} "
                  f"could be closed; it records nothing")
            continue
        try:
            writer(detail)
            print(f"run-deadline: {collector} closed as degraded ({detail})")
            closed.append(collector)
        except Exception as exc:
            print(f"run-deadline: could not close {collector} ({exc}); "
                  f"exiting anyway")
    return closed


_started_at = time.monotonic()


def _on_interrupt(signum, frame):
    try:
        signame = signal.Signals(signum).name
    except Exception:
        signame = f"signal {signum}"
    try:
        close_open_runs_as_interrupted(signame, time.monotonic() - _started_at)
    except Exception as exc:
        # A bookkeeper that crashes while recording a kill has recorded
        # nothing; never let it also hide the kill.
        print(f"run-deadline: interrupt handler failed ({exc}); exiting anyway")
    _reraise(signum)


def _reraise(signum) -> None:
    """Hand the signal back to the platform's default disposition, so a runner
    reads the conventional 128+N (143 for SIGTERM) rather than a run that
    pretended to finish."""
    try:
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    except Exception:
        pass
    os._exit(128 + int(signum))
