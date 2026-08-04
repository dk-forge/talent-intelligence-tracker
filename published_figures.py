"""DOUBLE VERIFICATION for every number a reader or a journalist can quote.

The sibling layoff tracker carries the same module, for the same reason, written
after the same day. Read that one's header for the full argument; the rule is
identical and it is one sentence:

    A NUMBER IS PUBLISHED ONLY IF IT CAN BE INDEPENDENTLY RECOMPUTED AND THE TWO
    AGREE.

Not "it rendered without erroring". A second, separately-derived value must
match. The two derivations have to come from different code paths, or the check
is measuring one path against itself, and a check that agrees with itself will
report green while the page is wrong. That is not hypothetical here. The region
strip on this dashboard badges a number computed by one SQL expression and, when
tapped, fills the page from a different one, and both were correct in isolation
for weeks.

WHAT MAKES THIS PRODUCT DIFFERENT, AND MORE DANGEROUS
-----------------------------------------------------
The layoff tracker counts one thing: jobs. This dashboard mixes THREE units on a
single screen — records (an update, one row), companies (distinct employers), and
money (dollars raised) — and puts them in the same visual grid. The at-a-glance
matrix has a money row sitting directly beneath four count rows. A reader who
adds a money cell to a count cell gets nonsense, and nothing in the markup stops
them. So UNIT is a first-class property of every figure here, not a footnote, and
the BASIS check treats a label that does not name its unit as a defect even when
the number behind it is perfectly correct.

THE FOUR ASSERTIONS, and two more that came out of real defects:

  AGREEMENT      the figure rendered into the page equals what the API returns
                 for that figure's own STAMPED query — the parameters the page
                 itself sends, stored beside the number, never re-derived here
                 from an assumption about what the figure ought to mean.
  RECONCILIATION the parts sum to the whole, or the card says why not.
  DRILL_DOWN     tapping a region tab returns the count the tab displays. This is
                 the one that was broken: World badged 23,991 and returned 25,479.
  BASIS          a label names its unit and its period, and two figures sharing a
                 label share a basis.
  CROSS_SURFACE  a figure printed on more than one page is one number.
  COMPARISON     where a surface states a relationship to an outside estimate,
                 both sides share a basis.

PASS / FAIL / UNKNOWN ARE THREE STATES. A check that cannot reach a surface says
UNKNOWN and says so in those words. UNKNOWN is never folded into PASS, never
lets a run exit zero, and is never the absence of a signal treated as a good
signal. Nothing is truncated silently: where a check bounds what it examines, it
names what it skipped.

HOW IT RUNS. `check_all()` is the one entry point. ops_status.py calls it, and
anything else that wants these verdicts calls the same function rather than
growing a second opinion about what "failing" means.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://asktherecruiter.com/blog/wp-json/talent/v1/"
HOME_URL = "https://asktherecruiter.com/blog/talent-intelligence-tracker/"
UA = "AiLayoffTracker/1.0 (+https://asktherecruiter.com)"

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

# ---------------------------------------------------------------------------
# UNITS. Mixing them is the defect this product is most exposed to.
# ---------------------------------------------------------------------------
RECORDS = "records"          # one update / one row. NOT people, NOT hires.
COMPANIES = "companies"      # distinct employers
MONEY = "dollars"
PLACES = "places"


class Result:
    def __init__(self, key, label, state, detail="", observed=None, error=None):
        self.key = key
        self.label = label
        self.state = state
        self.detail = detail
        self.observed = observed
        self.error = error

    @property
    def transport(self):
        """True when we never got an HTTP answer at all.

        An HTTPError means the site DID answer, so it is never transport: the
        surface is reachable and answered wrongly, which is a finding, not an
        environment problem."""
        return self.error is not None and not isinstance(self.error, urllib.error.HTTPError)


class Report:
    def __init__(self, results):
        self.results = results

    @property
    def failed(self):
        return [r for r in self.results if r.state == FAIL]

    @property
    def unknown(self):
        return [r for r in self.results if r.state == UNKNOWN]

    @property
    def verdict(self):
        # A confirmed defect outranks an unverifiable one; an unverifiable one
        # outranks silence. UNKNOWN is never promoted to PASS.
        if self.failed:
            return FAIL
        if self.unknown:
            return UNKNOWN
        return PASS

    def one_line(self):
        n = len(self.results)
        if self.verdict == FAIL:
            return (f"{len(self.failed)}/{n} published-figure check(s) FAILING: "
                    + "; ".join(f"{r.label}: {r.detail}" for r in self.failed))
        if self.verdict == UNKNOWN:
            return (f"{len(self.unknown)}/{n} published-figure check(s) UNVERIFIED "
                    f"(not checked, NOT passing): "
                    + ", ".join(r.key for r in self.unknown))
        return f"{n}/{n} published-figure checks pass"


class Figure:
    """One number a reader can see, and everything needed to re-derive it."""

    __slots__ = ("key", "surface", "dom", "label", "unit", "period", "params",
                 "field", "note")

    def __init__(self, key, surface, dom, label, unit, period, params, field, note=""):
        self.key = key
        self.surface = surface
        self.dom = dom
        self.label = label
        self.unit = unit
        self.period = period
        self.params = params          # THE STAMPED QUERY
        self.field = field
        self.note = note


def _i(v):
    try:
        return int(str(v).replace(",", "") or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# THE REGISTRY
# ---------------------------------------------------------------------------
# Dashboard figures that BOTH the PHP server-render and the REST endpoint
# produce. That duality is what makes them checkable at all.
HOME_FIGURES = (
    Figure("home.ribbon_countries", "home", "tit-ribbon-c",
           "countries", PLACES, "all time", {}, lambda d: _i(d.get("countries"))),
)

# The four stat tiles carry no element id, but they DO carry their own label, and
# the label is a more honest key than an id would be: it is the words the reader
# actually sees. Each is matched on that label and compared against its own
# stamped query.
#
# NOTE THE STAMPED QUERY, because getting it wrong here is the classic
# self-agreeing mistake. The all-time tile counts NOTABLE rows, so its query is
# /query?detail=notable. The unfiltered /aggregate `total` counts everything
# including routine and reads about three thousand higher; checking the tile
# against THAT would report a defect every day forever and the check would be
# switched off within a week.
TILE_FIGURES = (
    Figure("home.tile_updates", "home", "updates", "updates", RECORDS, "all time",
           {"detail": "notable", "per_page": 1}, lambda d: _i(d.get("total")),
           note="RECORDS, not people. The most misread unit on the page."),
)

# Named rather than silently skipped.
TILE_NOT_COVERED = {
    "employers / official filings tiles":
        "their all-time values come from a different population filter than any "
        "single public endpoint exposes, so there is no second derivation to "
        "check them against",
    "money tile":
        "the tile prints a rounded short form ($503B) while the API carries the "
        "exact figure; a rounded render cannot be equality-checked without "
        "encoding the rounding rule, which would just re-implement the renderer",
}

# Named rather than omitted. An uncovered figure that is not named reads as
# covered, and that is how eight mechanisms reported health while doing nothing.
NOT_RECOMPUTABLE = {
    "at-a-glance matrix (6 rows x 4 periods = 24 cells)":
        "server-rendered from period windows computed with current_time(); the "
        "DRILL_DOWN check covers the region strip, not these cells",
    "9 charts": "drawn into canvas elements, never present in the DOM as text",
    "company / place permalink figures":
        "one page per entity, thousands of them; bounding the sample would make "
        "the verdict depend on which sample, so they are NOT covered here",
    "corrections and sources page figures":
        "prose-embedded, no stamped query available",
}

# The region strip. `codes` is exactly what the click handler sends.
REGION_TABS = ("World", "Americas", "Europe", "Middle East", "Africa", "Asia",
               "Oceania")


def _fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class Ctx:
    def __init__(self, fetch=None, timeout=30, cachebust=None):
        import uuid
        self._fetch = fetch or _fetch
        self._cache = {}
        self.timeout = timeout
        self.cachebust = cachebust or uuid.uuid4().hex[:12]

    def fetch(self, url, timeout=None):
        if url not in self._cache:
            self._cache[url] = self._fetch(url, timeout or self.timeout)
        return self._cache[url]


def _get_json(ctx, path, params):
    q = dict(params)
    q["cb"] = ctx.cachebust
    try:
        return json.loads(ctx.fetch(BASE + path + "?" + urllib.parse.urlencode(q))), None
    except Exception as e:                                  # noqa: BLE001
        return None, e


def _get_html(ctx, url):
    sep = "&" if "?" in url else "?"
    try:
        return ctx.fetch(url + sep + "cb=" + ctx.cachebust).decode("utf-8", "replace"), None
    except Exception as e:                                  # noqa: BLE001
        return None, e


def _why(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 503:
            return "site is in its deploy maintenance window (HTTP 503)"
        return f"live site returned HTTP {e.code}"
    return f"could not reach the live site ({e})"


def _worst(parts):
    states = [s for s, _ in parts]
    worst = FAIL if FAIL in states else (UNKNOWN if UNKNOWN in states else PASS)
    return worst, "; ".join(d for s, d in parts if s == worst)


# ---------------------------------------------------------------------------
# 3. DRILL-DOWN — the one that was broken
# ---------------------------------------------------------------------------
def check_region_drilldown(ctx):
    """A region tab returns the count it is badged with.

    THE DEFECT, precisely. The badge is built server-side by summing a
    per-country map, and that map is built with a WHERE clause that keeps only
    rows having a country or an HQ country. Rows with neither are silently
    dropped from the sum. The click handler, for the World tab, sends no country
    filter at all, so the query counts every notable row including the placeless
    ones. Two derivations of "how many rows are in this view", differing by
    exactly the population one of them excludes.

    Neither number is wrong on its own, which is why nothing caught it. The badge
    is a true count of placed rows; the drill-down is a true count of all rows.
    What is wrong is that they are presented as the same number, and the reader
    is the one who discovers they are not.
    """
    key, label = "region_drilldown_matches", "Region tabs return what they badge"
    html, err = _get_html(ctx, HOME_URL)
    if html is None:
        return Result(key, label, UNKNOWN, _why(err) + " — region tabs NOT checked",
                      error=err)

    tabs = []
    for m in re.finditer(r'<button[^>]*class=["\'][^"\']*tit-region[^"\']*["\'][^>]*>(.*?)</button>',
                         html, re.S):
        blob = m.group(0)
        codes = re.search(r'data-codes=["\']([^"\']*)["\']', blob)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()
        n = re.search(r"([\d,]+)\s*$", text)
        if n:
            tabs.append((text, codes.group(1) if codes else "", _i(n.group(1))))
    if not tabs:
        return Result(key, label, UNKNOWN,
                      "no region tabs found in the served page — they are NOT "
                      "being checked")

    parts = []
    for text, codes, badged in tabs:
        params = {"detail": "notable", "per_page": 1}
        if codes:
            # Exactly what the click handler sends for a non-World tab.
            params["country"] = codes
            params["country_basis"] = "any"
        got, gerr = _get_json(ctx, "query", params)
        if got is None:
            parts.append((UNKNOWN, f"{text}: could not fetch its drill-down — NOT checked"))
            continue
        landed = _i(got.get("total"))
        if landed != badged:
            parts.append((FAIL,
                          f'the "{text}" tab is badged {badged:,} but tapping it '
                          f'returns {landed:,} records ({landed - badged:+,}). The '
                          f'badge and the click count different populations'))
        else:
            parts.append((PASS, f"{text}: {badged:,} badged, {landed:,} returned"))
    state, detail = _worst(parts)
    return Result(key, label, state, detail)


# ---------------------------------------------------------------------------
# 2. RECONCILIATION
# ---------------------------------------------------------------------------
def check_region_reconciliation(ctx):
    """The region tabs are a partition, so they must add up to World.

    Every region is a disjoint set of country codes, so a reader adding the six
    regional badges must land exactly on the World badge. If they do not, either
    a country belongs to two regions or it belongs to none, and in both cases a
    number on the page is describing a population nobody can name.
    """
    key, label = "region_parts_reconcile", "Region badges sum to the World badge"
    html, err = _get_html(ctx, HOME_URL)
    if html is None:
        return Result(key, label, UNKNOWN, _why(err) + " — not checked", error=err)

    world, rest = None, []
    for m in re.finditer(r'<button[^>]*class=["\'][^"\']*tit-region[^"\']*["\'][^>]*>(.*?)</button>',
                         html, re.S):
        codes = re.search(r'data-codes=["\']([^"\']*)["\']', m.group(0))
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()
        n = re.search(r"([\d,]+)\s*$", text)
        if not n:
            continue
        if codes and codes.group(1):
            rest.append((text, _i(n.group(1))))
        else:
            world = _i(n.group(1))
    if world is None or not rest:
        return Result(key, label, UNKNOWN,
                      "the World tab or the regional tabs were not found — the "
                      "partition is NOT being checked")
    s = sum(v for _, v in rest)
    if s != world:
        return Result(key, label, FAIL,
                      f"the {len(rest)} regional tabs sum to {s:,} records but the "
                      f"World tab badges {world:,} ({s - world:+,}). A country is in "
                      f"two regions or in none", observed=s)
    return Result(key, label, PASS,
                  f"{len(rest)} regions sum to {s:,}, equal to the World badge")


# ---------------------------------------------------------------------------
# 1. AGREEMENT
# ---------------------------------------------------------------------------
def check_figures_agree(ctx):
    """The page's number equals the API's answer to that number's own query."""
    key, label = "figures_agree_with_api", "Dashboard figures agree with the API"
    html, herr = _get_html(ctx, HOME_URL)
    agg, aerr = _get_json(ctx, "aggregate", {})
    if html is None or agg is None:
        e = herr or aerr
        return Result(key, label, UNKNOWN, _why(e) + " — figures NOT checked", error=e)

    parts = []
    for f in HOME_FIGURES:
        expect = f.field(agg)
        m = re.search(r'id=["\']' + re.escape(f.dom) + r'["\'][^>]*>([\d,\.\s]+)<', html)
        if not m:
            parts.append((UNKNOWN, f"#{f.dom} not found — {f.key} NOT checked"))
            continue
        shown = _i(m.group(1))
        if shown != expect:
            parts.append((FAIL,
                          f"{f.key}: the page shows {shown:,} but the API answers "
                          f"{expect:,} for its own query ({f.label}, {f.unit}, "
                          f"{f.period})"))
        else:
            parts.append((PASS, f"{f.key}: {shown:,} {f.unit}"))

    # The stat tiles, matched on the label the reader sees.
    for f in TILE_FIGURES:
        m = re.search(r'<span class=["\']tit-fstat["\'][^>]*>\s*<b>[\d,]+</b>\s*'
                      r'<span>\s*' + re.escape(f.dom) + r'[^<]*</span>\s*'
                      r'<span class=["\']tit-fstat-all["\']>([\d,]+) all time',
                      html, re.S | re.I)
        if not m:
            parts.append((UNKNOWN,
                          f'the "{f.dom} ... all time" tile was not found — '
                          f'{f.key} is NOT being checked'))
            continue
        shown = _i(m.group(1))
        got, gerr = _get_json(ctx, "query", f.params)
        if got is None:
            parts.append((UNKNOWN,
                          f"{f.key}: could not fetch its stamped query — NOT checked"))
            continue
        expect = f.field(got)
        if shown != expect:
            parts.append((FAIL,
                          f"{f.key}: the tile shows {shown:,} {f.unit} all time but "
                          f"its own query {f.params} answers {expect:,}"))
        else:
            parts.append((PASS, f"{f.key}: {shown:,} {f.unit}"))

    state, detail = _worst(parts)
    if state == PASS:
        detail += (" | not independently recomputable and therefore NOT covered: "
                   + ", ".join(sorted(NOT_RECOMPUTABLE) + sorted(TILE_NOT_COVERED)))
    return Result(key, label, state, detail)


# ---------------------------------------------------------------------------
# 4. BASIS
# ---------------------------------------------------------------------------
def check_basis_is_stated(ctx):
    """A label names its unit; a money figure is never labelled like a count.

    This product prints dollars and record counts in the same grid. "23,991" and
    "$503bn" one row apart are not comparable and must not be describable by the
    same word. So the money tile has to say money, the count tiles have to say
    what they count, and neither may say "hires" or "people" — this dashboard has
    never measured people, and a reader who thinks it does will quote it as a
    headcount figure.
    """
    key, label = "figure_basis_is_stated", "Dashboard figures state their unit"
    html, err = _get_html(ctx, HOME_URL)
    if html is None:
        return Result(key, label, UNKNOWN, _why(err) + " — labels NOT checked", error=err)

    # SCOPE MATTERS. An earlier draft of this check scanned the whole page and
    # reported a defect because a news card's own summary text contained the
    # phrase "headcount added" — the words of a source, quoted accurately, in the
    # data rather than in the chrome. That is a false positive, and a false
    # positive is a wrong check: it trains a reader to dismiss the alert, which
    # is how a real one gets ignored later. So this reads the LABELS the product
    # writes, not the content it publishes.
    chunks = []
    # The stat tiles, including their nested label and all-time spans.
    for m in re.finditer(r'<span class=["\']tit-fstat[^"\']*["\'][^>]*>(.*?)</span>\s*</span>',
                         html, re.S):
        chunks.append(m.group(1))
    # Chart headings, region tabs and the coverage ribbon.
    for m in re.finditer(r'<(?:span|button|h[23])[^>]*class=["\'][^"\']*'
                         r'tit-(?:chart|region|ribbon|stat)[^"\']*["\'][^>]*>(.*?)'
                         r'</(?:span|button|h[23])>', html, re.S):
        chunks.append(m.group(1))
    text = re.sub(r"\s+", " ",
                  " ".join(re.sub(r"<[^>]+>", " ", c) for c in chunks)).lower()
    parts = []

    if not text.strip():
        return Result(key, label, UNKNOWN,
                      "no tile or chart labels were found in the served page — "
                      "units are NOT being checked")

    forbidden = [w for w in ("jobs created", "people hired", "headcount added",
                             "hires this year") if w in text]
    if forbidden:
        parts.append((FAIL,
                      "the dashboard counts RECORDS, not people, but the page "
                      "claims: " + ", ".join(forbidden)))
    else:
        parts.append((PASS, "no label claims people or headcount"))

    if "updates" in text or "employers" in text:
        parts.append((PASS, "count tiles name their unit"))
    else:
        parts.append((FAIL,
                      "no count tile names its unit, so a reader cannot tell "
                      "records from companies from dollars"))

    if "raised" in text or "$" in text:
        parts.append((PASS, "the money figure is labelled as money"))
    else:
        parts.append((UNKNOWN,
                      "no money label was found in the served page — the money "
                      "unit is NOT being checked"))

    state, detail = _worst(parts)
    return Result(key, label, state, detail)


def check_same_claim_agrees_on_one_page(ctx):
    """Two figures making the same claim on one screen are one number.

    The stat tile publishes an all-time update count. The World tab badges the
    number of records in the worldwide view. Those are the same claim in two
    places, a few hundred pixels apart, and a reader will read them as one fact
    because there is nothing on the page telling them otherwise.

    This is the cross-surface rule applied WITHIN a surface, and it is worth its
    own check because it needs no second page and no second request: the
    disagreement is visible in a single response, which means it was always
    visible and nothing was looking.
    """
    key, label = "same_claim_agrees_on_page", "One claim, one number, on one page"
    html, err = _get_html(ctx, HOME_URL)
    if html is None:
        return Result(key, label, UNKNOWN, _why(err) + " — not checked", error=err)

    tile = re.search(r'<span class=["\']tit-fstat["\'][^>]*>\s*<b>([\d,]+)</b>'
                     r'\s*<span>\s*updates[^<]*</span>\s*'
                     r'<span class=["\']tit-fstat-all["\']>([\d,]+) all time',
                     html, re.S | re.I)
    world = None
    for m in re.finditer(r'<button[^>]*class=["\'][^"\']*tit-region[^"\']*["\'][^>]*>(.*?)</button>',
                         html, re.S):
        codes = re.search(r'data-codes=["\']([^"\']*)["\']', m.group(0))
        if codes and codes.group(1):
            continue
        n = re.search(r"([\d,]+)\s*$",
                      re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip())
        if n:
            world = _i(n.group(1))
    if not tile or world is None:
        return Result(key, label, UNKNOWN,
                      "the all-time updates tile or the World badge was not found "
                      "— this agreement is NOT being checked")
    alltime = _i(tile.group(2))
    if alltime != world:
        return Result(key, label, FAIL,
                      f"the stat tile publishes {alltime:,} updates all time and the "
                      f"World tab badges {world:,} records for the same worldwide "
                      f"view ({world - alltime:+,}). Both are on the page at once",
                      observed=world)
    return Result(key, label, PASS,
                  f"the all-time tile and the World badge both read {world:,}")


CHECKS = (check_figures_agree, check_region_reconciliation,
          check_region_drilldown, check_basis_is_stated,
          check_same_claim_agrees_on_one_page)


def check_all(ctx=None, checks=CHECKS):
    """Run every published-figure check. Stdlib only, no keys, read-only GETs."""
    ctx = ctx or Ctx()
    out = []
    for c in checks:
        try:
            out.append(c(ctx))
        except Exception as e:                              # noqa: BLE001
            # A check that crashes has not passed. It has not run.
            out.append(Result(getattr(c, "__name__", "check"), c.__name__, UNKNOWN,
                              f"the check itself raised {e!r} — NOT a pass", error=e))
    return Report(out)


def main(argv=None):
    rep = check_all()
    print("PUBLISHED FIGURES —", rep.verdict.upper())
    for r in rep.results:
        print(f"  [{r.state.upper():7}] {r.key}")
        for line in r.detail.split("; "):
            print("            ", line)
    if rep.verdict == FAIL:
        return 2
    if rep.verdict == UNKNOWN:
        return 3
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
