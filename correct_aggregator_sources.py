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

THE PART THAT DOES NOT REACH THE SITE, STATED PLAINLY
-----------------------------------------------------
`tit_correctable_columns()` in `includes/api.php` is
`signal_direction, talent_readthrough, city, region, country`. `source_url` and
`source_name` are not in it and are not enrichable either, so a revision here
CANNOT be pushed to the live row. Correcting the citation on the page needs
either that allowlist widened or a withdraw-and-republish, and the second is
dangerous in general. Measured here, though, the fingerprint does NOT move:
`source_name` reaches `content_hash` only through `strip_outlet_suffix()`, and
none of these headlines carries a trailing " - Outlet". So the live row could be
corrected in place the day that allowlist grows. This script corrects the repo's
memory and says so; it does not claim the page.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sqlite3
import sys
from urllib.parse import urlparse

import requests

from pipeline import store, validate

#: A browser-ish agent, for the same reason every other fetch here sends one.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "TalentIntel/1.0 (+https://asktherecruiter.com)"
)

NOTE = ("citation re-pointed from an aggregator to the publisher named by the "
        "document's own rel=canonical")

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


class Unsafe(RuntimeError):
    """A correction that would do more than it claims."""


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


def corrected_signal(row: sqlite3.Row, url: str, name: str) -> validate.Signal:
    signal = validate.Signal(**{f: row[f] for f in _FIELDS})
    signal.source_url = url
    signal.source_name = name
    rehashed = validate.content_hash(
        signal.company_key, signal.pillar, signal.published_date,
        signal.headline, signal.source_name)
    # MEASURED, not assumed. `source_name` reaches content_hash only through
    # strip_outlet_suffix(), which removes a trailing " - Outlet" from the
    # headline; the hashed payload itself is
    # company_key|pillar|published_date|normalised_headline. None of these
    # headlines carries an outlet suffix, so the fingerprint does NOT move --
    # verified on both rows. That is what keeps an in-place site correction
    # possible IF tit_correctable_columns() is ever widened, and it is why this
    # is asserted rather than left to be rediscovered.
    if rehashed != row["content_hash"]:
        raise Unsafe(
            f"re-pointing {row['company']} moves its content_hash "
            f"({row['content_hash']} -> {rehashed}), which means the new "
            f"source_name is a suffix of its headline. The live row could then "
            f"never be matched again, so this needs a withdraw-and-republish "
            f"and not this script.")
    return signal


def run(db_path: str, *, apply: bool, session=None) -> int:
    conn = store.connect(db_path) if hasattr(store, "connect") else sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = targets(conn)
    print(f"{len(rows)} current row(s) cited to an aggregator under the "
          f"registrable-domain rule.\n")

    repaired = refused = 0
    for row in rows:
        host = urlparse(row["source_url"]).hostname or ""
        canonical, _aggregator_site_name, why = canonical_of(
            row["source_url"], session=session)
        chost = (urlparse(canonical).hostname or "").lower() if canonical else ""
        print(f"  {row['company']}")
        print(f"    cited     {row['source_url']}")
        if not canonical:
            print(f"    canonical (none) -- {why}")
        else:
            print(f"    canonical {canonical}")

        if not canonical or validate.is_aggregator_host(chost) \
                or not urlparse(canonical).path.strip("/"):
            reason = why if not canonical else (
                "canonicalises to itself" if chost == host.lower()
                else f"canonical is also an aggregator ({chost})"
                if validate.is_aggregator_host(chost) else "canonical is a bare domain")
            print(f"    LEFT ALONE: {reason}. A human decides this one; "
                  f"nothing here retracts.\n")
            refused += 1
            continue

        name = publisher_name(canonical, site_name_of(canonical, session=session))
        signal = corrected_signal(row, canonical, name)
        print(f"    -> {name} ({chost})")
        print(f"    content_hash {row['content_hash'][:12]} unchanged")
        if apply:
            store.revise(conn, row["signal_id"], signal, NOTE)
            print("    REVISED (original survives at is_current = 0)\n")
        else:
            print("    would revise (dry run)\n")
        repaired += 1

    if apply:
        conn.commit()
    print(f"repaired {repaired}, left for a human {refused}, "
          f"{'APPLIED' if apply else 'dry run, nothing written'}.")
    print("\nNOTE: source_url and source_name are not in tit_correctable_columns(), "
          "so this does NOT change the live page. See this file's header.")
    return 0 if not refused else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="data/talent_intel.db")
    p.add_argument("--apply", action="store_true",
                   help="write the revisions. Without it, nothing is written.")
    args = p.parse_args(argv)
    return run(args.db, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
