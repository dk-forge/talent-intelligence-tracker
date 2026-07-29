#!/usr/bin/env python3
"""Check that the documents we cite are still there, and still themselves.

    python3 link_check.py --dry-run                 # check nothing, show the plan
    python3 link_check.py --dry-run --limit 40      # fetch and report, write nothing
    python3 link_check.py --limit 200               # check and record
    python3 link_check.py --random --limit 200      # a representative rot rate
    python3 link_check.py --collector national_press --limit 100

WHY THIS IS DIFFERENT FROM THE SIBLING'S VERSION
------------------------------------------------
The AI Layoff Tracker checks its own public pages and samples source links as an
afterthought, because most of its rows point at state WARN registries that a
government keeps up. Here the source link IS the product: "every update links to
the filing or report behind it" and "no figure appears unless the source states
it". A dead source link does not inconvenience a reader, it silently converts a
sourced claim into an unsourced one, and the page looks exactly the same
afterwards. With 575 publisher feeds across 139 countries in the catalogue, many
of them small national outlets, rot is certain rather than hypothetical.

THE CASE STATUS CODES CANNOT CATCH
----------------------------------
A URL whose FINAL domain after redirects is not the domain we stored is the most
dangerous outcome available, and it answers 200. `botswanaguardian.co.bw` now
redirects to a betting site whose feed verified perfectly green: valid RSS,
recent items, every automated check passing. A cited article that quietly
becomes a casino is worse than a 404, because a 404 is visibly broken and this
is not. So the drift guard `collectors/national_press.py` already applies to
FEEDS is applied here to stored ARTICLE URLs, reusing the same
`registrable_domain()` rather than growing a second implementation that would
eventually disagree with the first.

WHAT IT WILL NOT DO
-------------------
It never deletes, retracts, revises or edits a signal. It writes to
`source_links` and nowhere else. A dead link is recorded and surfaced (here, in
ops_status.py, and in the weekly digest); deciding what to do about one is a
separate step with a human in it, on purpose. Automatic retraction on an HTTP
code would mean a publisher's bad afternoon silently deleting evidence.

NOT A WORDPRESS BROKEN-LINK-CHECKER PLUGIN, AND THIS IS NOT A PREFERENCE
------------------------------------------------------------------------
If you are ever tempted to replace this with one of the off-the-shelf plugins:
they crawl POST CONTENT. Every source link we hold lives in the custom
`wp_tit_signals` table (and in this repo's SQLite), never in a post body. Such a
plugin would check a handful of prose links, find them fine, and paint a green
badge over 13,893 entirely unchecked source URLs. That is the exact
false-healthy failure this project keeps finding, and it is worse than having no
checker, because it arrives with a reassuring number attached.

COST
----
Zero. No model is called, ever. This is HTTP and a SQLite write.

POLITENESS
----------
One host at a time with a gap between requests to the same host, a browser-ish
User-Agent that names us, and `robots.txt` respected through
`collectors.national_press.robots_allows` — the helper that already exists,
rather than a second one. A publisher that disallows the path is recorded
`robots` and not fetched, and is never counted as broken: they told us their
terms and we are not entitled to a verdict on a document we did not ask for
properly.

Exit codes: 0 the check ran | 1 the check itself could not run
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent

# Reused rather than re-declared: one drift guard, one robots reader, one
# User-Agent. A second copy of any of them would drift from the original and
# the divergence would show up as a phantom rot spike.
from collectors.national_press import (  # noqa: E402
    PER_HOST_PAUSE,
    USER_AGENT,
    registrable_domain,
    robots_allows,
)
from pipeline import schema, source_links, store  # noqa: E402

TIMEOUT = 25

# A bot wall is not a dead document. A human with a browser still reaches the
# article, so a 403 from a publisher that fronts Cloudflare is not rot and must
# never be counted as it — doing so would report every paywalled publisher in
# the catalogue as broken and bury the two states that matter.
WALLED_CODES = frozenset({401, 402, 403, 405, 406, 429})
DEAD_CODES = frozenset({404, 410})


def classify(status: int, final_url: str, source_url: str) -> tuple[str, str]:
    """(state, detail) for one observation. Pure: tested without a network.

    Drift is checked BEFORE the status code, because a hijacked domain answers
    200 and would otherwise be filed as the healthiest thing in the table.
    """
    expected = registrable_domain(source_url)
    landed = registrable_domain(final_url or source_url)
    if status and expected and landed and landed != expected:
        # A cross-domain CONSENT GATE is not a takeover, and European
        # publishers are full of them: hln.be bounces to
        # myprivacy.dpgmedia.be/consent?...callbackUrl=https%3A%2F%2Fwww.hln.be%2F...
        # A gate carries the original URL with it, because its whole job is to
        # send the reader back. A squatter has no reason to. So the tell is
        # whether the page we landed on still names the document we asked for,
        # and using it keeps `drifted` meaning "somebody else is serving this
        # now" instead of degrading into a list of cookie banners nobody reads.
        from urllib.parse import unquote

        haystack = (final_url + " " + unquote(final_url)).lower()
        if expected in haystack:
            return "walled", (
                f"HTTP {status}: bounced to a consent or privacy gate on "
                f"{landed}, which carries the article URL back with it. The "
                f"document is still the publisher's.")
        return "drifted", (
            f"HTTP {status} but the final domain is {landed}, not {expected}. "
            f"The domain may have changed hands: verify before citing it, and "
            f"do NOT assume a 200 means the article is still there.")
    if status == 0:
        return "unreachable", "the request never completed (DNS, TLS or timeout)"
    if status in DEAD_CODES:
        return "dead", f"HTTP {status}: the document is gone"
    if status in WALLED_CODES:
        return "walled", f"HTTP {status}: bot wall or paywall, still a live document"
    if status >= 500:
        return "error", f"HTTP {status}: the publisher's server is failing"
    if 200 <= status < 400:
        return "live", f"HTTP {status}"
    return "error", f"HTTP {status}: unexpected"


def probe(url: str, session, timeout: int = TIMEOUT) -> tuple[int, str]:
    """(status, final_url). Returns 0 on a transport failure and never raises.

    A monitoring job that can crash on the thing it monitors is a monitoring job
    that reports "healthy" by dying quietly in a workflow nobody reads.

    GET rather than HEAD: enough publishers answer 405 or a bare 200 to a HEAD
    regardless of whether the article exists that HEAD measures the CDN and not
    the document. `stream=True` plus an immediate close means the body is never
    downloaded, so the cost is one round trip either way.
    """
    try:
        resp = session.get(url, headers={"User-Agent": USER_AGENT},
                           timeout=timeout, allow_redirects=True, stream=True)
        status, final = resp.status_code, (getattr(resp, "url", "") or url)
        resp.close()
        return status, final
    except Exception:
        return 0, ""


def run(conn, *, limit: int, collector: str | None, dry_run: bool,
        recheck_days: int, shuffle: bool, pause: float,
        session=None, sleep=time.sleep) -> dict:
    import requests

    session = session or requests.Session()
    candidates = source_links.check_candidates(
        conn, limit=limit, collector=collector,
        recheck_after_days=recheck_days, shuffle=shuffle)

    if not candidates:
        print("Nothing due a check.")
        return {"checked": 0, "states": {}, "drifted": [], "dead": []}

    # One host at a time. Grouping first means a publisher carrying forty of our
    # citations is visited forty times in a row with a gap, rather than being
    # interleaved with every other host and hit whenever the shuffle says so.
    by_host: dict[str, list[dict]] = {}
    for row in candidates:
        by_host.setdefault((urlparse(row["source_url"]).hostname or "").lower(),
                           []).append(row)

    states = Counter()
    drifted, dead = [], []
    checked = 0

    for host in sorted(by_host):
        for i, row in enumerate(by_host[host]):
            url = row["source_url"]
            if i:
                sleep(pause)

            if not robots_allows(url, session=session):
                state, detail, status, final = (
                    "robots",
                    "robots.txt disallows this path: the publisher's own terms",
                    None, "")
            else:
                status, final = probe(url, session)
                state, detail = classify(status, final, url)

            checked += 1
            states[state] += 1
            landed = registrable_domain(final or url)

            if state == "drifted":
                drifted.append((url, final, detail))
                print(f"  ::error::DRIFTED {url}\n            -> {final}")
            elif state == "dead":
                dead.append((url, status))
                print(f"  DEAD    {status}  {url}")
            elif state not in ("live", "walled"):
                print(f"  {state.upper():<11} {status}  {url}")

            if not dry_run:
                source_links.record_check(
                    conn, url, state=state, http_status=status,
                    final_url=final, final_domain=landed, detail=detail,
                    source_name=row.get("source_name") or "", host=host)

    rot = sum(states[s] for s in source_links.ROT_STATES)
    reachable = sum(states[s] for s in source_links.REACHABLE_STATES)
    rot_pct = round(100.0 * rot / checked, 1) if checked else 0.0

    print(f"\n{checked} source URL(s) checked: {reachable} reachable, "
          f"{rot} rotted ({rot_pct}%)")
    print("  " + ", ".join(f"{s}={n}" for s, n in sorted(states.items())))

    return {"checked": checked, "states": dict(states), "rot": rot,
            "rot_pct": rot_pct, "drifted": drifted, "dead": dead}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="check and print, record nothing")
    parser.add_argument("--plan-only", action="store_true",
                        help="show what would be checked and make no request")
    parser.add_argument("--limit", type=int, default=120,
                        help="URLs to check this run (default 120)")
    parser.add_argument("--collector", default=None,
                        help="only URLs stored by this collector")
    parser.add_argument("--recheck-days", type=int, default=30,
                        help="re-check a URL this many days after its last check")
    parser.add_argument("--random", dest="shuffle", action="store_true",
                        help="sample the corpus at random, for a representative "
                             "rot rate rather than working the queue")
    parser.add_argument("--pause", type=float, default=PER_HOST_PAUSE,
                        help="seconds between requests to the SAME host")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)

    conn = schema.connect(args.db)
    try:
        if args.plan_only:
            plan = source_links.check_candidates(
                conn, limit=args.limit, collector=args.collector,
                recheck_after_days=args.recheck_days, shuffle=args.shuffle)
            hosts = Counter((urlparse(r["source_url"]).hostname or "") for r in plan)
            print(f"PLAN: {len(plan)} URL(s) across {len(hosts)} host(s); "
                  f"no request made.")
            for host, n in hosts.most_common(15):
                print(f"    {n:>4}  {host}")
            print(source_links.rot_summary(conn))
            return 0

        result = run(conn, limit=args.limit, collector=args.collector,
                     dry_run=args.dry_run, recheck_days=args.recheck_days,
                     shuffle=args.shuffle, pause=args.pause)

        if args.dry_run:
            print("\ndry run: nothing recorded")
            return 0

        summary = source_links.rot_summary(conn)
        detail = (f"{result['checked']} checked this run, {result.get('rot', 0)} "
                  f"rotted ({result.get('rot_pct', 0)}%); ledger: "
                  f"{summary['checked']}/{summary['distinct_source_urls']} distinct "
                  f"source URLs checked, {summary['rot']} rotted "
                  f"({summary['rot_pct']}%), {summary['archived']} archived")

        # DEGRADED on drift only. Rot is expected decay and the Wayback snapshot
        # is its backstop, so a rot rate is reported rather than alarmed. A
        # DRIFTED domain is not decay: it is a source we would cite being served
        # by somebody else, and that needs a human this week.
        status = "degraded" if result["drifted"] else "ok"
        if result["drifted"]:
            detail = (f"{len(result['drifted'])} source URL(s) now resolve to a "
                      f"DIFFERENT domain (possible takeover): "
                      + ", ".join(u for u, _, _ in result["drifted"][:3])
                      + " | " + detail)

        # A run with nothing due is healthy, not empty: report the LEDGER size so
        # report_health's "zero found is degraded" rule (right for a collector,
        # wrong for a rotation that has caught up) does not invent an incident.
        store.report_health(conn, "link_check", status=status,
                            items_found=result["checked"] or summary["checked"],
                            items_stored=result["checked"], detail=detail)
        conn.commit()

        print("\n" + detail)
        worst = source_links.rot_by_publisher(conn)
        if worst:
            print("\nrot by publisher (a publisher that changed its URL scheme "
                  "shows up here first):")
            for row in worst[:10]:
                print(f"    {row['rot_pct']:>5}%  {row['rot']}/{row['checked']}  "
                      f"{row['host']}"
                      + (f"  ({row['drifted']} DRIFTED)" if row["drifted"] else ""))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
