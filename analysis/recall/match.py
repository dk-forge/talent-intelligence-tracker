"""Matching rules for the recall measurement. Pure functions, no network.

Everything that decides FOUND / PARTIAL / MISSED lives here so it can be
tested offline against fixtures, and so the rule a number was produced under is
reviewable rather than buried in a request loop.

Bias note, stated deliberately: where a rule could go either way it is written
to favour counting an event as held. We are publishing our own recall, so the
failure mode to guard against is overstating the misses to look humble, just as
much as understating them to look complete.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from analysis.recall import stats

# Same legal-suffix stripping the pipeline uses for `company_key`
# (pipeline/vocab.py). Duplicated rather than imported so this module stays a
# leaf with no pipeline import chain, and so a pipeline refactor cannot quietly
# change a published historical number.
_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|limited|plc|corp|corporation|co|pbc|lp|llp|gmbh|ag|sa|nv"
    r"|bv|ab|as|oy|spa|srl|pte|pty|holdings|group|technologies|labs)\b"
)
_STOPWORD_TOKENS = {"the", "and", "for"}

# How far either side of the announcement date a stored row may sit and still
# be the same event. Wide on purpose: a trade outlet often writes up a round
# days after the press release, and a filing can precede the news.
WINDOW_BEFORE_DAYS = 10
WINDOW_AFTER_DAYS = 21

# A stored funding figure this close to the gold figure is the same number.
# Tolerance exists because sources round ("$34 million" vs "$33.5 million") and
# because non-USD rounds are converted at slightly different rates.
AMOUNT_TOLERANCE = 0.08

PILLAR_FOR_SIGNAL = {
    "funding": "company_development",
    "leadership": "leadership_change",
}

# Where the same document legitimately lands under a different heading.
#
# An SEC 8-K Item 5.02 covers both "we changed an officer" and "here is what we
# are paying them", so a filing about a new chief financial officer can be
# classified as compensation. We do hold that event, but nobody browsing
# leadership changes will ever see it, which is a real defect and not the same
# thing as never having collected it. Counting it as a flat miss would overstate
# the gap; counting it as a clean hit would hide a fixable classification bug.
ADJACENT_PILLARS = {
    "leadership": ("rewards_comp",),
    "funding": ("rewards_comp", "leadership_change", "how_we_work"),
}

# An adjacent-pillar row only counts when its headline is visibly about the
# event in question. Otherwise any row for the same employer in the same month
# would be scored as a hit.
_LOOKS_LIKE_FUNDING = re.compile(
    r"\b(rais(e|ed|es|ing)|funding|seed round|series\s+[a-e]\b|pre-seed"
    r"|private placement|secures?\s+\$)", re.I)
_LOOKS_LIKE_LEADERSHIP = re.compile(
    r"\b(appoint|named|promot|succeed|steps? down|resign|departure|joins as"
    r"|item\s*5\.02|chief\s+\w+\s+officer|\bceo\b|\bcfo\b|\bcoo\b|\bcto\b"
    r"|board of directors)", re.I)

_LOOKS_LIKE = {"funding": _LOOKS_LIKE_FUNDING, "leadership": _LOOKS_LIKE_LEADERSHIP}


def company_key(name: str) -> str:
    """Normalised employer key, matching the pipeline's own normalisation."""
    k = (name or "").lower()
    k = re.sub(r"[^\w\s&-]", " ", k)
    k = _SUFFIXES.sub(" ", k)
    return re.sub(r"\s+", " ", k).strip()


def first_token(name: str) -> str:
    """The widest sane query term: the first distinctive word of the name.

    Used as a fallback query so that a stored 'Glow Security' is still reached
    when the gold set says 'Glow', and vice versa.
    """
    for word in company_key(name).split():
        if word not in _STOPWORD_TOKENS:
            return word
    return company_key(name)


def names_match(gold_name: str, row_name: str) -> bool:
    """True when two employer names plausibly denote the same employer.

    Whole-word containment in either direction. 'Glow' matches 'Glow Security',
    'Enigma Technologies' matches 'Enigma'. 'Glowforge' does not match 'Glow',
    because the test is on word boundaries, not substrings.
    """
    a, b = company_key(gold_name), company_key(row_name)
    if not a or not b:
        return False
    if a == b:
        return True
    long, short = (a, b) if len(a) >= len(b) else (b, a)
    return re.search(rf"(?<![\w]){re.escape(short)}(?![\w])", long) is not None


def _parse_date(value):
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def row_date(row: dict):
    """The date a stored row claims for its event.

    published_date first, then effective_date, then the capture date. A row
    with no date at all returns None and is treated as in-window: a missing
    date is a field defect, not evidence that the event is a different one.
    """
    for field in ("published_date", "effective_date", "captured_at"):
        parsed = _parse_date(row.get(field))
        if parsed:
            return parsed
    return None


def in_window(gold_date: date, row: dict) -> bool:
    stored = row_date(row)
    if stored is None:
        return True
    return (gold_date - timedelta(days=WINDOW_BEFORE_DAYS)
            <= stored
            <= gold_date + timedelta(days=WINDOW_AFTER_DAYS))


def _to_number(value):
    if value in (None, "", "null"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def candidates(gold: dict, rows: list) -> list:
    """Stored rows that could be this gold event: same employer, same kind of
    event, same time window."""
    signal = gold["signal_type"]
    wanted_pillar = PILLAR_FOR_SIGNAL[signal]
    adjacent = ADJACENT_PILLARS[signal]
    looks_right = _LOOKS_LIKE[signal]
    gold_date = _parse_date(gold["event_date"])
    out = []
    for row in rows:
        pillar = row.get("pillar") or ""
        if pillar != wanted_pillar:
            if pillar not in adjacent:
                continue
            text = f"{row.get('headline') or ''} {row.get('summary') or ''}"
            if not looks_right.search(text):
                continue
        if not names_match(gold["company"], row.get("company") or ""):
            continue
        if gold_date and not in_window(gold_date, row):
            continue
        out.append(row)
    return out


def field_defects(gold: dict, row: dict) -> list:
    """What is wrong with a row that IS the right event.

    These are the difference between 'we have it' and 'we have it right', and
    they are reported separately because they need different fixes: a miss
    needs a new source, a defect needs a better extractor.
    """
    defects = []

    if (row.get("pillar") or "") != PILLAR_FOR_SIGNAL[gold["signal_type"]]:
        # We hold the document but filed it under the wrong heading, so it is
        # absent from every view a reader would look in.
        defects.append("wrong_category")

    stored_country = (row.get("country") or row.get("hq_country") or "").upper()
    gold_country = (gold.get("country") or "").upper()
    if not stored_country:
        defects.append("country_missing")
    elif gold_country and stored_country != gold_country:
        defects.append("country_wrong")

    if gold["signal_type"] == "funding":
        gold_amount = _to_number(gold.get("amount_usd"))
        stored_amount = _to_number(row.get("funding_amount_usd"))
        if stored_amount is None:
            defects.append("amount_missing")
        elif gold_amount:
            spread = abs(stored_amount - gold_amount) / gold_amount
            if spread > AMOUNT_TOLERANCE:
                defects.append("amount_mismatch")

    if row_date(row) is None:
        defects.append("date_missing")

    if not (row.get("source_url") or "").startswith("http"):
        defects.append("source_url_missing")

    return defects


def classify(gold: dict, rows: list) -> dict:
    """FOUND / FOUND_PARTIAL / MISSED for one gold event.

    When several stored rows could be the event, the cleanest one decides the
    verdict. Holding the event twice, once well and once poorly, is a
    deduplication problem and not a recall failure.
    """
    matches = candidates(gold, rows)
    if not matches:
        return {"verdict": "MISSED", "defects": [], "matched_row": None}

    scored = sorted(matches, key=lambda r: len(field_defects(gold, r)))
    best = scored[0]
    defects = field_defects(gold, best)
    return {
        "verdict": "FOUND" if not defects else "FOUND_PARTIAL",
        "defects": defects,
        "matched_row": {
            "signal_id": best.get("signal_id"),
            "company": best.get("company"),
            "headline": best.get("headline"),
            "country": best.get("country"),
            "hq_country": best.get("hq_country"),
            "funding_amount_usd": best.get("funding_amount_usd"),
            "published_date": best.get("published_date"),
            "source_url": best.get("source_url"),
        },
    }


def rate(numerator: int, denominator: int):
    """A percentage that always travels with its counts, or None when there is
    nothing to divide. A bare percentage with no denominator is not a result."""
    if not denominator:
        return None
    return round(100.0 * numerator / denominator, 1)


def _bucket(items, key_fn):
    out = {}
    for item in items:
        out.setdefault(key_fn(item), []).append(item)
    return out


def summarise(results: list) -> dict:
    """Overall and per-cell recall. Every cell carries its raw counts.

    And, since the US family landed, its INTERVAL. A cell of 9 events and a cell
    of 90 print identically as percentages and mean entirely different things,
    and a breakdown is exactly the place a reader is invited to compare one
    against another. The interval is what stops "Austin is worse than New York"
    being read off two ranges that overlap almost completely.

    Computed for every family, including the worldwide one, because the
    argument for publishing it does not depend on which set produced the
    number. It is added beside the existing keys and never in place of one, so
    every historical result stays readable by the same code.
    """

    def cell(rows):
        found = sum(1 for r in rows if r["verdict"] == "FOUND")
        partial = sum(1 for r in rows if r["verdict"] == "FOUND_PARTIAL")
        missed = sum(1 for r in rows if r["verdict"] == "MISSED")
        total = len(rows)
        return {
            "total": total,
            "found": found,
            "found_partial": partial,
            "missed": missed,
            "held": found + partial,
            "held_pct": rate(found + partial, total),
            "clean_pct": rate(found, total),
            "held_interval": stats.interval(found + partial, total),
        }

    def breakdown(key_fn):
        return {k: cell(v) for k, v in sorted(_bucket(results, key_fn).items())}

    defect_counts = {}
    for r in results:
        for d in r["defects"]:
            defect_counts[d] = defect_counts.get(d, 0) + 1

    out = {
        "overall": cell(results),
        "by_signal_type": breakdown(lambda r: r["gold"]["signal_type"]),
        "by_geography": breakdown(
            lambda r: "US" if r["gold"]["country"] == "US" else "non-US"),
        "by_country": breakdown(lambda r: r["gold"]["country"]),
        "by_source_type": breakdown(lambda r: r["gold"]["source_type"]),
        "by_size_band": breakdown(lambda r: r["gold"]["size_band"]),
        "by_segment": breakdown(
            lambda r: f"{'US' if r['gold']['country'] == 'US' else 'non-US'}"
                      f" {r['gold']['signal_type']}"),
        "defects": dict(sorted(defect_counts.items(),
                               key=lambda kv: -kv[1])),
    }

    # Only when the reference set actually carries metros. A worldwide result
    # must not grow an empty `by_metro` key: the collapse gate reads the groups
    # a summary has, and an always-present empty group is a group that can never
    # collapse and can never be noticed missing.
    if any(r["gold"].get("metro") for r in results):
        out["by_metro"] = breakdown(lambda r: r["gold"].get("metro") or "unplaced")
        out["by_metro_segment"] = breakdown(
            lambda r: f"{r['gold'].get('metro') or 'unplaced'} "
                      f"{r['gold']['signal_type']}")
    return out
