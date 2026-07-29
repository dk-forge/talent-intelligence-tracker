#!/usr/bin/env python3
"""Fetch EVERY URL in the company sitemap and prove each one is a clean 200.

Why this exists, and why it fetches all of them.

The company sitemap shipped 712 URLs on plugin 1.45.4. Twenty were sampled by
hand and all twenty passed. Twenty-two of them were broken: an employer key
containing "&" was written into <loc> as the XML entity `&#038;`, which any
consumer that does NOT resolve the entity reads as a literal ampersand, so the
URL truncates at it, 301s to /company/b-&/ and 404s. The sample missed every
one, because the sample resolved the entity and the failure only appears when
you do not.

The lesson is not "sample better". A sitemap is a list of promises and the only
check that verifies a list of promises is checking all of them. 712 requests
against our own host takes about a minute and costs nothing.

What counts as a pass, per URL:

  * HTTP 200 with redirects DISABLED. A 301 is a failure even when it lands on
    a 200, because a sitemap full of redirects is a "Page with redirect" report
    in Search Console.
  * No robots directive of noindex, in the header or the meta tag. A noindex URL
    inside a sitemap is the exact defect the sibling tracker was reported for.
  * The RAW <loc> text carries no "&" and no "%". Both are decoder-dependent
    here: %26 does not survive the rewrite, and &#038; resolves for some readers
    and not others. This is checked against the raw XML rather than the parsed
    tree, because the parsed tree is what hid the bug.

Usage:
    python3 check_sitemap_urls.py                  # the live sitemap
    python3 check_sitemap_urls.py --limit 50       # a quicker smoke run
    python3 check_sitemap_urls.py --url <sitemap>  # somewhere else

Exit 0 if every URL passed, 1 otherwise. No keys, no dependencies.
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

# ModSecurity on this host rejects the default urllib and curl agents alike.
UA = "TalentIntel/1.0 (+https://asktherecruiter.com)"
SITEMAP = ("https://asktherecruiter.com/blog/talent-intelligence-tracker/"
           "company-sitemap.xml")
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is a result to report, not a hop to follow."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def fetch(url, timeout=45):
    """Return (status, headers, body). Never raises for an HTTP status."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        resp = OPENER.open(req, timeout=timeout)
        return resp.status, resp.headers, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, ""
    except Exception as exc:  # network, TLS, timeout
        return f"ERR {type(exc).__name__}", {}, ""


def check_one(url, attempts=3):
    """Return a list of complaints about this URL. Empty means it passed.

    Transient 5xx are RETRIED rather than reported. This is shared hosting and
    it 502s and 504s at random under load (gotcha 8 in CLAUDE.md); a checker
    that reports those as broken URLs trains its reader to skim the failures,
    which is how a real one gets missed.

    The backoff is deliberately long. A first pass retried after 1.5s and 3s and
    still reported one URL as a hard 504; the same URL answered 200 in 2.4s a
    minute later. All three attempts had landed inside one bad window, which is
    a checker measuring its own impatience. Seconds are free here and a false
    failure is not.
    """
    for attempt in range(attempts):
        status, headers, body = fetch(url)
        if not (isinstance(status, int) and 500 <= status < 600):
            break
        if attempt < attempts - 1:
            time.sleep(5 * (attempt + 1))

    problems = []

    if status != 200:
        location = headers.get("Location", "") if headers else ""
        problems.append(f"HTTP {status}" + (f" -> {location}" if location else ""))
        return problems

    header_robots = (headers.get("X-Robots-Tag", "") if headers else "")
    if "noindex" in header_robots.lower():
        problems.append(f"X-Robots-Tag: {header_robots}")

    metas = re.findall(r'<meta name="robots" content="([^"]*)"', body)
    if any("noindex" in m.lower() for m in metas):
        problems.append(f"meta robots: {metas}")
    if len(metas) > 1:
        problems.append(f"{len(metas)} robots tags: {metas}")

    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=SITEMAP)
    ap.add_argument("--limit", type=int, default=0, help="check only the first N")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    # Cache-buster on THIS ONE request only. Cloudflare and the host page cache
    # will happily serve the previous deploy's sitemap, and checking a stale
    # file tells you about a deploy that is no longer running. The 690 page
    # fetches below deliberately do NOT carry one: appending a random query to
    # every request bypasses the edge and hammers the origin, which is what
    # shared hosting throttles.
    sep = "&" if "?" in args.url else "?"
    status, _, xml = fetch(f"{args.url}{sep}cb={random.randint(1, 10**9)}")
    if status != 200:
        print(f"sitemap itself answered {status}", file=sys.stderr)
        return 1

    # The RAW text first, deliberately. Parsing resolves the entity and that is
    # what made a broken URL look fine.
    raw_locs = re.findall(r"<loc>(.*?)</loc>", xml, re.S)
    unsafe = [loc for loc in raw_locs if "&" in loc or "%" in loc]

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        print(f"sitemap is not well-formed XML: {exc}", file=sys.stderr)
        return 1
    urls = [u.find("s:loc", NS).text for u in root.findall("s:url", NS)]
    if args.limit:
        urls = urls[:args.limit]

    print(f"sitemap: {args.url}")
    print(f"  {len(raw_locs)} <loc> entries, {len(urls)} parsed")
    if unsafe:
        print(f"  {len(unsafe)} carry a decoder-dependent character in the RAW text:")
        for loc in unsafe[:10]:
            print(f"    {loc}")
        if len(unsafe) > 10:
            print(f"    ... and {len(unsafe) - 10} more")
    print(f"  fetching all {len(urls)} with redirects disabled, "
          f"{args.workers} at a time")

    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for url, problems in zip(urls, pool.map(check_one, urls)):
            if problems:
                failures.append((url, problems))

    print()
    print(f"fetched {len(urls)}, clean {len(urls) - len(failures)}, "
          f"failed {len(failures)}")
    for url, problems in failures[:40]:
        print(f"  FAIL {url}")
        for p in problems:
            print(f"       {p}")
    if len(failures) > 40:
        print(f"  ... and {len(failures) - 40} more")

    return 1 if (failures or unsafe) else 0


if __name__ == "__main__":
    sys.exit(main())
