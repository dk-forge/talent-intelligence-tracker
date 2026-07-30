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
        '("appoints chief executive" OR "names new CEO" OR "steps down as CEO")',
        '("plans to hire" OR "hiring spree" OR "to create jobs" OR "opens new office")',
        '("raises" OR "raised") ("Series A" OR "Series B" OR "seed funding")',
    ),
    "de": (
        '("neuer Vorstandsvorsitzender" OR "wird CEO" OR "verlässt das Unternehmen" OR "tritt zurück")',
        '("will einstellen" OR "schafft Arbeitsplätze" OR "neue Stellen" OR "eröffnet Standort")',
        '("Finanzierungsrunde" OR "sammelt ein" OR "Millionen eingesammelt")',
    ),
    "fr": (
        '("nommé directeur général" OR "devient PDG" OR "quitte ses fonctions" OR "nouveau PDG")',
        '("va recruter" OR "créer des emplois" OR "ouvre un site" OR "plan de recrutement")',
        '("levée de fonds" OR "lève" "millions")',
    ),
    "es": (
        '("nuevo consejero delegado" OR "nombrado director general" OR "deja el cargo" OR "nuevo CEO")',
        '("creará empleo" OR "contratará" OR "nuevos puestos" OR "abre oficina")',
        '("ronda de financiación" OR "capta" "millones")',
    ),
    "pt": (
        '("novo presidente-executivo" OR "assume como CEO" OR "deixa o cargo" OR "novo CEO")',
        '("vai contratar" OR "cria empregos" OR "novas vagas" OR "abre escritório")',
        '("rodada de investimento" OR "capta" "milhões")',
    ),
    "it": (
        '("nuovo amministratore delegato" OR "nominato CEO" OR "lascia la guida")',
        '("assumerà" OR "nuove assunzioni" OR "crea posti di lavoro" OR "apre una sede")',
        '("round di finanziamento" OR "raccoglie" "milioni")',
    ),
    "nl": (
        '("nieuwe topman" OR "wordt CEO" OR "stapt op" OR "benoemd tot bestuursvoorzitter")',
        '("gaat aannemen" OR "nieuwe banen" OR "opent vestiging" OR "breidt uit")',
        '("financieringsronde" OR "haalt" "miljoen op")',
    ),
    # Added 2026-07-27. Every one was fetched live before being committed, and
    # the matching prefilter terms were tested against the real headlines that
    # came back, not against invented ones. Two only shipped after a fix: the
    # Turkish set missed 78% of real Turkish hiring headlines on the first pass
    # (its misses were ordinary newsroom wording, not exotic vocabulary), and
    # Indonesian returned football transfers because "merekrut" is the verb for
    # signing a player.
    "pl": (
        '("nowym prezesem" OR "powołany na stanowisko prezesa" OR "rezygnuje")',
        '("zatrudni" OR "nowe miejsca pracy" OR "otwiera biuro")',
        '("runda finansowania" OR "pozyskał" "mln")',
    ),
    "sv": (
        '("ny vd" OR "utses till vd" OR "lämnar sin post")',
        '("ska anställa" OR "nya jobb" OR "öppnar kontor")',
        '("finansieringsrunda" OR "tar in" "miljoner")',
    ),
    "tr": (
        '("genel müdür atandı" OR "yeni CEO" OR "görevinden ayrıldı")',
        '("işe alacak" OR "istihdam" OR "yeni ofis açtı")',
        '("yatırım turu" OR "yatırım aldı" OR "tohum yatırımı")',
    ),
    "id": (
        '("direktur utama baru" OR "menunjuk CEO" OR "mengundurkan diri")',
        '("membuka lowongan" OR "merekrut" OR "kantor baru")',
        '("pendanaan" OR "putaran pendanaan" OR "seri A")',
    ),
    "vi": (
        '("bổ nhiệm tổng giám đốc" OR "CEO mới" OR "từ chức")',
        '("tuyển dụng" OR "việc làm mới" OR "mở văn phòng")',
        '("vòng gọi vốn" OR "huy động" "triệu USD")',
    ),
    "ja": (
        '("社長に就任" OR "CEOに就任" OR "代表取締役に就任" OR "退任")',
        '("採用を拡大" OR "人員を増やす" OR "新拠点" OR "新オフィス")',
        '("資金調達" OR "シリーズA" OR "シードラウンド")',
    ),
    "ko": (
        '("신임 대표이사" OR "대표이사 선임" OR "CEO 선임" OR "사임")',
        '("채용 확대" OR "신규 채용" OR "인력 충원" OR "사무소 개소")',
        '("투자 유치" OR "시리즈 A" OR "시드 투자")',
    ),
    "ar": (
        '("تعيين رئيس تنفيذي" OR "الرئيس التنفيذي الجديد" OR "استقالة")',
        '("توظيف" OR "فرص عمل" OR "مكتب جديد")',
        '("جولة تمويل" OR "تمويل" "مليون")',
    ),
    # Added 2026-07-29 with the Israel market. Fetched live before being
    # committed, per the standing rule: the leadership phrases returned 21
    # items and the prefilter's Hebrew block kept 10 of the 10 read; funding
    # returned 26 with 9 of 12 kept. One trap is baked into the spelling:
    # מנכ"ל (CEO) is written with a gershayim, and the ASCII double-quote form
    # of it TERMINATES the surrounding phrase quoting, so the whole query
    # matched nothing — 0 items, silently. The U+05F4 gershayim (״) below is
    # punctuation to Google's tokenizer, matches the ASCII-quoted spelling in
    # real headlines, and leaves the phrase quotes intact. "עובדים חדשים"
    # (new employees) was tried and dropped: it pulled machine-translated
    # union stories from the Vietnamese wire, not Israeli employers.
    "he": (
        '("מנכ״ל חדש" OR "מונה למנכ״ל" OR "מונתה למנכ״לית")',
        '("מגייסת עובדים" OR "מגייס עובדים" OR "גיוס עובדים" OR "משרות חדשות" OR "מרכז פיתוח חדש")',
        '("גיוס הון" OR "סבב גיוס" OR "השלימה גיוס" OR "גייסה מיליון")',
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
    ("pl", "PL"),
    ("sv", "SE"),
    ("tr", "TR"),
    ("id", "ID"), ("id", "MY"),
    ("vi", "VN"),
    ("ja", "JP"),
    ("ko", "KR"),
    ("ar", "AE"), ("ar", "SA"), ("ar", "EG"), ("ar", "QA"), ("ar", "MA"),
    # 2026-07-28 widening (coverage audit): English editions need no new query
    # pack, so each of these is one tuple and nothing else. The non-English
    # ones ride the existing es/fr packs. Kept to markets with real hiring/
    # funding news flow; the derived recency window below absorbs the longer
    # sweep automatically.
    # No ("en","US") here: build_locales() pins the US edition as a fixed
    # anchor on every run, so listing it again would sweep it twice.
    ("en", "KE"), ("en", "GH"), ("en", "PK"), ("en", "BD"), ("en", "MY"),
    ("en", "HK"), ("en", "IL"),
    ("es", "PE"), ("es", "EC"), ("es", "UY"),
    ("fr", "CA"), ("fr", "MA"), ("fr", "SN"),
    # 2026-07-29, with the Israel market. The English IL edition above reads
    # what Israeli outlets publish in English; the Hebrew one is where the
    # rounds break first (Calcalist, Globes, TheMarker publish Hebrew hours
    # before CTech's English write-up, when one comes at all). The measured
    # recall failure this answers: Israel held 1 of 10 goldset events.
    ("he", "IL"),
)


# The recency window is DERIVED from how long the rotation takes, never
# hardcoded. It was `when:3d` while 25 locales rotated 3 per run twice a day,
# which swept everything in about four days; adding eight languages made the
# sweep 6.2 days and silently opened a 3.2-day hole in every non-anchor market.
# Nothing errored. Those markets simply returned less, which is indistinguish-
# able from a quiet week.
#
# Widening costs nothing to fetch and nothing to classify: already-seen URLs are
# skipped before any spend, so an overlapping window re-reads headlines we have
# already judged and pays for none of them.
def recency_window_days(locales_per_run: int, runs_per_day: int) -> int:
    """Cover the whole gap between visits, plus a day of margin."""
    import math

    if locales_per_run <= 0 or runs_per_day <= 0:
        return 7
    sweep = math.ceil(len(GOOGLE_NEWS_LOCALES) / locales_per_run / runs_per_day)
    return max(3, min(30, sweep + 1))


def google_news_queries(lang: str, *, window_days: int = 7) -> list[str]:
    """Phrases for one edition. English is the fallback and the anchor."""
    phrases = GOOGLE_NEWS_VOCAB.get(lang, GOOGLE_NEWS_VOCAB["en"])
    return [f"{p} when:{window_days}d" for p in phrases]


# --- The discovery backstop's query ----------------------------------------
#
# A different shape from the locale packs above, and it has to be, because it
# is asked of countries whose Google News edition is thin or absent. Measured
# 2026-07-28 on Kuwait, Barbados, Fiji, Sri Lanka and Mongolia, counting how
# many returned items NAMED the country:
#
#   phrase pack, country quoted on the end   0-5 items,    0 named the country
#   country FIRST, then one intent group     28-54 items,  most named it
#   the same without `when:`                 62-100 items, i.e. the archive
#
# gl=BB has almost no edition behind it, so a phrase-led query falls back to
# the global index and returns US and European stories under a Barbados
# heading. Leading with the country is what keeps the answer about the
# country; `when:` is what stops it being a history lesson.
#
# One query per country, not three. These are places with a handful of
# business stories a month, so three near-identical queries would return the
# same handful three times and spend three times the redirect resolutions on
# them.
BACKSTOP_INTENTS = (
    '"appointed" OR "appoints" OR "chief executive" OR "steps down" OR '
    '"hiring" OR "to hire" OR "new jobs" OR "creates jobs" OR "opens office" OR '
    '"raises" OR "funding round" OR "seed funding" OR "investment"'
)


def backstop_query(country: str, *, window_days: int = 21) -> str:
    """The single discovery query for a country with no direct publisher feed.

    The window is wide because these countries produce few business stories,
    not because old ones are wanted: already-seen URLs are skipped before any
    spend, so re-reading a fortnight costs nothing.
    """
    return f'"{country}" ({BACKSTOP_INTENTS}) when:{window_days}d'


GOOGLE_NEWS_QUERIES = (
    '("appoints chief executive" OR "names chief executive" OR "appointed CEO")',
    '("appoints chief financial officer" OR "appoints CFO" OR "new chief people officer")',
    '("steps down as chief executive" OR "steps down as CEO" OR "to step down as CEO")',
    '("hiring spree" OR "recruitment drive" OR "to ramp up hiring")',
    '("plans to hire" OR "will hire" OR "to add jobs") ("engineers" OR "staff" OR "roles")',
    '("opens new office" OR "opens its new" OR "new engineering hub")',
    '("global capability centre" OR "global capability center")',
    '("raises" OR "raised") ("Series A" OR "Series B" OR "Series C" OR "seed funding")',
    '("acquires" OR "to acquire") ("startup" OR "company") when:2d',
    '("pay rise" OR "raises minimum salary" OR "retention bonus")',
    '("return to office" OR "remote work policy") ("employees" OR "staff")',
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
#
# Tuned 2026-07-28 against a measured day of the archive (2026-01-05, one query
# at a time, counting how many survived the free prefilter as DISTINCT stories).
# The rule the numbers taught: **GDELT matches the article BODY and hands back
# only the TITLE**, so a query built from two AND-ed groups matches an article
# whose two halves are paragraphs apart and whose headline carries neither.
#
#   ("appoints" OR "names") ("chief executive" OR ...)   75 fetched,  5 usable
#   the full phrases below                                7 fetched,  3 usable
#
# Same recall, a tenth of the noise, and the noise is what costs money. So a
# GDELT query should be a phrase a SUB-EDITOR would put in a headline, never a
# conjunction of topics.
#
# The funding line is new. Money is one of the four pillars and GDELT had no
# query for it at all, so that pillar was reachable only through Google News —
# which has no archive, and therefore no 2026.

GDELT_QUERIES = (
    '("hiring spree" OR "to create jobs" OR "will create jobs") sourcelang:english',
    '("global capability centre" OR "global capability center") sourcelang:english',
    '("opens new office" OR "opens new hub" OR "new engineering hub" OR "opens its new office") sourcelang:english',
    '("appoints new chief executive" OR "appoints chief executive" OR "names new chief executive" OR "appointed chief executive officer" OR "names new CEO") sourcelang:english',
    '("steps down as chief executive" OR "steps down as CEO" OR "to step down as CEO" OR "stepping down as chief executive") sourcelang:english',
    '("expands its workforce" OR "recruitment drive" OR "ramp up hiring") sourcelang:english',
    '("return to office" OR "remote work policy" OR "hybrid working") sourcelang:english',
    '("pay rise" OR "raises minimum salary" OR "retention bonus") sourcelang:english',
    '("Series A funding" OR "Series B funding" OR "seed funding round" OR "raises seed round") sourcelang:english',
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


# Which collector reads each live source, keyed by the name shown on the page.
# This exists so the sources page can be checked against what run_collect
# actually registers, in BOTH directions. The check used to be a hardcoded list
# of five names, which caught a source listed without a collector and was blind
# to a collector running with no source listed - and that is the direction the
# defect took: on 2026-07-29 nine collectors were registered while the page
# named five.
#
# Only live sources belong here. A candidate has no collector by definition.
COLLECTOR_BY_SOURCE_NAME = {
    "SEC EDGAR 8-K (Item 5.02)": "sec_edgar",
    "SEC EDGAR Form D": "sec_form_d",
    "GDELT DOC 2.0": "gdelt",
    "Google News RSS": "google_news",
    "Employer job boards (Greenhouse, Lever, Ashby)": "ats_boards",
    "UK gender pay gap service": "uk_paygap",
    "SEC executive compensation disclosures": "sec_execcomp",
    "National and regional tech press": "national_press",
    "BSE corporate announcements (SEBI Regulation 30)": "bse_india",
    "EDINET extraordinary reports (FSA Japan)": "edinet_japan",
}


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
    # Retired on 2026-07-27 and un-retired the same day, which is worth
    # recording rather than tidying away. It was retired for having produced
    # zero records in its whole life. That was true, and the reason was not
    # GDELT: it was the six pipeline bugs fixed earlier that day, above all the
    # free filter rejecting every funding story and 429s being counted as the
    # model declining a candidate. Its first run with those fixed stored three.
    #
    # The lesson is about attribution. A source that yields nothing is either a
    # dead source or a broken pipeline, and the two look identical from the
    # outside. Retire nothing until the pipeline has been proven on a source
    # that does work.
    #
    # It earns its place on complementarity, not volume: 3 stored from 120
    # fetched is a tenth of Google News' rate, but Rossing's recruitment drive
    # in Namibia and a V&A strike ballot are not in any edition we query.
    Source("GDELT DOC 2.0", "https://www.gdeltproject.org/", "live",
           "News aggregation", ("Hiring", "Office opening", "Leadership change"),
           "Global, machine-translated from 65 languages",
           notes="Reaches markets no Google News edition we query covers, and "
                 "it is the only news route with an archive: DOC 2.0 takes "
                 "explicit start and end dates, so 2026 is recoverable through "
                 "it and through nothing else. Throttles erratically; the "
                 "collector paces at 12 seconds a query and retries, and a "
                 "query it never lands is logged as a coverage gap rather than "
                 "retried into a rate-limit spiral."),
    Source("Google News RSS", "https://news.google.com/", "live",
           "News aggregation", ("Hiring", "Funding", "Leadership change", "Layoffs"),
           "38 country editions, 15 languages",
           notes="Keyless and unthrottled. Read in each edition's own language, "
                 "because English phrases in a non-English edition return almost "
                 "nothing. Its links are encoded redirects, but Google's own "
                 "resolution endpoint returns the publisher URL, so records cite "
                 "the article rather than an outlet homepage."),

    # Job-posting volume: the most direct measure of hiring that exists, and
    # the one entry on this page that is a MEASUREMENT rather than a document.
    # The notes say so, and say what it cannot do, because a counted number
    # presented like a filed one is the easiest lie in this product to tell.
    Source("Employer job boards (Greenhouse, Lever, Ashby)",
           "https://boards-api.greenhouse.io/", "live",
           "Employer publications", ("Hiring", "Posted pay", "Location"),
           "Global, per employer",
           notes="Keyless JSON, no model, no cost: an employer's own open roles, "
                 "counted once a day. Three honest limits. The count is OUR "
                 "measurement of a page on two dates, so rows are reported and "
                 "never verified. Nothing can be back-filled, because these APIs "
                 "publish no history and no archive holds snapshots of them, so "
                 "every series starts the day we began counting. And a board "
                 "that shrinks is never read as job cuts: roles leave a board "
                 "when they are filled, withdrawn or reposted, so only growth is "
                 "published. SmartRecruiters was dropped in July 2026 because "
                 "its API robots.txt disallows every agent but LinkedIn's."),

    # These three were LIVE and collecting while this page listed five sources.
    # The project rule is that this page names EXACTLY the live collectors, and
    # understating them is not the safe direction it looks like: a reader
    # judging our coverage was being shown roughly half of what the tracker
    # actually runs on, and the two SEC ones are among the largest contributors
    # of rows in the database. Added 2026-07-29 after diffing this registry
    # against run_collect.SOURCES.
    Source("UK gender pay gap service", "https://gender-pay-gap.service.gov.uk/",
           "live", "Government filings", ("Pay", "Employer size"),
           "United Kingdom", "GB",
           notes="A statutory annual return every UK employer over 250 staff must "
                 "file, so it is a filing rather than a claim, and it needs no "
                 "model to read. It is also the reason one country dominates the "
                 "country chart: nearly every GB row in the tracker comes from "
                 "here, which is filing volume and not British business activity. "
                 "The chart says so where it renders."),
    Source("SEC executive compensation disclosures",
           "https://www.sec.gov/edgar.shtml", "live",
           "Regulatory filings", ("Pay", "Leadership change"), "National", "US",
           notes="Read straight from the filing, with no model in the path. Pay "
                 "figures are as disclosed and are never converted, estimated or "
                 "annualised by us."),
    Source("BSE corporate announcements (SEBI Regulation 30)",
           "https://www.bseindia.com/corporates/ann.html", "live",
           "Regulatory filings", ("Leadership change", "Board appointment"),
           "National", "IN",
           notes="Every company listed in India must tell the exchange when its "
                 "directors or key managerial personnel change, and must file it "
                 "under a category SEBI defines rather than a description it "
                 "chooses. That mandated category is what this reads, so no "
                 "model is involved and there is no cost. It is the first "
                 "structured source here for any country other than the United "
                 "States and the United Kingdom. Two limits worth knowing: it "
                 "carries no city, so Indian rows place at country level only, "
                 "and audit-firm appointments are excluded because an auditor is "
                 "a firm rather than an employee."),
    Source("EDINET extraordinary reports (FSA Japan)",
           "https://disclosure2.edinet-fsa.go.jp/", "live",
           "Regulatory filings", ("Leadership change",),
           "National", "JP",
           notes="Japan's Financial Services Agency types the statutory REASON "
                 "for every extraordinary report as a clause number in its API "
                 "metadata, so no model is involved and there is no cost. Read "
                 "the scope narrowly, because it is much narrower than the "
                 "Indian equivalent: the only officer clause Japan types is a "
                 "change of REPRESENTATIVE DIRECTOR, the chief executive, and "
                 "not the wider board or senior management. The clause covers "
                 "arrivals and departures together, so rows carry no direction "
                 "and name no person; both are in the filing, which is linked. "
                 "It also exempts a change already described in the annual "
                 "report, and Japanese shareholder meetings cluster in the same "
                 "weeks as those reports, so this is a floor on Japanese "
                 "leadership change rather than a count of it. No city is "
                 "carried, so Japanese rows place at country level only."),
    Source("National and regional tech press",
           "https://asktherecruiter.com/blog/talent-intelligence-tracker/sources/",
           "live", "News publishers",
           ("Funding", "Hiring", "Leadership change", "Office opening"),
           "593 verified feeds across 139 countries",
           notes="Publishers' own feeds, never an aggregator's database: where a "
                 "round is found through a directory, the record cites the outlet "
                 "that reported it. Every feed was fetched and parsed before "
                 "being listed, because a feed that answers 200 with zero items "
                 "reads as healthy forever. Feeds a publisher's robots.txt "
                 "disallows are not requested at all, and 25 were withdrawn on "
                 "that basis. Countries with no usable publisher feed are covered "
                 "by a Google News country edition instead, marked as discovery "
                 "backstop rather than as a named publisher."),

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
        # A backstop row has no feed and no publisher, and it is still the most
        # connected thing we have for its country: a search that runs twice a
        # day and reports its own health. Excluding it would leave the page
        # silent about twenty-one countries that ARE collected, which is the
        # mirror image of the overclaiming the prune above exists to stop.
        or _is_backstop(row)
    )


def _is_backstop(row: dict) -> bool:
    return (row.get("feed_role") or "").strip().lower() == "backstop"


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
                # Three states, not two. "candidate" is research; "live" is a
                # named source a collector reads; "backstop" is a country
                # reached by discovery search, with no publisher behind the
                # name. Folding the third into either of the others tells the
                # reader something untrue in one direction or the other.
                "status": "backstop" if _is_backstop(row) else "candidate",
                "category": row.get("category") or "Other",
                "signals": list(signals),
                "coverage": row.get("coverage") or "",
                "country": row.get("country") or "",
                "rss": row.get("rss") or "",
                "free": (row.get("free") or "").lower() != "paid",
                "notes": row.get("notes") or "",
                # A catalogue row is research and a backstop row is a country
                # search, so neither has a named collector. The key is still
                # present so the page can read one shape for every row.
                "collector": "",
            })
    return out


def sources_manifest() -> list[dict]:
    """Renders straight onto the public sources page.

    Hand-written entries win on a name clash: they are the ones that know
    whether a collector exists, and that is the only field the page must never
    get wrong.

    Each live row carries its `collector` key. That is what lets the page join a
    source to its health row without a second, hand-typed copy of this map: the
    PHP side had five of the nine entries, so `national_press` (the largest
    source by items found), `sec_execcomp` and `uk_paygap` all rendered as "not
    yet reported" while running twice a day. Derived, not typed.
    """
    hand = [
        {
            "name": s.name, "url": s.url, "status": s.status,
            "category": s.category, "signals": list(s.signals),
            "coverage": s.coverage, "country": s.country,
            "rss": s.rss, "free": s.free, "notes": s.notes,
            "collector": COLLECTOR_BY_SOURCE_NAME.get(s.name, ""),
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
#
# --- THE 2026-07-30 TRIAGE, so nobody researches these twice ----------------
#
# Ten candidates named below were each fetched and checked for a machine-readable
# mechanism, for robots permission, and for whether they are a PRIMARY source.
# One was built. The findings, because a candidate that is impossible should not
# sit on the roadmap looking merely unstarted:
#
#   BUILT
#     IN  BSE Regulation 30 announcements — 354 leadership filings in a live
#         7-day window, ~18,500/year, mandated category, keyless, no LLM cost.
#         collectors/bse_india.py. Note this is BSE only: NSE runs the same SEBI
#         regime, so adding it would double-count the same filings.
#     JP  EDINET extraordinary reports. The FSA's v2 API types the statutory
#         reason for every 臨時報告書 as a CLAUSE NUMBER in the document-list
#         metadata (`currentReportReason`, spec Version 2 page 47 footnote *4),
#         so it is the same class of machine-readable label as Item 5.02 and
#         SEBI Regulation 30, and it needs no document download and no model.
#         collectors/edinet_japan.py. THE SCOPE IS MUCH NARROWER THAN INDIA'S
#         and must not be described as "officer changes": read from the
#         ordinance itself (e-gov 348M50000040005), 企業内容等の開示に関する
#         内閣府令 第19条第2項 has 44 items and EXACTLY ONE is an officer
#         change — item 9, 代表取締役の異動, the representative director alone.
#         Three consequences a later session would otherwise re-derive:
#           * `第19条第2項第9号の2/の3/の4` all have that clause as a string
#             PREFIX and are shareholder resolutions, a rejected AGM resolution
#             and a change of ACCOUNTING AUDITOR. A substring match files audit
#             firms as leadership changes, which is the bse_india auditor bug
#             again. `第29条第2項第9号` belongs to the specified-securities
#             ordinance (405M50000040022) and is a FUND MERGER; that ordinance
#             has no officer clause at all, so REITs are out by law, not taste.
#           * item 9 exempts a change already described in the annual report,
#             and Japanese AGMs cluster in the same weeks as those reports, so
#             the commonest timing of a Japanese succession can produce no
#             report at all. This source is a FLOOR, not a census.
#           * the clause covers arrivals and departures together, so no row can
#             carry a direction and no person is named. Recovering either means
#             reading the document body, which is an LLM call per document and
#             was declined.
#         VOLUME IS UNMEASURED. No authenticated call has ever been made from
#         this repo, so unlike India's 354-in-7-days and Australia's 192-in-30
#         there is no live count here, only a bound: 3,829 listed filers on the
#         official code list (2026-07-30) against a published Japanese
#         president-turnover rate of 3.84% for 2025, which puts the order of
#         magnitude at a few hundred a year — roughly 1-3% of India's. That is
#         thin but not zero, and it is the highest-value leadership row there
#         is. JAPAN THEREFORE STAYS discovery_only: the first real run measures
#         it, and promotion is one commit after that.
#         The sibling AI Layoff Tracker built an EDINET client and RETIRED it on
#         2026-07-24 for "0 layoff rows ever". That result does NOT transfer and
#         is not evidence against this: it never read `currentReportReason`, it
#         scanned document bodies for layoff vocabulary, and NONE of the 44
#         clauses in Article 19(2) contains any workforce-reduction word, so its
#         zero was guaranteed by the ordinance rather than by the source's
#         quality. Read it as a fact about layoffs, not about appointments.
#
#   BLOCKED — do not retry without the owner doing something first
#     GB  Companies House appointments. Every route needs an API key: the REST
#         API and the streaming API both 401 unauthenticated, and the free bulk
#         "Company Data Product" carries no officers at all. Genuine registry
#         volume, and the key is free — but a human must create it. NEEDS-OWNER.
#     IL  Tel Aviv Stock Exchange (MAYA). maya.tase.co.il/robots.txt says
#         `Disallow: /api/`, which is the disclosure feed itself, and
#         api.tase.co.il sits behind an Imperva bot wall. The exchange's own
#         terms are the answer here, so this is closed unless the owner takes a
#         TASE API subscription. NEEDS-OWNER.
#     IL  Israel Innovation Authority. Cloudflare returns 403 to every path
#         including /sitemap.xml, so there is nothing to parse without defeating
#         bot protection. Closed.
#     GB  RNS via the FCA National Storage Mechanism, the obvious registry-grade
#         route once Companies House was blocked: the portal 403s, its API wants
#         an auth token, and data.fca.org.uk/robots.txt names ClaudeBot under
#         `Disallow: /`. Refused on the robots file alone. NEEDS-OWNER.
#
#     AU  ASX market announcements. The ONE candidate blocked by a LICENCE
#         rather than by a robots file or a missing taxonomy, which is why it
#         gets its own paragraph: everything technical about it works.
#         Measured live 2026-07-30 over the whole window the API exposes
#         (2026-06-30 to 2026-07-30, 10,000 announcements, 400 pages of 25 at
#         asx.api.markitdigital.com/asx-research/1.0/markets/announcements):
#         142 distinct MANDATED announcement types, of which the board and
#         officer ones are `Director Appointment/Resignation` 105,
#         `Company Secretary Appointment/Resignation` 48,
#         `CEO/Managing Director - Appointment Resignation` 46 and
#         `Chair Appointment/Resignation` 33 — 192 announcements in 30 days,
#         about 45 a week, ~2,300 a year. That is the same kind of machine-
#         readable label Item 5.02 and SEBI Regulation 30 give, so the
#         taxonomy problem that kills Form 6-K does not exist here.
#         www.asx.com.au/robots.txt permits it: the whole file is
#         `Disallow: /search*`, and neither asx.api.markitdigital.com nor
#         announcements.asx.com.au serves a robots.txt at all.
#         WHAT BLOCKS IT is www.asx.com.au/legals/terms-of-use, which says in
#         two independent places that this use needs ASX's permission:
#         "Market Announcements are freely available for investors' private and
#         personal use only, and cannot be used for any commercial purpose
#         without the express written authority of ASX. A commercial purpose is
#         any use other than accessing and using the content for your own
#         personal and private decision making"; and, under Prohibited uses,
#         "use any spider, screen scraper, robot ... to use or access the Site
#         in any way whatsoever, including monitoring, downloading or copying
#         any content on the Site (except ... with ASX's prior written
#         consent)". The legacy interstitial at
#         /asx/v2/statistics/displayAnnouncement.do makes the reader confirm it
#         by hand: "I confirm that any content I access will not be used for any
#         commercial purpose". ASX sells this use as ComNews / ComNews Direct.
#         So this is the SmartRecruiters decision again (see
#         collectors/ats_watchlist.json): the endpoints answer 200 and the terms
#         still say no, which is exactly why it is written down here instead of
#         being discovered by whether a request works. NEEDS-OWNER — one email
#         to ASX Information Services, and the connector is a day's work with
#         the measurements above already done.
#         Two traps recorded so the next attempt does not re-find them: the
#         API's `url` field is empty on all 10,000 rows, and the PDF is reached
#         from `documentKey` at asx.api.markitdigital.com/asx-research/1.0/file/
#         {documentKey}, on the vendor's host rather than asx.com.au. And
#         `Change of Director's Interest Notice` (Appendix 3Y, 589 in the same
#         30 days — the largest of any leadership-looking type) is NOT an
#         appointment: it is a SITTING director's shareholding moving under
#         Listing Rule 3.19A. Collecting it because it looks like the spine
#         would treble the volume with rows that are not talent signals at all.
#         The appointments themselves sit under Listing Rule 3.16.1.
#
#   REAL BUT TOO THIN TO BE WORTH A CONNECTOR
#     A connector yielding a handful of rows a month is worse than none, because
#     it renders on the sources page as coverage. Investment promotion agencies
#     are legitimate primary sources about their own announcements, but they are
#     press offices, not registries, and the volume shows it:
#     GB  Invest NI — the only one of the eight with a working RSS feed. Ten
#         items covering three weeks, and most are staff profiles and business
#         features rather than employer job announcements.
#     IE  IDA Ireland (403 on every RSS path), FR Business France (503),
#         DE Germany Trade & Invest (301, no feed), IN Invest India (403),
#         CA Invest in Canada (403), AU Austrade (no feed), JP JETRO (no feed),
#         SG Singapore EDB (403 on robots.txt itself).
#
#   WRONG SHAPE, measured rather than assumed
#     Foreign private issuers on SEC EDGAR looked like the big unlock: the
#     `locationCodes` parameter on efts.sec.gov filters by the filer's own
#     registered country (L3 Israel, L2 Ireland, 2M Germany, I0 France, K7
#     India, C3 Australia, U0 Singapore, M0 Japan, X0 United Kingdom, A0-B0 the
#     Canadian provinces), and Israel alone has 1,952 6-K/20-F/40-F filings YTD.
#     It does not work, for a structural reason worth remembering: Form 6-K has
#     NO item taxonomy. Foreign issuers file no Item 5.02 equivalent, so there
#     is nothing to search but prose, and sampling "appointed as" against
#     Israeli filers returned resellers, distributors and Companies Law
#     boilerplate at about one useful hit in eight. THAT is why the US has 7,620
#     documents and Israel has 24, and it is why the jurisdictions worth
#     building next are the ones that mandate a category: India (built),
#     Australia (ASX does type its announcement headers, and robots.txt does
#     permit the pages — but the terms of use do not; see the AU paragraph
#     above, and do not re-research it on the strength of the robots file
#     alone), and the UK once a key exists.

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
    # --- 2026-07-29 widening -------------------------------------------------
    # Every addition below already has its papers of record wired in
    # data/sources_catalogue.csv (spec 14.2: a local-language term without
    # that country's outlets is pure waste — a test now asserts the pairing),
    # and its Google News edition already sits in GOOGLE_NEWS_LOCALES above,
    # so listing it here claims nothing that is not already collected. All
    # start at discovery_only, the tier that is honest about what runs.
    #
    # Israel first, because it is the measured hole: the 2026-07-28 recall
    # goldset held 1 of 10 Israeli events, and the owner found four missed
    # Tel Aviv rounds by simply asking an outside model. Ten wired feeds
    # (Globes x3, Geektime, Techtime, Ynet, Haaretz, the Innovation
    # Authority, NoCamels, JPost) plus the new Hebrew query pack are the
    # response. Hebrew terms are live-verified newsroom phrasing, not
    # translations — see the "he" pack note above for the gershayim trap.
    Market("IL", "Israel", DISCOVERY_ONLY,
           live_sources=("google_news",),
           candidate_official_sources=("Israel Innovation Authority programme "
                                       "announcements",),
           terms=("גיוס הון", "מגייסת עובדים", "מנכ״ל חדש",
                  "Israeli startup raises", "opens Tel Aviv office")),
    # The first market outside the US and the UK to earn structured_official.
    # It is earned in the sense this file means: collectors/bse_india.py runs,
    # reports health, and has a passing offline test. What earned it is not
    # Indian exceptionalism but SEBI Regulation 30's MANDATED disclosure
    # category — the same kind of machine-readable label that Item 5.02 gives
    # US officer changes, and the thing nine other researched candidates turned
    # out to lack. See the triage note above candidate_official_sources below.
    Market("IN", "India", STRUCTURED_OFFICIAL,
           live_sources=("google_news", "bse_india"),
           candidate_official_sources=("Invest India press releases",),
           # Indian business press is English-first; the segment that earns
           # its keep is the GCC wave, phrased to avoid colliding with the
           # standalone euphemism queries (a test keeps the two sets disjoint).
           terms=("GCC in India", "hiring in Bengaluru", "नई नौकरियां")),
    Market("CA", "Canada", DISCOVERY_ONLY,
           live_sources=("google_news",),
           candidate_official_sources=("Invest in Canada announcements",),
           terms=("new jobs Toronto", "embauche au Québec")),
    Market("AU", "Australia", DISCOVERY_ONLY,
           live_sources=("google_news",),
           candidate_official_sources=("Austrade announcements",),
           terms=("new jobs Sydney", "hiring in Melbourne")),
    Market("SG", "Singapore", DISCOVERY_ONLY,
           live_sources=("google_news",),
           candidate_official_sources=("Singapore EDB press releases",),
           terms=("regional headquarters Singapore", "new jobs Singapore")),
    Market("JP", "Japan", DISCOVERY_ONLY,
           live_sources=("google_news",),
           candidate_official_sources=("JETRO investment announcements",),
           terms=("採用拡大", "新拠点", "社長に就任")),
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
