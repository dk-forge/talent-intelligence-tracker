"""Fixed vocabularies.

Nothing the model freely types is ever stored. Every categorical field on a
signal is normalised through one of these closed lists first, and a value that
will not normalise is a rejected record, not a new category.

Spec 6.3: "Bay Area" / "SF" / "San Francisco" must not be three cities.
"""

from __future__ import annotations

import re

# --- Pillars (spec 1) ------------------------------------------------------

PILLARS = (
    "company_development",   # acquisitions, expansions, new sites
    "leadership_change",     # exec hires and departures
    "rewards_comp",          # comp actions, retention awards, pay transparency
    "how_we_work",           # RTO policy, hub investment, distributed work
)

PILLAR_LABELS = {
    "company_development": "Company developments & M&A",
    "leadership_change": "Leadership changes",
    "rewards_comp": "Rewards & compensation",
    "how_we_work": "How we work & location strategy",
}

# --- Signal direction ------------------------------------------------------

SIGNAL_DIRECTIONS = ("hiring", "displacement", "neutral", "comp_shift")

# `displacement` exists because a hiring-side source can still report a
# displacement read-through (an acquisition implying integration redundancies).
# It never means we collected a layoff — layoffs are read from the sibling
# tracker's API and never stored here (spec 17).

# --- Confidence (spec 2 rule 3) -------------------------------------------

CONFIDENCE_TIERS = ("verified", "reported", "rumored")

# verified  = primary source (regulatory filing, company IR, official agency)
# reported  = credible outlet reporting it as fact
# rumored   = outlet reports it as unconfirmed, "people familiar", or similar
#
# These are never silently promoted. A row enters at the tier its source earns.

PRIMARY_SOURCE_DOMAINS = frozenset({
    "sec.gov",
    "efts.sec.gov",
    "www.sec.gov",
    "find-and-update.company-information.service.gov.uk",
    "idaireland.com",
    "www.idaireland.com",
    "investni.com",
    "www.investni.com",
    "businessfrance.fr",
    "www.businessfrance.fr",
    "gtai.de",
    "www.gtai.de",
})

# --- Functions ("roles hiring for") ----------------------------------------
#
# The single most useful filter a recruiter has: not "which company", but
# "which function". The model already names these in prose ("finance, IT, HR
# and shared-service roles") — storing them as a closed list is what turns that
# sentence into something filterable.
#
# Closed on purpose. A signal whose function will not normalise stores an empty
# list rather than inventing a category.

FUNCTIONS = (
    "engineering",
    "data_ai",
    "it_infrastructure",
    "product",
    "design",
    "finance",
    "hr_people",
    "sales",
    "marketing",
    "customer_support",
    "operations",
    "supply_chain",
    "manufacturing",
    "legal_compliance",
    "research",
    "clinical_healthcare",
    "executive",
)

FUNCTION_LABELS = {
    "engineering": "Engineering",
    "data_ai": "Data & AI",
    "it_infrastructure": "IT & infrastructure",
    "product": "Product",
    "design": "Design",
    "finance": "Finance",
    "hr_people": "HR & people",
    "sales": "Sales",
    "marketing": "Marketing",
    "customer_support": "Customer support",
    "operations": "Operations",
    "supply_chain": "Supply chain",
    "manufacturing": "Manufacturing",
    "legal_compliance": "Legal & compliance",
    "research": "Research",
    "clinical_healthcare": "Clinical & healthcare",
    "executive": "Executive",
}

_FUNCTION_ALIASES = {
    "software": "engineering", "software engineering": "engineering",
    "developers": "engineering", "development": "engineering", "tech": "engineering",
    "technology": "engineering", "r&d engineering": "engineering",
    "data": "data_ai", "data science": "data_ai", "analytics": "data_ai",
    "machine learning": "data_ai", "ai": "data_ai", "artificial intelligence": "data_ai",
    "it": "it_infrastructure", "infrastructure": "it_infrastructure",
    "devops": "it_infrastructure", "cloud": "it_infrastructure",
    "cybersecurity": "it_infrastructure", "security": "it_infrastructure",
    "network": "it_infrastructure", "networking": "it_infrastructure",
    "product management": "product", "ux": "design", "ui": "design",
    "accounting": "finance", "accountancy": "finance", "audit": "finance",
    "treasury": "finance", "fp&a": "finance",
    "hr": "hr_people", "human resources": "hr_people", "people": "hr_people",
    "talent acquisition": "hr_people", "recruiting": "hr_people",
    "recruitment": "hr_people", "payroll": "hr_people",
    "business development": "sales", "account management": "sales",
    "commercial": "sales", "communications": "marketing", "brand": "marketing",
    "customer service": "customer_support", "support": "customer_support",
    "call centre": "customer_support", "call center": "customer_support",
    "shared services": "operations", "back office": "operations",
    "back-office": "operations", "business services": "operations",
    "administration": "operations", "admin": "operations",
    "logistics": "supply_chain", "procurement": "supply_chain",
    "warehouse": "supply_chain", "production": "manufacturing",
    "factory": "manufacturing", "plant": "manufacturing",
    "legal": "legal_compliance", "compliance": "legal_compliance",
    "regulatory": "legal_compliance", "risk": "legal_compliance",
    "r&d": "research", "biotech": "research", "scientific": "research",
    "clinical": "clinical_healthcare", "nursing": "clinical_healthcare",
    "medical": "clinical_healthcare", "care": "clinical_healthcare",
    "leadership": "executive", "management": "executive", "c-suite": "executive",
    # Job titles, not departments. The model is asked for the closed list
    # directly; these exist so a stray title still lands somewhere sensible.
    "software engineer": "engineering", "developer": "engineering",
    "data analyst": "data_ai", "data scientist": "data_ai",
    "cloud architect": "it_infrastructure", "devops engineer": "it_infrastructure",
    "network engineer": "it_infrastructure", "accountant": "finance",
    "recruiter": "hr_people", "nurse": "clinical_healthcare",
    "shared service": "operations", "business process": "operations",
    "back office professional": "operations",
}


def normalize_function(value: str):
    k = _key(value)
    if not k:
        return None
    # Strip the noise words a model wraps around a function name, then try the
    # singular too: "software engineers" and "data analysts" are titles, not
    # departments, and would otherwise fall through.
    k = re.sub(r"\s+(roles?|jobs?|staff|positions?|professionals?|teams?|talent|specialists?)$", "", k)
    k = k.replace("-", " ").strip()
    for candidate in (k, re.sub(r"s$", "", k)):
        if candidate.replace(" ", "_") in FUNCTIONS:
            return candidate.replace(" ", "_")
        if candidate in _FUNCTION_ALIASES:
            return _FUNCTION_ALIASES[candidate]
    return None


def normalize_functions(values) -> list:
    """Normalise a list, dropping anything unrecognised. Order preserved,
    duplicates removed."""
    if isinstance(values, str):
        values = re.split(r"[,;/]| and ", values)
    out = []
    for v in values or []:
        hit = normalize_function(str(v))
        if hit and hit not in out:
            out.append(hit)
    return out


# --- Industries ------------------------------------------------------------

INDUSTRIES = (
    "technology", "financial_services", "healthcare", "pharma_biotech",
    "retail_ecommerce", "manufacturing", "energy_utilities", "telecom",
    "media_entertainment", "transport_logistics", "professional_services",
    "public_sector", "hospitality_travel", "education", "food_beverage",
    "automotive", "aerospace_defence", "real_estate_construction",
)

_INDUSTRY_ALIASES = {
    "tech": "technology", "software": "technology", "saas": "technology",
    "semiconductors": "technology", "it services": "technology",
    "finance": "financial_services", "banking": "financial_services",
    "insurance": "financial_services", "fintech": "financial_services",
    "health": "healthcare", "health care": "healthcare",
    "hospitals": "healthcare", "nhs": "healthcare",
    "pharma": "pharma_biotech", "pharmaceutical": "pharma_biotech",
    "pharmaceuticals": "pharma_biotech", "biotech": "pharma_biotech",
    "life sciences": "pharma_biotech",
    "retail": "retail_ecommerce", "ecommerce": "retail_ecommerce",
    "e-commerce": "retail_ecommerce", "consumer goods": "retail_ecommerce",
    "industrial": "manufacturing", "chemicals": "manufacturing",
    "energy": "energy_utilities", "oil and gas": "energy_utilities",
    "utilities": "energy_utilities", "renewables": "energy_utilities",
    "telecoms": "telecom", "telecommunications": "telecom",
    "media": "media_entertainment", "gaming": "media_entertainment",
    "publishing": "media_entertainment",
    "logistics": "transport_logistics", "shipping": "transport_logistics",
    "airline": "transport_logistics", "aviation": "transport_logistics",
    "consulting": "professional_services", "accounting": "professional_services",
    "legal services": "professional_services", "law": "professional_services",
    "government": "public_sector", "civil service": "public_sector",
    "defence": "aerospace_defence", "defense": "aerospace_defence",
    "aerospace": "aerospace_defence",
    "hospitality": "hospitality_travel", "travel": "hospitality_travel",
    "hotels": "hospitality_travel", "food": "food_beverage",
    "beverage": "food_beverage", "brewing": "food_beverage",
    "construction": "real_estate_construction",
    "property": "real_estate_construction", "real estate": "real_estate_construction",
}


def normalize_industry(value: str):
    k = _key(value)
    if not k:
        return None
    if k.replace(" ", "_").replace("-", "_") in INDUSTRIES:
        return k.replace(" ", "_").replace("-", "_")
    return _INDUSTRY_ALIASES.get(k)


# --- US states -------------------------------------------------------------
#
# Only meaningful when the country is US. Stored as the two-letter code so the
# filter is a clean enumeration rather than free text.

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "washington d.c.": "DC",
}

# Cities we already curate that imply a state, so a US signal naming only the
# city still lands in the state filter.
_CITY_STATE = {
    "San Francisco": "CA", "New York": "NY", "Seattle": "WA",
    "Austin": "TX", "Boston": "MA",
}


def normalize_state(value: str):
    """Two-letter US state code, or None. Accepts a name or a code."""
    k = _key(value)
    if not k:
        return None
    if len(k) == 2 and k.upper() in set(US_STATES.values()):
        return k.upper()
    return US_STATES.get(k)


def state_for_city(city: str):
    return _CITY_STATE.get(city or "")


# --- Geography -------------------------------------------------------------
#
# Deliberately small and hand-curated. A city enters this list when we can
# defend covering it, not because a model mentioned it. Grow it on evidence.

_CITY_ALIASES = {
    "san francisco": ("San Francisco", "North America", "US"),
    "sf": ("San Francisco", "North America", "US"),
    "bay area": ("San Francisco", "North America", "US"),
    "silicon valley": ("San Francisco", "North America", "US"),
    "new york": ("New York", "North America", "US"),
    "new york city": ("New York", "North America", "US"),
    "nyc": ("New York", "North America", "US"),
    "seattle": ("Seattle", "North America", "US"),
    "austin": ("Austin", "North America", "US"),
    "boston": ("Boston", "North America", "US"),
    # Canada, not the United States. Every Toronto signal has been filing
    # itself under the US country filter and the US state facet since the list
    # was written; caught while wiring the employer HQ resolver, which reads
    # the country back out of this table.
    "toronto": ("Toronto", "North America", "CA"),
    "london": ("London", "Europe", "GB"),
    "manchester": ("Manchester", "Europe", "GB"),
    "edinburgh": ("Edinburgh", "Europe", "GB"),
    "belfast": ("Belfast", "Europe", "GB"),
    "dublin": ("Dublin", "Europe", "IE"),
    "cork": ("Cork", "Europe", "IE"),
    "galway": ("Galway", "Europe", "IE"),
    "limerick": ("Limerick", "Europe", "IE"),
    "berlin": ("Berlin", "Europe", "DE"),
    "munich": ("Munich", "Europe", "DE"),
    "münchen": ("Munich", "Europe", "DE"),
    "hamburg": ("Hamburg", "Europe", "DE"),
    "frankfurt": ("Frankfurt", "Europe", "DE"),
    "amsterdam": ("Amsterdam", "Europe", "NL"),
    "rotterdam": ("Rotterdam", "Europe", "NL"),
    "eindhoven": ("Eindhoven", "Europe", "NL"),
    "brussels": ("Brussels", "Europe", "BE"),
    "bruxelles": ("Brussels", "Europe", "BE"),
    "antwerp": ("Antwerp", "Europe", "BE"),
    "luxembourg": ("Luxembourg", "Europe", "LU"),
    "paris": ("Paris", "Europe", "FR"),
    "madrid": ("Madrid", "Europe", "ES"),
    "barcelona": ("Barcelona", "Europe", "ES"),
    "lisbon": ("Lisbon", "Europe", "PT"),
    "milan": ("Milan", "Europe", "IT"),
    "stockholm": ("Stockholm", "Europe", "SE"),
    "copenhagen": ("Copenhagen", "Europe", "DK"),
    "oslo": ("Oslo", "Europe", "NO"),
    "helsinki": ("Helsinki", "Europe", "FI"),
    "zurich": ("Zurich", "Europe", "CH"),
    "zürich": ("Zurich", "Europe", "CH"),
    "warsaw": ("Warsaw", "Europe", "PL"),
    "krakow": ("Krakow", "Europe", "PL"),
    "kraków": ("Krakow", "Europe", "PL"),
    "prague": ("Prague", "Europe", "CZ"),
    "bucharest": ("Bucharest", "Europe", "RO"),
    "bangalore": ("Bangalore", "Asia", "IN"),
    "bengaluru": ("Bangalore", "Asia", "IN"),
    "hyderabad": ("Hyderabad", "Asia", "IN"),
    "pune": ("Pune", "Asia", "IN"),
    "singapore": ("Singapore", "Asia", "SG"),
    "tokyo": ("Tokyo", "Asia", "JP"),
    "sydney": ("Sydney", "Oceania", "AU"),
    "melbourne": ("Melbourne", "Oceania", "AU"),
}

# Administrative names, for `pipeline.identity`. A Wikidata headquarters points
# at whatever entity the editors chose — Alphabet's is "Googleplex", a building
# — so the resolver walks the containment chain and takes the first name this
# table knows. That chain speaks in boroughs and counties, and without these
# every London employer whose registered office sits in the City resolves to no
# city at all. Aliases only: no new city is introduced here, so nothing new can
# appear in a filter.
_CITY_ALIASES.update({
    "greater london": ("London", "Europe", "GB"),
    "city of london": ("London", "Europe", "GB"),
    "city of westminster": ("London", "Europe", "GB"),
    "greater manchester": ("Manchester", "Europe", "GB"),
    "city of manchester": ("Manchester", "Europe", "GB"),
    "manhattan": ("New York", "North America", "US"),
    "brooklyn": ("New York", "North America", "US"),
    "new york county": ("New York", "North America", "US"),
    "city and county of san francisco": ("San Francisco", "North America", "US"),
    "praha": ("Prague", "Europe", "CZ"),
    "milano": ("Milan", "Europe", "IT"),
    "lisboa": ("Lisbon", "Europe", "PT"),
    "bengaluru urban": ("Bangalore", "Asia", "IN"),
    "city of toronto": ("Toronto", "North America", "CA"),
})

REGIONS = ("North America", "Europe", "Asia", "Oceania", "Latin America", "Africa", "Middle East")

# ISO2 -> display name, for the countries a city currently maps to.
COUNTRY_NAMES = {
    "AF": "Afghanistan",
    "AL": "Albania",
    "DZ": "Algeria",
    "AD": "Andorra",
    "AO": "Angola",
    "AG": "Antigua and Barbuda",
    "AR": "Argentina",
    "AM": "Armenia",
    "AU": "Australia",
    "AT": "Austria",
    "AZ": "Azerbaijan",
    "BS": "Bahamas",
    "BH": "Bahrain",
    "BD": "Bangladesh",
    "BB": "Barbados",
    "BY": "Belarus",
    "BE": "Belgium",
    "BZ": "Belize",
    "BJ": "Benin",
    "BT": "Bhutan",
    "BO": "Bolivia",
    "BA": "Bosnia and Herzegovina",
    "BW": "Botswana",
    "BR": "Brazil",
    "BN": "Brunei",
    "BG": "Bulgaria",
    "BF": "Burkina Faso",
    "BI": "Burundi",
    "CV": "Cabo Verde",
    "KH": "Cambodia",
    "CM": "Cameroon",
    "CA": "Canada",
    "CF": "Central African Republic",
    "TD": "Chad",
    "CL": "Chile",
    "CN": "China",
    "CO": "Colombia",
    "KM": "Comoros",
    "CG": "Congo",
    "CR": "Costa Rica",
    "CI": "Cote d'Ivoire",
    "HR": "Croatia",
    "CU": "Cuba",
    "CY": "Cyprus",
    "CZ": "Czechia",
    "CD": "DR Congo",
    "DK": "Denmark",
    "DJ": "Djibouti",
    "DM": "Dominica",
    "DO": "Dominican Republic",
    "EC": "Ecuador",
    "EG": "Egypt",
    "SV": "El Salvador",
    "GQ": "Equatorial Guinea",
    "ER": "Eritrea",
    "EE": "Estonia",
    "SZ": "Eswatini",
    "ET": "Ethiopia",
    "FJ": "Fiji",
    "FI": "Finland",
    "FR": "France",
    "GA": "Gabon",
    "GM": "Gambia",
    "GE": "Georgia",
    "DE": "Germany",
    "GH": "Ghana",
    "GR": "Greece",
    "GD": "Grenada",
    "GT": "Guatemala",
    "GN": "Guinea",
    "GW": "Guinea-Bissau",
    "GY": "Guyana",
    "HT": "Haiti",
    "HN": "Honduras",
    "HK": "Hong Kong",
    "HU": "Hungary",
    "IS": "Iceland",
    "IN": "India",
    "ID": "Indonesia",
    "IR": "Iran",
    "IQ": "Iraq",
    "IE": "Ireland",
    "IL": "Israel",
    "IT": "Italy",
    "JM": "Jamaica",
    "JP": "Japan",
    "JO": "Jordan",
    "KZ": "Kazakhstan",
    "KE": "Kenya",
    "KI": "Kiribati",
    "KW": "Kuwait",
    "KG": "Kyrgyzstan",
    "LA": "Laos",
    "LV": "Latvia",
    "LB": "Lebanon",
    "LS": "Lesotho",
    "LR": "Liberia",
    "LY": "Libya",
    "LI": "Liechtenstein",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "MO": "Macao",
    "MG": "Madagascar",
    "MW": "Malawi",
    "MY": "Malaysia",
    "MV": "Maldives",
    "ML": "Mali",
    "MT": "Malta",
    "MH": "Marshall Islands",
    "MR": "Mauritania",
    "MU": "Mauritius",
    "MX": "Mexico",
    "FM": "Micronesia",
    "MD": "Moldova",
    "MC": "Monaco",
    "MN": "Mongolia",
    "ME": "Montenegro",
    "MA": "Morocco",
    "MZ": "Mozambique",
    "MM": "Myanmar",
    "NA": "Namibia",
    "NR": "Nauru",
    "NP": "Nepal",
    "NL": "Netherlands",
    "NZ": "New Zealand",
    "NI": "Nicaragua",
    "NE": "Niger",
    "NG": "Nigeria",
    "KP": "North Korea",
    "MK": "North Macedonia",
    "NO": "Norway",
    "OM": "Oman",
    "PK": "Pakistan",
    "PW": "Palau",
    "PS": "Palestine",
    "PA": "Panama",
    "PG": "Papua New Guinea",
    "PY": "Paraguay",
    "PE": "Peru",
    "PH": "Philippines",
    "PL": "Poland",
    "PT": "Portugal",
    "PR": "Puerto Rico",
    "QA": "Qatar",
    "RO": "Romania",
    "RU": "Russia",
    "RW": "Rwanda",
    "KN": "Saint Kitts and Nevis",
    "LC": "Saint Lucia",
    "VC": "Saint Vincent and the Grenadines",
    "WS": "Samoa",
    "SM": "San Marino",
    "ST": "Sao Tome and Principe",
    "SA": "Saudi Arabia",
    "SN": "Senegal",
    "RS": "Serbia",
    "SC": "Seychelles",
    "SL": "Sierra Leone",
    "SG": "Singapore",
    "SK": "Slovakia",
    "SI": "Slovenia",
    "SB": "Solomon Islands",
    "SO": "Somalia",
    "ZA": "South Africa",
    "KR": "South Korea",
    "SS": "South Sudan",
    "ES": "Spain",
    "LK": "Sri Lanka",
    "SD": "Sudan",
    "SR": "Suriname",
    "SE": "Sweden",
    "CH": "Switzerland",
    "SY": "Syria",
    "TW": "Taiwan",
    "TJ": "Tajikistan",
    "TZ": "Tanzania",
    "TH": "Thailand",
    "TL": "Timor-Leste",
    "TG": "Togo",
    "TO": "Tonga",
    "TT": "Trinidad and Tobago",
    "TN": "Tunisia",
    "TR": "Turkey",
    "TM": "Turkmenistan",
    "TV": "Tuvalu",
    "UG": "Uganda",
    "UA": "Ukraine",
    "AE": "United Arab Emirates",
    "GB": "United Kingdom",
    "US": "United States",
    "UY": "Uruguay",
    "UZ": "Uzbekistan",
    "VU": "Vanuatu",
    "VE": "Venezuela",
    "VN": "Vietnam",
    "YE": "Yemen",
    "ZM": "Zambia",
    "ZW": "Zimbabwe",
}

# Aliases are DERIVED from the names above, plus the variants a newsroom
# actually writes. Deriving them is the point: a hand-maintained alias list is
# how "Philippines" and "Egypt" ended up unmappable while the collector was
# happily querying 25 national editions. Adding a country to COUNTRY_NAMES now
# makes its own name work automatically.
_COUNTRY_ALIASES = {name.lower(): code for code, name in COUNTRY_NAMES.items()}
_COUNTRY_ALIASES.update({'usa': 'US',
    'u.s.': 'US',
    'u.s.a.': 'US',
    'america': 'US',
    'united states of america': 'US',
    'uk': 'GB',
    'britain': 'GB',
    'great britain': 'GB',
    'england': 'GB',
    'scotland': 'GB',
    'wales': 'GB',
    'northern ireland': 'GB',
    'republic of ireland': 'IE',
    'eire': 'IE',
    'deutschland': 'DE',
    'holland': 'NL',
    'the netherlands': 'NL',
    'czech republic': 'CZ',
    'uae': 'AE',
    'emirates': 'AE',
    'ksa': 'SA',
    'south korea': 'KR',
    'republic of korea': 'KR',
    'north korea': 'KP',
    'ivory coast': 'CI',
    'cape verde': 'CV',
    'swaziland': 'SZ',
    'burma': 'MM',
    'east timor': 'TL',
    'macedonia': 'MK',
    'holy see': 'VA',
    'vatican': 'VA',
    'congo-kinshasa': 'CD',
    'democratic republic of the congo': 'CD',
    'congo-brazzaville': 'CG',
    'russia federation': 'RU',
    'russian federation': 'RU',
    'viet nam': 'VN',
    'hong kong sar': 'HK',
    'macau': 'MO',
    'türkiye': 'TR',
    'turkiye': 'TR',
    'republic of china': 'TW', "people's republic of china": 'CN',
    'prc': 'CN',
    'laos pdr': 'LA',
    'brasil': 'BR',
    'espana': 'ES',
    'españa': 'ES',
    'italia': 'IT',
    'mexico city': 'MX',
    'new zealand aotearoa': 'NZ'})


def _key(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def normalize_city(value: str):
    """Return (city, region, iso2) or None. Never invents a city."""
    hit = _CITY_ALIASES.get(_key(value))
    return hit if hit else None


def normalize_country(value: str):
    """Return an ISO2 code or None. Accepts a name, alias, or ISO2."""
    k = _key(value)
    if not k:
        return None
    if k.upper() in COUNTRY_NAMES:
        return k.upper()
    return _COUNTRY_ALIASES.get(k)


def normalize_pillar(value: str):
    k = _key(value).replace(" ", "_").replace("-", "_")
    return k if k in PILLARS else None


def normalize_direction(value: str):
    k = _key(value).replace(" ", "_").replace("-", "_")
    return k if k in SIGNAL_DIRECTIONS else None


def normalize_confidence(value: str):
    k = _key(value)
    return k if k in CONFIDENCE_TIERS else None


def company_key(name: str) -> str:
    """Stable join key for a company. Strips common legal suffixes so
    'Acme Inc.' and 'Acme, Inc' collapse to one employer."""
    k = _key(name)
    k = re.sub(r"[^\w\s&-]", " ", k)
    k = re.sub(
        r"\b(inc|llc|ltd|limited|plc|corp|corporation|co|pbc|lp|llp|gmbh|ag|sa|nv|bv|ab|as|oy|spa|srl|pte|pty)\b",
        " ",
        k,
    )
    return re.sub(r"\s+", " ", k).strip()


# --- Funding stage ---------------------------------------------------------

# The round's name, not its size. Stage is what makes a funding row comparable:
# a $30M seed and a $30M Series D are different talent events (the first is
# about to build a team, the second is buying growth), and without this column
# the only thing a reader can sort by is the number.
FUNDING_STAGES = (
    "pre_seed", "seed", "series_a", "series_b", "series_c", "series_d_plus",
    "growth", "debt", "grant", "ipo", "other",
)

FUNDING_STAGE_LABELS = {
    "pre_seed": "Pre-seed",
    "seed": "Seed",
    "series_a": "Series A",
    "series_b": "Series B",
    "series_c": "Series C",
    "series_d_plus": "Series D+",
    "growth": "Growth",
    "debt": "Debt",
    "grant": "Grant",
    "ipo": "IPO",
    "other": "Other",
}

_FUNDING_STAGE_ALIASES = {
    "preseed": "pre_seed", "pre seed": "pre_seed", "pre seed round": "pre_seed",
    "angel": "pre_seed", "friends and family": "pre_seed",
    "seed round": "seed", "seed funding": "seed", "seed extension": "seed",
    "a round": "series_a", "b round": "series_b", "c round": "series_c",
    "late stage": "series_d_plus", "growth equity": "growth",
    "growth round": "growth", "growth capital": "growth",
    "private equity": "growth", "pe": "growth", "mezzanine": "growth",
    "pre ipo": "growth", "crossover": "growth",
    "debt financing": "debt", "venture debt": "debt", "credit facility": "debt",
    "term loan": "debt", "loan": "debt", "convertible note": "debt",
    "government grant": "grant", "grant funding": "grant", "award": "grant",
    "initial public offering": "ipo", "public offering": "ipo",
    "direct listing": "ipo", "listing": "ipo", "flotation": "ipo",
    "strategic investment": "other", "corporate round": "other",
    "bridge": "other", "extension": "other", "undisclosed": "other",
}

# Series D through Z all collapse to one bucket. Splitting them would make a
# filter with one row per option, and by Series D the talent story is the same:
# an established company scaling, not a team being founded.
_SERIES_LETTER = re.compile(r"^series\s*[-_ ]?([a-z])\b")


def normalize_funding_stage(value: str):
    k = _key(value).replace("_", " ").replace("-", " ")
    k = re.sub(r"\s+", " ", k).strip()
    if not k:
        return None
    flat = k.replace(" ", "_")
    if flat in FUNDING_STAGES:
        return flat
    m = _SERIES_LETTER.match(k)
    if m:
        letter = m.group(1)
        return f"series_{letter}" if letter in ("a", "b", "c") else "series_d_plus"
    if k in _FUNDING_STAGE_ALIASES:
        return _FUNDING_STAGE_ALIASES[k]
    # Trailing noise a model adds ("seed round of funding", "IPO listing").
    stripped = re.sub(r"\s+(round|funding|financing|raise|deal|stage)$", "", k)
    if stripped != k:
        return normalize_funding_stage(stripped)
    return None


# --- Work mode -------------------------------------------------------------

# Where the work happens, when the source says so. rto_mandate is deliberately
# separate from onsite: "we are an office company" and "everyone is ordered
# back four days a week" are the same location and completely different news
# for anyone deciding whether to take the job.
WORK_MODES = ("remote", "hybrid", "onsite", "rto_mandate", "flexible")

WORK_MODE_LABELS = {
    "remote": "Remote",
    "hybrid": "Hybrid",
    "onsite": "Onsite",
    "rto_mandate": "Return-to-office mandate",
    "flexible": "Flexible",
}

_WORK_MODE_ALIASES = {
    "work from home": "remote", "wfh": "remote", "fully remote": "remote",
    "remote first": "remote", "remote only": "remote", "distributed": "remote",
    "work from anywhere": "remote",
    "hybrid working": "hybrid", "hybrid work": "hybrid",
    "hybrid model": "hybrid", "part remote": "hybrid",
    "on site": "onsite", "in office": "onsite", "in person": "onsite",
    "office based": "onsite", "on premises": "onsite", "onsite only": "onsite",
    "rto": "rto_mandate", "return to office": "rto_mandate",
    "return to office mandate": "rto_mandate", "office mandate": "rto_mandate",
    "rto policy": "rto_mandate", "back to office": "rto_mandate",
    "flexible working": "flexible", "flex": "flexible",
    "flexible hours": "flexible", "employee choice": "flexible",
}


def normalize_work_mode(value: str):
    k = _key(value).replace("_", " ").replace("-", " ")
    k = re.sub(r"\s+", " ", k).strip()
    if not k:
        return None
    flat = k.replace(" ", "_")
    if flat in WORK_MODES:
        return flat
    return _WORK_MODE_ALIASES.get(k)


# --- Employer type ---------------------------------------------------------

# What kind of organisation the employer is. Same standing as hq_country: the
# model may answer from its own knowledge of the company, because "is this a
# listed company or a startup" is stable background fact, not a claim about
# this week's event. Empty when it does not know.
EMPLOYER_TYPES = ("public", "private", "startup", "government", "nonprofit", "education")

EMPLOYER_TYPE_LABELS = {
    "public": "Public company",
    "private": "Private company",
    "startup": "Startup",
    "government": "Government",
    "nonprofit": "Nonprofit",
    "education": "Education",
}

_EMPLOYER_TYPE_ALIASES = {
    "publicly traded": "public", "publicly listed": "public",
    "public company": "public", "listed": "public", "listed company": "public",
    "plc": "public", "quoted": "public",
    "privately held": "private", "private company": "private",
    "privately owned": "private", "family owned": "private",
    "start up": "startup", "scaleup": "startup", "scale up": "startup",
    "venture backed": "startup", "vc backed": "startup",
    "early stage": "startup", "private startup": "startup",
    "public sector": "government", "state": "government",
    "state owned": "government", "federal": "government",
    "municipal": "government", "agency": "government",
    "civil service": "government", "government agency": "government",
    "non profit": "nonprofit", "not for profit": "nonprofit",
    "charity": "nonprofit", "ngo": "nonprofit", "foundation": "nonprofit",
    "university": "education", "college": "education", "school": "education",
    "academic": "education", "higher education": "education",
    "school district": "education",
}


def normalize_employer_type(value: str):
    k = _key(value).replace("_", " ").replace("-", " ")
    k = re.sub(r"\s+", " ", k).strip()
    if not k:
        return None
    flat = k.replace(" ", "_")
    if flat in EMPLOYER_TYPES:
        return flat
    return _EMPLOYER_TYPE_ALIASES.get(k)


# --- Headcount scope -------------------------------------------------------

# What the headcount number COUNTS. Without it, 4,000 could be four thousand
# jobs created, four thousand people at one plant, or the whole company, and a
# reader sorting by headcount is comparing three different quantities. Only
# meaningful when headcount is non-null.
HEADCOUNT_SCOPES = ("new_roles", "total_workforce", "single_site", "affected")

HEADCOUNT_SCOPE_LABELS = {
    "new_roles": "New roles",
    "total_workforce": "Total workforce",
    "single_site": "Single site",
    "affected": "Roles affected",
}

_HEADCOUNT_SCOPE_ALIASES = {
    "new roles": "new_roles", "new jobs": "new_roles", "new hires": "new_roles",
    "roles added": "new_roles", "jobs created": "new_roles",
    "roles created": "new_roles", "openings": "new_roles",
    "total workforce": "total_workforce", "total headcount": "total_workforce",
    "total employees": "total_workforce", "workforce": "total_workforce",
    "company wide": "total_workforce", "companywide": "total_workforce",
    "global workforce": "total_workforce", "headcount": "total_workforce",
    "single site": "single_site", "one site": "single_site",
    "site": "single_site", "facility": "single_site", "plant": "single_site",
    "one location": "single_site", "location": "single_site",
    "roles affected": "affected", "employees affected": "affected",
    "impacted": "affected", "roles cut": "affected", "jobs cut": "affected",
    "redundancies": "affected", "layoffs": "affected",
}


def normalize_headcount_scope(value: str):
    k = _key(value).replace("_", " ").replace("-", " ")
    k = re.sub(r"\s+", " ", k).strip()
    if not k:
        return None
    flat = k.replace(" ", "_")
    if flat in HEADCOUNT_SCOPES:
        return flat
    return _HEADCOUNT_SCOPE_ALIASES.get(k)


# --- Funding amount as a number --------------------------------------------

# funding_amount is stored verbatim because that is the quotable form the
# source used. That makes it useless for arithmetic: the live table holds
# '$3.6M', '$1.45 Million', '$130 Million' and '$1,000.0 million' side by side,
# so nothing can sum, sort or chart funding at all. This parses the string we
# already hold into plain US dollars.
#
# Deterministic and in Python on purpose. The model is never asked for the
# number: "never state a figure that is not in the text" is the rule the whole
# product rests on, and a model converting '$1.45 Million' to 1450000 is
# stating a figure the text does not contain.

# US$ and USD are noise once we know the currency, and stripping them first
# stops 'US$' from tripping the S$ (Singapore dollar) marker below.
_USD_PREFIX = re.compile(r"(?i)\bUS\s*\$")
_USD_CODE = re.compile(r"(?i)\bUSD\b")

# Anything that is NOT a US dollar. A non-USD figure leaves funding_amount_usd
# NULL rather than being converted: we would have to pick an exchange rate, and
# a made-up rate on a historical round is a made-up number. The verbatim string
# is still there for anyone who wants to read it.
_NON_USD = re.compile(
    r"[€£¥₹₽₩₪฿]"          # currency symbols
    r"|(?<![A-Za-z])(?:HK|NZ|NT|RM|Mex|MX|C|A|S|R|Z)\s?\$"         # C$, A$, S$, HK$, R$
    r"|\b(?:EUR|GBP|JPY|CHF|CAD|AUD|NZD|SGD|HKD|INR|CNY|RMB|SEK"
    r"|NOK|DKK|BRL|MXN|ZAR|KRW|PLN|ILS|AED|SAR|TRY|RUB|THB|IDR"
    r"|MYR|PHP|VND|EGP|NGN|TWD|CZK|HUF|RON|CLP|COP|ARS|PKR|BDT)\b"
    r"|\b(?:euros?|pounds?\s+sterling|sterling|yen|yuan|rupees?|won"
    r"|rand|reais|reals?|shekels?|dirhams?|kron[ao]r?|zloty|ruble[s]?"
    r"|lakh|crore)\b",
    re.I,
)

_AMOUNT = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*"
    r"(k|m|mm|mn|bn|b|t|thousand|million|millions|billion|billions|trillion)?\b",
    re.I,
)

_MULTIPLIERS = {
    None: 1,
    "k": 1_000, "thousand": 1_000,
    "m": 1_000_000, "mm": 1_000_000, "mn": 1_000_000,
    "million": 1_000_000, "millions": 1_000_000,
    "b": 1_000_000_000, "bn": 1_000_000_000,
    "billion": 1_000_000_000, "billions": 1_000_000_000,
    "t": 1_000_000_000_000, "trillion": 1_000_000_000_000,
}

# A round larger than this is a parse failure, not news. Ten trillion dollars
# is more than any company has ever raised, so a value above it means the
# string was something other than a funding figure.
_MAX_PLAUSIBLE_USD = 10_000_000_000_000


def parse_funding_usd(value: str):
    """Return the figure as whole US dollars, or None.

    None means "we will not guess", and covers: no digits at all, a currency
    that is not the US dollar, and anything that parses to an implausible
    number. Only the FIRST number is read, so a range ('$5M to $10M') stores
    its low end, matching how headcounts are parsed on the sibling tracker.
    """
    text = (str(value or "")).strip()
    if not text:
        return None

    text = _USD_PREFIX.sub("$", text)
    text = _USD_CODE.sub(" ", text)

    if _NON_USD.search(text):
        return None

    m = _AMOUNT.search(text)
    if not m:
        return None

    try:
        number = float(m.group(1).replace(",", ""))
    except ValueError:
        return None

    suffix = (m.group(2) or "").lower() or None
    amount = number * _MULTIPLIERS.get(suffix, 1)
    if amount <= 0 or amount > _MAX_PLAUSIBLE_USD:
        return None
    return int(round(amount))


# --- Materiality ------------------------------------------------------------

# How much a row is worth a recruiter's attention. Computed in Python at
# validate time (see validate.compute_materiality), never asked of a model, so
# it costs nothing and cannot drift between runs.
#
# It exists because correctness and usefulness came apart: the SEC backfill
# made thousands of individually-correct rows, most of them a bare officer
# change at a company nobody is recruiting against, and collectively they bury
# the handful of rows that state a headcount or a nine-figure raise.
MATERIALITY_LEVELS = ("high", "medium", "routine")

MATERIALITY_LABELS = {
    "high": "High",
    "medium": "Medium",
    "routine": "Routine",
}


def normalize_materiality(value: str):
    k = _key(value)
    return k if k in MATERIALITY_LEVELS else None


# --- Corporate deal type ----------------------------------------------------

# What KIND of corporate event this is, when the source says. An acquisition is
# one of the highest-value recruiter triggers — integration churn and duplicate
# roles follow it reliably — and until this column existed every deal sat
# inside company_development with nothing to distinguish it.
#
# Direction is the whole point. "Acme acquires Beta" and "Beta acquired by
# Acme" are the same event with opposite meaning for anyone recruiting: the
# buyer is usually hiring integration staff, the bought company is where the
# duplicate roles are. The value is always recorded from the perspective of the
# `company` on the row.
DEAL_TYPES = (
    "acquisition",    # this employer is BUYING
    "acquired",       # this employer is BEING bought
    "merger",
    "divestiture",    # selling a unit, spin-off, carve-out
    "joint_venture",
    "ipo",
)

DEAL_TYPE_LABELS = {
    "acquisition": "Acquiring",
    "acquired": "Being acquired",
    "merger": "Merger",
    "divestiture": "Divestiture",
    "joint_venture": "Joint venture",
    "ipo": "IPO",
}

_DEAL_TYPE_ALIASES = {
    "acquires": "acquisition", "acquiring": "acquisition",
    "acquirer": "acquisition", "buyer": "acquisition",
    "buys": "acquisition", "takeover": "acquisition",
    "acquisition of": "acquisition",
    "acquired by": "acquired", "being acquired": "acquired",
    "target": "acquired", "sold": "acquired", "bought": "acquired",
    "merge": "merger", "merges": "merger", "merger of equals": "merger",
    "combination": "merger",
    "divest": "divestiture", "divests": "divestiture",
    "divestment": "divestiture", "spin off": "divestiture",
    "spinoff": "divestiture", "spin-off": "divestiture",
    "carve out": "divestiture", "carveout": "divestiture",
    "sale of unit": "divestiture", "asset sale": "divestiture",
    "jv": "joint_venture", "joint venture": "joint_venture",
    "initial public offering": "ipo", "listing": "ipo",
    "going public": "ipo", "direct listing": "ipo",
}


def normalize_deal_type(value: str):
    """Closed vocabulary, or None. Same shape as every other enum here.

    Deliberately NOT inferred from the headline in Python: 'Acme in talks to
    acquire Beta' and 'Acme acquired Beta' read almost identically to a regex
    and mean different things, and which side of the deal the row's employer is
    on is exactly the judgement a keyword cannot make.
    """
    k = _key(value).replace("_", " ").replace("-", " ")
    k = re.sub(r"\s+", " ", k).strip()
    if not k:
        return None
    flat = k.replace(" ", "_")
    if flat in DEAL_TYPES:
        return flat
    return _DEAL_TYPE_ALIASES.get(k)
