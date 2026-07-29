"""Diff the leads against what we actually hold.

Only a genuinely missing item is interesting. A work list full of things we
already have under a slightly different name is a work list nobody reads, and
worse, it makes the chase pay to re-find records we already stored.

So the matching is deliberately GENEROUS, and the bias is stated rather than
discovered later: where a rule could go either way it is written to call the
lead HELD. Under-reporting misses costs us a lead we might have chased.
Over-reporting them costs money on every run, pollutes the per-country miss
counts the health machinery reads, and would make the tripwire look productive
precisely when it is wrong.

The employer-name rule is imported from analysis.recall.match rather than
rewritten, so "is this the same employer?" has exactly one answer in this repo
and the discovery side cannot silently drift from the measurement side.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from analysis.recall.match import company_key, first_token, names_match

# Wider than the recall window (10 before / 21 after) on purpose. There the
# date came from a researched gold set; here it is the model's recollection,
# which is the field it gets wrong most often. A date two months out is not
# evidence that we are looking at a different event.
DATE_SLACK_DAYS = 60

HELD = "HELD"
MISSING = "MISSING"
UNUSABLE = "UNUSABLE"


def load_index(conn: sqlite3.Connection) -> list[dict]:
    """Everything current we hold, reduced to what the diff needs.

    Read in one pass and matched in memory: the table is thousands of rows, not
    millions, and a single scan beats one LIKE query per lead both in time and
    in how easy it is to reason about.
    """
    rows = conn.execute(
        """
        SELECT signal_id, company, company_key, pillar, country, hq_country,
               published_date, effective_date, captured_at, source_url,
               source_name, headline
          FROM signals
         WHERE is_current = 1
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _parse(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _row_date(row: dict) -> date | None:
    for field in ("published_date", "effective_date", "captured_at"):
        parsed = _parse(row.get(field))
        if parsed:
            return parsed
    return None


def _in_window(claimed: date | None, row: dict) -> bool:
    """A missing date on either side means in-window. A date we cannot compare
    is not evidence of a different event."""
    if claimed is None:
        return True
    stored = _row_date(row)
    if stored is None:
        return True
    return (claimed - timedelta(days=DATE_SLACK_DAYS)
            <= stored
            <= claimed + timedelta(days=DATE_SLACK_DAYS))


def usable(lead: dict) -> bool:
    """A lead we can act on names an employer. Nothing else is required: the
    chase searches by name, so a lead with no URL is still chaseable and a lead
    with a URL but a vague name is not."""
    name = (lead.get("claimed_company") or "").strip()
    if len(name) < 2 or not company_key(name):
        return False
    # A description is not a name. The classifier's prompt already refuses
    # these; the tripwire has to refuse them too or they become permanent
    # residents of the work list.
    lowered = name.lower()
    return not any(word in lowered for word in
                   ("undisclosed", "a major ", "the company", "several ", "various "))


def verdict(lead: dict, index: list[dict]) -> dict:
    """HELD, MISSING or UNUSABLE for one lead, with the row that decided it."""
    if not usable(lead):
        return {"verdict": UNUSABLE, "matched": None,
                "why": "no usable employer name in the lead"}

    claimed_date = _parse(lead.get("claimed_event_date"))
    name = lead["claimed_company"]
    token = first_token(name)

    for row in index:
        stored_name = row.get("company") or ""
        if not stored_name:
            continue
        # Cheap reject first: the token test is a substring check on a key we
        # already have, and it throws away almost every row before the
        # word-boundary rule has to run.
        if token and token not in (row.get("company_key") or company_key(stored_name)):
            continue
        if not names_match(name, stored_name):
            continue
        if not _in_window(claimed_date, row):
            continue
        return {
            "verdict": HELD,
            "matched": {
                "signal_id": row.get("signal_id"),
                "company": stored_name,
                "pillar": row.get("pillar"),
                "country": row.get("country") or row.get("hq_country"),
                "published_date": row.get("published_date"),
                "source_url": row.get("source_url"),
            },
            "why": "we already hold this employer inside the date window",
        }

    return {"verdict": MISSING, "matched": None,
            "why": "no row for this employer in the window"}


def dedupe(leads: list[dict]) -> tuple[list[dict], int]:
    """One entry per employer per run.

    A country query and the industry sweep will both surface the same big round,
    and a work list that names it twice is a chase that pays twice to find the
    same article. First mention wins, so the more specific query (countries are
    asked first) keeps its provenance.
    """
    out, seen = [], set()
    dropped = 0
    for lead in leads:
        key = (company_key(lead.get("claimed_company") or ""),
               lead.get("claimed_signal_type") or "")
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(lead)
    return out, dropped


def run(leads: list[dict], index: list[dict]) -> list[dict]:
    """Every lead, with its verdict folded in. Order is preserved so the work
    list reads in the order the queries were asked."""
    out = []
    for lead in leads:
        entry = dict(lead)
        entry.update(verdict(lead, index))
        out.append(entry)
    return out
