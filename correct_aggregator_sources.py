#!/usr/bin/env python3
"""Re-point rows cited to an aggregator at the publisher their canonical names.

QUEUE IT, NEVER DISPATCH IT (CLAUDE.md):

    gh workflow run drain-writers.yml -f enqueue=correct-aggregator-sources.yml \
         -f inputs_json='{"dry_run":"false"}' -f reason='why'

WHAT WENT WRONG
---------------
`validate._BLOCKED_SOURCE_HOSTS` matched on the EXACT host. It listed
`news.yahoo.com`, so `finance.yahoo.com` and `sg.finance.yahoo.com` were never
tested against anything and three rows went live cited to an aggregator:

    7-Eleven             finance.yahoo.com/.../7-eleven-names-ceo
    Haus Cramer Gruppe   finance.yahoo.com/.../warsteiner-owner-haus-cramer-gruppe
    HSBC                 sg.finance.yahoo.com/news/hsbc-plans-hire-100-ai

The feed loader in `collectors/national_press.py` had already learned this and
derives its aggregator set from the registrable domain. So one rule lived in two
layers and the layer that decides what may be STORED was the weaker one. The
forward fix is in `pipeline/validate.py`: `is_aggregator_host()` now matches the
registrable domain, and `prefer_canonical()` follows the pointer. This is the
backward half.

WHY THIS IS NOT A BLANKET DOMAIN BLOCK
--------------------------------------
Because the canonical, checked on 2026-07-30, says three different things:

    7-Eleven             -> https://www.cstoredive.com/news/7-eleven-names-new-ceo/826096/
    Haus Cramer Gruppe   -> https://www.just-drinks.com/news/haus-cramer-gruppe-names-new-ceo/
    HSBC                 -> ITSELF

So two of the three are a real publisher's article behind a syndication URL and
one is the aggregator all the way down.

CLAUDE.md: an aggregator is a DISCOVERY POINTER. Following the pointer to the
publisher and storing that is the rule being kept, not bent, and cstoredive.com
is a publisher this project already reads directly. Refusing all three on the
host would have thrown away a publisher we can name for the sake of a tidier
rule.

WHAT IT DOES AND DOES NOT DO
----------------------------
Repairs only what the evidence supports. A row whose canonical is a real
publisher's article is REVISED -- `store.revise()`, so the original survives at
`is_current = 0` and "what did we know on date D" stays answerable. A row whose
canonical is the aggregator itself, or which no longer resolves, is NOT touched:
it is printed, named, and left for a human. Nothing here retracts. An automatic
retraction driven by an HTTP response would let a publisher's bad afternoon
delete evidence, which is the same reasoning `link_check.py` carries.

Costs nothing: no model is called, ever. One HEAD/GET per candidate row.

THE SECOND ROUTE: THE INDEX, WHEN THE POINTER WILL NOT ANSWER (2026-09-16)
--------------------------------------------------------------------------
79 published rows cited a commercial data provider's "news note" pages. The
provider serves those pages behind a bot wall (HTTP 403, no canonical), so
the canonical route above LEFT ALONE every one of them and the rows would
have sat there until a human retracted or re-pointed each by hand.

So when the canonical says nothing, the EVENT is chased the way
`collectors/tripwire_chase.py` chases a lead: the row's own headline, with the
provider's masthead stripped off, is put to the Google News RSS index, and a
candidate is accepted only when ALL of these hold, mechanically:

    the outlet (the item's <source>) is not itself an aggregator;
    the row's employer name appears in the candidate's title;
    the row's headline money figure appears in the candidate's title,
        normalised ($20M, $20 million, US$20M all read as 20m);
    the candidate was published within CHASE_WINDOW_DAYS of the row.

Several matching outlets: the EARLIEST is credited, because it is the one
that reported it. Anything short of the whole rule is UNKNOWN and printed with
the reason: no figure in the headline to match on, no candidate, an index
entry whose publisher URL cannot be recovered. UNKNOWN never applies anything,
and the decision on an UNKNOWN row is the owner's. The publisher's URL is
recovered through `google_news.resolve_source_url`; the outlet's display name
is the index's own <source> label, and the publisher's page is fetched, once,
for its `og:site_name` only when the index carries no label. No page on the
provider is requested at all on this route.

THIS ROUTE IS DETERMINISTIC ON PURPOSE. The two-referee adjudicator exists for
a JUDGEMENT; matching a name, a figure and a date is not one, and printing all
three beside each decision is what makes a verifier able to check every line
for free. A row that would need a referee (the figure is missing, or the
candidates disagree) is left UNKNOWN here rather than judged.

THE LIVE ROW: SITE FIRST, THEN THE REVISION, AND TODAY THE SITE REFUSES
------------------------------------------------------------------------
A published row is corrected the way `correct_city_country.reissue` does it:
`/correct` first, then `store.revise()`, then the revision inherits
`published_at` so publish() never offers it again. The fingerprint holds
because the stored headline carries " - <provider>" as its masthead suffix
and `content_hash` hashes the STRIPPED form; the revision stores the stripped
headline, which is also what would take the provider's name off the public
page. A hash that would move raises `Unsafe`.

BUT `tit_correctable_columns()` in `includes/api.php` does NOT carry
`headline`, `source_url` or `source_name`, and that is a standing decision,
not an oversight: `tests/test_form_d_correction.py::
test_the_correction_route_writes_those_two_columns_and_nothing_else` forbids
them by name, so that a bug in a correction pass can never rewrite what a
document said. Widening that allowlist to let this pass through is the
owner's call, and this file does not make it. Until it is made, `/correct`
drops every field this sends and reports `skipped_no_fields`, and
`push_citation` raises `PluginTooOld` BEFORE anything is written locally: a
published row is left exactly as it was, and the run says why. Every one of
the 82 rows measured on 2026-09-16 is published, so with today's plugin an
`--apply` run corrects nothing and refuses loudly; the dry run is the
evidence, and the decision is written up in docs/HANDOVER.md.

EVERY LINE THIS PRINTS IS REDACTED. The run log of a public repository is a
tracked artifact in every sense that matters, so provider names pass through
`provider_names.redact` before they reach stdout.
"""

from __future__ import annotations

import argparse
import dataclasses
import email.utils
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

from collectors import google_news
from pipeline import provider_names, publish, store, validate

#: A browser-ish agent, for the same reason every other fetch here sends one.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "TalentIntel/1.0 (+https://asktherecruiter.com)"
)

NOTE = ("citation re-pointed from an aggregator to the publisher named by the "
        "document's own rel=canonical")
NOTE_CHASED = ("citation re-pointed from an aggregator to the outlet that "
               "reported the event, found through the news index by employer "
               "name, headline figure and date; the aggregator's masthead was "
               "removed from the headline")

#: How far either side of the row's own date a candidate may be published.
#: The provider's note can trail the outlet by days and an outlet can follow
#: up a week later; wider than that and a Series B starts matching a Series C.
CHASE_WINDOW_DAYS = 30
#: Seconds between rows on the chase route: one index query, two resolution
#: requests and one publisher fetch per row, and nothing here is in a hurry.
CHASE_PAUSE = 1.0

#: The fields a correction sends to /correct. NOT in the plugin's allowlist
#: today (see the header); kept as a constant so the refusal can name them.
CORRECTABLE_ON_SITE = ("headline", "source_url", "source_name")
SITE_ALLOWLIST = "tit_correctable_columns() in includes/api.php"

_FIELDS = tuple(f.name for f in dataclasses.fields(validate.Signal))

_CANONICAL = re.compile(
    rb"""<link[^>]+rel=["']canonical["'][^>]*>""", re.I)
_HREF = re.compile(rb"""href=["']([^"']+)["']""", re.I)
#: The publisher's own name for itself. Used in preference to anything derived
#: from the host, because a display name is what a reader sees under "Source"
#: and "Cstoredive" is not what that outlet calls itself.
_SITE_NAME = re.compile(
    rb"""<meta[^>]+(?:property|name)=["']og:site_name["'][^>]*>""", re.I)
_CONTENT = re.compile(rb"""content=["']([^"']*)["']""", re.I)

#: A money figure as a headline writes it: an optional currency mark or code,
#: a number, an optional scale word. "$20M", "US$20 million", "EUR 23M",
#: "SEK 55M", "Rs 10 crore" and "$1.05B" all match; a bare "2026" does not,
#: because a figure needs a currency or a scale to be one.
_MONEY = re.compile(
    r"(?:(?P<cur>[$€£¥₹]|US\$|A\$|C\$|S\$|HK\$|NZ\$|R\$|"
    r"\b(?:USD|EUR|GBP|SEK|NOK|DKK|CHF|INR|Rs\.?|JPY|CNY|RMB|KRW|AUD|CAD|SGD|BRL|ZAR)\b)\s?"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s?(?P<scale>[MBKmbk]\b|million|billion|mn|bn|crore|Cr\b|lakh)?"
    r"|(?P<num2>\d[\d,]*(?:\.\d+)?)\s?(?P<scale2>million|billion|crore|lakh|[MB]\b))",
    re.I | re.U)
_SCALE = {"m": "m", "million": "m", "mn": "m", "b": "b", "billion": "b", "bn": "b",
          "k": "k", "crore": "crore", "cr": "crore", "lakh": "lakh"}


class Unsafe(RuntimeError):
    """A correction that would do more than it claims."""


class PluginTooOld(RuntimeError):
    """The live site dropped the citation fields, so its allowlist has not been
    extended yet. Refusing beats a corrected database behind a wrong page."""


def _r(text) -> str:
    """Redacted for stdout. The run log is public."""
    return provider_names.redact(str(text or ""))


def canonical_of(url: str, *, session=None, timeout: int = 25) -> tuple[str, str, str]:
    """(canonical_url, site_name, why). An empty canonical is a refusal, never a guess."""
    get = (session or requests).get
    try:
        resp = get(url, timeout=timeout, allow_redirects=True,
                   headers={"User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        return "", "", f"fetch failed: {type(exc).__name__}"
    if resp.status_code != 200:
        return "", "", f"HTTP {resp.status_code}"
    body = resp.content or b""
    tag = _CANONICAL.search(body)
    if not tag:
        return "", "", "no rel=canonical in the document"
    href = _HREF.search(tag.group(0))
    if not href:
        return "", "", "rel=canonical carries no href"
    return href.group(1).decode("utf-8", "replace").strip(), _site_name(body), "ok"


def _site_name(body: bytes) -> str:
    meta = _SITE_NAME.search(body or b"")
    if not meta:
        return ""
    got = _CONTENT.search(meta.group(0))
    return got.group(1).decode("utf-8", "replace").strip() if got else ""


def site_name_of(url: str, *, session=None, timeout: int = 25) -> str:
    """The PUBLISHER's own name for itself, read from the publisher's page.

    A separate fetch on purpose. Reading og:site_name off the aggregator's copy
    returns the aggregator -- both rows came back labelled "Yahoo Finance",
    which is precisely the name this whole pass exists to stop citing. The name
    has to come from the document we are about to credit.
    """
    get = (session or requests).get
    try:
        resp = get(url, timeout=timeout, allow_redirects=True,
                   headers={"User-Agent": USER_AGENT})
    except requests.RequestException:
        return ""
    return _site_name(resp.content) if resp.status_code == 200 else ""


def publisher_name(url: str, site_name: str = "") -> str:
    """A display name: the publisher's own og:site_name when it states one.

    THE FALLBACK IS DELIBERATELY DUMB and is a last resort: strip `www.`, drop
    the public suffix, title-case what is left. It gets "Cstoredive" for an
    outlet that calls itself "C-Store Dive", which is why the meta tag wins. A
    label is what a reader sees under Source; it is never a claim, but a wrong
    one is visible on the page in a way a wrong URL is not.
    """
    site_name = (site_name or "").strip()
    if site_name and len(site_name) <= 60:
        return site_name
    host = (urlparse(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    stem = host.split(".")[0] if host else ""
    return "-".join(part.capitalize() for part in stem.split("-")) or host


def targets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every CURRENT row whose host the new rule calls an aggregator.

    Derived, not typed. It asks validate the same question the write path asks,
    so this covers whatever the last edit to that function moved and the next
    edit needs no new script -- the same reasoning as
    `correct_company_key.py`'s worklist.
    """
    rows = conn.execute(
        "SELECT * FROM signals WHERE is_current = 1 ORDER BY signal_id").fetchall()
    return [r for r in rows
            if validate.is_aggregator_host(urlparse(r["source_url"]).hostname or "")]


# --- the chase route ---------------------------------------------------------

def money_tokens(text: str) -> list[str]:
    """Every money figure in `text`, normalised to number plus scale letter,
    in order of appearance. "$20M" and "US$20 million" both read "20m"."""
    out = []
    for m in _MONEY.finditer(text or ""):
        num = (m.group("num") or m.group("num2") or "").replace(",", "")
        scale = (m.group("scale") or m.group("scale2") or "").lower()
        if not num:
            continue
        try:
            num = repr(float(num)).rstrip("0").rstrip(".") if "." in num else str(int(num))
        except ValueError:
            continue
        out.append(num + _SCALE.get(scale, scale))
    return out


def stripped_headline(row) -> str:
    """The row's headline without the aggregator's masthead suffix: what the
    hash was taken over, and what the corrected row will carry."""
    return validate.strip_outlet_suffix(row["headline"] or "", row["source_name"])


def _company_in(company: str, title: str) -> bool:
    name = re.sub(r"\s+", " ", (company or "").strip()).casefold()
    if not name:
        return False
    return re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", (title or "").casefold()) is not None


def _published(item: dict) -> datetime | None:
    try:
        when = email.utils.parsedate_to_datetime(item.get("published_date") or "")
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _row_date(row) -> datetime | None:
    text = (row["published_date"] or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def judge_candidates(row, items: list[dict]) -> tuple[list[dict], str]:
    """The candidates that satisfy the WHOLE rule, earliest first, and why the
    rest did not. Pure; the index is fetched by the caller."""
    headline = stripped_headline(row)
    figures = money_tokens(headline)
    if not figures:
        return [], "no money figure in the headline to match on; needs a referee"
    figure = figures[0]
    anchor = _row_date(row)
    reasons: dict[str, int] = {}
    kept = []
    for item in items:
        outlet = (urlparse(item.get("source_url") or "").hostname or "").lower()
        title = item.get("headline") or ""
        if not outlet or validate.is_aggregator_host(outlet):
            reasons["outlet is an aggregator"] = reasons.get("outlet is an aggregator", 0) + 1
            continue
        if not _company_in(row["company"], title):
            reasons["employer not in title"] = reasons.get("employer not in title", 0) + 1
            continue
        if figure not in money_tokens(title):
            reasons["figure not in title"] = reasons.get("figure not in title", 0) + 1
            continue
        when = _published(item)
        if anchor is not None and when is not None \
                and abs(when - anchor) > timedelta(days=CHASE_WINDOW_DAYS):
            reasons["outside the date window"] = reasons.get("outside the date window", 0) + 1
            continue
        kept.append(item)
    kept.sort(key=lambda it: _published(it) or datetime.max.replace(tzinfo=timezone.utc))
    if kept:
        return kept, "ok"
    if not items:
        return [], "the index returned no candidate for the headline"
    return [], "no candidate met the whole rule (" + ", ".join(
        f"{k}: {v}" for k, v in sorted(reasons.items())) + ")"


def chase_publisher(row, *, fetch=None, resolve=None, session=None
                    ) -> tuple[dict | None, str, list[dict]]:
    """(the accepted candidate with its publisher URL, why, every judged match).

    `fetch(query)` returns parsed index items; `resolve(item)` recovers the
    publisher URL from the index's redirect. Both default to the Google News
    collector's own functions and are parameters so the test can stay offline.
    """
    fetch = fetch or (lambda q: google_news.fetch(q))
    resolve = resolve or (lambda it: google_news.resolve_source_url(it, session=session))
    headline = stripped_headline(row)
    try:
        items = fetch(headline)
    except requests.RequestException as exc:
        return None, f"index query failed: {type(exc).__name__}", []
    matches, why = judge_candidates(row, items)
    if not matches:
        return None, why, []
    best = dict(matches[0])
    resolved = resolve(dict(best))
    url = (resolved.get("source_url") or "").strip()
    host = (urlparse(url).hostname or "").lower()
    if not host or validate.is_aggregator_host(host) or not urlparse(url).path.strip("/"):
        return None, ("matched in the index but the publisher URL could not be "
                      "recovered from the redirect"), matches
    best["source_url"] = url
    return best, "ok", matches


def corrected_signal(row: sqlite3.Row, url: str, name: str) -> validate.Signal:
    signal = validate.Signal(**{f: row[f] for f in _FIELDS})
    signal.source_url = url
    signal.source_name = name
    # The masthead suffix comes off with the masthead. The hash was taken over
    # the stripped form, so this is what keeps it still.
    signal.headline = stripped_headline(row)
    rehashed = validate.content_hash(
        signal.company_key, signal.pillar, signal.published_date,
        signal.headline, signal.source_name)
    # MEASURED, not assumed. `source_name` reaches content_hash only through
    # strip_outlet_suffix(), which removes a trailing " - Outlet" from the
    # headline; the hashed payload itself is
    # company_key|pillar|published_date|normalised_headline. Storing the
    # stripped headline makes the new source_name irrelevant to the hash, so
    # the fingerprint does NOT move. That is what keeps the in-place site
    # correction possible, and it is asserted rather than left to be
    # rediscovered.
    if rehashed != row["content_hash"]:
        raise Unsafe(
            f"re-pointing {row['company']} moves its content_hash "
            f"({row['content_hash']} -> {rehashed}), so the live row could "
            f"never be matched again. This needs a withdraw-and-republish "
            f"and not this script.")
    return signal


def push_citation(row, signal: validate.Signal, *, session=None) -> dict:
    """Correct the live row's citation in place, or refuse.

    /correct is an UPDATE keyed on (content_hash, collector, is_current), so a
    second run sends the values the row already holds and the server reports
    it as unchanged. `skipped_no_fields` is the server saying its allowlist
    dropped everything we sent: the whole pass is impossible against the
    deployed plugin, and that raises rather than counting.
    """
    site, key = publish._config()
    payload = {"content_hash": row["content_hash"]}
    for field in CORRECTABLE_ON_SITE:
        value = getattr(signal, field)
        if value:
            payload[field] = value
    poster = session or requests
    resp = poster.post(
        f"{site}/wp-json/talent/v1/correct",
        json={"collector": row["collector"], "rows": [payload]},
        headers={"X-Talent-API-Key": key, "User-Agent": publish.USER_AGENT,
                 "Content-Type": "application/json"},
        timeout=publish.TIMEOUT,
    )
    if resp.status_code >= 400:
        raise publish.PublishError(f"{resp.status_code}: {resp.text[:300]}")
    result = resp.json() or {}
    if result.get("errors"):
        raise publish.PublishError(f"/correct reported {result['errors']}")
    if int(result.get("skipped_no_fields") or 0):
        raise PluginTooOld(
            f"the live site dropped every field this correction sends, so "
            f"{SITE_ALLOWLIST} does not allow {', '.join(CORRECTABLE_ON_SITE)}. "
            f"Widening it is the owner's decision (see this file's header); "
            f"nothing has been written locally.")
    return result


def reissue(conn, row, signal: validate.Signal, note: str, *, push=push_citation) -> None:
    """Correct the site, then append the revision. In that order, on purpose.

    A row is a target while its LIVE revision cites the aggregator, so the
    local revision is the only record that the site was corrected. Written
    first, a run killed between the two steps would leave the page wrong with
    nothing left in the database to find it. Written second, the worst a kill
    costs is one repeated UPDATE of a value the site already holds.
    """
    if row["published_at"]:
        push(row, signal)
    store.revise(conn, row["signal_id"], signal, note)
    if row["published_at"]:
        # The live row now holds this revision, so the revision is published.
        # Left NULL it would be offered to publish() every run and come back
        # 'duplicate' on a hash the site has already seen.
        conn.execute(
            "UPDATE signals SET published_at = ? WHERE signal_id = ? AND is_current = 1",
            (row["published_at"], row["signal_id"]))
    conn.commit()


def run(db_path: str, *, apply: bool, session=None, fetch=None, resolve=None,
        push=push_citation, pause: float = CHASE_PAUSE, limit: int | None = None) -> int:
    conn = store.connect(db_path) if hasattr(store, "connect") else sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = targets(conn)
    print(f"{len(rows)} current row(s) cited to an aggregator under the "
          f"registrable-domain rule.\n")
    if limit is not None:
        rows = rows[:limit]

    repaired = chased = refused = 0
    # A host that answered the canonical fetch with a refusal is not asked
    # again this run: 79 rows on one bot-walled host is one answer, not 79.
    refused_hosts: set[str] = set()
    for index, row in enumerate(rows):
        if index and pause:
            time.sleep(pause)
        host = (urlparse(row["source_url"]).hostname or "").lower()
        if host in refused_hosts:
            canonical, why = "", "host refused the canonical fetch earlier in this run"
        else:
            canonical, _aggregator_site_name, why = canonical_of(
                row["source_url"], session=session)
            if not canonical and (why.startswith("HTTP 4") or why.startswith("fetch failed")):
                refused_hosts.add(host)
        chost = (urlparse(canonical).hostname or "").lower() if canonical else ""
        print(f"  {_r(row['company'])}  [{row['content_hash'][:12]}]")
        print(f"    cited     {_r(row['source_url'])}")
        if not canonical:
            print(f"    canonical (none) -- {_r(why)}")
        else:
            print(f"    canonical {_r(canonical)}")

        route = "canonical"
        url = name = ""
        if canonical and not validate.is_aggregator_host(chost) \
                and urlparse(canonical).path.strip("/"):
            url, name = canonical, publisher_name(canonical, site_name_of(canonical, session=session))
        else:
            reason = why if not canonical else (
                "canonicalises to itself" if chost == host
                else f"canonical is also an aggregator ({_r(chost)})"
                if validate.is_aggregator_host(chost) else "canonical is a bare domain")
            print(f"    pointer   {_r(reason)}; chasing the event through the index")
            best, why, matches = chase_publisher(row, fetch=fetch, resolve=resolve, session=session)
            for item in matches:
                mark = "*" if best and item.get("headline") == best.get("headline") else " "
                print(f"     {mark} {(urlparse(item.get('source_url') or '').hostname or '?'):<32}"
                      f" {(item.get('published_date') or '')[:16]:<17} {_r(item.get('headline'))[:90]}")
            if not best:
                print(f"    UNKNOWN: {_r(why)}. A human decides this one; nothing here retracts.\n")
                refused += 1
                continue
            route = "chased"
            url = best["source_url"]
            # The index's own <source> label is the outlet's name as readers
            # know it. Only when the index carries none is the publisher's page
            # fetched, once, for its og:site_name; the dumb host fallback is
            # last.
            name = (best.get("source_name") or "").strip() \
                or publisher_name(url, site_name_of(url, session=session))

        try:
            signal = corrected_signal(row, url, name)
        except Unsafe as exc:
            print(f"    UNSAFE: {_r(exc)}\n")
            refused += 1
            continue
        print(f"    -> {_r(name)} ({(urlparse(url).hostname or '').lower()})  via {route}")
        print(f"    headline  {_r(signal.headline)[:100]}")
        print(f"    content_hash {row['content_hash'][:12]} unchanged")
        if apply:
            reissue(conn, row, signal, NOTE if route == "canonical" else NOTE_CHASED, push=push)
            print("    REVISED (original survives at is_current = 0)"
                  + (", live row corrected" if row["published_at"] else "") + "\n")
        else:
            print("    would revise (dry run)\n")
        repaired += 1
        chased += route == "chased"

    print(f"repaired {repaired} ({chased} through the index), left for a human "
          f"{refused}, {'APPLIED' if apply else 'dry run, nothing written'}.")
    return 0 if not refused else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="data/talent_intel.db")
    p.add_argument("--apply", action="store_true",
                   help="write the revisions. Without it, nothing is written.")
    p.add_argument("--limit", type=int, default=None,
                   help="stop after N rows (blank for all)")
    args = p.parse_args(argv)
    return run(args.db, apply=args.apply, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
