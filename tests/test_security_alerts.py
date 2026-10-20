"""Offline tests for security_alerts.py: no network, a fake opener only."""
import datetime as dt
import io
import json
import os
import tempfile
import unittest
import urllib.error

import security_alerts as sa

NOW = dt.datetime(2026, 10, 20, 12, 0, tzinfo=dt.timezone.utc)


class _Resp(io.BytesIO):
    def __init__(self, body, link=None):
        super().__init__(json.dumps(body).encode())
        self.headers = {"Link": link} if link else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _dep(n, sev, fixed="1.2.3"):
    return {"number": n,
            "dependency": {"package": {"name": f"pkg{n}", "ecosystem": "pip"},
                           "manifest_path": "requirements.lock"},
            "security_vulnerability": {"severity": sev,
                                       "first_patched_version": {"identifier": fixed}},
            "security_advisory": {"ghsa_id": f"GHSA-{n}"}}


def _opener(routes):
    def op(req, timeout=None):
        for frag, resp in routes.items():
            if frag in req.full_url:
                if isinstance(resp, Exception):
                    raise resp
                return resp() if callable(resp) else resp
        raise AssertionError(req.full_url)
    return op


def _http(code):
    return urllib.error.HTTPError("u", code, "x", {}, None)


class SecurityAlertsTest(unittest.TestCase):
    def test_paginates_and_counts_by_severity(self):
        pages = iter([_Resp([_dep(2, "high")], link='<https://api.github.com/next?p=2>; rel="next"'),
                      _Resp([_dep(1, "critical"), _dep(3, "low", None)])])
        op = _opener({"dependabot": lambda: next(pages), "next": lambda: next(pages),
                      "code-scanning": _Resp([])})
        snap = sa.build("o/r", env={"ALERTS_READ_TOKEN": "t", "GITHUB_TOKEN": "g"}, opener=op)
        d = snap["dependabot"]
        assert d["state"] == "OK" and d["total"] == 3
        assert d["counts"] == {"critical": 1, "high": 1, "medium": 0, "low": 1}
        assert [a["id"] for a in d["alerts"]] == [1, 2, 3]
        assert d["alerts"][0]["fixed_in"] == "1.2.3" and d["alerts"][2]["fixed_in"] is None
        assert snap["code_scanning"]["total"] == 0

    def test_missing_token_and_403_404_are_unknown_never_zero(self):
        op = _opener({"dependabot": _http(403), "code-scanning": _http(404)})
        snap = sa.build("o/r", env={"ALERTS_READ_TOKEN": "t", "GITHUB_TOKEN": "g"}, opener=op)
        for key in ("dependabot", "code_scanning"):
            assert snap[key]["state"] == "UNKNOWN" and snap[key]["total"] is None
        snap = sa.build("o/r", env={}, opener=_opener({}))
        assert "ALERTS_READ_TOKEN" in snap["dependabot"]["reason"]
        assert snap["dependabot"]["total"] is None

    def test_unchanged_snapshot_is_not_rewritten_same_day(self):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        snap = {"schema": 1, "dependabot": sa._unknown("x"), "code_scanning": sa._unknown("y")}
        assert sa.write_snapshot(p, snap, now=NOW) is True
        assert sa.write_snapshot(p, snap, now=NOW + dt.timedelta(hours=3)) is False
        assert sa.write_snapshot(p, snap, now=NOW + dt.timedelta(hours=25)) is True

    def test_summarize_absent_stale_unknown_and_alerts(self):
        assert sa.summarize(None, now=NOW)[0] == "UNKNOWN"
        ok = {"checked_at": "2026-10-20T06:00:00Z",
              "dependabot": {"state": "OK", "total": 1, "counts": {"high": 1},
                             "alerts": [{"id": 7, "severity": "high", "package": "x", "fixed_in": "2"}]},
              "code_scanning": {"state": "OK", "total": 0, "counts": {}, "alerts": []}}
        verdict, lines = sa.summarize(ok, now=NOW)
        assert verdict == "ALERTS" and any("#7 high x -> fixed in 2" in l for l in lines)
        stale = dict(ok, checked_at="2026-10-17T06:00:00Z")
        assert sa.summarize(stale, now=NOW)[0] == "UNKNOWN"
        half = dict(ok, code_scanning={"state": "UNKNOWN", "reason": "HTTP 403"})
        assert sa.summarize(half, now=NOW)[0] == "ALERTS"
        half_clean = dict(half, dependabot={"state": "OK", "total": 0, "counts": {}, "alerts": []})
        assert sa.summarize(half_clean, now=NOW)[0] == "UNKNOWN"
        clean = dict(ok, dependabot={"state": "OK", "total": 0, "counts": {}, "alerts": []})
        assert sa.summarize(clean, now=NOW)[0] == "OK"


if __name__ == "__main__":
    unittest.main()
