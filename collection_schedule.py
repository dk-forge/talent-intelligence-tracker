#!/usr/bin/env python3
"""What is SUPPOSED to run, read from the workflows, and what to do when it didn't.

WHY (coverage audit 2026-09-24, self-heal slice)
------------------------------------------------
Three silences, one cause: every health check here judged the collectors that
had a `source_health` row, so a source that never ran had nothing to judge.

* `singapore_acra` (7th of the month) has never recorded a run. No row, so
  staleness could not call it stale.
* EDINET missed its 2026-09-22 slot and DART its 09-23 slot while the
  self-hosted runner was down. Nothing re-runs a missed weekly slot; the next
  chance was a week later.
* google_news stored ZERO rows for five days while the run went red for an
  unrelated reason. A zero-store streak on any source other than the three
  PRIMARY_COLLECTORS was not checked at all.

So the list of sources is read from the workflow files -- the crons that
actually run -- not from the health table and not from a second hand-kept
list. collect-structured.yml's `Pick the source` case map, collect.yml's sweep
loop and collect-press.yml together ARE the configuration.

Stdlib-only, like staleness.py and ops_status.py, so it runs before any venv.

    python3 collection_schedule.py --plan          # what a catch-up would run
    python3 collection_schedule.py --never-ran     # configured, no row ever
    python3 collection_schedule.py --enqueue       # catch-up tickets -> writer queue
    python3 collection_schedule.py --issues        # sync zero-store issues
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOWS = ROOT / ".github" / "workflows"
DB = ROOT / "data" / "talent_intel.db"

STRUCTURED = "collect-structured.yml"

#: A missed slot is a slot older than its cadence plus this much. One day: a
#: weekly job that ran 7d 23h ago is late, not missing; at 8 days it is gone.
CATCH_UP_SLACK_HOURS = 24

#: At most this many catch-up dispatches a day. Each is a run inside the
#: single `talent-collect` writer lock, so a flood would evict the daily runs.
CATCH_UP_LIMIT = 2

#: Consecutive runs storing nothing before a source gets an issue. Runs, not
#: days: three weekly runs is three weeks of a registry giving nothing.
ZERO_STORE_RUNS = 3


@dataclass(frozen=True)
class Source:
    name: str
    workflow: str
    cron: str
    cadence_hours: int


_CRON_LINE = re.compile(r"^\s*-\s*cron:\s*['\"]([^'\"]+)['\"]")


def crons(workflow: str) -> list[str]:
    """Uncommented cron expressions in one workflow (a `#` line never counts)."""
    path = WORKFLOWS / workflow
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        m = _CRON_LINE.match(line)
        if m:
            out.append(m.group(1))
    return out


def cadence_hours(cron: str) -> int:
    """Daily, weekly or monthly, from the cron's day fields."""
    _m, _h, dom, _mon, dow = cron.split()
    if dom != "*":
        return 24 * 31
    if dow != "*":
        return 24 * 7
    return 24


def configured_sources() -> dict[str, Source]:
    """Every source a live cron runs, keyed by collector name."""
    out: dict[str, Source] = {}

    # collect.yml: the scheduled sweep loop names its sources.
    text = (WORKFLOWS / "collect.yml").read_text()
    m = re.search(r"for source in ([\w ]+); do", text)
    for cron in crons("collect.yml")[:1]:
        for name in (m.group(1).split() if m else []):
            out[name] = Source(name, "collect.yml", cron, cadence_hours(cron))

    for cron in crons("collect-press.yml")[:1]:
        out["national_press"] = Source("national_press", "collect-press.yml",
                                       cron, cadence_hours(cron))

    # collect-structured.yml: the case map pairs each cron with its source,
    # and `*)` names the one every other (daily) cron runs.
    text = (WORKFLOWS / STRUCTURED).read_text()
    mapped = dict(re.findall(r"'([^']+)'\)\s+scheduled=(\w+)", text))
    default = re.search(r"\*\)\s+scheduled=(\w+)", text)
    for cron in crons(STRUCTURED):
        name = mapped.get(cron) or (default.group(1) if default else None)
        if name and name not in out:
            out[name] = Source(name, STRUCTURED, cron, cadence_hours(cron))
    return out


def _last_runs(conn) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute(
        "SELECT collector, MAX(run_at) FROM source_health GROUP BY collector")}


def _parse(ts: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def never_ran(conn) -> list[str]:
    """Configured sources with no health row at all."""
    last = _last_runs(conn)
    return sorted(n for n in configured_sources() if n not in last)


def catch_up_plan(conn, *, now: datetime | None = None,
                  limit: int = CATCH_UP_LIMIT) -> list[str]:
    """Structured weekly/monthly sources whose slot was missed, to re-run now.

    Daily sources are excluded: tomorrow's run is their catch-up, and a
    perishable daily feed (ats_boards) cannot be back-filled anyway.
    """
    now = now or datetime.now(timezone.utc)
    last = _last_runs(conn)
    due = []
    for s in configured_sources().values():
        if s.workflow != STRUCTURED or s.cadence_hours <= 24:
            continue
        when = _parse(last[s.name]) if s.name in last else None
        age = (now - when).total_seconds() / 3600 if when else float("inf")
        if age > s.cadence_hours + CATCH_UP_SLACK_HOURS:
            due.append((s.cadence_hours, -min(age, 1e9), s.name))
    due.sort()
    return [name for _c, _a, name in due[:limit]]


def zero_store_sources(conn, *, runs: int = ZERO_STORE_RUNS) -> dict[str, int]:
    """Configured sources whose last `runs` runs all stored nothing."""
    out = {}
    for name in configured_sources():
        rows = conn.execute(
            "SELECT items_stored FROM source_health WHERE collector = ? "
            "ORDER BY run_at DESC LIMIT ?", (name, runs)).fetchall()
        if len(rows) >= runs and all(not (r[0] or 0) for r in rows):
            out[name] = len(rows)
    return out


def issue_marker(source: str) -> str:
    return f"<!-- zero-store:{source} -->"


def sync_zero_store_issues(streaks: dict[str, int], sources, *, repo: str,
                           gh=None) -> list[str]:
    """One self-closing GitHub issue per source storing nothing.

    Opening mails the owner once; updating the body mails nobody; the close on
    recovery is the second and last mail (gh_fallback.py explains why an issue
    and not a red run). Never raises: this runs from a health job.
    """
    if gh is None:
        import gh_fallback as gh
    notes = []
    for name in sources:
        marker = issue_marker(name)
        if name in streaks:
            ok, note = gh.open_or_update(
                repo, marker=marker,
                title=f"{name} has stored 0 rows on {streaks[name]} consecutive runs",
                line=f"- {datetime.now(timezone.utc):%Y-%m-%d}: last "
                     f"{streaks[name]} runs stored 0. Fix steps: "
                     f"docs/RUNBOOK_COLLECTION.md, section `{name}`.",
                preamble=(f"`{name}` ran but stored nothing on {streaks[name]} "
                          "consecutive runs. That is an outage until shown "
                          "otherwise (google_news stored 0 for five days in "
                          "September 2026 behind an unrelated red)."),
                what=f"{name} stores a row")
        else:
            number, _body, _n = gh.find_open(repo, marker=marker)
            if number is None:
                continue
            ok, note = gh.close(repo, marker=marker,
                                note=f"{name} stored rows again; closing.")
        notes.append(f"{name}: {note}")
    return notes


def enqueue_catch_up(queue: dict, plan, *, now: datetime | None = None) -> list[str]:
    """Write one writer-queue ticket per missed source; return those added.

    Never a direct `gh workflow run`: GitHub keeps ONE pending run per
    concurrency group, and a direct dispatch into `talent-collect` can silently
    displace the run already waiting (writer_queue.py, 2026-07-29). The
    drainer dispatches tickets one at a time when the lock is free. A source
    that already has a live ticket is not queued twice.
    """
    from datetime import timedelta

    import writer_queue

    now = now or datetime.now(timezone.utc)
    live = {(t.get("inputs") or {}).get("source")
            for t in queue.get("tickets", [])
            if t.get("workflow") == STRUCTURED
            and t.get("state") not in writer_queue.TERMINAL_STATES}
    added = []
    for i, name in enumerate(plan):
        if name in live:
            continue
        writer_queue.enqueue(queue, STRUCTURED, {"source": name},
                             reason=f"catch-up: {name} missed its scheduled slot",
                             requested_by="catch-up",
                             now=now + timedelta(seconds=i))
        live.add(name)
        added.append(name)
    return added


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--never-ran", action="store_true")
    ap.add_argument("--enqueue", action="store_true",
                    help="write writer-queue tickets for the catch-up plan")
    ap.add_argument("--issues", action="store_true")
    ap.add_argument("--repo", default="dk-forge/talent-intelligence-tracker")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rc = 0
    if args.never_ran or not (args.plan or args.enqueue or args.issues):
        missing = never_ran(conn)
        print("never ran: " + (", ".join(missing) or "none"))
    plan = catch_up_plan(conn)
    if args.plan or args.enqueue:
        print("catch-up plan: " + (", ".join(plan) or "nothing missed"))
    if args.enqueue:
        import writer_queue
        queue = writer_queue.load()
        added = enqueue_catch_up(queue, plan)
        writer_queue.save(queue)
        print("queued: " + (", ".join(added) or "nothing new"))
    if args.issues:
        streaks = zero_store_sources(conn)
        print("zero-store streaks: " + (", ".join(f"{k}={v}" for k, v in streaks.items())
                                        or "none"))
        for line in sync_zero_store_issues(streaks, configured_sources(), repo=args.repo):
            print("  " + line)
    return rc


if __name__ == "__main__":
    sys.exit(main())
