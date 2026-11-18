"""Weekly finish line: is this tracker at 100%? Shown, not claimed.

Owner ruling 2026-11-18 ("set finish lines"). PASS means ALL of:
  1. every public page returns 200
  2. no broken internal links
  3. every number shown has a source link
  4. a UTM-tagged sign-up CTA is visible at phone (390px) AND desktop (1280px)
  5. the data was refreshed within 7 days (from a data endpoint, never page metadata)

No LLM, no key, no database write. Stdlib plus headless Chrome through cdp.py
(the same client contrast_audit uses), so nothing new is installed.

The SAME file lives in dk-forge/ai-layoff-tracker (railway/finish_line.py) and
dk-forge/talent-intelligence-tracker (finish_line.py). Change both.

Usage:
  python3 finish_line.py --name "Talent Intelligence Tracker" \
      --base https://asktherecruiter.com/blog/talent-intelligence-tracker/ \
      --json finish_line_status.json --summary "$GITHUB_STEP_SUMMARY"
Exit 0 = PASS, 1 = FAIL (UNKNOWN counts as FAIL: "could not check" is never a pass).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = "dk-forge-finish-line/1.0 (+weekly self-check)"
HOST = "asktherecruiter.com"
FRESH_DAYS = 7
PHONE, DESKTOP = 390, 1280
SKIP_PATH = re.compile(r"/(wp-json|wp-admin|wp-login|feed|xmlrpc)\b|\.(xml|json|csv|png|jpe?g|webp|svg|pdf|css|js)$", re.I)

# What counts as "a number shown": grouped thousands, percentages, money, any
# 3+ digit figure, and 1-2 digit COUNTS ("42 jobs added") - a small number
# followed by a lowercase word that is not a time unit or "of". Dates such as
# "31 July" / "Nov 18," and clock times are not counts and stay out. Years are
# dropped in the page (YEARISH). Shared with the tests through this constant.
NUM_PATTERN = (r"(?:[$\u20ac\u00a3]\s?)?\b\d{1,3}(?:,\d{3})+\b|\b\d+(?:\.\d+)?\s?%"
               r"|[$\u20ac\u00a3]\s?\d+(?:\.\d+)?\s?(?:[kKmMbB]n?|million|billion)?|\b\d{3,}\b"
               r"|\b\d{1,2}(?=\s[a-z])(?!\s(?:days?|hours?|minutes?|weeks?|months?|years?|am|pm|of)\b)")

# Run in the page: numbers shown without a source link nearby.
NUMBERS_JS = r"""
(() => {
  const SKIP = 'script,style,noscript,nav,header,footer,form,button,select,option,svg,[aria-hidden="true"],.screen-reader-text';
  const NUM = new RegExp(__NUM__, 'g');
  const YEARISH = /^(19|20)\d{2}$/;
  const BLOCK = 'li,tr,p,dd,dt,td,th,figure,figcaption,blockquote,h1,h2,h3,h4,h5,h6,div';
  // The ROW or CARD a number belongs to: structural containers first, so a
  // badge strip inside a card (e.g. .tit-card-badges in li.tit-card) never
  // stands in for the card that carries the source link.
  const CARD_STRUCT = 'tr,li,article,figure';
  const CARD_CLASS = '[class*="card"]:not([class*="card-"]),[class*="stat"],[class*="kpi"]';
  const sourced = (el) => {
    if (!el) return false;
    for (const a of el.querySelectorAll('a[href]')) {
      const h = a.getAttribute('href') || '';
      if (h.startsWith('#') || /utm_/.test(h)) continue;
      return true;
    }
    return false;
  };
  const root = document.querySelector('main, article, #content, .entry-content') || document.body;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const out = []; let total = 0; const seen = new Set();
  while (walker.nextNode()) {
    const t = walker.currentNode; const p = t.parentElement;
    if (!p || p.closest(SKIP) || p.closest('a')) continue;
    const st = getComputedStyle(p); if (st.display === 'none' || st.visibility === 'hidden') continue;
    const hits = (t.textContent.match(NUM) || []).filter(n => !YEARISH.test(n.trim()));
    if (!hits.length) continue;
    const block = p.closest(BLOCK) || p;
    if (seen.has(block)) continue; seen.add(block);
    total += 1;
    const card = block.closest(CARD_STRUCT) || block.closest(CARD_CLASS);
    if (sourced(block) || sourced(card)) continue;
    out.push((block.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 120));
  }
  return JSON.stringify({total, unsourced: out});
})()
"""

def numbers_js() -> str:
    return NUMBERS_JS.replace("__NUM__", json.dumps(NUM_PATTERN))


# Run in the page: is a UTM-tagged CTA to the product visible at this width?
CTA_JS = r"""
(() => {
  for (const a of document.querySelectorAll('a[href*="utm_"]')) {
    let u; try { u = new URL(a.href); } catch (e) { continue; }
    if (!/(^|\.)asktherecruiter\.com$/.test(u.hostname) || u.pathname.startsWith('/blog')) continue;
    const r = a.getBoundingClientRect(); const st = getComputedStyle(a);
    if (r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none' && parseFloat(st.opacity || '1') > 0)
      return a.href;
  }
  return '';
})()
"""


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.dates: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.hrefs.append(a["href"])
        if tag == "time" and a.get("datetime"):
            self.dates.append(a["datetime"])


def parse(html: str) -> _Links:
    p = _Links()
    p.feed(html)
    return p


def normalise(href: str, page_url: str) -> str | None:
    """Absolute URL without fragment, or None for mailto/tel/js/anchors."""
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    url, _ = urllib.parse.urldefrag(urllib.parse.urljoin(page_url, href))
    return url if url.startswith(("http://", "https://")) else None


def is_internal(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host == HOST or host.endswith("." + HOST)


def in_scope(url: str, base) -> bool:
    """A page of THIS tracker (crawled), as opposed to a link we only check.
    `base` is one prefix or a tuple of them (the tracker home plus its sibling
    route families, e.g. /blog/company-layoffs/)."""
    u = urllib.parse.urlsplit(url)
    bases = (base,) if isinstance(base, str) else tuple(base)
    return url.startswith(bases) and not u.query and not SKIP_PATH.search(u.path)


def data_date(payload, key: str, today: dt.date) -> dt.date | None:
    """Newest date held under `key` anywhere in a JSON payload (e.g. /status
    last_updated, or every row's captured_at). This is the DATA's date: page
    metadata such as JSON-LD dateModified is rewritten on every request and
    would hide stale data, so it is never used."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == key and isinstance(v, str):
                    m = DATE_RE.search(v)
                    if m:
                        try:
                            d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                        except ValueError:
                            continue
                        if d <= today:
                            found.append(d)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(payload)
    return max(found) if found else None


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - UNKNOWN, reported as FAIL
        return None


DATE_RE = re.compile(r"(?<!\d)(20\d{2})-([01]\d)-([0-3]\d)(?!\d)")


def fetch(url: str, timeout: int = 30) -> tuple[int, str]:
    """GET once; a network error or 5xx is retried twice with a pause, so one
    slow answer from a shared host is not reported as a broken page."""
    for attempt in range(3):
        status, body = _fetch_once(url, timeout)
        if status == 200 or (0 < status < 500):
            return status, body
        time.sleep(3 * (attempt + 1))
    return status, body


def _fetch_once(url: str, timeout: int) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            body = r.read().decode("utf-8", "replace") if "html" in ctype else ""
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001 - network failure is a result, not a crash
        return 0, f"{type(e).__name__}"


def crawl(base, max_pages: int, max_links: int, delay: float, get=fetch, scope=()):
    """BFS over the tracker's pages from one start URL or several (the home and
    each sibling route family). Returns (pages, bad_pages, bad_links, truncated)."""
    starts = [base] if isinstance(base, str) else list(base)
    queue, seen, pages = list(starts), set(starts), {}
    prefixes = tuple(starts) + tuple(scope)  # scope: route families with no index page of their own
    bad_pages: list[tuple[str, int]] = []
    link_status: dict[str, int] = {}
    linked_from: dict[str, str] = {}
    truncated = False
    while queue:
        if len(pages) >= max_pages:
            truncated = True
            break
        url = queue.pop(0)
        status, html = get(url)
        pages[url] = html
        if status != 200:
            bad_pages.append((url, status))
            continue
        for href in parse(html).hrefs:
            u = normalise(href, url)
            if not u or not is_internal(u):
                continue
            if in_scope(u, prefixes):
                if u not in seen:
                    seen.add(u)
                    queue.append(u)
            else:
                linked_from.setdefault(u, url)
        time.sleep(delay)
    for u, src in list(linked_from.items())[:max_links]:
        if u in link_status:
            continue
        link_status[u], _ = get(u)
        time.sleep(delay)
    if len(linked_from) > max_links:
        truncated = True
    bad_links = [(u, s, linked_from[u]) for u, s in link_status.items() if s >= 400 or s == 0]
    bad_links += [(u, s, starts[0]) for u, s in bad_pages]
    return pages, bad_pages, bad_links, truncated


def browser_checks(urls: list[str], settle: float = 2.0):
    """(cta_missing[(url, width)], unsourced{url: [text]}, numbers_total). Raises CDPUnavailable."""
    import cdp  # same directory; stdlib-only CDP client
    cta_missing: list[tuple[str, int]] = []
    unsourced: dict[str, list[str]] = {}
    total = 0
    with cdp.Browser(width=DESKTOP, height=900) as b:
        for url in urls:
            for width in (DESKTOP, PHONE):
                b.resize(width, 900 if width == DESKTOP else 844)
                b.navigate(url, settle=settle)
                if not b.eval_js(CTA_JS):
                    cta_missing.append((url, width))
                if width == DESKTOP:
                    res = json.loads(b.eval_js(numbers_js()) or "{}")
                    total += res.get("total", 0)
                    if res.get("unsourced"):
                        unsourced[url] = res["unsourced"]
    return cta_missing, unsourced, total


def verdict(r: dict) -> list[str]:
    """Reasons for FAIL; empty list = PASS."""
    why = []
    if r["bad_pages"]:
        why.append(f"{len(r['bad_pages'])} public page(s) not 200")
    if r["bad_links"]:
        why.append(f"{len(r['bad_links'])} broken internal link(s)")
    if r.get("browser_pages", 0) < r.get("pages_ok", 0):
        why.append(f"citation and CTA checks covered {r['browser_pages']} of {r['pages_ok']} pages "
                   "(UNKNOWN for the rest, so not a pass)")
    if r["truncated"]:
        why.append(f"not every page checked (cap {r['max_pages']} pages / {r['max_links']} links reached)")
    if r.get("browser_error"):
        why.append(f"browser checks could not run: {r['browser_error']}")
    else:
        n = sum(len(v) for v in r["unsourced"].values())
        if n:
            why.append(f"{n} number(s) shown without a source link on {len(r['unsourced'])} page(s)")
        if r["cta_missing"]:
            why.append(f"UTM sign-up CTA not visible on {len(r['cta_missing'])} page/width pair(s)")
    if r["data_date"] is None:
        why.append("no data date from the freshness endpoint (UNKNOWN)")
    elif r["data_age_days"] > FRESH_DAYS:
        why.append(f"data last refreshed {r['data_date']} ({r['data_age_days']} days ago, limit {FRESH_DAYS})")
    return why


def markdown(r: dict) -> str:
    status = "PASS" if not r["reasons"] else "FAIL"
    lines = [f"## Finish line: Trackers 100% ({r['name']}): **{status}**", "",
             f"Checked {r['checked_at']} | {r['pages_checked']} pages crawled, "
             f"{r['browser_pages']} rendered at {PHONE}px and {DESKTOP}px, {r['numbers_total']} numbers looked at.", ""]
    rows = [("Every public page returns 200", not r["bad_pages"] and not r["truncated"]),
            ("No broken internal links", not r["bad_links"]),
            ("Every number has a source link", not r.get("browser_error") and not r["unsourced"]),
            ("UTM sign-up CTA at phone and desktop", not r.get("browser_error") and not r["cta_missing"]),
            (f"Data refreshed within {FRESH_DAYS} days", r["data_date"] is not None and r["data_age_days"] <= FRESH_DAYS)]
    lines += ["| Check | Result |", "|---|---|"] + [f"| {n} | {'PASS' if ok else 'FAIL'} |" for n, ok in rows]
    if r["reasons"]:
        lines += ["", "**Why it failed**", ""] + [f"- {w}" for w in r["reasons"]]
    for title, items in (("Pages not 200", [f"{u} ({s})" for u, s in r["bad_pages"]]),
                         ("Broken internal links", [f"{u} ({s}) on {src}" for u, s, src in r["bad_links"]]),
                         ("CTA missing", [f"{u} at {w}px" for u, w in r["cta_missing"]]),
                         ("Numbers without a source link", [f"{u}: \"{t}\"" for u, ts in r["unsourced"].items() for t in ts[:3]])):
        if items:
            lines += ["", f"<details><summary>{title} ({len(items)})</summary>", ""] + [f"- {i}" for i in items[:40]] + ["", "</details>"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--base", required=True, help="tracker home URL, with trailing slash")
    ap.add_argument("--also", action="append", default=[],
                    help="another route family to crawl as part of this tracker (repeatable)")
    ap.add_argument("--scope", action="append", default=[],
                    help="route family crawled when linked but with no index page to start from (repeatable)")
    ap.add_argument("--fresh-url", required=True, help="JSON endpoint that says when the data last changed")
    ap.add_argument("--fresh-key", required=True, help="key holding the data date, e.g. last_updated")
    ap.add_argument("--max-pages", type=int, default=1500)
    ap.add_argument("--max-links", type=int, default=800)
    ap.add_argument("--browser-pages", type=int, default=400,
                    help="pages rendered in Chrome (hubs first); fewer than all pages is reported UNKNOWN, never PASS")
    ap.add_argument("--delay", type=float, default=0.25)
    ap.add_argument("--json")
    ap.add_argument("--summary")
    a = ap.parse_args(argv)
    today = dt.datetime.now(dt.timezone.utc).date()
    pages, bad_pages, bad_links, truncated = crawl([a.base] + a.also, a.max_pages, a.max_links, a.delay, scope=a.scope)
    d = data_date(fetch_json(a.fresh_url), a.fresh_key, today)
    # Hubs (short paths) first, then a spread of the long tail (company/place pages).
    ok_pages = sorted((u for u in pages if u not in dict(bad_pages)), key=lambda u: (u.count("/"), u))
    sample = ok_pages[: a.browser_pages]
    r = {"name": a.name, "base": a.base, "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
         "pages_checked": len(pages), "bad_pages": bad_pages, "bad_links": bad_links, "truncated": truncated,
         "max_pages": a.max_pages, "max_links": a.max_links,
         "data_date": d.isoformat() if d else None, "data_age_days": (today - d).days if d else None,
         "cta_missing": [], "unsourced": {}, "numbers_total": 0, "browser_pages": len(sample),
         "pages_ok": len(ok_pages)}
    try:
        r["cta_missing"], r["unsourced"], r["numbers_total"] = browser_checks(sample)
    except Exception as e:  # noqa: BLE001 - CDPUnavailable or a crashed Chrome: UNKNOWN, reported
        r["browser_error"] = f"{type(e).__name__}: {e}"[:200]
    r["reasons"] = verdict(r)
    r["status"] = "PASS" if not r["reasons"] else "FAIL"
    md = markdown(r)
    print(md)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as f:
            f.write(md)
    return 0 if r["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
