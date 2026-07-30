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
    # BSE is the exchange a company listed in India files its Regulation 30
    # disclosures WITH, and it publishes that filing rather than a report of it,
    # so it is the same class of host as sec.gov: the filing venue, not an
    # outlet. Without this line collectors/bse_india.py caps at 'reported' and
    # India's whole structured spine understates what it is.
    "bseindia.com",
    "www.bseindia.com",
    # EDINET is the Financial Services Agency's own disclosure system, and this
    # host serves the filing itself rather than a report of it, so it is the
    # same class as sec.gov: the venue a Japanese issuer files WITH. Without
    # this line collectors/edinet_japan.py caps at 'reported' and a statutory
    # filing reads as a news story.
    "disclosure2dl.edinet-fsa.go.jp",
    # DART is the Financial Supervisory Service's own disclosure registry: a
    # Korean listed company files WITH it and this host serves the filing
    # itself. Same class as sec.gov. Without this line
    # collectors/opendart_korea.py caps at 'reported'. Note the collector never
    # FETCHES this host — it reads opendart.fss.or.kr/api/ — which is why
    # robots.txt disallowing /dsaf001/main.do is a fact about link checking
    # rather than a reason to cite something else.
    "dart.fss.or.kr",
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
#
# One city, one state, or it does not belong here. Portland (Oregon and Maine),
# Columbus (Ohio and Georgia) and Kansas City (Missouri and Kansas) are in the
# city gazetteer — their COUNTRY is unambiguous — and deliberately absent from
# this table, because the state facet is the one place where guessing between
# them would be visibly wrong.
_CITY_STATE = {
    "San Francisco": "CA", "New York": "NY", "Seattle": "WA",
    "Austin": "TX", "Boston": "MA",
    "Los Angeles": "CA", "San Diego": "CA", "Palo Alto": "CA",
    "Mountain View": "CA", "Menlo Park": "CA", "Sunnyvale": "CA",
    "Santa Clara": "CA", "Cupertino": "CA", "Oakland": "CA",
    "San Jose CA": "CA", "Sacramento": "CA",
    "Redmond": "WA", "Bellevue": "WA",
    "Chicago": "IL", "Denver": "CO", "Boulder": "CO",
    "Atlanta": "GA", "Miami": "FL", "Tampa": "FL", "Orlando": "FL",
    "Dallas": "TX", "Fort Worth": "TX", "Houston": "TX", "San Antonio": "TX",
    "Phoenix": "AZ", "Tempe": "AZ",
    "Philadelphia": "PA", "Pittsburgh": "PA",
    "Minneapolis": "MN", "Detroit": "MI", "Ann Arbor": "MI",
    "Nashville": "TN", "Raleigh": "NC", "Durham": "NC", "Charlotte": "NC",
    "Cleveland": "OH", "Cincinnati": "OH", "Indianapolis": "IN",
    "Milwaukee": "WI", "Madison": "WI",
    "St. Louis": "MO", "Salt Lake City": "UT", "Provo": "UT",
    "Las Vegas": "NV", "Baltimore": "MD", "New Orleans": "LA",
    "Cambridge MA": "MA", "Birmingham AL": "AL", "Washington DC": "DC",
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

# --- The hub gazetteer -------------------------------------------------------
#
# WHY THIS BLOCK EXISTS. Measured 2026-07-29: 969 of 15,711 current rows
# carried a city, in 25 distinct cities, and the world's other startup hubs —
# Tel Aviv, Dubai, Sao Paulo, Seoul, Lagos, Nairobi, Jakarta — could not be
# stored at all, because "normalise through a fixed vocabulary or be dropped"
# means a place absent from THIS table is a place the product cannot report
# even when a source states it plainly. The 45 entries above were the ceiling
# on city coverage, not the extractor.
#
# It is still a fixed vocabulary and it still refuses everything not in it.
# What changed is the defensible extent: the hubs a hiring-side reader expects
# to filter by, each with the country a story about it belongs to.
#
# THREE RULES THIS TABLE KEEPS, and a test pins each:
#
# 1. ONE REGION PER COUNTRY. `validate._region_for_country` finds a region by
#    scanning these values for the first city with a matching code, so two
#    cities in one country disagreeing about their region would make the
#    region a dictionary-order accident.
# 2. EVERY CODE IS A COUNTRY WE CAN NAME. A code missing from COUNTRY_NAMES
#    stores a city whose country label renders empty.
# 3. NO CITY NAME BELONGS TO TWO COUNTRIES. This is why Cambridge (England and
#    Massachusetts), Birmingham (England and Alabama), Newcastle (England and
#    New South Wales) and San Jose (California and Costa Rica) are deliberately
#    ABSENT: a bare "Cambridge-based" cannot be placed without guessing, and
#    guessing a country is the one thing this product may never do. They are
#    reachable only in their qualified spellings below, where the source itself
#    resolved the ambiguity. Same-country collisions (Portland OR/ME,
#    Columbus OH/GA) are fine — the country is right either way — so those
#    cities are here but deliberately not in _CITY_STATE.
#
# Also deliberately absent: city names that are ordinary English words a
# headline uses as words (Reading, Bath, Mobile, Nice, Orange). The cost of
# admitting one is a company called Reading declining as "that is a place" in
# cheap_extract._valid_name, for a market we have never had a row from.
_CITY_ALIASES.update({
    # -- North America ------------------------------------------------------
    "chicago": ("Chicago", "North America", "US"),
    "los angeles": ("Los Angeles", "North America", "US"),
    # No bare "LA". Two letters is not enough to be a place: "La Jolla", "la
    # ciudad" and "La Poste" all start with it, and the gazetteer is read by a
    # scanner that only knows what it matched.
    "san diego": ("San Diego", "North America", "US"),
    "palo alto": ("Palo Alto", "North America", "US"),
    "mountain view": ("Mountain View", "North America", "US"),
    "menlo park": ("Menlo Park", "North America", "US"),
    "sunnyvale": ("Sunnyvale", "North America", "US"),
    "santa clara": ("Santa Clara", "North America", "US"),
    "cupertino": ("Cupertino", "North America", "US"),
    "oakland": ("Oakland", "North America", "US"),
    "redmond": ("Redmond", "North America", "US"),
    "bellevue": ("Bellevue", "North America", "US"),
    "denver": ("Denver", "North America", "US"),
    "boulder": ("Boulder", "North America", "US"),
    "atlanta": ("Atlanta", "North America", "US"),
    "miami": ("Miami", "North America", "US"),
    "dallas": ("Dallas", "North America", "US"),
    "fort worth": ("Fort Worth", "North America", "US"),
    "houston": ("Houston", "North America", "US"),
    "san antonio": ("San Antonio", "North America", "US"),
    "phoenix": ("Phoenix", "North America", "US"),
    "tempe": ("Tempe", "North America", "US"),
    "philadelphia": ("Philadelphia", "North America", "US"),
    "pittsburgh": ("Pittsburgh", "North America", "US"),
    "portland": ("Portland", "North America", "US"),
    "minneapolis": ("Minneapolis", "North America", "US"),
    "detroit": ("Detroit", "North America", "US"),
    "ann arbor": ("Ann Arbor", "North America", "US"),
    "nashville": ("Nashville", "North America", "US"),
    "raleigh": ("Raleigh", "North America", "US"),
    "durham": ("Durham", "North America", "US"),
    "charlotte": ("Charlotte", "North America", "US"),
    "columbus": ("Columbus", "North America", "US"),
    "cleveland": ("Cleveland", "North America", "US"),
    "cincinnati": ("Cincinnati", "North America", "US"),
    "indianapolis": ("Indianapolis", "North America", "US"),
    "milwaukee": ("Milwaukee", "North America", "US"),
    "madison": ("Madison", "North America", "US"),
    "kansas city": ("Kansas City", "North America", "US"),
    "st. louis": ("St. Louis", "North America", "US"),
    "st louis": ("St. Louis", "North America", "US"),
    "saint louis": ("St. Louis", "North America", "US"),
    "salt lake city": ("Salt Lake City", "North America", "US"),
    "provo": ("Provo", "North America", "US"),
    "las vegas": ("Las Vegas", "North America", "US"),
    "sacramento": ("Sacramento", "North America", "US"),
    "baltimore": ("Baltimore", "North America", "US"),
    "tampa": ("Tampa", "North America", "US"),
    "orlando": ("Orlando", "North America", "US"),
    "new orleans": ("New Orleans", "North America", "US"),
    "san juan": ("San Juan", "North America", "US"),
    # Washington DC only in its qualified spellings: a bare "Washington" is as
    # often the state as the capital, and the state is not a city.
    "washington dc": ("Washington DC", "North America", "US"),
    "washington d.c.": ("Washington DC", "North America", "US"),
    "washington, dc": ("Washington DC", "North America", "US"),
    "washington, d.c.": ("Washington DC", "North America", "US"),
    # The four names that belong to two countries, admitted ONLY where the
    # source spelled out which one it meant (rule 3 above).
    # Their display names are qualified too, and are alias keys in their own
    # right: a value we STORE has to read back as itself, or the column cannot
    # be re-normalised and `identity` cannot resolve an HQ it already wrote.
    # That is also why the US San Jose displays as "San Jose CA" — a bare "San
    # Jose" must keep meaning "we will not guess which one".
    "cambridge, ma": ("Cambridge MA", "North America", "US"),
    "cambridge ma": ("Cambridge MA", "North America", "US"),
    "cambridge, massachusetts": ("Cambridge MA", "North America", "US"),
    "cambridge, uk": ("Cambridge UK", "Europe", "GB"),
    "cambridge uk": ("Cambridge UK", "Europe", "GB"),
    "cambridge, england": ("Cambridge UK", "Europe", "GB"),
    "birmingham, al": ("Birmingham AL", "North America", "US"),
    "birmingham al": ("Birmingham AL", "North America", "US"),
    "birmingham, alabama": ("Birmingham AL", "North America", "US"),
    "birmingham, uk": ("Birmingham UK", "Europe", "GB"),
    "birmingham uk": ("Birmingham UK", "Europe", "GB"),
    "birmingham, england": ("Birmingham UK", "Europe", "GB"),
    "san jose, ca": ("San Jose CA", "North America", "US"),
    "san jose ca": ("San Jose CA", "North America", "US"),
    "san jose, california": ("San Jose CA", "North America", "US"),
    "london, ontario": ("London, Ontario", "North America", "CA"),
    "vancouver": ("Vancouver", "North America", "CA"),
    "montreal": ("Montreal", "North America", "CA"),
    "montréal": ("Montreal", "North America", "CA"),
    "ottawa": ("Ottawa", "North America", "CA"),
    "calgary": ("Calgary", "North America", "CA"),
    "edmonton": ("Edmonton", "North America", "CA"),
    "waterloo": ("Waterloo", "North America", "CA"),
    "kitchener": ("Kitchener", "North America", "CA"),
    "halifax": ("Halifax", "North America", "CA"),
    "quebec city": ("Quebec City", "North America", "CA"),
    "winnipeg": ("Winnipeg", "North America", "CA"),
    # -- Latin America ------------------------------------------------------
    "mexico city": ("Mexico City", "Latin America", "MX"),
    "ciudad de méxico": ("Mexico City", "Latin America", "MX"),
    "ciudad de mexico": ("Mexico City", "Latin America", "MX"),
    "cdmx": ("Mexico City", "Latin America", "MX"),
    "guadalajara": ("Guadalajara", "Latin America", "MX"),
    "monterrey": ("Monterrey", "Latin America", "MX"),
    "sao paulo": ("Sao Paulo", "Latin America", "BR"),
    "são paulo": ("Sao Paulo", "Latin America", "BR"),
    "rio de janeiro": ("Rio de Janeiro", "Latin America", "BR"),
    "belo horizonte": ("Belo Horizonte", "Latin America", "BR"),
    "florianopolis": ("Florianopolis", "Latin America", "BR"),
    "florianópolis": ("Florianopolis", "Latin America", "BR"),
    "buenos aires": ("Buenos Aires", "Latin America", "AR"),
    "cordoba": ("Cordoba", "Latin America", "AR"),
    "córdoba": ("Cordoba", "Latin America", "AR"),
    "santiago": ("Santiago", "Latin America", "CL"),
    "bogota": ("Bogota", "Latin America", "CO"),
    "bogotá": ("Bogota", "Latin America", "CO"),
    "medellin": ("Medellin", "Latin America", "CO"),
    "medellín": ("Medellin", "Latin America", "CO"),
    "lima": ("Lima", "Latin America", "PE"),
    "montevideo": ("Montevideo", "Latin America", "UY"),
    "san jose, costa rica": ("San Jose, Costa Rica", "Latin America", "CR"),
    "panama city": ("Panama City", "Latin America", "PA"),
    "quito": ("Quito", "Latin America", "EC"),
    "guayaquil": ("Guayaquil", "Latin America", "EC"),
    "santo domingo": ("Santo Domingo", "Latin America", "DO"),
    "guatemala city": ("Guatemala City", "Latin America", "GT"),
    # -- Europe -------------------------------------------------------------
    "bristol": ("Bristol", "Europe", "GB"),
    "leeds": ("Leeds", "Europe", "GB"),
    "glasgow": ("Glasgow", "Europe", "GB"),
    "cardiff": ("Cardiff", "Europe", "GB"),
    "sheffield": ("Sheffield", "Europe", "GB"),
    "nottingham": ("Nottingham", "Europe", "GB"),
    "liverpool": ("Liverpool", "Europe", "GB"),
    "oxford": ("Oxford", "Europe", "GB"),
    "brighton": ("Brighton", "Europe", "GB"),
    "bologna": ("Bologna", "Europe", "IT"),
    "rome": ("Rome", "Europe", "IT"),
    "roma": ("Rome", "Europe", "IT"),
    "turin": ("Turin", "Europe", "IT"),
    "torino": ("Turin", "Europe", "IT"),
    "naples": ("Naples", "Europe", "IT"),
    "florence": ("Florence", "Europe", "IT"),
    "firenze": ("Florence", "Europe", "IT"),
    "cologne": ("Cologne", "Europe", "DE"),
    "köln": ("Cologne", "Europe", "DE"),
    "koln": ("Cologne", "Europe", "DE"),
    "dusseldorf": ("Dusseldorf", "Europe", "DE"),
    "düsseldorf": ("Dusseldorf", "Europe", "DE"),
    "stuttgart": ("Stuttgart", "Europe", "DE"),
    "leipzig": ("Leipzig", "Europe", "DE"),
    "dresden": ("Dresden", "Europe", "DE"),
    "karlsruhe": ("Karlsruhe", "Europe", "DE"),
    "nuremberg": ("Nuremberg", "Europe", "DE"),
    "nürnberg": ("Nuremberg", "Europe", "DE"),
    "bonn": ("Bonn", "Europe", "DE"),
    "hanover": ("Hanover", "Europe", "DE"),
    "hannover": ("Hanover", "Europe", "DE"),
    "bremen": ("Bremen", "Europe", "DE"),
    "vienna": ("Vienna", "Europe", "AT"),
    "wien": ("Vienna", "Europe", "AT"),
    "graz": ("Graz", "Europe", "AT"),
    "linz": ("Linz", "Europe", "AT"),
    "geneva": ("Geneva", "Europe", "CH"),
    "genève": ("Geneva", "Europe", "CH"),
    "basel": ("Basel", "Europe", "CH"),
    "lausanne": ("Lausanne", "Europe", "CH"),
    "bern": ("Bern", "Europe", "CH"),
    "the hague": ("The Hague", "Europe", "NL"),
    "den haag": ("The Hague", "Europe", "NL"),
    "utrecht": ("Utrecht", "Europe", "NL"),
    "delft": ("Delft", "Europe", "NL"),
    "groningen": ("Groningen", "Europe", "NL"),
    "ghent": ("Ghent", "Europe", "BE"),
    "gent": ("Ghent", "Europe", "BE"),
    "leuven": ("Leuven", "Europe", "BE"),
    "liege": ("Liege", "Europe", "BE"),
    "liège": ("Liege", "Europe", "BE"),
    "lyon": ("Lyon", "Europe", "FR"),
    "marseille": ("Marseille", "Europe", "FR"),
    "toulouse": ("Toulouse", "Europe", "FR"),
    "bordeaux": ("Bordeaux", "Europe", "FR"),
    "lille": ("Lille", "Europe", "FR"),
    "nantes": ("Nantes", "Europe", "FR"),
    "grenoble": ("Grenoble", "Europe", "FR"),
    "montpellier": ("Montpellier", "Europe", "FR"),
    "sophia antipolis": ("Sophia Antipolis", "Europe", "FR"),
    "valencia": ("Valencia", "Europe", "ES"),
    "bilbao": ("Bilbao", "Europe", "ES"),
    "seville": ("Seville", "Europe", "ES"),
    "sevilla": ("Seville", "Europe", "ES"),
    "malaga": ("Malaga", "Europe", "ES"),
    "málaga": ("Malaga", "Europe", "ES"),
    "zaragoza": ("Zaragoza", "Europe", "ES"),
    "porto": ("Porto", "Europe", "PT"),
    "braga": ("Braga", "Europe", "PT"),
    "gothenburg": ("Gothenburg", "Europe", "SE"),
    "göteborg": ("Gothenburg", "Europe", "SE"),
    "malmo": ("Malmo", "Europe", "SE"),
    "malmö": ("Malmo", "Europe", "SE"),
    "uppsala": ("Uppsala", "Europe", "SE"),
    "aarhus": ("Aarhus", "Europe", "DK"),
    "århus": ("Aarhus", "Europe", "DK"),
    "odense": ("Odense", "Europe", "DK"),
    "bergen": ("Bergen", "Europe", "NO"),
    "trondheim": ("Trondheim", "Europe", "NO"),
    "espoo": ("Espoo", "Europe", "FI"),
    "tampere": ("Tampere", "Europe", "FI"),
    "oulu": ("Oulu", "Europe", "FI"),
    "reykjavik": ("Reykjavik", "Europe", "IS"),
    "reykjavík": ("Reykjavik", "Europe", "IS"),
    "wroclaw": ("Wroclaw", "Europe", "PL"),
    "wrocław": ("Wroclaw", "Europe", "PL"),
    "poznan": ("Poznan", "Europe", "PL"),
    "poznań": ("Poznan", "Europe", "PL"),
    "gdansk": ("Gdansk", "Europe", "PL"),
    "gdańsk": ("Gdansk", "Europe", "PL"),
    "lodz": ("Lodz", "Europe", "PL"),
    "łódź": ("Lodz", "Europe", "PL"),
    "brno": ("Brno", "Europe", "CZ"),
    "bratislava": ("Bratislava", "Europe", "SK"),
    "budapest": ("Budapest", "Europe", "HU"),
    "cluj-napoca": ("Cluj-Napoca", "Europe", "RO"),
    "cluj": ("Cluj-Napoca", "Europe", "RO"),
    "timisoara": ("Timisoara", "Europe", "RO"),
    "timișoara": ("Timisoara", "Europe", "RO"),
    "iasi": ("Iasi", "Europe", "RO"),
    "iași": ("Iasi", "Europe", "RO"),
    "sofia": ("Sofia", "Europe", "BG"),
    "plovdiv": ("Plovdiv", "Europe", "BG"),
    "belgrade": ("Belgrade", "Europe", "RS"),
    "novi sad": ("Novi Sad", "Europe", "RS"),
    "zagreb": ("Zagreb", "Europe", "HR"),
    "ljubljana": ("Ljubljana", "Europe", "SI"),
    "athens": ("Athens", "Europe", "GR"),
    "thessaloniki": ("Thessaloniki", "Europe", "GR"),
    "tallinn": ("Tallinn", "Europe", "EE"),
    "tartu": ("Tartu", "Europe", "EE"),
    "riga": ("Riga", "Europe", "LV"),
    "vilnius": ("Vilnius", "Europe", "LT"),
    "kaunas": ("Kaunas", "Europe", "LT"),
    "kyiv": ("Kyiv", "Europe", "UA"),
    "kiev": ("Kyiv", "Europe", "UA"),
    "lviv": ("Lviv", "Europe", "UA"),
    "minsk": ("Minsk", "Europe", "BY"),
    "chisinau": ("Chisinau", "Europe", "MD"),
    "chișinău": ("Chisinau", "Europe", "MD"),
    "nicosia": ("Nicosia", "Europe", "CY"),
    "valletta": ("Valletta", "Europe", "MT"),
    "skopje": ("Skopje", "Europe", "MK"),
    "tirana": ("Tirana", "Europe", "AL"),
    "sarajevo": ("Sarajevo", "Europe", "BA"),
    # -- Middle East --------------------------------------------------------
    "tel aviv": ("Tel Aviv", "Middle East", "IL"),
    "tel aviv-yafo": ("Tel Aviv", "Middle East", "IL"),
    "tel aviv-jaffa": ("Tel Aviv", "Middle East", "IL"),
    "jerusalem": ("Jerusalem", "Middle East", "IL"),
    "haifa": ("Haifa", "Middle East", "IL"),
    "herzliya": ("Herzliya", "Middle East", "IL"),
    "be'er sheva": ("Beersheba", "Middle East", "IL"),
    "beersheba": ("Beersheba", "Middle East", "IL"),
    "dubai": ("Dubai", "Middle East", "AE"),
    "abu dhabi": ("Abu Dhabi", "Middle East", "AE"),
    "sharjah": ("Sharjah", "Middle East", "AE"),
    "riyadh": ("Riyadh", "Middle East", "SA"),
    "jeddah": ("Jeddah", "Middle East", "SA"),
    "dammam": ("Dammam", "Middle East", "SA"),
    "neom": ("Neom", "Middle East", "SA"),
    "doha": ("Doha", "Middle East", "QA"),
    "kuwait city": ("Kuwait City", "Middle East", "KW"),
    "manama": ("Manama", "Middle East", "BH"),
    "muscat": ("Muscat", "Middle East", "OM"),
    "amman": ("Amman", "Middle East", "JO"),
    "beirut": ("Beirut", "Middle East", "LB"),
    "istanbul": ("Istanbul", "Middle East", "TR"),
    "ankara": ("Ankara", "Middle East", "TR"),
    "izmir": ("Izmir", "Middle East", "TR"),
    # -- Africa -------------------------------------------------------------
    "lagos": ("Lagos", "Africa", "NG"),
    "abuja": ("Abuja", "Africa", "NG"),
    "nairobi": ("Nairobi", "Africa", "KE"),
    "mombasa": ("Mombasa", "Africa", "KE"),
    "cape town": ("Cape Town", "Africa", "ZA"),
    "johannesburg": ("Johannesburg", "Africa", "ZA"),
    "pretoria": ("Pretoria", "Africa", "ZA"),
    "durban": ("Durban", "Africa", "ZA"),
    "cairo": ("Cairo", "Africa", "EG"),
    "alexandria": ("Alexandria", "Africa", "EG"),
    "giza": ("Giza", "Africa", "EG"),
    "accra": ("Accra", "Africa", "GH"),
    "kigali": ("Kigali", "Africa", "RW"),
    "kampala": ("Kampala", "Africa", "UG"),
    "dar es salaam": ("Dar es Salaam", "Africa", "TZ"),
    "addis ababa": ("Addis Ababa", "Africa", "ET"),
    "dakar": ("Dakar", "Africa", "SN"),
    "abidjan": ("Abidjan", "Africa", "CI"),
    "casablanca": ("Casablanca", "Africa", "MA"),
    "rabat": ("Rabat", "Africa", "MA"),
    "tunis": ("Tunis", "Africa", "TN"),
    "algiers": ("Algiers", "Africa", "DZ"),
    "lusaka": ("Lusaka", "Africa", "ZM"),
    "harare": ("Harare", "Africa", "ZW"),
    "gaborone": ("Gaborone", "Africa", "BW"),
    "port louis": ("Port Louis", "Africa", "MU"),
    # -- Asia ---------------------------------------------------------------
    "mumbai": ("Mumbai", "Asia", "IN"),
    "bombay": ("Mumbai", "Asia", "IN"),
    "new delhi": ("New Delhi", "Asia", "IN"),
    "delhi": ("New Delhi", "Asia", "IN"),
    "gurugram": ("Gurugram", "Asia", "IN"),
    "gurgaon": ("Gurugram", "Asia", "IN"),
    "noida": ("Noida", "Asia", "IN"),
    "chennai": ("Chennai", "Asia", "IN"),
    "kolkata": ("Kolkata", "Asia", "IN"),
    "calcutta": ("Kolkata", "Asia", "IN"),
    "ahmedabad": ("Ahmedabad", "Asia", "IN"),
    "jaipur": ("Jaipur", "Asia", "IN"),
    "chandigarh": ("Chandigarh", "Asia", "IN"),
    "kochi": ("Kochi", "Asia", "IN"),
    "coimbatore": ("Coimbatore", "Asia", "IN"),
    "indore": ("Indore", "Asia", "IN"),
    "thiruvananthapuram": ("Thiruvananthapuram", "Asia", "IN"),
    "karachi": ("Karachi", "Asia", "PK"),
    "lahore": ("Lahore", "Asia", "PK"),
    "islamabad": ("Islamabad", "Asia", "PK"),
    "dhaka": ("Dhaka", "Asia", "BD"),
    "colombo": ("Colombo", "Asia", "LK"),
    "kathmandu": ("Kathmandu", "Asia", "NP"),
    "seoul": ("Seoul", "Asia", "KR"),
    "busan": ("Busan", "Asia", "KR"),
    "beijing": ("Beijing", "Asia", "CN"),
    "shanghai": ("Shanghai", "Asia", "CN"),
    "shenzhen": ("Shenzhen", "Asia", "CN"),
    "hangzhou": ("Hangzhou", "Asia", "CN"),
    "guangzhou": ("Guangzhou", "Asia", "CN"),
    "chengdu": ("Chengdu", "Asia", "CN"),
    "hong kong": ("Hong Kong", "Asia", "HK"),
    "taipei": ("Taipei", "Asia", "TW"),
    "hsinchu": ("Hsinchu", "Asia", "TW"),
    "osaka": ("Osaka", "Asia", "JP"),
    "kyoto": ("Kyoto", "Asia", "JP"),
    "fukuoka": ("Fukuoka", "Asia", "JP"),
    "yokohama": ("Yokohama", "Asia", "JP"),
    "jakarta": ("Jakarta", "Asia", "ID"),
    "bandung": ("Bandung", "Asia", "ID"),
    "surabaya": ("Surabaya", "Asia", "ID"),
    "kuala lumpur": ("Kuala Lumpur", "Asia", "MY"),
    "penang": ("Penang", "Asia", "MY"),
    "cyberjaya": ("Cyberjaya", "Asia", "MY"),
    "bangkok": ("Bangkok", "Asia", "TH"),
    "chiang mai": ("Chiang Mai", "Asia", "TH"),
    "manila": ("Manila", "Asia", "PH"),
    "cebu": ("Cebu", "Asia", "PH"),
    "taguig": ("Taguig", "Asia", "PH"),
    "ho chi minh city": ("Ho Chi Minh City", "Asia", "VN"),
    "saigon": ("Ho Chi Minh City", "Asia", "VN"),
    "hanoi": ("Hanoi", "Asia", "VN"),
    "da nang": ("Da Nang", "Asia", "VN"),
    "phnom penh": ("Phnom Penh", "Asia", "KH"),
    "almaty": ("Almaty", "Asia", "KZ"),
    "astana": ("Astana", "Asia", "KZ"),
    "tashkent": ("Tashkent", "Asia", "UZ"),
    "tbilisi": ("Tbilisi", "Asia", "GE"),
    "yerevan": ("Yerevan", "Asia", "AM"),
    "baku": ("Baku", "Asia", "AZ"),
    # -- Oceania ------------------------------------------------------------
    "brisbane": ("Brisbane", "Oceania", "AU"),
    "perth": ("Perth", "Oceania", "AU"),
    "adelaide": ("Adelaide", "Oceania", "AU"),
    "canberra": ("Canberra", "Oceania", "AU"),
    "auckland": ("Auckland", "Oceania", "NZ"),
    "wellington": ("Wellington", "Oceania", "NZ"),
    "christchurch": ("Christchurch", "Oceania", "NZ"),
    "suva": ("Suva", "Oceania", "FJ"),
})

# Cities whose bare name belongs to two countries, so the vocabulary refuses
# the bare form on purpose (rule 3 above). Named rather than merely omitted so
# a future contributor adding "cambridge" has to delete a line that says why
# not, and so the extractor can tell "a place we will not guess at" apart from
# "not a place at all".
AMBIGUOUS_CITY_NAMES = frozenset({
    "cambridge", "birmingham", "san jose", "washington", "newcastle",
    "hamilton", "richmond", "victoria", "santa cruz", "valencia city",
    "sydney nova scotia", "st petersburg", "santiago de compostela",
})

# Sub-national names that DO fix a country, for reading a source's own
# disambiguation: "London, Ontario" is not London, and "Cambridge,
# Massachusetts" is not Cambridge. Only the qualifiers a newsroom actually
# appends; US states come from US_STATES, which is already exhaustive.
_PROVINCE_COUNTRY = {
    # Canada
    "ontario": "CA", "on": "CA", "quebec": "CA", "québec": "CA", "qc": "CA",
    "british columbia": "CA", "bc": "CA", "alberta": "CA", "ab": "CA",
    "manitoba": "CA", "saskatchewan": "CA", "nova scotia": "CA",
    "new brunswick": "CA", "newfoundland": "CA", "newfoundland and labrador": "CA",
    # United Kingdom
    "england": "GB", "scotland": "GB", "wales": "GB",
    "northern ireland": "GB", "uk": "GB", "u.k.": "GB", "britain": "GB",
    # Australia
    "new south wales": "AU", "nsw": "AU", "victoria state": "AU", "vic": "AU",
    "queensland": "AU", "qld": "AU", "western australia": "AU", "wa state": "AU",
    "south australia": "AU", "tasmania": "AU",
    # India, Germany, Spain — the states a dateline names beside a city
    "maharashtra": "IN", "karnataka": "IN", "tamil nadu": "IN",
    "telangana": "IN", "gujarat": "IN", "haryana": "IN", "kerala": "IN",
    "uttar pradesh": "IN", "west bengal": "IN", "rajasthan": "IN",
    "bavaria": "DE", "bayern": "DE", "hesse": "DE", "saxony": "DE",
    "north rhine-westphalia": "DE", "baden-württemberg": "DE",
    "catalonia": "ES", "catalunya": "ES", "andalusia": "ES",
    "basque country": "ES", "madrid region": "ES",
}


def place_qualifier_country(value: str):
    """ISO2 for a trailing place qualifier — a country, a US state, or one of
    the provinces above — or None.

    This is how "London, Ontario" stops being London: the qualifier resolves to
    CA, the gazetteer says London is GB, and a contradiction is a place the
    source disambiguated AWAY from the one we would have stored.
    """
    k = _key(value)
    if not k:
        return None
    hit = _PROVINCE_COUNTRY.get(k)
    if hit:
        return hit
    if normalize_state(k):
        return "US"
    return normalize_country(k)


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
    # Aruba and Curacao are constituent countries of the Kingdom of the
    # Netherlands rather than UN members, which is why a list built from the
    # UN roll left them out. Both are their own labour markets, both are
    # already on the Americas region tab, and both are countries the news
    # backstop now searches. Without an entry here a story from either
    # normalises to nothing and the record is rejected: a place a collector
    # covers and the vocabulary cannot admit.
    "AW": "Aruba",
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
    "CW": "Curacao",
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
    # The island spells itself with a cedilla and half the wire copy does not.
    'curaçao': 'CW',
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


# One employer, two spellings: variant key -> the key we keep.
#
# WHY THIS IS A LIST AND NOT A RULE.
#
# Each pair below differs from its partner ONLY in punctuation, and the
# difference comes from the filer rather than from us: SEC's EDGAR company
# index writes "PERMA FIX ENVIRONMENTAL SERVICES INC" where the 8-K cover page
# writes "Perma-Fix Environmental Services, Inc.", and the GOV.UK pay-gap
# service holds one NHS trust twice, under two employer ids, once with "&" and
# once with "and". So company_key produced two keys for one employer, both of
# which claim the SAME profile URL (the slug transliterates "&" to "and",
# strips accents and turns every run of punctuation into one hyphen).
# includes/company.php detects that as a collision and refuses to serve or
# publish either side, deliberately, rather than guessing which half of an
# employer's history to show.
#
# The rule-shaped fix is to make company_key fold exactly what the slug folds,
# so two names that produce one URL can only produce one key. That was measured
# before it was rejected: over the 7,788 distinct stored names it changes 274
# keys and 624 stored rows to merge THREE employers, and every one of those 274
# would need re-issuing because company_key feeds content_hash. It also
# contradicts the fix directly above — folding hyphens to spaces feeds "co" back
# to the suffix strip, and CO-OPERATIVE GROUP is mangled a second way.
#
# So the merge is stated, one line per employer, at the cost of needing to be
# added to. That cost is paid for by the check in ops_status.py [1c], which
# lists any two stored keys that claim one slug and are not named here: a new
# pair is loud rather than quietly unpublishable.
#
# The SURVIVING key in each pair is the one whose plain space-for-hyphen form is
# already the canonical slug (ASCII, "and", no punctuation). That keeps the fast
# path in tit_company_rows() — a direct REPLACE(company_key,' ','-') comparison
# in SQL — able to find it without going through the slug index at all.
EMPLOYER_KEY_ALIASES = {
    # SEC filer 0000891532, one 8-K and three pay-versus-performance tables.
    'perma-fix environmental services': 'perma fix environmental services',
    # SEC filer 0001401914. The company spells itself Daré; EDGAR shouts DARE.
    'daré bioscience': 'dare bioscience',
    # GOV.UK pay-gap employers 15028 (to 2022) and 22115 (from 2023). The trust
    # re-registered and dropped the ampersand; both ids are the same trust.
    'barking havering & redbridge university hospitals nhs trust':
        'barking havering and redbridge university hospitals nhs trust',
}


def company_key(name: str) -> str:
    """Stable join key for a company. Strips common legal suffixes so
    'Acme Inc.' and 'Acme, Inc' collapse to one employer.

    A suffix must be a WHOLE SPACE-DELIMITED TOKEN. `\\b` is not that: a hyphen
    is a word boundary, so `\\bco\\b` matched the "co" inside "co-operative" and
    'CO-OPERATIVE GROUP LIMITED' was stored under the key '-operative group'.
    Six employers were mangled that way, all of them real:

        ASSOCIATED BANC-CORP            -> 'associated banc-'
        CO-DIAGNOSTICS, INC.            -> '-diagnostics'
        CO-OPERATIVE GROUP LIMITED      -> '-operative group'
        THE MIDCOUNTIES CO-OPERATIVE    -> 'the midcounties -operative'
        CENTRAL ENGLAND CO-OPERATIVE    -> 'central england -operative'
        Overlay Alpha Co-GP, LLC        -> 'overlay alpha -gp'

    The lookaround below excludes a hyphen on either side, so 'Acme Co.' still
    loses its suffix (the punctuation strip above has already turned the dot
    into a space) while 'co-operative' and 'banc-corp' keep theirs.

    THIS CHANGES THE KEY FOR THOSE SIX AND ONLY THOSE SIX, out of 7,770 distinct
    stored names. company_key feeds content_hash, so rows already stored under a
    mangled key keep it, and a new signal for one of those six will not dedupe
    against them until a correction pass rewrites the stored keys through
    store.revise(). That pass is `correct_company_key.py`, and it derives its
    worklist by calling this function rather than from a list of six, so it
    covers the aliases below and anything a later fix here moves.

    The last step applies EMPLOYER_KEY_ALIASES, three curated merges of one
    employer recorded under two spellings. See the note above that map.
    """
    k = _key(name)
    k = re.sub(r"[^\w\s&-]", " ", k)
    k = re.sub(
        r"(?<![\w-])(inc|llc|ltd|limited|plc|corp|corporation|co|pbc|lp|llp|gmbh|ag|sa|nv|bv|ab|as|oy|spa|srl|pte|pty)(?![\w-])",
        " ",
        k,
    )
    k = re.sub(r"\s+", " ", k).strip()
    # After the suffix strip, never before: the alias is written in the form the
    # rest of this function produces, so a reader can check an entry against a
    # stored key by eye.
    return EMPLOYER_KEY_ALIASES.get(k, k)


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

# A US DOLLAR HAS TO BE STATED, not merely not-contradicted.
#
# The rule used to be a denylist: refuse if _NON_USD matches, otherwise treat the
# number as dollars. A denylist of currency words is guaranteed to be short by
# exactly the currencies nobody has met yet, and absence of evidence was being
# read as evidence of dollars. Three live rows proved it: '25 millioner kroner'
# and '10,5 mio. kr.' are Danish (kron[ao]r? does not match "kroner", and "kr."
# was in no list at all) and '500 millones' names no currency in the string
# while its own summary says euros. All three sat in funding_amount_usd on a
# page that promises amounts in other currencies are left out rather than
# converted at a rate nobody published.
#
# So the test is now POSITIVE, and it is cheap to be strict: of 3,097 current
# rows carrying a funding_amount, 3,094 name '$', 'US$' or 'USD' outright. The
# only three that did not were these three, and all three were wrong. A currency
# we have never seen now refuses by default instead of quietly becoming dollars.
_USD_MARKER = re.compile(r"(?i)\bUSD\b|\bUS\s*\$|(?<![A-Za-z])\$")

# The multiplier may be attached by a hyphen as well as by a space: BetaKit
# writes '$20-million USD', and \s* does not match '-', so that round was stored
# as twenty dollars. En and em dashes too, because a publisher's typographer may
# have been through it.
_AMOUNT = re.compile(
    r"(\d[\d,]*(?:[.,]\d+)?)\s*[-‐-―]?\s*"
    r"(k|m|mm|mn|mln|mio|mil|bn|b|t|thousand"
    r"|million|millions|millones|millioner|milliones|milhões|milhoes"
    r"|milione|milioni|millioni|milionu|miljoen"
    r"|billion|billions|billones|billioner|trillion)?\b\.?",
    re.I,
)

# Words for a million and a billion in the languages the feed catalogue actually
# covers. These only ever apply to a string that has already stated US dollars,
# so widening this list cannot turn a foreign amount into a dollar figure -- it
# can only stop 'USD 53 millones' being stored as fifty-three dollars.
#
# 'mia'/'milliard' are deliberately ABSENT. A Scandinavian milliard is 10^9 and
# a Spanish billón is 10^12, and no string here has ever paired either with an
# explicit USD marker, so guessing which convention a publisher meant would be
# inventing a figure. Such a string refuses, which is the correct answer.
_MULTIPLIERS = {
    None: 1,
    "k": 1_000, "thousand": 1_000,
    "m": 1_000_000, "mm": 1_000_000, "mn": 1_000_000,
    "mln": 1_000_000, "mio": 1_000_000,
    "million": 1_000_000, "millions": 1_000_000,
    "millones": 1_000_000, "milliones": 1_000_000, "millioner": 1_000_000,
    "milhões": 1_000_000, "milhoes": 1_000_000,
    "milione": 1_000_000, "milioni": 1_000_000, "millioni": 1_000_000,
    "milionu": 1_000_000, "miljoen": 1_000_000,
    "b": 1_000_000_000, "bn": 1_000_000_000,
    "billion": 1_000_000_000, "billions": 1_000_000_000,
    "billones": 1_000_000_000, "billioner": 1_000_000_000,
    "t": 1_000_000_000_000, "trillion": 1_000_000_000_000,
}

# Scale words that mean different things in different languages, so no reading of
# them is safe. 'mil' is a million in Singapore and Malaysian English ("US$22 mil
# in pre-Series A", which the 2026-07-29 sweep found) and a THOUSAND in Spanish
# and Portuguese. A thousand-fold error in either direction on the money total is
# worse than an absent figure, and the verbatim string is still on the row for
# anyone reading it. Matched by _AMOUNT so it cannot fall through to no
# multiplier at all, which is how 'US$22 mil' became twenty-two dollars.
_AMBIGUOUS_SCALE = frozenset({"mil"})


def _read_number(raw: str):
    """'1,450' -> 1450.0 and '10,5' -> 10.5, deciding which comma is which.

    A European decimal comma had never mattered, because every such string was
    refused for being a foreign currency before the number was read. Extending
    the multiplier vocabulary changes that: 'USD 1,5 millones' would otherwise
    strip the comma and store fifteen million dollars for one and a half. The
    rule is the ordinary one -- a group of exactly three digits after the last
    separator is a thousands group, anything else is a decimal fraction -- and a
    string carrying both '.' and ',' is read as English thousands.
    """
    text = raw.strip()
    if "." in text:
        return float(text.replace(",", ""))
    if "," in text:
        head, _, tail = text.rpartition(",")
        if len(tail) != 3 and head.replace(",", "").isdigit():
            return float(f"{head.replace(',', '')}.{tail}")
    return float(text.replace(",", ""))

# A round larger than this is a parse failure, not news. Ten trillion dollars
# is more than any company has ever raised, so a value above it means the
# string was something other than a funding figure.
_MAX_PLAUSIBLE_USD = 10_000_000_000_000


def parse_funding_usd(value: str):
    """Return the figure as whole US dollars, or None.

    None means "we will not guess", and covers: no digits at all, NO STATED US
    DOLLAR, a currency that is not the US dollar, and anything that parses to an
    implausible number. Only the FIRST number is read, so a range ('$5M to
    $10M') stores its low end, matching how headcounts are parsed on the sibling
    tracker.
    """
    text = (str(value or "")).strip()
    if not text:
        return None

    # A dollar must be STATED. See _USD_MARKER: the old denylist read "no
    # foreign currency word I recognise" as "dollars", and every currency it did
    # not recognise became one.
    if not _USD_MARKER.search(text):
        return None

    text = _USD_PREFIX.sub("$", text)
    text = _USD_CODE.sub(" ", text)

    if _NON_USD.search(text):
        return None

    m = _AMOUNT.search(text)
    if not m:
        return None

    try:
        number = _read_number(m.group(1))
    except ValueError:
        return None

    suffix = (m.group(2) or "").lower() or None
    if suffix in _AMBIGUOUS_SCALE:
        return None
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


# --- Site events -----------------------------------------------------------
#
# What an employer did with a PLACE of work: opened one, closed one, made an
# existing one bigger, moved one. This is the earliest geographic hiring signal
# there is — a site decision lands months before the job adverts do — and until
# it had a column of its own it was invisible unless the story happened to
# state a headcount.
#
# `announced` is the honest fifth value and is not a synonym for `opened`. A
# company saying it WILL build a plant in 2028 and a company cutting a ribbon
# this morning mean different things to somebody deciding where to apply, and
# collapsing them would put "three employers opened sites here this quarter"
# on a page where none of the three has opened anything yet.
#
# It says NOTHING about headcount. `opened` never implies signal_direction
# 'hiring' and `closed` never implies 'displacement': the direction still comes
# from what the source states, and "headcount not stated" stays the common and
# correct answer. That separation is the whole reason this is its own column
# rather than a direction value.
SITE_EVENTS = ("opened", "closed", "expanded", "relocated", "announced")

SITE_EVENT_LABELS = {
    "opened": "Site opened",
    "closed": "Site closed",
    "expanded": "Site expanded",
    "relocated": "Site relocated",
    "announced": "Site announced",
}

_SITE_EVENT_ALIASES = {
    "open": "opened", "opens": "opened", "opening": "opened",
    "new office": "opened", "new site": "opened", "launched": "opened",
    "launch": "opened", "inaugurated": "opened", "established": "opened",
    "establishes": "opened", "set up": "opened", "opened site": "opened",
    "close": "closed", "closes": "closed", "closing": "closed",
    "shut": "closed", "shuts": "closed", "shutdown": "closed",
    "shutting": "closed", "shuttered": "closed", "wind down": "closed",
    "winding down": "closed", "exit": "closed", "exits": "closed",
    "expand": "expanded", "expands": "expanded", "expanding": "expanded",
    "expansion": "expanded", "enlarged": "expanded", "extended": "expanded",
    "upgrade": "expanded", "upgraded": "expanded",
    "relocate": "relocated", "relocates": "relocated",
    "relocating": "relocated", "relocation": "relocated",
    "move": "relocated", "moves": "relocated", "moved": "relocated",
    "moving": "relocated",
    "announce": "announced", "announces": "announced",
    "planned": "announced", "plans": "announced", "proposed": "announced",
    "to build": "announced", "to open": "announced", "will open": "announced",
    "under construction": "announced", "breaking ground": "announced",
}


def normalize_site_event(value: str):
    """Closed vocabulary, or None.

    Deliberately not derived from the headline in Python. "Acme to close its
    Cork plant" and "Acme closes its Cork plant" differ by one word and by a
    year, and a regex reading a headline cannot tell a decision from an event —
    which is the same reason normalize_deal_type refuses to guess a direction.
    """
    k = _key(value).replace("_", " ").replace("-", " ")
    k = re.sub(r"\s+", " ", k).strip()
    if not k:
        return None
    flat = k.replace(" ", "_")
    if flat in SITE_EVENTS:
        return flat
    return _SITE_EVENT_ALIASES.get(k)
