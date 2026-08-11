"""Do we hold each landmark? Pure functions plus two readers. No model, no cost.

TWO LENSES, because on 2026-08-04 they disagreed and the disagreement was the
whole defect. Anthropic's $30bn round was IN the database, correctly extracted,
and NOT on the site: a publish guardrail had quarantined it and nobody had
answered the finding in five days. A guard that only asked the database would
have reported that round HELD while no reader could see it.

    stored  -- the committed data/talent_intel.db. Offline, always available,
               and what ops_status can recompute at session start.
    live    -- the public /query endpoint, which is what a reader actually
               sees. Free, no key, ~14 requests.

`held_not_live` is therefore a first-class outcome and not a rounding error.

WHAT REDDENS. Only a REGRESSION: an entry a previous report recorded as held,
now not held. An entry that has never been held is a STANDING GAP, printed
every week and never red, because a permanent red on a corpus that only
backfilling can move trains the next session to ignore the exit code. That is
the same reasoning `ops_status._report_rejection_audit` already applies to the
young-corpus finding, applied to a different measurement.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from analysis.recall.match import company_key, names_match, row_date

# Verdicts. UNKNOWN exists so that "the lens could not be read" has somewhere
# to go other than into MISSING (which would manufacture regressions during a
# host outage) or into HELD (which would be a silent pass).
HELD = "HELD"
WRONG_AMOUNT = "WRONG_AMOUNT"
MISSING = "MISSING"
UNKNOWN = "UNKNOWN"

# How far either side of the announcement a stored row may sit and still be
# this round. Wider than the recall matcher's -10/+21 because outlets rewrite a
# mega-round for weeks, and narrower than a quarter because the employers in
# this set raise more than once a quarter: Anthropic's Series G and Series H
# are 105 days apart, and a window that let them touch would score one round
# twice and never notice the other going missing.
WINDOW_DAYS = 45

# Same tolerance the recall matcher uses, for the same reason: publishers round.
AMOUNT_TOLERANCE = 0.08

# The pillar a funding round is filed under, plus the headings the same
# document legitimately lands in. Kept narrow on purpose: this check exists to
# notice absence, so a generous pillar rule would let any row for the employer
# in the window answer for the round.
FUNDING_PILLAR = "company_development"

_FUNDING_WORDS = (
    "raise", "raised", "raises", "raising", "funding", "round", "series ",
    "valuation", "valued", "investment", "financing", "capital", "backs",
    "invests", "stake",
)


def _amount(value):
    if value in (None, "", "null"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _looks_like_funding(row: dict) -> bool:
    text = ("%s %s" % (row.get("headline") or "", row.get("summary") or "")).lower()
    return any(word in text for word in _FUNDING_WORDS)


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def entry_names(entry: dict) -> list:
    out = [entry.get("company") or ""]
    out += [str(a) for a in (entry.get("aliases") or [])]
    return [n for n in out if n]


def in_window(entry_day: date, row: dict, window_days: int = WINDOW_DAYS) -> bool:
    stored = row_date(row)
    if stored is None:
        # A row with no date at all is a field defect, not evidence that it is
        # a different event. The recall matcher makes the same call.
        return True
    return abs((stored - entry_day).days) <= window_days


def candidates(entry: dict, rows, window_days: int = WINDOW_DAYS) -> list:
    """Stored rows that could be this round: this employer, funding-shaped,
    in this window.

    EXACT EMPLOYER NAMES WIN OUTRIGHT. `names_match` is whole-word containment
    in either direction, which is right for 'Figure' against 'Figure AI' and
    wrong for 'xAI' against 'MI XAI Investment, LLC', a $1.84m private
    placement by a feeder vehicle, which was being offered as a candidate for
    a $20bn round. So if any row carries the employer's name exactly, only
    those rows are considered, and the looser rule is the fallback for when
    nothing does.
    """
    exact, loose = _split_candidates(entry, rows, window_days)
    return exact or loose


def _split_candidates(entry: dict, rows, window_days: int = WINDOW_DAYS):
    """(rows whose employer name IS this one, rows whose name merely contains it)"""
    day = _parse_date(entry.get("event_date"))
    wanted = {company_key(name) for name in entry_names(entry)}
    exact, loose = [], []
    for row in rows:
        pillar = row.get("pillar") or ""
        if pillar != FUNDING_PILLAR and not _looks_like_funding(row):
            continue
        stored_name = row.get("company") or ""
        if not any(names_match(name, stored_name) for name in entry_names(entry)):
            continue
        if day and not in_window(day, row, window_days):
            continue
        (exact if company_key(stored_name) in wanted else loose).append(row)
    return exact, loose


def verdict(entry: dict, rows, tolerance: float = AMOUNT_TOLERANCE,
            window_days: int = WINDOW_DAYS) -> dict:
    """HELD / WRONG_AMOUNT / MISSING for one landmark against one row set.

    The amount is part of the verdict and not a footnote. The live site spent
    six months showing 'OpenAI capta 93.175 millones' as its only OpenAI
    funding row: an event we held, under a figure no English reader can read.
    A check that called that HELD would have been true and useless.
    """
    exact, loose = _split_candidates(entry, rows, window_days)
    matched = exact or loose
    if not matched:
        return {"verdict": MISSING, "detail": "no row for this event",
                "matched": None}

    wanted = _amount(entry.get("amount_usd"))
    approximate = bool(entry.get("amount_is_approximate"))

    if approximate or wanted is None:
        best = matched[0]
        return {
            "verdict": HELD,
            "detail": ("amount not checked: the document states this round in "
                       "%s, so the USD figure here is a conversion"
                       % (entry.get("currency") or "another currency")),
            "amount_checked": False,
            "matched": _describe(best),
        }

    agreeing, quantified = None, None
    for row in matched:
        stored = _amount(row.get("funding_amount_usd"))
        if stored is None:
            continue
        quantified = quantified or row
        if abs(stored - wanted) / wanted <= tolerance:
            agreeing = row
            break
        # "more than $1 billion" and "over $4 billion" are the document's own
        # words on three entries here, so a stored figure ABOVE the landmark is
        # the same claim rather than a mismatch. Below it never is.
        if str(entry.get("amount_text") or "").lower().startswith(
                ("more than", "over", "at least")) and stored >= wanted:
            agreeing = row
            break

    if agreeing is not None:
        return {"verdict": HELD, "detail": "", "amount_checked": True,
                "matched": _describe(agreeing)}

    if not exact:
        # Nothing carries this employer's actual name. A near-name row with a
        # different figure is not this round reported badly, it is a different
        # company: 'MI XAI Investment, LLC' raising $1.84m is not xAI's $20bn
        # Series E. Calling that WRONG_AMOUNT would send a session to fix an
        # extractor when the real answer is that the round was never collected.
        return {"verdict": MISSING,
                "detail": "no row for this event (the only near-name rows "
                          "belong to a different employer)",
                "amount_checked": True, "matched": None}

    if quantified is not None:
        stored = _amount(quantified.get("funding_amount_usd"))
        return {
            "verdict": WRONG_AMOUNT,
            "detail": "stored $%s, the document says $%s"
                      % (_money(stored), _money(wanted)),
            "amount_checked": True,
            "matched": _describe(quantified),
        }

    return {
        "verdict": WRONG_AMOUNT,
        "detail": "the row is here but no USD amount was parsed from it",
        "amount_checked": True,
        "matched": _describe(exact[0]),
    }


def _money(value) -> str:
    if value is None:
        return "none"
    if value >= 1e9:
        return "%.3gbn" % (value / 1e9)
    if value >= 1e6:
        return "%.3gm" % (value / 1e6)
    return "%.0f" % value


def _describe(row: dict) -> dict:
    return {
        "signal_id": row.get("signal_id"),
        "company": row.get("company"),
        "headline": (row.get("headline") or "")[:120],
        "funding_amount_usd": row.get("funding_amount_usd"),
        "published_date": row.get("published_date"),
        "source_url": row.get("source_url"),
    }


# --- reading the two lenses ------------------------------------------------

def stored_rows(conn) -> list:
    """Every current row that could answer for a funding round.

    Reads the committed database directly. No filtering on published_at: the
    stored lens is deliberately "do we HOLD it", and whether a reader can see
    it is the live lens's question. Those two being different is the finding
    this module exists to surface, so conflating them here would erase it.
    """
    cur = conn.execute(
        "SELECT signal_id, company, company_key, pillar, headline, summary, "
        "       funding_amount_usd, published_date, effective_date, "
        "       captured_at, source_url, published_at "
        "FROM signals WHERE is_current = 1")
    columns = [c[0] for c in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def live_rows(fetch, entries) -> dict:
    """{company_key_for_the_query: rows} from the public /query endpoint.

    `fetch` is injected so this is testable offline and so the module never
    imports requests. One query per DISTINCT employer, not per entry: three
    Anthropic landmarks are one request.

    Any failure raises. The caller records the live lens as unavailable rather
    than mapping an outage onto MISSING, which would manufacture a regression
    for every entry on the day the host has a bad seven minutes.
    """
    out = {}
    for name in sorted({(e.get("company") or "").strip() for e in entries}):
        if not name:
            continue
        out[name] = fetch(name)
    return out


def rows_for(entry: dict, by_company: dict) -> list:
    """Pool the live results for every name this entry might be stored under."""
    pooled, seen = [], set()
    for name in entry_names(entry):
        for row in by_company.get(name) or []:
            key = row.get("signal_id") or id(row)
            if key in seen:
                continue
            seen.add(key)
            pooled.append(row)
    return pooled


# --- history, status and the summary --------------------------------------

def previous_history(previous: dict | None) -> dict:
    """What earlier runs recorded, keyed by entry id.

    The repository is the memory here, as it is for the database and the alert
    outbox. A regression is only definable against a written-down past, and a
    past held in a job log is a past nobody can read.
    """
    if not previous:
        return {}
    history = previous.get("history")
    if isinstance(history, dict):
        return history
    # Tolerate an older report shape by rebuilding history from its entries.
    rebuilt = {}
    for item in previous.get("entries") or []:
        ident = item.get("id")
        if ident:
            rebuilt[ident] = {
                "ever_stored": bool(item.get("ever_stored")),
                "ever_live": bool(item.get("ever_live")),
                "first_held_on": item.get("first_held_on"),
                "last_held_on": item.get("last_held_on"),
            }
    return rebuilt


def evaluate(entries, stored, live_by_company=None, *, today=None,
             history=None, tolerance=AMOUNT_TOLERANCE,
             window_days=WINDOW_DAYS) -> dict:
    """The whole check. Returns the report body; writes nothing.

    `live_by_company` None means the live lens did not run, which is UNKNOWN
    and never a pass and never a regression.
    """
    today = today or date.today()
    history = dict(history or {})
    live_ran = live_by_company is not None

    results, regressions, gaps = [], [], []
    for entry in entries:
        past = history.get(entry["id"]) or {}

        stored_v = verdict(entry, stored, tolerance, window_days)
        if live_ran:
            live_v = verdict(entry, rows_for(entry, live_by_company),
                             tolerance, window_days)
        else:
            live_v = {"verdict": UNKNOWN,
                      "detail": "the live lens did not run", "matched": None}

        held_stored = stored_v["verdict"] == HELD
        held_live = live_v["verdict"] == HELD

        ever_stored = bool(past.get("ever_stored")) or held_stored
        ever_live = bool(past.get("ever_live")) or held_live

        # Reader-visible status. The live lens decides when it ran, because it
        # is the one that answers the question the owner actually asked.
        if live_ran:
            if held_live:
                status = "held"
            elif held_stored:
                status = "held_not_live"
            elif live_v["verdict"] == WRONG_AMOUNT or stored_v["verdict"] == WRONG_AMOUNT:
                status = "wrong_amount"
            else:
                status = "missing"
        else:
            status = {HELD: "held", WRONG_AMOUNT: "wrong_amount",
                      MISSING: "missing"}[stored_v["verdict"]]

        regressed = []
        if past.get("ever_stored") and not held_stored:
            regressed.append("stored: was held, now %s" % stored_v["verdict"])
        if live_ran and past.get("ever_live") and not held_live:
            regressed.append("live: was held, now %s" % live_v["verdict"])

        item = {
            "id": entry["id"],
            "quarter": entry["quarter"],
            "company": entry["company"],
            "event_date": entry["event_date"],
            "amount_usd": entry["amount_usd"],
            "source_url": entry["source_url"],
            "stored_verdict": stored_v["verdict"],
            "stored_detail": stored_v["detail"],
            "live_verdict": live_v["verdict"],
            "live_detail": live_v["detail"],
            "status": status,
            "matched": stored_v["matched"] or live_v["matched"],
            "ever_stored": ever_stored,
            "ever_live": ever_live,
            "regression": regressed,
        }
        results.append(item)

        if regressed:
            regressions.append(item)
        elif status != "held":
            # A never-held landmark is a standing gap: real, listed every week,
            # and not a failure of this week's run.
            gaps.append(item)

        history[entry["id"]] = {
            "ever_stored": ever_stored,
            "ever_live": ever_live,
            "first_held_on": past.get("first_held_on")
                             or (today.isoformat() if (held_stored or held_live) else None),
            "last_held_on": (today.isoformat() if (held_stored or held_live)
                             else past.get("last_held_on")),
        }

    return {
        "entries": results,
        "history": history,
        "regressions": regressions,
        "standing_gaps": gaps,
        "summary": summarise(results, live_ran),
        "by_quarter": by_quarter(results),
    }


def summarise(results, live_ran: bool) -> dict:
    total = len(results)
    held = sum(1 for r in results if r["status"] == "held")
    held_not_live = sum(1 for r in results if r["status"] == "held_not_live")
    wrong = sum(1 for r in results if r["status"] == "wrong_amount")
    missing = sum(1 for r in results if r["status"] == "missing")
    regressions = sum(1 for r in results if r["regression"])
    gaps = total - held - regressions
    return {
        "total": total,
        "held": held,
        "held_not_live": held_not_live,
        "wrong_amount": wrong,
        "missing": missing,
        "standing_gaps": gaps,
        "regressions": regressions,
        "live_lens": "read" if live_ran else "not read",
        "one_line": one_line(total, held, gaps, regressions, held_not_live),
    }


def one_line(total: int, held: int, gaps: int, regressions: int,
             held_not_live: int = 0) -> str:
    """The line the owner reads in the weekly email and at session start.

    Counts, never a bare percentage. A percentage with no denominator is not a
    result, and this denominator is small enough that the raw numbers are the
    clearer statement anyway.
    """
    line = ("landmarks: %d of %d held, %d standing gap%s, %d regression%s"
            % (held, total, gaps, "" if gaps == 1 else "s",
               regressions, "" if regressions == 1 else "s"))
    if held_not_live:
        line += (", %d stored but not live" % held_not_live)
    return line


def by_quarter(results) -> dict:
    out = {}
    for item in results:
        cell = out.setdefault(item["quarter"], {"total": 0, "held": 0,
                                                "missing": 0, "wrong_amount": 0,
                                                "held_not_live": 0})
        cell["total"] += 1
        key = item["status"]
        if key in cell:
            cell[key] += 1
    return dict(sorted(out.items()))


def report_is_stale(report: dict | None, now: date, max_age_days: int = 10) -> bool:
    """True when the committed report is older than the weekly cadence allows.

    10 days rather than 7: one missed Monday is a bank holiday on the runner
    fleet, two is a workflow that has stopped. Absence of a report is stale by
    definition, because "never checked" is not "checked and fine".
    """
    if not report:
        return True
    checked = _parse_date(report.get("checked_on"))
    if checked is None:
        return True
    return (now - checked) > timedelta(days=max_age_days)
