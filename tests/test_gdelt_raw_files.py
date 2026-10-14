"""gdelt discovery from GDELT's raw 15-minute GKG files, not the DOC API (#257).

The DOC 2.0 API answered 429 on almost every request from three unrelated
networks in October 2026, so the collector stored nothing. The raw update files
at data.gdeltproject.org are static downloads with no request-rate limit. No
network here: every zip is built in the test.
"""
import io
import zipfile

import pytest

import run_collect
import source_registry as registry
from collectors import gdelt


def gkg_row(url, title, themes="", stamp="20261014101500"):
    cols = [""] * 27
    cols[0] = f"{stamp}-1"
    cols[1] = stamp
    cols[2] = "1"
    cols[3] = url.split("/")[2].removeprefix("www.")
    cols[4] = url
    cols[8] = ";".join(f"{t},12" for t in themes.split()) if themes else ""
    cols[26] = (f"<PAGE_PRECISEPUBTIMESTAMP>{stamp}</PAGE_PRECISEPUBTIMESTAMP>"
                f"<PAGE_TITLE>{title}</PAGE_TITLE>") if title else ""
    return "\t".join(cols)


def gkg_zip(*rows, stamp="20261014101500") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{stamp}.gkg.csv", "\n".join(rows) + "\n")
    return buf.getvalue()


HIRE = gkg_row("https://www.example.com/news/tesco-hire-28000-staff",
               "Tesco announce plans to hire 28,000 extra Christmas staff",
               "RECRUITMENT WB_2670_JOBS")
CEO = gkg_row("https://example.org/aegean-new-ceo",
              "Aegean Airlines appoints Michalis Kouveliotis as new CEO",
              "TAX_FNCACT_CEO APPOINTMENT")
PHRASE = gkg_row("https://example.net/a/b",
                 "Acme&#x2019;s hiring spree reaches Leeds", "")
NOISE = gkg_row("https://example.com/sport/match-report",
                "City win the derby in stoppage time", "SOC_GENERALCRIME")
THEMED_BUT_OFF = gkg_row("https://example.com/space",
                         "Mars rover finds new rock", "WB_2670_JOBS")
NO_TITLE = gkg_row("https://example.com/untitled", "", "RECRUITMENT")


def test_a_gkg_row_yields_url_title_domain_date_and_themes():
    rows = gdelt.parse_gkg(gkg_zip(PHRASE, NO_TITLE))
    assert len(rows) == 1, "a row with no title has nothing to classify"
    row = rows[0]
    assert row["url"] == "https://example.net/a/b"
    assert row["title"] == "Acme’s hiring spree reaches Leeds"  # unescaped
    assert row["domain"] == "example.net"
    assert row["date"] == "2026-10-14"


def test_the_production_phrases_still_select_without_a_theme():
    phrases = gdelt.query_phrases(registry.GDELT_QUERIES)
    assert "hiring spree" in phrases and "series a funding" in phrases
    assert gdelt.match(gdelt.parse_gkg(gkg_zip(PHRASE))[0], phrases)


def test_a_signal_theme_plus_an_employment_word_selects():
    phrases = gdelt.query_phrases(registry.GDELT_QUERIES)
    for row in gdelt.parse_gkg(gkg_zip(HIRE, CEO)):
        assert gdelt.match(row, phrases), row["title"]


def test_noise_and_a_theme_without_an_employment_title_are_dropped():
    phrases = gdelt.query_phrases(registry.GDELT_QUERIES)
    for row in gdelt.parse_gkg(gkg_zip(NOISE, THEMED_BUT_OFF)):
        assert not gdelt.match(row, phrases), row["title"]


def test_update_urls_walk_back_in_fifteen_minute_steps_and_cap():
    urls = gdelt.update_urls("20261014101500", 3)
    assert urls == [
        "https://data.gdeltproject.org/gdeltv2/20261014101500.gkg.csv.zip",
        "https://data.gdeltproject.org/gdeltv2/20261014100000.gkg.csv.zip",
        "https://data.gdeltproject.org/gdeltv2/20261014094500.gkg.csv.zip",
    ]
    assert len(gdelt.update_urls("20261014101500", 500)) == gdelt.MAX_FILES


LASTUPDATE = (b"84135 77ac http://data.gdeltproject.org/gdeltv2/20261014101500.export.CSV.zip\n"
              b"5561147 0238 http://data.gdeltproject.org/gdeltv2/20261014101500.gkg.csv.zip\n")


def fake_get(files: dict, calls: list):
    def _get(url):
        calls.append(url)
        if url.endswith("lastupdate.txt"):
            return 200, LASTUPDATE
        return files.get(url, (404, b""))
    return _get


def test_collect_reads_files_sequentially_and_keeps_the_item_shape(monkeypatch):
    calls = []
    base = "https://data.gdeltproject.org/gdeltv2/"
    files = {
        base + "20261014101500.gkg.csv.zip": (200, gkg_zip(HIRE, NOISE)),
        # The same story in an older file, and a syndicated copy: kept once.
        base + "20261014100000.gkg.csv.zip": (200, gkg_zip(HIRE, CEO, PHRASE)),
    }
    monkeypatch.setattr(gdelt, "_get", fake_get(files, calls))
    monkeypatch.setattr(gdelt.time, "sleep", lambda *_: None)
    items = gdelt.collect(list(registry.GDELT_QUERIES), files=2)

    assert calls[0].endswith("lastupdate.txt") and len(calls) == 3
    assert sorted(i["headline"][:5] for i in items) == ["Acme’", "Aegea", "Tesco"]
    expected = {"raw_text", "headline", "source_url", "source_name",
                "discovery_url", "published_date", "language",
                "source_country", "query", "collector", "fetched_at"}
    for item in items:
        assert expected <= set(item)
        assert item["collector"] == "gdelt"
        assert item["source_url"] == item["discovery_url"]
        assert item["headline"] in item["raw_text"]
    assert gdelt.STATS["files_read"] == 2 and gdelt.STATS["duplicate_url"] == 1


def test_a_missing_file_is_counted_and_the_rest_still_land(monkeypatch):
    base = "https://data.gdeltproject.org/gdeltv2/"
    files = {base + "20261014100000.gkg.csv.zip": (200, gkg_zip(CEO))}
    monkeypatch.setattr(gdelt, "_get", fake_get(files, []))
    monkeypatch.setattr(gdelt.time, "sleep", lambda *_: None)
    items = gdelt.collect(list(registry.GDELT_QUERIES), files=2)
    assert len(items) == 1
    assert gdelt.STATS["files_failed"] == 1


def test_a_429_on_a_file_counts_as_throttled(monkeypatch):
    base = "https://data.gdeltproject.org/gdeltv2/"
    files = {base + "20261014101500.gkg.csv.zip": (429, b"limit requests")}
    monkeypatch.setattr(gdelt, "_get", fake_get(files, []))
    monkeypatch.setattr(gdelt.time, "sleep", lambda *_: None)
    gdelt.collect(list(registry.GDELT_QUERIES), files=1)
    assert gdelt.STATS["throttled_out"] == 1


def test_an_unreachable_index_raises_instead_of_reading_as_a_quiet_day(monkeypatch):
    monkeypatch.setattr(gdelt, "_get", lambda url: (503, b""))
    with pytest.raises(gdelt.RawFilesUnavailable):
        gdelt.collect(list(registry.GDELT_QUERIES), files=1)


# --- the health row says WHY a run was empty -------------------------------

def test_the_health_row_names_gdelt_throttling(monkeypatch):
    monkeypatch.setitem(gdelt.STATS, "throttled_out", 3)
    monkeypatch.setitem(gdelt.STATS, "files_failed", 0)
    assert "GDELT THROTTLED: 3" in run_collect.source_markers("gdelt")


def test_the_health_row_names_unreadable_gdelt_files(monkeypatch):
    monkeypatch.setitem(gdelt.STATS, "throttled_out", 0)
    monkeypatch.setitem(gdelt.STATS, "files_failed", 2)
    assert "GDELT FILES FAILED: 2" in run_collect.source_markers("gdelt")


def test_a_clean_run_and_other_sources_add_no_marker(monkeypatch):
    monkeypatch.setitem(gdelt.STATS, "throttled_out", 0)
    monkeypatch.setitem(gdelt.STATS, "files_failed", 0)
    assert run_collect.source_markers("gdelt") == ""
    monkeypatch.setitem(gdelt.STATS, "throttled_out", 5)
    assert run_collect.source_markers("google_news") == ""
