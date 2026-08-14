"""EDINET extraordinary reports — Japan's typed officer-change disclosure.

EDINET is the Financial Services Agency's mandated electronic disclosure system.
Its v2 API exposes a document list per calendar day, and every document carries a
`docTypeCode`. Code `180` is the 臨時報告書 (extraordinary report), and that is
the document a Japanese issuer must file when specific corporate events happen.

The field that makes this worth building is NOT the document. It is
`currentReportReason` (臨報提出事由) in the document-list METADATA. The API
specification, "EDINET API 仕様書 (Version 2)", 2026-06, page 47 item 29 and
footnote *4, defines it as the extraordinary report's statutory reason, written
as a CLAUSE NUMBER and comma-joined when a filing has more than one:

    「臨報提出事由は、『第19条第2項第1号』、『第29条第2項第1号』のように記載され、
      『第19条』は企業内容等の開示に関する内閣府令第19条を、『第29条』は特定有価
      証券の内容等の開示に関する内閣府令第29条をそれぞれ意味します。」

So the reason is a closed, machine-readable label in the same class as Item 5.02
of a US Form 8-K and a SEBI Regulation 30 category. It is NOT free prose, which
is what makes this collector `as_classified` and therefore free: no model is ever
called, and no document is ever downloaded.

WHICH CLAUSE. 企業内容等の開示に関する内閣府令 第19条第2項 has 44 items. Read
from the ordinance itself (e-gov 348M50000040005), exactly ONE of them is an
officer change, item 9:

    「提出会社の代表取締役（…指名委員会等設置会社である場合は代表執行役…）の異動
      があつた場合 … イ 当該異動に係る代表取締役の氏名、職名及び生年月日
      ロ 当該異動の年月日 …」

That is the whole reach of this source, and it is narrower than the comparable
non-US spines. It is the REPRESENTATIVE DIRECTOR only — the president/CEO and
co-representatives — not the wider board and not senior management. India's
Regulation 30 covers every director and every key managerial person; Item 5.02
covers directors and principal officers. Japan types the CEO change and nothing
else. Do not widen this to "officers" in copy; it is not what the clause says.

WHAT IS DELIBERATELY NOT COLLECTED, and why each would be wrong:

- **`第19条第2項第9号の2`, `の3`, `の4`.** These are separate clauses that all
  begin with the accepted one as a STRING PREFIX, so a substring match admits
  them silently. の2 is shareholder-meeting resolutions, の3 is a resolution
  modified or voted down at the AGM, and `の4` is a change of ACCOUNTING AUDITOR.
  That last one is the bse_india auditor exclusion arriving in a different
  shape: an audit firm is an appointed firm, not an employee. This is why
  `officer_clauses` compares whole comma-separated elements and never uses `in`.
- **`第29条第2項第9号`.** Article 29 belongs to a DIFFERENT ordinance, for
  specified securities (405M50000040022, investment corporations and REITs).
  Read from that ordinance, its item 9 is ファンドの併合 — a fund merger. A
  match that ignored the article number would file fund mergers as leadership
  changes. Article 29(2) contains no officer-change clause at all, so REITs are
  excluded because the law gives them no such trigger, not by editorial taste.
- **`docTypeCode` 190, 訂正臨時報告書.** A correction to a report already filed.
  Storing it as a new row would double-count one event, and this repo never
  overwrites a record either. Chasing it properly means matching `parentDocID`
  to a stored row and calling `store.revise()`; that is real work and is not
  done, so these are counted and skipped rather than quietly mixed in.

THE RECALL HOLE, stated because it is large and invisible. Item 9 exempts a
change that happens between the annual shareholders' meeting and the filing of
the annual report when the annual report already describes it. Japanese AGMs
cluster in late June and 有価証券報告書 are filed in the same weeks, so the
single commonest timing of a Japanese presidential succession can legitimately
produce NO extraordinary report. This source therefore under-counts by an
unmeasured amount concentrated in June and July. It is a floor, not a census.

WHAT THIS SOURCE CANNOT SAY. The metadata carries the employer, the clause, the
filing date and the document id. It does NOT carry the person's name, the job
title, or whether the person arrived or left — item 9 covers both directions in
one clause, and telling them apart needs the document body. So every row here is
`neutral`, never `hiring`, and no person is ever named. Reading the body to
recover the direction would mean an LLM call per document and this collector
would stop being free; that trade was declined.

ACCESS AND LICENCE — a green light, unlike ASX. EDINET's terms of use
(disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html) place the
content under the Japanese Public Data License 1.0, which permits commercial
reuse and redistribution, and they require attribution ("本コンテンツを利用する
際は出典を記載してください") — carried in `source_name`. They also prohibit
scraping the website while explicitly exempting the API:

    「スクレイピング等を利用して本ウェブサイトからコンテンツを機械的に取得する
      ことは禁止します。ただし、API機能を利用する場合はこの限りではありません。」

That is a hard design constraint, not a preference: every FACT here comes from
the API. The one non-API fetch is the EDINET code list, which the API
specification itself publishes as a 固定リンク (permanent link) for API users on
page 86, so it is the sanctioned path rather than a scrape.

THE HTTP-200 TRAP, which is the one that would have cost the most. EDINET
answers **HTTP 200 on errors** and puts the real status in the body, and the
body has TWO different shapes (spec pages 82-84, and verified live on
2026-07-29 against the real host):

    401 / 429 ->  {"StatusCode": 401, "message": "Access denied due to ..."}
    400/404/500 -> {"metadata": {"status": "404", "message": "Not Found"}}

A collector that checks `resp.status_code != 200` sees success, finds no
`results`, and reports a healthy empty day. An expired key and a throttled run
would both look exactly like "Japan filed nothing today", forever. `_status_of`
reads both shapes and `fetch_list` raises on anything that is not 200.

THE SOURCE URL TRAP, found by fetching both candidates. The obvious viewer link
`https://disclosure2.edinet-fsa.go.jp/WEEK0010.aspx?docID=<id>` is NOT a
document link: measured 2026-07-29, it returns the same 82,145-byte search
screen for a real document id and for a nonsense one, and `docID` appears
nowhere in that HTML. Storing it would mean every Japanese row cites a search
box, and `link_check.py` could never notice, because it answers 200 forever. The
stored URL is instead the document's own PDF permalink, which behaves like a
receipt should:

    https://disclosure2dl.edinet-fsa.go.jp/searchdocument/pdf/{docID}.pdf
    S100XUNB -> 200 application/pdf   S100ZZZZ -> 404

ROMANISATION IS READ, NEVER INVENTED. `filerName` is Japanese, and
`vocab.company_key` leaves non-ASCII untouched, so a Japanese name produces an
EMPTY company slug: every Japanese employer would collide on "" and the company
profile route would break. The fix is not a transliteration rule of ours. The
EDINET code list publishes each filer's OWN English name, and that is what is
stored; a filer with no English name on file is DECLINED and counted, because a
value that will not normalise is a rejected record rather than a new category.
Measured 2026-07-30 on the real list: 3,428 of 3,829 listed filers carry one
(89.5%).

GEOGRAPHY: THE HEAD OFFICE GOES IN `hq_city`, AND NEVER IN `city`.

This paragraph used to say no city was parsed at all. The reason it gave was
sound and is still the reason the rule below is an ALLOWLIST rather than a
parser: the code list's `Province` column is a ward-level Japanese address with
full-width digits and, for the Tokyo wards that hold most large filers, no
prefecture at all. `新宿区西新宿六丁目５番１号` never says Tokyo. A general
municipality vocabulary would be ~1,900 entries and guessing from one is how
`ats_boards` once turned "Cambridge, MA" into Morocco.

What changed is that the column was measured rather than assumed. On the real
list of 2026-08-14 (11,379 rows, 8,114 carrying an address), a bounded map of
the 23 Tokyo special wards plus the designated cities places **5,455 of all
rows (47.9%)** and **3,169 of the 4,359 filers that carry an English name and
can therefore be stored at all (72.7%)**. So the address does not have to be
parsed; it has to be RECOGNISED, from a fixed list, exactly the way
`uk_paygap.POSTCODE_AREA_CITY` recognises a postcode area.

Two measured facts hold the shape of the rule:

* **A bare ward is a Tokyo ward, but only from a list.** 24 distinct ward tokens
  appear with no city and no prefecture in front of them. 23 of them are Tokyo's
  special wards. The 24th is `淀川区`, which is Osaka's Yodogawa ward and which
  appears 45 times WITH `大阪市` in front of it and once without. A rule saying
  "a bare 区 means Tokyo" would therefore be wrong on a real row in the real
  file, which is why WARD_CITY is enumerated and `淀川区` simply falls through
  to no city rather than to a nearby guess.
* **A ward that follows a 市 loses to the 市.** 1,720 addresses write the ward
  after the city (`大阪市中央区…`, `札幌市北区…`), and `中央区` and `北区` are
  ward names in Tokyo and in half the designated cities alike. Reading the
  leading token first, and the ward only when nothing precedes it, is what keeps
  those apart.

**It is `hq_city`, with the reason.** `Province` is 提出者の所在地, the address
the filer is registered at, which is its head office and not the site of the
work an extraordinary report describes. `city` in this product is a STATED job
location and `pipeline/classify.py` forbids inferring one; a head office is a
different fact, so it goes in the column for that fact and a Japanese row still
carries no stated city. `country` is Japan by construction, because every filer
here files with a Japanese finance bureau.

The map only EMITS a name; `vocab.normalize_city` decides whether it is a place
this site knows, so nothing here can invent a city the curated gazetteer has
never heard of. Nagoya, Kobe, Sapporo, Hiroshima, Kawasaki, Saitama, Chiba and
Sendai are recognised here and are not in that gazetteer today, so they resolve
to nothing; they are listed anyway so that adding one to the gazetteer is the
only step that pass would need.
"""

from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from datetime import date, datetime, timedelta, timezone

import requests

LIST_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
DOCUMENT_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/pdf/{doc_id}.pdf"
# Published as a permanent link for API users by the API specification itself
# (Version 2, 2026-06, page 86). The English list is used rather than the
# Japanese one because its COLUMN HEADERS are English and therefore far less
# fragile to parse; both carry identical data.
CODELIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelisteng/Edinetcode.zip"

COLLECTOR = "edinet_japan"
SOURCE_NAME = "EDINET, Financial Services Agency of Japan (extraordinary report)"

# Both code lists are cp932, NOT shift_jis. Verified 2026-07-30 on the real
# files: `shift_jis` raises on byte 0xfb at offset 35,244 of the Japanese list,
# because cp932 is Microsoft's superset with the NEC/IBM extended characters
# that Japanese company names actually use. Naming the narrower codec here
# would crash the run on a filer whose name contains one.
CODELIST_ENCODING = "cp932"

USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# 書類種別コード, from the specification's own reference table (page 88).
DOC_TYPE_EXTRAORDINARY = "180"   # 臨時報告書
DOC_TYPE_CORRECTION = "190"      # 訂正臨時報告書 — counted, never stored

# The one officer-change clause, canonical half-width form. See the module
# docstring: this is 企業内容等の開示に関する内閣府令 第19条第2項第9号,
# 代表取締役の異動.
OFFICER_CLAUSE = "第19条第2項第9号"

# Named rather than merely omitted, so a later reader sees a decision. Each of
# the first three has OFFICER_CLAUSE as a string prefix, which is precisely why
# matching is by whole element and never by `in`.
EXCLUDED_CLAUSES = {
    "第19条第2項第9号の2": "shareholder meeting resolutions, not an officer change",
    "第19条第2項第9号の3": "an AGM resolution modified or voted down",
    "第19条第2項第9号の4": "change of accounting auditor — an audit firm is not an employee",
    "第29条第2項第9号": "fund merger under the specified-securities ordinance, not an officer change",
}

# How far back a run reads. One API call per calendar day, so this is also the
# call count. Seven days on a weekly cron is one cadence plus six days of
# overlap, and overlap is free because already-seen URLs are skipped before
# anything is stored.
DEFAULT_DAYS = 7

MAX_DAYS = 366          # a backfill widens the window; it does not become a script
REQUEST_DELAY = 0.5     # the terms forbid 短時間における大量のアクセス
CODELIST_MAX_BYTES = 20 * 1024 * 1024

# ---------------------------------------------------------------------------
# The head-office address, as a fixed vocabulary. See the docstring's geography
# section for the two measurements these two maps encode.
# ---------------------------------------------------------------------------

# A municipality token that IS the city, read as the leading token of the
# address (after an optional prefecture). Ordered by measured volume on the
# 2026-08-14 list so a reader sees what the map is actually for.
MUNICIPALITY_CITY = {
    "大阪市": "Osaka",        # 530
    "名古屋市": "Nagoya",     # 229
    "横浜市": "Yokohama",     # 179
    "京都市": "Kyoto",        # 100
    "福岡市": "Fukuoka",      # 100
    "神戸市": "Kobe",         #  91
    "札幌市": "Sapporo",      #  67
    "広島市": "Hiroshima",    #  49
    "川崎市": "Kawasaki",     #  49
    "さいたま市": "Saitama",  #  35
    "千葉市": "Chiba",        #  32
    "仙台市": "Sendai",       #  29
}

# The 23 special wards of Tokyo, and ONLY those. A ward token that is not on
# this list places nothing: 淀川区 is Osaka's and appears in this file with no
# city in front of it, which is the whole reason the rule is a list. Ordered as
# the Tokyo Metropolitan Government orders them, so a reader can count 23.
WARD_CITY = {
    ward: "Tokyo" for ward in (
        "千代田区", "中央区", "港区", "新宿区", "文京区", "台東区", "墨田区",
        "江東区", "品川区", "目黒区", "大田区", "世田谷区", "渋谷区", "中野区",
        "杉並区", "豊島区", "北区", "荒川区", "板橋区", "練馬区", "足立区",
        "葛飾区", "江戸川区",
    )
}

# A leading prefecture, stripped before the municipality is read. Written out
# rather than generated: 東京都, 北海道 and the two 府 are the four that do not
# end in 県, and a regex that only knew 県 would read 東京都 as part of a ward
# name. Bounded at three characters before 県 because every prefecture name is
# one, two or three.
_PREFECTURE = re.compile(r"^(東京都|北海道|(?:京都|大阪)府|.{1,3}県)")

# The first administrative token after the prefecture. 市 before 区 is not an
# ordering preference, it is the whole disambiguation: 大阪市中央区 must read as
# Osaka and 中央区 alone as Tokyo.
_MUNICIPALITY = re.compile(r"^(.{1,6}?[市区郡町村])")

# Full-width to half-width digits. `currentReportReason` is typed 全半角 in the
# specification (page 47), so the clause may legitimately arrive as
# 「第１９条第２項第９号」. Everything is normalised before it is compared or
# displayed, and the SAME normalised string is what reaches raw_text — see the
# note on `_row`, and tests/test_edinet_japan.py, for why that identity is
# load-bearing rather than tidy.
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


class EdinetError(RuntimeError):
    """A window could not be read, or came back implausibly empty."""


def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def api_key_from_env() -> str:
    """The EDINET subscription key, or a loud failure.

    Deliberately NOT tolerant of an empty value. A GitHub workflow that maps a
    secret which does not exist sets the variable to the empty string, and this
    repo has already shipped that exact bug once: the first SEC leadership
    dispatch went green having stored nothing because an empty
    `EDGAR_USER_AGENT` produced a 403 that read as a quiet day. An empty key
    here would produce a body-level 401, which `fetch_list` also refuses, but
    failing at the first line with the name of the missing secret is a much
    shorter path to the cause.
    """
    key = (os.environ.get("EDINET_API_KEY_JP") or "").strip()
    if not key:
        raise EdinetError(
            "EDINET_API_KEY_JP is not set (or is empty). EDINET's v2 API "
            "requires a subscription key on every request; without it the host "
            "answers HTTP 200 carrying {'StatusCode': 401} in the body, which "
            "is indistinguishable from a day on which nothing was filed.")
    return key


def days_from_env(default_days: int | None = None) -> int:
    """How many days back to read. Set by the workflow, so a backfill is a
    longer window through the same path rather than a script of its own."""
    raw = (os.environ.get("TIT_EDINET_DAYS") or "").strip()
    if not raw:
        return default_days if default_days is not None else DEFAULT_DAYS
    if not re.fullmatch(r"\d{1,4}", raw):
        raise EdinetError(
            f"TIT_EDINET_DAYS holds {raw!r}, which is not a number of days")
    days = int(raw)
    if days < 1:
        raise EdinetError("TIT_EDINET_DAYS must be at least 1 day")
    if days > MAX_DAYS:
        raise EdinetError(
            f"TIT_EDINET_DAYS={days} would make {days} API calls in one run. "
            f"The cap is {MAX_DAYS}; EDINET's terms forbid bulk hammering.")
    return days


def window(days: int, *, today: datetime | None = None) -> list[str]:
    """The calendar days to ask for, oldest first, in the API's date format.

    The list endpoint takes ONE day per call, so the window is a list of dates
    rather than a range: there is no from/to parameter to widen.
    """
    end = (today or datetime.now(timezone.utc)).date()
    return [(end - timedelta(days=offset)).isoformat()
            for offset in range(days - 1, -1, -1)]


def document_url(doc_id: str) -> str | None:
    """The filing's own PDF permalink, or None if the id is not a document id.

    See the source-URL note in the module docstring for why this is not the
    viewer page: the viewer answers 200 with a search screen for any input,
    real or invented, so it can never be checked.
    """
    clean = (doc_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{8}", clean):
        return None
    return DOCUMENT_URL.format(doc_id=clean)


def _halfwidth(text: str) -> str:
    """Full-width digits to half-width, whitespace collapsed."""
    return re.sub(r"\s+", "", (text or "")).translate(_FULLWIDTH_DIGITS)


def officer_clauses(reason: str) -> tuple[str, ...]:
    """The comma-separated statutory clauses of one filing, normalised.

    `currentReportReason` joins multiple reasons with a comma, so a filing that
    reports a presidential change AND a subsidiary change carries both. Each
    element is normalised to half-width digits; nothing else about it is
    touched, because the clause vocabulary is the ordinance's and not ours.
    """
    return tuple(part for part in
                 (_halfwidth(p) for p in (reason or "").split(","))
                 if part)


def is_officer_change(reason: str) -> bool:
    """Whether this filing reports a change of representative director.

    Whole-element equality, never a substring test. `第19条第2項第9号の4` has
    the accepted clause as a prefix and is a change of ACCOUNTING AUDITOR, so
    `OFFICER_CLAUSE in reason` would file audit-firm changes as leadership
    signals — the bse_india auditor bug in a different disguise.
    """
    return OFFICER_CLAUSE in officer_clauses(reason)


def _status_of(payload: dict) -> tuple[str, str]:
    """The real status and message, from either error shape EDINET uses.

    HTTP is 200 on every error (spec page 82), and the body is shaped one way
    for 401/429 and another for 400/404/500 (page 84). Both are read here so
    that neither can be mistaken for an empty day.
    """
    if not isinstance(payload, dict):
        return "malformed", "response was not a JSON object"
    # 401 and 429 put the status at the top level, with no `metadata` at all.
    if "StatusCode" in payload:
        return str(payload.get("StatusCode")), str(payload.get("message") or "")
    meta = payload.get("metadata")
    if isinstance(meta, dict) and meta.get("status") is not None:
        return str(meta.get("status")), str(meta.get("message") or "")
    return "malformed", f"no status in payload (keys: {sorted(payload)[:8]})"


def fetch_list(day: str, key: str, *, timeout: int = 45, session=None) -> list[dict]:
    """One calendar day's document list, with metadata (`type=2`).

    Raises on any status that is not 200, including the ones EDINET reports
    inside an HTTP 200 body.
    """
    params = {"date": day, "type": "2", "Subscription-Key": key}
    get = (session or requests).get
    resp = get(LIST_URL, params=params, headers=_headers(), timeout=timeout)

    http_status = int(getattr(resp, "status_code", 0) or 0)
    try:
        payload = resp.json()
    except ValueError as exc:
        raise EdinetError(
            f"{day}: EDINET did not return JSON (HTTP {http_status}): "
            f"{str(getattr(resp, 'text', ''))[:160]!r}") from exc

    status, message = _status_of(payload)
    if status != "200":
        # Never let this become a zero. A 401 (expired key) and a 429
        # (throttled) both arrive as HTTP 200 and would otherwise read as a day
        # on which Japan filed nothing.
        raise EdinetError(
            f"{day}: EDINET reported status {status} ({message!r}) inside an "
            f"HTTP {http_status} response. This is the documented behaviour, "
            f"not a transport error: an invalid key, a throttled run and a bad "
            f"parameter all answer HTTP 200.")

    results = payload.get("results")
    if not isinstance(results, list):
        raise EdinetError(
            f"{day}: status was 200 but there is no `results` list "
            f"(keys: {sorted(payload)[:8]}). The response shape has changed.")
    return results


def head_office_city(address: str) -> str:
    """The filer's head-office city, or "" when the address does not name one.

    Reads the `Province` column of the EDINET code list, which is the address
    the filer is registered at. The return is a city NAME and not a stored
    value: the caller hands it to `vocab.normalize_city`, which is what decides
    whether the site knows the place. Nothing here invents one.

        新宿区西新宿六丁目５番１号        -> Tokyo
        東京都港区赤坂１丁目             -> Tokyo
        大阪市中央区平野町２丁目          -> Osaka   (the 市 wins over the 区)
        札幌市北区北七条西                -> Sapporo (likewise; not Tokyo's 北区)
        淀川区西中島１丁目９番２０号      -> ""      (Osaka's ward, written bare)
        アメリカ合衆国４８６７４ミシガン州  -> ""      (a foreign address)
    """
    text = (address or "").strip()
    if not text:
        return ""
    prefecture = _PREFECTURE.match(text)
    rest = text[prefecture.end():] if prefecture else text

    token = _MUNICIPALITY.match(rest)
    if token:
        name = token.group(1)
        if name in MUNICIPALITY_CITY:
            return MUNICIPALITY_CITY[name]
        # A ward is only a Tokyo ward when nothing put it inside another city,
        # and only when it is one of the 23. Everything else places nothing.
        if name in WARD_CITY and (not prefecture or prefecture.group(1) == "東京都"):
            return WARD_CITY[name]

    # `東京都` with an address shape this map does not recognise is still Tokyo:
    # the prefecture is the city there, which is true of no other prefecture in
    # Japan and is why this is one line and not a 47-entry fallback.
    if prefecture and prefecture.group(1) == "東京都":
        return "Tokyo"
    return ""


def fetch_filer_directory(*, timeout: int = 60, session=None) -> dict[str, dict]:
    """EDINET code -> {"name": English name, "hq_city": city or ""}.

    ONE fetch for both facts. They come out of the same two columns of the same
    row of the same download, and splitting them into two functions would mean
    two requests to a service whose terms forbid 短時間における大量のアクセス.
    """
    get = (session or requests).get
    resp = get(CODELIST_URL,
               headers={"User-Agent": USER_AGENT}, timeout=timeout)
    if int(getattr(resp, "status_code", 0) or 0) != 200:
        raise EdinetError(
            f"{CODELIST_URL} returned {resp.status_code}. Without the code "
            f"list there is no English name for any filer, and a Japanese name "
            f"produces an empty company slug, so the run stops here rather "
            f"than storing rows that would all collide.")

    body = resp.content
    if not body or len(body) > CODELIST_MAX_BYTES:
        raise EdinetError(
            f"the EDINET code list was {len(body or b'')} bytes, which is "
            f"outside the plausible range for it.")

    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise EdinetError(
                f"the EDINET code list zip holds no CSV (members: "
                f"{archive.namelist()[:5]}).")
        raw = archive.read(names[0])
    except zipfile.BadZipFile as exc:
        raise EdinetError("the EDINET code list was not a readable zip") from exc

    text = raw.decode(CODELIST_ENCODING, errors="strict")
    lines = text.splitlines()
    # Row 0 is a download banner ("Date of download data creation,..."), and the
    # real header is row 1.
    if len(lines) < 3:
        raise EdinetError("the EDINET code list has no rows")
    reader = csv.DictReader(io.StringIO("\n".join(lines[1:])))

    out: dict[str, dict] = {}
    for row in reader:
        code = (row.get("EDINET Code") or "").strip()
        english = (row.get("Submitter Name（alphabetic）") or "").strip()
        if code and english:
            # `Province` is the header the English list gives 提出者の所在地.
            # A missing column reads as "" and places nothing, which is the
            # right failure: an unplaced filer is ordinary here (27% of the
            # named ones), so this may not raise the way a missing name does.
            out[code] = {
                "name": english,
                "hq_city": head_office_city(row.get("Province") or ""),
            }
    if not out:
        raise EdinetError(
            "the EDINET code list parsed to zero English names. Its column "
            "headers have moved; this collector reads 'EDINET Code' and "
            "'Submitter Name（alphabetic）' (note the FULL-WIDTH parentheses).")
    return out


def fetch_english_names(*, timeout: int = 60, session=None) -> dict[str, str]:
    """EDINET code -> the filer's own English name.

    Kept as its own name because the romanisation rule in the module docstring
    is about names alone and reads better without the address beside it. It is
    a view over `fetch_filer_directory` and makes no second request.
    """
    return {code: entry["name"]
            for code, entry in fetch_filer_directory(
                timeout=timeout, session=session).items()}


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _date(value: str) -> str | None:
    """'2026-03-26 14:05' -> '2026-03-26'."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text.split(" ")[0].split("T")[0]).isoformat()
    except ValueError:
        return None


def is_withheld(entry: dict) -> bool:
    """Whether EDINET says this document is withdrawn, withheld or expired.

    Each of these is a documented status field (spec pages 47-48), and each one
    means a stored citation would not resolve for a reader:

    * `withdrawalStatus` "1" is a withdrawal notice, "2" a withdrawn document;
    * `disclosureStatus` "1"/"2" is a document a finance bureau has withheld;
    * `legalStatus` "0" is a document whose public viewing period has expired,
      so its PDF permalink no longer serves anything.
    """
    if str(entry.get("withdrawalStatus") or "0").strip() != "0":
        return True
    if str(entry.get("disclosureStatus") or "0").strip() not in ("0", "3"):
        return True
    if str(entry.get("legalStatus") or "1").strip() == "0":
        return True
    return False


def _row(entry: dict, filers: dict[str, dict]) -> dict | None:
    """One document's raw dict, or None if it is not a storable officer change.

    `filers` is `fetch_filer_directory`'s map. A plain code -> name mapping is
    still accepted, because that is what every existing caller and fixture
    passes, and a collector should not go red over the shape of a lookup.
    """
    if str(entry.get("docTypeCode") or "").strip() != DOC_TYPE_EXTRAORDINARY:
        return None
    reason = str(entry.get("currentReportReason") or "")
    if not is_officer_change(reason):
        return None
    if is_withheld(entry):
        return None

    doc_id = str(entry.get("docID") or "").strip()
    url = document_url(doc_id)
    edinet_code = str(entry.get("edinetCode") or "").strip()
    filer = filers.get(edinet_code) or {}
    if isinstance(filer, str):
        filer = {"name": filer, "hq_city": ""}
    company = (filer.get("name") or "").strip()
    hq_city = (filer.get("hq_city") or "").strip()
    filed_at = _date(str(entry.get("submitDateTime") or ""))
    sec_code = re.sub(r"\D", "", str(entry.get("secCode") or ""))

    # No English name on file means no usable company key: `vocab.company_key`
    # passes Japanese through unchanged and the resulting slug is empty, so
    # every such employer would collide with every other. Declined, and counted
    # by the caller.
    if not (url and company and filed_at and edinet_code):
        return None

    # The clause is written in its canonical half-width form BOTH here and in
    # `as_classified`'s summary, and that identity is what keeps the record
    # storable. `validate._NUMBER` uses `\d`, which matches full-width digits in
    # Python, so a summary saying 第19条第2項第9号 against a raw_text saying
    # 第19条第２項第９号 tokenises as {19,2,9} against {19,２,９} and
    # `assert_figures_are_sourced` discards the whole record for "inventing" 2
    # and 9. Demonstrated on 2026-07-29 before this line was written; pinned by
    # test_a_full_width_clause_still_round_trips.
    clause = OFFICER_CLAUSE
    description = _squeeze(str(entry.get("docDescription") or ""))

    body = (
        f"{company} (EDINET code {edinet_code}) filed an extraordinary report "
        f"({clause}) with the Financial Services Agency of Japan on "
        f"{filed_at}. Article 19, paragraph 2, item 9 of the Cabinet Office "
        f"Ordinance on Disclosure of Corporate Affairs requires a company to "
        f"file an extraordinary report when its representative director "
        f"changes, and EDINET publishes that statutory reason as a typed field "
        f"on the filing rather than as prose. The filing itself is the "
        f"document linked above. It states which representative director "
        f"changed and on what date; this record deliberately does not repeat "
        f"the person's name or the direction of the change, because neither is "
        f"in the typed metadata and reading the document body is not free."
    )
    if description:
        # Quoted, so a figure inside the filer's own wording is bounded by a
        # non-space character on both sides. Never repeated in the summary.
        body += f"\n\nEDINET describes the document as: \"{description}\""

    return {
        "raw_text": body,
        "headline": f"{company}: change of representative director filed with EDINET",
        "source_url": url,
        "source_name": SOURCE_NAME,
        "discovery_url": url,
        "published_date": filed_at,
        "company": company,
        "country": "Japan",
        # The registered head office, never a job location. See the geography
        # section of the module docstring: `Province` is where the filer is
        # registered, and an extraordinary report does not say where the
        # representative director sat.
        "hq_city": hq_city,
        "doc_id": doc_id,
        "edinet_code": edinet_code,
        "sec_code": sec_code,
        "clause": clause,
        "doc_description": description,
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def collect(queries=None, *, days: int | None = None, today: datetime | None = None,
            session=None, api_key: str | None = None) -> list[dict]:
    """Every representative-director change filed in the window.

    `queries` is accepted and ignored so this collector is interchangeable with
    the others in run_collect: there is nothing to search for, because the
    mandated clause IS the population.
    """
    key = api_key or api_key_from_env()
    filers = fetch_filer_directory(session=session)
    days_back = days if days is not None else days_from_env()
    wanted = window(days_back, today=today)

    out: list[dict] = []
    seen: set[str] = set()
    read = extraordinary = officer = corrections = declined = 0

    for day in wanted:
        if session is None:
            import time
            time.sleep(REQUEST_DELAY)
        rows = fetch_list(day, key, session=session)
        read += len(rows)
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            doc_type = str(entry.get("docTypeCode") or "").strip()
            if doc_type == DOC_TYPE_CORRECTION:
                corrections += 1
                continue
            if doc_type != DOC_TYPE_EXTRAORDINARY:
                continue
            extraordinary += 1
            if not is_officer_change(str(entry.get("currentReportReason") or "")):
                continue
            officer += 1
            item = _row(entry, filers)
            if not item:
                declined += 1
                continue
            if item["source_url"] in seen:
                continue
            seen.add(item["source_url"])
            out.append(item)

    LAST_RUN.update({
        "read": read, "extraordinary": extraordinary, "officer": officer,
        "corrections_skipped": corrections, "declined": declined,
        "stored_candidates": len(out), "days": days_back,
    })
    print(f"[{COLLECTOR}] {wanted[0]}..{wanted[-1]}: {read} documents read, "
          f"{extraordinary} extraordinary reports, {officer} reporting "
          f"{OFFICER_CLAUSE}, {len(out)} usable "
          f"({declined} declined for no English name or no document id, "
          f"{corrections} corrections skipped)")
    return out


# `run_collect` reads this to judge health on what a run READ rather than on
# what it emitted. Without it a source that legitimately finds nothing on a
# quiet week is `degraded` every week, which is how a health page teaches
# people to ignore it. There is deliberately NO minimum-rows floor of the kind
# bse_india carries: India files ~250 leadership disclosures a week and a zero
# there is provably a breakage, whereas Japan's clause is one clause covering
# one role across ~3,800 filers, so a genuinely empty week is ordinary. The
# floor that WOULD be honest here cannot be set until a real run has measured
# the rate; see the TECHLOG entry.
LAST_RUN: dict = {}


def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated.

    Every value is read off the API response or is a fixed editorial line, so
    nothing on the record can be something a model believed. No figure is
    stored at all: the metadata contains no amount, no headcount and no
    person, and the only numerals that reach the summary are the clause and the
    filing date, both written from the same normalised strings that are in
    `raw_text`.
    """
    company = item["company"]
    return {
        "company": company,
        "pillar": "leadership_change",
        # ALWAYS neutral, and never `hiring`. Item 9 covers a person becoming a
        # representative director and a person ceasing to be one under ONE
        # clause, so the typed metadata cannot tell an arrival from a
        # departure. Guessing would make half of these rows wrong; the
        # honest answer is that this source records that leadership changed.
        # Never `displacement` either: one executive leaving is not a workforce
        # reduction, and workforce reductions are the sibling tracker's scope.
        "signal_direction": "neutral",
        "headline": item["headline"],
        "summary": (
            f"{company} filed an extraordinary report with Japan's Financial "
            f"Services Agency on {item['published_date']}, under "
            f"{item['clause']} of the Cabinet Office Ordinance on Disclosure "
            f"of Corporate Affairs, which is the clause requiring disclosure "
            f"of a change of representative director."
        ),
        "talent_readthrough": (
            "A Japanese listed company must file an extraordinary report when "
            "its representative director changes, and EDINET publishes that "
            "reason as a typed clause rather than as prose, so this is a "
            "complete rather than selective feed of chief-executive change in "
            "Japan: it covers the mid-cap employers no outlet writes about on "
            "the same basis as the large ones. Read it narrowly. The clause "
            "covers the representative director alone, not the wider board and "
            "not senior management, and it does not say whether the person "
            "arrived or left. It also exempts a change already described in "
            "the annual report, which is the commonest timing for a Japanese "
            "succession, so this is a floor on Japanese leadership change "
            "rather than a count of it."
        ),
        "country": "Japan",
        # The filer's REGISTERED HEAD OFFICE, read off the code list, and
        # deliberately not `city`. `city` is a stated job location in this
        # product; a head office is where the company is registered, which the
        # `hq_*` columns exist to carry separately. `validate.py` puts this
        # through the curated gazetteer, so a municipality the site does not
        # know stores nothing rather than a new category.
        "headquarters_city": item.get("hq_city") or "",
        # Read from the source: every filer on this endpoint files with a
        # Japanese finance bureau under the Financial Instruments and Exchange
        # Act, which is a disclosure obligation of a public issuer.
        "employer_type": "public",
        # A statutory filing made TO the regulator that publishes it, so the
        # same class of host as sec.gov. `infer_confidence` caps this at what
        # the host is worth, so it lands at 'verified' only while
        # disclosure2dl.edinet-fsa.go.jp is a listed primary source domain.
        "confidence": "verified",
    }
