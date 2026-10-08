"""ab_github_models: the one-word parser must keep 'did not answer' distinct
from NO (a broken model must not read as a conservative one), and the model
picker must only take ids the LIVE catalog lists."""
from ab_github_models import gate_answer, pick_models


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
