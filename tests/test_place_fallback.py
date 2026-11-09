"""pipeline/place_fallback.py: two agreeing signals or no country at all.

The two rows from the 2026-11-09 talent daily are placed; every measured
counterexample from the 2026-09-02 refusal and the 2026-11 corpus stays blank.
"""
import pytest

from pipeline import place_fallback as pf


def test_austro_panel_bosnian_national_outlet_and_bih_in_headline():
    assert pf.decide(
        source_url="https://www.biznisinfo.ba/u-bih-se-otvara-tvornica/?utm_source=rss",
        headline="A factory of 20,000 square meters is opening in BiH, with workers "
                 "receiving 14 salaries annually",
        language=None) == "BA"


def test_phincon_indonesian_headline_on_national_dot_com_outlet():
    assert pf.decide(
        source_url="https://www.liputan6.com/bisnis/read/8330140/ada-lowongan-kerja",
        headline="Ada Lowongan Kerja di Perusahaan IT Ini, Cek Syaratnya",
        language="id") == "ID"


def test_feed_host_maps_to_the_same_publisher():
    assert pf.publisher_country("https://feed.liputan6.com/rss/bisnis") == "ID"
    assert pf.publisher_country("https://www.biznisinfo.ba/x") == "BA"


@pytest.mark.parametrize("url,headline,language,hq", [
    # A ccTLD alone: Qualcomm on a Brazilian outlet, General Atomics on a Taiwanese one.
    ("https://www.infomoney.com.br/mercados/qualcomm", "Qualcomm tem queda de receita", "pt", None),
    ("https://technews.tw/2026/11/02/ga-asi", "GA-ASI confirmed new drone", None, "US"),
    # The ccTLD and the HQ disagree.
    ("https://www.telegraaf.nl/financieel/x", "Voormalig Shell-topman naar Castrol", "nl", "GB"),
    # A headline place alone, the 2026-09-02 counterexamples.
    ("https://example.com/a", "HPE Aruba hire boosts networking unit", "en", None),
    ("https://example.com/b", "GoGig utser Sofia Fridell", None, None),
    # Headline names another country than the outlet.
    ("https://www.latribune.fr/article/afrique/x", "Hydrogene: Técnicas Reunidas en Egypt", "fr", None),
    # Two countries named.
    ("https://www.biznisinfo.ba/x", "Firm from Croatia opens plant in BiH", None, None),
    # Language alone, no publisher country and no place in the title.
    ("https://example.com/c", "Ada Lowongan Kerja di Perusahaan IT Ini", "id", None),
    # A vanity TLD is not a country.
    ("https://dev.io/news/x", "New CTO named", "en", None),
    ("https://startup.ai/2026/11/x", "스윙 신임 대표", "ko", None),
    # Language contradicts the outlet.
    ("https://news.yahoo.co.jp/articles/x", "신임 대표 선임", "ko", None),
    # Nothing at all.
    ("", "", None, None),
])
def test_stays_blank_without_two_agreeing_signals(url, headline, language, hq):
    assert pf.decide(source_url=url, headline=headline, language=language, hq_country=hq) is None


def test_korean_national_outlet_and_korean_headline():
    # byline.network is a curated National outlet in data/feeds.csv.
    assert pf.decide(source_url="https://byline.network/2026/11/swing/",
                     headline="스윙 신임 대표", language="ko") == "KR"


def test_cctld_plus_matching_hq_places():
    assert pf.decide(source_url="https://www.wiwo.de/unternehmen/bayer",
                     headline="Bayer: Quartalszahlen", language="de", hq_country="DE") == "DE"


def test_language_plus_headline_without_publisher():
    assert pf.decide(source_url="https://example.com/x",
                     headline="Lowongan kerja baru di Indonesia untuk lulusan", language="id") == "ID"


def test_every_exclusive_language_maps_to_a_known_country():
    from pipeline import vocab
    for code, iso2 in pf.EXCLUSIVE_LANGUAGE.items():
        assert iso2 in vocab.COUNTRY_NAMES, code


def test_build_signal_places_an_unplaced_row_from_two_agreeing_signals():
    from tests.test_validate import classified, raw
    from pipeline import validate
    head = "Ada Lowongan Kerja di Perusahaan IT Ini, Cek Syaratnya"
    signal = validate.build_signal(
        classified(company="PhinCon", city="", country="", headline=head,
                   summary="PhinCon membuka lowongan kerja.",
                   talent_readthrough="IT roles in Indonesia."),
        raw(headline=head, raw_text=head,
            source_url="https://www.liputan6.com/bisnis/read/8330140/ada-lowongan-kerja"),
        "national_press")
    assert signal.country == "ID"
    assert signal.region == validate._region_for_country("ID")


def test_build_signal_keeps_the_classifier_country():
    from tests.test_validate import classified, raw
    from pipeline import validate
    signal = validate.build_signal(
        classified(), raw(source_url="https://www.liputan6.com/x"), "national_press")
    assert signal.country == "IE"
