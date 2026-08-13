"""The non-English appointment grammar (pipeline/leadership_intl.py) and the
cross-language duplicate pre-check built on it.

Same property as `test_cheap_extract.py`, and it matters more here: PRECISION
OVER RECALL. A decline costs one paid read. A wrong $0 close puts a wrong
employer, or a job description in place of a person's name, on a public page
with a note saying no model read it. So the decline battery below is the
important half of this file, and every entry in it is a real headline that
parsed WRONG at some point while this module was being measured.

Every headline here is taken verbatim from `data/gate_labels/labels-2026-08.jsonl`
over the priced window, which is also the corpus the module was measured
against: 124 parses, 100% agreement with the paid model on the person and on
the pillar, 97.6% on the employer key.
"""

import pytest

from pipeline import cheap_extract, dedupe, leadership_intl, schema, store, validate


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def item(headline, lang, teaser="", source_name="", url="https://outlet.example/a"):
    return {
        "headline": headline,
        "lang": lang,
        "source_name": source_name,
        "raw_text": f"{headline}\n\n{teaser}".strip(),
        "source_url": url,
        "discovery_url": url,
        "published_date": "2026-03-17",
    }


# --- What must close ---------------------------------------------------------

CLOSES = [
    ("fr", "Michele Serra est nommé directeur général de Korus Group",
     "Korus Group", "Michele Serra"),
    ("fr", "Axel Renaudin nommé Directeur Général de BBDO Paris",
     "BBDO Paris", "Axel Renaudin"),
    ("fr", "Covéa Insurance : Philippe Domart nommé directeur général",
     "Covéa Insurance", "Philippe Domart"),
    ("es", "Avanade nombra a Chris Howarth nuevo CEO", "Avanade", "Chris Howarth"),
    ("es", "Andrés Saborido, nuevo CEO de Wayra", "Wayra", "Andrés Saborido"),
    ("pt", "Marlos Steffen é o novo CEO da Approach Tech",
     "Approach Tech", "Marlos Steffen"),
    ("it", "Francesco Durante è il nuovo Amministratore Delegato di Multiversity",
     "Multiversity", "Francesco Durante"),
    ("it", "Carlo Noseda nominato CEO di Balich Wonder Studio",
     "Balich Wonder Studio", "Carlo Noseda"),
    ("de", "Marcel Dissel wird CEO der Corvaglia-Gruppe",
     "Corvaglia-Gruppe", "Marcel Dissel"),
    ("sv", "Carina Färm ny vd för Nodava", "Nodava", "Carina Färm"),
    ("sv", "Mark Silfver blir ny vd i Relier Syd", "Relier Syd", "Mark Silfver"),
    ("tr", "Arçelik'in yeni CEO'su Can Dinçer oldu", "Arçelik", "Can Dinçer"),
]


@pytest.mark.parametrize("lang,headline,company,person", CLOSES)
def test_a_stated_appointment_closes_completely(lang, headline, company, person):
    out = leadership_intl.extract(item(headline, lang))
    assert out is not None, f"{lang}: {headline}"
    assert out["company"] == company
    assert out["pillar"] == "leadership_change"
    assert out["signal_direction"] == "neutral"
    assert out["confidence"] == "reported"
    assert person in out["summary"]
    assert out["notes"] == leadership_intl.EVIDENCE_NOTE


@pytest.mark.parametrize("lang,headline,company,person", CLOSES)
def test_parse_appointment_agrees_with_extract(lang, headline, company, person):
    """One grammar, two levers. `extract` and the duplicate pre-check must read
    the same headline the same way or the pre-check would skip candidates the
    extractor would have closed differently."""
    parsed = leadership_intl.parse_appointment(item(headline, lang))
    assert parsed is not None
    assert parsed.company == company
    assert parsed.person == person


def test_the_publisher_suffix_is_stripped_with_the_collectors_own_name():
    headline = "Carina Färm ny vd för Nodava - Siljan News"
    assert leadership_intl.parse_appointment(item(headline, "sv")) is None, (
        "without source_name there is nothing to strip and the employer span "
        "must not be guessed at")
    parsed = leadership_intl.parse_appointment(
        item(headline, "sv", source_name="Siljan News"))
    assert parsed is not None and parsed.company == "Nodava"


def test_a_deterministic_intl_row_survives_build_signal_and_stores(conn):
    raw = item("Andrés Saborido, nuevo CEO de Wayra", "es")
    out = leadership_intl.extract(raw)
    signal = validate.build_signal(out, raw, "google_news")
    signal.notes = out["notes"]
    stored = store.store(conn, signal)
    assert stored
    row = conn.execute("SELECT company, pillar, notes FROM signals").fetchone()
    assert row["company"] == "Wayra"
    assert row["pillar"] == "leadership_change"
    assert row["notes"] == leadership_intl.EVIDENCE_NOTE


def test_cheap_extract_routes_non_english_leadership_here():
    """The whole lever: `run_collect` calls `cheap_extract.extract` and nothing
    else, so a grammar that is not reachable from there closes zero rows —
    which is exactly what `_parse_leadership` did for the entire priced
    window."""
    out = cheap_extract.extract(item("Carlo Noseda nominato CEO di Balich Wonder Studio", "it"))
    assert out is not None
    assert out["company"] == "Balich Wonder Studio"
    assert out["notes"] == leadership_intl.EVIDENCE_NOTE


# --- What must DECLINE -------------------------------------------------------

DECLINES = [
    # A departure is not an appointment, and it is the second commonest shape
    # in this corpus. Reading one as the other inverts the record.
    ("de", "Ecotel-CEO Markus Hendrich tritt zurück", "a resignation"),
    ("fr", "Robert Playter, CEO de Boston Dynamics depuis 2019, quitte ses fonctions",
     "a departure"),
    ("es", "Chus Bueno dimite como CEO de la Euroliga", "a resignation"),
    ("sv", "Fredrik Lidman lämnar LFAB", "a departure"),
    # A stated start date is a fact the row cannot carry, and both of these
    # welded the date onto the employer's name before the knock-out existed.
    ("fr", "Rudolf Bruder nommé directeur général de Swica dès novembre",
     "a stated start month"),
    ("de", "Marc Schuler wird CEO bei Blaser Swisslube ab März 2026",
     "a stated start month"),
    # German capitalises every noun, so two capitalised tokens are not a name.
    ("de", "Swisscom Banking-Spezialist wird CEO von Inacta",
     "a job description where the person's name should be"),
    # The span ran past the end of the employer's name.
    ("it", "Igor de Biasio è il nuovo amministratore delegato di Enav. Tutte le foto",
     "a caption welded onto the employer"),
    ("es", "Diego Escalada, nuevo CEO de Alkemy en España",
     "a territory clause welded onto the employer"),
    ("pt", "Aline Penna assume como CEO da Tânia Bulhões no Brasil",
     "a territory clause welded onto the employer"),
    ("it", "Cambio al vertice in Alstom: Martin Sion è il nuovo amministratore delegato",
     "a lead-in clause read as the employer"),
    # An all-caps headline erases every capitalisation boundary the span checks
    # depend on, and this one also carries the publisher's own typo.
    ("fr", "CHRISTOPHE PINARD-LEGRY NOMMÉ DIRECTEUR GÉNÉRAL DE CANA L EUROPE",
     "an all-caps headline"),
    # Two seats is two records.
    ("sv", "Promoteq utser Mathias Krümmel till ny VD och koncernchef",
     "two titles"),
    # Interim arrangements, pronouns, honorifics.
    ("fr", "Jean Dupont nommé directeur général par intérim de Acme", "an interim seat"),
    ("sv", "Hon blir ny VD för Aktiespararna", "a pronoun, not a name"),
    ("de", "Dr. Bartolt Haase wird CEO der Acme AG", "an honorific"),
    # A money figure means the story is bigger than one appointment.
    ("fr", "Acme lève 12 millions d'euros et nomme Jean Dupont directeur général de Acme",
     "a funding round beside the appointment"),
]


@pytest.mark.parametrize("lang,headline,why", DECLINES)
def test_ambiguous_headlines_decline(lang, headline, why):
    assert leadership_intl.extract(item(headline, lang)) is None, (
        f"{why}: {headline!r} must take the paid path")


def test_an_unsupported_script_declines_before_any_pattern_runs():
    """Rule 4 moved, not removed. Korean, Hebrew, Japanese and Vietnamese
    appointments are 176 of the 922 google_news leadership rows in the priced
    window, and a Latin-script name grammar has nothing to say about them."""
    for lang, headline in (("ko", "X-PASS, 박수정 신임 대표이사 선임"),
                           ("he", 'גיא חנינה מונה למנכ"ל רותם שני'),
                           ("ja", "米ディズニー、新CEOにダマロ氏"),
                           ("vi", "Mercedes-Benz Việt Nam có CEO mới"),
                           ("en", "Avalara Names Hugo Sarrazin Chief Executive Officer")):
        assert leadership_intl.extract(item(headline, lang)) is None, lang


def test_no_place_is_ever_claimed():
    """An invented country is the defect that had a US-filtered reader seeing 5
    of 51 events. These grammars have no place span they could read without
    guessing, so they claim none and `identity.place_if_unplaced` does the one
    free resolution it already does."""
    out = leadership_intl.extract(item("Axel Renaudin nommé Directeur Général de BBDO Paris", "fr"))
    assert out["city"] == "" and out["country"] == ""
    assert out["headquarters_city"] == "" and out["headquarters_country"] == ""


# --- The duplicate pre-check -------------------------------------------------

def _store_appointment(conn, headline, lang, url):
    raw = item(headline, lang, url=url)
    out = leadership_intl.extract(raw)
    signal = validate.build_signal(out, raw, "google_news")
    signal.notes = out["notes"]
    assert store.store(conn, signal)


def test_the_same_appointment_in_another_language_is_recognised(conn):
    _store_appointment(conn, "Jose Manuel Albesa, nuevo CEO de Puig", "es",
                       "https://a.example/1")
    parsed = leadership_intl.parse_appointment(
        item("Jose Manuel Albesa nominato CEO di Puig", "it"))
    assert parsed is not None
    assert dedupe.leadership_event_duplicate(
        conn, parsed.company_key, parsed.person,
        published_date="2026-03-17") is not None


def test_a_different_person_at_the_same_employer_is_not_a_duplicate(conn):
    """Employer alone would collapse two genuinely different appointments. A
    large employer has several a year, and a CEO in March and a CFO in April
    are two records."""
    _store_appointment(conn, "Jose Manuel Albesa, nuevo CEO de Puig", "es",
                       "https://a.example/1")
    parsed = leadership_intl.parse_appointment(
        item("Marta Ruiz nominato CEO di Puig", "it"))
    assert parsed is not None
    assert dedupe.leadership_event_duplicate(
        conn, parsed.company_key, parsed.person,
        published_date="2026-03-17") is None


def test_one_matching_token_is_not_a_person_match(conn):
    """EVERY token of the name, not any. A shared surname is not the same
    person, and there is no later stage that could notice."""
    _store_appointment(conn, "Jose Manuel Albesa, nuevo CEO de Puig", "es",
                       "https://a.example/1")
    assert dedupe.leadership_event_duplicate(
        conn, "puig", "Carmen Albesa", published_date="2026-03-17") is None


def test_a_single_token_name_never_matches(conn):
    _store_appointment(conn, "Jose Manuel Albesa, nuevo CEO de Puig", "es",
                       "https://a.example/1")
    assert dedupe.leadership_event_duplicate(
        conn, "puig", "Albesa", published_date="2026-03-17") is None


def test_the_window_belongs_to_the_candidate_not_to_today(conn):
    """The bug `funding_event_duplicate` had until 2026-08-04, which made that
    whole layer dead code for anything discovered late — and late is the norm:
    google_news's median discovery lag is 130 days."""
    _store_appointment(conn, "Jose Manuel Albesa, nuevo CEO de Puig", "es",
                       "https://a.example/1")
    assert dedupe.leadership_event_duplicate(
        conn, "puig", "Jose Manuel Albesa", published_date="2026-03-17") is not None
    assert dedupe.leadership_event_duplicate(
        conn, "puig", "Jose Manuel Albesa", published_date="2027-01-01") is None


def test_the_precheck_is_in_shadow_until_something_measures_it():
    """A skipped candidate leaves no trace: extraction never runs, so nothing
    downstream can contradict the decision. The audit measured 0 coverage-
    losing drops in 16 skips, and the Wilson interval on 0/16 reaches 19.4%.
    Arming on that would be a decision taken on an interval, not on evidence.

    This test guards the DEFAULT, not the mechanism. Flip it when the shadow
    ledger has enough skips to bound the rate, and say what the bound was."""
    import os

    import run_collect

    saved = os.environ.pop("TIT_LEADERSHIP_PRECHECK", None)
    try:
        assert not run_collect.leadership_precheck_arms()
        os.environ["TIT_LEADERSHIP_PRECHECK"] = "on"
        assert run_collect.leadership_precheck_arms()
        os.environ["TIT_LEADERSHIP_PRECHECK"] = "shadow"
        assert not run_collect.leadership_precheck_arms()
    finally:
        os.environ.pop("TIT_LEADERSHIP_PRECHECK", None)
        if saved is not None:
            os.environ["TIT_LEADERSHIP_PRECHECK"] = saved
