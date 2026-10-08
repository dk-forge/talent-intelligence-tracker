"""ab_github_models: the one-word parser must keep 'did not answer' distinct
from NO (a broken model must not read as a conservative one), and the model
picker must only take ids the LIVE catalog lists."""
from unittest.mock import patch

import ab_github_models
from ab_github_models import fetch_catalog, gate_answer, pick_models


def test_answer_parsing_keeps_unanswered_distinct_from_no():
    assert gate_answer(" YES") is True
    assert gate_answer('"No."') is False
    assert gate_answer("") is None
    assert gate_answer("Maybe") is None


def test_picker_only_uses_listed_ids():
    cat = [{"id": "xai/grok-3-mini"}, {"id": "openai/gpt-4.1-nano"},
           {"id": "openai/gpt-5"}, {"id": "meta/llama-3.3-70b-instruct"}]
    assert pick_models(cat) == ["xai/grok-3-mini", "openai/gpt-4.1-nano",
                                "meta/llama-3.3-70b-instruct"]
    assert pick_models([]) == []


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = body

    def json(self):
        import json
        return json.loads(self.text)


def test_fetch_catalog_reports_http_errors_instead_of_crashing():
    """A non-2xx catalog response (the live run's actual failure: an empty,
    non-JSON body) must come back as a readable error, not an unhandled
    JSONDecodeError three frames deep in requests."""
    with patch.object(ab_github_models.requests, "get",
                       return_value=_FakeResponse(403, "")):
        cat, err = fetch_catalog("tok")
    assert cat == []
    assert "403" in err


def test_fetch_catalog_sends_the_github_accept_header():
    """Every other GitHub API caller in this repo sets Accept:
    application/vnd.github+json (runner_pick.py, reference_freshness.py,
    depth_source_probe.py, and this file's own call() for the inference
    endpoint) -- the catalog GET was the one place that forgot it."""
    with patch.object(ab_github_models.requests, "get",
                       return_value=_FakeResponse(200, "[]")) as mock_get:
        fetch_catalog("tok")
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Accept"] == "application/vnd.github+json"


def test_fetch_catalog_returns_the_parsed_list_on_success():
    with patch.object(ab_github_models.requests, "get",
                       return_value=_FakeResponse(200, '[{"id": "xai/grok-3-mini"}]')):
        cat, err = fetch_catalog("tok")
    assert err == ""
    assert cat == [{"id": "xai/grok-3-mini"}]
