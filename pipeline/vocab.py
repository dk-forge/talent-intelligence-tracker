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
    "toronto": ("Toronto", "North America", "US"),
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
