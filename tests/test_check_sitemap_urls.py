"""Sitemap checks use fake responses only."""
from unittest.mock import Mock
import pytest
import check_sitemap_urls as checker


@pytest.mark.parametrize("status,headers,body,expected", [
    (200, {}, '<meta name="robots" content="index, follow">', []),
    (301, {"Location": "https://example.invalid/new"}, "", ["HTTP 301 -> https://example.invalid/new"]),
    (200, {"X-Robots-Tag": "NOINDEX"}, "", ["X-Robots-Tag: NOINDEX"]),
    (200, {}, '<meta name="robots" content="noindex">', ["meta robots: ['noindex']"]),
    (200, {}, '<meta name="robots" content="index"><meta name="robots" content="follow">', ["2 robots tags: ['index', 'follow']"]),
])
def test_checks_status_and_indexability(monkeypatch, status, headers, body, expected):
    fetch = Mock(return_value=(status, headers, body))
    monkeypatch.setattr(checker, "fetch", fetch)
    assert checker.check_one("https://example.invalid/company/fixture/") == expected
    fetch.assert_called_once()


def test_transient_server_failure_retries_without_real_sleep(monkeypatch):
    fetch = Mock(side_effect=[(502, {}, ""), (504, {}, ""), (200, {}, "")])
    sleep = Mock()
    monkeypatch.setattr(checker, "fetch", fetch)
    monkeypatch.setattr(checker.time, "sleep", sleep)
    assert checker.check_one("https://example.invalid/") == []
    assert [c.args for c in sleep.call_args_list] == [(5,), (10,)]
    assert fetch.call_count == 3


def test_persistent_failure_is_reported_after_bounded_attempts(monkeypatch):
    fetch = Mock(return_value=(503, {}, ""))
    monkeypatch.setattr(checker, "fetch", fetch)
    monkeypatch.setattr(checker.time, "sleep", Mock())
    assert checker.check_one("https://example.invalid/") == ["HTTP 503"]
    assert fetch.call_count == 3
