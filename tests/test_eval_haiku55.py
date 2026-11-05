"""eval_haiku55: chosen candidate, READ-only steps, truncation counted."""
import importlib

import eval_haiku55 as ev


def _reload(monkeypatch, **env):
    for k in ("EVAL_CANDIDATE", "EVAL_STEPS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(ev)


def test_default_candidate_and_read_baseline_is_sonnet_55(monkeypatch):
    m = _reload(monkeypatch)
    assert m.CANDIDATE == "anthropic/claude-haiku-5.5"
    assert m.READ_MODELS == ("anthropic/claude-sonnet-5.5", m.CANDIDATE)


def test_candidate_from_env(monkeypatch):
    m = _reload(monkeypatch, EVAL_CANDIDATE="google/gemini-2.5-flash-lite")
    assert m.READ_MODELS == ("anthropic/claude-sonnet-5.5", "google/gemini-2.5-flash-lite")


def test_steps_parse(monkeypatch):
    m = _reload(monkeypatch)
    assert m.steps("") == {"gate", "extraction", "referee", "read"}
    assert m.steps("read") == {"read"}
    assert m.steps(" Read , gate ") == {"read", "gate"}


def test_read_only_runs_baseline_extraction_only(monkeypatch):
    m = _reload(monkeypatch, EVAL_CANDIDATE="google/gemini-2.5-flash-lite")
    ran = []
    monkeypatch.setattr(m, "run_gate", lambda *a: ran.append("gate"))
    monkeypatch.setattr(m, "run_referee", lambda *a: ran.append("referee"))
    monkeypatch.setattr(m, "run_extraction",
                        lambda items, key, dry, models=None: ran.append(("extraction", models)) or {})
    monkeypatch.setattr(m, "run_read", lambda *a: ran.append("read"))
    monkeypatch.setenv("EVAL_STEPS", "read")
    assert m.main(["--dry-run"]) == 0
    assert ran == [("extraction", (m.EXTRACTION_MODELS[0],)), "read"]


def test_truncated_reply_is_counted(monkeypatch):
    m = _reload(monkeypatch)

    class R:
        status_code = 200

        def json(self):
            return {"usage": {"cost": 0.001},
                    "choices": [{"finish_reason": "length", "message": {"content": '{"a": "b'}}]}

    monkeypatch.setattr(m.requests, "post", lambda *a, **k: R())
    m._spent["usd"] = 0.0
    content, cost, err = m.call("x/y", "s", "u", max_tokens=5, key="k")
    assert err == "truncated" and content == '{"a": "b' and cost == 0.001


def test_read_max_tokens_defaults_to_production(monkeypatch):
    from pipeline import classify
    monkeypatch.delenv("EVAL_READ_MAX_TOKENS", raising=False)
    m = _reload(monkeypatch)
    assert m.read_max_tokens() == classify.READ_MAX_TOKENS
    monkeypatch.setenv("EVAL_READ_MAX_TOKENS", "400")
    assert m.read_max_tokens() == 400
    monkeypatch.setenv("EVAL_READ_MAX_TOKENS", "")
    assert m.read_max_tokens() == classify.READ_MAX_TOKENS


def test_error_kinds_are_reported(monkeypatch):
    m = _reload(monkeypatch)
    assert m.error_kind("truncated") == "truncated"
    assert m.error_kind("HTTP 502") == "api"
    assert m.error_kind("network Timeout") == "api"
    assert m.error_kind("cap") == "cap"
    t = m.Tally()
    t.add_call(0.0, "truncated")
    t.add_call(0.0, "HTTP 500")
    t.add_call(0.0, None)
    t.add_parse_error()
    assert t.err == 3
    assert "errors_by_type=truncated:1,api:1,parse:1" in t.line()
