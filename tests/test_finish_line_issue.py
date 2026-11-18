"""The dk-forge/todo issue filer, copied with finish_line_issue.py from
dk-forge/asktherecruiter-sandbox PR #4352 (2026-11-18): it lists open issues
instead of searching (search answers 422 for a repo the token cannot see),
moves to the next token on a 422, pages through every issue and fails rather
than file a duplicate on an incomplete scan, and logs GitHub's own error.
"""
import importlib.util
import urllib.error
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _filer(tmp_path, monkeypatch, call):
    fi = _load("finish_line_issue")
    monkeypatch.setenv("TODO_ISSUES_TOKEN", "first")
    monkeypatch.setenv("TODO_ISSUES_TOKEN_FALLBACK", "second")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    body = tmp_path / "b.md"
    body.write_text("x")
    return fi.main(["--title", "t", "--product", "ATR", "--body-file", str(body)], call=call)


def _http(code, msg):
    import io
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(('{"message": "%s"}' % msg).encode()))


def test_issue_filer_moves_on_after_a_422(tmp_path, monkeypatch, capsys):
    used = []

    def call(method, path, token, body=None):
        used.append(token)
        if token == "first":
            raise _http(422, "Validation Failed")
        return [] if method == "GET" else {"html_url": "https://github.com/dk-forge/todo/issues/1"}
    assert _filer(tmp_path, monkeypatch, call) == 0
    assert "second" in used
    assert "FILED: https://github.com" in capsys.readouterr().out


def test_issue_filer_reports_what_github_said(tmp_path, monkeypatch, capsys):
    def call(method, path, token, body=None):
        raise _http(422, "Validation Failed: label does not exist")
    assert _filer(tmp_path, monkeypatch, call) == 0
    out = capsys.readouterr().out
    assert "NOT FILED" in out and "label does not exist" in out
    assert "no token can write" not in out


def test_issue_filer_dedupes_without_the_search_api(tmp_path, monkeypatch, capsys):
    paths = []

    def call(method, path, token, body=None):
        paths.append((method, path))
        if method == "GET":
            return [{"number": 7, "title": "t", "state": "open"}]
        return {}
    assert _filer(tmp_path, monkeypatch, call) == 0
    assert not any("/search/" in p for _, p in paths)
    assert "REPEAT" in capsys.readouterr().out


@pytest.mark.parametrize("labels", [["Claude", "ATR"]])
def test_issue_filer_labels_exist_in_todo(labels):
    fi = _load("finish_line_issue")
    assert fi.plan(None, "t", "ATR") == ("create", labels)


def test_issue_filer_pages_past_500_and_finds_issue_501(tmp_path, monkeypatch, capsys):
    issues = [{"number": n, "title": f"other {n}", "state": "open"} for n in range(500)]
    issues.append({"number": 501, "title": "t", "state": "open"})
    posts = []

    def call(method, path, token, body=None):
        if method == "GET":
            page = int(path.rsplit("page=", 1)[1])
            return issues[(page - 1) * 100:page * 100]
        posts.append(path)
        return {}
    assert _filer(tmp_path, monkeypatch, call) == 0
    assert not any(p.endswith("/issues") for p in posts)
    assert "REPEAT: https://github.com/dk-forge/todo/issues/501" in capsys.readouterr().out


def test_issue_filer_never_creates_on_an_incomplete_scan(tmp_path, monkeypatch, capsys):
    posts = []

    def call(method, path, token, body=None):
        if method == "GET":
            return [{"number": 1, "title": "other", "state": "open"}] * 100
        posts.append(path)
        return {}
    assert _filer(tmp_path, monkeypatch, call) == 1
    assert posts == []
    assert "NOT FILED" in capsys.readouterr().out


def test_issue_filer_falls_back_to_the_second_token(tmp_path, monkeypatch, capsys):
    import urllib.error
    fi = _load("finish_line_issue")
    monkeypatch.setenv("TODO_ISSUES_TOKEN", "first")
    monkeypatch.setenv("TODO_ISSUES_TOKEN_FALLBACK", "second")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    used = []

    def call(method, path, token, body=None):
        used.append(token)
        if token == "first":
            raise urllib.error.HTTPError(path, 403, "no", {}, None)
        return [] if method == "GET" else {"html_url": "https://github.com/dk-forge/todo/issues/1"}
    body = tmp_path / "b.md"
    body.write_text("x")
    assert fi.main(["--title", "t", "--product", "ATR", "--body-file", str(body)], call=call) == 0
    assert used[0] == "first" and "second" in used
    assert "FILED: https://github.com" in capsys.readouterr().out
