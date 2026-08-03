"""The benchmark-diff loop: dormant by default, private by construction.

Ported from the sibling layoff tracker's tracker-diff. Three properties carry
the whole design and each is pinned here:

1. DORMANT: with neither BENCHMARK_* secret set, run_benchmark_diff.main()
   prints exactly one line and returns 0. The repo carries zero benchmark
   data; the owner arming a secret is the only activation.
2. PRIVATE: no company name and no feed URL from the reference list may reach
   stdout, the health detail, or any committed file. Logs carry counts and
   slice indices only. Names travel to exactly one place, the owner's inbox,
   via the keyed /alert route, and only when recall drops below threshold.
3. POINTER, NOT SOURCE: what gets chased is the employer's OWN article or
   filing, through the ordinary classify -> validate -> store path. The
   reference list contributes a NAME and nothing else.
"""

import io
import json
import os
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import run_benchmark_diff  # noqa: E402
from collectors import benchmark_chase  # noqa: E402


SECRETS = ("BENCHMARK_FEED_URLS", "BENCHMARK_COMPANIES")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in SECRETS:
        monkeypatch.delenv(name, raising=False)
    benchmark_chase.prepare(None)
    yield
    benchmark_chase.prepare(None)


# --- dormancy ---------------------------------------------------------------

def test_dormant_run_is_one_line_and_exit_zero(capsys):
    """The property the whole port ships on: no secret, no work, no noise."""
    assert not benchmark_chase.armed()
    code = run_benchmark_diff.main([])
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 0
    assert len(out) == 1, f"a dormant run must log exactly one line, got {out}"
    assert "dormant" in out[0]
    assert "BENCHMARK_FEED_URLS" in out[0] and "BENCHMARK_COMPANIES" in out[0]


def test_dormant_collect_fetches_nothing(capsys):
    """The collector itself is safe to invoke bare: dormant means empty."""
    items = benchmark_chase.collect()
    assert items == []
    assert benchmark_chase.LAST_RUN.get("read") == 0
    assert "dormant" in capsys.readouterr().out


def test_either_secret_arms_it(monkeypatch):
    monkeypatch.setenv("BENCHMARK_COMPANIES", "Acme")
    assert benchmark_chase.armed()
    monkeypatch.delenv("BENCHMARK_COMPANIES")
    monkeypatch.setenv("BENCHMARK_FEED_URLS", "https://example.com/feed.json")
    assert benchmark_chase.armed()


# --- the list ---------------------------------------------------------------

def test_inline_names_split_on_commas_and_newlines(monkeypatch):
    monkeypatch.setenv("BENCHMARK_COMPANIES", "Acme Corp, Bravo Ltd\nCharlie AI\n")
    assert benchmark_chase.benchmark_names() == [
        "Acme Corp", "Bravo Ltd", "Charlie AI"]


def test_feed_json_and_csv_both_parse():
    js = json.dumps([{"company": "Acme Corp"}, {"name": "Bravo Ltd"},
                     {"other": "ignored"}])
    assert benchmark_chase._names_from_body(js) == ["Acme Corp", "Bravo Ltd"]
    wrapped = json.dumps({"data": [{"company_name": "Charlie AI"}]})
    assert benchmark_chase._names_from_body(wrapped) == ["Charlie AI"]
    csv_body = "company,jobs\nAcme Corp,10\nBravo Ltd,5\n"
    assert benchmark_chase._names_from_body(csv_body) == ["Acme Corp", "Bravo Ltd"]


def test_feeds_and_inline_deduplicate_case_insensitively(monkeypatch, capsys):
    class FakeResp:
        status_code = 200
        text = json.dumps([{"company": "ACME CORP"}, {"company": "Bravo Ltd"}])

    class FakeSession:
        def get(self, url, **kwargs):
            return FakeResp()

    monkeypatch.setenv("BENCHMARK_FEED_URLS", "https://example.com/private.json")
    monkeypatch.setenv("BENCHMARK_COMPANIES", "acme corp, Delta Inc")
    names = benchmark_chase.benchmark_names(session=FakeSession())
    assert names == ["ACME CORP", "Bravo Ltd", "Delta Inc"]
    # The feed is referred to by index only. Its URL is a secret and a URL
    # substring can slip past GitHub's masking into a public log.
    out = capsys.readouterr().out
    assert "example.com" not in out and "private" not in out
    assert "feed #1" in out


# --- the diff ---------------------------------------------------------------

def test_the_diff_normalises_through_company_key():
    """'Acme, Inc.' on their list must match our 'Acme Inc' row: the diff uses
    the SAME normaliser the store keys employers with."""
    from pipeline.vocab import company_key

    keys = {company_key("Acme, Inc."), company_key("Bravo Holdings Ltd")}
    missing = benchmark_chase.missing_names(
        ["Acme Inc", "Bravo Holdings", "Charlie AI"], keys)
    assert missing == ["Charlie AI"]


def test_the_rotating_slice_walks_the_whole_list():
    missing = [f"Company {i}" for i in range(10)]
    seen: set[str] = set()
    for ordinal in range(730000, 730005):
        sliced, idx, n = benchmark_chase.todays_slice(
            missing, 4, date.fromordinal(ordinal))
        assert n == 3 and 1 <= idx <= n and len(sliced) <= 4
        seen.update(sliced)
    assert seen == set(missing), "five consecutive days must cover 3 slices"


def test_an_absent_database_refuses_rather_than_chases_everything(tmp_path):
    with pytest.raises(RuntimeError):
        benchmark_chase.our_company_keys(str(tmp_path / "missing.db"))


# --- the chase --------------------------------------------------------------

def _press_fetch(query, *, lang, country):
    return [{"raw_text": "Plantopia raises $9 million",
             "headline": "Plantopia raises $9 million",
             "discovery_url": "https://news.google.com/x",
             "source_url": "https://outlet.example/article",
             "source_name": "Outlet", "published_date": "", "query": ""}]


def test_the_query_is_built_from_the_name_and_nothing_else():
    q = benchmark_chase.query_for("Plantopia")
    assert q.startswith('"Plantopia"')
    assert "when:" in q
    # An embedded quote is stripped, never escaped into the query syntax.
    assert benchmark_chase.query_for('Plant"opia').startswith('"Plantopia"')


def test_the_chase_drops_articles_that_do_not_name_the_employer():
    miss = {"headline": "Some other company raises $5M", "raw_text": "..."}
    assert benchmark_chase._mentions("Plantopia", miss) is False
    hit = {"headline": "Plantopia raises $9 million", "raw_text": "..."}
    assert benchmark_chase._mentions("Plantopia", hit) is True


def test_the_chase_tags_its_own_collector_and_buckets_by_employer():
    items = benchmark_chase.collect(
        leads=[{"company": "Plantopia"}], pause=0, fetch=_press_fetch,
        resolve=lambda item, session=None: item,
        sec_search=lambda *a, **k: [])
    assert len(items) == 1
    assert items[0]["collector"] == "benchmark_chase"
    assert items[0]["query"] == "Plantopia", "bucketed by employer for fair_share"
    assert items[0]["raw_text"], "a collector that forgets raw_text posts zero"
    assert benchmark_chase.LAST_RUN["read"] == 1


def test_the_chase_caps_articles_per_lead():
    def many(query, *, lang, country):
        return [{"raw_text": f"Plantopia news {i}",
                 "headline": f"Plantopia news {i}",
                 "discovery_url": f"https://news.google.com/{i}",
                 "source_url": f"https://outlet.example/{i}",
                 "source_name": "Outlet", "published_date": ""}
                for i in range(9)]

    items = benchmark_chase.collect(
        leads=[{"company": "Plantopia"}], pause=0, fetch=many,
        resolve=lambda item, session=None: item,
        sec_search=lambda *a, **k: [])
    assert len(items) == benchmark_chase.MAX_ITEMS_PER_LEAD


def test_a_filing_counts_only_when_the_employer_filed_it():
    """SEC full-text search returns filings that merely MENTION a name; a
    mention is not evidence. Only the employer's own filing survives."""
    hits = [
        {"_id": "0001-23-000001:doc.htm",
         "_source": {"display_names": ["Plantopia Inc  (PLNT)  (CIK 0001234567)"],
                     "file_date": "2026-07-01"}},
        {"_id": "0001-23-000002:doc.htm",
         "_source": {"display_names": ["Unrelated Corp  (CIK 0007654321)"],
                     "file_date": "2026-07-02"}},
    ]
    items = benchmark_chase.collect(
        leads=[{"company": "Plantopia"}], pause=0,
        fetch=lambda *a, **k: [],
        resolve=lambda item, session=None: item,
        sec_search=lambda *a, **k: hits,
        sec_fetch_text=lambda url: "Item 5.02 officer change at Plantopia Inc")
    assert len(items) == 1
    assert items[0]["source_name"] == "SEC EDGAR"
    assert "Plantopia" in items[0]["headline"]
    assert items[0]["cik"] == "1234567"


# --- privacy: the property the sibling test-pins ----------------------------

def test_the_chase_log_carries_counts_and_never_a_name(capsys):
    """The reference list is a secret. A name in an Actions log is a name in a
    public place, so the collector's own narration is indices and counts."""
    benchmark_chase.collect(
        leads=[{"company": "Plantopia"}, {"company": "Verdantis"}],
        pause=0, fetch=_press_fetch,
        resolve=lambda item, session=None: item,
        sec_search=lambda *a, **k: [])
    out = capsys.readouterr().out
    assert "Plantopia" not in out and "Verdantis" not in out
    assert "lead 1/2" in out and "lead 2/2" in out


def test_the_script_redacts_run_collects_narration():
    """run_collect narrates candidates by headline, which is right for every
    other collector and a leak for this one, so the chase re-emits only the
    count-shaped lines. Simulated with the real redaction gate."""
    narration = "\n".join([
        "[benchmark_chase] 2 fetched, 0 filtered out, 2 going to the classifier",
        "  REJECT  Plantopia raises $9 million",
        "          model judged it not a talent signal",
        "  STORE   Verdantis - Verdantis appoints chief executive",
        "[benchmark_chase] found=2 would store=1 duplicate=0 rejected=1 "
        "deferred=0 budget-deferred=0 already-seen=0",
        "DRY RUN - nothing was written.",
    ])
    kept = [line for line in narration.splitlines()
            if run_benchmark_diff._SAFE_LINE.match(line)]
    blob = "\n".join(kept)
    assert "Plantopia" not in blob and "Verdantis" not in blob
    assert "found=2" in blob and "DRY RUN" in blob


def test_an_armed_dry_run_prints_no_name_end_to_end(monkeypatch, tmp_path):
    """The whole script, armed with fake names against a tiny real database:
    stdout carries recall, slice indices and counts, and not one list member."""
    import sqlite3

    db = tmp_path / "talent.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE signals (company_key TEXT, is_current INTEGER)")
        conn.execute("INSERT INTO signals VALUES ('held employer', 1)")

    monkeypatch.setenv("BENCHMARK_COMPANIES",
                       "Held Employer Inc, Plantopia, Verdantis")
    monkeypatch.setattr(benchmark_chase, "DB_PATH", str(db))

    chased: list[dict] = []

    def fake_chase(leads, *, dry_run, limit):
        chased.extend(leads)
        print("[benchmark_chase] found=0 would store=0 duplicate=0 rejected=0 "
              "deferred=0 budget-deferred=0 already-seen=0")
        return 0

    monkeypatch.setattr(run_benchmark_diff, "_run_chase", fake_chase)

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = run_benchmark_diff.main(["--dry-run"])
    out = buf.getvalue()

    assert code == 0
    assert {l["company"] for l in chased} == {"Plantopia", "Verdantis"}
    for name in ("Plantopia", "Verdantis", "Held Employer"):
        assert name not in out, f"list member {name!r} leaked into the log"
    assert "RECALL 1/3" in out and "33.3%" in out
    assert "slice 1/1" in out


def test_the_recall_alert_carries_names_to_the_inbox_only(monkeypatch):
    """Below threshold, the names go through the keyed /alert route and the
    log records only the percentage and the count."""
    posted = {}

    class Resp:
        status_code = 200

    def fake_post(url, *, json=None, headers=None, timeout=0):
        posted["url"] = url
        posted["json"] = json
        posted["headers"] = headers
        return Resp()

    monkeypatch.setenv("WP_SITE_URL", "https://example.com/blog")
    monkeypatch.setenv("WP_API_KEY", "k")
    sent = run_benchmark_diff.email_recall_gap(
        ["Plantopia", "Verdantis"], 60.0, 5, post=fake_post)
    assert sent
    assert posted["url"].endswith("/wp-json/talent/v1/alert")
    assert "X-Talent-API-Key" in posted["headers"]
    assert "Plantopia" in posted["json"]["body"]
    assert "60.0%" in posted["json"]["subject"]


def test_a_healthy_recall_sends_no_alert(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("no alert may fire at or above the threshold")

    monkeypatch.setenv("WP_SITE_URL", "https://example.com/blog")
    monkeypatch.setenv("WP_API_KEY", "k")
    assert run_benchmark_diff.email_recall_gap(
        ["Plantopia"], run_benchmark_diff.RECALL_ALERT_PCT, 100,
        post=explode) is False
    assert run_benchmark_diff.email_recall_gap([], 10.0, 0, post=explode) is False


def test_a_failed_alert_never_raises(monkeypatch):
    monkeypatch.setenv("WP_SITE_URL", "https://example.com/blog")
    monkeypatch.setenv("WP_API_KEY", "k")

    def boom(*a, **k):
        raise OSError("host down")

    assert run_benchmark_diff.email_recall_gap(
        ["Plantopia"], 10.0, 10, post=boom) is False


def test_the_chase_switches_the_gate_label_ledger_off():
    """Labels are committed to the repo and carry headlines; a headline of a
    chased-but-unverified employer is list membership in a public place."""
    import inspect

    src = inspect.getsource(run_benchmark_diff._run_chase)
    assert 'os.environ["TIT_GATE_LEDGER"] = "off"' in src


# --- wiring -----------------------------------------------------------------

def test_the_chase_is_registered_and_only_the_diff_workflow_knows_it():
    """Registered in run_collect so it shares every guard; referenced by no
    workflow except through run_benchmark_diff.py, so nothing can dispatch the
    collector around the dormancy check and the redaction."""
    import run_collect

    assert run_collect.SOURCES["benchmark_chase"] is benchmark_chase
    workflows = ROOT / ".github" / "workflows"
    for path in workflows.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "benchmark_chase" not in text, (
            f"{path.name} names the collector directly; the only entry point "
            "is run_benchmark_diff.py")


def test_the_workflow_ships_dispatch_only_and_degrades_spend():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "benchmark-diff.yml").read_text())
    triggers = doc.get("on") or doc.get(True)
    assert "schedule" not in triggers, (
        "a lock-group writer must not carry its own cron; the weekly slot "
        "lives in schedule-link-hygiene.yml")
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["default"] is True, (
        "a bare dispatch must rehearse, not write")
    assert doc["concurrency"]["group"] == "talent-collect"
    text = (ROOT / ".github" / "workflows" / "benchmark-diff.yml").read_text()
    assert "spend.py --degrade" in text, (
        "the chase spends through the gate, so the ceiling check runs first, "
        "and it degrades rather than halts")
    assert "TIT_READTHROUGH_CAP" in text, "the run's paid reads are unbounded"
    assert "BENCHMARK_FEED_URLS" in text and "BENCHMARK_COMPANIES" in text


def test_the_weekly_slot_is_mapped_and_ticketed():
    """The scheduler's case statement is what decides; a cron without a
    mapping queues nothing forever (that failure mode is designed against in
    the scheduler itself, this pins the pair from this side)."""
    text = (ROOT / ".github" / "workflows" /
            "schedule-link-hygiene.yml").read_text()
    assert "'50 7 * * 2')   WANT='benchmark-diff.yml' ;;" in text
    assert "- cron: '50 7 * * 2'" in text
    assert "benchmark)  WANT='benchmark-diff.yml' ;;" in text


def test_no_benchmark_data_is_committed():
    """The repo must carry zero benchmark data: no worklist file, no cached
    list, nothing under data/. The diff lives in memory for the length of one
    run, which is the whole point of secrets-only supply."""
    src = (ROOT / "collectors" / "benchmark_chase.py").read_text()
    assert "json.dump(" not in src and "open(" not in src.replace(
        "sqlite3.connect", ""), (
        "the chase must not write the list, or any derivative of it, to disk")
