"""Dedup layers 1 and 2 (spec 7).

One acquisition reported by forty outlets must become one record. Layer 3 (a
bounded, rotating LLM deep scan for the pairs these miss) is a scheduled job,
not part of the write path.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta

# Same company + same pillar inside this window is the same development.
SAME_EVENT_DAYS = 14

# Spec 7 lesson (a): a flat window misses a re-report of the same event months
# later, so a near-identical headline gets a much wider window.
NEAR_IDENTICAL_DAYS = 400


def exact_duplicate(conn: sqlite3.Connection, content_hash: str) -> str | None:
    """Return why this hash is already known, or None.

    Deliberately ignores is_current. A retracted record is still a record we
    have judged and withdrawn, and re-storing it would silently undo the
    retraction — which is how the WWT homepage-sourced row came back. The
    unique index spans all revisions, so checking only current rows also
    crashed the run with an IntegrityError instead of skipping.
    """
    row = conn.execute(
        "SELECT is_current, notes FROM signals WHERE content_hash = ? LIMIT 1",
        (content_hash,),
    ).fetchone()
    if row is None:
        return None
    if row["is_current"]:
        return "duplicate"
    return "retracted"


#: Two stated amounts this close together are one round rounded two ways
#: ("$29.9bn" and "$30bn"); further apart than this and they are two claims.
AMOUNT_TOLERANCE = 0.02


def _same_amount_claim(a: int | None, b: int | None) -> bool | None:
    """True: one figure. False: two different figures. None: no claim to compare.

    Split out so the window rule below can say what it means. `None` is not a
    third kind of match - it is the ordinary case, where neither row states a
    parsed figure at all, which is every leadership row and every acquisition.
    """
    if a is None and b is None:
        return None
    if a is None or b is None:
        return False
    if a == b:
        return True
    return abs(a - b) <= AMOUNT_TOLERANCE * max(a, b)


def fuzzy_duplicate(conn: sqlite3.Connection, signal) -> str | None:
    """Return the signal_id of an existing record this duplicates, or None.

    THE WINDOW IS NOT ENOUGH ON ITS OWN WHEN MONEY IS STATED (2026-08-04).

    The 14-day employer+pillar window says "one company, one pillar, one
    fortnight, therefore one development", and for a leadership change or an
    acquisition that is true. It is not true of funding in 2026: the employers
    this product is largest in announce a round and a valuation update inside
    the same fortnight, and OpenAI and Anthropic each did it more than once.

    Measured against the live database on that date: a correctly-quantified
    'OpenAI raises $110 billion at $730 billion valuation' dated 2026-02-24 was
    suppressed by 'OpenAI capta 93.175 millones en una ronda récord' - a
    Spanish row whose amount never parsed, published three days later. Same
    company, same pillar, thirteen days apart, so the window collapsed them and
    the survivor was the one that states no dollars at all. That row is still
    the only OpenAI funding row on the live site.

    So a window row that makes a DIFFERENT amount claim is no longer a same-
    event match. It falls through to the near-identical-headline test below,
    which still collapses an outlet rewrite of one story, and otherwise the
    two are stored as the two developments they are. Where neither row states
    a parsed figure the rule is exactly what it always was, so nothing outside
    funding changes at all.

    A false MERGE is silent and permanent; a false split is a visible duplicate
    a correction can join. That is the same asymmetry funding_event_duplicate
    already states, applied one layer later.
    """
    if not signal.published_date:
        return None

    pub = date.fromisoformat(signal.published_date)
    window_start = (pub - timedelta(days=SAME_EVENT_DAYS)).isoformat()
    window_end = (pub + timedelta(days=SAME_EVENT_DAYS)).isoformat()

    rows = conn.execute(
        """
        SELECT signal_id, headline, published_date, funding_amount_usd
          FROM signals
         WHERE is_current = 1
           AND company_key = ?
           AND pillar = ?
           AND published_date BETWEEN ? AND ?
        """,
        (signal.company_key, signal.pillar, window_start, window_end),
    ).fetchall()
    incoming = getattr(signal, "funding_amount_usd", None)
    for row in rows:
        if _same_amount_claim(incoming, row["funding_amount_usd"]) is not False:
            return row["signal_id"]

    wide_start = (pub - timedelta(days=NEAR_IDENTICAL_DAYS)).isoformat()
    wide_end = (pub + timedelta(days=NEAR_IDENTICAL_DAYS)).isoformat()
    candidates = conn.execute(
        """
        SELECT signal_id, headline
          FROM signals
         WHERE is_current = 1
           AND company_key = ?
           AND pillar = ?
           AND published_date BETWEEN ? AND ?
        """,
        (signal.company_key, signal.pillar, wide_start, wide_end),
    ).fetchall()

    for row in candidates:
        if _token_overlap(row["headline"], signal.headline) >= 0.85:
            return row["signal_id"]
    return None


def funding_event_duplicate(conn: sqlite3.Connection, company_key: str,
                            amount_usd: int | None, amount_canon: str,
                            days: int = 21,
                            published_date: str | None = None) -> str | None:
    """A funding round we already hold, matched BEFORE any model is paid.

    fuzzy_duplicate above catches the same round after classification, which
    means the read-through was already bought. This runs on the deterministic
    parse of the headline (pipeline/cheap_extract.py), so the seventh outlet
    to rewrite a round we stored on Monday costs nothing at all.

    Matched on employer + amount, inside a recency window. The amount match
    uses the USD integer when both sides parse, else the canonical text form
    (currency kept, so €71M never matches $71M). Returns the existing
    signal_id, or None — and None on any doubt, because the cost of a miss
    here is one paid read, while a false match silently drops a real story.

    THE WINDOW IS THE CANDIDATE'S, NOT TODAY'S (fixed 2026-08-04). It used to
    run back `days` from `date.today()`, which silently made this whole layer
    dead code for anything we discover late - and late is the norm, not the
    exception: google_news's median discovery lag over 2,795 current rows is
    130 days, and only 17.9% of its rows arrive inside three days. A round
    published in March and surfaced in August compared itself against a window
    that started in July and matched nothing, so the seventh outlet's rewrite
    of it was bought as a full read every time. Anchoring on the candidate's
    own published_date is what the docstring above always claimed it did.
    """
    if not company_key or not amount_canon:
        return None
    anchor = date.today()
    if published_date:
        try:
            anchor = date.fromisoformat(published_date)
        except ValueError:
            pass
    since = (anchor - timedelta(days=days)).isoformat()
    until = (anchor + timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT signal_id, funding_amount, funding_amount_usd
          FROM signals
         WHERE is_current = 1
           AND company_key = ?
           AND pillar = 'company_development'
           AND funding_amount IS NOT NULL
           AND published_date >= ?
           AND published_date <= ?
        """,
        (company_key, since, until),
    ).fetchall()

    from . import cheap_extract  # local import; cheap_extract imports nothing from here

    for row in rows:
        if amount_usd is not None and row["funding_amount_usd"] == amount_usd:
            return row["signal_id"]
        stored_canon = cheap_extract._canon_amount(row["funding_amount"] or "")
        if stored_canon and stored_canon == amount_canon:
            return row["signal_id"]
    return None


def leadership_event_duplicate(conn: sqlite3.Connection, company_key: str,
                               person: str, days: int = 21,
                               published_date: str | None = None) -> str | None:
    """An appointment we already hold, matched BEFORE any model is paid.

    The cross-language sibling of `funding_event_duplicate`, and it exists
    because the measured waste is overwhelmingly this shape rather than that
    one. Of the 612 paid extractions in `data/gate_labels/labels-2026-08.jsonl`
    that turned out to be events already held — 20.3% of every paid
    extraction — **60.9% are chief-executive appointments and only 13.7% carry
    a currency amount at all**, and 78% are not in English. PayPal's
    appointment of Enrique Lores was bought twice more after it was stored,
    once in Turkish and once in Spanish; Disney's of Josh D'Amaro three times,
    in Italian, Japanese and Turkish.

    Matched on EMPLOYER PLUS PERSON, and both are required. Employer alone
    would collapse two genuinely different appointments at one company — a CEO
    in March and a CFO in April are two records, and a large employer has
    several a year.

    The person is matched against the stored row's own English prose rather
    than a column, because there is no person column: the row records that an
    appointment happened and names the person in `summary` and
    `talent_readthrough`. That is exactly what makes this work across
    languages — "Josh D'Amaro" is spelled the same in the Italian headline and
    the English summary, while every other word differs.

    Returns the existing signal_id, or None. None on any doubt: a miss costs
    one paid read, a false match silently drops a real appointment, and there
    is no later stage that can notice.

    THE WINDOW IS THE CANDIDATE'S, NOT TODAY'S — the same fix
    `funding_event_duplicate` needed on 2026-08-04, for the same reason
    (google_news's median discovery lag is 130 days).
    """
    if not company_key or not person:
        return None
    tokens = [t for t in re.split(r"[\s'’-]+", person) if len(t) > 1]
    if len(tokens) < 2:
        # One token is not a person. "Lores" alone would match any story
        # naming him, including one about a different employer's board.
        return None
    anchor = date.today()
    if published_date:
        try:
            anchor = date.fromisoformat(published_date)
        except ValueError:
            pass
    since = (anchor - timedelta(days=days)).isoformat()
    until = (anchor + timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT signal_id, headline, summary, talent_readthrough
          FROM signals
         WHERE is_current = 1
           AND company_key = ?
           AND pillar = 'leadership_change'
           AND published_date >= ?
           AND published_date <= ?
        """,
        (company_key, since, until),
    ).fetchall()

    for row in rows:
        prose = " ".join(str(row[field] or "") for field in
                         ("headline", "summary", "talent_readthrough")).lower()
        # EVERY token of the name, not any: "Enrique Lores" must not match a
        # row about Enrique Garcia, and a surname shared with the employer
        # ("Tânia Bulhões") must not match on its own.
        if all(token.lower() in prose for token in tokens):
            return row["signal_id"]
    return None


def _token_overlap(a: str, b: str) -> float:
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
