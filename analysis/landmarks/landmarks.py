"""Load and validate the committed landmark set. Stdlib only, no network.

The set is DATA, so that adding next quarter's rounds is an edit to a JSON
file and not a code change. It is validated in code, so that the properties
which make it worth checking against cannot be eroded by an edit: an empty
file, an entry with no primary source, a duplicate, or a quarter label that
disagrees with its own date are all validation failures rather than a quietly
smaller measurement.

The floors below are the anti-erosion guard, and they are the same idea as
`analysis/recall/goldset.REQUIRED_SHAPE`: the cheapest way to make a landmark
check look good is to remove the landmarks it fails. So removing entries is a
red test, and it should be.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_PATH = os.path.join(ROOT, "data", "landmarks.json")

# The shape any landmark set must have.
#
# MIN_ENTRIES is set AT the size of the set shipped on 2026-08-04, not below
# it, deliberately. A floor with slack under it is a floor nobody notices
# sliding. Adding a quarter raises the real count and nothing here has to
# change; removing an entry is a failing test and a decision somebody has to
# defend in a commit message.
MIN_ENTRIES = 20
MIN_QUARTERS = 6
MIN_COMPANIES = 10

SOURCE_KINDS = {
    # The company's own announcement page or press release. The default, and
    # what every entry in the shipped set uses.
    "company_announcement",
    # A regulator's copy: an SEC filing, a Companies House return. Equally
    # primary, and for some employers the only public document that exists.
    "regulator_filing",
    # A publisher, allowed ONLY where the company published nothing itself.
    # Recorded explicitly so the file cannot drift into being a press digest
    # without anybody deciding that it should.
    "publisher",
}

_QUARTER = re.compile(r"^(20\d\d)Q([1-4])$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{4,}$")


class InvalidLandmarkSet(ValueError):
    """The set on disk cannot be measured against. Never a soft warning."""


def load(path: str = DEFAULT_PATH) -> dict:
    """Read and validate. Raises rather than returning a degraded set.

    A landmark check that runs against a broken file and reports a number is
    worse than one that refuses: the number looks like a measurement.
    """
    if not os.path.exists(path):
        raise InvalidLandmarkSet(f"no landmark set at {path}")
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except ValueError as exc:
            raise InvalidLandmarkSet(f"{path} is not readable JSON: {exc}") from exc

    problems = validate(data)
    if problems:
        raise InvalidLandmarkSet(
            "%s is not a usable landmark set:\n  - %s"
            % (path, "\n  - ".join(problems)))
    return data


def entries(data: dict) -> list:
    return list(data.get("entries") or [])


def digest(path: str = DEFAULT_PATH) -> str:
    """Content hash of the set, recorded beside every result.

    A verdict that cannot be tied to the exact list it was measured against is
    not re-derivable, and the temptation to edit an entry after a bad week is
    exactly what this makes visible.
    """
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:16]


def quarter_of(day: str) -> str:
    """The calendar quarter a date falls in, as 2026Q1."""
    parsed = parse_date(day)
    if parsed is None:
        raise InvalidLandmarkSet(f"not a date: {day!r}")
    return "%dQ%d" % (parsed.year, (parsed.month - 1) // 3 + 1)


def parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def names(entry: dict) -> list:
    """Every name this employer might be stored under.

    Cursor is stored by some outlets as Anysphere and by others as Cursor, and
    an entry that only knows one of them reports a MISSING we do hold. The
    aliases are a FIXED vocabulary per entry, never a fuzzy expansion: this
    project's rule is that a value which will not normalise is a rejected
    record, not a new category.
    """
    out = [entry.get("company") or ""]
    out += [str(a) for a in (entry.get("aliases") or [])]
    return [n for n in out if n]


def validate(data: dict) -> list:
    """Every reason this set may not be measured against. Empty list is valid."""
    problems = []

    if not isinstance(data, dict):
        return ["the landmark set is not a JSON object"]

    items = data.get("entries")
    if not isinstance(items, list):
        return ["the landmark set has no 'entries' list"]
    if not items:
        # Stated separately from the count floor because it is the failure the
        # guard is most likely to be defeated by: a file emptied by a bad merge
        # or a truncating write reports "0 of 0 held, no regressions" and
        # exits 0 forever.
        return ["the landmark set is EMPTY. A check against nothing passes "
                "every week and means nothing."]

    if len(items) < MIN_ENTRIES:
        problems.append(
            "only %d entries; the floor is %d. Entries are added when a "
            "quarter closes and are not removed to make a number look better."
            % (len(items), MIN_ENTRIES))

    seen_ids, seen_events = set(), set()
    quarters, companies = set(), set()

    for index, entry in enumerate(items):
        where = entry.get("id") or "entry %d" % index

        ident = entry.get("id") or ""
        if not _ID.match(ident):
            problems.append("%s: id must be a lowercase slug" % where)
        if ident in seen_ids:
            problems.append("%s: duplicate id" % where)
        seen_ids.add(ident)

        company = (entry.get("company") or "").strip()
        if not company:
            problems.append("%s: no company" % where)
        companies.add(company.lower())

        day = parse_date(entry.get("event_date"))
        if day is None:
            problems.append("%s: event_date is not YYYY-MM-DD" % where)
        else:
            quarter = entry.get("quarter")
            if not _QUARTER.match(str(quarter or "")):
                problems.append("%s: quarter must look like 2026Q1" % where)
            elif quarter != quarter_of(entry["event_date"]):
                # Caught a real class of hand-edit: an entry copied from the
                # quarter above and re-dated, keeping the old label, which then
                # reports a whole quarter as covered by an event in another one.
                problems.append(
                    "%s: quarter %s does not match event_date %s (%s)"
                    % (where, quarter, entry["event_date"],
                       quarter_of(entry["event_date"])))
            quarters.add(entry.get("quarter"))

        amount = entry.get("amount_usd")
        if not isinstance(amount, (int, float)) or amount <= 0:
            problems.append("%s: amount_usd must be a positive number" % where)

        url = str(entry.get("source_url") or "")
        if not url.startswith("https://"):
            # Same rule as the pipeline's: no source URL, no record. A landmark
            # with no document is a rumour we would be scoring ourselves on.
            problems.append("%s: source_url must be an https URL" % where)

        kind = entry.get("source_kind")
        if kind not in SOURCE_KINDS:
            problems.append(
                "%s: source_kind %r is not one of %s"
                % (where, kind, ", ".join(sorted(SOURCE_KINDS))))

        key = (company.lower(), str(entry.get("event_date")))
        if key in seen_events:
            problems.append("%s: duplicate event (same company, same date)" % where)
        seen_events.add(key)

    if len(quarters) < MIN_QUARTERS:
        problems.append(
            "only %d quarters covered; the floor is %d. A set concentrated in "
            "one quarter measures one quarter." % (len(quarters), MIN_QUARTERS))
    if len(companies) < MIN_COMPANIES:
        problems.append(
            "only %d distinct employers; the floor is %d. A guard over four "
            "frontier labs watches four employers."
            % (len(companies), MIN_COMPANIES))

    return problems
