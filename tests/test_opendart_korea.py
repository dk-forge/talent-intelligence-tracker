"""Korea's leadership spine must stay a typed filing, and must not go quiet.

The failures guarded against here were all found while the connector was being
built against the real API and DART's public search:

- a snapshot endpoint synthesised into an event with a date the source never
  stated (refused in code, asserted here);
- the Korea Exchange's whole timely-disclosure feed stored because `I001` is
  ONE detail code covering 360 different report names;
- a listed parent's filing about an unnamed subsidiary stored as the parent's
  own leadership change;
- an amendment stored as a second event;
- a Korean company name reaching `company_key`, where it produces a WordPress
  slug that 404s on the live host;
- full-width digits reading as invented figures and silently discarding a
  correct record;
- an unset GitHub secret reaching the API as an empty string, which OpenDART
  answers with a 302 to an HTML page rather than an error;
- HTTP 200 read as success when an unregistered key returns
  `200 {"status":"010"}`;
- a week of silence exiting green.

Recorded fixture, never a live call. Every row in it is a real filing captured
from DART's public search on 2026-07-29; see the fixture's own `_provenance`.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from collectors import opendart_korea as dart
from pipeline import validate, vocab

FIXTURE = Path(__file__).parent / "fixtures" / "opendart_korea_leadership.json"
KEY = "a" * 40


class _Base(unittest.TestCase):
    def setUp(self):
        self.cases = {k: v for k, v in json.loads(FIXTURE.read_text()).items()
                      if not k.startswith("_")}

    def row(self, case: str):
        """The collector's raw dict for one fixture case, or None if declined."""
        block = self.cases[case]
        return dart._row(block["list"][0], block["_corp_name_eng"])

    def signal(self, case: str):
        item = self.row(case)
        self.assertIsNotNone(item, f"{case} was declined but should store")
        return validate.build_signal(dart.as_classified(item), item,
                                     dart.COLLECTOR)

    def stored_cases(self):
        return ("ceo_change", "ceo_change_notice", "executive_director_change",
                "independent_director")


class TheRecordIsAFiling(_Base):
    def test_every_stored_row_reaches_verified(self):
        # A DART disclosure is filed WITH the regulator, so the host is the
        # filing venue and not an outlet. If dart.fss.or.kr ever leaves
        # vocab.PRIMARY_SOURCE_DOMAINS this drops to 'reported' silently.
        for case in self.stored_cases():
            self.assertEqual(self.signal(case).confidence, "verified", case)

    def test_the_host_is_registered_as_a_primary_source(self):
        self.assertIn("dart.fss.or.kr", vocab.PRIMARY_SOURCE_DOMAINS)

    def test_the_source_url_is_the_canonical_viewer_keyed_on_the_receipt(self):
        for case in self.stored_cases():
            sig = self.signal(case)
            parsed = urlparse(sig.source_url)
            self.assertEqual(parsed.hostname, "dart.fss.or.kr")
            self.assertEqual(parsed.path, "/dsaf001/main.do")
            rcp = parse_qs(parsed.query).get("rcpNo")
            self.assertTrue(rcp and len(rcp[0]) == 14 and rcp[0].isdigit())

    def test_the_english_viewer_is_not_the_source_url(self):
        # englishdart.fss.or.kr answers 200 with a body of the single word
        # "Reject" for 4 of 20 real filings sampled on 2026-07-29, Kia and
        # Korea Gas Corporation among them. A citation that answers 200 with one
        # word is worse than a 404 because a link checker calls it live.
        self.assertNotIn("englishdart", dart.VIEWER_URL)
        for case in self.stored_cases():
            self.assertNotIn("englishdart", self.signal(case).source_url, case)

    def test_a_malformed_receipt_number_yields_no_url(self):
        for bad in ("", "  ", "2026072890081", "202607289008140",
                    "abcdefghijklmn", "../../etc/passwd", "<script>"):
            self.assertIsNone(dart.viewer_url(bad), bad)

    def test_the_country_is_korea_and_never_guessed(self):
        sig = self.signal("ceo_change")
        self.assertEqual(sig.country, "KR")
        for case in self.cases:
            item = self.row(case)
            if item:
                self.assertEqual(item["country"], "South Korea", case)

    def test_no_city_is_invented(self):
        # The API carries no city for the event. A registered address exists on
        # company.json and is deliberately not read: a legal seat is not where
        # an appointment happened, and identity.py is the single authority for
        # hq_city.
        for case in self.stored_cases():
            self.assertIsNone(self.signal(case).city, case)

    def test_the_pillar_is_leadership_change(self):
        for case in self.stored_cases():
            self.assertEqual(self.signal(case).pillar, "leadership_change", case)

    def test_raw_text_is_set_on_every_row(self):
        # A collector that forgets raw_text posts zero records silently. That
        # bug cost the sibling weeks, so it is asserted rather than assumed.
        for case in self.cases:
            item = self.row(case)
            if item:
                self.assertTrue(item["raw_text"].strip(), case)


class OnlyTheTypedItemsAreCollected(_Base):
    def test_an_unrelated_timely_disclosure_is_declined(self):
        # 단일판매ㆍ공급계약체결 is the commonest I001 name of all and shares the
        # detail code with the CEO items. Without the allowlist, reading I001
        # would store the exchange's entire timely-disclosure feed.
        self.assertIsNone(self.row("unrelated_timely_disclosure"))

    def test_the_allowlist_is_the_selector_and_not_the_detail_code(self):
        # 8,211 I001 filings over 2026-05-01..2026-07-29 carried 360 distinct
        # report names. If this ever shrinks to "read I001", the tracker fills
        # with supply contracts and dividend decisions.
        self.assertIn("I001", dart.DETAIL_TYPES)
        self.assertTrue(dart.REPORT_NAMES)
        self.assertFalse(dart.is_wanted("단일판매ㆍ공급계약체결"))
        self.assertFalse(dart.is_wanted("현금ㆍ현물배당결정"))
        self.assertFalse(dart.is_wanted("최대주주변경"))

    def test_every_allowlisted_name_carries_an_english_label(self):
        for name, label in dart.REPORT_NAMES.items():
            self.assertTrue(dart.is_wanted(name), name)
            self.assertTrue(label.strip(), name)
            self.assertRegex(label, r"^[\x20-\x7e]+$")

    def test_a_parents_filing_about_a_subsidiary_is_refused(self):
        # The title says a CEO changed; corp_name is the PARENT and the
        # subsidiary is named only inside the document. Attributing it to the
        # parent would be a chaebol-shaped wrong answer.
        self.assertIsNone(self.row("subsidiary_change"))
        self.assertIn("대표이사변경 (자회사의 주요경영사항)",
                      dart.REFUSED_REPORT_NAMES)

    def test_the_refused_names_are_not_also_allowlisted(self):
        for refused in dart.REFUSED_REPORT_NAMES:
            self.assertNotIn(refused, dart.REPORT_NAMES, refused)

    def test_an_amendment_is_not_a_second_event(self):
        self.assertIsNone(self.row("amendment"))
        for prefix in dart.AMENDMENT_PREFIXES:
            self.assertFalse(dart.is_wanted(f"{prefix} 대표이사변경"), prefix)
        # The label still resolves, so a future revision path can recognise one.
        self.assertEqual(dart.english_label("[기재정정] 대표이사변경"),
                         "Change of CEO")

    def test_a_snapshot_endpoint_is_refused_rather_than_diffed(self):
        # This is the whole judgement of this connector. A roster is not an
        # event, and diffing two rosters would stamp a date the source never
        # stated onto a record that claims to be sourced.
        for endpoint in ("exctvSttus.json", "empSttus.json",
                         "outcmpnyDrctrNdChangeSttus.json", "elestock.json"):
            self.assertIn(endpoint, dart.REFUSED_ENDPOINTS, endpoint)
            self.assertTrue(dart.REFUSED_ENDPOINTS[endpoint].strip())
        # And nothing in the collector may reach for one.
        source = Path(dart.__file__).read_text()
        for endpoint in dart.REFUSED_ENDPOINTS:
            self.assertNotIn(f"/api/{endpoint}", source, endpoint)
        self.assertEqual(dart.LIST_URL,
                         "https://opendart.fss.or.kr/api/list.json")


class DirectionIsNeverInferred(_Base):
    def test_every_row_is_neutral(self):
        # The report title records that a change happened and never which way
        # it went: 독립이사의선임ㆍ해임또는중도퇴임에관한신고 names all three
        # possibilities in one string. Guessing would be inventing.
        for case in self.stored_cases():
            self.assertEqual(self.signal(case).signal_direction, "neutral", case)

    def test_no_row_is_ever_displacement(self):
        # `displacement` would put one officer's departure inside the sibling
        # tracker's scope, which is workforce reduction and not this.
        for case in self.cases:
            item = self.row(case)
            if item:
                self.assertNotEqual(
                    dart.as_classified(item)["signal_direction"],
                    "displacement", case)


class NamesAreTheCompanysOwn(_Base):
    def test_a_filer_with_no_english_name_is_declined_not_romanised(self):
        # company_key passes Hangul through unchanged and the WordPress slug is
        # [^a-z0-9]+ -> '-', so a Korean key yields an empty slug and falls back
        # to a percent-encoded one, which HANDOVER.md records as a 404 on this
        # host. Declining is the only honest option: this file invents no
        # transliteration.
        block = self.cases["ceo_change"]
        self.assertIsNone(dart._row(block["list"][0], ""))
        self.assertIsNone(dart._row(block["list"][0], "   "))

    def test_the_stored_employer_is_latin_and_slugs_to_something(self):
        import re
        for case in self.stored_cases():
            sig = self.signal(case)
            slug = re.sub(r"[^a-z0-9]+", "-", sig.company_key.lower()).strip("-")
            self.assertTrue(slug, f"{case} slugs to nothing: {sig.company_key!r}")
            self.assertRegex(sig.company, r"[A-Za-z]")

    def test_the_korean_name_is_still_carried_in_the_source_text(self):
        # The filer's own name belongs on the record even though it cannot be
        # the key.
        item = self.row("ceo_change")
        self.assertIn("한울앤제주", item["raw_text"])
        self.assertEqual(item["company"], "HanWool & Jeju")

    def test_a_subsidiary_is_never_folded_into_a_parent(self):
        # No alias, no suffix rule, no parent lookup. Two Korean filers that
        # are legally separate stay separate, because the source says nothing
        # about the relationship.
        keys = {self.signal(case).company_key for case in self.stored_cases()}
        self.assertEqual(len(keys), len(self.stored_cases()))

    def test_no_krx_code_is_written_to_the_ticker_column(self):
        # `ticker` is SEC-authoritative everywhere else in this tracker. A
        # 6-digit KRX code in the same column is two vocabularies in one filter.
        for case in self.stored_cases():
            self.assertIsNone(self.signal(case).ticker, case)


class FiguresRoundTrip(_Base):
    def test_every_figure_in_the_summary_is_in_the_source_text(self):
        for case in self.cases:
            item = self.row(case)
            if item:
                validate.assert_figures_are_sourced(
                    dart.as_classified(item)["summary"], item["raw_text"])

    def test_the_receipt_number_in_the_summary_is_verbatim_in_raw_text(self):
        for case in self.stored_cases():
            item = self.row(case)
            self.assertIn(item["rcept_no"], item["raw_text"], case)
            self.assertIn(item["rcept_no"],
                          dart.as_classified(item)["summary"], case)

    def test_full_width_digits_are_folded_so_they_cannot_read_as_invented(self):
        # validate._numbers_in tokenises with \d, which matches U+FF10..FF19,
        # and _normalize_number does not fold them: '１２３' and '123' compare
        # unequal, so a correct record is discarded silently.
        self.assertNotEqual(validate._numbers_in("１２３"),
                            validate._numbers_in("123"))
        self.assertEqual(dart._squeeze("１２３억"), "123억")

        entry = dict(self.cases["ceo_change"]["list"][0])
        entry["report_nm"] = "대표이사변경"
        entry["corp_name"] = "한울앤제주 ２호"
        item = dart._row(entry, "HanWool & Jeju")
        self.assertIn("2호", item["raw_text"])
        validate.assert_figures_are_sourced(
            dart.as_classified(item)["summary"], item["raw_text"])

    def test_nfkc_would_break_the_allowlist_so_it_is_not_used(self):
        # The obvious fix for full-width digits is NFKC. It rewrites U+318D —
        # the ㆍ in 독립이사의선임ㆍ해임또는중도퇴임에관한신고 — to U+119E, so
        # the allowlist would stop matching the report name the API sends.
        import unicodedata
        name = "독립이사의선임ㆍ해임또는중도퇴임에관한신고"
        self.assertIn(name, dart.REPORT_NAMES)
        self.assertNotEqual(unicodedata.normalize("NFKC", name), name)
        self.assertNotIn(unicodedata.normalize("NFKC", name), dart.REPORT_NAMES)
        # And the collector's own squeeze leaves it alone.
        self.assertEqual(dart._squeeze(name), name)

    def test_no_model_is_involved(self):
        # If as_classified ever disappears, run_collect starts paying to read a
        # source whose every field is a column, and the cost discipline in
        # CLAUDE.md quietly stops holding.
        self.assertTrue(callable(getattr(dart, "as_classified", None)))


class TheKeyIsRefusedBeforeItIsSpent(_Base):
    def test_an_empty_secret_is_refused_with_the_302_named(self):
        # An unset GitHub secret maps to the empty string. OpenDART answers a
        # keyless request with 302 and an HTML page, which is exactly how a
        # leadership dispatch once went green having stored nothing.
        original = os.environ.get("OPENDART_API_KEY_KR")
        try:
            for bad in ("", "   "):
                os.environ["OPENDART_API_KEY_KR"] = bad
                with self.assertRaises(dart.OpenDartError) as caught:
                    dart.api_key()
                self.assertIn("302", str(caught.exception))
            os.environ["OPENDART_API_KEY_KR"] = "short"
            with self.assertRaises(dart.OpenDartError) as caught:
                dart.api_key()
            self.assertIn("40", str(caught.exception))
            os.environ["OPENDART_API_KEY_KR"] = KEY
            self.assertEqual(dart.api_key(), KEY)
        finally:
            if original is None:
                os.environ.pop("OPENDART_API_KEY_KR", None)
            else:
                os.environ["OPENDART_API_KEY_KR"] = original

    def test_a_bad_key_arrives_as_http_200_and_is_still_an_error(self):
        # Verified live and keyless on 2026-07-29: an unregistered key returns
        # 200 {"status":"010"}. Trusting the status code would read that as a
        # window with no filings in it.
        class _Session:
            def get(self, *a, **kw):
                return _Resp({"status": "010",
                              "message": "등록되지 않은 인증키입니다."})

        with self.assertRaises(dart.OpenDartError) as caught:
            dart.fetch_page("E005", "20260701", "20260708", 1, key=KEY,
                            session=_Session())
        self.assertIn("010", str(caught.exception))

    def test_a_302_names_the_missing_key(self):
        class _Session:
            def get(self, *a, **kw):
                return _Resp({}, status=302)

        with self.assertRaises(dart.OpenDartError) as caught:
            dart.fetch_page("E005", "20260701", "20260708", 1, key=KEY,
                            session=_Session())
        self.assertIn("crtfc_key", str(caught.exception))

    def test_the_quota_and_maintenance_statuses_are_distinguished(self):
        for status, needle in (("020", "20,000"), ("800", "maintenance")):
            class _Session:
                def get(self, *a, **kw):
                    return _Resp({"status": status, "message": "x"})

            with self.assertRaises(dart.OpenDartError) as caught:
                dart.fetch_page("E005", "20260701", "20260708", 1, key=KEY,
                                session=_Session())
            self.assertIn(needle, str(caught.exception), status)

    def test_an_empty_window_is_status_013_and_not_an_error(self):
        class _Session:
            def get(self, *a, **kw):
                return _Resp({"status": "013",
                              "message": "조회된 데이타가 없습니다."})

        rows, pages = dart.fetch_page("E005", "20260701", "20260708", 1,
                                      key=KEY, session=_Session())
        self.assertEqual((rows, pages), ([], 1))

    def test_a_success_without_a_list_key_is_a_breakage(self):
        class _Session:
            def get(self, *a, **kw):
                return _Resp({"status": "000", "rows": []})

        with self.assertRaises(dart.OpenDartError) as caught:
            dart.fetch_page("E005", "20260701", "20260708", 1, key=KEY,
                            session=_Session())
        self.assertIn("shape has changed", str(caught.exception))


class SilenceIsNotHealth(_Base):
    def test_a_window_below_the_floor_raises_rather_than_returning_empty(self):
        asked = []

        class _Session:
            def get(self, url, params=None, headers=None, timeout=None):
                asked.append(params.get("pblntf_detail_ty"))
                return _Resp({"status": "000", "list": [], "total_page": 1})

        with self.assertRaises(dart.OpenDartError) as caught:
            dart.collect(key=KEY, session=_Session())
        self.assertIn("not a quiet week", str(caught.exception))
        self.assertEqual([a for a in asked if a], list(dart.DETAIL_TYPES))

    def test_the_floor_is_below_the_measured_weekly_minimum(self):
        # Twelve full weeks to 2026-07-29 ran 12 to 49 allowlisted filings.
        self.assertGreater(dart.MIN_ROWS_PER_WINDOW, 0)
        self.assertLess(dart.MIN_ROWS_PER_WINDOW, 12)

    def test_the_leash_matches_the_weekly_cron(self):
        import staleness
        self.assertEqual(staleness.max_age_hours("opendart_korea"), 180)

    def test_a_full_window_stores_and_the_allowlist_still_filters(self):
        block_rows = []
        for case, block in self.cases.items():
            block_rows.extend(block["list"])
        # Enough allowlisted rows to clear the floor, plus every refusal case.
        # The padding copies carry SYNTHETIC receipt numbers: they exercise the
        # paging and dedupe loop rather than claiming to be real filings, and a
        # real one repeated would be deduped on its source URL, which is the
        # behaviour the last assertion in this test checks.
        allowed = [r for r in block_rows if dart.is_wanted(r["report_nm"])
                   and r["report_nm"] not in dart.REFUSED_REPORT_NAMES]
        padding = []
        for n, row in enumerate(allowed * 3):
            clone = dict(row)
            clone["rcept_no"] = f"2026072990{n:04d}"
            padding.append(clone)
        rows = block_rows + padding + [dict(allowed[0])]

        english = {b["list"][0]["corp_code"]: b["_corp_name_eng"]
                   for b in self.cases.values()}

        class _Session:
            def __init__(self):
                self.pages = 0

            def get(self, url, params=None, headers=None, timeout=None):
                if url == dart.COMPANY_URL:
                    code = params["corp_code"]
                    return _Resp({"status": "000",
                                  "corp_name_eng": english.get(code, "")})
                self.pages += 1
                if params["page_no"] > 1:
                    return _Resp({"status": "000", "list": [], "total_page": 1})
                return _Resp({"status": "000", "list": rows, "total_page": 1})

        items = dart.collect(key=KEY, session=_Session())
        self.assertTrue(items)
        for item in items:
            self.assertTrue(dart.is_wanted(item["report_nm"]))
            self.assertNotIn(item["report_nm"], dart.REFUSED_REPORT_NAMES)
        # One row per receipt number, across both detail types.
        urls = [i["source_url"] for i in items]
        self.assertEqual(len(urls), len(set(urls)))

    def test_a_window_longer_than_the_api_allows_is_refused(self):
        original = os.environ.get("TIT_DART_DAYS")
        try:
            os.environ["TIT_DART_DAYS"] = "365"
            with self.assertRaises(dart.OpenDartError) as caught:
                dart.days_from_env()
            self.assertIn("three months", str(caught.exception))
        finally:
            if original is None:
                os.environ.pop("TIT_DART_DAYS", None)
            else:
                os.environ["TIT_DART_DAYS"] = original


class Configuration(_Base):
    def test_the_window_defaults_to_a_week(self):
        self.assertEqual(dart.days_from_env(), dart.DEFAULT_DAYS)

    def test_a_nonsense_window_is_refused(self):
        original = os.environ.get("TIT_DART_DAYS")
        for bad in ("lots", "0", "-3", "7.5"):
            os.environ["TIT_DART_DAYS"] = bad
            try:
                with self.assertRaises(dart.OpenDartError, msg=bad):
                    dart.days_from_env()
            finally:
                if original is None:
                    os.environ.pop("TIT_DART_DAYS", None)
                else:
                    os.environ["TIT_DART_DAYS"] = original

    def test_the_window_is_the_api_date_format(self):
        from datetime import datetime, timezone
        start, end = dart.window(
            7, today=datetime(2026, 7, 29, tzinfo=timezone.utc))
        self.assertEqual((start, end), ("20260722", "20260729"))

    def test_the_collector_is_registered(self):
        import run_collect
        self.assertIs(run_collect.SOURCES["opendart_korea"], dart)

    def test_korea_is_in_the_coverage_manifest_at_all(self):
        # It was not, until this connector was built: ("ko", "KR") has been in
        # GOOGLE_NEWS_LOCALES with its own query pack the whole time, so the
        # country was swept while the manifest said nothing about it.
        import source_registry as registry
        market = next((m for m in registry.MARKETS if m.iso2 == "KR"), None)
        self.assertIsNotNone(market, "KR is missing from MARKETS")
        self.assertIn(("ko", "KR"), registry.GOOGLE_NEWS_LOCALES)

    def test_korea_stays_discovery_only_until_a_real_run(self):
        # The SOURCE is measured — 261 allowlisted filings over 90 days, read
        # from DART's own public search. The CONNECTOR is not: no authenticated
        # OpenDART call has ever been made from this repo. A tier is a public
        # claim about the connector, so promotion waits for the first real run.
        import source_registry as registry
        market = next(m for m in registry.MARKETS if m.iso2 == "KR")
        self.assertEqual(market.status, registry.DISCOVERY_ONLY)
        self.assertEqual(tuple(market.live_sources), ("google_news",))

    def test_the_reason_the_snapshot_endpoints_were_refused_survives(self):
        # A refusal that keeps the volume and loses the reasoning reads to the
        # next session as a rich source nobody got round to. Both halves live in
        # the registry's triage block, so both are asserted.
        source = (Path(__file__).parents[1] / "source_registry.py").read_text()
        block = source.split("#     KR  OpenDART")[1].split("\n#\n")[0]
        for needed in ("exctvSttus.json", "empSttus.json", "elestock.json",
                       "261", "I001", "E005", "Reject", "discovery_only"):
            self.assertIn(needed, block, needed)

    def test_the_source_is_named_on_the_public_sources_page(self):
        import source_registry as registry
        self.assertEqual(
            registry.COLLECTOR_BY_SOURCE_NAME[dart.SOURCE_NAME],
            dart.COLLECTOR)

    def test_the_schedule_runs_it(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows"
                    / "collect-structured.yml").read_text()
        self.assertIn("opendart_korea", workflow)
        self.assertIn("TIT_DART_DAYS", workflow)


class _Resp:
    """A requests-shaped response. Only what _payload reads."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


if __name__ == "__main__":
    unittest.main()
