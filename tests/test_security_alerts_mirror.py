"""Unit tests for scripts/ops/security_alerts_mirror.py."""

from __future__ import annotations

import importlib.util
import io
import json
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "security_alerts_mirror", HERE / "security_alerts_mirror.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)  # type: ignore[union-attr]

SECRET = "ghp_THIS_MUST_NEVER_APPEAR_1234567890"
CS = {"rule": {"id": "py/sql-injection", "security_severity_level": "high"},
      "most_recent_instance": {"location": {"path": "backend/src/a.py", "start_line": 12}},
      "created_at": "2026-10-01T00:00:00Z", "html_url": "https://gh/cs/1"}
DB = {"dependency": {"package": {"name": "requests"}, "manifest_path": "backend/requirements.txt"},
      "security_advisory": {"ghsa_id": "GHSA-x", "severity": "medium"},
      "security_vulnerability": {"severity": "medium"},
      "created_at": "2026-10-02T00:00:00Z", "html_url": "https://gh/db/2"}
SS = {"secret_type": "github_personal_access_token", "secret_type_display_name": "GitHub PAT",
      "secret": SECRET, "first_location_detected": {"path": "x/.env", "start_line": 3},
      "created_at": "2026-10-03T00:00:00Z", "html_url": "https://gh/ss/3"}


def _ok(*alerts, token="GITHUB_TOKEN"):
    return {"ok": True, "alerts": list(alerts), "token": token}


def _res(cs=None, db=None, ss=None):
    return {"code_scanning": cs or _ok(), "dependabot": db or _ok(), "secret_scanning": ss or _ok()}


class _Resp:
    def __init__(self, data, link=None):
        self._d, self.headers = json.dumps(data).encode(), {"Link": link} if link else {}

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class MirrorTests(unittest.TestCase):

    def test_body_has_counts_table_and_never_the_secret_value(self) -> None:
        body = m.build_body(_res(_ok(CS), _ok(DB), _ok(SS)))
        assert SECRET not in body
        assert "| Code scanning | 1 | 0 | 1 |" in body
        assert "| Dependabot | 1 | 0 | 0 | 1 |" in body
        assert "backend/src/a.py:12" in body and "backend/requirements.txt" in body
        assert "GitHub PAT" in body and "x/.env:3" in body and "https://gh/ss/3" in body
        assert "2026-10-02" in body

    def test_unreadable_type_is_not_zero_and_says_so(self) -> None:
        res = _res(ss={"ok": False, "reason": "GITHUB_TOKEN: HTTP 403 Resource not accessible"})
        body = m.build_body(res)
        assert "| Secret scanning | UNREADABLE |" in body and "HTTP 403" in body
        assert "Not readable" in body and "ALERTS_READ_TOKEN" in body
        assert m.decide(res, None, body)["action"] == "create"

    def test_all_zero_closes_open_issue_and_none_creates_nothing(self) -> None:
        res = _res()
        body = m.build_body(res)
        assert m.decide(res, None, body)["action"] == "none"
        assert m.decide(res, {"state": "open", "body": "old"}, body)["action"] == "close"

    def test_nonzero_reopens_closed_issue_and_edits_in_place(self) -> None:
        res = _res(_ok(CS))
        body = m.build_body(res)
        assert m.decide(res, {"state": "closed", "body": "x"}, body)["action"] == "reopen"
        assert m.decide(res, {"state": "open", "body": "x"}, body)["action"] == "edit"
        assert m.decide(res, {"state": "open", "body": body}, body)["action"] == "none"

    def test_pipes_in_values_do_not_break_the_table(self) -> None:
        a = dict(CS, rule={"id": "a|b", "severity": "warning"})
        body = m.build_body(_res(_ok(a)))
        assert "a\\|b" in body and "| medium |" in body

    def test_fetch_falls_back_to_next_token_and_paginates(self) -> None:
        seen = []

        def opener(req, timeout=0):
            seen.append((req.full_url, req.headers["Authorization"]))
            if req.headers["Authorization"] == "Bearer pat":
                raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"message":"nope"}'))
            if "page=2" in req.full_url:
                return _Resp([DB])
            return _Resp([DB], link='<https://api.github.com/x?page=2>; rel="next"')

        r = m.fetch_kind("o/r", "dependabot", [("ALERTS_READ_TOKEN", "pat"), ("GITHUB_TOKEN", "gh")], opener)
        assert r["ok"] and r["token"] == "GITHUB_TOKEN" and len(r["alerts"]) == 2

    def test_fetch_all_tokens_fail_reports_reasons(self) -> None:
        def opener(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"message":"denied"}'))

        r = m.fetch_kind("o/r", "secret_scanning", [("ALERTS_READ_TOKEN", ""), ("GITHUB_TOKEN", "gh")], opener)
        assert not r["ok"] and "GITHUB_TOKEN: HTTP 403 denied" in r["reason"]


if __name__ == "__main__":
    unittest.main()
