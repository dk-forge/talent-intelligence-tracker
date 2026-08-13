"""The employer identity spine: who the employer IS, resolved deterministically.

`company_key` is a normalised name. It is the only employer key the pipeline
has, and a name collapses the moment two employers share one — there are three
"Cornerstone" filers and two "Atlas Energy"s in the live table already. This
module fills the columns that do not collapse (`cik`, `ticker`) and the ones
that make an employer findable by place and kind (`hq_city`, `hq_country`,
`employer_type`).

Everything here is free and deterministic. No model is ever called: an LLM
guessing a ticker from a company name is exactly the plausible-but-wrong fact
this product cannot carry, and it would guess with total confidence.

Three authorities, each used only for what it is actually authoritative about:

- **SEC `company_tickers.json`** (public domain) is the ONLY ticker and CIK
  authority. Wikidata's P249 is not: Apple's returns 6689, its Tokyo listing.
  Matched on the normalised company name, and only when the name maps to
  exactly one CIK.
- **Wikidata** (CC0, no key, no attribution) gives headquarters and kind of
  organisation, via `wbsearchentities` for a QID then one batched SPARQL call.
- Nothing else. GLEIF was considered for legal-entity country and is not here:
  it duplicates what Wikidata already answers, on names that match worse.

Three rules govern every write, and each is enforced in exactly one place:

1. **A sourced value always beats a derived one.** A CIK read out of the EFTS
   hit that produced the filing, or a ticker the article printed, is a fact
   about a document. Everything this module produces is an inference from a
   name. So enrichment fills BLANKS and never overwrites — see `enrich()` and
   the WHERE clauses in `backfill()`.
2. **Resolution is cached per employer, forever.** Employers repeat, and the
   SEC filers among them recur every quarter. Negative results are cached too:
   807 of the 3,604 employers resolved so far are names Wikidata does not
   know, and each of them is asked about once rather than on every run.
3. **It fails open, always.** A timeout, a 429, a malformed response or an
   ambiguous match leaves every field exactly as it was. Nothing in this module
   may raise into the pipeline; identity is a nice-to-have and ingestion is not.

Run the backfill over rows that predate this module:

    python -m pipeline.identity --backfill --limit 200

Idempotent and safe to interrupt: each employer commits as it resolves, and a
second run skips everything already cached.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import vocab

# --- Being a good citizen ---------------------------------------------------

# Both SEC and Wikidata ask for a descriptive agent with a contact address, and
# SEC enforces it: a browser-shaped UA gets "Request Rate Threshold Exceeded"
# and an empty one gets a 403. Same shape and the same env override as
# collectors/sec_edgar.py, and `or` rather than a get() default for the same
# reason it uses one: a workflow mapping a MISSING secret into env sets the
# variable to empty string, which a default never sees.
USER_AGENT = (os.environ.get("EDGAR_USER_AGENT") or "").strip() \
    or "TalentIntel/1.0 (info@asktherecruiter.com)"

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WDQS = "https://query.wikidata.org/sparql"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Wikidata publishes no numeric read limit, only "be reasonable". Three
# requests a second from ONE serial client, with Retry-After honoured below,
# is inside that by any reading — and the backfill batches its SPARQL twelve
# employers at a time, so the load on the expensive endpoint is a twelfth of
# what the request count suggests. SEC publishes an actual number, 10/s, and
# 0.15 is the interval collectors/sec_edgar.py already uses against it.
MIN_INTERVAL = {"query.wikidata.org": 0.35, "wikidata.org": 0.35, "sec.gov": 0.15}
TIMEOUT = 30

# After this many consecutive network failures the module stops trying for the
# rest of the process. A blocked egress or a WDQS outage should cost one round
# of timeouts, not one per employer.
_FAILURE_BUDGET = 8

_last_call: dict[str, float] = {}
_consecutive_failures = 0


def _host_of(url: str) -> str:
    for host in MIN_INTERVAL:
        if host in url:
            return host
    return "other"


def _throttle(url: str) -> None:
    host = _host_of(url)
    gap = MIN_INTERVAL.get(host, 1.0)
    last = _last_call.get(host)
    if last is not None:
        wait = gap - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_call[host] = time.monotonic()


def _get_json(url: str, params: dict | None = None, *, accept: str = "application/json"):
    """One GET, returning parsed JSON or None. NEVER raises.

    Rule 3. Every caller in this module treats None as "leave the fields alone",
    which is why no exception needs to escape: there is no failure mode here
    that is worth more than the blank it would replace.
    """
    global _consecutive_failures
    if _consecutive_failures >= _FAILURE_BUDGET:
        return None

    # Imported here, not at module scope: pipeline/validate.py imports this
    # module, half the test suite imports validate, and requests is not
    # installed on the machine that runs those tests. A module-scope import
    # would make an unrelated test file fail to collect.
    try:
        import requests
    except ImportError:
        _consecutive_failures = _FAILURE_BUDGET
        return None

    for attempt in range(2):
        _throttle(url)
        try:
            resp = requests.get(
                url,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": accept},
                timeout=TIMEOUT,
            )
        except Exception:
            _consecutive_failures += 1
            return None

        if resp.status_code in (429, 503):
            # Honour Retry-After when it is a plain number of seconds, and cap
            # it: a service asking us to wait an hour is a service to skip.
            delay = 2.0
            header = (resp.headers or {}).get("Retry-After", "")
            try:
                delay = min(float(str(header).strip()), 10.0)
            except (TypeError, ValueError):
                pass
            if attempt == 0:
                time.sleep(delay)
                continue
            _consecutive_failures += 1
            return None

        if resp.status_code != 200:
            _consecutive_failures += 1
            return None

        try:
            data = resp.json()
        except Exception:
            _consecutive_failures += 1
            return None
        _consecutive_failures = 0
        return data
    return None


# --- What counts as an organisation -----------------------------------------

# The failure mode this list exists to stop: `wbsearchentities` for "NASA"
# returns the plant genus Nasa FIRST and the space agency second, and for
# "Stripe" it returns the payment company first but a Gremlins character and a
# beetle family right behind it. Taking the top hit is how a beetle acquires a
# headquarters.
#
# Rather than enumerate the thousands of Wikidata classes an employer can be an
# instance of, the SPARQL walks `P31/P279*` up to these roots. A plant genus
# never reaches "organization"; a bank holding company, a rural electric
# cooperative and a teaching hospital all do.
ORG_ROOTS = (
    "Q43229",    # organization
    "Q891723",   # public company
    "Q4830453",  # business
    "Q783794",   # company
    "Q6881511",  # enterprise
    "Q327333",   # government agency
    "Q163740",   # nonprofit organization
    "Q3918",     # university
    "Q2385804",  # educational institution
    "Q16917",    # hospital
    "Q4287745",  # medical organization
    "Q31855",    # research institute
)

# And the reason the allow-list alone is not enough. Wikidata's P279 graph is
# edited by hand and is full of shortcuts, so the transitive closure leaks: a
# French commune reaches "company", a city in North Carolina reaches
# "government agency", and New Caledonia reaches "organization". Every one of
# those was resolved as an employer by the first backfill — "Curis, Inc."
# became Curis-au-Mont-d'Or, "Graham Corporation" became Graham NC, "Merlin,
# Inc." became Merlines — and each arrived with a headquarters in France.
#
# A place is never an employer, whatever the ontology says, so the deny-list is
# checked FIRST and wins outright. Q7188 (government) came out of the allow
# list at the same time and for the same reason: every commune in France is
# under it.
DENY_ROOTS = (
    "Q56061",    # administrative territorial entity (communes, counties, states)
    "Q486972",   # human settlement (city, town, village)
    "Q3624078",  # sovereign state
    "Q82794",    # geographic region
    "Q16521",    # taxon — the plant genus Nasa arrives here
    "Q5",        # human
    "Q4167410",  # Wikimedia disambiguation page
    "Q13442814",  # scholarly article
    "Q11424",    # film
    "Q7725634",  # literary work
    "Q2188189",  # musical work
)

# Which root wins when several match, mapped onto our closed vocabulary
# (vocab.EMPLOYER_TYPES). Order is precedence, most specific first: Alphabet is
# an instance of business AND of public company, and "public" is the useful one.
# A root that maps to None is accepted as an organisation but says nothing
# about kind — "organization" and "enterprise" are true of everything.
#
# Read from the DIRECT P31 values, not from the closure, and that is the whole
# point of the table. Through the closure "government agency" is reachable from
# "employment agency", so HireQuest — a staffing firm listed as HQI — came back
# as a government body. Government, education and nonprofit are precise claims
# about an employer and a reader will believe them, so they are only ever made
# when Wikidata states the class outright.
#
# The order was set by the answers it gives, not by taxonomy. Mayo Clinic is an
# instance of both nonprofit and educational institution, and "Nonprofit" is
# the truer label for a hospital system; MIT is an instance of university, and
# "Education" is the truer label for that. So the specific class (university)
# outranks nonprofit and the generic one (educational institution) sits below.
_TYPE_BY_INSTANCE = (
    ("Q891723", "public"),      # public company
    ("Q3918", "education"),     # university
    ("Q875538", "education"),   # public university
    ("Q902104", "education"),   # private university
    ("Q327333", "government"),  # government agency
    ("Q1752939", "government"),  # federal agency of the United States
    ("Q1326624", "government"),  # state-owned enterprise
    # NHS trusts are public bodies, and there are ninety of them among the
    # employers here. Their P279 chain reaches "company" and nothing else this
    # table knows, so without these two they all came back "Private company".
    ("Q6954187", "government"),  # NHS foundation trust
    ("Q6954197", "government"),  # NHS trust
    ("Q163740", "nonprofit"),   # nonprofit organization
    ("Q79913", "nonprofit"),    # non-governmental organization
    ("Q708676", "nonprofit"),   # charitable organization
    ("Q31855", "nonprofit"),    # research institute
    ("Q2385804", "education"),  # educational institution — generic, so it loses
    ("Q18388277", "private"),   # technology company
    ("Q1058914", "private"),    # software company
    ("Q4830453", "private"),    # business
    ("Q783794", "private"),     # company
    ("Q6881511", "private"),    # enterprise
)

# The closure may only ever conclude "public" or "private". Both are safe to be
# vague about — every employer is one or the other — and neither carries the
# weight that calling a staffing agency a government body does.
_CLOSURE_PUBLIC = "Q891723"
_CLOSURE_PRIVATE = ("Q4830453", "Q783794", "Q6881511")

_QID = re.compile(r"^Q\d+$")


def is_organization(roots) -> bool:
    """No organisation root, or any deny root, means no resolution.

    The deny check comes first and is absolute: a place, a person, a species or
    a film is not an employer however the P279 graph happens to be wired.
    """
    got = set(roots or ())
    if got & set(DENY_ROOTS):
        return False
    return bool(got & set(ORG_ROOTS))


def employer_type_from(instances, roots) -> str | None:
    got = set(instances or ())
    for qid, kind in _TYPE_BY_INSTANCE:
        if qid in got:
            # Normalised rather than returned raw, so this module cannot widen
            # the vocabulary by accident — same rule as every other column.
            return vocab.normalize_employer_type(kind)
    closure = set(roots or ())
    if _CLOSURE_PUBLIC in closure:
        return "public"
    if closure & set(_CLOSURE_PRIVATE):
        return "private"
    return None


# --- The cache ---------------------------------------------------------------

# Mirrors schema.TABLES so a connection this module was handed (a test's
# tmp_path database, say) works whether or not it came from schema.connect().
CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS employer_identity (
    company_key   TEXT PRIMARY KEY,
    company       TEXT,
    qid           TEXT,
    ticker        TEXT,
    cik           TEXT,
    hq_city       TEXT,
    hq_country    TEXT,
    employer_type TEXT,
    resolved      INTEGER NOT NULL DEFAULT 0,
    detail        TEXT,
    resolved_at   TEXT NOT NULL
);
"""


@dataclass
class Identity:
    company_key: str
    company: str = ""
    qid: str | None = None
    ticker: str | None = None
    cik: str | None = None
    hq_city: str | None = None
    hq_country: str | None = None
    employer_type: str | None = None
    resolved: bool = False
    detail: str = ""

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.ticker, self.cik, self.hq_city, self.hq_country, self.employer_type)
        )


def ensure_cache(conn: sqlite3.Connection) -> None:
    """Create the table if this connection can. Read-only connections cannot,
    and that is fine: on those the table already exists or there is no cache."""
    try:
        conn.executescript(CACHE_TABLE)
    except sqlite3.OperationalError:
        pass


_CACHE_COLUMNS = ("company", "qid", "ticker", "cik", "hq_city", "hq_country",
                  "employer_type", "resolved", "detail")


def cache_get(conn: sqlite3.Connection, company_key: str) -> Identity | None:
    ensure_cache(conn)
    # Columns named explicitly rather than SELECT *: this is handed connections
    # that may or may not carry a Row factory, so positional access has to be
    # meaningful on its own.
    row = conn.execute(
        f"SELECT {', '.join(_CACHE_COLUMNS)} FROM employer_identity WHERE company_key = ?",
        (company_key,),
    ).fetchone()
    if row is None:
        return None
    company, qid, ticker, cik, hq_city, hq_country, employer_type, resolved, detail = row
    return Identity(
        company_key=company_key,
        company=company or "",
        qid=qid,
        ticker=ticker,
        cik=cik,
        hq_city=hq_city,
        hq_country=hq_country,
        employer_type=employer_type,
        resolved=bool(resolved),
        detail=detail or "",
    )


def cache_put(conn: sqlite3.Connection, ident: Identity) -> None:
    """Write a resolution, positive or negative.

    A negative is a real result and is stored as one: rule 2. Without it every
    run re-asks Wikidata about the 400-odd shell-company filers it has never
    heard of, which is the slowest possible way to learn nothing.
    """
    ensure_cache(conn)
    conn.execute(
        """INSERT OR REPLACE INTO employer_identity
           (company_key, company, qid, ticker, cik, hq_city, hq_country,
            employer_type, resolved, detail, resolved_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ident.company_key, ident.company, ident.qid, ident.ticker, ident.cik,
            ident.hq_city, ident.hq_country, ident.employer_type,
            1 if ident.resolved else 0, ident.detail,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


# --- SEC: the ticker and CIK authority ---------------------------------------

SEC_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / ".cache" / "sec_company_tickers.json"
SEC_CACHE_MAX_AGE = timedelta(days=7)

_sec_index: dict[str, tuple[str, str]] | None = None


def _build_sec_index(payload: dict) -> dict[str, tuple[str, str]]:
    """Normalised name -> (ticker, cik), for names that map to exactly one CIK.

    Two share classes of one company are not ambiguity: they are one employer
    with one CIK, so they collapse, and the FILE'S OWN ORDER picks the ticker.
    That order is not arbitrary — the file is ranked, and the primary listing
    comes first: GOOGL before GOOG, and Customers Bancorp's common stock CUBI
    before its subordinated notes CUBB. Choosing alphabetically instead put
    CUBB on the row, which is a real ticker for the wrong instrument.

    Two DIFFERENT companies under one normalised name ARE ambiguity, and are
    dropped rather than guessed. On the live table that rejected nothing, which
    is the result you want from a guard like this.
    """
    grouped: dict[str, list[tuple[int, str, str]]] = {}
    for rank, (raw_rank, entry) in enumerate(sorted(
            (payload or {}).items(),
            key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 10 ** 9)):
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        ticker = str(entry.get("ticker") or "").strip().upper()
        cik = re.sub(r"\D", "", str(entry.get("cik_str") or "")).lstrip("0")
        if not (title and ticker and cik):
            continue
        key = vocab.company_key(title)
        if key:
            grouped.setdefault(key, []).append((rank, ticker, cik))

    index: dict[str, tuple[str, str]] = {}
    for key, entries in grouped.items():
        ciks = {cik for _r, _t, cik in entries}
        if len(ciks) != 1:
            continue
        best = min(entries)          # lowest rank = the file's own first entry
        index[key] = (best[1], best[2])
    return index


def sec_ticker_index(*, path: Path | None = None, allow_network: bool = True) -> dict:
    """The SEC name->(ticker, cik) map, from disk cache or one HTTP fetch.

    Cached to disk rather than into the database: it is ~800KB of somebody
    else's file, the database is committed to the repo, and it is regenerated
    weekly upstream. A stale copy is still a usable copy, so a failed refresh
    keeps whatever is on disk instead of losing the map.
    """
    global _sec_index
    if _sec_index is not None:
        return _sec_index

    cache = Path(path) if path else SEC_CACHE_PATH
    payload = None
    fresh = False
    if cache.exists():
        try:
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                cache.stat().st_mtime, tz=timezone.utc)
            payload = json.loads(cache.read_text())
            fresh = age < SEC_CACHE_MAX_AGE
        except Exception:
            payload = None

    if not fresh and allow_network:
        fetched = _get_json(SEC_TICKERS_URL)
        if isinstance(fetched, dict) and fetched:
            payload = fetched
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(fetched))
            except OSError:
                pass  # an unwritable cache is slow, not broken

    _sec_index = _build_sec_index(payload if isinstance(payload, dict) else {})
    return _sec_index


def sec_lookup(name: str, *, allow_network: bool = True) -> tuple[str | None, str | None]:
    """(ticker, cik) for a company name, or (None, None).

    This is the ONLY place a ticker is ever derived. Wikidata's P249 was tested
    and rejected: on Apple it returns 6689, the Tokyo listing, which is a
    correct fact and the wrong answer.
    """
    key = vocab.company_key(name)
    if not key:
        return None, None
    index = sec_ticker_index(allow_network=allow_network)
    hit = index.get(key)
    return hit if hit else (None, None)


# --- Wikidata: headquarters and kind of organisation -------------------------

SPARQL = """
SELECT ?item
       (SAMPLE(?linkCount) AS ?sitelinks)
       (GROUP_CONCAT(DISTINCT ?instance; separator=" ") AS ?instances)
       (GROUP_CONCAT(DISTINCT ?orgRoot; separator=" ") AS ?roots)
       (GROUP_CONCAT(DISTINCT ?placeLabel; separator="|") AS ?places)
       (SAMPLE(?hqCountryCode) AS ?hqCountry)
       (SAMPLE(?countryCode) AS ?country)
WHERE {
  VALUES ?item { %(values)s }
  OPTIONAL { ?item wikibase:sitelinks ?linkCount }
  OPTIONAL { ?item wdt:P31 ?instance }
  OPTIONAL {
    ?item wdt:P31/wdt:P279* ?orgRoot .
    FILTER(?orgRoot IN (%(roots)s))
  }
  OPTIONAL {
    ?item wdt:P159 ?hq .
    OPTIONAL {
      ?hq wdt:P131* ?place .
      ?place rdfs:label ?placeLabel .
      FILTER(lang(?placeLabel) = "en")
    }
    OPTIONAL { ?hq wdt:P17/wdt:P297 ?hqCountryCode }
  }
  OPTIONAL { ?item wdt:P17/wdt:P297 ?countryCode }
}
GROUP BY ?item
"""


def search_qids(name: str, *, limit: int = 5) -> list[str]:
    """Candidate QIDs for a name, best match first. Empty list on any failure."""
    data = _get_json(WIKIDATA_API, {
        "action": "wbsearchentities",
        "search": name[:250],
        "language": "en",
        "uselang": "en",
        "format": "json",
        "type": "item",
        "limit": limit,
    })
    if not isinstance(data, dict):
        return []
    wanted = vocab.company_key(name)
    out = []
    for hit in data.get("search") or []:
        qid = str((hit or {}).get("id") or "")
        if not _QID.match(qid):
            continue
        # What the search actually matched on, which is not always the label:
        # "NASA" matches the space agency through an alias while its label is
        # "National Aeronautics and Space Administration". Both are checked.
        matched = str(((hit or {}).get("match") or {}).get("text") or "")
        if _names_agree(wanted, matched) or _names_agree(wanted, hit.get("label")):
            out.append(qid)
    return out


def _names_agree(wanted_key: str, candidate: str | None) -> bool:
    """Is this hit's own name the name we asked about?

    `wbsearchentities` matches from the front, so a search for a company
    returns anything whose name STARTS with it: "First Foundation" brings back
    a Nigerian primary school and a Lagos hospital. A hit whose name merely
    begins with ours is a different organisation, and accepting it puts a real
    employer's rows in the wrong country.

    The comparison is exact on `vocab.company_key`, which already strips the
    legal suffix from both sides — "Apple Inc." and "Apple" are both "apple",
    "Siemens AG" and "Siemens" are both "siemens" — so nothing is gained by
    tolerating extra words, and tolerating them is what lets the city of Graham
    answer for Graham Corporation.
    """
    other = vocab.company_key(candidate or "")
    return bool(wanted_key) and other == wanted_key


def _entity_id(uri: str) -> str:
    return str(uri or "").rsplit("/", 1)[-1]


def fetch_properties(qids) -> dict:
    """ONE SPARQL call for every candidate QID. Returns {qid: {...}}.

    Batched on purpose: the alternative is a request per candidate per
    employer, which for a 1,900-employer backfill is 9,000 requests to a free
    public endpoint. This is 1 per employer.
    """
    qids = [q for q in dict.fromkeys(qids) if _QID.match(q or "")]
    if not qids:
        return {}
    query = SPARQL % {
        "values": " ".join(f"wd:{q}" for q in qids),
        "roots": ", ".join(f"wd:{q}" for q in ORG_ROOTS + DENY_ROOTS),
    }
    data = _get_json(WDQS, {"query": query, "format": "json"},
                     accept="application/sparql-results+json")
    if not isinstance(data, dict):
        return {}

    out: dict[str, dict] = {}
    for binding in ((data.get("results") or {}).get("bindings") or []):
        if not isinstance(binding, dict):
            continue
        qid = _entity_id((binding.get("item") or {}).get("value"))
        if not _QID.match(qid):
            continue
        roots = [_entity_id(u) for u in
                 ((binding.get("roots") or {}).get("value") or "").split()]
        instances = [_entity_id(u) for u in
                     ((binding.get("instances") or {}).get("value") or "").split()]
        places = [p.strip() for p in
                  ((binding.get("places") or {}).get("value") or "").split("|") if p.strip()]
        try:
            sitelinks = int(float((binding.get("sitelinks") or {}).get("value") or 0))
        except (TypeError, ValueError):
            sitelinks = 0
        out[qid] = {
            "sitelinks": sitelinks,
            "instances": instances,
            "roots": roots,
            "places": places,
            "hq_country": ((binding.get("hqCountry") or {}).get("value") or "").strip(),
            "country": ((binding.get("country") or {}).get("value") or "").strip(),
        }
    return out


def _first_vocabulary_city(places, expected_iso2: str | None = None) -> tuple[str | None, str | None]:
    """A headquarters is stored only as a city our filters actually carry.

    P159 often points at a building rather than a settlement — Alphabet's is
    "Googleplex" — so the whole P131 containment chain is offered and the first
    entry in the curated city list wins. Nothing freeform is stored: a city the
    vocabulary does not have could never match a filter anyway, and storing it
    would quietly turn a closed vocabulary into an open one.

    `expected_iso2` is the guard on that. SPARQL's GROUP_CONCAT has no defined
    order, so "first" is not "innermost", and a chain containing a county name
    that another country also uses would otherwise be free to win. A city whose
    country disagrees with the employer's own is a collision, not a match.
    """
    for place in places or ():
        hit = vocab.normalize_city(place)
        if not hit:
            continue
        city, _region, iso2 = hit
        if expected_iso2 and iso2 != expected_iso2:
            continue
        return city, iso2
    return None, None


def search_with_fallback(name: str, *, limit: int = 4) -> list[str]:
    """Candidate QIDs from the filer's name AND from its normalised form.

    Both, not one or the other, because neither wins consistently.
    `wbsearchentities` matches from the front, so a legal suffix is enough to
    lose the company entirely — "Broadcom Inc." and "Cornerstone Building
    Brands, Inc." both return zero, and "broadcom" returns Broadcom. But the
    suffix also carries signal: "COSTA LIMITED" finds Costa Coffee and "costa"
    finds Costa Rica first.

    So both pools are searched and merged. Which of them is right is not
    settled by the order they arrive in — see `_best_candidate`.
    """
    queries, found = [], {}
    for query in (name, vocab.company_key(name)):
        if query and query not in queries:
            queries.append(query)
    for query in queries:
        for qid in search_qids(query, limit=limit):
            found.setdefault(qid, None)
    return list(found)


def _best_candidate(candidates: list[str], props: dict) -> tuple[str | None, int]:
    """Of the candidates that are organisations, the most written-about one.

    Sitelinks — how many Wikipedias carry an article — stand in for notability,
    and notability is the only cheap signal that separates two organisations
    with the same name. "BURBERRY LIMITED" returns a French entity with no
    articles ahead of Burberry, which has fifty; taking the first hit put the
    British fashion house in France. Search rank breaks ties, so where
    notability says nothing this behaves exactly as before.

    Returns (qid or None, how many candidates were rejected as
    non-organisations, how many organisations were in the running).

    That third number is the honest measure of how much this function guessed.
    `_names_agree` has already thrown out every hit whose name merely BEGINS
    with the employer's, so two survivors are two organisations with the SAME
    name, and sitelinks then hand it to the more written-about one. For a
    listed company that is right. For a seed-stage startup sharing a name with
    an established firm it is systematically wrong, always in the same
    direction, and it is confident: the AI video company Synthesia resolves to
    the Czech chemical works, and Fluidstack to a French namesake. Callers that
    cannot afford that read it and decline — see `place_if_unplaced`.
    """
    rejected = organisations = 0
    best, best_rank = None, None
    for index, qid in enumerate(candidates):
        entry = props.get(qid)
        if not entry:
            continue
        if not is_organization(entry["roots"]):
            rejected += 1
            continue
        organisations += 1
        rank = (entry.get("sitelinks") or 0, -index)
        if best_rank is None or rank > best_rank:
            best, best_rank = qid, rank
    return best, rejected, organisations


def _identity_from_props(name: str, candidates: list[str], props: dict) -> Identity:
    """Read the best organisation candidate into an Identity."""
    ident = Identity(company_key=vocab.company_key(name), company=name)
    if not candidates:
        ident.detail = "no wikidata search hit"
        return ident
    if not props:
        ident.detail = "sparql returned nothing"
        return ident

    qid, rejected, organisations = _best_candidate(candidates, props)
    if qid is None:
        ident.detail = f"no organisation among {len(candidates)} candidates"
        return ident

    entry = props[qid]
    # Country first, so it can vet the city rather than the other way round.
    # P17 of the headquarters beats P17 of the company: a German
    # multinational's US subsidiary files from Germany, and the column is about
    # where the employer sits.
    country = vocab.normalize_country(entry["hq_country"]) \
        or vocab.normalize_country(entry["country"])
    city, city_iso2 = _first_vocabulary_city(entry["places"], country)

    ident.qid = qid
    ident.hq_city = city
    ident.hq_country = country or city_iso2
    ident.employer_type = employer_type_from(entry.get("instances"), entry["roots"])
    ident.resolved = bool(ident.hq_country or city or ident.employer_type)
    ident.detail = f"wikidata {qid}" + (f", skipped {rejected} non-org" if rejected else "")
    if organisations > 1:
        # Recorded, not suppressed. The existing backfill has always taken the
        # most-written-about of a set of same-named organisations and it keeps
        # doing so; this only lets a caller SEE that it was a choice.
        ident.detail += f", {organisations} {AMBIGUOUS_MARKER}"
    return ident


def wikidata_lookup(name: str, *, max_candidates: int = 4) -> Identity:
    """Resolve one name to headquarters + employer type, or an empty Identity."""
    candidates = search_with_fallback(name, limit=max_candidates)
    return _identity_from_props(name, candidates, fetch_properties(candidates))


# --- Resolution --------------------------------------------------------------

def resolve(name: str, *, conn: sqlite3.Connection | None = None,
            allow_network: bool = True, retry_negative: bool = False,
            refresh: bool = False) -> Identity:
    """The one entry point. Cached, deterministic, and never raises.

    With `allow_network=False` this is a pure cache read, which is what the
    ingestion path uses: enrichment is worth a lot and worth blocking a
    collector run for exactly nothing.
    """
    key = vocab.company_key(name)
    if not key:
        return Identity(company_key="", company=name or "", detail="no company name")

    cached = cache_get(conn, key) if conn is not None else None
    if cached is not None and not refresh and (cached.resolved or not retry_negative):
        return cached
    if not allow_network:
        return cached or Identity(company_key=key, company=name, detail="cache miss")

    try:
        ident = _finish(name, wikidata_lookup(name))
    except Exception as exc:  # rule 3: identity never takes the caller down
        return Identity(company_key=key, company=name, detail=f"wikidata error: {exc!r}")

    if conn is not None:
        cache_put(conn, ident)
    return ident


def _finish(name: str, ident: Identity) -> Identity:
    """Apply the SEC authority on top of a Wikidata reading. Shared by both
    the single and the batched path so the rules cannot drift apart."""
    try:
        ticker, cik = sec_lookup(name)
    except Exception:
        ticker, cik = None, None
    if ticker and cik:
        ident.ticker, ident.cik = ticker, cik
        # Being in company_tickers.json means having registered securities, so
        # it settles "public" against Wikidata's class graph, which calls
        # plenty of listed companies plain "business". It does NOT settle it
        # against government, education or nonprofit: the Tennessee Valley
        # Authority has SEC-registered bonds and is a federal corporation, and
        # calling it a public company would be wrong in the only way that
        # matters to a reader filtering by employer type.
        if ident.employer_type in (None, "", "private", "startup"):
            ident.employer_type = "public"
        ident.resolved = True
        ident.detail = (ident.detail + "; sec ticker").strip("; ")
    return ident


def resolve_many(pairs, conn: sqlite3.Connection, *, batch_size: int = 12,
                 retry_negative: bool = False):
    """Resolve many employers, ONE SPARQL call per batch. Yields Identity.

    `pairs` is (company_key, name). The batching is what makes a 1,900-employer
    backfill reasonable to point at a free public endpoint: unbatched it is one
    WDQS query per employer, batched it is one per twelve. The search API is
    still per-name — there is no batch form of `wbsearchentities` — and it is
    now the floor on how fast this can go.
    """
    batch: list[tuple[str, str, list[str]]] = []

    def flush():
        qids = [q for _k, _n, cs in batch for q in cs]
        props = fetch_properties(qids) if qids else {}
        for key, name, candidates in batch:
            ident = _finish(name, _identity_from_props(name, candidates, props))
            ident = replace(ident, company_key=key)
            cache_put(conn, ident)
            yield ident
        batch.clear()

    for key, name in pairs:
        cached = cache_get(conn, key)
        if cached is not None and (cached.resolved or not retry_negative):
            yield cached
            continue
        try:
            candidates = search_with_fallback(name)
        except Exception:
            candidates = []
        batch.append((key, name, candidates))
        if len(batch) >= batch_size:
            yield from flush()
    if batch:
        yield from flush()


# Which Signal fields this module is allowed to touch, and where each comes
# from. Nothing outside this tuple is ever written by enrichment.
ENRICHED_FIELDS = ("ticker", "cik", "hq_city", "hq_country", "employer_type")


# --- The one lookup ingestion is allowed to pay latency for -----------------
#
# WHY THIS EXISTS. `enrich()` is cache-only on the ingestion path, for a good
# reason: a slow or blocked lookup must never cost a record. The cost of that
# choice was invisible until it was measured, because NOTHING FILLS THE CACHE
# ON ITS OWN. `--backfill` is a command a human types, no workflow has ever run
# it, and 12,881 of 16,597 employer keys have no cache row at all. So for a
# newly seen employer the ingestion lookup is not "usually a hit, sometimes a
# miss" — it is a guaranteed miss, every time, for ever.
#
# What that costs is one specific class of row and nothing else. A row with no
# `country` and no `hq_country` is not merely thinner than its neighbours: the
# site's geographic clause is `country IN (...) OR (country IS NULL AND
# hq_country IN (...))`, so it is invisible to EVERY place filter on a product
# whose whole organising idea is place. Measured 2026-08-12: 1,666 current rows
# in that state, and of the 21 US funding events the recall set says we hold,
# 13 were invisible to a reader filtering the site to the United States.
#
# So exactly those rows, and no others, get one resolution attempt with the
# network on. It is free (Wikidata + SEC, no model, ever), it is cached for
# that employer for ever, it fails open like everything else here, and it is
# bounded per process so a bad day cannot turn a collect run into a crawl.
# Every other caller stays cache-only and pays nothing.
#
# A note for whoever reads this next: the fix is NOT to let a model guess a
# headquarters from a company name. That is the plausible-but-wrong fact this
# product cannot carry, it is why the ticker authority here is SEC and not an
# LLM, and a confidently wrong country on a public page is worse than an
# honest blank.
PLACEMENT_LOOKUP_BUDGET = int(os.environ.get("TIT_PLACEMENT_LOOKUPS") or 150)

#: Written into `Identity.detail` when more than one organisation carried the
#: employer's exact name, and read back off the cached row by `is_ambiguous`.
#: A marker in `detail` rather than a new column because `detail` is already
#: the place this module records how it got there ("skipped 1 non-org"), it is
#: already cached, and a new column would need a migration on a table that
#: exists on every checkout.
AMBIGUOUS_MARKER = "same-named organisations"

#: Set `TIT_IDENTITY_LOOKUP=off` to keep ingestion strictly cache-only — the
#: behaviour before 2026-08-12. The offline dry run sets it, and so does the
#: test suite, because a unit test that reaches Wikidata is not a unit test.
_LOOKUP_DISABLED = ("off", "0", "no", "false")

_placement_lookups = 0


def placement_lookups_used() -> int:
    """How many network placement lookups this process has spent."""
    return _placement_lookups


def reset_placement_budget() -> None:
    """Forget the per-process count. For tests, and for a long-lived caller."""
    global _placement_lookups
    _placement_lookups = 0


def placement_lookup_enabled() -> bool:
    if (os.environ.get("TIT_IDENTITY_LOOKUP") or "on").strip().lower() in _LOOKUP_DISABLED:
        return False
    return _placement_lookups < PLACEMENT_LOOKUP_BUDGET


def is_ambiguous(ident) -> bool:
    """Did more than one organisation of that exact name exist?

    Then the resolution is a notability coin flip and this path declines it.
    Precision over recall, the same rule `cheap_extract` states: a blank
    country is honestly blank, and a wrong one is indistinguishable on the page
    from a right one. Measured on the committed corpus, the wrong ones are not
    rare or random — BKV Corporation resolves to a Hungarian political party,
    Capital Bancorp to a Nigerian bank, AWARE, INC. to a French namesake — and
    they are wrong in the direction of whichever organisation the encyclopedia
    happens to care about more.
    """
    return AMBIGUOUS_MARKER in (getattr(ident, "detail", "") or "")


def is_placeable(ident) -> bool:
    """May this resolution put a country on a row a reader will see?

    Two bars, and the second one was bought with a near miss.

    1. Not `is_ambiguous`: one organisation of that name, not two.
    2. **A curated headquarters CITY came with the country.** `hq_country` is
       read from P17 of the entity's headquarters and FALLS BACK to P17 of the
       entity itself, and that fallback is where the errors live. It is a much
       weaker fact — "this thing is associated with country X" rather than
       "this employer sits in this city, which is in country X" — and on the
       committed corpus the two halves behave completely differently:

           with a curated hq_city   82 employers, and the sample reads
                                    Accel/Palo Alto, Databricks/San Francisco,
                                    Cyera/Tel Aviv, DeepSeek/Hangzhou
           country only            108 employers, and the sample reads
                                    Premier Lacrosse League/CA (a US league),
                                    Synthesia/CZ (the chemical works, not the
                                    AI company), AirTrunk/AU, African Bank/ZA

       The Premier Lacrosse League row is not hypothetical: it is one of the 13
       US funding events a reader could not find, and the cityless fallback
       would have moved it from invisible to Canadian. That is strictly worse.

    The cost of the bar is recall, and it is paid knowingly: 190 employers
    become 82. A blank country is honestly blank.
    """
    return bool(getattr(ident, "hq_country", None)
                and getattr(ident, "hq_city", None)
                and not is_ambiguous(ident))


def place_if_unplaced(signal, conn: sqlite3.Connection | None = None) -> list[str]:
    """Resolve ONE employer over the network, but only to rescue a placeless row.

    Returns the fields it filled, and `[]` for everything else — a row that
    already carries a country in either column, an ambiguous resolution, a
    spent budget, a disabled lookup, a missing connection, and every failure
    mode there is.
    """
    global _placement_lookups
    if conn is None or signal is None:
        return []
    if getattr(signal, "country", None) or getattr(signal, "hq_country", None):
        return []
    company = getattr(signal, "company", "")
    if not company:
        return []
    if not placement_lookup_enabled():
        return []
    _placement_lookups += 1
    try:
        ident = resolve(company, conn=conn, allow_network=True)
    except Exception:
        return []                                    # rule 3, as everywhere else
    if not is_placeable(ident):
        return []
    filled = []
    for field in ENRICHED_FIELDS:
        if getattr(signal, field, None):
            continue                                 # rule 1: sourced beats derived
        value = getattr(ident, field, None)
        if value:
            setattr(signal, field, value)
            filled.append(field)
    return filled


def enrich(signal, conn: sqlite3.Connection | None = None, *,
           allow_network: bool = False) -> list[str]:
    """Fill BLANK identity fields on a Signal. Returns the names it filled.

    RULE 1, ENFORCED HERE: a value that is already set is never replaced. A CIK
    that came out of the EFTS hit for the filing, or a ticker the article
    printed in "(NASDAQ: AAPL)", is a fact about a document we fetched.
    Everything this module derives is an inference from a name string. The
    inference fills a blank or it does nothing; it never wins an argument.

    Defaults to `allow_network=False` so the collector path pays no latency and
    takes no new failure mode: it reads the cache, which the backfill fills.

    With no connection this is a no-op, deliberately. An earlier draft opened
    the live database itself when handed none, which made `build_signal` — a
    pure function of two dicts — quietly depend on what happened to be cached
    on that machine, and four existing unit tests started passing or failing
    according to the contents of `data/talent_intel.db`. The connection is a
    parameter now, and where the data comes from is visible at the call site.
    """
    try:
        if signal is None or not getattr(signal, "company", ""):
            return []
        ident = resolve(getattr(signal, "company"), conn=conn,
                        allow_network=allow_network)
        filled = []
        for field in ENRICHED_FIELDS:
            if getattr(signal, field, None):
                continue  # <- rule 1. Sourced beats derived, every time.
            value = getattr(ident, field, None)
            if value:
                setattr(signal, field, value)
                filled.append(field)
        return filled
    except Exception:
        # Rule 3. An enrichment failure is a blank column, never a lost record.
        return []


# --- Backfill ----------------------------------------------------------------

def _employers_needing_identity(conn: sqlite3.Connection, limit: int | None,
                                *, retry_negative: bool = False) -> list[tuple[str, str]]:
    """(company_key, company) for employers this module has not yet looked at.

    Keyed on the CACHE, not on which columns are NULL, and that distinction is
    the whole difference between chunking working and not working. Most
    employers keep a NULL hq_city forever — the curated city list has 45
    entries and Cupertino is not one of them — so "any identity column is NULL"
    matches the same employers on every run, and `--limit 240` walks the same
    240 names each time while the rest are never reached. Asking who has no
    cache row is a question whose answer shrinks.

    Employers with no geography at all come FIRST, and that ordering is the
    point of the query rather than a nicety. The site's country filter unions
    job location with employer HQ (`country_basis=any`), so a row holding
    NEITHER is not merely less rich than its neighbours — it is invisible to
    every geographic filter on the site, and geography is how this product
    segments. A missing `hq_city` costs a row nothing it was ever going to
    have; a missing country costs it every reader who arrived by place.
    Measured 2026-07-28: 152 current rows carried neither, 116 of them funding
    filings, and each of them sat at the very tail of a `n DESC` ordering
    because an unplaced employer is usually a small one with a single row.
    Row count still orders WITHIN each group, so the original argument — an
    interrupted run has already fixed the employers that appear most — holds
    exactly as before, one tier down.
    """
    unresolved_clause = "" if retry_negative else " AND i.company_key IS NULL"
    sql = f"""
        SELECT s.company_key, MIN(s.company) AS company, COUNT(*) AS n,
               SUM(CASE WHEN (s.country IS NULL OR s.country = '')
                         AND (s.hq_country IS NULL OR s.hq_country = '')
                        THEN 1 ELSE 0 END) AS unplaced
          FROM signals s
     LEFT JOIN employer_identity i ON i.company_key = s.company_key
         WHERE s.is_current = 1
           AND s.company_key IS NOT NULL AND s.company_key != ''
           AND (s.ticker IS NULL OR s.cik IS NULL OR s.hq_country IS NULL
                OR s.hq_city IS NULL OR s.employer_type IS NULL)
               {unresolved_clause}
      GROUP BY s.company_key
      ORDER BY (unplaced > 0) DESC, unplaced DESC, n DESC, s.company_key
    """
    rows = conn.execute(sql).fetchall()
    out = [(r[0], r[1] or r[0]) for r in rows]
    return out[:limit] if limit else out


def apply_identity(conn: sqlite3.Connection, ident: Identity) -> dict:
    """UPDATE the blank identity columns of every row for one employer.

    An in-place UPDATE, not a `store.revise()` revision, and deliberately: this
    fills a NULL derived column, it does not correct a claim. Nothing a source
    said changes, no headline, figure, date or confidence moves, and there is
    no past state to reconstruct because the past state is NULL. It is the same
    shape as `schema.backfill_funding_usd`, which re-derives a column in place
    for exactly the same reason. Two thousand revisions saying "we looked up a
    postcode" would make the revision history less readable, not more.

    Every UPDATE carries `IS NULL OR = ''`: rule 1 again, at the SQL level.
    """
    counts = {}
    for field in ENRICHED_FIELDS:
        value = getattr(ident, field, None)
        if not value:
            continue
        cur = conn.execute(
            f"""UPDATE signals SET {field} = ?
                 WHERE company_key = ?
                   AND ({field} IS NULL OR {field} = '')""",
            (value, ident.company_key),
        )
        if cur.rowcount:
            counts[field] = cur.rowcount
    return counts


def apply_cache(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    """Push every cached identity onto the rows, resolving nothing.

    The cheap half of the backfill, separated out because the two halves fail
    differently: resolution is a long network walk that can be interrupted, and
    applying is a handful of indexed UPDATEs that cannot. Splitting them also
    means the resolving half can run against a COPY of the database — which is
    what you want when something else on the machine is touching the live file.
    """
    ensure_cache(conn)
    rows = conn.execute(
        f"SELECT company_key, {', '.join(ENRICHED_FIELDS)} "
        "FROM employer_identity WHERE resolved = 1").fetchall()

    stats = {"employers": len(rows), "resolved": len(rows), "unresolved": 0,
             "rows": {f: 0 for f in ENRICHED_FIELDS}, "samples": []}
    for row in rows:
        ident = Identity(company_key=row[0],
                         **dict(zip(ENRICHED_FIELDS, row[1:])))
        if ident.is_empty or dry_run:
            continue
        for field, n in apply_identity(conn, ident).items():
            stats["rows"][field] += n
    if not dry_run:
        conn.commit()
    return stats


#: The two columns a PLACEMENT pass may write, and no others. `ticker`, `cik`
#: and `employer_type` are perfectly good values and they are not what a
#: placeless row is missing; keeping them out means the pass that runs against
#: history has one job and one failure mode.
PLACEMENT_FIELDS = ("hq_city", "hq_country")


def _employers_with_placeless_rows(conn: sqlite3.Connection, limit: int | None,
                                   *, retry_negative: bool = False
                                   ) -> list[tuple[str, str]]:
    """(company_key, company) for employers holding a row with NO place at all.

    Not "missing some identity column" — `_employers_needing_identity` asks
    that, and most employers keep a NULL `hq_city` for ever because the curated
    city list is 422 aliases long. This asks the one question a reader can
    feel: which employers have a current row that no country filter on the site
    can return.

    ALREADY-ASKED EMPLOYERS ARE EXCLUDED, and that is what makes `--limit`
    work. The first run of this pass walked the 400 employers holding the most
    placeless rows, found 393 of them already cached from the general backfill,
    finished in thirty seconds and placed two. Every subsequent run with the
    same limit would have walked the same 400. It is the lesson
    `_employers_needing_identity` already wrote down one function up — ask who
    has NO cache row, because that is a question whose answer shrinks — and
    this function was written without it.

    Ordered by how many placeless rows each employer holds, so an interrupted
    run has already fixed the ones that appear most.
    """
    unresolved_clause = "" if retry_negative else " AND i.company_key IS NULL"
    rows = conn.execute(f"""
        SELECT s.company_key, MIN(s.company) AS company, COUNT(*) AS n
          FROM signals s
     LEFT JOIN employer_identity i ON i.company_key = s.company_key
         WHERE s.is_current = 1
           AND s.company_key IS NOT NULL AND s.company_key != ''
           AND (s.country IS NULL OR s.country = '')
           AND (s.hq_country IS NULL OR s.hq_country = '')
               {unresolved_clause}
      GROUP BY s.company_key
      ORDER BY n DESC, s.company_key
    """).fetchall()
    out = [(r[0], r[1] or r[0]) for r in rows]
    return out[:limit] if limit else out


def place_backfill(conn: sqlite3.Connection, *, limit: int | None = None,
                   dry_run: bool = False, retry_negative: bool = False,
                   verbose: bool = True) -> dict:
    """Fill hq_city/hq_country on rows that carry no place in either column.

    The backward half of `place_if_unplaced`, under the same bar and for the
    same reason: free, no model, and only what `is_placeable` admits. History
    is where a wrong country does the most damage, because it is already on the
    public page, so this pass declines more readily than the general backfill
    rather than less.

    Reports the three outcomes separately, because they are three different
    findings. `unresolved` means Wikidata has never heard of the employer.
    `declined` means it answered and the answer did not clear the bar, which is
    a place we could reach with a better source rather than one nobody knows.
    """
    ensure_cache(conn)
    todo = _employers_with_placeless_rows(conn, limit,
                                          retry_negative=retry_negative)
    stats = {"employers": len(todo), "placed": 0, "unresolved": 0,
             "declined": 0, "rows": {f: 0 for f in PLACEMENT_FIELDS},
             "samples": [], "declined_samples": []}

    for index, ident in enumerate(resolve_many(todo, conn,
                                               retry_negative=retry_negative), 1):
        if not ident.hq_country:
            stats["unresolved"] += 1
        elif not is_placeable(ident):
            stats["declined"] += 1
            if len(stats["declined_samples"]) < 10:
                stats["declined_samples"].append(ident)
        else:
            stats["placed"] += 1
            if not dry_run:
                for field in PLACEMENT_FIELDS:
                    value = getattr(ident, field, None)
                    if not value:
                        continue
                    cur = conn.execute(
                        f"UPDATE signals SET {field} = ? WHERE company_key = ? "
                        f"AND ({field} IS NULL OR {field} = '')",
                        (value, ident.company_key))
                    stats["rows"][field] += cur.rowcount
            if len(stats["samples"]) < 10:
                stats["samples"].append(ident)
        if not dry_run:
            conn.commit()
        if verbose and (index % 25 == 0 or index == len(todo)):
            print(f"  {index}/{len(todo)} employers "
                  f"({stats['placed']} placed, {stats['unresolved']} unknown, "
                  f"{stats['declined']} declined)", flush=True)
        if _consecutive_failures >= _FAILURE_BUDGET:
            print("  network is failing; stopping early and keeping what resolved",
                  flush=True)
            break
    return stats


def backfill(conn: sqlite3.Connection, *, limit: int | None = None,
             allow_network: bool = True, retry_negative: bool = False,
             dry_run: bool = False, verbose: bool = True) -> dict:
    """Resolve every employer missing identity, and fill their blanks.

    Idempotent (a resolved employer is cached and its columns are no longer
    NULL) and safe to interrupt (each employer commits on its own).
    """
    ensure_cache(conn)
    todo = _employers_needing_identity(conn, limit, retry_negative=retry_negative)
    stats = {"employers": len(todo), "resolved": 0, "unresolved": 0,
             "rows": {f: 0 for f in ENRICHED_FIELDS}, "samples": []}

    stream = (resolve_many(todo, conn, retry_negative=retry_negative)
              if allow_network else
              (replace(resolve(name, conn=conn, allow_network=False),
                       company_key=key) for key, name in todo))

    for index, ident in enumerate(stream, 1):
        if ident.is_empty:
            stats["unresolved"] += 1
        else:
            stats["resolved"] += 1
            if not dry_run:
                for field, n in apply_identity(conn, ident).items():
                    stats["rows"][field] += n
            if len(stats["samples"]) < 10:
                stats["samples"].append(ident)

        if not dry_run:
            conn.commit()
        if verbose and (index % 25 == 0 or index == len(todo)):
            print(f"  {index}/{len(todo)} employers "
                  f"({stats['resolved']} resolved, {stats['unresolved']} not)",
                  flush=True)
        if _consecutive_failures >= _FAILURE_BUDGET:
            print("  network is failing; stopping early and keeping what resolved",
                  flush=True)
            break

    return stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.identity",
        description="Fill ticker / cik / hq / employer_type from SEC + Wikidata. No LLM.")
    parser.add_argument("--backfill", action="store_true",
                        help="resolve employers in the database and fill their blanks")
    parser.add_argument("--apply-cache", action="store_true",
                        help="fill blanks from already-cached resolutions; no network")
    parser.add_argument("--place-unplaced", action="store_true",
                        help="fill hq_city/hq_country on rows carrying NO place "
                             "at all; declines every ambiguous name")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after N employers (they are ordered by row count)")
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve and report, write nothing to signals")
    parser.add_argument("--retry-negative", action="store_true",
                        help="re-ask about names cached as unresolvable")
    parser.add_argument("--db", default=None, help="database path (default: the live one)")
    parser.add_argument("--name", default=None,
                        help="resolve one name and print it, for spot checks")
    args = parser.parse_args(argv)

    from . import schema  # local: keeps `import identity` free of DB side effects

    if args.name:
        conn = schema.connect(args.db)
        # A spot check that answers from the cache checks the cache, not the
        # resolver, so --name always goes back to the sources.
        ident = resolve(args.name, conn=conn, refresh=True)
        conn.commit()
        print(json.dumps({
            "company": ident.company, "qid": ident.qid, "ticker": ident.ticker,
            "cik": ident.cik, "hq_city": ident.hq_city, "hq_country": ident.hq_country,
            "employer_type": ident.employer_type, "detail": ident.detail,
        }, indent=2))
        return 0

    if not (args.backfill or args.apply_cache or args.place_unplaced):
        parser.error("nothing to do: pass --backfill, --apply-cache, "
                     "--place-unplaced or --name")

    conn = schema.connect(args.db)

    if args.place_unplaced:
        print("place the unplaced" + (" (dry run)" if args.dry_run else ""))
        stats = place_backfill(conn, limit=args.limit, dry_run=args.dry_run,
                               retry_negative=args.retry_negative)
        conn.commit()
        print(f"\nemployers with a placeless row : {stats['employers']}")
        print(f"placed                         : {stats['placed']}")
        print(f"wikidata does not know them    : {stats['unresolved']}")
        print(f"declined, the answer is too weak: {stats['declined']}")
        for field in PLACEMENT_FIELDS:
            print(f"rows gained {field:<16}: {stats['rows'][field]}")
        for label, group in (("placed", stats["samples"]),
                             ("declined", stats["declined_samples"])):
            if group:
                print(f"\n{label}:")
                for ident in group:
                    print(f"  {ident.company[:38]:<38} {ident.hq_city or '-':<14} "
                          f"{ident.hq_country or '-':<3}  {ident.detail}")
        return 0

    label = "apply cached identities" if args.apply_cache else "identity backfill"
    print(f"{label}{' (dry run)' if args.dry_run else ''}")
    if args.apply_cache:
        stats = apply_cache(conn, dry_run=args.dry_run)
    else:
        stats = backfill(conn, limit=args.limit, dry_run=args.dry_run,
                         retry_negative=args.retry_negative)
    conn.commit()

    print(f"\nemployers examined : {stats['employers']}")
    print(f"resolved           : {stats['resolved']}")
    print(f"unresolved         : {stats['unresolved']}")
    for field in ENRICHED_FIELDS:
        print(f"rows gained {field:<14}: {stats['rows'][field]}")
    if stats["samples"]:
        print("\nsample:")
        for ident in stats["samples"]:
            print(f"  {ident.company[:38]:<38} {ident.ticker or '-':<7} "
                  f"cik={ident.cik or '-':<9} {ident.hq_city or '-':<14} "
                  f"{ident.hq_country or '-':<3} {ident.employer_type or '-'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
