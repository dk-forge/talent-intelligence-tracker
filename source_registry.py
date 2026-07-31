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
#
# IT CARRIES NO FUNDING TERM, AND THAT IS NOT THE GAP IT LOOKS LIKE.
# Checked 2026-07-30, because 36 hiring/leadership/pay terms with nothing about
# funding reads as a hole in the product's largest pillar. It is not on any live
# query path: `run_collect.build_queries` returns GOOGLE_NEWS_QUERIES for
# google_news and GDELT_QUERIES for gdelt, and reaches the branch that reads
# this only for sources whose collectors take the population from a feed list, a
# register or a frame and ignore the `queries` argument entirely. Funding IS
# queried — by GOOGLE_NEWS_QUERIES, by all sixteen GOOGLE_NEWS_VOCAB packs, by
# GDELT_QUERIES and by BACKSTOP_INTENTS. Padding this tuple would look like
# closing a gap and change nothing that runs. If a future collector is wired to
# this branch, add the funding terms then, and take them from the widened
# GOOGLE_NEWS_VOCAB below rather than writing new ones.
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
#
# THE FUNDING QUERY WAS MEASURED ON 2026-07-30 AND WIDENED.
# ---------------------------------------------------------
# The old English phrasing was `("raises" OR "raised") ("Series A" OR "Series B"
# OR "seed funding")`. Google News AND-s the two groups, so a round that is
# never called a Series or a seed — a growth round, a debt facility, a credit
# line, a capital increase, an undisclosed stage — could not match however many
# times the article said "raises". That is a whole class of funding event
# structurally excluded, not a phrase that happened to be missing.
#
# Measured against the 54 funding events the 2026-07-28 recall run MISSED,
# reading each publisher's own headline:
#
#   old query      13 / 54  (24%)
#   this one       40 / 54  (74%)     0 false hits on the 19 leadership
#                                     headlines in the same gold set
#
# The 14 still unmatched are all "X raises $60m" — a verb and an abbreviated
# amount and no noun at all. Google News matches article text and not only
# headlines, so both figures are LOWER bounds and those 14 are probably reached
# by the body copy; a direct RSS probe to settle it returned zero rows for every
# query including the control, so that remains unverified rather than assumed.
#
# Every verb and every euphemism below appears verbatim in one of those 54 real
# headlines. None was invented, which is the same discipline the sibling's
# layoff euphemism list was built with, and the reason the Czech `investice`
# trap is not repeated here: no bare high-frequency token stands alone.
GOOGLE_NEWS_VOCAB = {
    "en": (
        '("appoints chief executive" OR "names new CEO" OR "steps down as CEO")',
        '("plans to hire" OR "hiring spree" OR "to create jobs" OR "opens new office")',
        '("raises" OR "raised" OR "secures" OR "closes" OR "lands" OR "announces" OR "nets") '
        '("funding" OR "round" OR "Series A" OR "Series B" OR "seed" OR "investment")',
        '("Series A" OR "Series B" OR "Series C" OR "seed round" OR "pre-seed") '
        '("million" OR "billion" OR "led by" OR "valuation")',
        '("emerges from stealth" OR "out of stealth" OR "oversubscribed" OR '
        '"bridge round" OR "extension round" OR "growth capital" OR '
        '"strategic investment" OR "capital increase" OR "credit line")',
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
        # "cierra una ronda" is here because a real miss used it and the old
        # phrasing could not: Techla's "Kintai cierra una ronda Serie A de 10
        # millones de euros". Spanish and Italian are the only two non-English
        # packs touched, because they are the only two where a missed headline
        # in that language is on file. Widening the other thirteen on the
        # strength of the English result would be exactly the guesswork the
        # per-language design exists to avoid.
        '("ronda de financiación" OR "ronda de inversión" OR "cierra una ronda" '
        'OR "levanta capital" OR "capta" "millones")',
    ),
    "pt": (
        '("novo presidente-executivo" OR "assume como CEO" OR "deixa o cargo" OR "novo CEO")',
        '("vai contratar" OR "cria empregos" OR "novas vagas" OR "abre escritório")',
        '("rodada de investimento" OR "capta" "milhões")',
    ),
    "it": (
        '("nuovo amministratore delegato" OR "nominato CEO" OR "lascia la guida")',
        '("assumerà" OR "nuove assunzioni" OR "crea posti di lavoro" OR "apre una sede")',
        # "aumento di capitale" is the mechanism Italian press names when a
        # round is a capital increase rather than a Series: Young Group's €22.5m
        # on 2026-07-16 was reported that way and nothing here could match it.
        '("round di finanziamento" OR "aumento di capitale" OR "raccoglie" "milioni")',
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
    # Widened 2026-07-30 for the reason set out on GOOGLE_NEWS_VOCAB: the old
    # single query AND-ed a raise verb with a stage word, so a round nobody
    # called a Series or a seed could not match at all.
    '("raises" OR "raised" OR "secures" OR "closes" OR "lands" OR "announces" OR "nets") '
    '("funding" OR "round" OR "Series A" OR "Series B" OR "seed" OR "investment" '
    'OR "capital" OR "million" OR "billion" OR "valuation" OR "led by")',
    '("Series A" OR "Series B" OR "Series C" OR "seed round" OR "pre-seed") '
    '("million" OR "billion" OR "led by" OR "valuation")',
    '("emerges from stealth" OR "out of stealth" OR "oversubscribed" OR '
    '"bridge round" OR "extension round" OR "growth capital" OR '
    '"strategic investment" OR "capital increase" OR "credit line")',
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
    # Same widening as the Google News packs, in GDELT's syntax (space is AND,
    # so these stay a flat OR of phrases). The phrases are the ones that occur
    # in the 54 real funding misses; a bare "funding" or "investment" is
    # deliberately absent, because unanchored high-frequency tokens are what
    # made "expansion" return cattle herds on the first live run.
    '("Series A funding" OR "Series B funding" OR "Series C funding" OR "seed funding round" OR "raises seed round" OR "closes seed round") sourcelang:english',
    '("secures funding" OR "raises funding" OR "closes funding round" OR "lands investment" OR "oversubscribed round" OR "emerges from stealth" OR "growth capital round") sourcelang:english',
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
    "DART disclosures (Korea, FSS OpenDART)": "opendart_korea",
    "Companies House officer appointments": "companies_house",
    "ARES Czech company register (veřejný rejstřík)": "czechia_ares",
    "Estonian business register (Ariregister open data)": "estonia_ariregister",
    "BORME Section A (Registro Mercantil, Spain)": "spain_borme",
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
    Source("DART disclosures (Korea, FSS OpenDART)",
           "https://dart.fss.or.kr/", "live",
           "Regulatory filings", ("Leadership change", "Board appointment"),
           "National", "KR",
           notes="Korea's Financial Supervisory Service runs the mandatory "
                 "disclosure registry, and the Korea Exchange's own filing "
                 "system assigns the report title, so no model is involved and "
                 "there is no cost. Read the scope narrowly. Korea types its "
                 "disclosures one level coarser than India does: every timely "
                 "disclosure shares one code, so what selects a row is the "
                 "exchange's own report title, and only two kinds of change "
                 "have one. A change of representative director, which is the "
                 "chief executive, and the appointment, dismissal or early "
                 "retirement of an independent director. Ordinary inside "
                 "directors are elected at a shareholder meeting and do not "
                 "appear. The title records that the change happened without "
                 "recording which direction it went, so rows carry no "
                 "direction and name no person; both are in the filing, which "
                 "is linked. No city is carried, so Korean rows place at "
                 "country level only. A filing a listed parent makes about a "
                 "subsidiary it does not name is excluded, because the change "
                 "is not the parent's."),
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
    Source("Companies House officer appointments",
           "https://find-and-update.company-information.service.gov.uk/",
           "live", "Government filings", ("Leadership change", "Board appointment"),
           "United Kingdom", "GB",
           notes="Every UK company must tell the registrar who its directors "
                 "and secretaries are, so this is a register entry rather than "
                 "a claim, and it needs no model to read. Two limits matter "
                 "more here than on any other source. First, it is NOT the "
                 "whole register: there are about 5.7 million UK companies, "
                 "most of them dormant micro-companies whose director changes "
                 "say nothing about the labour market, so this reads only the "
                 "9,230 employers the gender pay gap duty covers, meaning 250 "
                 "employees or more. Second, a row is the legal fact of an "
                 "appointment and not evidence of a hire: the register does "
                 "not say whether the person came from inside or outside the "
                 "business, so nothing here is counted as hiring. Departures "
                 "sit on the same records and are not collected, because the "
                 "register never says why somebody left. The registered office "
                 "is recorded as the employer's address and never as a job "
                 "location."),
    Source("ARES Czech company register (veřejný rejstřík)",
           "https://ares.gov.cz/ekonomicke-subjekty",
           "live", "Government filings",
           ("Leadership change", "Board appointment", "Board departure"),
           "Czechia", "CZ",
           notes="The Czech public register states, per person, the date an "
                 "office actually began and the date it ended, separately from "
                 "the dates a court registered either. That makes it the only "
                 "registry read here that reports departures on the same "
                 "footing as arrivals, and none of it is a difference between "
                 "two readings. Two limits. It is NOT the whole register: the "
                 "register's own change feed carries about 22,500 companies a "
                 "month and this reads the 1% whose employee band in the "
                 "statistical register is 250 or more, so an employer whose "
                 "band was never filled in is missed rather than judged small. "
                 "And a row is the legal fact of an office beginning or "
                 "ending, never a hire and never a redundancy: the register "
                 "does not say where the person came from or why they left. "
                 "The registered office is recorded as the employer's address "
                 "and never as a job location."),
    Source("Estonian business register (Ariregister open data)",
           "https://avaandmed.ariregister.rik.ee/en/downloading-open-data",
           "live", "Government filings",
           ("Leadership change", "Board appointment"),
           "Estonia", "EE",
           notes="The register's daily open-data file states the date each "
                 "person's office began, so an appointment is read rather than "
                 "inferred. Read the gap as carefully as the rows: the "
                 "published file lists only the people holding an office "
                 "TODAY, so it carries no end date on any of its 520,895 rows "
                 "and this source reports appointments and never departures. "
                 "It is also not the whole register: about 202 appointments a "
                 "day are published for a country of 1.3 million people, "
                 "mostly at one-person companies, so this reads only employers "
                 "reporting 50 full-time equivalent staff or more in their "
                 "annual accounts, the point at which the European "
                 "Commission's own definition stops calling a business small. "
                 "A row is the legal fact of an appointment and not evidence "
                 "of a hire. No city is stated, because the file carries "
                 "none."),
    Source("BORME Section A (Registro Mercantil, Spain)",
           "https://www.boe.es/diario_borme/", "live", "Government filings",
           ("Leadership change", "Executive hire", "Executive departure"),
           "Spain", "ES",
           notes="Every act inscribed in a Spanish commercial register is "
                 "published by law in the Boletín Oficial del Registro "
                 "Mercantil, under the register's own fixed act heading and "
                 "with the office as a fixed abbreviation, so an appointment "
                 "and a removal are told apart by the bulletin's words rather "
                 "than by reading its prose. This is one of only two sources "
                 "here that report a DEPARTURE. The bulletin publishes no "
                 "headcount, and the accounts that would are sold rather than "
                 "published, so there is no employee threshold to draw and the "
                 "filter is the office instead: this reads the consejero "
                 "delegado, the director the board has delegated its powers to "
                 "under article 249 of the Ley de Sociedades de Capital, and "
                 "not a seat on the board. Everything board-grade would be "
                 "about 494 acts a day; this is about 49. The date on a row is "
                 "the day the registrar inscribed the act, which the bulletin "
                 "publishes about a week later, so a Spanish row is a week old "
                 "by construction."),
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
#     KR  OpenDART (Financial Supervisory Service). collectors/opendart_korea.py.
#         Korea's typed taxonomy STOPS ONE LEVEL TOO COARSE, and that is the
#         finding worth keeping. All 84 endpoints across the six published API
#         groups were read on 2026-07-29. `pblntf_detail_ty` has ~60 values and
#         every Korea Exchange timely disclosure — supply contracts, dividends,
#         buybacks, CEO changes, litigation — shares ONE of them, `I001`. There
#         is no Item 5.02 equivalent to ask for. Two things rescue it:
#           * `E005` is a detail code of its own whose every row (150 of 150 in
#             90 days) carries one report name, 독립이사의선임ㆍ해임또는중도퇴임
#             에관한신고 — the appointment, dismissal or early retirement of an
#             independent director. Typed, and typed as the event.
#           * inside `I001`, the exchange's own report TITLE is a fixed
#             vocabulary. 8,211 I001 filings over 2026-05-01..2026-07-29 carry
#             360 DISTINCT titles, and the leadership ones recur identically:
#             대표이사변경 79, 대표이사(대표집행임원)변경(안내공시) 28,
#             대표집행임원변경 4. That is KRX's form title, not a sentence a
#             company composed, so matching it is the same act as matching BSE's
#             SUBCATNAME — and it is the ONLY thing that makes I001 usable,
#             because reading the code alone would store the whole feed.
#         MEASURED: 261 allowlisted filings in 90 days, ~1,060/year, from
#         DART's own public search unauthenticated (dart.fss.or.kr/dsab007/,
#         which robots.txt permits). Per ISO week over twelve full weeks: 12 to
#         49, median 19. That is ~8% of India's volume, and the reason is
#         scope: Regulation 30 covers every director and key managerial person,
#         while Korea's mandated item covers the representative director and,
#         separately, independent directors. Ordinary inside directors are
#         elected at a shareholder meeting whose result is untyped prose.
#         REFUSED, with the numbers, so nobody re-derives them:
#           * the 36 주요사항보고서 endpoints (group DS005) contain NO officer
#             item at all — insolvency, capital raises, buybacks, mergers,
#             asset transfers. The brief that commissioned this named that
#             family as a candidate; it is a dead end.
#           * 임원현황 (exctvSttus.json) and 직원현황 (empSttus.json) are
#             point-in-time SNAPSHOTS: a roster as of stlm_dt with tenure as
#             free text, and a headcount by division. Neither states an
#             appointment and neither carries an appointment date. An event out
#             of them means diffing year N against N-1 and stamping a date the
#             source never stated, which is what "no source URL, no record" and
#             "the model never invents a number" both forbid.
#           * 독립(사외)이사 및 그 변동현황 is the one endpoint with change
#             FIELDS (apnt / rlsofc / mdstrm_resig) and they are period COUNTS
#             with no person and no date.
#           * 임원ㆍ주요주주 소유보고 (elestock.json) is event-driven and
#             carries rcept_dt, but the API exposes no 보고사유, so an
#             appointment cannot be told from a share purchase.
#           * 대표이사변경 (자회사의 주요경영사항), 2 of 261: a listed PARENT
#             reporting a change at a subsidiary it does not name in the title.
#             The chaebol trap, refused rather than collapsed.
#         The English viewer at englishdart.fss.or.kr/dsbh001/main.do looked
#         like the ideal citation and is NOT usable: on 20 real filings sampled
#         2026-07-29 it answered 200 with a body of the single word "Reject" for
#         4 of them, Kia and Korea Gas Corporation among them. source_url is
#         therefore dart.fss.or.kr/dsaf001/main.do?rcpNo=, the form OpenDART's
#         own field documentation gives — and that path IS disallowed by
#         dart.fss.or.kr/robots.txt, so link_check.py records these as `robots`
#         rather than checking them. Nothing fetches it; the collector talks
#         only to opendart.fss.or.kr/api/, which serves no robots.txt.
#         KOREA THEREFORE STAYS discovery_only. The source is measured; the
#         connector has never made an authenticated call, and a tier is a claim
#         about the connector. Promotion is one commit after the first real run.
#         The sibling AI Layoff Tracker built an OpenDART client and RETIRED it
#         on 2026-07-24 for "0 layoff rows ever". That result does NOT transfer:
#         it read the disclosure list for DISCOVERY and then scanned document
#         BODIES for Korean layoff vocabulary, and Korean statutory disclosure
#         has no workforce-reduction item — the 36 major-report endpoints above
#         are the proof. Its zero was guaranteed by the taxonomy, not by the
#         source's quality. Read it as a fact about layoffs, not appointments.
#
#     GB  Companies House officer appointments — BUILT 2026-07-30, once the
#         owner created the key this triage said was the only blocker.
#         collectors/companies_house.py. The key unblocked the ACCESS and not
#         the design, and the design was the whole problem: the register holds
#         about 5.7 million live companies making ~1.4 million officer
#         appointments a year (~27,000 a week, measured on a random sample of
#         120 of them), against a database of 15,711 signals. So the population
#         is not the register. It is the 9,230 employers the gender pay gap
#         duty covers — the only free primary list of UK companies keyed on
#         EMPLOYEES rather than on filing choices — which yields ~7,354
#         appointments a year, ~110 stored rows a week. The accounts-category
#         filter that looks like the obvious alternative was measured and
#         refused: 6.35% of FULL/GROUP/MEDIUM filers are 250+ employee
#         employers. Full derivation in the collector's docstring.
#         Note the STREAMING API is not the route, whatever it looks like:
#         Companies House registers streaming applications separately and says
#         the keys are not interchangeable, and a connection that must stay
#         open to keep its timepoint cannot live in a workflow that holds one
#         writer lock and exits.
#
#     CZ  ARES, the Czech Ministry of Finance's register service — BUILT
#         2026-07-30. collectors/czechia_ares.py. Keyless, no model, and the
#         only registry in this tracker that states BOTH directions per person:
#         `clenstvi.clenstvi.vznikClenstvi` and `zanikClenstvi` are the dates
#         an office actually began and ended, `funkce.vznikFunkce` /
#         `zanikFunkce` the same for a named role, all separate from the
#         `datumZapisu` / `datumVymazu` on which the court registered them.
#         Nothing is diffed out of two snapshots.
#         MEASURED live over 2026-07-02..07-29: the change feed carried 24,651
#         notifications across 23 batches, 22,492 distinct companies, ~880 a
#         day; 226 of them (1.0%) sit at 250 employees or more; those produced
#         **42 office events at 20 employers in 28 days, ~550 a year** — 20
#         arrivals, 4 departures, 9 promotions, 9 role endings.
#         Two traps, both found by reading real records rather than the spec:
#           * `datumVymazu` is NOT a departure. 353 of 543 member versions on
#             ČEZ's record carry one with no `zanikClenstvi`; they are
#             amendments. Reading them as exits reports a leaving rate about
#             nine times the truth.
#           * and the obvious repair, reading only the live version, loses a
#             real change: Jean-Charles Chen stopped being chairman of ICO
#             17774713 on 2026-07-10 and stayed on the board, and the ONLY
#             place that fact exists is the version the register has already
#             deleted. The collector groups the versions and reads all of them.
#         REFUSED, with the number: legal form as a materiality proxy. Filtering
#         to `a.s.` joint-stock companies polls 1,362 to find 117 material ones,
#         8.6% precision — the UK accounts-category failure again (6.35%), and
#         for the same structural reason.
#         THE HOLE, stated because it is large: `kategoriePoctuPracovniku` is
#         `000 Neuvedeno` on 12,624 of 19,285 RES records (65%) and on 41.6% of
#         joint-stock companies, and 3,207 of the 22,492 have no RES record at
#         all. A large employer whose statistical band was never populated is
#         invisible here. That is a recall hole, not a precision one, and it is
#         on the sources page rather than only in a docstring.
#     EE  Ariregister, the Estonian Centre of Registers and Information
#         Systems — BUILT 2026-07-30. collectors/estonia_ariregister.py.
#         Keyless, no model, three static file downloads.
#         THE LOAD-BEARING NEGATIVE: `lopp_kpv` is null on **520,895 of
#         520,895** person rows, because the published file lists CURRENT
#         office-holders only. Estonia yields appointments and NEVER
#         departures, no window can change that, and the sentence is on every
#         stored row, in the sources-page note and in the read-through.
#         Refused rather than worked around: `arireg.ettevotjaMuudatusedTasuline_v1`,
#         the SOAP change list, needs an account and is *tasuline* (chargeable);
#         and diffing yesterday's file against today's, because a vanished row
#         may be a departure, a correction, a merger or a deregistration and
#         the file states no date for any of them.
#         MEASURED on the whole 2026-07-30 file: 375,305 companies, 520,895
#         person rows, **18,155 appointments in 90 days — 202 a day, ~74,000 a
#         year** from a country of 1.3 million people, 86% of them `JUHL` at
#         one-person `OÜ` micro-companies. So there is a threshold, drawn on the
#         Commission's own boundary (Recommendation 2003/361: small is under
#         50 employees) using the annual reports' own
#         `AverageNumberOfEmployeesInFullTimeEquivalentUnits`:
#           10+ 5,449 companies / 808 a year   50+ **825 / 235**
#           25+ 1,878 / 384                    100+ 368 / 119
#                                              250+ 107 / **38**
#         250 — the line the UK and Czech connectors draw — was tried FIRST and
#         refused with that number: under one appointment a week means most
#         weekly runs store nothing, and a collector returning zero is
#         `degraded` by this repo's own rule. Measured at 50 over
#         2026-05-01..07-30: **66 appointments in 91 days, ~5 a week**.
#
#     NEITHER CZ NOR EE IS IN `MARKETS` BELOW, and there are two reasons rather
#     than an oversight. First, the same one that keeps Japan and Korea at
#     `discovery_only`: no run has yet gone through run_collect and stored a
#     row, and a tier is a claim about the connector rather than about the
#     source. Second, and this one is mechanical: **the segment budget is full
#     at 56 of 56.** `build_segments()` spends one slot per market plus one per
#     `terms` entry, and
#     `test_the_segment_matrix_still_sweeps_inside_the_recency_window` requires
#     ceil(segments / 4 / 2) <= the derived recency window, which is 7 days at
#     51 locales. Two more markets make the sweep 8 days and the guard refuses
#     it. Room comes from widening the locale rotation — which means a verified
#     language pack, not a translation — and NOT from raising
#     SEGMENTS_PER_RUN. Both countries are on the sources page with a live
#     collector behind them, which is where coverage is claimed truthfully
#     today; promoting them is one commit once a real run has landed and the
#     budget has room.
#
# --- THE 2026-07-31 REGISTRY SWEEP: fourteen more, one built ----------------
#
# The 2026-07-30 triage above stopped at ten candidates. This one asked a
# narrower question of fourteen MORE national registries — does it publish a
# DIRECTOR CHANGE as a typed, dated event, free and without a key — and every
# line below was fetched live on 2026-07-30 rather than read about. The point
# of the question: leadership is the one pillar of this tracker that has a
# filing regime behind it worldwide, the way WARN and the ERM sit behind the
# sibling's layoffs, so every country that types the event is a country whose
# leadership rows cost nothing.
#
# THE TEST THAT DECIDES IT, and it is not "does the endpoint answer 200".
# It is: does the source STATE the event, or would we have to infer it by
# diffing two snapshots? Diffing is refused here (Korea's roster endpoints,
# Estonia's daily file), because a date the source never stated is a figure we
# invented. That single question sorts all fourteen.
#
#   BUILT
#     ES  BORME Section A, the bulletin every Spanish commercial register
#         publishes its inscribed acts in. collectors/spain_borme.py. Keyless,
#         daily, no model, and it STATES BOTH DIRECTIONS — only the second
#         source here that reports a departure at all, after czechia_ares.
#         The act heading is the register's own fixed word (Nombramientos,
#         Ceses/Dimisiones, Revocaciones, Reelecciones) and the office is a
#         fixed abbreviation, so this is the same class of machine-readable
#         label as Item 5.02 and SEBI Regulation 30.
#         MEASURED live 2026-07-22..07-30 over 213 province files: 15,642
#         company entries, **340 consejero delegado acts — 141 arrivals, 199
#         departures — about 49 a publication day, ~12,700 a year, at 209
#         distinct employers a week.**
#         THE MATERIALITY PROBLEM AND WHAT WAS DONE ABOUT IT. Spain publishes
#         NO headcount anywhere in this bulletin, and the accounts that would
#         are deposited with the Colegio de Registradores and sold. So the
#         threshold that works for the UK (the pay-gap duty), Czechia (the RES
#         band) and Estonia (the annual report's FTE figure) has no equivalent,
#         and every leadership act in BORME is **123,455 rows a year** — the
#         Companies House failure a third time. The filter is therefore the
#         OFFICE, drawn where Japan and Korea are drawn: the **consejero
#         delegado**, the director the board delegated its powers to under LSC
#         article 249. Widening to Presidente and Consejero is one entry in
#         `spain_borme.OFFICES` and eight times the volume; it was declined
#         with that number rather than left unconsidered.
#         THREE TRAPS, all found by running it rather than by reading the spec:
#           * **A board renewal is inscribed as a total cancellation followed
#             by a total re-appointment.** 46 of 373 person-company-date keys
#             carry the SAME office in BOTH directions and nobody left; 92 of
#             432 candidate rows were halves of such a pair. Storing them
#             reports a leaving rate that is not real — the Czech `datumVymazu`
#             finding in a new shape. And collapsing on the PERSON alone is
#             wrong in the other direction: SPLA SA ceased one man as
#             `Con.Delegado` and appointed him `Cons.Del.Sol` on one date, and
#             a sole delegation becoming a joint one is a change the register
#             made. The collapse keys on the office.
#           * **The document is `txt.php`, not `xml.php`.** The XML is the same
#             text, cleaner, and `boe.es/robots.txt` disallows
#             `/diario_borme/xml.php?` in as many words. The open-data API is
#             not a way round it either: it serves SUMMARIES only, and
#             `/datosabiertos/api/borme/id/{ident}` 404s.
#           * **The date is the inscription date and its year has two digits.**
#             `(03.02.97)` read as 2000+97 is the year 2097. The pivot is the
#             publication date it must precede. The bulletin publishes about a
#             week after inscription (median 7 days, p90 8, p99 11 over 7,281
#             entries), so a Spanish row is a week old by construction and the
#             sources page says so.
#         The last item of every day's Section A is `ÍNDICE ALFABÉTICO DE
#         SOCIEDADES`, not a province, and it parses to zero company entries.
#
#   REAL, MEASURED, AND NOT BUILT — the costed roadmap, best first
#     NO  Brønnøysund (Enhetsregisteret). data.brreg.no, keyless, no robots.txt,
#         and the only registry found with a **role-level change feed**:
#         `/oppdateringer/roller?afterTime=` returns CloudEvents saying the
#         roles of one company changed at one instant. MEASURED 2026-07-29:
#         **1,338 role updates at 1,322 distinct companies in one day.** The
#         register also carries `antallAnsatte` — a real employee count, 21,393
#         on Equinor — which looked like the one candidate handing over a
#         materiality filter for free. **It was sampled rather than assumed,
#         and the sample is why Norway is second and not first: on 147 of the
#         1,322 companies that changed a role that day, `antallAnsatte` is
#         UNSTATED on 127 (86%)**, with 9 at 10-49, 6 under 10, 4 at 50-249 and
#         1 at 250+. So the filter exists and covers a seventh of the feed;
#         projecting the observed 3.4% at 50 staff or more gives about 45 a day
#         and ~11,000 a year, and the 86% is a recall hole of exactly the Czech
#         `000 Neuvedeno` kind (65% there) rather than a precision one.
#         WHY IT IS NOT BUILT: `/enheter/{orgnr}/roller` is a CURRENT ROSTER.
#         `sistEndret` sits on the role GROUP rather than the person, an
#         individual role carries no date at all, and there are no end dates —
#         `avregistrert` is a boolean. So the feed says WHICH company changed
#         and the roster says who is there NOW, and recovering who arrived or
#         left means diffing two snapshots, which is refused. What Norway CAN
#         honestly yield is the EDINET shape: "the board or management of this
#         employer changed on this date", no person and no direction, filtered
#         on `antallAnsatte`. That is a real signal and a day's work; it is
#         second on this list rather than first because a row with no person in
#         it is worth less than a Spanish row with two.
#         Two dead ends recorded: `data.brreg.no/kunngjoring/api/` 302s to the
#         open-data landing page and `w2.brreg.no/kunngjoring/api/` 404s, so
#         there is no announcement API to read instead; and
#         `/oppdateringer/roller` rejects `dato` and `oppdateringsid` — the
#         parameter is `afterTime`, which is not guessable from the enheter
#         feed's own `dato`.
#     FR  BODACC, through the DILA open-data portal. Keyless, no key, 8,449,509
#         annonces, and **24,905 `modification` annonces mentioning
#         administration in July 2026 alone** — about 300,000 a year, the
#         largest free European feed found.
#         WHY IT IS NOT BUILT: the change is not typed at the person. The whole
#         of `modificationsgenerales` on such an annonce is the sentence
#         `Modification survenue sur l'administration.` — no name, no office, no
#         direction — and the `administration` field beside it is the roster
#         AFTER the change, so telling an arrival from a continuation means
#         diffing against the previous annonce for that company. Same refusal
#         as Norway, with less to fall back on: France states no employee
#         count either, only `capital` and `formeJuridique`, and legal form as
#         a materiality proxy has already been measured and refused twice
#         (UK 6.35%, Czechia 8.6%).
#     LV  Uzņēmumu reģistrs `officers.csv` on data.gov.lv. **CC0-1.0**, keyless,
#         4.2MB, 32,730 rows, and it carries `registered_on` per officer with
#         `position` and `governing_body` from a fixed vocabulary. Appointments
#         are therefore STATED. It is the Estonian shape and inherits the
#         Estonian limitation — no end-date column, so departures cannot be
#         reported — over a country of 1.9 million with no published employee
#         figure to threshold on. Cheap to build, thin to run.
#     SK  Register právnických osôb, api.statistics.sk/rpo/v1. Keyless,
#         `statutoryBodies` with a typed `statutoryBodyMember` code and a
#         `validFrom` per person. But no `validTo` on any live member, and
#         **no change feed**: `/rpo/v1/search` accepts an identifier or a name
#         and refuses `dbModificationDateFrom`, `modificationDateFrom` and
#         `dbModificationDate` alike, and `/rpo/v1/changes` 404s. Without a
#         change feed the population is the whole register, one entity at a
#         time. Refused on cost rather than on shape.
#     BR  Receita Federal's CNPJ open data carries `Sócios` with a
#         `data_entrada_sociedade` and typed qualification codes including
#         Diretor and Presidente. Monthly bulk, several GB, entry dates only,
#         no exits, no headcount. Not fetched beyond the index — the 2026-07
#         directory 404s at the path tried and the real one was not chased,
#         which is stated here rather than dressed up as a finding.
#
#   REFUSED, WITH THE REASON
#     FI  PRH open data, `avoindata.prh.fi/opendata-ytj-api/v3/companies`.
#         Answers 200 keyless and its response has no officer field at all:
#         the keys are businessId, euId, names, mainBusinessLine, website,
#         companyForms, companySituations, registeredEntries, addresses,
#         tradeRegisterStatus, status, registrationDate, lastModified. PRH
#         sells company representatives as a separate product. Closed.
#     DK  CVR. The distribution service (`distribution.virk.dk/cvr-permanent`)
#         is the one registry found that states BOTH a start and an end date per
#         participant AND publishes employee bands, which would make it the
#         best source on this whole list. It answers **HTTP 401** — access is
#         free but needs credentials the Erhvervsstyrelsen issues on request.
#         NEEDS-OWNER, and it is the single highest-value ask on this page.
#     CH  Zefix (`zefix.admin.ch/ZefixPublicREST`) and the Swiss Official
#         Gazette API (`shab.ch/api/v1/publications`) both answer **401**.
#         NEEDS-OWNER.
#     IE  CRO web services answer `401 Access denied. Invalid API credentials`;
#         GR  the businessportal.gr open-data API answers `401 No API key found`;
#         NL  api.kvk.nl the same and its officer data is a paid product.
#         All three NEEDS-OWNER, and none is worth an ask before Denmark.
#     PL  KRS `api-krs.ms.gov.pl` answers per-company only (204 or 404 on a
#         number that is not in the requested register) with no change feed, so
#         the population would be the whole of KRS one company at a time.
#     BE  KBO open data publishes no natural persons at all.
#     NZ  the NZBN API needs a registered key; `api.business.govt.nz` returned
#         an HTML 404 to an unauthenticated read.
#
#   WHAT THE SWEEP DID NOT ANSWER, said plainly
#     * Brazil's bulk file was not downloaded, so its volume is an inference
#       from its documented schema and not a measurement.
#     * No terms-of-use page was read for Norway, Latvia or Slovakia. Latvia's
#       licence is stated CC0-1.0 by data.gov.lv itself; the other two are
#       unchecked, and Australia is on this page precisely because an endpoint
#       answering 200 is not permission.
#     * Spain's own reuse terms were read only as far as the BOE's open-data
#       page, which states that the API exists "to facilitate access, download
#       and reuse". Spain's general reuse regime (Ley 37/2007 and RD 1495/2011)
#       was not read line by line. robots.txt WAS, in full, and it is what moved
#       the collector off xml.php.
##
#   BLOCKED — do not retry without the owner doing something first
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
#     alone), and the United Kingdom — which is now built, and is the one
#     jurisdiction where the taxonomy was never the constraint: every UK
#     officer appointment is typed, and the constraint was VOLUME. See the
#     GB paragraph above.

MARKETS = (
    Market("IE", "Ireland", DISCOVERY_ONLY,
           live_sources=("google_news",),
           candidate_official_sources=("IDA Ireland press releases",),
           terms=("IDA Ireland", "jobs announcement Dublin")),
    # Promoted 2026-07-30, and it should have been promoted earlier: uk_paygap
    # has been a working GB structured connector with a health check and a
    # passing test since 2026-07-28 and was never listed here, so the tier
    # understated the country while the country chart was dominated by it.
    # companies_house is what makes the promotion unarguable — a second
    # structured source, on a different pillar, from a different statutory
    # duty. It also fixes the concentration: 4,761 of the 4,793 GB rows came
    # from the pay-gap return alone.
    Market("GB", "United Kingdom", STRUCTURED_OFFICIAL,
           live_sources=("google_news", "uk_paygap", "companies_house"),
           candidate_official_sources=("RNS regulatory news",
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
    # Korea was NOT in this list until 2026-07-29, which is a gap rather than a
    # decision: ("ko", "KR") has been in GOOGLE_NEWS_LOCALES with its own query
    # pack the whole time, and the catalogue carries five Korean publisher
    # feeds, so the country was being swept while the coverage manifest said
    # nothing about it at all.
    #
    # It stays discovery_only even though collectors/opendart_korea.py exists
    # and its leadership items are MEASURED (261 allowlisted filings over
    # 2026-05-01..2026-07-29, read from DART's own public search). The
    # measurement is of the source; what is unproven is the CONNECTOR, because
    # no authenticated OpenDART call has ever been made from this repo. A tier
    # here is a public claim, and "coverage is earned" means the connector has
    # run, not that the filings are known to exist. Promotion is one commit
    # after the first real run: add "opendart_korea" to live_sources and move
    # the status, in the same change that records what the run returned.
    Market("KR", "South Korea", DISCOVERY_ONLY,
           live_sources=("google_news",),
           candidate_official_sources=(
               "DART disclosures (Korea, FSS OpenDART) — connector built, "
               "awaiting its first authenticated run",),
           terms=("hiring in Seoul", "서울 사무소", "경력 채용")),
    # --- 2026-07-29 widening, twelve markets ---------------------------------
    #
    # READ THIS BEFORE ADDING THE THIRTEENTH. What follows is what MARKETS
    # actually controls, traced through the code rather than assumed, because two
    # widely-believed things about it are false.
    #
    # It controls the PUBLIC COVERAGE CLAIM and, today, nothing else.
    # `coverage_manifest()` renders straight onto the sources page and into
    # `ops_status [3]`. That is the whole of its live effect, which is why these
    # twelve cost exactly $0 and add exactly zero candidates:
    #
    #   * MARKETS does NOT drive the Google News locale rotation.
    #     `GOOGLE_NEWS_LOCALES` is an independent tuple and `build_locales` reads
    #     only it. Every country below has been in that rotation for days — some
    #     since 2026-07-28 — and has been swept twice a day while the coverage
    #     manifest said nothing about it at all. This is the same gap Korea had:
    #     ("ko","KR") swept for its whole life with KR absent from this list.
    #   * MARKETS does NOT drive the prefilter's geography gate either. The
    #     comment above `prefilter._geography_terms` claimed it grew with this
    #     tuple; the function reads `vocab.COUNTRY_NAMES`, `vocab._CITY_ALIASES`
    #     and a hardcoded short-code list, and never touches MARKETS. That
    #     comment has been corrected.
    #   * `build_segments()` DOES read this tuple, and `build_queries()` puts its
    #     output in the query list for every source that is not gdelt,
    #     google_news or tripwire_chase — which is every STRUCTURED source, and
    #     each one of those accepts `queries` and ignores it (`national_press`
    #     says so in its docstring; the SEC pair search by form and item; a
    #     derived source has no search vocabulary at all). So a segment added here
    #     reaches no fetch today. It still costs, and the cost is the SWEEP
    #     BUDGET below.
    #
    # THE SEGMENT BUDGET IS THE BINDING CONSTRAINT, and it is 56.
    # `test_the_segment_matrix_still_sweeps_inside_the_recency_window` requires
    # ceil(segments / SEGMENTS_PER_RUN / RUNS_PER_DAY) <= the derived recency
    # window: 4 x 2 x 7d = 56 segments. Each market contributes its name plus one
    # per `terms` entry. The fifteen above spend 44, so twelve name-only markets
    # spend the remaining twelve exactly. **That is why none of these carries
    # `terms`** — one term pack of three would cost four slots and buy one
    # market instead of four. The ceiling rises only when the locale rotation
    # grows enough to widen the derived window (72 at 71 locales).
    #
    # ADDING A THIRTEENTH therefore means one of: giving a market local-language
    # `terms` and dropping three others; or widening the locale rotation. It does
    # NOT mean raising SEGMENTS_PER_RUN, which would relax a guard that exists
    # because queries once asked `when:3d` while the matrix took 6.2 days.
    #
    # HOW THESE TWELVE WERE CHOSEN, from data/recall_worklist.json:
    # they are the countries the sealed gold set scored us ZERO on that already
    # have a Google News edition in the rotation and at least two wired publisher
    # feeds in the catalogue. Both conditions matter. Without an edition, a
    # discovery_only market cannot honestly say `live_sources=("google_news",)` —
    # which is what the tier test requires — and adding an edition means adding a
    # LANGUAGE PACK, which is a live-verified measurement and not a translation.
    # That is what excludes China (7 feeds, no edition), Norway (5, none) and
    # Finland (4, none): there is no `zh`, `no` or `fi` pack, and inventing one
    # unverified is the silent-zero failure this file opens by describing.
    # Saudi Arabia is excluded on the second condition — ONE wired feed, which is
    # the single point of failure the catalogue refuses elsewhere. Its ar:SA
    # edition keeps sweeping; it is simply not claimed.
    #
    # Every one starts at discovery_only. Coverage is earned, and what these
    # twelve earn is the right to be LISTED for the sweep that was already
    # happening — not a connector, and not a promotion.
    Market("BR", "Brazil", DISCOVERY_ONLY, live_sources=("google_news",)),
    Market("ES", "Spain", DISCOVERY_ONLY, live_sources=("google_news",)),
    Market("IT", "Italy", DISCOVERY_ONLY, live_sources=("google_news",)),
    Market("MX", "Mexico", DISCOVERY_ONLY, live_sources=("google_news",)),
    Market("AR", "Argentina", DISCOVERY_ONLY, live_sources=("google_news",)),
    Market("CO", "Colombia", DISCOVERY_ONLY, live_sources=("google_news",)),
    Market("PT", "Portugal", DISCOVERY_ONLY, live_sources=("google_news",)),
    Market("CH", "Switzerland", DISCOVERY_ONLY, live_sources=("google_news",)),
    Market("SE", "Sweden", DISCOVERY_ONLY, live_sources=("google_news",)),
    Market("AE", "United Arab Emirates", DISCOVERY_ONLY,
           live_sources=("google_news",)),
    Market("ZA", "South Africa", DISCOVERY_ONLY, live_sources=("google_news",)),
    Market("NZ", "New Zealand", DISCOVERY_ONLY, live_sources=("google_news",)),
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
