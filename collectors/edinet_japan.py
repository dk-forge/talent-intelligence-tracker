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

GEOGRAPHY IS NOT PARSED. The code list's address field is a ward-level Japanese
address with full-width digits and, for the Tokyo wards that hold most large
filers, no prefecture at all — `新宿区西新宿六丁目５番１号` never says Tokyo.
Deriving a city would need a municipality vocabulary of ~1,900 entries, and
guessing from it is how `ats_boards` once turned "Cambridge, MA" into Morocco.
`country` is Japan by construction, because every filer here files with a
Japanese finance bureau, and no city is stored.
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


def fetch_english_names(*, timeout: int = 60, session=None) -> dict[str, str]:
    """EDINET code -> the filer's own English name, from the official list.

    This is the only non-API fetch, and the specification publishes it as a
    permanent link for exactly this use (page 86). It is required rather than
    decorative: see the romanisation note in the module docstring.
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

    out: dict[str, str] = {}
    for row in reader:
        code = (row.get("EDINET Code") or "").strip()
        english = (row.get("Submitter Name（alphabetic）") or "").strip()
        if code and english:
            out[code] = english
    if not out:
        raise EdinetError(
            "the EDINET code list parsed to zero English names. Its column "
            "headers have moved; this collector reads 'EDINET Code' and "
            "'Submitter Name（alphabetic）' (note the FULL-WIDTH parentheses).")
    return out


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


def _row(entry: dict, english_names: dict[str, str]) -> dict | None:
    """One document's raw dict, or None if it is not a storable officer change."""
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
    company = english_names.get(edinet_code, "").strip()
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
    english_names = fetch_english_names(session=session)
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
            item = _row(entry, english_names)
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
