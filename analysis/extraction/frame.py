"""The frame the extraction benchmark is drawn from, and the strata it is drawn on.

WHY A SEPARATE MODULE. The draw and the grading are two different runs, months
apart if the sample is re-used, and the second one must be able to say exactly
what the first one did. Everything that decides which rows are eligible and
which cell each one falls in lives here, is pure, offline, and is pinned by
`tests/test_extraction_benchmark.py` — so a later session cannot quietly change
the population and go on comparing the numbers.

WHAT THE FRAME IS. Current signals produced by the LLM EXTRACTION path. The
structured collectors (`bse_india`, `uk_paygap`, `sec_execcomp`,
`companies_house`, `sec_form_d_bulk` ...) parse a filing deterministically and
never ask a model what the company or the amount is, so grading them would
measure a parser under a heading that says "extraction". They are excluded by
name rather than by a guess, and `NEWS_COLLECTORS` is asserted against the
collectors that actually call `classify.classify`.

THE STRATA, and the honest limits of each:

* **region** — `US` / `Europe` / `RoW`, from the row's OWN stored country, and
  where that is null from the publisher's country-code TLD. That is a PROXY:
  the stored country is one of the fields under measurement, so a row filed in
  the wrong country is in the wrong cell. It cannot be otherwise — the frame
  has to be strataed before anything is read — and the effect is reported
  rather than hidden: the graded country field is where a systematic error in
  this proxy would show up.
* **event type** — `funding`, `hiring`, `leadership`, `pay`,
  `working_practices`, a partition of the four production pillars with
  `company_development` split by whether a funding figure was extracted. These
  are the reader-facing event types; a partition, so every frame row lands in
  exactly one.
* **language** — a PROXY again, and a coarse one: a non-Latin headline script,
  or a publisher whose country is not predominantly English-speaking. The
  actual language of each body comes back FROM THE REFEREES, which read it, and
  the report states the measured mix beside the proxy mix. Do not quote the
  proxy as the language finding.
"""

from __future__ import annotations

from urllib.parse import urlparse

#: Collectors whose rows were built by the LLM extraction path. Everything else
#: in `signals` came off a filing or a register through a deterministic parser.
NEWS_COLLECTORS = (
    "google_news",
    "gdelt",
    "national_press",
    "press_archive",
    "primary_chase",
    "tripwire_chase",
)

#: Collectors that build a row from a filing or a register with a deterministic
#: parser and never ask a model what the company or the amount is. Listed, not
#: inferred, and `tests/test_extraction_benchmark.py` fails when a collector in
#: the corpus appears in NEITHER tuple — so a new source forces somebody to say
#: which side of the measurement it belongs on, instead of silently dropping
#: out of the frame.
STRUCTURED_COLLECTORS = (
    "ats_boards",
    "bse_india",
    "companies_house",
    "czechia_ares",
    "denmark_cvr",
    "edinet_japan",
    "estonia_ariregister",
    "irs_form_990",
    "opendart_korea",
    "sec_edgar",
    "sec_execcomp",
    "sec_form_d",
    "sec_form_d_bulk",
    "spain_borme",
    "uk_paygap",
    "us_exec_wire",
)

#: ISO2 codes counted as Europe for the region stratum. Geographic Europe plus
#: Turkey, which the tracker's own market registry treats as a European hiring
#: market. Deliberately a flat set: a region stratum is a sampling convenience,
#: not a political statement, and nothing downstream reads it.
EUROPE = frozenset({
    "AD", "AL", "AT", "BA", "BE", "BG", "BY", "CH", "CY", "CZ", "DE", "DK",
    "EE", "ES", "FI", "FO", "FR", "GB", "GE", "GI", "GR", "HR", "HU", "IE",
    "IS", "IT", "LI", "LT", "LU", "LV", "MC", "MD", "ME", "MK", "MT", "NL",
    "NO", "PL", "PT", "RO", "RS", "RU", "SE", "SI", "SK", "SM", "TR", "UA",
    "VA", "XK",
})

#: Country-code TLD -> ISO2, for a row whose own country is null. Only the
#: codes that actually appear in this corpus; an unknown TLD leaves the region
#: as RoW rather than guessing.
TLD_COUNTRY = {
    "ae": "AE", "ar": "AR", "at": "AT", "au": "AU", "be": "BE", "br": "BR",
    "ca": "CA", "ch": "CH", "cl": "CL", "cn": "CN", "co": "CO", "cz": "CZ",
    "de": "DE", "dk": "DK", "eg": "EG", "es": "ES", "fi": "FI", "fr": "FR",
    "gr": "GR", "hu": "HU", "id": "ID", "ie": "IE", "il": "IL", "in": "IN",
    "it": "IT", "jp": "JP", "ke": "KE", "kr": "KR", "ma": "MA", "mx": "MX",
    "my": "MY", "ng": "NG", "nl": "NL", "no": "NO", "nz": "NZ", "pe": "PE",
    "ph": "PH", "pl": "PL", "pt": "PT", "ro": "RO", "ru": "RU", "sa": "SA",
    "se": "SE", "sg": "SG", "th": "TH", "tr": "TR", "ua": "UA", "uk": "GB",
    "us": "US", "vn": "VN", "za": "ZA",
}

#: A generic TLD says nothing about where the publisher is, so it says nothing
#: about the region either. It is NOT read as "American": that assumption is
#: how a worldwide corpus turns into a US measurement with nobody noticing.
GENERIC_TLDS = frozenset({"com", "org", "net", "io", "co", "info", "news", "biz"})

#: Countries where the press this tracker reads publishes predominantly in
#: English. Used ONLY for the language PROXY in the draw.
ANGLOPHONE = frozenset({"US", "GB", "IE", "AU", "NZ", "CA", "IN", "ZA", "SG", "PH", "NG", "KE"})

REGIONS = ("US", "Europe", "RoW")
EVENT_TYPES = ("funding", "hiring", "leadership", "pay", "working_practices")

#: The code-point ranges a Latin-script headline stays inside: Basic Latin
#: and Latin-1, Latin Extended-A and -B, general punctuation and currency
#: symbols. Greek and Cyrillic are non-Latin here and that is intended.
_LATIN_RANGES = ((0x0000, 0x024F), (0x2000, 0x206F), (0x20A0, 0x20BF))

def host_country(source_url: str) -> str | None:
    """ISO2 from a publisher's country-code TLD, or None for a generic one."""
    host = (urlparse(source_url or "").hostname or "").lower()
    if not host:
        return None
    tld = host.rsplit(".", 1)[-1]
    if tld in GENERIC_TLDS:
        return None
    return TLD_COUNTRY.get(tld)

def region_country(row: dict) -> str | None:
    """The country the region stratum is read off: the row's own, else the TLD's."""
    return (row.get("country") or None) or host_country(row.get("source_url") or "")

def region_of(row: dict) -> str:
    country = region_country(row)
    if country == "US":
        return "US"
    if country in EUROPE:
        return "Europe"
    return "RoW"

def event_type_of(row: dict) -> str:
    """One of EVENT_TYPES. A partition of the production pillars.

    `company_development` is split on whether extraction found a funding figure,
    because "funding" and "a company doing something corporate" are two very
    different reads and the benchmark has to be able to report them apart.
    """
    pillar = (row.get("pillar") or "").strip()
    if pillar == "leadership_change":
        return "leadership"
    if pillar == "rewards_comp":
        return "pay"
    if pillar == "how_we_work":
        return "working_practices"
    if pillar == "company_development":
        return "funding" if row.get("funding_amount_usd") else "hiring"
    return "hiring"

def non_latin_headline(row: dict) -> bool:
    """True when the headline uses a script outside `_LATIN_RANGES`.

    Character by character rather than by regex, because the ranges have to be
    written as numbers: an escape sequence in a pattern string is one editor
    setting away from becoming the byte it names.
    """
    return any(
        not any(low <= ord(ch) <= high for low, high in _LATIN_RANGES)
        for ch in (row.get("headline") or "")
    )

def likely_non_english(row: dict) -> bool:
    """The language PROXY. Never quote it as the language of anything.

    True when the headline is not in Latin script, or when the publisher and
    the row both sit outside the predominantly English-speaking countries.
    """
    if non_latin_headline(row):
        return True
    country = region_country(row)
    return bool(country) and country not in ANGLOPHONE

def cell_of(row: dict) -> tuple[str, str]:
    return region_of(row), event_type_of(row)

def eligible(row: dict) -> bool:
    """A frame row: current, from the extraction path, and with a source to read.

    `is_current` is applied by the query, not here — this is the per-row half,
    so a test can hand it one dict.
    """
    return bool(
        (row.get("collector") or "") in NEWS_COLLECTORS
        and (row.get("source_url") or "").startswith("http")
        and (row.get("content_hash") or "")
    )

def allocate(cell_sizes: dict[tuple[str, str], int], target: int,
             min_cell: int = 5) -> dict[tuple[str, str], int]:
    """How many rows each cell contributes to a sample of `target`.

    Proportional to the frame, with a floor of `min_cell` wherever the frame can
    supply it, and the remainder settled by largest fractional part so the total
    is exactly `target` and the answer does not depend on dict order.

    THE FLOOR IS THE POINT. Purely proportional allocation puts 2 rows in
    `pay` and then reports a pay accuracy of 50% or 100%, which is not a
    measurement. The floor buys every cell an interval that is at least
    readable, and it is why the per-cell counts are published beside the rates.
    """
    total = sum(cell_sizes.values())
    if total <= 0 or target <= 0:
        return {cell: 0 for cell in cell_sizes}
    if target >= total:
        return dict(cell_sizes)

    take = {cell: min(size, min_cell) for cell, size in cell_sizes.items() if size}
    left = target - sum(take.values())
    if left <= 0:
        # The floors alone overshoot: give every non-empty cell an equal share,
        # largest cells breaking the tie, and never more than the cell holds.
        take = {cell: 0 for cell in cell_sizes}
        order = sorted((c for c, s in cell_sizes.items() if s),
                       key=lambda c: (-cell_sizes[c], c))
        while sum(take.values()) < target and any(
                take[c] < cell_sizes[c] for c in order):
            for cell in order:
                if take[cell] < cell_sizes[cell] and sum(take.values()) < target:
                    take[cell] += 1
        return take

    headroom = {cell: cell_sizes[cell] - take.get(cell, 0) for cell in cell_sizes}
    pool = sum(headroom.values())
    shares = {cell: (left * headroom[cell] / pool if pool else 0.0) for cell in cell_sizes}
    whole = {cell: min(int(shares[cell]), headroom[cell]) for cell in cell_sizes}
    take = {cell: take.get(cell, 0) + whole[cell] for cell in cell_sizes}

    remainder = target - sum(take.values())
    order = sorted(cell_sizes, key=lambda c: (-(shares[c] - int(shares[c])), -cell_sizes[c], c))
    index = 0
    while remainder > 0 and any(take[c] < cell_sizes[c] for c in cell_sizes):
        cell = order[index % len(order)]
        if take[cell] < cell_sizes[cell]:
            take[cell] += 1
            remainder -= 1
        index += 1
    return take
