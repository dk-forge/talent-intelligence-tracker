"""Public trust tiers: verified / reported / measured.

Owner-approved 2026-10-11, shared in meaning with the AI Layoff Tracker. The
stored `confidence` column (verified/reported/rumored) is unchanged; a tier is
DERIVED from it plus who published the item, so nothing stored moves.

  verified  an official or primary filed document (host in
            vocab.PRIMARY_SOURCE_DOMAINS and confidence == 'verified').
  reported  news or press. A stored 'rumored' row is reported with the sub-flag
            rumored=True; it is never promoted.
  measured  our own reading of a job board (the ats_boards collector, or a
            source named "... job board"). A measured item is never an
            announced hiring event: count_meaning files it as OPEN_VACANCIES.

Two guards are deliberate. A news host can never come out `verified`, even if
a stored row somehow says so, because validate.infer_confidence is the write
guard and this is the read guard. And job-board wins over confidence, so a
board reading can never be shown as a filing or a news report.

See docs/VERIFICATION_TIERS.md.
"""

from __future__ import annotations

import re
from typing import NamedTuple
from urllib.parse import urlparse

from pipeline import vocab

TIERS = ("verified", "reported", "measured")
TIER_LABELS = {"verified": "Verified", "reported": "Reported",
               "measured": "Measured"}

#: Collectors whose items are our measurement of a job board.
MEASURED_COLLECTORS = frozenset({"ats_boards"})
_JOB_BOARD_NAME = re.compile(r"\bjob board\b", re.IGNORECASE)


class Tier(NamedTuple):
    tier: str
    rumored: bool


def _is_measured(collector: str | None, source_name: str | None) -> bool:
    if (collector or "") in MEASURED_COLLECTORS:
        return True
    return bool(_JOB_BOARD_NAME.search(source_name or ""))


def _is_primary_host(source_url: str | None) -> bool:
    host = (urlparse(source_url or "").hostname or "").lower()
    return host in vocab.PRIMARY_SOURCE_DOMAINS


def tier_for(confidence: str | None, source_url: str | None = None,
             collector: str | None = None,
             source_name: str | None = None) -> Tier:
    """Map one stored row's fields to its public tier. Pure, no I/O."""
    if _is_measured(collector, source_name):
        return Tier("measured", False)
    conf = (confidence or "").strip().lower()
    if conf == "verified" and _is_primary_host(source_url):
        return Tier("verified", False)
    return Tier("reported", conf == "rumored")


def tier_of_row(row) -> Tier:
    """`tier_for` over anything indexable by column name; missing keys are None."""
    def g(key):
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return None
    return tier_for(g("confidence"), g("source_url"), g("collector"),
                    g("source_name"))
