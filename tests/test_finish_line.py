"""finish_line.py / finish_line_issue.py: the checker logic, offline (no network, no browser)."""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import finish_line as fl  # noqa: E402
import finish_line_issue as fi  # noqa: E402

BASE = "https://asktherecruiter.com/blog/talent-intelligence-tracker/"
TODAY = dt.date(2026, 11, 18)


def _result(**over):
    r = {"name": "T", "bad_pages": [], "bad_links": [], "truncated": False, "max_pages": 1, "max_links": 1,
         "unsourced": {}, "cta_missing": [], "data_date": "2026-11-17", "data_age_days": 1}
    r.update(over)
    return r


def test_all_green_is_pass():
    assert fl.verdict(_result()) == []


def test_each_failure_is_named():
    r = _result(bad_pages=[(BASE + "x/", 404)], bad_links=[("https://asktherecruiter.com/y", 500, BASE)],
                unsourced={BASE: ["880,000 jobs cut"]}, cta_missing=[(BASE, 390)],
                data_date="2026-11-01", data_age_days=17)
    why = " | ".join(fl.verdict(r))
    for part in ("not 200", "broken internal link", "without a source link", "CTA not visible", "17 days ago"):
        assert part in why


def test_could_not_check_is_never_a_pass():
    assert fl.verdict(_result(data_date=None, data_age_days=None))
    assert fl.verdict(_result(browser_error="CDPUnavailable: no chrome"))
    assert fl.verdict(_result(truncated=True))


def test_freshness_comes_from_the_data_endpoint_not_page_metadata():
    status = {"version": "2", "pipeline_since": "2026-11-18T17:00:50+00:00", "last_updated": "2026-11-02T17:00:50+00:00"}
    assert fl.data_date(status, "last_updated", TODAY) == dt.date(2026, 11, 2)
    rows = {"rows": [{"captured_at": "2026-11-17 23:31:24"}, {"captured_at": "2026-11-18 09:13:22"},
                     {"captured_at": "2027-01-01 00:00:00"}]}
    assert fl.data_date(rows, "captured_at", TODAY) == dt.date(2026, 11, 18)
    assert fl.data_date(None, "last_updated", TODAY) is None
    assert not hasattr(fl, "newest_date")  # the HTML-date reader (JSON-LD dateModified is always today) is gone


def test_crawl_covers_every_sibling_route_family():
    other = "https://asktherecruiter.com/blog/company-layoffs/"
    site = {BASE: (200, ""), other: (200, f'<a href="{other}acme/">a</a>'), other + "acme/": (404, "")}
    pages, bad_pages, _, _ = fl.crawl([BASE, other], 10, 10, 0, get=lambda u: site[u])
    assert set(pages) == set(site)
    assert bad_pages == [(other + "acme/", 404)]
    assert fl.in_scope(other + "acme/", (BASE, other))
    # A family with no index page is crawled when linked, not started from.
    state = "https://asktherecruiter.com/blog/state-layoffs/"
    site2 = {BASE: (200, f'<a href="{state}ca/">ca</a>'), state + "ca/": (200, "")}
    pages, *_ = fl.crawl([BASE], 10, 10, 0, get=lambda u: site2[u], scope=(state,))
    assert set(pages) == set(site2)


def test_scope_and_normalise():
    assert fl.normalise("/blog/talent-intelligence-tracker/places/#x", BASE) == BASE + "places/"
    assert fl.normalise("mailto:a@b.c", BASE) is None
    assert fl.in_scope(BASE + "company/box/", BASE)
    assert not fl.in_scope(BASE + "feed/", BASE)
    assert not fl.in_scope("https://asktherecruiter.com/resume-score?utm_source=x", BASE)
    assert fl.is_internal("https://asktherecruiter.com/x") and not fl.is_internal("https://sec.gov/x")


def test_crawl_finds_bad_pages_and_bad_links():
    site = {
        BASE: (200, f'<a href="{BASE}a/">a</a><a href="{BASE}gone/">g</a><a href="/pricing">p</a><a href="https://sec.gov/f">s</a>'),
        BASE + "a/": (200, "<p>ok</p>"),
        BASE + "gone/": (404, ""),
        "https://asktherecruiter.com/pricing": (500, ""),
    }
    pages, bad_pages, bad_links, truncated = fl.crawl(BASE, 10, 10, 0, get=lambda u: site[u])
    assert set(pages) == {BASE, BASE + "a/", BASE + "gone/"}
    assert bad_pages == [(BASE + "gone/", 404)]
    assert ("https://asktherecruiter.com/pricing", 500, BASE) in bad_links
    assert not truncated


def test_crawl_cap_is_reported_as_truncated():
    site = {BASE: (200, f'<a href="{BASE}a/">a</a>'), BASE + "a/": (200, "")}
    assert fl.crawl(BASE, 1, 10, 0, get=lambda u: site[u])[3] is True


def test_markdown_says_fail_and_why():
    r = _result(cta_missing=[(BASE, 390)])
    r.update(reasons=fl.verdict(r), checked_at="now", pages_checked=1, browser_pages=1, numbers_total=0)
    md = fl.markdown(r)
    assert "**FAIL**" in md and "CTA" in md


def test_issue_plan_dedupes_by_exact_title():
    issues = [{"title": "Finish line FAIL: x", "number": 7, "state": "open"},
              {"title": "Finish line FAIL: x", "number": 8, "state": "open", "pull_request": {}}]
    assert fi.plan(fi.find_open(issues, "Finish line FAIL: x"), "t", "ATR") == ("repeat", 7)
    assert fi.plan(fi.find_open(issues, "other"), "t", "ATR") == ("create", ["Claude", "ATR"])


def test_issue_filer_without_token_does_not_fail(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TODO_ISSUES_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    body = tmp_path / "b.md"
    body.write_text("x")
    assert fi.main(["--title", "t", "--product", "ATR", "--body-file", str(body)],
                   call=lambda *a: (_ for _ in ()).throw(AssertionError("no call"))) == 0
    assert "NOT FILED" in capsys.readouterr().out


def test_partial_browser_coverage_is_never_a_pass():
    why = " | ".join(fl.verdict(_result(browser_pages=40, pages_ok=900)))
    assert "covered 40 of 900 pages" in why
    assert fl.verdict(_result(browser_pages=900, pages_ok=900)) == []


def test_small_counts_are_numbers_but_dates_times_and_units_are_not():
    import re
    hits = re.findall(fl.NUM_PATTERN, "42 jobs added, 31 July, Nov 18, 9:13 AM, 7 days, 5 of 9, $12m, 1,234, 95%, 480")
    assert hits == ["42", "$12m", "1,234", "95%", "480"]
    assert "__NUM__" not in fl.numbers_js() and "new RegExp(" in fl.numbers_js()


def test_the_card_is_the_row_not_a_badge_strip_inside_it():
    js = fl.numbers_js()
    assert "block.closest(CARD_STRUCT) || block.closest(CARD_CLASS)" in js
    assert "const CARD_STRUCT = 'tr,li,article,figure'" in js
    assert ':not([class*="card-"])' in js  # .tit-card-badges is not a card


def test_numbers_are_judged_on_the_block_text_not_one_text_node():
    import re
    js = fl.numbers_js()
    assert "blockText.match(NUM)" in js and "t.textContent.match(NUM)" not in js
    # What the page sees once markup is flattened: "<strong>42</strong> jobs" -> "42 jobs".
    assert re.findall(fl.NUM_PATTERN, "42 jobs added") == ["42"]


def test_a_same_site_permalink_is_not_a_citation():
    js = fl.numbers_js()
    assert "new URL(a.href, location.href).host !== location.host" in js
    assert "source|citation|cite" in js


def test_publisher_links_with_utm_still_count_and_only_our_cta_is_skipped():
    js = fl.numbers_js()
    assert "/utm_/.test(h)" not in js
    assert "OWN_CTA" in js and "asktherecruiter" in js


def test_freshness_ignores_collector_runs_that_stored_nothing():
    import datetime as dt
    payload = {"collectors": {"a": {"run_at": "2026-11-18T09:00:00+00:00", "items_stored": 0},
                              "b": {"run_at": "2026-11-10T09:00:00+00:00", "items_stored": 5}}}
    assert fl.data_date(payload, "run_at", dt.date(2026, 11, 18)) == dt.date(2026, 11, 10)


def test_freshness_reads_source_health_not_the_top_20_notable_rows():
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/finish-line.yml").read_text()
    assert "talent/v1/source-health\" --fresh-key run_at" in wf
    assert "per_page=20" not in wf


def test_non_ascii_links_are_requested_percent_encoded():
    # Run 44386250165: ?country=Türkiye was reported broken (status 0) because
    # urllib refused the raw character; a browser encodes it and gets 200.
    u = fl.iri_to_uri("https://asktherecruiter.com/blog/ai-layoff-tracker/?country=Türkiye&company=Ministère%20de%20l'Education")
    assert u == ("https://asktherecruiter.com/blog/ai-layoff-tracker/?country=T%C3%BCrkiye"
                 "&company=Minist%C3%A8re%20de%20l'Education")
    assert fl.iri_to_uri("https://x.test/a%20b?q=1") == "https://x.test/a%20b?q=1"


def test_429_is_retried_but_a_lasting_429_is_still_reported(monkeypatch=None):
    calls = []
    answers = iter([(429, ""), (200, "<html></html>")])
    orig_once, orig_sleep = fl._fetch_once, fl.time.sleep
    try:
        fl._fetch_once = lambda url, timeout: (calls.append(url), next(answers))[1]
        fl.time.sleep = lambda s: None
        assert fl.fetch("https://x.test/feed")[0] == 200
        assert len(calls) == 2
        fl._fetch_once = lambda url, timeout: (429, "")
        assert fl.fetch("https://x.test/feed")[0] == 429
        fl._fetch_once = lambda url, timeout: (404, "")
        assert fl.fetch("https://x.test/gone")[0] == 404
        # The back-off has a per-run budget so a pacing host cannot time the job out.
        fl._PAUSE_BUDGET, slept = 0, []
        fl.time.sleep = slept.append
        fl._fetch_once = lambda url, timeout: (429, "")
        assert fl.fetch("https://x.test/feed")[0] == 429 and slept == []
    finally:
        fl._fetch_once, fl.time.sleep = orig_once, orig_sleep
        fl._PAUSE_BUDGET = 1800
