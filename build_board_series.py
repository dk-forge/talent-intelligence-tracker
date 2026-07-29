#!/usr/bin/env python3
"""Turn the recorded job-board counts into the series a company page renders.

    python3 build_board_series.py                 # write the shipped seed file
    python3 build_board_series.py --stdout        # look at it first
    python3 build_board_series.py --publish       # POST it to the live site

Derived, never collected: it reads `data/ats_board_state.json` — the archive the
ATS collector writes on every run — and writes
`wordpress-plugin/talent-intelligence-tracker/data/board_series.json`. It makes
no network call unless `--publish` is given, and it never touches the database.

Why a separate artefact rather than a column on a signal row:

* A signal row is an EVENT ("this board opened 40 more roles"). A trajectory is
  a SERIES, and squeezing one into the other would either flatten it to a
  sentence or duplicate the whole history onto every row.
* The profile page must be able to draw the line without going back to
  Greenhouse or Lever. Those APIs publish no history at all, so a page that
  re-fetched would only ever be able to draw today's dot.
* The count is OUR measurement, so the artefact carries the board URL that
  backs it and the rule the direction was decided by, per board. A number on a
  page with no source and no rule is exactly what this tracker exists not to be.

Dormant on purpose: nothing schedules it and nothing publishes it automatically.
The plugin reads the shipped file until somebody runs `--publish`, in the same
shape the recall page uses.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from collectors import ats_boards

REPO_ROOT = Path(__file__).resolve().parent
OUT_PATH = (REPO_ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
            / "data" / "board_series.json")

# How much of the archive travels to the site. The state file keeps ~2 years;
# a profile page draws a year at most, and the site copy is a single option
# blob, so the export is bounded here rather than growing without limit.
EXPORT_DAYS = 365

RULE = (
    "Each point is a count of the roles an employer had open on its own job "
    "board on that date, taken once a day. The direction is decided by a fixed "
    f"rule: a board is rising or falling only if it moved by at least "
    f"{ats_boards.TRAJECTORY_MIN_DELTA} roles AND at least "
    f"{ats_boards.TRAJECTORY_MIN_RELATIVE:.0%} over the window, across at "
    f"least {ats_boards.TRAJECTORY_MIN_OBSERVATIONS} readings spanning at "
    f"least {ats_boards.TRAJECTORY_MIN_SPAN_DAYS} days. Anything less says "
    "'we cannot tell', which is a real answer here. A rising board is evidence "
    "of hiring. " + ats_boards.FALLING_CAVEAT
)


def build(state: dict, *, today: str | None = None,
          export_days: int = EXPORT_DAYS,
          watchlist: list[dict] | None = None) -> dict:
    """The publishable shape: one entry per employer, keyed by company_key.

    Boards that are no longer on the watchlist are left out. The state file
    keeps them — it is the archive and nothing is deleted from it — but a
    withdrawn board's line would freeze on the day we stopped counting while
    still looking live, which is worse than not showing it.
    """
    day = today or datetime.now(timezone.utc).date().isoformat()
    cutoff = (datetime.strptime(day, "%Y-%m-%d").toordinal() - export_days)

    if watchlist is None:
        try:
            watchlist = ats_boards.load_watchlist()
        except ats_boards.BoardError:
            watchlist = []
    current = {f"{b['ats']}:{b['slug']}" for b in watchlist} if watchlist else None

    boards: dict[str, list[dict]] = {}
    for board_id, record in sorted((state.get("boards") or {}).items()):
        if current is not None and board_id not in current:
            continue
        history = [h for h in record.get("history") or []
                   if h.get("date") and h.get("total") is not None]
        if not history:
            continue
        series = [[h["date"], int(h["total"])] for h in history
                  if datetime.strptime(h["date"], "%Y-%m-%d").toordinal() >= cutoff]
        if not series:
            continue

        company = record.get("company") or board_id
        ats, _, slug = board_id.partition(":")
        ats = record.get("ats") or ats
        slug = record.get("slug") or slug

        key = record.get("company_key") or ""
        if not key:
            # An old state file predates the key being written. Derive it the
            # same way the store does rather than dropping the board.
            from pipeline import vocab
            key = vocab.company_key(company)

        # A state file written before the URL was recorded still has one: the
        # board address is a pure function of the ATS and the slug, and this is
        # the same formula the collector cites on every row. Derived, never
        # guessed — a board we cannot address is skipped rather than published
        # with a dead link, because an unsourced count is the one thing this
        # artefact may not contain.
        url = record.get("url") or (
            ats_boards.BOARD_URLS[ats].format(slug=slug)
            if ats in ats_boards.BOARD_URLS and slug else "")
        if not url:
            continue

        boards.setdefault(key, []).append({
            "company": company,
            "ats": ats,
            "source_name": (record.get("source_name")
                            or ats_boards.SOURCE_NAMES.get(ats, "")),
            # The source that makes the claim. Without it the series is an
            # assertion; with it a reader can go and count for themselves.
            "source_url": url,
            "first_seen": series[0][0],
            "latest": {"date": series[-1][0], "total": series[-1][1]},
            "trajectory": record.get("trajectory")
                          or ats_boards.trajectory(history, today=day),
            "series": series,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": day,
        "window_days": ats_boards.TRAJECTORY_WINDOW_DAYS,
        "rule": RULE,
        "employers": len(boards),
        "boards": boards,
    }


def publish(payload: dict) -> int:
    """POST the series to the keyed endpoint. Same contract as publish_health."""
    import requests

    from pipeline.publish import TIMEOUT, USER_AGENT, PublishError, _config

    site, key = _config()
    resp = requests.post(
        f"{site}/wp-json/talent/v1/board-series",
        json=payload,
        headers={"X-Talent-API-Key": key, "User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise PublishError(f"{resp.status_code}: {resp.text[:300]}")
    return payload["employers"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default=None, help="state file to read")
    parser.add_argument("--out", default=str(OUT_PATH))
    parser.add_argument("--stdout", action="store_true",
                        help="print the payload and write nothing")
    parser.add_argument("--publish", action="store_true",
                        help="POST to the site as well (needs WP_API_KEY)")
    args = parser.parse_args()

    state = ats_boards.load_state(Path(args.state) if args.state else None)
    payload = build(state)

    boards = sum(len(v) for v in payload["boards"].values())
    directions: dict[str, int] = {}
    for entries in payload["boards"].values():
        for entry in entries:
            direction = (entry["trajectory"] or {}).get("direction", "unknown")
            directions[direction] = directions.get(direction, 0) + 1

    if args.stdout:
        json.dump(payload, sys.stdout, indent=1)
        print()
    else:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
        print(f"{boards} boards, {payload['employers']} employers -> {out}")

    print("direction: " + ", ".join(f"{k}={v}" for k, v in sorted(directions.items())))
    if args.publish:
        if not os.environ.get("WP_API_KEY"):
            print("WP_API_KEY is not set — nothing was published.", file=sys.stderr)
            return 1
        print(f"published {publish(payload)} employers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
