"""The source registry (spec 13.1) and the search vocabulary (spec 14).

Two honesty mechanisms live here:

1. **Status tiers.** A country is not covered because it appears in this file.
   It is covered when it has a working connector, a health check and a passing
   test. `candidate_official_sources` exists so the roadmap can be published
   without lying about the present.
2. **Vocabulary as data.** Search terms live here, never inside a prompt, so
   they are reviewable, testable and diffable.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

# --- Status tiers ----------------------------------------------------------

DISCOVERY_ONLY = "discovery_only"            # news discovery only
STRUCTURED_OFFICIAL = "structured_official"  # a real official connector runs
RECONCILED = "reconciled"                    # official + benchmark reconciliation

TIER_PUBLIC_CLAIM = {
    DISCOVERY_ONLY: "we monitor news here",
    STRUCTURED_OFFICIAL: "structured coverage",
    RECONCILED: "verified coverage",
}


@dataclass(frozen=True)
class Market:
    iso2: str
    name: str
    status: str
    live_sources: tuple = ()
    candidate_official_sources: tuple = ()
    terms: tuple = ()          # country-specific vocabulary, local language
    benchmark: str = ""


# --- Layer 1: base vocabulary (always on, spec 14) -------------------------
#
# Hiring-side only. Layoffs, WARN and redundancies are read from the sibling
# tracker's API and never collected here (spec 17).

# Ordered highest-precision first: the daily broad query uses the leading
# terms, so anything ambiguous must not sit near the top.
#
# The first live run is why there is no bare "expansion" here. That single word
# returned MLB expansion, World of Warcraft expansion, Medicaid expansion,
# cattle herd expansion and war escalation — 25 candidates, zero of them about
# employment. Every term below names people or a workplace explicitly.
BASE_VOCABULARY = (
    "to create jobs", "will create jobs", "new jobs at", "jobs announcement",
    "hiring spree", "recruitment drive", "ramp up hiring", "headcount growth",
    "expanding its workforce", "workforce expansion", "adding roles",
    "opens new office", "opens new hub", "new engineering hub",
    "opening a new facility", "opens campus", "investment creating jobs",
    "appointed chief executive", "names chief executive", "appoints CEO",
    "steps down as CEO", "new chief people officer", "appoints CFO",
    "executive appointment", "joins as chief",
    "pay rise for staff", "salary increase for employees", "retention bonus",
    "compensation package", "pay transparency", "raises minimum salary",
    "return to office policy", "remote work policy", "hybrid working policy",
    "four-day week", "relocates headquarters",
)

# --- Layer 3: euphemisms, run STANDALONE ----------------------------------
#
# A euphemism AND-ed with the base vocabulary can only match articles that also
# use the obvious word, which is the opposite of the intent. This bug shipped
# on the sibling and made 16 terms dead on arrival (spec 5 / 14).

STANDALONE_QUERIES = (
    "capability build",
    "centre of excellence opening",
    "shared services transition",
    "operating model change",
    "strategic realignment hiring",
    "organisational design change",
    "talent hub",
    "global capability centre",
)

# --- Google News queries ---------------------------------------------------
#
# Now that publisher URLs resolve, these have to earn their keep. The broad
# "to create jobs" sweep returned political and economic-development stories —
# Ohio approving projects, a bill passing, Uzbekistan digitalising — none of
# which name an employer.
#
# Two fixes: phrases that only a company announcement uses, and `when:` so we
# see today's news rather than a stale week. Operators go inside the query
# string, which is what makes Google News queryable rather than a firehose.

# Google News is per-locale AND per-language, and the second half is the part
# that matters. Measured on 2026-07-27, the same English phrases returned:
#
#   US:en  23 items    DE:de  2 items    BR:pt  0 items
#
# and the German phrasing returned 20 for the same German edition. Rotating the
# hl/gl parameters while keeping English phrases would have looked like global
# coverage and delivered almost nothing outside the English-speaking world.
#
# So a locale is only in the rotation once its language has a phrase set below.
# Adding a language is what adds countries; the locale list is downstream of it.
GOOGLE_NEWS_ANCHOR = ("en", "US")

# Leadership / hiring / funding, in each language's own newsroom phrasing.
# These are deliberately the same three intents in every language rather than a
# translation of all eleven English queries: a phrase that does not match is a
# silent zero, and three well-chosen ones beat eleven guesses.
GOOGLE_NEWS_VOCAB = {
    "en": (
        '("appoints chief executive" OR "names new CEO" OR "steps down as CEO") when:3d',
        '("plans to hire" OR "hiring spree" OR "to create jobs" OR "opens new office") when:3d',
        '("raises" OR "raised") ("Series A" OR "Series B" OR "seed funding") when:3d',
    ),
    "de": (
        '("neuer Vorstandsvorsitzender" OR "wird CEO" OR "verlässt das Unternehmen" OR "tritt zurück") when:3d',
        '("will einstellen" OR "schafft Arbeitsplätze" OR "neue Stellen" OR "eröffnet Standort") when:3d',
        '("Finanzierungsrunde" OR "sammelt ein" OR "Millionen eingesammelt") when:3d',
    ),
    "fr": (
        '("nommé directeur général" OR "devient PDG" OR "quitte ses fonctions" OR "nouveau PDG") when:3d',
        '("va recruter" OR "créer des emplois" OR "ouvre un site" OR "plan de recrutement") when:3d',
        '("levée de fonds" OR "lève" "millions") when:3d',
    ),
    "es": (
        '("nuevo consejero delegado" OR "nombrado director general" OR "deja el cargo" OR "nuevo CEO") when:3d',
        '("creará empleo" OR "contratará" OR "nuevos puestos" OR "abre oficina") when:3d',
        '("ronda de financiación" OR "capta" "millones") when:3d',
    ),
    "pt": (
        '("novo presidente-executivo" OR "assume como CEO" OR "deixa o cargo" OR "novo CEO") when:3d',
        '("vai contratar" OR "cria empregos" OR "novas vagas" OR "abre escritório") when:3d',
        '("rodada de investimento" OR "capta" "milhões") when:3d',
    ),
    "it": (
        '("nuovo amministratore delegato" OR "nominato CEO" OR "lascia la guida") when:3d',
        '("assumerà" OR "nuove assunzioni" OR "crea posti di lavoro" OR "apre una sede") when:3d',
        '("round di finanziamento" OR "raccoglie" "milioni") when:3d',
    ),
    "nl": (
        '("nieuwe topman" OR "wordt CEO" OR "stapt op" OR "benoemd tot bestuursvoorzitter") when:3d',
        '("gaat aannemen" OR "nieuwe banen" OR "opent vestiging" OR "breidt uit") when:3d',
        '("financieringsronde" OR "haalt" "miljoen op") when:3d',
    ),
}

GOOGLE_NEWS_LOCALES = (
    ("en", "GB"), ("en", "CA"), ("en", "AU"), ("en", "IN"), ("en", "IE"),
    ("en", "SG"), ("en", "NZ"), ("en", "ZA"), ("en", "PH"), ("en", "NG"),
    ("de", "DE"), ("de", "AT"), ("de", "CH"),
    ("fr", "FR"), ("fr", "BE"),
    ("es", "ES"), ("es", "MX"), ("es", "AR"), ("es", "CL"), ("es", "CO"),
    ("pt", "BR"), ("pt", "PT"),
    ("it", "IT"),
    ("nl", "NL"),
)


def google_news_queries(lang: str) -> list[str]:
    """Phrases for one edition. English is the fallback and the anchor."""
    return list(GOOGLE_NEWS_VOCAB.get(lang, GOOGLE_NEWS_VOCAB["en"]))


GOOGLE_NEWS_QUERIES = (
    '("appoints chief executive" OR "names chief executive" OR "appointed CEO") when:3d',
    '("appoints chief financial officer" OR "appoints CFO" OR "new chief people officer") when:3d',
    '("steps down as chief executive" OR "steps down as CEO" OR "to step down as CEO") when:3d',
    '("hiring spree" OR "recruitment drive" OR "to ramp up hiring") when:3d',
    '("plans to hire" OR "will hire" OR "to add jobs") ("engineers" OR "staff" OR "roles") when:3d',
    '("opens new office" OR "opens its new" OR "new engineering hub") when:3d',
    '("global capability centre" OR "global capability center") when:3d',
    '("raises" OR "raised") ("Series A" OR "Series B" OR "Series C" OR "seed funding") when:3d',
    '("acquires" OR "to acquire") ("startup" OR "company") when:2d',
    '("pay rise" OR "raises minimum salary" OR "retention bonus") when:3d',
    '("return to office" OR "remote work policy") ("employees" OR "staff") when:3d',
)


# --- GDELT queries ---------------------------------------------------------
#
# GDELT is not Google News and its query language is not the same: a space
# means AND, OR requires parentheses, and sourcelang: filters by language.
# Reusing the Google News query strings produced 219 candidates of which 216
# were noise, much of it non-English coverage of unrelated topics.
#
# These are written for how GDELT searches: narrow phrases that only appear in
# corporate hiring coverage, English-only.

GDELT_QUERIES = (
    '("hiring spree" OR "to create jobs" OR "will create jobs") sourcelang:english',
    '("global capability centre" OR "global capability center") sourcelang:english',
    '("opens new office" OR "opens new hub" OR "new engineering hub") sourcelang:english',
    '("appoints" OR "names") ("chief executive" OR "chief people officer" OR "chief financial officer") sourcelang:english',
    '("steps down as" OR "to step down") ("chief executive" OR "ceo") sourcelang:english',
    '("expands its workforce" OR "recruitment drive" OR "ramp up hiring") sourcelang:english',
    '("return to office" OR "remote work policy" OR "hybrid working") sourcelang:english',
    '("pay rise" OR "raises minimum salary" OR "retention bonus") sourcelang:english',
)


# --- The source catalogue --------------------------------------------------
#
# One row per source, with an honest status. This renders straight onto the
# public sources page, so the page can never drift from reality.
#
#   live      = a connector runs, reports health, and has a passing test
#   candidate = researched and real, but nothing reads it yet
#
# A source is NEVER promoted to live because it looks easy. That distinction is
# the whole point: a catalogue that implies coverage we do not have is a lie
# told in a table.

@dataclass(frozen=True)
class Source:
    name: str
    url: str
    status: str            # live | candidate
    category: str
    signals: tuple
    coverage: str          # Global | National | Regional | Local
    country: str = ""
    rss: str = ""
    free: bool = True
    notes: str = ""


SOURCES = (
    # --- live: something actually reads these today ------------------------
    Source("SEC EDGAR 8-K (Item 5.02)", "https://www.sec.gov/edgar.shtml", "live",
           "Regulatory filings", ("Executive hire", "Executive departure", "Board appointment"),
           "National", "US",
           notes="Legally required within four business days. Primary source, so "
                 "records earn verified confidence."),
    Source("SEC EDGAR Form D", "https://www.sec.gov/edgar.shtml", "live",
           "Regulatory filings", ("Funding", "Private placement"),
           "National", "US",
           notes="Structured XML: issuer, industry, city, state and amount sold. "
                 "The money figure is read off the filing, never inferred."),
    Source("GDELT DOC 2.0", "https://www.gdeltproject.org/", "live",
           "News aggregation", ("Hiring", "Office opening", "Leadership change"),
           "Global",
           notes="Worldwide, machine-translated from 65 languages. Returns real "
                 "article URLs. Throttles erratically, so lost queries are logged "
                 "as coverage gaps."),
    Source("Google News RSS", "https://news.google.com/", "live",
           "News aggregation", ("Hiring", "Funding", "Leadership change", "Layoffs"),
           "25 country editions, 7 languages",
           notes="Keyless and unthrottled. Read in each edition's own language, "
                 "because English phrases in a non-English edition return almost "
                 "nothing. Its links are encoded redirects, but Google's own "
                 "resolution endpoint returns the publisher URL, so records cite "
                 "the article rather than an outlet homepage."),

    # --- candidate: researched, real, not yet connected --------------------
    Source("SEC EDGAR 8-K (Items 1.01 / 2.01)", "https://www.sec.gov/edgar.shtml",
           "candidate", "Regulatory filings", ("M&A", "Acquisition"), "National", "US"),
    Source("USAspending.gov", "https://www.usaspending.gov/", "candidate",
           "Government open data", ("Federal contract", "Government contract"),
           "National", "US", notes="Free API, no key. Contract awards precede hiring."),
    Source("UK Companies House", "https://www.gov.uk/government/organisations/companies-house",
           "candidate", "Regulatory filings", ("Board appointment", "Incorporation"),
           "National", "GB"),
    Source("IDA Ireland", "https://www.idaireland.com/", "candidate",
           "Investment promotion agency", ("Hiring", "Office opening", "Job creation"),
           "National", "IE",
           notes="Announces 'X company, Y jobs, Z city' as official releases. "
                 "Pre-structured headcount and location, free."),
    Source("Invest Northern Ireland", "https://www.investni.com/", "candidate",
           "Investment promotion agency", ("Hiring", "Job creation"), "Regional", "GB"),
    Source("Business France", "https://www.businessfrance.fr/", "candidate",
           "Investment promotion agency", ("Hiring", "Office opening"), "National", "FR"),
    Source("Germany Trade & Invest", "https://www.gtai.de/", "candidate",
           "Investment promotion agency", ("Hiring", "Facility expansion"), "National", "DE"),
    Source("US Bureau of Labor Statistics (JOLTS)", "https://www.bls.gov/jlt/", "candidate",
           "Labour market data", ("Labor market trends",), "National", "US",
           rss="https://www.bls.gov/feed/", notes="Context only, never mixed into counts."),
    Source("PR Newswire", "https://www.prnewswire.com/", "candidate",
           "Press release wire", ("Funding", "Executive hire", "Expansion"), "Global",
           rss="https://www.prnewswire.com/rss/"),
    Source("Business Wire", "https://www.businesswire.com/", "candidate",
           "Press release wire", ("Executive hire", "M&A", "Expansion"), "Global"),
    Source("TechCrunch", "https://techcrunch.com/", "candidate",
           "Technology press", ("Funding", "M&A", "Layoffs"), "Global",
           rss="https://techcrunch.com/feed/"),
    Source("Sifted", "https://sifted.eu/", "candidate",
           "Technology press", ("Funding", "Hiring"), "Regional", "GB",
           rss="https://sifted.eu/feed/", notes="Best single source for European startups."),
    Source("GeekWire", "https://www.geekwire.com/", "candidate",
           "Regional business press", ("Hiring", "Office opening", "Funding"),
           "Regional", "US", rss="https://www.geekwire.com/feed/",
           notes="Pacific Northwest: the Amazon and Microsoft ecosystem."),
    Source("BetaKit", "https://betakit.com/", "candidate",
           "Technology press", ("Funding", "Hiring"), "National", "CA",
           rss="https://betakit.com/feed/"),
    Source("EU-Startups", "https://www.eu-startups.com/", "candidate",
           "Technology press", ("Funding",), "Regional", "ES",
           rss="https://www.eu-startups.com/feed/"),
    Source("Tech in Asia", "https://www.techinasia.com/", "candidate",
           "Technology press", ("Funding", "Expansion"), "Regional", "SG",
           rss="https://www.techinasia.com/feed", free=False),
    Source("Crunchbase News", "https://news.crunchbase.com/", "candidate",
           "Venture capital press", ("Funding", "M&A"), "Global",
           rss="https://news.crunchbase.com/feed/"),
    Source("FierceBiotech", "https://www.fiercebiotech.com/", "candidate",
           "Trade publication", ("Funding", "Hiring", "FDA approval"), "Global",
           rss="https://www.fiercebiotech.com/rss/xml",
           notes="Biotech hiring follows FDA milestones."),
    Source("AI Layoff Tracker (sibling)", "https://asktherecruiter.com/blog/ai-layoff-tracker/",
           "candidate", "Sibling product", ("Layoffs", "WARN notices"), "Global",
           notes="READ ONLY, never re-collected. One source of truth per fact."),

    # --- owner-supplied catalogue, 2026-07-27 ------------------------------
    # All candidates. Listing a source is not a claim that we read it.
    Source("Reuters", "https://www.reuters.com", "candidate",
           "Global business press", ("M&A", "Layoffs", "Executive hire"), "Global"),
    Source("Bloomberg", "https://www.bloomberg.com", "candidate",
           "Financial press", ("M&A", "IPO", "Layoffs"), "Global", free=False),
    Source("Associated Press", "https://apnews.com", "candidate",
           "Global business press", ("M&A", "Layoffs"), "Global"),
    Source("CNBC", "https://www.cnbc.com", "candidate",
           "Financial press", ("Earnings", "M&A", "Executive hire"), "Global"),
    Source("Financial Times", "https://www.ft.com", "candidate",
           "Financial press", ("M&A", "Hiring", "Layoffs"), "Global", free=False),
    Source("The Wall Street Journal", "https://www.wsj.com", "candidate",
           "Financial press", ("M&A", "Executive hire"), "Global", free=False),
    Source("Fortune", "https://fortune.com", "candidate",
           "Business press", ("Executive hire", "Funding"), "Global"),
    Source("Forbes", "https://www.forbes.com", "candidate",
           "Business press", ("Funding", "Executive hire"), "Global"),
    Source("Business Insider", "https://www.businessinsider.com", "candidate",
           "Business press", ("Layoffs", "Hiring"), "Global"),
    Source("Axios", "https://www.axios.com", "candidate",
           "Business press", ("Funding", "M&A"), "Global",
           notes="Pro Rata is the most-read daily deal-flow newsletter in venture."),
    Source("SiliconANGLE", "https://siliconangle.com", "candidate",
           "Technology press", ("Funding", "Product launch"), "Global"),
    Source("Crunchbase", "https://www.crunchbase.com", "candidate",
           "Startup intelligence", ("Funding", "M&A"), "Global", free=False,
           notes="Licensed data. Free tier is not sufficient for systematic use."),
    Source("PitchBook", "https://pitchbook.com", "candidate",
           "Venture and private equity", ("Funding", "PE buyout"), "Global", free=False),
    Source("CB Insights", "https://www.cbinsights.com", "candidate",
           "Market intelligence", ("Funding", "M&A"), "Global", free=False),
    Source("S&P Global Market Intelligence", "https://www.spglobal.com/marketintelligence",
           "candidate", "Market intelligence", ("M&A", "Financial results"), "Global", free=False),
    Source("Morningstar", "https://www.morningstar.com", "candidate",
           "Financial data", ("Financial results",), "Global", free=False),
    Source("Yahoo Finance", "https://finance.yahoo.com", "candidate",
           "Financial data", ("Earnings", "Financial results"), "Global"),
    Source("Investing.com", "https://www.investing.com", "candidate",
           "Financial data", ("Earnings",), "Global"),
    Source("MarketWatch", "https://www.marketwatch.com", "candidate",
           "Markets press", ("Earnings", "Layoffs"), "Global"),
    Source("AlphaSense", "https://www.alpha-sense.com", "candidate",
           "Market intelligence", ("Earnings", "Executive commentary"), "Global", free=False),
    Source("Dealroom", "https://dealroom.co", "candidate",
           "Startup intelligence", ("Funding",), "Global", free=False),
    Source("Tracxn", "https://tracxn.com", "candidate",
           "Startup intelligence", ("Funding",), "Global", free=False),
)


CATALOGUE_CSV = Path(__file__).parent / "data" / "sources_catalogue.csv"


# A source we cannot connect to is not a roadmap item, it is a name. The
# catalogue held 383 rows and 272 of them had no feed, no API and no filing
# system behind them: reading those would mean scraping a homepage, which is
# the failure surface the collectors exist to avoid. They are dropped rather
# than listed as researched, because a long list of names reads as coverage.
#
# Outlets without a feed are not lost. Google News discovery still surfaces
# their articles; they are simply not sources we connect to by name.
_WIREABLE_TYPES = frozenset({
    "Government Agency", "Government Open Data", "Regulatory Body",
    "Stock Exchange", "Statistical Agency",
})


def _wireable(row: dict) -> bool:
    return (
        (row.get("rss") or "").startswith("http")
        or (row.get("api") or "").startswith("http")
        or (row.get("source_type") or "") in _WIREABLE_TYPES
    )


def _catalogue() -> list[dict]:
    """The owner's imported research catalogue.

    Every row is a CANDIDATE. A spreadsheet is research, not coverage: a source
    becomes live only in SOURCES above, where a collector actually reads it.
    """
    if not CATALOGUE_CSV.exists():
        return []
    out = []
    with CATALOGUE_CSV.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if not _wireable(row):
                continue
            signals = tuple(
                s.strip() for s in (row.get("signals") or "").split(";") if s.strip()
            )
            out.append({
                "name": row["name"],
                "url": row["url"],
                "status": "candidate",
                "category": row.get("category") or "Other",
                "signals": list(signals),
                "coverage": row.get("coverage") or "",
                "country": row.get("country") or "",
                "rss": row.get("rss") or "",
                "free": (row.get("free") or "").lower() != "paid",
                "notes": row.get("notes") or "",
            })
    return out


def sources_manifest() -> list[dict]:
    """Renders straight onto the public sources page.

    Hand-written entries win on a name clash: they are the ones that know
    whether a collector exists, and that is the only field the page must never
    get wrong.
    """
    hand = [
        {
            "name": s.name, "url": s.url, "status": s.status,
            "category": s.category, "signals": list(s.signals),
            "coverage": s.coverage, "country": s.country,
            "rss": s.rss, "free": s.free, "notes": s.notes,
        }
        for s in SOURCES
    ]
    seen = {h["name"].lower() for h in hand}
    merged = hand + [c for c in _catalogue() if c["name"].lower() not in seen]
    merged.sort(key=lambda x: (x["status"] != "live", x["name"].lower()))
    return merged


# --- Markets ---------------------------------------------------------------
#
# Every market starts at discovery_only. Promote one ONLY when its official
# connector runs, reports health, and has a passing test.

MARKETS = (
    Market("IE", "Ireland", DISCOVERY_ONLY,
           live_sources=("google_news",),
           candidate_official_sources=("IDA Ireland press releases",),
           terms=("IDA Ireland", "jobs announcement Dublin")),
    Market("GB", "United Kingdom", DISCOVERY_ONLY,
           live_sources=("google_news",),
           candidate_official_sources=("RNS regulatory news", "Companies House appointments",
                                       "Invest NI press releases")),
    Market("US", "United States", DISCOVERY_ONLY,
           live_sources=("google_news",),
           candidate_official_sources=("SEC EDGAR 8-K Item 5.02",)),
    Market("DE", "Germany", DISCOVERY_ONLY,
           live_sources=("google_news",),
           candidate_official_sources=("Germany Trade & Invest",),
           terms=("Stellenaufbau", "neue Arbeitsplätze", "Standort eröffnet")),
    Market("NL", "Netherlands", DISCOVERY_ONLY,
           live_sources=("google_news",),
           terms=("nieuwe banen", "vestiging geopend")),
    Market("BE", "Belgium", DISCOVERY_ONLY, live_sources=("google_news",)),
    Market("LU", "Luxembourg", DISCOVERY_ONLY, live_sources=("google_news",)),
    Market("FR", "France", DISCOVERY_ONLY,
           live_sources=("google_news",),
           candidate_official_sources=("Business France",),
           terms=("créations d'emplois", "nouveau site")),
)

# Spec 14.2: adding a local-language term without that country's papers of
# record is pure waste. Any market with `terms` needs outlets before its terms
# are trusted — tests assert this.


def coverage_manifest() -> list[dict]:
    """Renders straight onto the public sources page. Never hand-maintain a
    coverage table."""
    return [
        {
            "iso2": m.iso2,
            "country": m.name,
            "status": m.status,
            "public_claim": TIER_PUBLIC_CLAIM[m.status],
            "live_sources": list(m.live_sources),
            "candidate_official_sources": list(m.candidate_official_sources),
        }
        for m in MARKETS
    ]


# --- Layer 5: the rotating segment matrix (spec 14) ------------------------

def build_segments() -> list[str]:
    """base vocabulary x geography. Too large to run daily, by design."""
    segments = []
    for market in MARKETS:
        segments.append(market.name)
        segments.extend(market.terms)
    return segments


def rotate(segments: list[str], day_of_year: int, run_index: int,
           runs_per_day: int, per_run: int) -> list[str]:
    """Deterministic rotation so the full matrix sweeps in ~1-2 weeks at flat
    cost. Every segment added lengthens the cycle for all the others."""
    if not segments:
        return []
    start = ((day_of_year * runs_per_day + run_index) * per_run) % len(segments)
    return [segments[(start + i) % len(segments)] for i in range(min(per_run, len(segments)))]
