"""The key-limit check reads OpenRouter's own `limit_remaining`.

Ported from ai-layoff-tracker#597. A key whose limit resets monthly reports
lifetime `usage` far above its `limit` while `limit_remaining` says the full
limit is left. `limit - usage` read that as exhausted and printed a false
"key limit reached" warning. `limit_remaining` is the truth when present;
`limit - usage` is only the fallback.
"""

from __future__ import annotations

import sys

import spend


def _run(monkeypatch, capsys, state):
    monkeypatch.setattr(spend, "fetch", lambda: state)
    monkeypatch.setattr(spend, "month_delta", lambda used: (0.0, "2026-11"))
    monkeypatch.setattr(sys, "argv", ["spend.py"])
    monkeypatch.delenv("GITHUB_ENV", raising=False)
    assert spend.main() == 0
    return capsys.readouterr().out


def test_monthly_reset_key_with_limit_remaining_stays_on(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, {
        "usage": 93.5972, "limit": 15.0, "limit_remaining": 15.0,
        "limit_reset": "monthly"})
    assert "key limit reached" not in out
    assert "under $1 left" not in out
    assert "remaining on key    $15.0000" in out


def test_limit_remaining_zero_is_reported_as_exhausted(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, {
        "usage": 93.5972, "limit": 15.0, "limit_remaining": 0.0,
        "limit_reset": "monthly"})
    assert "key limit reached" in out


def test_without_limit_remaining_falls_back_to_limit_minus_usage(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, {"usage": 16.0, "limit": 15.0})
    assert "key limit reached" in out
