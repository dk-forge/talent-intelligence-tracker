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

from dataclasses import dataclass, field

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
