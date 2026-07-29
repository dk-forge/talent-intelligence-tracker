"""The discovery tripwire, tested offline.

Three things are worth protecting here, and they are the three ways this
instrument could quietly become dangerous or worthless.

1. THE MODEL'S CLAIMS MUST NEVER BECOME DATA. Every field an LLM returns keeps
   a `claimed_` prefix, the chase searches on the employer's NAME and nothing
   else, and no claimed value is ever handed to the store path. If a future edit
   passes a claimed amount or URL downstream, these tests fail.
2. THE BUDGET MUST STAY DERIVED FROM THE CAP. Search-backed queries are the
   expensive kind; a plan that quietly grows past the cap is the failure mode
   the owner would only see on a bill.
3. THE DIFF MUST STAY GENEROUS. Over-reporting misses spends money on things we
   already hold and poisons the per-country miss counts the health machinery
   reads, so the matching rule is pinned exactly as strict as it was.
"""

import json
import os
import subprocess
import sqlite3
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analysis.tripwire import ask, diff, plan as planner, report  # noqa: E402


# --- the budget ------------------------------------------------------------

def test_the_plan_costs_no_more_than_the_cap():
    """The one number the owner cares about. Everything else in plan.py is
    derived from it, so this is the test that catches a well-meaning 'let's ask
    about a few more countries'."""
    projection = planner.monthly_projection()
    assert projection["projected_usd_per_month"] <= planner.TRIPWIRE_MONTHLY_USD


def test_the_query_count_is_derived_from_money_not_the_other_way_round():
    expected = int(planner.TRIPWIRE_MONTHLY_USD / planner.USD_PER_QUERY_ESTIMATE)
    assert planner.QUERIES_PER_MONTH == expected
    assert planner.COUNTRIES_PER_RUN >= 1


def test_the_per_run_guard_covers_the_biggest_legitimate_run():
    """The sweep run is the largest there is. The guard must admit it and
    nothing bigger."""
    assert (planner.MAX_QUERIES_PER_RUN
            == planner.COUNTRIES_PER_RUN + planner.INDUSTRY_SWEEP_QUERIES)


def test_the_lookback_outlives_the_rotation():
    """A country comes round about once a month at this cadence. A window
    shorter than the cycle leaves a gap nothing ever looks at, which is the one
    bug a tripwire may not have."""
    cycle_runs = max(1, len(planner.DEFAULT_COUNTRY_ORDER) / planner.COUNTRIES_PER_RUN)
    days_per_run = 30 / planner.RUNS_PER_MONTH
    assert planner.LOOKBACK_DAYS >= min(45, cycle_runs * days_per_run / 2)


def test_industries_are_the_whole_vocabulary():
    from pipeline.vocab import INDUSTRIES

    assert planner.INDUSTRY_SWEEP_QUERIES == len(INDUSTRIES) == 18


# --- priority by measured recall -------------------------------------------

RECALL = {
    "measured_on": "2026-07-28",
    "goldset": {"version": "test-v1"},
    "summary": {"by_country": {
        "JP": {"total": 4, "held": 0, "held_pct": 0.0},
        "NZ": {"total": 1, "held": 0, "held_pct": 0.0},
        "IL": {"total": 10, "held": 1, "held_pct": 10.0},
        "US": {"total": 34, "held": 30, "held_pct": 88.2},
    }},
}


def test_zero_recall_countries_come_first_and_biggest_first():
    tiers = planner.country_tiers(RECALL)
    assert [t["iso2"] for t in tiers[:2]] == ["JP", "NZ"], (
        "a country that held nothing across four measured events outranks one "
        "with a single event, and both outrank anything we do hold")
    by_code = {t["iso2"]: t["tier"] for t in tiers}
    assert by_code["JP"] == planner.TIER_ZERO
    assert by_code["IL"] == planner.TIER_WEAK
    assert by_code["US"] == planner.TIER_COVERED
    # A country never measured is present and honestly labelled, not silently
    # treated as covered.
    assert by_code["BR"] == planner.TIER_UNMEASURED


def test_most_of_a_run_goes_to_the_countries_we_score_zero_in():
    """Three of four slots, which is what ZERO_TIER_SHARE buys. The fourth
    keeps covered and weak countries in view so a regression somewhere we
    thought was fine still surfaces."""
    many_zeros = {"summary": {"by_country": {
        code: {"total": 3, "held": 0, "held_pct": 0.0}
        for code in ("JP", "NZ", "BR", "SE", "FI")}}}
    many_zeros["summary"]["by_country"]["US"] = {"total": 34, "held": 30,
                                                 "held_pct": 88.2}
    chosen = planner.countries_for_run(planner.country_tiers(many_zeros),
                                       cycle=0, per_run=4)
    zeros = [c for c in chosen if c["tier"] == planner.TIER_ZERO]
    assert len(chosen) == 4
    assert len(zeros) == 3


def test_a_short_zero_list_spills_into_the_other_tiers_rather_than_shrinking():
    """Only two countries scored zero here, so the run still asks four
    questions. Paying for three quarters of a run is not a saving."""
    chosen = planner.countries_for_run(planner.country_tiers(RECALL),
                                       cycle=0, per_run=4)
    assert len(chosen) == 4
    assert len([c for c in chosen if c["tier"] == planner.TIER_ZERO]) == 2


def test_a_run_never_asks_about_the_same_country_twice():
    tiers = planner.country_tiers(RECALL)
    for cycle in range(12):
        chosen = planner.countries_for_run(tiers, cycle=cycle, per_run=4)
        codes = [c["iso2"] for c in chosen]
        assert len(codes) == len(set(codes)), f"cycle {cycle} repeats a country"


def test_the_rotation_moves_and_is_reproducible():
    tiers = planner.country_tiers(RECALL)
    first = [c["iso2"] for c in planner.countries_for_run(tiers, cycle=0, per_run=4)]
    again = [c["iso2"] for c in planner.countries_for_run(tiers, cycle=0, per_run=4)]
    later = [c["iso2"] for c in planner.countries_for_run(tiers, cycle=3, per_run=4)]
    assert first == again, "the same cycle must plan the same run"
    assert first != later, "consecutive runs must walk the pool, not re-ask it"


def test_with_no_recall_on_file_the_plan_says_it_is_guessing():
    run_plan = planner.build_plan(cycle=0, recall=None, sweep_industries=False)
    assert "DEFAULT ORDER" in run_plan["basis"]
    assert run_plan["countries"], "a missing measurement must not mean no plan"


def test_the_industry_sweep_runs_once_a_month(tmp_path):
    today = date(2026, 7, 15)
    assert planner.industries_due(str(tmp_path), today) is True

    (tmp_path / "tripwire-2026-07-02.json").write_text(
        json.dumps({"plan": {"industries": ["technology"]}}))
    assert planner.industries_due(str(tmp_path), today) is False
    assert planner.industries_due(str(tmp_path), date(2026, 8, 1)) is True


def test_a_country_only_run_does_not_sweep_industries(tmp_path):
    run_plan = planner.build_plan(cycle=0, recall=RECALL, sweep_industries=False,
                                  results_dir=str(tmp_path))
    assert run_plan["industries"] == []
    assert run_plan["query_count"] == len(run_plan["countries"])
    assert run_plan["query_count"] <= planner.MAX_QUERIES_PER_RUN


# --- the ask ---------------------------------------------------------------

def test_every_model_asserted_field_is_marked_as_a_claim():
    """The structural guarantee. If a field arrives from the model without a
    `claimed_` prefix, something downstream can mistake it for ours."""
    query = {"dimension": "country", "key": "IL", "label": "Israel", "tier": "zero"}
    leads = ask.parse_leads(json.dumps({"items": [
        {"company": "Plantopia", "country": "Israel", "amount": "$9M",
         "event_date": "2026-07-08", "signal_type": "funding",
         "outlet": "CTech", "url": "https://example.com/a"}]}), query)

    assert len(leads) == 1
    model_fields = {"company", "country", "city", "signal_type", "event_date",
                    "amount", "stage", "outlet", "url", "industry"}
    for key in leads[0]:
        assert not (key in model_fields), (
            f"{key} holds a model assertion under a bare name")
    assert leads[0]["claimed_amount"] == "$9M"
    assert leads[0]["claimed_country"] == "IL"


def test_a_lead_with_no_employer_is_dropped_at_the_parser():
    query = {"dimension": "country", "key": "SG", "label": "Singapore", "tier": "zero"}
    leads = ask.parse_leads(json.dumps({"items": [
        {"company": "", "country": "Singapore"},
        {"company": "X", "country": "Singapore"},
        "not even a dict",
    ]}), query)
    assert leads == []


def test_unparseable_output_is_zero_leads_not_an_exception():
    query = {"dimension": "industry", "key": "technology", "label": "technology",
             "tier": "sweep"}
    assert ask.parse_leads("I'm sorry, I can't help with that.", query) == []


def test_a_country_query_keeps_its_own_country_when_the_model_omits_one():
    query = {"dimension": "country", "key": "JP", "label": "Japan", "tier": "zero"}
    leads = ask.parse_leads(json.dumps({"items": [{"company": "Sakana"}]}), query)
    assert leads[0]["claimed_country"] == "JP"


def test_the_prompt_refuses_layoffs_like_every_other_prompt_here():
    """Layoffs belong to the sibling product. A discovery instrument that asks
    for them would pull this tracker straight across the line it promises not
    to cross."""
    prompt = ask.country_query("IL", "Israel", lookback_days=45, leads=8,
                               today=date(2026, 7, 28))
    assert "2026-06-13" in prompt, "the window must be stated as dates"
    assert "layoffs" in ask.SCHEMA.lower() and "no workforce reductions" in ask.SCHEMA.lower()


def test_the_prompt_demands_a_publisher():
    assert "publisher" in ask.SYSTEM.lower()
    assert "never fill the list" in ask.SCHEMA.lower()


def test_build_queries_covers_every_dimension_value():
    run_plan = planner.build_plan(cycle=0, recall=RECALL, sweep_industries=True)
    run_plan["leads_per_query"] = 8
    queries = ask.build_queries(run_plan)
    assert len(queries) == run_plan["query_count"]
    assert {q["dimension"] for q in queries} == {"country", "industry"}


# --- the diff --------------------------------------------------------------

INDEX = [
    {"signal_id": "s1", "company": "IceCure Medical Ltd.",
     "company_key": "icecure medical", "pillar": "company_development",
     "country": "IL", "published_date": "2026-06-25",
     "source_url": "https://example.com/icecure"},
    {"signal_id": "s2", "company": "Glowforge", "company_key": "glowforge",
     "pillar": "company_development", "country": "US",
     "published_date": "2026-07-01", "source_url": "https://example.com/glowforge"},
]


def _lead(company, **kwargs):
    base = {"claimed_company": company, "claimed_country": "IL",
            "claimed_event_date": "2026-07-10", "claimed_signal_type": "funding",
            "dimension": "country", "dimension_key": "IL"}
    base.update(kwargs)
    return base


def test_a_name_variant_still_counts_as_held():
    """'IceCure Medical' against a stored 'IceCure Medical Ltd.' is the same
    employer. Calling it missing would send the chase to re-find a record we
    already have."""
    assert diff.verdict(_lead("IceCure Medical"), INDEX)["verdict"] == diff.HELD


def test_a_different_employer_is_missing_even_when_the_name_overlaps():
    """The word-boundary rule comes from analysis.recall.match, so discovery
    and measurement cannot drift apart. 'Glow' is not 'Glowforge'."""
    assert diff.verdict(_lead("Glow"), INDEX)["verdict"] == diff.MISSING


def test_a_date_the_model_got_badly_wrong_still_matches():
    """The claimed date is the field an LLM is least reliable about, so the
    window is wide on purpose."""
    lead = _lead("IceCure Medical", claimed_event_date="2026-08-01")
    assert diff.verdict(lead, INDEX)["verdict"] == diff.HELD


def test_a_genuinely_different_event_a_year_later_is_missing():
    lead = _lead("IceCure Medical", claimed_event_date="2027-06-25")
    assert diff.verdict(lead, INDEX)["verdict"] == diff.MISSING


def test_a_lead_with_no_date_is_held_rather_than_chased():
    lead = _lead("IceCure Medical", claimed_event_date="")
    assert diff.verdict(lead, INDEX)["verdict"] == diff.HELD


def test_a_description_is_not_an_employer():
    for name in ("an undisclosed fintech", "a major bank", "several startups"):
        assert diff.verdict(_lead(name), INDEX)["verdict"] == diff.UNUSABLE


def test_the_same_employer_is_only_chased_once_per_run():
    leads = [_lead("Plantopia"), _lead("Plantopia Ltd"), _lead("Harmony Bio")]
    deduped, dropped = diff.dedupe(leads)
    assert dropped == 1
    assert [d["claimed_company"] for d in deduped] == ["Plantopia", "Harmony Bio"]


def test_load_index_reads_only_current_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE signals (signal_id TEXT, company TEXT,
                    company_key TEXT, pillar TEXT, country TEXT, hq_country TEXT,
                    published_date TEXT, effective_date TEXT, captured_at TEXT,
                    source_url TEXT, source_name TEXT, headline TEXT,
                    is_current INT)""")
    conn.execute("INSERT INTO signals VALUES ('a','A','a','p','IL',NULL,NULL,"
                 "NULL,NULL,'u','n','h',1)")
    conn.execute("INSERT INTO signals VALUES ('b','B','b','p','IL',NULL,NULL,"
                 "NULL,NULL,'u','n','h',0)")
    assert [row["signal_id"] for row in diff.load_index(conn)] == ["a"]


# --- the report ------------------------------------------------------------

def _result_of(verdicts):
    return [dict(_lead(f"Co{i}"), verdict=v, claimed_country="IL",
                 claimed_industry="", matched=None, why="")
            for i, v in enumerate(verdicts)]


def test_cost_per_lead_never_appears_without_a_denominator():
    counts = report.tally(_result_of([diff.MISSING, diff.HELD]))
    block = report.cost_block(0.06, 3, counts, results_dir="/nonexistent", conn=None)
    assert block["usd_per_query"] == 0.02
    assert block["usd_per_lead"] == 0.03
    assert block["usd_per_candidate_miss"] == 0.06
    # Nothing confirmed yet, so the figure is absent rather than invented.
    assert block["usd_per_confirmed_miss"] is None
    assert block["confirmed_misses_lifetime"] is None


def test_a_run_that_found_nothing_reports_no_rate_at_all():
    counts = report.tally([])
    block = report.cost_block(0.06, 3, counts, results_dir="/nonexistent", conn=None)
    assert block["usd_per_lead"] is None
    assert block["usd_per_candidate_miss"] is None


def test_a_confirmed_miss_is_a_stored_row_not_a_lead():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE signals (collector TEXT, is_current INT)")
    conn.execute("INSERT INTO signals VALUES (?, 1)", (report.CHASE_COLLECTOR,))
    conn.execute("INSERT INTO signals VALUES ('google_news', 1)")
    assert report.confirmed_misses(conn) == 1


def test_the_work_list_carries_only_misses_and_says_they_are_claims():
    results = _result_of([diff.MISSING, diff.HELD, diff.UNUSABLE])
    counts = report.tally(results)
    block = report.cost_block(0.04, 2, counts, results_dir="/nonexistent", conn=None)
    result = report.build_result({"basis": "test"}, [], results, block, [])
    worklist = report.build_worklist(result)

    assert worklist["missing_total"] == 1
    assert all(lead["verdict"] == diff.MISSING for lead in worklist["leads"])
    assert "LEAD, never a record" in worklist["instruction"]
    assert worklist["country_misses"] == {"IL": 1}


def test_misses_are_counted_by_the_country_the_lead_is_about():
    """An industry sweep that keeps surfacing missing Japanese companies is
    saying something about Japan's feeds, and grouping it under the industry
    would hide exactly that."""
    results = [dict(_lead("Sakana"), verdict=diff.MISSING, claimed_country="JP",
                    claimed_industry="technology", dimension="industry",
                    dimension_key="technology", matched=None, why="")]
    assert report.by_country(results)["JP"]["missing"] == 1
    assert report.by_industry(results)["technology"]["missing"] == 1


def test_a_run_that_found_nothing_files_itself_as_degraded(tmp_path):
    """The house rule, applied to the instrument itself: a source returning
    zero is degraded, never ok. An outside view that answers nothing is broken
    or throttled, and both look exactly like a quiet week."""
    import run_tripwire
    from pipeline import schema

    conn = schema.connect(tmp_path / "t.db")
    counts = report.tally([])
    block = report.cost_block(0.04, 2, counts, results_dir=str(tmp_path), conn=conn)
    result = report.build_result({"basis": "test"}, [], [], block, [])
    run_tripwire.report_health(conn, result, report.build_worklist(result))

    row = conn.execute("SELECT status, detail FROM source_health "
                       "WHERE collector = 'tripwire'").fetchone()
    assert row["status"] == "degraded"
    assert "0 leads" in row["detail"]


# --- the chase -------------------------------------------------------------

from collectors import tripwire_chase  # noqa: E402


def test_the_chase_query_is_built_from_the_name_and_nothing_else():
    """The safety property, stated as a test: no claimed amount, date, outlet or
    URL may reach the search, because none of them may reach the store."""
    lead = {"claimed_company": "Plantopia", "claimed_amount": "$9M",
            "claimed_event_date": "2026-07-08", "claimed_outlet": "CTech",
            "claimed_url": "https://example.com/invented"}
    query = tripwire_chase.query_for(lead)
    assert "Plantopia" in query
    for claim in ("$9M", "2026-07-08", "CTech", "example.com"):
        assert claim not in query


def test_the_chase_drops_articles_that_do_not_name_the_employer():
    item = {"headline": "Some other company raises $5M", "raw_text": "..."}
    assert tripwire_chase._mentions("Plantopia", item) is False
    hit = {"headline": "Plantopia raises $9 million", "raw_text": "..."}
    assert tripwire_chase._mentions("Plantopia", hit) is True


def test_the_chase_stores_no_claimed_value_on_the_candidate():
    """What leaves this collector is an ARTICLE. If a claimed_* field rides
    along on the item, a future edit could put a model's number in the store."""
    lead = {"claimed_company": "Plantopia", "claimed_country": "IL",
            "claimed_amount": "$9M", "claimed_url": "https://example.com/x",
            "dimension": "country", "dimension_key": "IL"}

    def fake_fetch(query, *, lang, country):
        return [{"raw_text": "Plantopia raises $9 million", "headline":
                 "Plantopia raises $9 million", "discovery_url": "https://news.google.com/x",
                 "source_url": "https://ctech.example/article", "source_name": "CTech",
                 "published_date": "Wed, 22 Jul 2026 07:00:00 GMT", "query": ""}]

    items = tripwire_chase.collect(leads=[lead], pause=0, fetch=fake_fetch,
                                   resolve=lambda item, session=None: item)
    assert len(items) == 1
    blob = json.dumps(items[0])
    assert "claimed_" not in blob and "$9M" not in blob
    assert items[0]["collector"] == "tripwire_chase"
    assert items[0]["query"] == "Plantopia", "bucketed by employer for fair_share"
    assert items[0]["raw_text"], "a collector that forgets raw_text posts zero"


def test_the_chase_caps_articles_per_lead():
    def fake_fetch(query, *, lang, country):
        return [{"raw_text": f"Plantopia news {i}", "headline": f"Plantopia news {i}",
                 "discovery_url": f"https://news.google.com/{i}",
                 "source_url": f"https://ctech.example/{i}", "source_name": "CTech",
                 "published_date": ""} for i in range(9)]

    items = tripwire_chase.collect(
        leads=[{"claimed_company": "Plantopia", "claimed_country": "IL"}],
        pause=0, fetch=fake_fetch, resolve=lambda item, session=None: item)
    assert len(items) == tripwire_chase.MAX_ITEMS_PER_LEAD


def test_an_empty_work_list_chases_nothing(tmp_path):
    missing = str(tmp_path / "nothing.json")
    assert tripwire_chase.collect(worklist_path=missing) == []


def test_the_chase_asks_each_market_in_its_own_edition():
    assert tripwire_chase._edition("JP") == ("ja", "JP")
    assert tripwire_chase._edition("ZZ")[1] == "US", "unknown markets fall back"


def test_the_chase_is_registered_but_nothing_schedules_it():
    import run_collect

    assert run_collect.SOURCES["tripwire_chase"] is tripwire_chase
    workflows = os.path.join(ROOT, ".github", "workflows")
    for name in os.listdir(workflows):
        text = open(os.path.join(workflows, name), encoding="utf-8").read()
        if "tripwire_chase" not in text:
            continue
        pytest.fail(f"{name} references the chase; it is meant to be manual")


# --- shipped dormant, and end to end ---------------------------------------

def test_the_tripwire_workflow_ships_dormant():
    yaml = pytest.importorskip("yaml")
    path = os.path.join(ROOT, ".github", "workflows", "tripwire.yml")
    text = open(path, encoding="utf-8").read()
    parsed = yaml.safe_load(text)

    triggers = parsed.get("on") or parsed.get(True)
    assert "workflow_dispatch" in triggers
    assert "schedule" not in triggers, (
        "a new source ships dormant and is armed only after a human reads a "
        "real run")
    assert any("cron:" in line for line in text.splitlines()), (
        "the intended cadence must be written down, commented out")
    # It writes the database (health) so it must hold the one lock.
    assert parsed["concurrency"]["group"] == "talent-collect"
    assert parsed["concurrency"]["cancel-in-progress"] is False


def test_the_dispatch_default_is_a_dry_run():
    yaml = pytest.importorskip("yaml")
    path = os.path.join(ROOT, ".github", "workflows", "tripwire.yml")
    parsed = yaml.safe_load(open(path, encoding="utf-8").read())
    inputs = (parsed.get("on") or parsed.get(True))["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["default"] is True


def _results_listing():
    """What the results directory holds, treating "not there yet" as empty.

    git does not track an empty directory, so a fresh checkout — which is every
    CI run — has no analysis/tripwire/results until a real run creates one.
    Listing it directly raised FileNotFoundError and failed the build for a
    reason unrelated to what this test protects, which is that an offline run
    writes NOTHING. A directory that does not exist holds nothing, so the
    assertion is the same either way.
    """
    try:
        return set(os.listdir(os.path.join(ROOT, "analysis", "tripwire", "results")))
    except FileNotFoundError:
        return set()


def test_an_offline_run_spends_nothing_and_writes_nothing(tmp_path):
    """The dry-run diagnostic the project requires before anything is armed.
    Proves the whole path — plan, ask, parse, diff, cost, work list — with no
    network call and no key."""
    before = _results_listing()
    proc = subprocess.run(
        [sys.executable, "run_tripwire.py", "--offline", "--no-industries",
         "--countries", "IL,JP"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
        env={**os.environ, "OPENROUTER_API_KEY": ""},
    )
    assert proc.returncode == 0, proc.stderr
    assert "OFFLINE replay" in proc.stdout
    assert "MISSING" in proc.stdout
    assert "nothing written to the repository" in proc.stdout
    after = _results_listing()
    assert before == after
    assert not os.path.exists(os.path.join(ROOT, "data", "tripwire_worklist.json")) \
        or "tripwire_worklist" not in proc.stdout.split("nothing written")[1]


def test_the_plan_is_printable_without_a_key_or_a_network():
    proc = subprocess.run(
        [sys.executable, "run_tripwire.py", "--plan-only"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
        env={**os.environ, "OPENROUTER_API_KEY": ""},
    )
    assert proc.returncode == 0, proc.stderr
    assert "TRIPWIRE BUDGET" in proc.stdout
    assert "$1.00/month" in proc.stdout
