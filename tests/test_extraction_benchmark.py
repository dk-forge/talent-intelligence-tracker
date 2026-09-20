"""The body-based extraction benchmark: the frame, the draw, the grading rule.

Offline. Every network door is stubbed at the argument (`fetch=`, `wayback=`,
`call=`), never at the module, so nothing here can reach a publisher, Wayback
or OpenRouter, and no test leaves a fake in `sys.modules` for the suite that
follows it.

The properties worth holding, each of which is a way the measurement could turn
into a flattering number:

* the frame is the extraction path and nothing else, and a NEW collector reds
  this file rather than quietly leaving the population;
* every cell is reachable and the draw reproduces exactly;
* the field definitions come from production's own prompt, so the referees
  grade against the rules the extractor was given;
* UNKNOWN never reaches a denominator, in either direction;
* a parse failure is counted apart from a wrong answer;
* an unreadable body costs nothing;
* the gate is read before every paid request and the cost is metered after.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import measure_extraction as bench  # noqa: E402
from analysis.extraction import frame  # noqa: E402


# --- the frame -------------------------------------------------------------

def test_every_collector_in_the_corpus_is_classified_one_way_or_the_other():
    """A new source must not be able to leave the frame without a decision.

    The frame is "rows an LLM extracted". A collector that is in neither tuple
    is one nobody has judged, and the failure mode is silent: it simply never
    appears in the sample, and the benchmark goes on reporting a number for a
    population that no longer matches the product.
    """
    db = REPO / "data" / "talent_intel.db"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        collectors = {row[0] for row in conn.execute(
            "SELECT DISTINCT collector FROM signals") if row[0]}
    finally:
        conn.close()
    known = set(frame.NEWS_COLLECTORS) | set(frame.STRUCTURED_COLLECTORS)
    assert not collectors - known, (
        "these collectors are in neither frame.NEWS_COLLECTORS nor "
        f"frame.STRUCTURED_COLLECTORS: {sorted(collectors - known)}")


def test_the_two_collector_tuples_do_not_overlap():
    assert not set(frame.NEWS_COLLECTORS) & set(frame.STRUCTURED_COLLECTORS)


def test_eligible_needs_the_extraction_path_a_url_and_a_hash():
    good = {"collector": "google_news", "source_url": "https://x.example/a",
            "content_hash": "abc"}
    assert frame.eligible(good)
    assert not frame.eligible({**good, "collector": "uk_paygap"})
    assert not frame.eligible({**good, "source_url": ""})
    assert not frame.eligible({**good, "content_hash": ""})


def test_event_type_partitions_the_production_pillars():
    seen = set()
    for pillar in ("company_development", "leadership_change", "rewards_comp", "how_we_work"):
        seen.add(frame.event_type_of({"pillar": pillar}))
    seen.add(frame.event_type_of({"pillar": "company_development", "funding_amount_usd": 4}))
    assert seen == set(frame.EVENT_TYPES)


def test_a_generic_tld_is_not_read_as_american():
    """The assumption that turns a worldwide corpus into a US measurement."""
    assert frame.host_country("https://news.example.com/a") is None
    assert frame.region_of({"country": None, "source_url": "https://x.example.com/a"}) == "RoW"
    assert frame.region_of({"country": None, "source_url": "https://x.example.co.uk/a"}) == "Europe"
    assert frame.region_of({"country": "US", "source_url": "https://x.example.fr/a"}) == "US"


def test_the_language_proxy_sees_both_script_and_publisher():
    assert frame.non_latin_headline({"headline": "トヨタが採用"})
    assert not frame.non_latin_headline({"headline": "Toyota hires 400 in Lyon"})
    assert frame.likely_non_english({"headline": "Toyota embauche", "country": "FR"})
    assert not frame.likely_non_english({"headline": "Toyota hires", "country": "GB"})


# --- the allocation --------------------------------------------------------

def test_allocation_hits_the_target_and_holds_the_floor():
    sizes = {("US", "pay"): 8, ("US", "funding"): 147, ("RoW", "leadership"): 2123,
             ("Europe", "pay"): 61, ("Europe", "funding"): 74}
    take = frame.allocate(sizes, 100, min_cell=5)
    assert sum(take.values()) == 100
    for cell, size in sizes.items():
        assert take[cell] <= size
        assert take[cell] >= min(5, size)


def test_allocation_never_asks_a_cell_for_more_than_it_holds():
    sizes = {("US", "pay"): 2, ("US", "funding"): 3, ("RoW", "leadership"): 4}
    take = frame.allocate(sizes, 200, min_cell=5)
    assert take == sizes


def test_allocation_is_independent_of_dict_order():
    sizes = {("US", "pay"): 8, ("RoW", "funding"): 1797, ("Europe", "hiring"): 312}
    reversed_sizes = dict(reversed(list(sizes.items())))
    assert frame.allocate(sizes, 60) == frame.allocate(reversed_sizes, 60)


def test_allocation_with_floors_that_overshoot_still_hits_the_target():
    sizes = {("US", "pay"): 40, ("RoW", "pay"): 40, ("Europe", "pay"): 40}
    take = frame.allocate(sizes, 6, min_cell=5)
    assert sum(take.values()) == 6


# --- the draw --------------------------------------------------------------

def _corpus(count: int = 400) -> list[dict]:
    rows = []
    for i in range(count):
        country = ["US", "FR", "BR", None, "GB", "JP"][i % 6]
        pillar = ["company_development", "leadership_change", "rewards_comp",
                  "how_we_work"][i % 4]
        rows.append({
            "content_hash": f"h{i:04d}", "signal_id": f"s{i}",
            "collector": "google_news", "country": country,
            "source_url": f"https://p{i % 7}.example.fr/a{i}",
            "pillar": pillar, "funding_amount_usd": 1000 if i % 5 == 0 else None,
            "headline": "Toyota hires" if i % 7 else "トヨタ",
            "company": "Toyota", "funding_amount": "$1M" if i % 5 == 0 else None,
            "headcount": None, "signal_direction": "neutral", "archive_url": "",
        })
    return rows


def test_the_draw_reproduces_exactly_for_a_seed():
    rows = _corpus()
    first = [r["content_hash"] for r in bench.draw(rows, 60, seed="alpha")]
    second = [r["content_hash"] for r in bench.draw(rows, 60, seed="alpha")]
    other = [r["content_hash"] for r in bench.draw(rows, 60, seed="beta")]
    assert first == second
    assert first != other
    assert len(set(first)) == 60


def test_the_draw_holds_the_language_mix_inside_each_cell():
    """A global floor is met by emptying one cell's foreign-language pool."""
    rows = _corpus()
    drawn = bench.draw(rows, 60, seed="alpha")
    cells: dict[tuple, list] = {}
    for row in drawn:
        cells.setdefault(frame.cell_of(row), []).append(row)
    mixed = 0
    for members in cells.values():
        share = sum(1 for r in members if frame.likely_non_english(r)) / len(members)
        if 0 < share < 1:
            mixed += 1
    assert mixed >= 2, "no cell carries both languages, so the mix is not being held"


# --- the definitions come from production ----------------------------------

def test_every_graded_field_takes_its_definition_from_the_live_prompt():
    from pipeline import classify

    rules = bench.field_rules()
    assert set(rules) == set(bench.FIELD_KEYS)
    for key, text in rules.items():
        assert text, key
        assert text[:40] in classify.SCHEMA_HINT, (
            f"{key}'s rule is not the production wording any more")


def test_the_direction_paragraph_is_carried_too():
    """The vocabulary line is not the definition; the paragraph under it is."""
    prose = bench.direction_rules()
    assert "A funding round is NOT hiring" in prose


def test_the_direction_paragraph_stops_before_unrelated_production_rules():
    """`direction_rules` used to run to the END of SCHEMA_HINT, past its own
    paragraph and into the `is_talent_signal` exclusion rules and the
    read-through style guide -- instructions for the extractor, not the
    referee. Every one of the benchmark's 99 parse failures on 2026-09-16 was
    gpt-4o-mini producing an unusable verdict for exactly this field, the one
    whose rule text carried instructions unrelated to answering
    correct/wrong/not_stated.
    """
    prose = bench.direction_rules()
    assert "is_talent_signal" not in prose
    assert "weak read-through is worse than none" not in prose


def test_the_prompt_does_not_carry_unrelated_production_instructions():
    prompt = bench.build_prompt({"headline": "H", "company": "Acme"}, "body text")
    assert "is_talent_signal" not in prompt


def test_the_prompt_shows_the_referee_the_six_values_and_nothing_else():
    row = {"headline": "H", "company": "Acme", "funding_amount": "$5M",
           "headcount": None, "country": "FR", "signal_direction": "hiring",
           "pillar": "company_development", "summary": "SECRET-SUMMARY",
           "talent_readthrough": "SECRET-READ"}
    prompt = bench.build_prompt(row, "body text")
    assert "SECRET-SUMMARY" not in prompt and "SECRET-READ" not in prompt
    for key in bench.FIELD_KEYS:
        assert key in prompt


def test_the_prompt_closes_the_verdict_vocabulary_against_a_fourth_word():
    """99 of 167 rows were lost to a word outside the three (2026-09-16).

    The vocabulary is stated as closed, the invented word that cost that run is
    named, and the consequence is spelled out. The CHECK is never weakened to
    admit a fourth word: `parse_answer` still refuses one.
    """
    prompt = " ".join(bench.build_prompt({"headline": "H"}, "body").split())
    for verdict in bench.VERDICTS:
        assert f'"{verdict}"' in prompt
    assert "There is no fourth word" in prompt
    assert "partially_correct" in prompt, "the word that cost the run is not named"
    verdict, reason = bench.parse_answer(
        _answer(signal_direction={"verdict": "partially_correct",
                                  "source_value": "x", "why": "y"}))
    assert verdict is None and "signal_direction" in reason


def test_the_prompt_says_neutral_is_gradeable_and_never_a_reason_to_decline():
    """93 of the 99 lost rows stored `signal_direction` = neutral."""
    prompt = bench.build_prompt(
        {"headline": "H", "signal_direction": "neutral"}, "body")
    # Collapsed: the prompt is hard-wrapped, so a sentence spans lines.
    block = " ".join(
        prompt[prompt.index("EVERY STORED VALUE IS GRADEABLE"):].split())
    assert '"neutral" is an ordinary value of signal_direction' in block
    assert "NEVER a reason to decline a verdict" in block
    # Named beside the vocabulary it belongs to, so it is not read as special.
    for other in ("hiring", "displacement", "comp_shift"):
        assert other in block


def test_the_prompt_gives_a_worked_example_for_the_neutral_confusion():
    """18 of 200 rows on 2026-09-16 were lost to openai/gpt-4o-mini writing
    {"verdict": "neutral", "source_value": "neutral"} for signal_direction —
    the exact mistake the prose above already warns against, still made after
    that warning was already live. A worked JSON example names both the right
    and the wrong shape next to each other, which prose alone had not fixed.
    """
    prompt = " ".join(bench.build_prompt(
        {"headline": "H", "signal_direction": "neutral"}, "body").split())
    assert '{"verdict": "correct", "source_value": "neutral", "why": "..."}' in prompt
    assert '{"verdict": "neutral", ...}' in prompt
    assert "never a value \"verdict\" can BE" in prompt


def test_the_prompt_gives_one_legal_way_to_decline():
    """A refusal must arrive as a verdict, not as an unparseable answer."""
    prompt = " ".join(bench.build_prompt({"headline": "H"}, "body").split())
    assert '"not_stated" IS THE ONLY WAY TO DECLINE' in prompt
    assert "Never express doubt by inventing a verdict word" in prompt
    # And the declining answer it asks for is one the parser accepts.
    verdict, reason = bench.parse_answer(
        _answer(signal_direction={"verdict": "not_stated",
                                  "source_value": "", "why": "cannot judge"}))
    assert verdict is not None and reason == ""
    assert bench.combine(verdict, verdict, "signal_direction") == (
        "unknown", "not_stated_in_source")


# --- parsing: a failure is a failure, never a verdict ----------------------

def _answer(**overrides) -> str:
    fields = {key: {"verdict": "correct", "source_value": "x", "why": "y"}
              for key in bench.FIELD_KEYS}
    fields.update(overrides)
    return json.dumps({"source_language": "fr", "fields": fields})


def test_a_fenced_answer_still_parses():
    verdict, reason = bench.parse_answer("```json\n" + _answer() + "\n```")
    assert verdict is not None and reason == ""
    assert verdict["source_language"] == "fr"


def test_a_truncated_answer_is_a_parse_failure_with_a_reason():
    verdict, reason = bench.parse_answer(_answer()[:60])
    assert verdict is None
    assert reason


def test_a_missing_field_is_a_parse_failure_and_never_a_wrong_answer():
    payload = json.loads(_answer())
    del payload["fields"]["country"]
    verdict, reason = bench.parse_answer(json.dumps(payload))
    assert verdict is None
    assert "country" in reason


def test_an_invented_verdict_word_is_refused():
    verdict, reason = bench.parse_answer(
        _answer(company={"verdict": "probably", "source_value": "", "why": ""}))
    assert verdict is None and "company" in reason


# --- the agreement rule ----------------------------------------------------

@pytest.mark.parametrize("first,second,expected,reason", [
    ("correct", "correct", "correct", ""),
    ("wrong", "wrong", "wrong", ""),
    ("correct", "wrong", "unknown", "referees_disagree"),
    ("wrong", "correct", "unknown", "referees_disagree"),
    ("correct", "not_stated", "unknown", "not_stated_in_source"),
    ("not_stated", "wrong", "unknown", "not_stated_in_source"),
    ("not_stated", "not_stated", "unknown", "not_stated_in_source"),
])
def test_the_truth_table(first, second, expected, reason):
    a = {"fields": {"company": {"verdict": first}}}
    b = {"fields": {"company": {"verdict": second}}}
    assert bench.combine(a, b, "company") == (expected, reason)


def test_a_missing_referee_is_unknown_and_never_an_agreement():
    a = {"fields": {"company": {"verdict": "wrong"}}}
    assert bench.combine(a, None, "company") == ("unknown", "no_verdict")
    assert bench.combine(None, None, "company") == ("unknown", "no_verdict")


# --- grading one row -------------------------------------------------------

STORED = {"headline": "Acme raises", "company": "Acme", "funding_amount": "$5M",
          "headcount": None, "country": "FR", "signal_direction": "hiring",
          "pillar": "company_development"}
ITEM = {"content_hash": "h1", "region": "Europe", "event_type": "funding",
        "likely_non_english": True, "source_url": "https://p.example.fr/a",
        "archive_url": ""}


def test_an_unreadable_body_is_unknown_for_every_field_and_spends_nothing():
    calls = []
    record = bench.grade_row(ITEM, STORED, start_usd=0.0, ceiling=1.0,
                             fetch=lambda url, **kw: "", wayback=lambda url: None,
                             call=lambda *a, **k: calls.append(a) or _answer())
    assert not calls, "a referee was asked about a page nobody could read"
    assert record["cost_usd"] == 0.0
    assert {entry["outcome"] for entry in record["fields"].values()} == {"unknown"}
    assert {entry["reason"] for entry in record["fields"].values()} == {"no_evidence"}


def test_a_thin_read_is_refused_rather_than_graded():
    """185 characters of a loading page is not the article."""
    record = bench.grade_row(ITEM, STORED, start_usd=0.0, ceiling=1.0,
                             fetch=lambda url, **kw: "x" * 200,
                             wayback=lambda url: None,
                             call=lambda *a, **k: _answer())
    assert record["evidence_chars"] == 0
    assert record["fields"]["company"]["reason"] == "no_evidence"


def test_one_referee_failing_to_parse_is_unknown_and_is_counted_apart(monkeypatch):
    body = "x" * 2000
    answers = iter([_answer(), "not json at all"])

    def call(model, system, user, **kw):
        return next(answers)

    monkeypatch.setattr(bench, "REFEREES", ("vendor-a/m", "vendor-b/m"))
    record = bench.grade_row(ITEM, STORED, start_usd=0.0, ceiling=1.0,
                             fetch=lambda url, **kw: body, wayback=lambda url: None,
                             call=call)
    assert {entry["outcome"] for entry in record["fields"].values()} == {"unknown"}
    assert {entry["reason"] for entry in record["fields"].values()} == {"no_verdict"}
    assert len(record["parse_failures"]) == 1
    assert record["parse_failures"][0]["model"] == "vendor-b/m"


def test_two_agreeing_referees_grade_the_fields(monkeypatch):
    monkeypatch.setattr(bench, "REFEREES", ("vendor-a/m", "vendor-b/m"))
    answer = _answer(country={"verdict": "wrong", "source_value": "BE", "why": "w"},
                     headcount={"verdict": "not_stated", "source_value": "", "why": "w"})
    record = bench.grade_row(ITEM, STORED, start_usd=0.0, ceiling=1.0,
                             fetch=lambda url, **kw: "x" * 2000,
                             wayback=lambda url: None,
                             call=lambda *a, **k: answer)
    assert record["fields"]["company"]["outcome"] == "correct"
    assert record["fields"]["country"]["outcome"] == "wrong"
    assert record["fields"]["headcount"]["outcome"] == "unknown"
    assert record["language"] == "fr"


# --- a referee that raises ---------------------------------------------------

def test_a_referee_error_is_recorded_with_its_message_not_only_its_class(monkeypatch):
    """The result file must be diagnosable on its own.

    The first paid run stored 164 rows of `no answer (ClassifyError)` and the
    run log printed nothing more; the cause had to be reconstructed from the
    bill. The upstream message travels into the referee record AND the
    parse-failure entry, whitespace collapsed, clipped at REASON_CHARS.
    """
    from pipeline import classify

    monkeypatch.setattr(bench, "REFEREES", ("vendor-a/m", "vendor-b/m"))
    upstream = ("OpenRouter 400: {\"error\":{\"message\":\"Unsupported value: "
                "\'temperature\' does not support 0 with this model.\n Only the "
                "default (1) value is supported.\"}}")

    def call(model, system, user, **kw):
        if model == "vendor-b/m":
            raise classify.ClassifyError(upstream)
        return _answer()

    record = bench.grade_row(ITEM, STORED, start_usd=0.0, ceiling=1.0,
                             fetch=lambda url, **kw: "x" * 2000,
                             wayback=lambda url: None, call=call)
    reason = record["referees"]["vendor-b/m"]["reason"]
    assert reason.startswith("no answer (ClassifyError: OpenRouter 400:")
    assert "temperature" in reason and "\n" not in reason
    assert record["parse_failures"] == [{"model": "vendor-b/m", "reason": reason}]
    assert {entry["reason"] for entry in record["fields"].values()} == {"no_verdict"}


def test_an_unparseable_answer_is_kept_beside_its_reason(monkeypatch):
    """The word the referee wrote travels with the parse failure.

    On 2026-09-16 the second referee failed 99 of 167 rows on one field and
    the answers were not kept, so the cause could only be correlated (93 of
    the 99 had a stored direction of `neutral`), never read.
    """
    monkeypatch.setattr(bench, "REFEREES", ("vendor-a/m", "vendor-b/m"))
    bad = json.loads(_answer())
    bad["fields"]["signal_direction"]["verdict"] = "partially_correct"

    def call(model, system, user, **kw):
        return json.dumps(bad) if model == "vendor-b/m" else _answer()

    record = bench.grade_row(ITEM, STORED, start_usd=0.0, ceiling=1.0,
                             fetch=lambda url, **kw: "x" * 2000,
                             wayback=lambda url: None, call=call)
    reason = record["referees"]["vendor-b/m"]["reason"]
    assert reason.startswith("field signal_direction carries no usable verdict; answer: ")
    assert "partially_correct" in reason
    assert record["parse_failures"][0]["reason"] == reason
    assert len(reason) <= bench.ANSWER_CHARS + 80


def test_a_long_upstream_message_is_clipped_not_dropped():
    from pipeline import classify

    reason = bench.no_answer(classify.ClassifyError("x" * 5000))
    assert reason.startswith("no answer (ClassifyError: xxx")
    assert len(reason) < bench.REASON_CHARS + 40


# --- the preflight ------------------------------------------------------------

def test_preflight_names_the_referee_that_cannot_answer_and_its_reason(monkeypatch):
    """One one-line question per referee, before any body is fetched."""
    from pipeline import classify

    gates, calls = [], []
    monkeypatch.setattr(bench.adj, "_gate",
                        lambda start, ceiling=None: gates.append((start, ceiling)))

    def call(model, system, user, **kw):
        calls.append((model, kw.get("max_tokens")))
        if model == "vendor-b/m":
            raise classify.ClassifyError("OpenRouter 404: No endpoints found")
        return '{"ok": true}'

    dead = bench.preflight(start_usd=0.0, ceiling=1.0,
                           referees=("vendor-a/m", "vendor-b/m"), call=call)
    assert list(dead) == ["vendor-b/m"]
    assert "OpenRouter 404" in dead["vendor-b/m"]
    # One gate read per attempt, one request per attempt, a tiny answer asked for.
    assert len(gates) == len(calls) == 1 + bench.ATTEMPTS
    assert all(max_tokens == bench.PREFLIGHT_MAX_TOKENS for _m, max_tokens in calls)


def test_preflight_is_empty_when_both_referees_answer():
    assert bench.preflight(start_usd=0.0, ceiling=1.0,
                           referees=("vendor-a/m", "vendor-b/m"),
                           call=lambda *a, **k: '{"ok": true}') == {}


def test_a_dead_referee_refuses_the_run_before_a_single_row_is_graded(
        monkeypatch, tmp_path, capsys):
    """$0.85 was spent grading 164 rows against a referee that answered none.

    The refusal is exit 1 with the referee and its reason on stderr, and
    `grade_row` is never reached, so the sample costs nothing.
    """
    sample = {"drawn_on": "2026-09-16", "seed": "s", "target": 1, "items": [
        {"content_hash": "h1", "signal_id": "s1", "region": "US",
         "event_type": "funding", "likely_non_english": False,
         "collector": "google_news", "source_url": "https://x.example.com/a",
         "archive_url": "", "stored": {}}]}
    sample_path = tmp_path / "sample-2026-09-16.json"
    sample_path.write_text(json.dumps(sample))
    result_path = tmp_path / "result-2026-09-16.json"

    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setattr(bench, "estimate", lambda *a, **k: (0.01, []))
    monkeypatch.setattr(bench, "prices", lambda *a, **k: {})
    monkeypatch.setattr(bench, "preflight",
                        lambda **k: {"vendor-b/m": "no answer (ClassifyError: OpenRouter 400: nope)"})

    def never(*args, **kwargs):
        raise AssertionError("a row was graded after a referee failed the preflight")

    monkeypatch.setattr(bench, "grade_row", never)
    monkeypatch.setattr(bench.schema, "connect", never)
    rc = bench.main(["--grade", "--sample", str(sample_path),
                     "--result", str(result_path), "--ceiling", "1.00"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "vendor-b/m" in err and "OpenRouter 400" in err
    assert not result_path.exists()


# --- the mid-run brake --------------------------------------------------------

def _graded(model_answered: dict, chars: int = 2000) -> dict:
    return {"evidence_chars": chars, "region": "US", "event_type": "funding",
            "referees": {m: {"answered": a} for m, a in model_answered.items()},
            "fields": {}, "parse_failures": []}


def test_the_brake_holds_until_there_are_enough_readable_rows():
    """Two failures out of two is not evidence; it is two rows."""
    records = [_graded({"a/m": True, "b/m": False}) for _ in range(5)]
    assert bench.referee_to_stop(records) is None


def test_the_brake_stops_a_referee_failing_most_readable_rows(monkeypatch):
    monkeypatch.setattr(bench, "REFEREES", ("a/m", "b/m"))
    records = [_graded({"a/m": True, "b/m": i % 3 == 0})
               for i in range(bench.ABORT_AFTER_ROWS)]
    stop = bench.referee_to_stop(records)
    assert stop is not None
    model, failed, readable = stop
    assert model == "b/m" and readable == bench.ABORT_AFTER_ROWS
    assert failed > bench.ABORT_RATE * readable


def test_the_brake_leaves_a_referee_that_mostly_answers_alone(monkeypatch):
    monkeypatch.setattr(bench, "REFEREES", ("a/m", "b/m"))
    records = [_graded({"a/m": True, "b/m": i % 3 != 0})
               for i in range(bench.ABORT_AFTER_ROWS)]
    assert bench.referee_to_stop(records) is None


def test_an_unreadable_body_is_never_counted_against_a_referee(monkeypatch):
    """A row nobody was asked about must not trip the brake."""
    monkeypatch.setattr(bench, "REFEREES", ("a/m", "b/m"))
    records = [_graded({}, chars=0) for _ in range(40)]
    assert bench.referee_to_stop(records) is None


def test_a_referee_stop_is_recorded_apart_from_a_budget_stop(monkeypatch, tmp_path, capsys):
    """A broken referee must reach a human; a budget stop must not.

    The budget stop exits 0 because the brake working is not a finding. A
    referee failing most rows is a defect, so it goes through the incomplete
    path and reds the workflow.
    """
    items = [{"content_hash": f"h{i}", "signal_id": f"s{i}", "region": "US",
              "event_type": "funding", "likely_non_english": False,
              "collector": "google_news", "source_url": f"https://x.example.com/{i}",
              "archive_url": "", "stored": {}}
             for i in range(bench.ABORT_AFTER_ROWS + 20)]
    sample = {"drawn_on": "2026-09-16", "seed": "s", "target": len(items), "items": items}
    sample_path = tmp_path / "sample-2026-09-16.json"
    sample_path.write_text(json.dumps(sample))
    result_path = tmp_path / "result-2026-09-16.json"

    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setattr(bench, "estimate", lambda *a, **k: (0.01, []))
    monkeypatch.setattr(bench, "prices", lambda *a, **k: {})
    monkeypatch.setattr(bench, "preflight", lambda **k: {})
    monkeypatch.setattr(bench, "REFEREES", ("a/m", "b/m"))

    class _Conn:
        def execute(self, *args):
            class _Cur:
                def fetchone(self_inner):
                    return {"headline": "h", "company": "Acme", "funding_amount": "",
                            "headcount": None, "country": "US",
                            "signal_direction": "neutral", "pillar": "company_development"}
            return _Cur()

        def commit(self):
            pass

    monkeypatch.setattr(bench.schema, "connect", lambda *a, **k: _Conn())
    graded = []

    def grade(item, row, **kwargs):
        record = _graded({"a/m": True, "b/m": False})
        record["content_hash"] = item["content_hash"]
        record["likely_non_english"] = False
        record["fields"] = {key: {"outcome": "unknown", "reason": "no_verdict"}
                            for key in bench.FIELD_KEYS}
        graded.append(record)
        return record

    monkeypatch.setattr(bench, "grade_row", grade)
    rc = bench.main(["--grade", "--sample", str(sample_path),
                     "--result", str(result_path), "--ceiling", "1.00"])
    out = capsys.readouterr().out
    assert "REFEREE STOP" in out
    assert len(graded) == bench.ABORT_AFTER_ROWS, "the rest of the sample was bought"
    written = json.loads(result_path.read_text())
    assert "b/m" in written["referee_stop"]
    assert "budget_stop" not in written, "a broken referee read as the budget working"
    assert rc == 2, "a broken referee did not reach a human"


# --- the meter -------------------------------------------------------------

def test_the_gate_is_read_before_every_request_and_the_retry_is_outside_the_call(monkeypatch):
    """One request per call, and a gate read in front of each attempt.

    A callable that retries internally puts several charges behind one gate
    read; that is the once-per-item defect the sibling tracker overshot its
    ceiling by 36 calls with.
    """
    from pipeline import classify

    gates, calls = [], []
    monkeypatch.setattr(bench.adj, "_gate",
                        lambda start, ceiling=None: gates.append((start, ceiling)))

    def call(model, system, user, **kw):
        calls.append(model)
        if len(calls) == 1:
            raise classify.Throttled("429")
        return _answer()

    verdict, reason, cost = bench.ask("vendor-a/m", "prompt", start_usd=0.0,
                                      ceiling=1.0, call=call)
    assert verdict is not None
    assert len(calls) == 2, "the retry did not go through a second gated attempt"
    assert len(gates) == 2, "a request was made without reading the gate"
    assert gates[0] == (0.0, 1.0)


def test_a_budget_stop_propagates_rather_than_being_swallowed(monkeypatch):
    def gate(start, ceiling=None):
        raise bench.adj.BudgetStop("ceiling reached")

    monkeypatch.setattr(bench.adj, "_gate", gate)
    with pytest.raises(bench.adj.BudgetStop):
        bench.ask("vendor-a/m", "prompt", start_usd=0.0, ceiling=1.0,
                  call=lambda *a, **k: _answer())


# --- the tally -------------------------------------------------------------

def _record(outcomes: dict, region="US", event="funding") -> dict:
    return {"region": region, "event_type": event, "likely_non_english": False,
            "evidence_chars": 1000, "parse_failures": [], "language": "en",
            "fields": {key: {"outcome": outcomes.get(key, "correct"), "reason": ""}
                       for key in bench.FIELD_KEYS}}


def test_unknown_never_reaches_the_denominator():
    records = [_record({"company": "correct"}), _record({"company": "wrong"}),
               _record({"company": "unknown"}), _record({"company": "unknown"})]
    company = bench.tally(records)["company"]
    assert (company["correct"], company["wrong"], company["judged"]) == (1, 1, 2)
    assert company["unknown"] == 2
    assert company["accuracy"] == 0.5
    low, high = company["interval"]
    assert low < 0.5 < high


def test_a_field_nothing_judged_reports_unknown_rather_than_a_rate():
    records = [_record({"headcount": "unknown"}) for _ in range(20)]
    headcount = bench.tally(records)["headcount"]
    assert headcount["judged"] == 0
    assert headcount["accuracy"] is None and headcount["interval"] is None


def test_an_unjudged_stratum_makes_the_run_incomplete():
    records = [_record({}, region="US"), _record({"company": "unknown"}, region="Europe")]
    summary = bench.summarise({"items": records}, records, 0.0, 1.0, 0.5)
    gaps = bench.incomplete(summary)
    assert any("Europe" in gap and "company" in gap for gap in gaps)
    assert bench.render(summary)


def test_parse_failures_are_reported_apart_from_wrong_answers():
    records = [_record({}), _record({})]
    records[0]["parse_failures"] = [{"model": "vendor-b/m", "reason": "truncated"}]
    summary = bench.summarise({"items": records}, records, 0.0, 1.0, None)
    assert summary["parse_failures"] == 1
    assert summary["parse_failures_by_model"] == {"vendor-b/m": 1}
    assert summary["overall"]["company"]["wrong"] == 0


# --- the price and the ceiling ---------------------------------------------

def test_an_unpriceable_run_is_unknown_and_never_free():
    total, lines = bench.estimate(200, 10_000, {})
    assert total is None
    assert any("UNKNOWN" in line for line in lines)


def test_the_estimate_uses_the_live_price_of_both_referees():
    table = {bench.REFEREES[0]: (1e-6, 5e-6), bench.REFEREES[1]: (2.5e-7, 2e-6)}
    total, lines = bench.estimate(100, 4390, table)
    assert len(lines) == 2
    assert total == pytest.approx(100 * ((1000 * 1e-6 + 420 * 5e-6)
                                         + (1000 * 2.5e-7 + 420 * 2e-6)))


def test_a_ceiling_past_the_owners_bar_is_refused_not_clamped(capsys):
    assert bench.main(["--estimate", "--ceiling", "9.99"]) == 1
    assert "6.00" in capsys.readouterr().err


def test_the_hard_bar_is_the_approved_share_of_the_grant():
    import budget

    grant = budget.load_grants().get("2026-09") or {}
    assert bench.CEILING_MAX_USD <= float(grant.get("usd", 0)), (
        "the benchmark's hard bar is above the owner's committed grant")


# --- the committed sample --------------------------------------------------

def test_the_committed_sample_is_the_one_this_code_would_draw():
    path = bench.latest("sample-*.json")
    assert path, "no sample is committed; --draw has never been run"
    sample = json.loads(path.read_text())
    items = sample["items"]
    assert len(items) == sample["target"] == 200
    assert len({item["content_hash"] for item in items}) == 200
    assert set(sample["drawn_cells"]) == {
        f"{region}/{event}" for region in frame.REGIONS for event in frame.EVENT_TYPES}
    assert min(sample["drawn_cells"].values()) >= 5
    assert all(item["source_url"].startswith("http") for item in items)
    # Multilingual is a property of the sample, not an aspiration in a docstring.
    assert sample["drawn_language_proxy"]["likely_non_english"] >= 40


def test_a_budget_stop_is_not_an_incomplete_measurement(monkeypatch, tmp_path, capsys):
    """The budget working must not manufacture a red run.

    A ceiling reached part way through leaves every remaining field ungraded.
    Read as "this stratum judged nothing" that is exit 2 and a red workflow,
    which is an alarm about a brake doing its job.
    """
    sample = {"drawn_on": "2026-09-16", "seed": "s", "target": 1, "items": [
        {"content_hash": "h1", "signal_id": "s1", "region": "US",
         "event_type": "funding", "likely_non_english": False,
         "collector": "google_news", "source_url": "https://x.example.com/a",
         "archive_url": "", "stored": {}}]}
    sample_path = tmp_path / "sample-2026-09-16.json"
    sample_path.write_text(json.dumps(sample))
    result_path = tmp_path / "result-2026-09-16.json"

    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setattr(bench, "estimate", lambda *a, **k: (0.01, []))
    monkeypatch.setattr(bench, "prices", lambda *a, **k: {})
    monkeypatch.setattr(bench, "preflight", lambda **k: {})

    class _Row(dict):
        pass

    class _Conn:
        def execute(self, *args):
            class _Cur:
                def fetchone(self_inner):
                    return {"headline": "h", "company": "Acme", "funding_amount": "",
                            "headcount": None, "country": "US",
                            "signal_direction": "neutral", "pillar": "company_development"}
            return _Cur()

        def commit(self):
            pass

    monkeypatch.setattr(bench.schema, "connect", lambda *a, **k: _Conn())

    def stop(*args, **kwargs):
        raise bench.adj.BudgetStop("run ceiling $0.01 reached")

    monkeypatch.setattr(bench, "grade_row", stop)
    rc = bench.main(["--grade", "--sample", str(sample_path),
                     "--result", str(result_path), "--ceiling", "0.01"])
    out = capsys.readouterr().out
    assert rc == 0, "the budget working reddened the run"
    assert "UNDECIDED" in out
    written = json.loads(result_path.read_text())
    assert written["budget_stop"]


# --- the standalone-brand rule ---------------------------------------------

def test_a_row_citing_a_commercial_provider_never_reaches_the_frame():
    """Two rules at once, and either alone would be enough.

    The sample is a committed, PUBLIC artifact and no tracked file here may
    carry a commercial data-provider name. And a row whose source is an
    aggregator should not exist at all - aggregators are discovery pointers,
    never stored sources - so the page behind it is not the kind of document
    this benchmark grades against. Redaction is the wrong answer, unlike in the
    gate ledger where the text IS the payload: this benchmark has to FETCH the
    URL, and a redacted URL fetches nothing.

    The banned name is taken from `provider_names.BANNED` and never typed here:
    a guard that has to spell the thing it forbids trips the guard beside it,
    which is how this test first went red.

    It was CI that caught the real row, not review: the local suite passed
    because the freshly written sample was not yet tracked when it ran.
    """
    from pipeline import provider_names

    banned = provider_names.BANNED[1]
    row = {"collector": "google_news", "content_hash": "abc",
           "source_url": f"https://app.{banned}.co/news/note/x"}
    assert frame.cites_a_provider(row)
    assert not frame.eligible(row)
    assert frame.eligible({**row, "source_url": "https://sifted.eu/articles/x"})
    # The same name in a headline, which is how it usually arrives.
    assert frame.cites_a_provider({"headline": f"Acme raises $1M - {banned}"})


def test_aggregator_sourced_is_narrower_than_cites_a_provider():
    """Two questions, two functions. `cites_a_provider` keeps a name out of
    the public sample and reads four fields. `sourced_from_an_aggregator`
    asks who served the document and reads the source host alone. A listed
    company NAMED like a provider, filing on its stock exchange, is caught by
    the first and must never be caught by the second: the exchange is a
    primary document. The first stays the frame gate because the sample is a
    committed artifact; the second is what "is this row aggregator-sourced"
    means, and it is the write path's own predicate.
    """
    from pipeline import provider_names

    banned = provider_names.BANNED[0]
    filing = {"collector": "bse_india", "content_hash": "abc",
              "company": f"{banned.title()} Technologies Ltd",
              "headline": f"{banned.title()} Technologies Ltd: Appointment of a director",
              "source_url": "https://www.bseindia.com/corporates/anndet_new?newsid=1"}
    assert frame.cites_a_provider(filing)
    assert not frame.sourced_from_an_aggregator(filing)

    note = {"collector": "google_news", "content_hash": "def",
            "source_url": f"https://app.{provider_names.BANNED[1]}.co/news/note/x"}
    assert frame.cites_a_provider(note)
    assert frame.sourced_from_an_aggregator(note)


def test_the_committed_sample_carries_no_provider_name():
    from pipeline import provider_names

    path = bench.latest("sample-*.json")
    assert path
    assert not provider_names.contains(path.read_text())


def test_default_result_filename_is_unique_per_run_not_per_day():
    """result-<date>.json overwrote itself when two runs landed the same
    day (run 3 on 2026-09-16 overwrote run 2). The filename now carries the
    time too, so two runs on one day produce two distinct files."""
    import datetime as _dt

    first = bench.default_result_filename(
        now=_dt.datetime(2026, 9, 16, 14, 0, 0, tzinfo=_dt.timezone.utc))
    second = bench.default_result_filename(
        now=_dt.datetime(2026, 9, 16, 22, 0, 0, tzinfo=_dt.timezone.utc))
    assert first != second
    assert first.startswith("result-2026-09-16")
    assert second.startswith("result-2026-09-16")


def test_two_runs_on_one_day_write_two_files_not_one(tmp_path, monkeypatch):
    """The regression itself: writing two runs' filenames into the same
    directory must produce two files, never one overwriting the other."""
    import datetime as _dt

    monkeypatch.setattr(bench, "OUT_DIR", tmp_path)
    run_one = tmp_path / bench.default_result_filename(
        now=_dt.datetime(2026, 9, 16, 9, 0, 0, tzinfo=_dt.timezone.utc))
    run_two = tmp_path / bench.default_result_filename(
        now=_dt.datetime(2026, 9, 16, 20, 0, 0, tzinfo=_dt.timezone.utc))
    run_one.write_text('{"run": 1}')
    run_two.write_text('{"run": 2}')

    files = sorted(tmp_path.glob("result-2026-09-16*.json"))
    assert len(files) == 2
    assert json.loads(run_one.read_text())["run"] == 1
    assert json.loads(run_two.read_text())["run"] == 2


def test_latest_still_finds_the_newest_timestamped_result(tmp_path, monkeypatch):
    """`latest()` is the reader every report/status path uses for "the
    latest result" - it must keep working with the new, time-qualified
    filenames (it sorts lexicographically, and the new format is still
    chronologically sortable as a string)."""
    monkeypatch.setattr(bench, "OUT_DIR", tmp_path)
    (tmp_path / "result-2026-09-15-235900.json").write_text('{"run": "old"}')
    (tmp_path / "result-2026-09-16-090000.json").write_text('{"run": "morning"}')
    (tmp_path / "result-2026-09-16-200000.json").write_text('{"run": "evening"}')

    newest = bench.latest("result-*.json")
    assert newest is not None
    assert json.loads(newest.read_text())["run"] == "evening"
