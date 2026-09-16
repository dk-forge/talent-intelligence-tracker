"""The narrow door for re-pointing a citation, and the general door it leaves shut.

tests/test_form_d_correction.py holds the rule that /correct never carries
headline, source_url or source_name. This module holds the complement: the
ONE route that may write those three writes nothing else, is keyed, holds the
headline invariant server-side, and is reached by the client only for a row a
committed ledger already records.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pipeline import publish, re_source

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
API = (PLUGIN / "includes" / "api.php").read_text()
MAIN = (PLUGIN / "talent-intelligence-tracker.php").read_text()


def _body(function_name: str) -> str:
    start = API.index(f"function {function_name}(")
    body = API[start:]
    return body[:body.index("\n}\n")]


def _list_body(function_name: str) -> str:
    """The text of a one-return allowlist function, closing brace excluded."""
    return _body(function_name)


# --- the allowlists -----------------------------------------------------------

def test_the_re_source_allowlist_is_exactly_the_three_citation_columns():
    listed = set(re.findall(r"'([a-z_]+)'", _list_body("tit_resourceable_columns")))
    assert listed == {"source_url", "source_name", "headline"}


def test_the_general_door_never_reads_the_re_source_allowlist():
    """Widening /correct by pointing it at the second list would be the same
    widening under another name."""
    assert "tit_resourceable_columns" not in _body("tit_api_correct")
    assert "tit_correctable_columns" not in _body("tit_api_resource")


def test_the_two_allowlists_are_disjoint():
    correct = set(re.findall(r"'([a-z_]+)'", _list_body("tit_correctable_columns")))
    resource = set(re.findall(r"'([a-z_]+)'", _list_body("tit_resourceable_columns")))
    assert correct and resource
    assert not (correct & resource)


# --- the route ------------------------------------------------------------------

def test_the_route_is_keyed_like_every_other_write():
    route = API[API.index("register_rest_route(TIT_NS, '/re-source'"):]
    assert "'permission_callback' => $keyed" in route[:300]
    assert "__return_true" not in route[:300]
    assert "'callback' => 'tit_api_resource'" in route[:300]


def test_the_handler_holds_the_headline_invariant_and_the_collector_scope():
    handler = _body("tit_api_resource")
    assert "tit_headline_only_lost_the_masthead(" in handler
    assert "'collector'" in handler and "$collector" in handler
    update = handler[handler.index("$wpdb->update"):]
    assert "'collector'    => $collector" in update
    assert "'is_current'   => 1" in update
    # Both halves of the citation are demanded before the row is even read.
    assert "source_url and source_name are both required" in handler
    assert "the citation must move to a different host" in handler


def test_the_handler_writes_only_the_three_columns():
    handler = _body("tit_api_resource")
    written = set(re.findall(r"\$data\['([a-z_]+)'\]|'([a-z_]+)' => \$new_", handler))
    columns = {a or b for a, b in written}
    assert columns == {"source_url", "source_name", "headline"}


def test_the_new_route_ships_with_a_version_bump():
    """FTP deploys run no activation hook; the version bump is what flushes."""
    version = re.search(r"define\('TIT_VERSION', '([\d.]+)'\)", MAIN).group(1)
    header = re.search(r"\* Version: ([\d.]+)", MAIN).group(1)
    assert version == header
    assert tuple(int(p) for p in version.split(".")) >= (1, 88, 7)


# --- the client and its ledger ---------------------------------------------------

def _ledger(tmp_path, **entries):
    ledger = {}
    for h, (url, name, headline) in entries.items():
        re_source.record(ledger, h, source_url=url, source_name=name,
                         headline=headline, route="index")
    path = tmp_path / "ledger.json"
    re_source.save_ledger(ledger, path)
    return re_source.load_ledger(path)


class _Poster:
    def __init__(self, status=200, body=None):
        self.calls = []
        self.status = status
        self.body = body if body is not None else {"resourced": 1, "errors": []}

    def post(self, url, **kw):
        self.calls.append({"url": url, **kw})
        poster = self

        class R:
            status_code = poster.status
            text = json.dumps(poster.body)

            def json(self):
                return poster.body
        return R()


ROW = {"content_hash": "h-acme", "collector": "google_news"}
PROPOSAL = dict(source_url="https://publisher.example/acme-20m", source_name="Publisher",
                headline="Acme Robotics raises $20M Series A")


def test_a_push_without_a_ledger_entry_builds_no_request(tmp_path, monkeypatch):
    monkeypatch.setenv("WP_SITE_URL", "https://example.test/blog")
    monkeypatch.setenv("WP_API_KEY", "k")
    poster = _Poster()
    with pytest.raises(re_source.NotInLedger):
        re_source.push(ROW, ledger=_ledger(tmp_path), session=poster, **PROPOSAL)
    assert poster.calls == []


def test_a_push_that_differs_from_the_record_builds_no_request(tmp_path, monkeypatch):
    monkeypatch.setenv("WP_SITE_URL", "https://example.test/blog")
    monkeypatch.setenv("WP_API_KEY", "k")
    ledger = _ledger(tmp_path, **{"h-acme": (PROPOSAL["source_url"], "Someone Else",
                                            PROPOSAL["headline"])})
    poster = _Poster()
    with pytest.raises(re_source.NotInLedger, match="source_name"):
        re_source.push(ROW, ledger=ledger, session=poster, **PROPOSAL)
    assert poster.calls == []


def test_a_recorded_push_reaches_the_narrow_door_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setenv("WP_SITE_URL", "https://example.test/blog")
    monkeypatch.setenv("WP_API_KEY", "k")
    ledger = _ledger(tmp_path, **{"h-acme": (PROPOSAL["source_url"], PROPOSAL["source_name"],
                                            PROPOSAL["headline"])})
    poster = _Poster()
    out = re_source.push(ROW, ledger=ledger, session=poster, **PROPOSAL)
    assert out["resourced"] == 1
    (call,) = poster.calls
    assert call["url"].endswith("/wp-json/talent/v1/re-source")
    assert "/correct" not in call["url"]
    assert call["json"]["collector"] == "google_news"
    (sent,) = call["json"]["rows"]
    assert set(sent) == {"content_hash", "source_url", "source_name", "headline"}
    assert call["headers"]["User-Agent"] == publish.USER_AGENT


def test_a_refused_row_is_a_publish_error_not_a_count(tmp_path, monkeypatch):
    monkeypatch.setenv("WP_SITE_URL", "https://example.test/blog")
    monkeypatch.setenv("WP_API_KEY", "k")
    ledger = _ledger(tmp_path, **{"h-acme": (PROPOSAL["source_url"], PROPOSAL["source_name"],
                                            PROPOSAL["headline"])})
    poster = _Poster(body={"resourced": 0, "errors": [
        {"index": 0, "error": "headline may only lose the current masthead suffix"}]})
    with pytest.raises(publish.PublishError, match="masthead"):
        re_source.push(ROW, ledger=ledger, session=poster, **PROPOSAL)


def test_a_ledger_entry_needs_every_field_and_a_known_route():
    with pytest.raises(ValueError):
        re_source.record({}, "h", source_url="https://x.example/a", source_name="",
                         headline="h", route="index")
    with pytest.raises(ValueError):
        re_source.record({}, "h", source_url="https://x.example/a", source_name="X",
                         headline="h", route="guess")


def test_the_php_harness_is_run_in_ci():
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
    assert "php tests/php/re_source.php" in workflow
