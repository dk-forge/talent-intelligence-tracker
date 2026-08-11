"""Japan's officer-change spine must stay a filing, and must not go quiet.

Every failure guarded against here is specific, and each was either found while
this collector was built against EDINET's published specification and its live
host, or is a bug this repo has already shipped once elsewhere:

- a clause number matched as a SUBSTRING, which files a change of ACCOUNTING
  AUDITOR (`第19条第2項第9号の4`) and a fund merger under the other ordinance
  (`第29条第2項第9号`) as leadership changes;
- an error arriving as HTTP 200 with the real status in the body, so an expired
  key or a throttled run reads as a day on which Japan filed nothing;
- a full-width clause number in the source against a half-width one in the
  summary, which makes `assert_figures_are_sourced` discard a correct record for
  inventing the digits 2 and 9;
- a source_url that answers 200 for any input and therefore can never be
  checked (EDINET's viewer page);
- a Japanese company name stored as the employer, which produces an EMPTY
  company slug and collides every Japanese employer with every other;
- a correction (`docTypeCode` 190) stored as a second event;
- `raw_text` missing, which posts zero records silently.

Recorded fixture, never a live call. No authenticated request was possible from
the machine this was written on, so the fixture's `currentReportReason` values
are constructed to the published specification rather than captured; the tests
that depend on that are named so a real run can confirm them.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlparse

from collectors import edinet_japan
from pipeline import validate, vocab

FIXTURE = Path(__file__).parent / "fixtures" / "edinet_japan_reason19_2_9.json"

# Cases the collector must store, and cases it must decline. Listed explicitly
# so that adding a fixture case forces a decision about it rather than letting
# it drift into whichever bucket the loops happen to put it in.
STORED = ("officer_change", "officer_change_fullwidth",
          "officer_change_among_several_reasons")
DECLINED = ("auditor_change", "shareholder_resolution", "fund_merger",
            "subsidiary_change_only", "correction_of_an_officer_change",
            "annual_report", "officer_change_no_english_name",
            "officer_change_withdrawn", "officer_change_viewing_expired")


class _Resp:
    """A requests-shaped response. Only what the collector reads."""

    def __init__(self, payload, status=200, content=b""):
        self._payload = payload
        self.status_code = status
        self.text = ("<html>maintenance</html>" if payload is _BAD_JSON
                     else json.dumps(payload, ensure_ascii=False))
        self.content = content

    def json(self):
        if self._payload is _BAD_JSON:
            raise ValueError("not json")
        return self._payload


_BAD_JSON = object()


class _Base(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(FIXTURE.read_text())
        self.names = self.cases["english_names"]

    def entries(self):
        return {k: v for k, v in self.cases.items()
                if not k.startswith("_") and k != "english_names"}

    def row(self, case: str):
        """The collector's raw dict for one fixture case, or None if declined."""
        return edinet_japan._row(self.cases[case], self.names)

    def signal(self, case: str):
        item = self.row(case)
        self.assertIsNotNone(item, f"{case} was declined but should store")
        return validate.build_signal(edinet_japan.as_classified(item), item,
                                     edinet_japan.COLLECTOR)


class TheClauseIsTheWholeSource(_Base):
    def test_the_officer_clause_is_matched_whole_and_never_as_a_substring(self):
        # `第19条第2項第9号の4` starts with the accepted clause. A substring
        # test admits it, and it is a change of accounting auditor: an audit
        # firm is an appointed firm, not an employee. This is the bse_india
        # auditor exclusion in a different disguise.
        self.assertTrue(edinet_japan.is_officer_change("第19条第2項第9号"))
        for excluded in edinet_japan.EXCLUDED_CLAUSES:
            self.assertFalse(edinet_japan.is_officer_change(excluded),
                             f"{excluded} must not read as an officer change")

    def test_the_other_ordinances_item_nine_is_a_fund_merger(self):
        # 第29条 is 特定有価証券の内容等の開示に関する内閣府令, a DIFFERENT
        # ordinance, whose item 9 is ファンドの併合. Ignoring the article
        # number would file fund mergers as leadership changes.
        self.assertFalse(edinet_japan.is_officer_change("第29条第2項第9号"))
        self.assertIsNone(self.row("fund_merger"))

    def test_a_filing_with_several_reasons_is_kept_for_the_one_that_matters(self):
        # currentReportReason is comma-joined. A filing reporting a subsidiary
        # change AND a presidential change is still a presidential change.
        self.assertTrue(
            edinet_japan.is_officer_change("第19条第2項第3号,第19条第2項第9号"))
        self.assertIsNotNone(self.row("officer_change_among_several_reasons"))

    def test_a_reason_that_is_not_the_officer_clause_stores_nothing(self):
        self.assertIsNone(self.row("subsidiary_change_only"))

    def test_only_extraordinary_reports_are_read(self):
        # docTypeCode 180 is 臨時報告書. An annual report is not one, and it
        # carries no reason at all.
        self.assertEqual(edinet_japan.DOC_TYPE_EXTRAORDINARY, "180")
        self.assertIsNone(self.row("annual_report"))

    def test_a_correction_is_never_stored_as_a_second_event(self):
        # 190 is 訂正臨時報告書. This repo appends revisions through
        # store.revise() and never overwrites, so storing a correction as a new
        # row would double-count one presidential change.
        self.assertEqual(edinet_japan.DOC_TYPE_CORRECTION, "190")
        self.assertIsNone(self.row("correction_of_an_officer_change"))
        # And the row it corrects is named, which is the hook a future session
        # would use to turn it into a real revision.
        self.assertEqual(
            self.cases["correction_of_an_officer_change"]["parentDocID"],
            "S100XUNB")


class FullWidthDigitsDoNotEatRecords(_Base):
    def test_a_full_width_clause_is_normalised_for_matching(self):
        # The specification types currentReportReason as 全半角, so the clause
        # may legitimately arrive with full-width digits.
        self.assertTrue(edinet_japan.is_officer_change("第１９条第２項第９号"))
        self.assertEqual(edinet_japan.officer_clauses("第１９条第２項第９号"),
                         ("第19条第2項第9号",))

    def test_a_full_width_clause_still_round_trips(self):
        # THE BUG THIS EXISTS FOR. `validate._NUMBER` uses `\d`, which matches
        # full-width digits in Python, so a summary saying 第19条第2項第9号
        # against a raw_text saying 第19条第２項第９号 tokenises {19,2,9}
        # against {19,２,９} and the record is discarded for "inventing" 2 and
        # 9. The collector writes the SAME normalised clause into both, so the
        # figure guard has nothing to complain about.
        item = self.row("officer_change_fullwidth")
        self.assertIsNotNone(item)
        self.assertIn("第19条第2項第9号", item["raw_text"])
        validate.assert_figures_are_sourced(
            edinet_japan.as_classified(item)["summary"], item["raw_text"])

    def test_the_demonstration_that_the_guard_really_would_have_fired(self):
        # Proof that the normalisation is load-bearing and not decoration: the
        # un-normalised pairing is genuinely rejected.
        with self.assertRaises(validate.Rejected):
            validate.assert_figures_are_sourced(
                "filed under 第19条第2項第9号",
                "...内閣府令第19条第２項第９号の規定に基づき...")

    def test_every_figure_in_every_summary_is_in_its_source_text(self):
        for case in STORED:
            item = self.row(case)
            validate.assert_figures_are_sourced(
                edinet_japan.as_classified(item)["summary"], item["raw_text"])

    def test_no_figure_is_stored_beyond_the_clause_and_the_date(self):
        # The metadata carries no amount, no headcount and no person, so there
        # is nothing else to get wrong. If a future edit puts a number in the
        # summary, it has to come from raw_text or this fails.
        for case in STORED:
            item = self.row(case)
            summary = edinet_japan.as_classified(item)["summary"]
            self.assertNotIn("万", summary)
            self.assertNotIn("億", summary)
            validate.assert_figures_are_sourced(summary, item["raw_text"])


class TheRecordIsAFiling(_Base):
    def test_every_stored_row_reaches_verified(self):
        # EDINET is the regulator a Japanese issuer files WITH, so the host is
        # the filing venue and not an outlet. If the host ever leaves
        # vocab.PRIMARY_SOURCE_DOMAINS this drops to 'reported' silently.
        for case in STORED:
            self.assertEqual(self.signal(case).confidence, "verified", case)

    def test_the_host_is_registered_as_a_primary_source(self):
        self.assertIn("disclosure2dl.edinet-fsa.go.jp",
                      vocab.PRIMARY_SOURCE_DOMAINS)

    def test_the_source_url_is_the_document_and_not_the_search_screen(self):
        # Measured 2026-07-29: disclosure2.edinet-fsa.go.jp/WEEK0010.aspx?docID=
        # returns the same 82,145-byte search page for a real document id and
        # for a nonsense one, and `docID` appears nowhere in that HTML. Storing
        # it would cite a search box, and link_check.py could never notice
        # because it answers 200 forever. The PDF permalink 404s on a bad id.
        for case in STORED:
            parsed = urlparse(self.signal(case).source_url)
            self.assertEqual(parsed.hostname, "disclosure2dl.edinet-fsa.go.jp")
            self.assertNotIn("WEEK0010", parsed.path)
            self.assertNotIn("aspx", parsed.path.lower())
            self.assertTrue(parsed.path.endswith(".pdf"))
            self.assertEqual(parsed.query, "")

    def test_a_malformed_document_id_yields_no_url_rather_than_a_broken_one(self):
        for bad in ("", "  ", "not-a-docid", "../../etc/passwd", "<script>",
                    "S100XUN", "S100XUNBB", "S100XUN/"):
            self.assertIsNone(edinet_japan.document_url(bad), bad)
        self.assertEqual(
            edinet_japan.document_url("S100XUNB"),
            "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/pdf/S100XUNB.pdf")

    def test_a_withdrawn_or_withheld_or_expired_document_is_declined(self):
        # Each of these means the citation would not resolve for a reader.
        self.assertIsNone(self.row("officer_change_withdrawn"))
        self.assertIsNone(self.row("officer_change_viewing_expired"))
        self.assertTrue(edinet_japan.is_withheld({"disclosureStatus": "2"}))
        self.assertTrue(edinet_japan.is_withheld({"withdrawalStatus": "1"}))
        self.assertFalse(edinet_japan.is_withheld(
            {"withdrawalStatus": "0", "disclosureStatus": "0",
             "legalStatus": "1"}))

    def test_the_country_is_japan_and_never_guessed(self):
        # Every filer on this endpoint files with a Japanese finance bureau, so
        # country is a property of the source rather than an inference.
        self.assertEqual(self.signal("officer_change").country, "JP")
        for case in STORED:
            self.assertEqual(self.row(case)["country"], "Japan", case)

    def test_no_city_is_invented(self):
        # The code list's address is ward-level with full-width digits and, for
        # the Tokyo wards, no prefecture: 新宿区西新宿六丁目５番１号 never says
        # Tokyo. Guessing from it is how ats_boards turned "Cambridge, MA" into
        # Morocco.
        for case in STORED:
            self.assertIsNone(self.row(case).get("city"), case)
            self.assertFalse(getattr(self.signal(case), "city", None) or "", case)

    def test_the_pillar_is_leadership_change(self):
        for case in STORED:
            self.assertEqual(self.signal(case).pillar, "leadership_change", case)

    def test_raw_text_is_set_on_every_row(self):
        # A collector that forgets raw_text posts zero records silently. That
        # bug cost the sibling weeks, so it is asserted rather than assumed.
        for case in self.entries():
            item = self.row(case)
            if item:
                self.assertTrue(item["raw_text"].strip(), case)

    def test_the_attribution_the_licence_requires_is_on_every_row(self):
        # EDINET's terms place the data under the Public Data License 1.0 and
        # require 出典 to be stated. source_name is where that lives.
        for case in STORED:
            self.assertIn("EDINET", self.row(case)["source_name"], case)
            self.assertIn("Financial Services Agency",
                          self.row(case)["source_name"], case)


class RomanisationIsReadNeverInvented(_Base):
    def test_the_employer_is_edinets_own_english_name(self):
        self.assertEqual(self.row("officer_change")["company"],
                         "OPTORUN CO.,LTD.")
        self.assertEqual(self.row("officer_change_fullwidth")["company"],
                         "Freund Corporation")

    def test_a_filer_with_no_english_name_is_declined_not_transliterated(self):
        # A value that will not normalise is a rejected record, not a new
        # category, and inventing a romanisation is exactly what the brief
        # forbids.
        self.assertIsNone(self.row("officer_change_no_english_name"))

    def test_a_japanese_name_would_have_produced_an_empty_slug(self):
        # Why the rule above exists. vocab.company_key passes non-ASCII through
        # untouched, so every Japanese employer would collide on "" and the
        # company profile route would break.
        import re
        for japanese in ("株式会社オプトラン", "架空電機株式会社"):
            key = vocab.company_key(japanese)
            self.assertEqual(re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-"),
                             "", japanese)
        # Whereas the English name the collector actually stores does not.
        self.assertTrue(vocab.company_key("OPTORUN CO.,LTD.").strip())

    def test_the_headline_names_the_employer(self):
        for case in STORED:
            sig = self.signal(case)
            self.assertTrue(sig.headline.startswith(sig.company), sig.headline)


class WhatThisSourceRefusesToClaim(_Base):
    def test_every_row_is_neutral_because_the_clause_covers_both_directions(self):
        # Item 9 covers a person BECOMING a representative director and a
        # person CEASING to be one, under one clause. The typed metadata cannot
        # tell them apart, so guessing would make half of these rows wrong.
        for case in STORED:
            self.assertEqual(self.signal(case).signal_direction, "neutral", case)

    def test_no_row_is_ever_displacement(self):
        # displacement would put this in the sibling tracker's scope. One
        # executive leaving is not a workforce reduction.
        for case in self.entries():
            item = self.row(case)
            if item:
                self.assertNotEqual(
                    edinet_japan.as_classified(item)["signal_direction"],
                    "displacement", case)

    def test_no_person_is_ever_named(self):
        # The person's name is in the document body, which this collector never
        # downloads. Nothing here may imply otherwise.
        for case in STORED:
            item = self.row(case)
            classified = edinet_japan.as_classified(item)
            for field in ("headline", "summary"):
                self.assertNotIn("氏名", classified[field], case)

    def test_the_readthrough_states_the_agm_exemption(self):
        # Item 9 exempts a change already described in the annual report, and
        # Japanese AGMs cluster in the same weeks as annual reports, so this
        # source is a floor rather than a census. A reader has to be told.
        text = edinet_japan.as_classified(self.row("officer_change"))["talent_readthrough"]
        self.assertIn("annual report", text)
        self.assertIn("floor", text)

    def test_the_readthrough_does_not_overclaim_the_scope(self):
        # The clause is the representative director alone. Copy that says
        # "officers" or "the board" is describing SEBI Regulation 30 or Item
        # 5.02, not this.
        text = edinet_japan.as_classified(self.row("officer_change"))["talent_readthrough"]
        self.assertIn("representative director", text)
        self.assertIn("not the wider board", text)

    def test_no_model_is_involved(self):
        # If as_classified ever disappears, run_collect starts paying to read a
        # source whose every field is already a column, and the cost discipline
        # in CLAUDE.md quietly stops holding.
        self.assertTrue(callable(getattr(edinet_japan, "as_classified", None)))


class AnErrorIsNeverAnEmptyDay(_Base):
    def test_a_body_level_401_inside_an_http_200_raises(self):
        # Verified live against the real host on 2026-07-29: an absent or wrong
        # key returns HTTP 200 with {"StatusCode": 401} in the body. A
        # status_code check alone sees success and finds no results, so an
        # expired key would look like Japan filing nothing, forever.
        class _Session:
            def get(self, *a, **kw):
                return _Resp({"StatusCode": 401,
                              "message": "Access denied due to invalid "
                                         "subscription key."})

        with self.assertRaises(edinet_japan.EdinetError) as caught:
            edinet_japan.fetch_list("2026-07-28", "k", session=_Session())
        self.assertIn("401", str(caught.exception))

    def test_a_body_level_429_inside_an_http_200_raises(self):
        class _Session:
            def get(self, *a, **kw):
                return _Resp({"StatusCode": 429, "message": "Too Many Requests"})

        with self.assertRaises(edinet_japan.EdinetError) as caught:
            edinet_japan.fetch_list("2026-07-28", "k", session=_Session())
        self.assertIn("429", str(caught.exception))

    def test_the_other_error_shape_is_also_read(self):
        # 400/404/500 use a completely different body: the status sits under
        # `metadata`, and there is no top-level StatusCode at all (spec p.84).
        class _Session:
            def get(self, *a, **kw):
                return _Resp({"metadata": {"status": "404",
                                           "message": "Not Found"}})

        with self.assertRaises(edinet_japan.EdinetError) as caught:
            edinet_japan.fetch_list("2026-07-28", "k", session=_Session())
        self.assertIn("404", str(caught.exception))

    def test_both_error_shapes_are_recognised_by_the_status_reader(self):
        self.assertEqual(edinet_japan._status_of({"StatusCode": 401,
                                                  "message": "no"})[0], "401")
        self.assertEqual(
            edinet_japan._status_of({"metadata": {"status": "200",
                                                  "message": "OK"}})[0], "200")
        self.assertEqual(edinet_japan._status_of({"nonsense": 1})[0], "malformed")
        self.assertEqual(edinet_japan._status_of("not a dict")[0], "malformed")

    def test_a_success_without_results_is_a_breakage_not_a_quiet_day(self):
        class _Session:
            def get(self, *a, **kw):
                return _Resp({"metadata": {"status": "200", "message": "OK"}})

        with self.assertRaises(edinet_japan.EdinetError) as caught:
            edinet_japan.fetch_list("2026-07-28", "k", session=_Session())
        self.assertIn("shape has changed", str(caught.exception))

    def test_a_non_json_body_raises(self):
        class _Session:
            def get(self, *a, **kw):
                return _Resp(_BAD_JSON, status=503)

        with self.assertRaises(edinet_japan.EdinetError):
            edinet_japan.fetch_list("2026-07-28", "k", session=_Session())

    def test_a_missing_api_key_fails_at_the_first_line(self):
        # A workflow that maps a secret which does not exist sets the variable
        # to the empty string. This repo already shipped that once, as an empty
        # EDGAR_USER_AGENT producing a 403 that read as a quiet day.
        original = os.environ.get("EDINET_API_KEY_JP")
        try:
            for bad in ("", "   "):
                os.environ["EDINET_API_KEY_JP"] = bad
                with self.assertRaises(edinet_japan.EdinetError) as caught:
                    edinet_japan.api_key_from_env()
                self.assertIn("EDINET_API_KEY_JP", str(caught.exception))
        finally:
            if original is None:
                os.environ.pop("EDINET_API_KEY_JP", None)
            else:
                os.environ["EDINET_API_KEY_JP"] = original


class TheWholeWindowRuns(_Base):
    def _session(self, rows_by_day=None):
        cases = self.cases
        names = self.names
        outer = self

        class _Session:
            def __init__(self):
                self.days = []

            def get(self, url, params=None, headers=None, timeout=None):
                if url == edinet_japan.CODELIST_URL:
                    return _Resp({}, content=outer._codelist_zip(names))
                self.days.append(params["date"])
                rows = (rows_by_day or {}).get(
                    params["date"],
                    [v for k, v in cases.items()
                     if not k.startswith("_") and k != "english_names"])
                return _Resp({"metadata": {"status": "200", "message": "OK"},
                              "results": rows})

        return _Session()

    @staticmethod
    def _codelist_zip(names: dict) -> bytes:
        """A cp932 code list zip shaped like the real one."""
        import io as _io
        import zipfile as _zip
        header = ("Date of download data creation,As Of 2026.07.30,"
                  "Number of data,%d" % len(names))
        cols = ("EDINET Code,Type of Submitter,Listed company / Unlisted company,"
                "Consolidated / NonConsolidated,Capital stock,account closing date,"
                "Submitter Name,Submitter Name（alphabetic）,"
                "Submitter Name（phonetic）,Province,Submitter's industry,"
                "Securities Identification Code,Submitter's corporate number")
        lines = [header, cols]
        for code, english in names.items():
            lines.append(
                f'"{code}","内国法人・組合","Listed company","Consolidated",'
                f'"1000","3.31","日本語名","{english}","ニホンゴメイ",'
                f'"新宿区西新宿六丁目５番１号","Machinery","10000","1234567890123"')
        buf = _io.BytesIO()
        with _zip.ZipFile(buf, "w") as zf:
            zf.writestr("EdinetcodeDlInfo.csv",
                        "\r\n".join(lines).encode("cp932"))
        return buf.getvalue()

    def test_one_call_per_calendar_day_oldest_first(self):
        from datetime import datetime, timezone
        days = edinet_japan.window(
            3, today=datetime(2026, 7, 29, tzinfo=timezone.utc))
        self.assertEqual(days, ["2026-07-27", "2026-07-28", "2026-07-29"])

    def test_a_run_reads_every_day_in_the_window(self):
        from datetime import datetime, timezone
        session = self._session()
        edinet_japan.collect(session=session, days=4, api_key="k",
                             today=datetime(2026, 7, 29, tzinfo=timezone.utc))
        self.assertEqual(session.days,
                         ["2026-07-26", "2026-07-27", "2026-07-28", "2026-07-29"])

    def test_a_run_stores_only_the_officer_changes_and_counts_the_rest(self):
        from datetime import datetime, timezone
        session = self._session(rows_by_day={"2026-07-29": [
            self.cases[c] for c in self.entries()]})
        rows = edinet_japan.collect(
            session=session, days=1, api_key="k",
            today=datetime(2026, 7, 29, tzinfo=timezone.utc))
        self.assertEqual(len(rows), len(STORED))
        self.assertEqual({r["clause"] for r in rows},
                         {edinet_japan.OFFICER_CLAUSE})
        stats = edinet_japan.LAST_RUN
        self.assertEqual(stats["read"], len(self.entries()))
        self.assertEqual(stats["corrections_skipped"], 1)
        # Three declined officer changes: no English name, withdrawn, expired.
        self.assertEqual(stats["declined"], 3)
        self.assertEqual(stats["officer"], len(STORED) + 3)

    def test_the_same_document_is_never_stored_twice_in_one_run(self):
        from datetime import datetime, timezone
        one = self.cases["officer_change"]
        session = self._session(rows_by_day={
            "2026-07-28": [one], "2026-07-29": [one]})
        rows = edinet_japan.collect(
            session=session, days=2, api_key="k",
            today=datetime(2026, 7, 29, tzinfo=timezone.utc))
        self.assertEqual(len(rows), 1)

    def test_health_reports_what_was_read_not_what_was_emitted(self):
        # Japan's clause is one clause covering one role, so a genuinely empty
        # week is ordinary. Without a read count a quiet week is `degraded`
        # every week, which is how a health page teaches people to ignore it.
        from datetime import datetime, timezone
        session = self._session(rows_by_day={"2026-07-29": [
            self.cases["subsidiary_change_only"]]})
        rows = edinet_japan.collect(
            session=session, days=1, api_key="k",
            today=datetime(2026, 7, 29, tzinfo=timezone.utc))
        self.assertEqual(rows, [])
        self.assertEqual(edinet_japan.LAST_RUN["read"], 1)


class TheCodeListIsRequiredAndVersioned(_Base):
    def test_the_code_list_is_cp932_and_not_shift_jis(self):
        # Verified 2026-07-30 on the real file: `shift_jis` raises on byte 0xfb
        # at offset 35,244, because cp932 is Microsoft's superset carrying the
        # NEC/IBM extended characters Japanese company names actually use.
        # Naming the narrower codec crashes the run on such a filer.
        self.assertEqual(edinet_japan.CODELIST_ENCODING, "cp932")
        blob = "髙".encode("cp932")
        self.assertEqual(blob.decode("cp932"), "髙")

    def test_the_english_names_parse_from_the_official_shape(self):
        zip_bytes = TheWholeWindowRuns._codelist_zip(self.names)

        class _Session:
            def get(self, *a, **kw):
                return _Resp({}, content=zip_bytes)

        names = edinet_japan.fetch_english_names(session=_Session())
        self.assertEqual(names["E33594"], "OPTORUN CO.,LTD.")

    def test_a_code_list_that_stops_parsing_stops_the_run(self):
        # If the headers move, every filer loses its English name and every row
        # would be declined: a silent zero dressed up as a quiet week.
        import io as _io
        import zipfile as _zip
        buf = _io.BytesIO()
        with _zip.ZipFile(buf, "w") as zf:
            zf.writestr("EdinetcodeDlInfo.csv",
                        "banner\r\nA,B,C\r\n1,2,3".encode("cp932"))

        class _Session:
            def get(self, *a, **kw):
                return _Resp({}, content=buf.getvalue())

        with self.assertRaises(edinet_japan.EdinetError) as caught:
            edinet_japan.fetch_english_names(session=_Session())
        self.assertIn("column headers", str(caught.exception))

    def test_a_failed_code_list_fetch_stops_the_run(self):
        class _Session:
            def get(self, *a, **kw):
                return _Resp({}, status=503, content=b"")

        with self.assertRaises(edinet_japan.EdinetError):
            edinet_japan.fetch_english_names(session=_Session())


class Configuration(_Base):
    def test_the_window_defaults_to_a_week(self):
        self.assertEqual(edinet_japan.days_from_env(), edinet_japan.DEFAULT_DAYS)

    def test_a_nonsense_window_is_refused(self):
        original = os.environ.get("TIT_EDINET_DAYS")
        for bad in ("lots", "0", "-3", "7.5", "9999"):
            os.environ["TIT_EDINET_DAYS"] = bad
            try:
                with self.assertRaises(edinet_japan.EdinetError, msg=bad):
                    edinet_japan.days_from_env()
            finally:
                if original is None:
                    os.environ.pop("TIT_EDINET_DAYS", None)
                else:
                    os.environ["TIT_EDINET_DAYS"] = original

    def test_the_collector_is_registered(self):
        import run_collect
        self.assertIs(run_collect.SOURCES["edinet_japan"], edinet_japan)

    def test_the_leash_matches_the_weekly_cron(self):
        import staleness
        self.assertEqual(staleness.max_age_hours("edinet_japan"), 180)

    def test_japan_stays_discovery_only_until_a_real_run_measures_it(self):
        # Coverage is EARNED. No authenticated call has ever been made from this
        # repo, so the volume this connector actually returns is unmeasured and
        # `structured_official` would be a claim rather than a fact. Promotion
        # is this assertion plus the market's status, in one commit, after the
        # first real run. See the TECHLOG entry for the exact gate.
        import source_registry as registry
        market = next(m for m in registry.MARKETS if m.iso2 == "JP")
        self.assertEqual(market.status, registry.DISCOVERY_ONLY)

    def test_the_workflow_can_run_it(self):
        wf = (Path(__file__).parent.parent / ".github" / "workflows"
              / "collect-structured.yml").read_text()
        self.assertIn("edinet_japan", wf)
        # The secret must be mapped, or the collector sees an empty key.
        self.assertIn("EDINET_API_KEY_JP", wf)


if __name__ == "__main__":
    unittest.main()
