"""Fixed vocabularies.

Nothing the model freely types is ever stored. Every categorical field on a
signal is normalised through one of these closed lists first, and a value that
will not normalise is a rejected record, not a new category.

Spec 6.3: "Bay Area" / "SF" / "San Francisco" must not be three cities.
"""

from __future__ import annotations

import re
import unicodedata

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
    # ARES is the Czech Ministry of Finance's own register service and it
    # republishes the courts' public register (veřejný rejstřík) rather than
    # reporting on it, so it is the same class of host as sec.gov: the register
    # itself. Without this line collectors/czechia_ares.py caps at 'reported'
    # and a court-maintained register reads as a news story. The collector
    # cites the API document under this host on purpose — the site's own
    # /ekonomicke-subjekty/{ico} page answers 200 with an identical app shell
    # for a real and an invented company, and or.justice.cz robots-disallows
    # the whole register UI. See the collector's docstring.
    "ares.gov.cz",
    # Ariregister is the Estonian Centre of Registers and Information Systems'
    # own publication of the business register it maintains. Same class again.
    # Two hosts, because the collector reads the open-data files from one
    # subdomain and cites the register's own company page on the other.
    "ariregister.rik.ee",
    "avaandmed.ariregister.rik.ee",
    # The Boletín Oficial del Registro Mercantil is where a Spanish commercial
    # register's acts are LEGALLY published, by the Agencia Estatal BOE, and
    # this host serves that bulletin rather than a report of it. Same class as
    # sec.gov. Without this line collectors/spain_borme.py caps at 'reported'
    # and a statutory inscription reads as a news story.
    "boe.es",
    "www.boe.es",
    # data.gov.il is the Israeli government's own portal, and the changes file
    # on it is published BY the Registrar of Companies at the Ministry of
    # Justice rather than reported by anybody. Same class as sec.gov: the
    # register itself. Without this line collectors/israel_registrar.py caps at
    # 'reported' and a statutory share allotment reads as a news story. The
    # collector cites the portal's own datastore query for the company on
    # purpose: the registrar's public lookup at ica.justice.gov.il is a search
    # FORM with no per-company permalink, so there is no stabler page to cite.
    "data.gov.il",
    # data.gov.sg is the Singapore government's own portal and the corporate
    # entities register on it is published BY the Accounting and Corporate
    # Regulatory Authority, the body companies incorporate with. Same class
    # again. Two hosts, because the collector reads the dataset through the
    # portal's API subdomain and cites the register's own collection page.
    "data.gov.sg",
    "api-production.data.gov.sg",
    # The IRS's own Tax Exempt Organization Search serves the FILED RETURN from
    # this host: /pub/epostcard/cor/<ein>_<period>_990_<id>.pdf is the copy of
    # the Form 990 the organisation filed, not a report of it. Same class as
    # sec.gov, the venue an employer files WITH. Without this line
    # collectors/irs_form_990.py caps at 'reported' and a disclosure Congress
    # made public specifically so it would be read comes out as a news story.
    # The collector never touches /app/eos on this host, which is the one path
    # that refuses automated clients; it reads /teos/ and /pub/, both 200.
    "apps.irs.gov",
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

# --- The way each city's own newsroom writes it ------------------------------
#
# We ask Google News in sixteen languages (source_registry.GOOGLE_NEWS_VOCAB) and
# then, until 2026-08-13, only accepted English answers. Of the 422 alias keys
# above exactly 27 were non-ASCII, and every one of those was Latin script with
# a diacritic: no Japanese, Korean, Chinese, Hebrew, Arabic, Thai or Cyrillic
# spelling of any city was in the table at all.
#
# That is the sibling tracker's English-only defect one layer down. Its version
# was 45 editions searched with English phrases, so the articles never arrived.
# Ours is worse-shaped and quieter: the articles DO arrive, we pay a model to
# read them, and then the place is dropped on a dictionary miss. Measured on the
# committed corpus, news rows placed with a city, by market:
#
#   TR 0 of 71    IL 0 of 70    VN 0 of 49    ID 0 of 47
#   JP 1 of 56    KR 1 of 77    BR 2 of 149   IT 3 of 175
#
# against 10-26% in the English-language markets. Nothing errored and no health
# check moved, because a NULL city is indistinguishable from a story that named
# no city.
#
# Only cities whose language has a live locale in GOOGLE_NEWS_LOCALES, plus the
# CJK markets the frame needs. A spelling nobody sends us is dead weight that
# still has to be right, so this list stays tied to the editions we query.
_CITY_ALIASES.update({
    # -- Japanese ------------------------------------------------------------
    "東京": ("Tokyo", "Asia", "JP"),
    "東京都": ("Tokyo", "Asia", "JP"),
    "大阪": ("Osaka", "Asia", "JP"),
    "大阪市": ("Osaka", "Asia", "JP"),
    "京都": ("Kyoto", "Asia", "JP"),
    "福岡": ("Fukuoka", "Asia", "JP"),
    "横浜": ("Yokohama", "Asia", "JP"),
    # -- Korean --------------------------------------------------------------
    "서울": ("Seoul", "Asia", "KR"),
    "서울시": ("Seoul", "Asia", "KR"),
    "ソウル": ("Seoul", "Asia", "KR"),
    "부산": ("Busan", "Asia", "KR"),
    # -- Chinese (no zh edition yet; these arrive through en, ja and ko copy) --
    "上海": ("Shanghai", "Asia", "CN"),
    "北京": ("Beijing", "Asia", "CN"),
    "深圳": ("Shenzhen", "Asia", "CN"),
    "杭州": ("Hangzhou", "Asia", "CN"),
    "广州": ("Guangzhou", "Asia", "CN"),
    "廣州": ("Guangzhou", "Asia", "CN"),
    "成都": ("Chengdu", "Asia", "CN"),
    "台北": ("Taipei", "Asia", "TW"),
    "臺北": ("Taipei", "Asia", "TW"),
    "新竹": ("Hsinchu", "Asia", "TW"),
    "香港": ("Hong Kong", "Asia", "HK"),
    # -- Hebrew (he-IL is live, and it is where Israeli rounds break first) ---
    "תל אביב": ("Tel Aviv", "Middle East", "IL"),
    "תל־אביב": ("Tel Aviv", "Middle East", "IL"),
    "תל אביב-יפו": ("Tel Aviv", "Middle East", "IL"),
    "ירושלים": ("Jerusalem", "Middle East", "IL"),
    "חיפה": ("Haifa", "Middle East", "IL"),
    "הרצליה": ("Herzliya", "Middle East", "IL"),
    "באר שבע": ("Beersheba", "Middle East", "IL"),
    # -- Arabic (ar-AE, ar-SA, ar-EG, ar-QA, ar-MA are all live) -------------
    "دبي": ("Dubai", "Middle East", "AE"),
    "أبوظبي": ("Abu Dhabi", "Middle East", "AE"),
    "أبو ظبي": ("Abu Dhabi", "Middle East", "AE"),
    "الشارقة": ("Sharjah", "Middle East", "AE"),
    "الرياض": ("Riyadh", "Middle East", "SA"),
    "جدة": ("Jeddah", "Middle East", "SA"),
    "الدمام": ("Dammam", "Middle East", "SA"),
    "القاهرة": ("Cairo", "Africa", "EG"),
    "الإسكندرية": ("Alexandria", "Africa", "EG"),
    "الدوحة": ("Doha", "Middle East", "QA"),
    "الدار البيضاء": ("Casablanca", "Africa", "MA"),
    "الرباط": ("Rabat", "Africa", "MA"),
    # -- Vietnamese (vi-VN is live and held zero placed rows) ----------------
    "hà nội": ("Hanoi", "Asia", "VN"),
    "ha noi": ("Hanoi", "Asia", "VN"),
    "thành phố hồ chí minh": ("Ho Chi Minh City", "Asia", "VN"),
    "tp hcm": ("Ho Chi Minh City", "Asia", "VN"),
    "tp.hcm": ("Ho Chi Minh City", "Asia", "VN"),
    "đà nẵng": ("Da Nang", "Asia", "VN"),
    # -- Thai ----------------------------------------------------------------
    "กรุงเทพ": ("Bangkok", "Asia", "TH"),
    "กรุงเทพมหานคร": ("Bangkok", "Asia", "TH"),
    "เชียงใหม่": ("Chiang Mai", "Asia", "TH"),
    # -- Indonesian (id-ID, id-MY are live) ----------------------------------
    "jakarta selatan": ("Jakarta", "Asia", "ID"),
    "jakarta pusat": ("Jakarta", "Asia", "ID"),
    "dki jakarta": ("Jakarta", "Asia", "ID"),
    # -- Latin script, the local spelling ------------------------------------
    "napoli": ("Naples", "Europe", "IT"),
    "milano": ("Milan", "Europe", "IT"),
    "warszawa": ("Warsaw", "Europe", "PL"),
    "københavn": ("Copenhagen", "Europe", "DK"),
    # German and Swiss wires write the umlaut out rather than dropping it, so
    # this is a different spelling and not something a fold can reach.
    "muenchen": ("Munich", "Europe", "DE"),
    "koeln": ("Cologne", "Europe", "DE"),
    "duesseldorf": ("Dusseldorf", "Europe", "DE"),
    "nuernberg": ("Nuremberg", "Europe", "DE"),
    "zuerich": ("Zurich", "Europe", "CH"),
    "bruxelles": ("Brussels", "Europe", "BE"),
    "brussel": ("Brussels", "Europe", "BE"),
    "bruselas": ("Brussels", "Europe", "BE"),
    "bucureşti": ("Bucharest", "Europe", "RO"),
    "bucurești": ("Bucharest", "Europe", "RO"),
    "estambul": ("Istanbul", "Middle East", "TR"),
    "i̇stanbul": ("Istanbul", "Middle East", "TR"),
    "i̇zmir": ("Izmir", "Middle East", "TR"),
    "münih": ("Munich", "Europe", "DE"),
    "lisboa": ("Lisbon", "Europe", "PT"),
    "sevilla": ("Seville", "Europe", "ES"),
    "cidade do méxico": ("Mexico City", "Latin America", "MX"),
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


# Transliterations a decomposition cannot reach: these letters carry no
# combining mark to strip, so NFKD leaves them exactly as they are.
_FOLD_LETTERS = {
    "ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "ł": "l", "đ": "d",
    "ð": "d", "þ": "th", "ı": "i", "ħ": "h", "ŋ": "n", "ə": "e",
}


def _fold(value: str) -> str:
    """The alias key with every diacritic removed. ASCII in, ASCII out.

    Half the wires transliterate their own accents, and the table held
    'münchen' but not 'munchen' — a spelling difference deciding whether a
    market appears on the dashboard at all. Turkish is the sharp case: 'İzmir'
    lowercases to an i with a combining dot above, which matched nothing.

    Non-Latin scripts fold to themselves, which is correct: there is nothing to
    strip and the exact key above is the only way in.
    """
    k = _key(value)
    k = "".join(_FOLD_LETTERS.get(ch, ch) for ch in k)
    return "".join(ch for ch in unicodedata.normalize("NFKD", k)
                   if not unicodedata.combining(ch))


def _build_folded_index() -> dict:
    """Folded key -> the one city it can only mean.

    A folded key claimed by two DIFFERENT cities is dropped, not resolved.
    This is a fallback for a spelling we cannot otherwise read, and guessing
    between two real cities is the failure AMBIGUOUS_CITY_NAMES exists to
    prevent — it must not come back in through the accent door.
    """
    claims: dict[str, set] = {}
    for alias, hit in _CITY_ALIASES.items():
        claims.setdefault(_fold(alias), set()).add(hit)
    return {k: next(iter(v)) for k, v in claims.items() if len(v) == 1}


_CITY_FOLDED = _build_folded_index()


def normalize_city(value: str):
    """Return (city, region, iso2) or None. Never invents a city."""
    key = _key(value)
    hit = _CITY_ALIASES.get(key)
    if hit:
        return hit
    # The exact table is the authority; the fold only rescues a spelling it
    # could not read, and only where the fold means exactly one city.
    if key in AMBIGUOUS_CITY_NAMES:
        return None
    folded = _fold(key)
    if folded in AMBIGUOUS_CITY_NAMES:
        return None
    return _CITY_FOLDED.get(folded)


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

    # --- 2026-08-02 slug-collision review -----------------------------------
    # ops_status flagged 12 slugs claimed by two keys. They were three different
    # things, and only the first is an alias:
    #   * 5 are one employer under two spellings -> merged here;
    #   * 2 are one employer whose every spelling is non-Latin, so no survivor
    #     satisfies this map's own SQL-findability rule -> SAME_EMPLOYER_NO_ASCII_KEY;
    #   * 5 are DIFFERENT employers colliding because the slug deletes non-Latin
    #     characters -> DISTINCT_EMPLOYER_SLUG_COLLISIONS.
    # Both lists are below. Merging the third group would have fused SK Telecom
    # with SK Hynix and two unrelated municipal football clubs.
    #
    # The survivor is never a free choice: test_the_surviving_spelling_is_the_
    # one_sql_can_find_without_the_index requires it to slugify to itself, so it
    # has to be the ASCII-clean spelling. Where both are clean, the plainer form
    # wins, matching the three entries above (hyphen -> space, accent -> plain).
    'coca-cola': 'coca cola',            # news vs EDGAR's "COCA COLA CO"
    'nestlé': 'nestle',
    'rcf notre-dame': 'rcf notre dame',  # one appointment, two French outlets
    # The Hebrew rendering one outlet used, against the company's own
    # international name (IDE Technologies). Both rows report Yuri Bronstein's
    # appointment as CEO of the same water-technology company (Calcalist and
    # TheMarker). Noting the risk honestly: a bare 'ide' is a generic key, so
    # if some unrelated IDE ever appears it will land here and need splitting.
    'ide טכנולוגיות מים': 'ide',
    # BBQ is the trading name of 제너시스BBQ (Genesis BBQ). Both rows track one
    # executive: Park Ji-man appointed 대표이사 in January, resigning in July.
    # The full legal name would be the better canonical, but it is not
    # SQL-findable, so the trading name survives.
    '제너시스bbq': 'bbq',
    # --- 2026-09-04 slug-collision review -----------------------------------
    # ops_status flagged 11 slugs claimed by two keys. Eight are one employer
    # under two spellings that differ only by a diacritic, a hyphen, an
    # ampersand or a space, and they follow the rule above: the survivor is the
    # ASCII-clean plainer form (hyphen -> space, accent -> plain, & -> and),
    # which is the spelling tit_company_rows() can find without the slug index.
    # Three were left for a human: 'indigo' / '인디고' may be two employers (the
    # Korean rendering need not be the airline); 'kcu npl 대부' has no spelling
    # the sources use that is also ASCII, so naming one would be inventing it;
    # and 'giày/giầy thượng đình' cannot be aliased to 'giay thuong dinh' until
    # the two slugifiers agree on đ (the Python slug() drops it, 'giay-thuong-
    # inh', while the published /company/ slug reads 'giay-thuong-dinh'), which
    # test_identity rightly refuses as two names.
    'air-india': 'air india',
    'colgate palmolive índia': 'colgate palmolive india',
    'colgate-palmolive india': 'colgate palmolive india',
    'dolce & gabbana': 'dolce and gabbana',
    'dolce&gabbana': 'dolce and gabbana',
    'erco energía': 'erco energia',
    'formosa hà tĩnh': 'formosa ha tinh',
    'gol linhas aéreas': 'gol linhas aereas',
    'hc valais-wallis academy': 'hc valais wallis academy',
    'n - able': 'n able',
    'n-able': 'n able',
}


# One employer, two spellings, and NEITHER can be the survivor: this map
# requires a key that slugifies to itself, and every spelling of these two is
# non-Latin. Naming an ASCII canonical would mean inventing a company name the
# sources do not use, so they are recorded rather than guessed at.
#
# They are the same defect as DISTINCT_EMPLOYER_SLUG_COLLISIONS below — the
# published slug cannot represent a non-Latin name — and they clear the same
# way: once tit_company_slug keeps those characters (or transliterates them),
# each spelling gets its own URL and one of them can become the survivor.
SAME_EMPLOYER_NO_ASCII_KEY = {
    # NH證 is how the Korean business press abbreviates NH투자증권 (NH
    # Investment & Securities). Both rows are the same CEO succession race.
    'nh': ('nh證', 'nh투자증권'),
    # One diacritic apart: "Giày" is the correct Vietnamese word for shoe,
    # "Giầy" the variant. Same company, same Vinaconex-linked CEO appointment,
    # reported by tuoitre.vn and vietstock.vn.
    'giay-thuong-inh': ('giày thượng đình', 'giầy thượng đình'),
}


# Two keys can claim one profile URL without being one employer. The published
# slug (tit_company_slug in the plugin) folds accents and then deletes every
# remaining non-[a-z0-9] character, so a name written in Hangul, Han or Hebrew
# is reduced to whatever Latin fragment it happens to contain — '오픈ai' and
# '페르소나ai' both become 'ai'. That is a slug defect, not a duplicate
# employer, and the fix is a plugin change to keep the two apart.
#
# Until then these pairs are recorded here so the collision report can say
# "two different employers, blocked on the slug" instead of asking someone to
# decide which spelling wins. Choosing one WOULD silently destroy an employer:
# every pair below is two distinct companies.
DISTINCT_EMPLOYER_SLUG_COLLISIONS = {
    # OpenAI (US, covered by Korean press) and Persona AI, a Korean defence-
    # tech startup raising from LIG Nex1. Unrelated.
    'ai': ('오픈ai', '페르소나ai'),
    # Two subsidiaries of BNK Financial Group: BNK PierX (renamed PierX Co.)
    # and BNK Capital. Separate companies, separate CEOs.
    'bnk': ('bnk 피어엑스', 'bnk캐피탈'),
    # Two municipal South Korean football clubs, Changwon and Hwaseong.
    'fc': ('창원fc', '화성fc'),
    # IBM and IBM Japan ('ibm' vs '日本ibm', the Japanese subsidiary's own
    # presidency changing hands) is NOT here any more. It was the one live
    # instance of this defect: 'ibm' is already ASCII, so nothing gets
    # romanised or folded away from it, and '日本ibm' collapsed onto the same
    # slug once its Han prefix was deleted. Fixed in tit_company_slug_index()
    # (includes/company.php): a slug collision where exactly one owner's own
    # spelling produced it and every other owner only arrived by having a
    # script deleted now gives each deleted-script owner its own
    # percent-encoded URL instead of refusing both. Left as a comment, not a
    # dict entry, because there is no longer a collision for this report to
    # classify.
    # SK Telecom and SK Hynix — two SK Group companies, and the one pair here
    # where a careless merge would have been most expensive.
    'sk': ('sk 电信', 'sk하이닉스'),
}


#: "<somebody>-backed <the actual employer>". Up to three tokens of backer
#: name, because the corpus writes "Abu Dhabi-backed" and "SoftBank Vision
#: Fund-backed" as well as "OpenAI-backed". Anchored at the start: a qualifier
#: in the middle of a name is doing different work.
_BACKER_PREFIX = re.compile(
    r"^(?:[\w&]+[\s-]){0,2}[\w&]+-(?:backed|owned|led|funded|founded|controlled)\s+",
    re.I,
)


def _strip_backer_prefix(k: str) -> str:
    """Drop a leading backer qualifier, but never the whole name.

    The guard is the point. If everything before the qualifier is all there is,
    the qualifier IS the name and stripping it would leave an empty key that
    every other nameless row would then collide with.
    """
    stripped = _BACKER_PREFIX.sub("", k, count=1).strip()
    return stripped if stripped else k


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

    A LEADING BACKER QUALIFIER IS NOT PART OF THE NAME (2026-08-20). Two
    outlets covered one $2bn round on the same day:

        "Thrive Holdings Raises $2B To Expand AI-Powered Business Roll-Ups"
        "OpenAI-backed Thrive Holdings raises $2B to bring AI to the enterprise"

    The second keyed as `openai-backed thrive holdings`, so the two rows never
    met in either dedup layer — both of which require the keys to be EQUAL —
    and $2bn was counted twice in the public money total. `Nvidia-backed
    Nscale` and `Google-backed Isomorphic` are the same shape and the same
    defect. The qualifier says who the investors are, which is a fact about the
    round and never a fact about the employer's identity.

    Only a HYPHENATED participle is stripped, and only from the front, and only
    when a name survives it. `-backed`, `-owned`, `-led`, `-funded`,
    `-founded`, `-controlled`: all of them describe a relationship to somebody
    else. It is deliberately not `\\bbacked\\b`, which would eat "Asset Backed
    Securities Corp", and deliberately not a bare leading-word strip, which is
    how "Revision Optics" became "optics" in the sibling tracker.

    The last step applies EMPLOYER_KEY_ALIASES, three curated merges of one
    employer recorded under two spellings. See the note above that map.
    """
    k = _key(name)
    k = re.sub(r"[^\w\s&-]", " ", k)
    k = _strip_backer_prefix(k)
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
# `(?![A-Za-z])` rather than `\b` for the reason _NON_USD carries the same
# lookahead: 'USD28 million' is how a code is written when it is glued to its
# figure, and a trailing `\b` does not close a letter run against a digit. Our
# own code has to answer that shape the way the foreign ones now do, or a
# stated US dollar reads as no currency at all.
_USD_CODE = re.compile(r"(?i)\bUSD(?![A-Za-z])")

# Anything that is NOT a US dollar. A non-USD figure leaves funding_amount_usd
# NULL rather than being converted: we would have to pick an exchange rate, and
# a made-up rate on a historical round is a made-up number. The verbatim string
# is still there for anyone who wants to read it.
_NON_USD = re.compile(
    r"[€£¥₹₽₩₪฿]"          # currency symbols
    r"|(?<![A-Za-z])(?:HK|NZ|NT|RM|Mex|MX|C|A|S|R|Z)\s?\$"         # C$, A$, S$, HK$, R$
    # The closing boundary is a NEGATIVE LOOKAHEAD FOR A LETTER, not `\b`, and
    # the difference is a live row. A newswire writes the code glued to its
    # figure -- 'JPY28 billion', 'EUR10 milioni', 'RMB7 billion' are three
    # strings this database holds -- and `\b` needs a non-word character after
    # the 'Y', which a digit is not. So JPY28 named no currency this pattern
    # could see; the string went on to state 'US$176.68 million' in a
    # parenthesised conversion, that satisfied _USD_MARKER, and 28 billion YEN
    # was stored as 28 billion DOLLARS. A digit after a currency code is the
    # ordinary way to write one, and it must close the token exactly as a space
    # does.
    r"|\b(?:EUR|GBP|JPY|CHF|CAD|AUD|NZD|SGD|HKD|INR|CNY|RMB|SEK"
    r"|NOK|DKK|BRL|MXN|ZAR|KRW|PLN|ILS|AED|SAR|TRY|RUB|THB|IDR"
    r"|MYR|PHP|VND|EGP|NGN|TWD|CZK|HUF|RON|CLP|COP|ARS|PKR|BDT)(?![A-Za-z])"
    r"|\b(?:euros?|pounds?\s+sterling|sterling|yen|yuan|rupees?|won"
    r"|rand|reais|reals?|shekels?|dirhams?|kron[ao]r?|zloty|ruble[s]?"
    r"|lakh|crore)\b",
    re.I,
)

# --- The dollar, written as a word ------------------------------------------
#
# Every form below is an inflection of "dollar" in a language the catalogue
# wires, listed rather than stemmed for the reason SCALE_WORDS_BY_LANGUAGE is
# listed rather than stemmed: a loose stem match puts `doler` (Spanish, to hurt)
# and `dolerite` (a rock) inside the currency, and this file has already paid
# once for a stem that matched more than it meant.
DOLLAR_WORDS = (
    # English, Italian, Estonian, Finnish
    "dollar", "dollars", "dollari", "dollaria", "dollarit", "dollaria",
    # Spanish, Portuguese, Slovak, Hungarian
    "dólar", "dólares", "dolar", "dolares", "dolár", "doláru", "dolárov",
    "dollár", "dollárt", "dollárok",
    # Turkish (doları / dolarları), Indonesian, Malay, Polish, Czech,
    # Croatian / Serbian / Bosnian, Romanian, Lithuanian, Latvian
    "doları", "dolari", "dolarları", "dolarlar", "dolarów", "dolarow",
    "dolarů", "dolaru", "dolary", "dolara", "dolare", "dolarima",
    "doleris", "doleriai", "dolāri", "dolāru",
    # Russian, Ukrainian, Bulgarian, Macedonian, Serbian Cyrillic
    "доллар", "доллара", "долларов", "доларів", "долар", "долара", "долари",
    "долары",
    # Greek
    "δολάριο", "δολάρια", "δολαρίων",
    # Albanian. Vietnamese is deliberately absent: it writes "đô la", two
    # tokens, and the bare "đô" is far too short to admit as a currency.
    "dollarë", "dollarësh",
)

#: Hebrew and Arabic glue their clitics on, so the dollar word needs the same
#: boundary the Hebrew prefilter block spells out rather than a `\b`.
_DOLLAR_WORDS_RTL = (
    r"(?<![א-ת])[והבלכמש]{0,2}דולר(?:ים)?(?![א-ת])",
    r"(?<![؀-ۿ])(?:ال|وال|بال)?دولار(?:ات|ا)?(?![؀-ۿ])",
)

#: Scripts that write the currency glued to the number, so no boundary exists
#: to assert. 美元 and 美金 name the US dollar outright ("American money"), which
#: is why the Chinese entries need no qualifier veto below: 港元, 加元 and 澳元
#: are different words rather than qualified forms of this one.
_DOLLAR_WORDS_GLUED = ("ドル", "달러", "美元", "美金")

# ...and the qualifiers that make it SOMEBODY ELSE'S dollar.
#
# This is the word-shaped sibling of the `(?:HK|NZ|C|A|S)\s?\$` arm of _NON_USD,
# and it carries the same honest limit: a Canadian round reported in Canadian
# dollars without the word "Canadian" anywhere in the amount string reads as US
# dollars, exactly as a bare `$` always has. The list is the dollars that
# actually appear in the business copy these feeds carry; it is not every dollar
# in ISO 4217, and it does not pretend to be.
_DOLLAR_QUALIFIERS_BEFORE = (
    r"canadian", r"australian", r"singapore(?:an)?", r"hong\s*kong", r"new\s*zealand",
    r"taiwan(?:ese)?", r"jamaican", r"namibian", r"fijian", r"brunei(?:an)?",
    r"liberian", r"guyanese", r"barbadian", r"bahamian", r"belize(?:an)?",
    r"caribbean", r"trinidad\w*", r"zimbabwe(?:an)?", r"surinamese", r"solomon",
    # German and Dutch put the adjective in front
    r"kanadische\w*", r"australische\w*", r"neuseel\w+", r"hongkong\w*",
    r"singapur\w*", r"taiwanische\w*", r"canadese", r"australische",
    # Turkish, Indonesian and Malay put the bare country name in front
    r"kanada", r"avustralya", r"yeni\s*zelanda", r"singapura?", r"tayvan",
    r"australia", r"brunei",
)
_DOLLAR_QUALIFIERS_AFTER = (
    r"canadiens?", r"canadiennes?", r"canadienses?", r"canadenses?",
    r"australiens?", r"australiennes?", r"australianos?", r"australians?",
    r"singapouriens?", r"singapurenses?", r"singapore",
    r"n[ée]o-?z[ée]landais\w*", r"neozeland[eé]s\w*", r"neozelandeses?",
    r"taiwan[eé]s\w*", r"taiwan", r"hong\s*kong", r"jamaicanos?",
    r"kanadsk\w+", r"australsk\w+", r"kanadyjski\w*", r"australijski\w*",
)
_DOLLAR_QUALIFIERS_GLUED = (
    "カナダドル", "豪ドル", "加ドル", "香港ドル", "台湾ドル", "NZドル",
    "ニュージーランドドル", "シンガポールドル", "シンガポール・ドル",
    "홍콩 달러", "캐나다 달러", "호주 달러", "싱가포르 달러", "대만 달러",
    "홍콩달러", "캐나다달러", "호주달러", "싱가포르달러",
)

_DOLLAR_WORD_ALT = "|".join(re.escape(w) for w in DOLLAR_WORDS)
_DOLLAR_GLUED_ALT = "|".join(re.escape(w) for w in _DOLLAR_WORDS_GLUED)

#: Every way this module will accept "a dollar was named in words".
_DOLLAR_WORD_PATTERNS = (
    (r"\b(?:%s)\b" % _DOLLAR_WORD_ALT,)
    + _DOLLAR_WORDS_RTL
    + (_DOLLAR_GLUED_ALT,)
)

#: A dollar word that a qualifier has claimed for another country. Checked
#: beside _NON_USD, so it refuses the figure rather than converting it.
_QUALIFIED_DOLLAR = re.compile(
    r"(?:%s)[\s\-·]{0,2}(?:%s)"
    % ("|".join(_DOLLAR_QUALIFIERS_BEFORE), _DOLLAR_WORD_ALT)
    + r"|(?:%s)\s+(?:de\s+|dos\s+|d[ae]\s+|di\s+)?(?:%s)"
    % (_DOLLAR_WORD_ALT, "|".join(_DOLLAR_QUALIFIERS_AFTER))
    + r"|(?:%s)" % "|".join(re.escape(q) for q in _DOLLAR_QUALIFIERS_GLUED),
    re.I | re.UNICODE,
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
#
# WHAT THIS RULE DOES *NOT* MEAN, added 2026-08-04. "Stated" was implemented as
# "written with the symbol $, the prefix US$ or the code USD", and a headline
# that writes the currency out in words states it just as plainly. Six real
# strings measured that day, every one of them a 2026 AI mega-round:
#
#   '122.000 millones de dólares'  '65 milliards de dollars'  '30 Mrd. Dollar'
#   '650億ドル'                     '965 מיליארד דולר'          '300亿美元'
#
# All six returned None, so the rows carrying them stored with
# funding_amount_usd NULL: absent from every money chart, from "sort by raised",
# and from the amount arm of funding_event_duplicate. The parser was not
# refusing to guess - the source had told it, in words, and the reader could not
# read the word.
#
# So a dollar WORD counts, on exactly the same terms the '$' sign already
# counts. That is the honest symmetry: a bare '$' is written by Canada,
# Australia, Singapore, Hong Kong and a dozen others, and this module has always
# accepted a bare '$' as a US dollar. A bare "dollars" is ambiguous in precisely
# the same way and to precisely the same degree, so it is admitted the same way
# - and the NAMED other dollars are vetoed the same way too, by
# _QUALIFIED_DOLLAR below, which is the word-shaped sibling of the `C$|A$|S$`
# arm of _NON_USD.
#
# It cannot widen anything else. A string naming no currency at all still
# returns None, which keeps the three rows this positive test was built for
# ('500 millones', '25 millioner kroner', '10,5 mio. kr.') refusing exactly as
# before, and keeps the live '93.175 millones' row NULL rather than guessing.
_USD_MARKER = re.compile(
    r"(?i)\bUSD(?![A-Za-z])|\bUS\s*\$|(?<![A-Za-z])\$"
    r"|(?:%s)" % "|".join(_DOLLAR_WORD_PATTERNS)
)

# --- The scale word, in every language the catalogue wires -------------------
#
# `$190 Milyon Dolar` was stored as ONE HUNDRED AND NINETY DOLLARS. Turkish for
# a million was not in the table, the token fell through to no multiplier at
# all, and a nine-figure round landed on the money chart as pocket change. Four
# rows went that way in one collection, and the mechanism is not Turkish: 575
# national press feeds across 139 countries were wired into a parser whose scale
# vocabulary was English with a handful of Romance words bolted on.
#
# So the vocabulary is now declared PER LANGUAGE, derived from the language
# column of data/sources_catalogue.csv rather than from whichever string last
# broke. tests/test_funding_amount_parsing.py reads that CSV and fails if a
# wired language is neither covered here nor named in UNCOVERED_LANGUAGES with
# a reason. A partial vocabulary fails silently and looks like sparse data —
# that is the lesson the figure-guard measurement wrote down on 2026-07-30, and
# this is the structure that makes the gap visible instead.
#
# Three things carried over from the Hebrew/Czech/Danish prefilter work, and all
# three shaped the code below rather than only the word lists:
#
#   1. Word boundaries are not universal. `\b` is meaningless in Chinese,
#      Japanese, Korean and Thai, which put no space between the number, the
#      scale word and the currency: `1亿美元` is one token to a regex engine, and
#      `亿\b` can never match it because 美 is a word character. Those scripts get
#      GLUED_SCALE, matched as a prefix with no boundary assertion at all. The
#      space-delimited scripts are matched by taking the WHOLE letter run after
#      the number and looking it up, which is a boundary that cannot be got
#      wrong and which also kills the ordering trap below.
#   2. Hebrew and Arabic glue clitics onto the FRONT of a word, and they are
#      word characters, so `מיליון` is often written `כמיליון`. A short list of
#      those prefixes is stripped before lookup, and only when what is left is a
#      word we know.
#   3. A regex alternative ending in a magnitude word can silently never match —
#      `mil` shadowing `milyon` inside one alternation is exactly how the
#      Turkish rows were lost, since `mil` matched, the boundary failed, and the
#      optional group settled for nothing. There is no alternation here any
#      more. The letter run is read once and looked up in a dict.
#
# A widened vocabulary CANNOT turn a foreign amount into a dollar figure: every
# path below runs only after _USD_MARKER has already found '$', 'US$' or 'USD'
# in the string. It can only stop 'USD 53 millones' being stored as fifty-three
# dollars.

_THOUSAND = 1_000
_MILLION = 1_000_000
_MILLIARD = 1_000_000_000
_TRILLION = 1_000_000_000_000

# Which separator a language writes a DECIMAL with. '.' means the English
# convention (dot decimal, comma thousands); ',' means the continental one
# (comma decimal, dot thousands); None means we do not claim to know, and the
# number falls back to the shape heuristic in _read_number.
#
# This is the second half of the same defect. `$150.000` from an Indonesian
# publisher is one hundred and fifty THOUSAND dollars, and an English-tuned
# reader stored 150. The mirror-image error is just as available: `1,5 milyon`
# is one and a half million, and stripping the comma makes it fifteen.
_DOT_DECIMAL = "."
_COMMA_DECIMAL = ","

_LANGUAGE_DECIMAL = {
    "Albanian": _COMMA_DECIMAL, "Arabic": None, "Bengali": _DOT_DECIMAL,
    "Bosnian": _COMMA_DECIMAL, "Bulgarian": _COMMA_DECIMAL,
    "Chinese": _DOT_DECIMAL, "Croatian": _COMMA_DECIMAL,
    "Czech": _COMMA_DECIMAL, "Danish": _COMMA_DECIMAL, "Dutch": _COMMA_DECIMAL,
    "English": _DOT_DECIMAL, "Estonian": _COMMA_DECIMAL,
    "Finnish": _COMMA_DECIMAL, "French": _COMMA_DECIMAL,
    "German": _COMMA_DECIMAL, "Greek": _COMMA_DECIMAL, "Hebrew": _DOT_DECIMAL,
    "Hungarian": _COMMA_DECIMAL, "Icelandic": _COMMA_DECIMAL,
    "Indonesian": _COMMA_DECIMAL, "Italian": _COMMA_DECIMAL,
    "Japanese": _DOT_DECIMAL, "Kinyarwanda": None, "Korean": _DOT_DECIMAL,
    "Kurdish": _COMMA_DECIMAL, "Latvian": _COMMA_DECIMAL,
    "Lithuanian": _COMMA_DECIMAL, "Macedonian": _COMMA_DECIMAL,
    "Maltese": _DOT_DECIMAL, "Montenegrin": _COMMA_DECIMAL,
    "Nepali": _DOT_DECIMAL, "Norwegian": _COMMA_DECIMAL,
    "Polish": _COMMA_DECIMAL, "Portuguese": _COMMA_DECIMAL,
    "Romanian": _COMMA_DECIMAL, "Russian": _COMMA_DECIMAL,
    "Serbian": _COMMA_DECIMAL, "Slovak": _COMMA_DECIMAL,
    "Slovenian": _COMMA_DECIMAL, "Spanish": _COMMA_DECIMAL,
    "Swahili": _DOT_DECIMAL, "Swedish": _COMMA_DECIMAL, "Thai": _DOT_DECIMAL,
    "Turkish": _COMMA_DECIMAL, "Ukrainian": _COMMA_DECIMAL,
    "Uzbek": _COMMA_DECIMAL, "Vietnamese": _COMMA_DECIMAL,
}

# The scale words themselves, keyed by the language name the catalogue uses.
#
# Inflection is why these are lists rather than stems: Latvian alone writes
# miljons / miljoni / miljonu / miljonus / miljoniem, and all five came off the
# live Latvian feed in one fetch. A stem match with a loose tail would also
# catch `milionário`, and the prefilter work already paid for that lesson once
# (bare `investice` gave nine false positives in fifteen). Every form below was
# either read off a wired feed on 2026-07-30 or is the dictionary citation form
# of one that was.
SCALE_WORDS_BY_LANGUAGE = {
    "Albanian": {"milion": _MILLION, "milionë": _MILLION,
                 "milionesh": _MILLION, "milionësh": _MILLION,
                 "miliard": _MILLIARD, "miliardë": _MILLIARD,
                 "mije": _THOUSAND, "mijë": _THOUSAND},
    # Arabic plurals are broken rather than suffixed, so both stems are listed.
    "Arabic": {"مليون": _MILLION, "ملايين": _MILLION,
               "مليار": _MILLIARD, "مليارات": _MILLIARD,
               "تريليون": _TRILLION, "ألف": _THOUSAND, "الف": _THOUSAND},
    "Bengali": {"মিলিয়ন": _MILLION, "বিলিয়ন": _MILLIARD},
    "Bosnian": {"milion": _MILLION, "miliona": _MILLION, "milione": _MILLION,
                "milijun": _MILLION, "milijuna": _MILLION,
                "milijarda": _MILLIARD, "milijardi": _MILLIARD,
                "milijarde": _MILLIARD, "hiljada": _THOUSAND},
    "Bulgarian": {"милион": _MILLION, "милиона": _MILLION, "млн": _MILLION,
                  "милиард": _MILLIARD, "милиарда": _MILLIARD,
                  "млрд": _MILLIARD, "хиляди": _THOUSAND},
    # Chinese counts in ten-thousands, which is the whole reason GLUED_SCALE
    # exists: 亿 is 10^8, not a billion, and `1亿美元` has no space anywhere in it.
    "Chinese": {"万": 10_000, "萬": 10_000, "千万": 10_000_000,
                "百万": _MILLION, "亿": 100_000_000, "億": 100_000_000,
                "十亿": _MILLIARD, "兆": _TRILLION, "千": _THOUSAND},
    "Croatian": {"milijun": _MILLION, "milijuna": _MILLION,
                 "milijuni": _MILLION, "milijarda": _MILLIARD,
                 "milijardi": _MILLIARD, "tisuća": _THOUSAND},
    "Czech": {"milion": _MILLION, "milionu": _MILLION, "milionů": _MILLION,
              "miliony": _MILLION, "miliónů": _MILLION,
              "miliarda": _MILLIARD, "miliardy": _MILLIARD,
              "miliard": _MILLIARD, "tisíc": _THOUSAND},
    "Danish": {"million": _MILLION, "millioner": _MILLION, "mio": _MILLION,
               "milliard": _MILLIARD, "milliarder": _MILLIARD,
               "mia": _MILLIARD, "tusinde": _THOUSAND},
    "Dhivehi": {"މިލިއަން": _MILLION},
    "Dutch": {"miljoen": _MILLION, "mln": _MILLION, "miljard": _MILLIARD,
              "mld": _MILLIARD, "duizend": _THOUSAND},
    "English": {"thousand": _THOUSAND, "k": _THOUSAND,
                "m": _MILLION, "mm": _MILLION, "mn": _MILLION,
                "million": _MILLION, "millions": _MILLION,
                "b": _MILLIARD, "bn": _MILLIARD,
                "billion": _MILLIARD, "billions": _MILLIARD,
                "t": _TRILLION, "tn": _TRILLION, "trillion": _TRILLION},
    "Estonian": {"miljon": _MILLION, "miljonit": _MILLION,
                 "miljardit": _MILLIARD, "miljard": _MILLIARD,
                 "tuhat": _THOUSAND},
    "Finnish": {"miljoona": _MILLION, "miljoonaa": _MILLION,
                "miljoonan": _MILLION, "miljardi": _MILLIARD,
                "miljardia": _MILLIARD, "tuhatta": _THOUSAND},
    "French": {"million": _MILLION, "millions": _MILLION,
               "milliard": _MILLIARD, "milliards": _MILLIARD,
               "mille": _THOUSAND},
    "German": {"million": _MILLION, "millionen": _MILLION, "mio": _MILLION,
               "milliarde": _MILLIARD, "milliarden": _MILLIARD,
               "mrd": _MILLIARD, "mia": _MILLIARD, "tausend": _THOUSAND},
    "Greek": {"εκατομμύριο": _MILLION, "εκατομμύρια": _MILLION,
              "εκατομμυρίων": _MILLION, "εκατ": _MILLION,
              "δισεκατομμύριο": _MILLIARD, "δισεκατομμύρια": _MILLIARD,
              "δισεκατομμυρίων": _MILLIARD, "δισ": _MILLIARD,
              "χιλιάδες": _THOUSAND},
    "Hebrew": {"מיליון": _MILLION, "מיליוני": _MILLION,
               "מיליארד": _MILLIARD, "מיליארדי": _MILLIARD,
               "טריליון": _TRILLION, "אלף": _THOUSAND, "אלפי": _THOUSAND},
    "Hungarian": {"millió": _MILLION, "milliót": _MILLION,
                  "millióval": _MILLION, "milliárd": _MILLIARD,
                  "milliárdot": _MILLIARD, "ezer": _THOUSAND},
    "Icelandic": {"milljón": _MILLION, "milljónir": _MILLION,
                  "milljóna": _MILLION, "milljarður": _MILLIARD,
                  "milljarðar": _MILLIARD, "milljarða": _MILLIARD,
                  "þúsund": _THOUSAND},
    "Indonesian": {"juta": _MILLION, "jt": _MILLION, "miliar": _MILLIARD,
                   "milyar": _MILLIARD, "triliun": _TRILLION,
                   "ribu": _THOUSAND},
    "Italian": {"milione": _MILLION, "milioni": _MILLION, "mln": _MILLION,
                "miliardo": _MILLIARD, "miliardi": _MILLIARD,
                "mld": _MILLIARD, "mila": _THOUSAND},
    "Japanese": {"万": 10_000, "百万": _MILLION, "千万": 10_000_000,
                 "億": 100_000_000, "十億": _MILLIARD, "兆": _TRILLION,
                 "千": _THOUSAND},
    "Kinyarwanda": {"miliyoni": _MILLION, "miliyari": _MILLIARD},
    "Korean": {"만": 10_000, "백만": _MILLION, "억": 100_000_000,
               "십억": _MILLIARD, "조": _TRILLION, "천": _THOUSAND},
    "Kurdish": {"milyon": _MILLION, "milyar": _MILLIARD},
    "Latvian": {"miljons": _MILLION, "miljoni": _MILLION, "miljonu": _MILLION,
                "miljonus": _MILLION, "miljoniem": _MILLION,
                "miljards": _MILLIARD, "miljardi": _MILLIARD,
                "miljardu": _MILLIARD, "miljardus": _MILLIARD,
                "tūkstoši": _THOUSAND},
    "Lithuanian": {"milijonas": _MILLION, "milijono": _MILLION,
                   "milijonų": _MILLION, "mln": _MILLION,
                   "milijardas": _MILLIARD, "milijardų": _MILLIARD,
                   "mlrd": _MILLIARD, "tūkst": _THOUSAND},
    "Macedonian": {"милион": _MILLION, "милиони": _MILLION,
                   "милиона": _MILLION, "милијарда": _MILLIARD,
                   "милијарди": _MILLIARD, "илјади": _THOUSAND},
    "Maltese": {"miljun": _MILLION, "miljuni": _MILLION, "elf": _THOUSAND},
    "Montenegrin": {"milion": _MILLION, "miliona": _MILLION,
                    "milijarda": _MILLIARD, "milijardi": _MILLIARD,
                    "hiljada": _THOUSAND},
    "Nepali": {"मिलियन": _MILLION, "बिलियन": _MILLIARD},
    "Norwegian": {"million": _MILLION, "millioner": _MILLION,
                  "mill": _MILLION, "milliard": _MILLIARD,
                  "milliarder": _MILLIARD, "mrd": _MILLIARD,
                  "tusen": _THOUSAND},
    "Polish": {"milion": _MILLION, "miliona": _MILLION, "milionów": _MILLION,
               "mln": _MILLION, "miliard": _MILLIARD,
               "miliardów": _MILLIARD, "mld": _MILLIARD,
               "tysięcy": _THOUSAND, "tys": _THOUSAND},
    # Brazilian spellings only. Portugal writes `bilião` for 10^12 and `mil
    # milhões` for 10^9, so `bilião`/`biliões` are refused rather than read —
    # see AMBIGUOUS_SCALE_WORDS. The wired Portuguese feeds are 15 Brazilian to
    # 3 Portuguese, and `bi` off BitNotícias and EuQueroInvestir is Brazilian.
    "Portuguese": {"milhão": _MILLION, "milhões": _MILLION,
                   "milhao": _MILLION, "milhoes": _MILLION,
                   "bilhão": _MILLIARD, "bilhões": _MILLIARD,
                   "bilhao": _MILLIARD, "bilhoes": _MILLIARD,
                   "bi": _MILLIARD, "trilhão": _TRILLION,
                   "trilhões": _TRILLION},
    "Romanian": {"milion": _MILLION, "milioane": _MILLION,
                 "miliard": _MILLIARD, "miliarde": _MILLIARD,
                 "mii": _THOUSAND},
    "Russian": {"миллион": _MILLION, "миллиона": _MILLION,
                "миллионов": _MILLION, "млн": _MILLION,
                "миллиард": _MILLIARD, "миллиарда": _MILLIARD,
                "миллиардов": _MILLIARD, "млрд": _MILLIARD,
                "триллион": _TRILLION, "трлн": _TRILLION,
                "тысяч": _THOUSAND},
    "Serbian": {"milion": _MILLION, "miliona": _MILLION, "милион": _MILLION,
                "милиона": _MILLION, "milijarda": _MILLIARD,
                "milijardi": _MILLIARD, "милијарда": _MILLIARD,
                "милијарди": _MILLIARD, "hiljada": _THOUSAND,
                "хиљада": _THOUSAND},
    "Slovak": {"milión": _MILLION, "milióna": _MILLION,
               "miliónov": _MILLION, "miliarda": _MILLIARD,
               "miliardy": _MILLIARD, "miliárd": _MILLIARD,
               "tisíc": _THOUSAND},
    "Slovenian": {"milijon": _MILLION, "milijona": _MILLION,
                  "milijonov": _MILLION, "milijarda": _MILLIARD,
                  "milijard": _MILLIARD, "tisoč": _THOUSAND},
    # `billón`/`billones` are 10^12 in Spanish and are NOT here; see
    # AMBIGUOUS_SCALE_WORDS for why they refuse rather than pick.
    "Spanish": {"millón": _MILLION, "millon": _MILLION,
                "millones": _MILLION, "milliones": _MILLION,
                "millardo": _MILLIARD, "millardos": _MILLIARD},
    "Swahili": {"milioni": _MILLION, "bilioni": _MILLIARD, "elfu": _THOUSAND},
    "Swedish": {"miljon": _MILLION, "miljoner": _MILLION,
                "miljard": _MILLIARD, "miljarder": _MILLIARD,
                "mdr": _MILLIARD, "tusen": _THOUSAND},
    # Thai writes no spaces and its scale words carry combining marks, which
    # \w does not match, so \b cannot be used at any point in ล้าน. GLUED_SCALE.
    "Thai": {"ล้าน": _MILLION, "พันล้าน": _MILLIARD,
             "ล้านล้าน": _TRILLION, "หมื่น": 10_000, "แสน": 100_000,
             "พัน": _THOUSAND},
    "Turkish": {"milyon": _MILLION, "milyar": _MILLIARD,
                "trilyon": _TRILLION, "bin": _THOUSAND},
    "Ukrainian": {"мільйон": _MILLION, "мільйона": _MILLION,
                  "мільйонів": _MILLION, "млн": _MILLION,
                  "мільярд": _MILLIARD, "мільярда": _MILLIARD,
                  "мільярдів": _MILLIARD, "млрд": _MILLIARD,
                  "тисяч": _THOUSAND},
    "Uzbek": {"million": _MILLION, "milliard": _MILLIARD, "ming": _THOUSAND},
    "Vietnamese": {"triệu": _MILLION, "tỷ": _MILLIARD, "tỉ": _MILLIARD,
                   "nghìn": _THOUSAND, "ngàn": _THOUSAND},
}

#: Languages in data/sources_catalogue.csv that have NO scale vocabulary here,
#: and why. Named rather than omitted, because an unlisted language is
#: indistinguishable from an oversight and the whole point of deriving the list
#: from the catalogue is that a gap has to be visible.
UNCOVERED_LANGUAGES = {
    "Oshiwambo": "one feed, New Era (Namibia), whose money copy is the English "
                 "half of an English/Oshiwambo masthead; no Oshiwambo scale "
                 "word has ever appeared in a fetched headline",
}

#: A scale word whose meaning depends on which language the publisher was
#: writing. No reading is safe, so the parser REFUSES and leaves the verbatim
#: string on the row. This is the standing rule of the file: a figure only
#: exists if the source states it, and a thousand-fold error on a summed total
#: is worse than an absent figure.
#:
#: Two of these are here from earlier sweeps and stay:
#:   `mil`  a million in Singapore and Malaysian English ('US$22 mil in
#:          pre-Series A') and a THOUSAND in Spanish and Portuguese.
#:   `mi`   milhões in Brazilian business press ('US$ 544 mi'), and a guess
#:          anywhere else.
#:
#: The rest are the long-scale trap, and two of them were WRONG in the table
#: this replaces rather than merely missing. `billones` and `billioner` were
#: mapped to 10^9. A Spanish billón and a Danish billion are 10^12, so those two
#: entries were a thousand-fold understatement waiting for its first row —
#: exactly the defect this whole pass is about, pointing the other way. The
#: comment that put them there had the diagnosis right and the conclusion
#: backwards: it is `billion`, not `milliard`, that means different things in
#: different languages.
#:
#: Which is why the milliard family is now READ rather than refused. `milliard`,
#: `miliard`, `milyar`, `miljard`, `Milliarde`, `mia`, `mld`, `mrd`, `млрд`,
#: `مليار` and `מיליארד` are 10^9 in every language that has the word — there is
#: no long-scale/short-scale disagreement about milliard anywhere, and there
#: never was. The earlier note excluded it alongside `billón`, whose ambiguity
#: is real, and inherited the refusal by association. `$190 Milyar Dolar` is a
#: hundred and ninety billion dollars in Turkish and in Turkish only, and
#: refusing it left the same hole `milyon` left.
AMBIGUOUS_SCALE_WORDS = {
    "mil": "a million in Singapore and Malaysian English, a thousand in "
           "Spanish and Portuguese",
    "mi": "milhões in Brazilian Portuguese, and a guess in any other language",
    "billón": "10^12 in Spanish, though Latin American copy calques the "
              "English 10^9 often enough that neither reading is safe",
    "billon": "unaccented billón, same problem",
    "billones": "as billón; it was mapped to 10^9 here, which is the same "
                "thousand-fold error this pass exists to remove",
    "billioner": "10^12 in Danish and Swedish; also previously mapped to 10^9",
    "billionen": "10^12 in German, and spelled almost exactly like the English "
                 "10^9",
    "bilião": "10^12 in European Portuguese, against bilhão at 10^9 in Brazil",
    "biliões": "as bilião",
    "trillón": "10^18 in Spanish long scale, against the English 10^12",
    "billiard": "10^15 where it is used at all, and read as a typo for "
                "milliard everywhere else",
}

#: Collisions between two languages that are resolved rather than refused, and
#: the reason each is safe. Everything else that two languages disagree about is
#: added to the refusal set automatically at import; see _build_scale_table.
#:
#: `m` and `t` are the two that matter. Indonesian writes `Rp5 M` for five
#: miliar (10^9) and `Rp2,35 T` for triliun, so both collide with the English
#: 10^6 and 10^12. They resolve to English because the string has already had to
#: state US DOLLARS to get this far, and an Indonesian desk writing a dollar
#: figure writes `US$5 juta`, never `US$5 M` — the M and T abbreviations belong
#: to rupiah. Both are listed here rather than silently, so a future Indonesian
#: dollar row in that shape has somewhere to be argued about.
RESOLVED_SCALE_COLLISIONS = {
    "m": (_MILLION, "English 10^6 over Indonesian miliar; the rupiah "
                    "abbreviation never carries a dollar sign"),
    "t": (_TRILLION, "English 10^12 over Indonesian triliun, which is also "
                     "10^12 — the collision is nominal"),
    "mia": (_MILLIARD, "Danish and German abbreviation for milliard; no other "
                       "wired language uses the token"),
    "mln": (_MILLION, "Dutch, Italian, Lithuanian and Polish all write 10^6"),
    "mld": (_MILLIARD, "Dutch and Italian both write 10^9"),
    "mrd": (_MILLIARD, "German and Norwegian both write 10^9"),
}


def _build_scale_table():
    """Flatten the per-language vocabulary into one lookup, refusing conflicts.

    Two languages that disagree about a token do not get to have the argument
    settled by dict ordering. A token claimed with two different multipliers is
    added to the refusal set unless RESOLVED_SCALE_COLLISIONS says which reading
    wins and why. That is the mechanism that would have caught `billones` on the
    day Spanish was wired, and it is the reason adding a language cannot quietly
    change what an existing token means.

    Returns (scale, decimal, ambiguous, glued):
      scale     token -> multiplier
      decimal   token -> '.' | ',' | None, the writer's decimal separator
      ambiguous tokens that refuse
      glued     [(token, multiplier, decimal)], longest first, for the scripts
                that put no space between the number and the word
    """
    claims = {}
    for language, words in SCALE_WORDS_BY_LANGUAGE.items():
        for token, multiplier in words.items():
            claims.setdefault(token.lower(), {}).setdefault(multiplier, set()
                                                            ).add(language)

    scale, decimal, ambiguous = {}, {}, set(AMBIGUOUS_SCALE_WORDS)
    for token, readings in claims.items():
        if token in ambiguous:
            continue
        if len(readings) > 1:
            resolved = RESOLVED_SCALE_COLLISIONS.get(token)
            if resolved is None:
                ambiguous.add(token)
                continue
            multiplier = resolved[0]
            languages = readings.get(multiplier, set())
        else:
            (multiplier, languages), = readings.items()
        scale[token] = multiplier
        # The decimal convention only carries when every language claiming the
        # token agrees. 'million' is English and also Danish, French, German
        # and Norwegian, and those write the decimal separator differently, so
        # the token says nothing about it and _read_number falls back to shape.
        conventions = {_LANGUAGE_DECIMAL.get(lang) for lang in languages}
        decimal[token] = conventions.pop() if len(conventions) == 1 else None

    glued = []
    for language in _GLUED_SCRIPT_LANGUAGES:
        for token in SCALE_WORDS_BY_LANGUAGE[language]:
            key = token.lower()
            if key in scale:
                glued.append((token, scale[key], decimal[key]))
    # Longest first, so 百万 is not read as 万 and พันล้าน is not read as พัน.
    glued.sort(key=lambda item: -len(item[0]))
    return scale, decimal, ambiguous, glued


#: The scripts that write a number, its scale word and its currency as one
#: unbroken run of word characters. `\b` cannot separate them and neither can a
#: letter-run, so these are matched as plain prefixes with no boundary at all.
_GLUED_SCRIPT_LANGUAGES = ("Chinese", "Japanese", "Korean", "Thai")

_SCALE, _SCALE_DECIMAL, _AMBIGUOUS_SCALE, _GLUED_SCALE = _build_scale_table()

#: Single-letter prefixes that Hebrew glues onto the front of a noun (and, the,
#: in, to, from, that, about), plus the Arabic definite article. Stripped only
#: when what remains is a scale word we already know, which is the narrow form
#: of the rule the prefilter work landed on: a bare substring match puts
#: `salary` inside `a rental`, and this does not.
_CLITIC_PREFIXES = ("ו", "ה", "ב", "ל", "מ", "כ", "ש", "ال")

#: The first number in the string, in any script's decimal digits. Groups may be
#: separated by a dot, a comma or a space — French and Polish write 1 500 000,
#: and NBSP and the narrow no-break space are what a CMS actually emits.
_NUMBER = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?"
                     r"|\d[\d.,]*\d"
                     r"|\d")

#: What may sit between the number and its scale word. The multiplier may be
#: attached by a hyphen as well as by a space: BetaKit writes '$20-million USD',
#: and \s* does not match '-', so that round was stored as twenty dollars. En
#: and em dashes too, because a publisher's typographer may have been through it.
_SCALE_GAP = re.compile(r"[\s ]*[-‐-―]?[\s ]*")

#: A run of letters in any script, optionally closed by the abbreviation dot
#: that `mio.`, `mln.`, `млн.` and `εκατ.` are usually written with.
_LETTER_RUN = re.compile(r"[^\W\d_]+", re.UNICODE)


def _scale_after(tail: str):
    """Read the scale word sitting immediately after a number.

    Returns (multiplier, decimal separator) or None for "no scale word here",
    and raises _Refuse for a word we know we cannot read.

    Taking the WHOLE letter run and looking it up is the boundary. It cannot be
    got wrong the way `\\b` can, and it removes the ordering trap that lost the
    Turkish rows: in an alternation, `mil` matches the front of `milyon`, the
    boundary then fails, and an optional group settles for no multiplier at all.
    A dict lookup of `milyon` has no such failure mode.
    """
    rest = _SCALE_GAP.sub("", tail, count=1) if tail else ""
    if not rest:
        return None

    # Glued scripts first: their tokens sit inside a letter run that would
    # otherwise swallow the currency word with them (`亿美元`, `ล้านบาท`).
    for token, multiplier, convention in _GLUED_SCALE:
        if rest.startswith(token):
            return multiplier, convention

    match = _LETTER_RUN.match(rest)
    if not match:
        return None
    word = match.group(0).lower()

    if word in _AMBIGUOUS_SCALE:
        raise _Refuse(word)
    if word in _SCALE:
        return _SCALE[word], _SCALE_DECIMAL[word]

    # Hebrew and Arabic clitics, and only when the remainder is a known word.
    for prefix in _CLITIC_PREFIXES:
        if word.startswith(prefix):
            stem = word[len(prefix):]
            if stem in _AMBIGUOUS_SCALE:
                raise _Refuse(stem)
            if stem in _SCALE:
                return _SCALE[stem], _SCALE_DECIMAL[stem]
    return None


class _Refuse(Exception):
    """A scale word we can see and deliberately will not read."""


#: A number whose separators can be read as thousands GROUPS: a leading run of
#: one to three digits, then groups of exactly three. '1.500.000' qualifies and
#: '1234,567' does not, which is what stops a four-digit head being read as a
#: thousands group it cannot be.
_THOUSAND_GROUPS = {
    ".": re.compile(r"\d{1,3}(?:\.\d{3})+$"),
    ",": re.compile(r"\d{1,3}(?:,\d{3})+$"),
}


def _read_number(raw: str, convention: str | None, scaled: bool = False):
    """'1,450' -> 1450.0 and '10,5' -> 10.5, deciding which separator is which.

    Three rules, and the ORDER of them is the whole design.

    The first is shape, and it holds under BOTH conventions rather than
    assuming one. A lone separator followed by exactly three digits is a
    thousands group: that is what it means in English, and continental copy does
    not pad a decimal fraction to three places either -- Spanish writes '1,5
    millones' and '1.500 millones', never '1,500 millones' for one and a half.
    Anything other than a three-digit tail is a decimal fraction, again in both
    conventions, because a thousands separator always leaves exactly three
    digits behind it. This is what makes an Indonesian '$150.000' a hundred and
    fifty THOUSAND rather than a hundred and fifty, and it needs no locale.

    The second is `convention`: the decimal separator written by the language of
    the scale word beside the number, where that word named one language. It is
    consulted only where the first rule and it DISAGREE -- a three-digit group
    under the separator that this publisher's language writes decimals with --
    and the answer there is not to pick. 'US$ 1,500 milhoes' is one and a half
    million to a Brazilian desk and fifteen hundred million to an English one,
    the two readings are a thousand-fold apart, and the string holds nothing
    that settles it. It raises _Refuse, and the row keeps its verbatim amount
    and no dollar figure. That is this project's standing rule: a figure only
    exists if the source states it, and $150.000 read as 150 is worse than NULL
    because NULL is visibly missing while 150 looks like data.

    The third is `scaled`, and it exists because the FIRST rule's premise is
    false in exactly one place. "Continental copy does not pad a decimal
    fraction to three places" holds for a bare amount, and it is what makes
    '$150.000' a hundred and fifty thousand. It does not hold when a SCALE WORD
    follows, where three decimal places is the ordinary English way to write
    money: '$1.265 Million' is one million two hundred and sixty-five thousand
    dollars, and reading its dot as a thousands group put a $1.265 BILLION seed
    round on the live page (row 31228, RevaTerra, citybiz).

    So where a scale word follows and the token named no single language, the
    fallback is the ENGLISH convention rather than the shape: '.' is the decimal
    point and ',' is the thousands separator. That is not a new guess -- it is
    the reading this module already gives the mirror image, '$1,500 million' ->
    1.5bn, asserted since 2026-07-30. A language that WOULD write '1.265
    Millionen' and mean 1265 million names itself in its scale word ('millionen'
    is German alone, decimal ','), which reaches `convention` above and keeps
    the thousands reading. Nothing changes for a string with no scale word.
    """
    text = raw.strip()
    for space in ("\u0020", "\u00a0", "\u202f"):
        # A space is only ever a thousands separator. No locale writes a
        # decimal fraction after one.
        text = text.replace(space, "")

    has_dot, has_comma = "." in text, "," in text
    if not has_dot and not has_comma:
        return float(text)

    if has_dot and has_comma:
        # Both present: whichever comes LAST is the decimal separator, under
        # either convention. '1,000.0' is a thousand; '1.000,50' is a thousand
        # and fifty cents. Reading it as English thousands, which is what this
        # did before, turned the second into 1.0005.
        decimal_sep = "." if text.rindex(".") > text.rindex(",") else ","
    else:
        sep = "." if has_dot else ","
        if _THOUSAND_GROUPS[sep].fullmatch(text):
            if convention == sep:
                raise _Refuse("%s under a '%s' decimal convention" % (text, sep))
            if convention is None and scaled and sep == ".":
                # '$1.265 Million': a scale word follows and no language is
                # named, so the English point is a decimal point. Docstring.
                decimal_sep = "."
            else:
                decimal_sep = ""
        else:
            decimal_sep = sep

    if decimal_sep:
        thousands_sep = "." if decimal_sep == "," else ","
        text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
    else:
        text = text.replace(".", "").replace(",", "")
    return float(text)


# A round larger than this is a parse failure, not news. Ten trillion dollars
# is more than any company has ever raised, so a value above it means the
# string was something other than a funding figure.
_MAX_PLAUSIBLE_USD = 10_000_000_000_000

# And a round SMALLER than this is a parse failure too, which had no guard at
# all. Nobody announces raising nine hundred dollars, so a sub-thousand figure
# means the string was cut short, the multiplier was in a word we do not know,
# or a thousands separator was read as a decimal point. The live case was
# '$1' -- from a headline that literally reads 'pendanaan non-dilutif $1...',
# truncated mid-figure by the source we quoted.
#
# This floor is the same threshold tests/test_funding_amount_parsing.py has
# always used to detect the failure after the fact. Enforcing it here turns a
# post-hoc alarm into a refusal, which is the house rule: we do not guess.
#
# It also BLINDS that test, and that is why read_funding_figure exists. A guard
# whose subject can no longer reach it always passes, and "the parser cannot
# produce a sub-thousand figure" is not the property anyone wanted checked —
# "no string we hold parses to one" is. The test reads the unclamped figure and
# pins the strings this floor is currently swallowing, so the next language
# whose scale word we do not know arrives as a red build rather than as six
# rows quietly worth a hundred and ninety dollars.
_MIN_PLAUSIBLE_USD = 1_000


# --- A stated RANGE is not a figure -----------------------------------------
#
# '$20-25 million' (QpiAI/YourStory, 2026-08-17) states no single number, and
# until this existed the parser had no opinion about that at all. What it had
# was an ACCIDENT, and the accident disagreed with itself by typography:
#
#   '$20 million to $25 million'  -> 20,000,000 stored, silently, as the low end
#   '$20M-$25M'                   -> 20,000,000 stored, silently, as the low end
#   '$20-25 million'              -> the reader takes '20', the scale word
#                                    belongs to the OTHER end, the uncapped
#                                    figure is twenty dollars, and
#                                    _MIN_PLAUSIBLE_USD refuses it
#
# Same fact, opposite handling, decided by whether a publisher happened to
# repeat the word 'million'. Only the third shape made any noise, and it made
# it in the wrong place — as an unexplained entry under the plausibility floor,
# which is where a language whose scale word we do not know is supposed to
# arrive. Documenting each one in FLOOR_REFUSALS turns that signal into a
# treadmill: every '$X to $Y million' headline needs its own line, and a list
# everybody adds to is a list nobody reads.
#
# So a range is detected BEFORE the number is read, and lands in a named
# outcome that is the same for all three shapes above.
#
# WHICH outcome is a data-correctness decision, not a parser detail, so it is
# one literal rather than a rewrite:
#
#   'refuse'  — no figure. The row keeps its verbatim amount, the reader still
#               sees '$20-25 million', and funding_amount_usd is visibly
#               absent. This is the default because that column is SUMMED into
#               a headline total and feeds the implausibility guardrail: a low
#               end is biased low, in one direction, on rows nothing marks as
#               estimated. The house rule is that NULL is visibly missing while
#               a wrong-looking-right number is not.
#   'low_end' — store the first end, which the source did state, and which is
#               the rule the sibling tracker uses for headcounts. Defensible;
#               it does not invent a number. If it is chosen, it applies to all
#               three shapes — including the one that reads its scale word off
#               the far end of the range, which today produces twenty dollars.
#
# Do not add a third value that computes a midpoint. Nobody printed it.
FUNDING_RANGE_POLICY = "refuse"

#: What may join the two ends of a range: a dash of any width, or the word a
#: publisher used instead of one. The second end may restate the currency
#: ('$20M to $25M'), and by this point _USD_PREFIX/_USD_CODE have already
#: rewritten 'US$'/'USD', so only a bare '$' can still be sitting there.
#:
#: The words are not English-only, because the corpus is not: 575 feeds in 43
#: languages, and 'USD 20 a 25 millones' is the same headline as '$20-25
#: million'. Requiring a DIGIT after the joiner is what makes the short ones
#: safe — '$5 million a year' has no second number, so 'a' cannot fire there.
#: The asymmetry is deliberate: a false positive here refuses a figure, which
#: is visibly absent, and a miss stores an unmarked low end, which is not.
_RANGE_JOIN = re.compile(
    r"[\s  ]*(?:[-‐‑‒–—―−]"
    r"|to\b|and\b|or\b"          # English
    r"|a\b|à\b|até\b|hasta\b"    # Spanish, Portuguese, French, Italian
    r"|und\b|bis\b|tot\b|en\b"  # German, Dutch
    r"|ile\b|do\b)"              # Turkish, Polish
    r"[\s  ]*\$?[\s  ]*(?=\d)",
    re.I)


def _range_far_end(text: str, first):
    """Index just past the SECOND number, if `first` is one end of a range.

    Returns None when the string states a single figure. The join has to sit
    immediately after the first number and be followed by another number, which
    is what keeps '$20-million USD' (a hyphenated scale word, BetaKit) and
    '$5M (2026-2027)' out of here.
    """
    tail = text[first.end():]
    # The join sits after the first number, and after that number's OWN scale
    # word when it has one: '$20-25 million' joins at the digits, '$5M to $10M'
    # joins past the 'M'. Both are one range and must not answer differently.
    starts = [0]
    scale_end = _scale_word_span(tail)
    if scale_end is not None:
        starts.append(scale_end)

    for start in starts:
        join = _RANGE_JOIN.match(tail, start)
        if not join:
            continue
        second = _NUMBER.match(tail, join.end())
        if second:
            return first.end() + second.end()
    return None


def _scale_word_span(tail: str):
    """Length of the scale word sitting at the head of `tail`, or None.

    Only a word `_scale_after` would act on counts — a known multiplier or one
    it deliberately refuses. Anything else is not a scale word standing between
    a number and a range join, it is the rest of the sentence.
    """
    if not tail:
        return None
    gap = _SCALE_GAP.match(tail)
    start = gap.end() if gap else 0
    rest = tail[start:]

    for token, _multiplier, _convention in _GLUED_SCALE:
        if rest.startswith(token):
            return start + len(token)

    match = _LETTER_RUN.match(rest)
    if not match:
        return None
    word = match.group(0).lower()
    known = word in _SCALE or word in _AMBIGUOUS_SCALE
    if not known:
        for prefix in _CLITIC_PREFIXES:
            stem = word[len(prefix):] if word.startswith(prefix) else None
            if stem and (stem in _SCALE or stem in _AMBIGUOUS_SCALE):
                known = True
                break
    if not known:
        return None
    end = start + match.end()
    # `mio.`, `mln.`, `млн.` are written with their abbreviation dot.
    if tail[end:end + 1] == ".":
        end += 1
    return end


def read_funding_figure(value: str):
    """The figure a funding string states, in US dollars, BEFORE plausibility.

    Split out from parse_funding_usd so the plausibility bounds are the only
    difference between them, and so a test can see what the floor is refusing.
    Returns None where the string states no figure we are willing to read at
    all: no digits, no stated US dollar, a currency that is not the US dollar,
    or a scale word whose meaning depends on the publisher's language.
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

    # ...and it has to be OUR dollar. _NON_USD covers the symbols and the codes;
    # _QUALIFIED_DOLLAR covers the same claim written in words, which is the
    # only new way a non-US currency can reach here now that a dollar word is a
    # marker. 'dollars canadiens' refuses; 'US dollars' does not.
    if _NON_USD.search(text) or _QUALIFIED_DOLLAR.search(text):
        return None

    m = _NUMBER.search(text)
    if not m:
        return None

    # A range is decided before the number is read, so that all three of its
    # typographies get the same answer rather than three different accidents.
    far_end = _range_far_end(text, m)
    if far_end is not None and FUNDING_RANGE_POLICY == "refuse":
        return None

    try:
        scale = _scale_after(text[m.end():])
    except _Refuse:
        return None
    if scale is None and far_end is not None:
        # 'low_end' on '$20-25 million': the first end is the figure, but the
        # scale word is written once, after the second. Reading it there is not
        # a guess — it is the only scale the string states, and it is what makes
        # this shape agree with '$20M to $25M' instead of parsing to twenty.
        try:
            scale = _scale_after(text[far_end:])
        except _Refuse:
            return None
    multiplier, convention = scale or (1, None)

    try:
        number = _read_number(m.group(0), convention, scaled=scale is not None)
    except (ValueError, _Refuse):
        return None
    return number * multiplier


def parse_funding_usd(value: str):
    """Return the figure as whole US dollars, or None.

    None means "we will not guess", and covers: no digits at all, NO STATED US
    DOLLAR, a currency that is not the US dollar, a scale word that means
    different things in different languages, and anything that parses to an
    implausible number.

    A stated RANGE ('$20-25 million') is its own outcome and not a small figure:
    see FUNDING_RANGE_POLICY, which decides whether it refuses or stores its low
    end. This docstring claimed the low end unconditionally until 2026-08-17,
    and that was only ever true of the shapes where the publisher repeated the
    scale word.
    """
    amount = read_funding_figure(value)
    if amount is None:
        return None
    if amount < _MIN_PLAUSIBLE_USD or amount > _MAX_PLAUSIBLE_USD:
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
#
# The last three are CAPITAL EVENTS, and they were added for a measured reason.
# A company raising money in the public and lender markets was being stored as
# a venture round: ChangXin's IPO, Oracle's bond, Intel's stock sale and
# Nvidia's infrastructure financing, four in one month, each caught late by the
# amount guardrail's MAGNITUDE check and each costing a human decision. That
# reason does not generalise downward — Zions Bancorporation's $500m senior
# notes issuance is the same event four orders of magnitude below any derived
# threshold, and it is on the live page as a funding round. So the KIND of
# event gets a value here, and pipeline/capital_event.py decides it from the
# source's own words. See that module for why it refuses what it refuses.
DEAL_TYPES = (
    "acquisition",    # this employer is BUYING
    "acquired",       # this employer is BEING bought
    "merger",
    "divestiture",    # selling a unit, spin-off, carve-out
    "joint_venture",
    "ipo",
    "bond_issue",       # bonds, sukuk, debentures, senior notes
    "public_offering",  # equity sold by an already-listed issuer
    "project_finance",  # a loan or facility advanced against an asset
    # And the four MONEY-BASIS kinds, decided by pipeline/money_raised.py from
    # the source's own words. They exist for the same reason the three above
    # do, one question further out: those ask "is this a venture round or a
    # market instrument", these ask "is the named employer raising money at
    # all". A VC closing its own fund, a company spending money, a state
    # subsidy and an investment pledge were all being added into a public
    # "money raised" total, and `deal_type` was empty on every one of them.
    "fund_raise",           # an investor closing its own fund or vehicle
    "outbound_investment",  # THIS employer is the one paying
    "state_funding",        # a subsidy, grant or public appropriation
    "pledge",               # announced, not received
)

DEAL_TYPE_LABELS = {
    "acquisition": "Acquiring",
    "acquired": "Being acquired",
    "merger": "Merger",
    "divestiture": "Divestiture",
    "joint_venture": "Joint venture",
    "ipo": "IPO",
    "bond_issue": "Bond issue",
    "public_offering": "Public offering",
    "project_finance": "Project financing",
    "fund_raise": "Fund close",
    "outbound_investment": "Outbound investment",
    "state_funding": "Government funding",
    "pledge": "Investment pledge",
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
