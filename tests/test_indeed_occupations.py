"""Indeed Hiring Lab occupation/country reference feed: offline, recorded fixtures.

The fixtures are real Hiring Lab rows (CC BY 4.0) thinned to every 7th day and
three categories, recorded 2026-10-05: US and GB publish a category file, EA
does not, which is the shape the live repository has.
"""
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import indeed_archive
import indeed_occupations as io_
import reference_freshness
import reference_store
from build_indeed_index import SchemaError

FIX = Path(__file__).parent / "fixtures" / "indeed_occupations"


def _inputs(countries=io_.COUNTRIES):
    agg, sec = io_.read_dir(FIX)
    # The fixture records three aggregates; the other eight reuse EA's shape
    # under their own country code so the "all 11 required" rule is exercised.
    ea = agg["EA"]
    for c in countries:
        if not agg.get(c):
            agg[c] = ea.replace(",EA,", f",{c},")
    # Six countries publish a category file live; reuse GB's for the four
    # unrecorded ones so the minimum is met.
    for c in ("AU", "CA", "DE", "FR"):
        sec[c] = sec["GB"].replace(",GB,", f",{c},")
    return agg, sec


def test_build_covers_all_eleven_and_records_unpublished_categories():
    agg, sec = _inputs()
    p = io_.build(agg, sec, now=datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert sorted(p["countries"]) == sorted(io_.COUNTRIES)
    assert p["categories_unpublished"] == ["EA", "ES", "IE", "IT", "NL"]
    us = p["countries"]["US"]
    assert set(us["categories"]) == {"Accounting", "Nursing", "Software Development"}
    assert p["countries"]["EA"]["categories"] is None
    assert p["rows"] == 11 + 6 * 3
    assert p["as_of"] == "2026-09-25"
    assert "CC BY 4.0" in p["attribution"] and p["licence"] == "CC BY 4.0"


def test_values_are_as_published_and_changes_are_ours():
    agg, sec = _inputs()
    sw = io_.build(agg, sec)["countries"]["US"]["categories"]["Software Development"]
    rows = [l.split(",") for l in (FIX / "job_postings_by_sector_US.csv").read_text().splitlines()[1:]]
    pub = {r[0]: float(r[2]) for r in rows if r[4] == "Software Development" and r[3] == "total postings"}
    assert sw["index"] == pub["2026-09-25"]
    assert sw["chg_4w"] == round(pub["2026-09-25"] - pub["2026-08-28"], 2)
    assert sw["chg_52w"] == round(pub["2026-09-25"] - pub["2025-09-26"], 2)
    assert len(sw["weekly"]) == io_.WEEKS_KEPT and sw["weekly"][-1] == ["2026-09-25", sw["index"]]


def test_total_uses_the_seasonally_adjusted_total_postings_series():
    agg, sec = _inputs()
    us = io_.build(agg, sec)["countries"]["US"]["total"]
    last = [l for l in (FIX / "aggregate_job_postings_US.csv").read_text().splitlines()
            if l.startswith("2026-09-25,US") and l.endswith("total postings")][0]
    assert us["index"] == float(last.split(",")[2])


def test_a_missing_aggregate_fails_loudly():
    agg, sec = _inputs()
    agg["IT"] = None
    with pytest.raises(SchemaError, match="IT"):
        io_.build(agg, sec)


def test_losing_category_files_fails_loudly():
    agg, sec = _inputs()
    for c in ("AU", "CA"):
        sec[c] = None
    with pytest.raises(SchemaError, match="category file"):
        io_.build(agg, sec)


def test_a_changed_schema_fails_loudly():
    agg, sec = _inputs()
    sec["US"] = sec["US"].replace("display_name", "category_name")
    with pytest.raises(SchemaError, match="display_name"):
        io_.build(agg, sec)


def test_fetch_treats_a_404_category_file_as_unpublished_and_other_errors_as_fatal():
    agg, sec = _inputs()

    def get(url):
        c = url.rsplit("_", 1)[-1][:2]
        if "sector" in url:
            return (200, sec[c]) if sec.get(c) else (404, "404: Not Found")
        return 200, agg[c]
    a, s = io_.fetch(get)
    assert s["EA"] is None and s["US"]
    with pytest.raises(SchemaError):
        io_.fetch(lambda url: (500, "") if "sector" in url else (200, agg[url.rsplit("_", 1)[-1][:2]]))


def test_store_writes_manifest_with_attribution_and_refuses_a_shrink(tmp_path):
    agg, sec = _inputs()
    p = io_.build(agg, sec)
    m = io_.store(p, tmp_path)
    assert m["rows"] == p["rows"] and m["licence"] == "CC BY 4.0"
    assert "Indeed Hiring Lab" in m["attribution"]
    assert json.loads((tmp_path / "indeed_occupations" / "latest.json").read_text())["rows"] == p["rows"]
    p2 = dict(p, rows=3)
    with pytest.raises(reference_store.StoreError, match="truncated"):
        io_.store(p2, tmp_path)


def test_compact_drops_weekly_arrays_only():
    agg, sec = _inputs()
    p = io_.build(agg, sec)
    c = io_.compact(p)
    assert "weekly" not in c["countries"]["US"]["total"]
    assert c["countries"]["US"]["categories"]["Nursing"]["index"] == p["countries"]["US"]["categories"]["Nursing"]["index"]
    assert len(json.dumps(c)) < len(json.dumps(p))


def test_freshness_flags_a_stalled_collector_and_a_stalled_source(tmp_path):
    agg, sec = _inputs()
    io_.store(io_.build(agg, sec), tmp_path)
    now = datetime.now(timezone.utc)
    rows = reference_freshness.check(tmp_path, now)
    assert rows[0]["source"] == "indeed_occupations"
    later = datetime(2027, 1, 1, tzinfo=timezone.utc)
    problems = reference_freshness.check(tmp_path, later)[0]["problems"]
    assert any("collector" in x for x in problems) and any("source" in x for x in problems)


def test_freshness_treats_a_never_stored_source_as_stale(tmp_path):
    rows = reference_freshness.check(tmp_path)
    assert rows[0]["problems"] == ["never stored (no MANIFEST.json)"]


class _Resp:
    def __init__(self, data=None):
        self._d = data or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class _Session:
    def __init__(self, open_issues):
        self.open, self.calls = open_issues, []

    def get(self, url, **k):
        self.calls.append(("GET", url))
        return _Resp(self.open)

    def post(self, url, **k):
        self.calls.append(("POST", url, k.get("json")))
        return _Resp({"number": 7})

    def patch(self, url, **k):
        self.calls.append(("PATCH", url, k.get("json")))
        return _Resp()


def test_one_issue_opened_then_edited_then_closed():
    stale = [{"source": "indeed_occupations", "problems": ["x"]}]
    fresh = [{"source": "indeed_occupations", "problems": []}]
    s = _Session([])
    assert reference_freshness.sync_issue(stale, repo="o/r", token="t", session=s) == "opened #7"
    existing = [{"number": 9, "title": reference_freshness.ISSUE_TITLE}]
    s = _Session(existing)
    assert reference_freshness.sync_issue(stale, repo="o/r", token="t", session=s) == "edited #9"
    assert not any(c[0] == "POST" for c in s.calls)
    s = _Session(existing)
    assert reference_freshness.sync_issue(fresh, repo="o/r", token="t", session=s) == "closed #9"
    assert reference_freshness.sync_issue(fresh, repo="o/r", token="t", session=_Session([])) == "no issue"


def test_monthly_snapshot_is_small_gzipped_and_byte_stable(tmp_path):
    agg, sec = _inputs()
    io_.store(io_.build(agg, sec), tmp_path)
    out = indeed_archive.snapshot("2026-10", tmp_path, tmp_path / "arch")
    first = out.read_bytes()
    indeed_archive.snapshot("2026-10", tmp_path, tmp_path / "arch")
    assert out.read_bytes() == first
    assert json.loads(gzip.decompress(first))["source"] == "indeed_occupations"


def test_publish_skips_without_a_key(monkeypatch):
    monkeypatch.delenv("WP_API_KEY", raising=False)
    assert reference_store.publish("indeed_occupations", {}) == "skipped"


PLUGIN = Path(__file__).parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"


def test_every_stored_source_has_a_site_allowlist_entry_and_attribution():
    import re
    php = (PLUGIN / "includes" / "reference_data.php").read_text()
    allow = re.findall(r"^\s{8}'([a-z0-9_]+)' => array\(", php, re.M)
    assert sorted(allow) == sorted(reference_store.SOURCES)
    assert php.count("'licence' =>") == len(reference_store.SOURCES)
    assert "tit_reference_attribution_html" in (PLUGIN / "includes" / "sources.php").read_text()
    assert "tit_require('includes/reference_data.php');" in (
        PLUGIN / "talent-intelligence-tracker.php").read_text()
