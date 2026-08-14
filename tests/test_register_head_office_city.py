"""A register's filed address places the employer, and it places it in `hq_city`.

WHY THIS FILE EXISTS.

A city-coverage audit on 2026-08-13 measured 1,158 of 29,569 current rows
(3.9%) carrying a city, 887 of them (77%) American, and found the cause of the
asymmetry in the connectors rather than in the budget: `sec_form_d_bulk` places
21.1% of its rows and `sec_edgar` 8.6%, while Companies House, ARES, OpenDART,
EDINET, BSE India and Estonia placed 0%. Between them EDINET and OpenDART held
379 rows with no city in EITHER column, while the address needed to place them
was sitting unread in a response those collectors already download.

WHAT THIS FILE PINS, and the distinction is the whole point of it.

`city` in this product is a STATED JOB LOCATION. `pipeline/classify.py` forbids
the model from inferring one, and a register does not state one: EDINET's
`Province` and DART's `adres` are the address the company is REGISTERED at.
A registered head office is a different fact, the `hq_*` columns exist to carry
it, and so:

  1. the address is read and stored, because leaving it unread is what left two
     whole countries unplaceable; and
  2. it lands in `hq_city` and never in `city`, because promoting a legal seat
     to a stated job location would put a number on the live page that no
     document says.

Test 3 is the one that matters most on a bad day: it drives the collectors' own
`as_classified` and asserts the address does not reach `city` through it.

THE PARSE IS A LOOKUP, NOT A PARSER, and the third group of tests holds that
line. Both maps are enumerated because both alphabets have a measured trap in
them: 淀川区 is Osaka's ward and appears in the EDINET list with no city in
front of it, so "a bare 区 is Tokyo" is wrong on a real row; and 中央区 and 北区
are ward names in Tokyo and in half the designated cities alike, so a rule that
did not read the leading 市 first would file Osaka and Sapporo under Tokyo.
"""

from __future__ import annotations

import unittest

from collectors import edinet_japan as edinet
from collectors import opendart_korea as dart
from pipeline import validate, vocab


class EdinetAddress(unittest.TestCase):
    """`Province`, which is 提出者の所在地, read from a fixed list."""

    def test_a_bare_tokyo_ward_is_tokyo(self):
        """The commonest shape in the file, and the one with no prefecture.

        1,019 filers give 港区 and 884 give 千代田区 with nothing in front of
        them (measured on the real list, 2026-08-14). If these do not place,
        the map places almost nothing.
        """
        self.assertEqual(edinet.head_office_city("新宿区西新宿六丁目５番１号"), "Tokyo")
        self.assertEqual(edinet.head_office_city("港区赤坂一丁目１２番３２号"), "Tokyo")

    def test_a_prefecture_in_front_of_the_ward_does_not_break_it(self):
        """80 rows write 東京都 out in full. Same answer, different shape."""
        self.assertEqual(edinet.head_office_city("東京都千代田区丸の内一丁目"), "Tokyo")

    def test_a_city_in_front_of_a_ward_wins_over_the_ward(self):
        """The disambiguation the whole rule turns on.

        中央区 and 北区 are Tokyo wards AND wards of most designated cities.
        1,720 addresses in the file write the ward after its city. Reading the
        leading 市 first is what keeps Osaka out of Tokyo's count.
        """
        self.assertEqual(edinet.head_office_city("大阪市中央区平野町二丁目"), "Osaka")
        self.assertEqual(edinet.head_office_city("札幌市北区北七条西一丁目"), "Sapporo")

    def test_a_ward_that_is_not_one_of_tokyos_23_places_nothing(self):
        """淀川区 is Osaka's, and the file really does write it bare once.

        This is the measured counterexample that makes WARD_CITY a list rather
        than a rule about the character 区. If this test goes green by way of
        "Tokyo", the map has been replaced by a guess.
        """
        self.assertEqual(edinet.head_office_city("淀川区西中島１丁目９番２０号"), "")

    def test_a_foreign_address_places_nothing(self):
        """Foreign filers are in this list too, and none of them is Japanese."""
        for address in ("アメリカ合衆国４８６７４ミシガン州ミッドランド市",
                        "スペイン王国２８０１３マドリッド市グラン・ビア２８番",
                        ""):
            self.assertEqual(edinet.head_office_city(address), "")

    def test_every_ward_on_the_list_is_one_of_tokyos_23(self):
        self.assertEqual(len(edinet.WARD_CITY), 23,
                         "Tokyo has 23 special wards. A 24th entry here is "
                         "another city's ward, which is the exact mistake "
                         "淀川区 records.")
        self.assertEqual(set(edinet.WARD_CITY.values()), {"Tokyo"})

    def test_every_name_the_maps_emit_is_a_name_and_not_a_stored_value(self):
        """The maps may name a city the curated gazetteer does not know.

        That is deliberate: `vocab.normalize_city` is the gate, so a name it
        rejects stores nothing. This test exists so that a later pass adding
        Nagoya or Sapporo to the gazetteer is a one-line change here of exactly
        zero lines.
        """
        for name in set(edinet.MUNICIPALITY_CITY.values()) | {"Tokyo"}:
            self.assertTrue(name.isascii() and name[:1].isupper(),
                            f"{name!r} is not a plain city name")


class DartAddress(unittest.TestCase):
    """`adres` off company.json, a response already paid for."""

    def test_a_first_level_city_ends_the_question(self):
        self.assertEqual(dart.head_office_city("서울특별시 종로구 세종대로 149"), "Seoul")
        self.assertEqual(dart.head_office_city("부산광역시 해운대구 센텀중앙로"), "Busan")

    def test_a_province_hands_the_answer_to_the_second_token(self):
        self.assertEqual(dart.head_office_city("경기도 성남시 분당구 판교로 235"),
                         "Seongnam")

    def test_a_province_town_this_file_cannot_name_places_nothing(self):
        """No transliteration here, for the same reason no company name is."""
        self.assertEqual(dart.head_office_city("경기도 여주시 세종로 1"), "")

    def test_an_address_with_no_administrative_token_places_nothing(self):
        for address in ("Seoul, Korea", "", "   "):
            self.assertEqual(dart.head_office_city(address), "")


class TheAddressNeverBecomesAStatedJobLocation(unittest.TestCase):
    """The load-bearing half. A filed address is a head office, not a job.

    Driven through each collector's own `as_classified` and then through
    `validate.build_signal`, because that is the path a real row takes and it
    is the only place the two columns can be told apart.
    """

    def _edinet_signal(self, address):
        entry = {
            "docTypeCode": edinet.DOC_TYPE_EXTRAORDINARY,
            "currentReportReason": edinet.OFFICER_CLAUSE,
            "docID": "S100XUNB",
            "edinetCode": "E00004",
            "submitDateTime": "2026-08-01 09:30",
            "secCode": "13760",
            "docDescription": "代表取締役の異動",
        }
        filers = {"E00004": {"name": "KANEKO SEEDS CO., LTD.",
                            "hq_city": edinet.head_office_city(address)}}
        item = edinet._row(entry, filers)
        self.assertIsNotNone(item)
        return item, validate.build_signal(edinet.as_classified(item), item,
                                           edinet.COLLECTOR)

    def test_edinet_places_the_head_office_and_states_no_job_location(self):
        item, signal = self._edinet_signal("東京都千代田区丸の内一丁目")
        self.assertEqual(item["hq_city"], "Tokyo")
        self.assertEqual(signal.hq_city, "Tokyo")
        self.assertIsNone(
            signal.city,
            "an EDINET row carries a STATED city. `Province` is the address "
            "the filer is registered at, and an extraordinary report says "
            "nothing about where the representative director sat. Promoting "
            "it to `city` publishes a job location no document states.")

    def test_edinet_stores_nothing_when_the_address_names_no_city(self):
        item, signal = self._edinet_signal("淀川区西中島１丁目９番２０号")
        self.assertEqual(item["hq_city"], "")
        self.assertIsNone(signal.city)
        self.assertIsNone(signal.hq_city)

    def _dart_row(self, address):
        entry = {
            "report_nm": "대표이사변경",
            "corp_name": "삼성전자주식회사",
            "rcept_no": "20260728900814",
            "rcept_dt": "20260728",
            "corp_code": "00126380",
            "corp_cls": "Y",
        }
        return dart._row(entry, "SAMSUNG ELECTRONICS CO,.LTD",
                         dart.head_office_city(address))

    def test_dart_places_the_head_office_and_states_no_job_location(self):
        item = self._dart_row("경기도 수원시 영통구 삼성로 129")
        self.assertIsNotNone(item)
        self.assertEqual(item["hq_city"], "Suwon")
        signal = validate.build_signal(dart.as_classified(item), item,
                                       dart.COLLECTOR)
        self.assertIsNone(
            signal.city,
            "a DART row carries a STATED city. `adres` is a legal seat, and "
            "the filing does not say where the appointment happened.")
        # Suwon is not in the curated gazetteer today, so nothing is stored and
        # that is the designed outcome: the name is offered, vocab decides.
        self.assertEqual(vocab.normalize_city("Suwon"), None)
        self.assertIsNone(signal.hq_city)

    def test_dart_places_a_city_the_gazetteer_knows(self):
        item = self._dart_row("서울특별시 종로구 세종대로 149")
        self.assertIsNotNone(item)
        signal = validate.build_signal(dart.as_classified(item), item,
                                       dart.COLLECTOR)
        self.assertEqual(signal.hq_city, "Seoul")
        self.assertIsNone(signal.city)


class OneFetchNotTwo(unittest.TestCase):
    """The address is free only because it rides an existing response.

    EDINET's terms forbid 短時間における大量のアクセス and DART meters requests,
    so a second download for the second field would be a real cost added to a
    change whose whole argument is that it has none.
    """

    def test_edinet_reads_name_and_address_from_one_download(self):
        class _Resp:
            status_code = 200

            def __init__(self, content):
                self.content = content

        import io
        import zipfile

        rows = (
            "Date of download data creation,As Of 2026.08.14,Number of data,2\n"
            "EDINET Code,Submitter Name（alphabetic）,Province\n"
            '"E00004","KANEKO SEEDS CO., LTD.","前橋市古市町一丁目"\n'
            '"E00005","EXAMPLE CORP","千代田区大手町一丁目"\n'
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("EdinetcodeDlInfo.csv", rows.encode("cp932"))
        payload = buf.getvalue()

        calls = []

        class _Session:
            def get(self, url, **kwargs):
                calls.append(url)
                return _Resp(payload)

        directory = edinet.fetch_filer_directory(session=_Session())
        self.assertEqual(len(calls), 1,
                         "the code list was downloaded more than once")
        self.assertEqual(directory["E00005"]["name"], "EXAMPLE CORP")
        self.assertEqual(directory["E00005"]["hq_city"], "Tokyo")
        # 前橋市 is a real municipality and is not on the map, so it places
        # nothing rather than reaching for the nearest thing that is.
        self.assertEqual(directory["E00004"]["hq_city"], "")

    def test_dart_reads_name_and_address_from_one_company_json(self):
        calls = []
        cache = {}

        def _fetch(code, *, key, timeout=45, session=None):
            calls.append(code)
            return {"corp_name_eng": "SAMSUNG ELECTRONICS CO,.LTD",
                    "adres": "경기도 수원시 영통구 삼성로 129"}

        original = dart.fetch_company
        dart.fetch_company = _fetch
        try:
            profile = dart.filer_profile("00126380", key="k" * 40, cache=cache)
            # The name view must not buy a second request.
            dart.english_name("00126380", key="k" * 40, cache=cache)
        finally:
            dart.fetch_company = original

        self.assertEqual(len(calls), 1,
                         "company.json was fetched more than once for one filer")
        self.assertEqual(profile["hq_city"], "Suwon")
        self.assertEqual(profile["name"], "SAMSUNG ELECTRONICS CO,.LTD")


if __name__ == "__main__":
    unittest.main()
