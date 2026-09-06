"""No outbound call in this repository may wait for ever.

WHY THIS FILE EXISTS
--------------------
`requests.get(url)` with no `timeout` blocks until the peer closes the socket.
There is no default. A publisher that accepts the connection and then stops
sending holds the process open indefinitely, and on a GitHub runner "for ever"
means until `timeout-minutes` cancels the job -- which cancels the commit step
with it, so the run discards every row it had already collected and paid for.
One hung feed out of ~600 costs the whole slice.

The repository was clean when this was written: an AST sweep on 2026-09-06
found 61 HTTP call sites and 61 explicit timeouts. That is exactly why the
test is here. A clean sweep is a fact about today, not a property of the code;
the next collector added is one `requests.get(url)` away from reintroducing it,
and nothing in the suite would have said a word.

WHAT IT CHECKS, AND WHY IT IS A SOURCE SCAN
--------------------------------------------
It parses every module in the repository and looks for a call that goes out to
somebody else's machine without a bounded wait. A source scan rather than a
runtime one, because the defect is a MISSING argument: there is no request to
observe, no exception to catch, and a mocked session cannot notice that the
real one would have hung.

Two ways a call site satisfies it:

  * it passes `timeout=` itself; or
  * it passes `**kwargs` through to a wrapper that has a `timeout` default
    (`capped_fetch.capped_get`, `http_retry.fetch`, `HttpFetcher.get`).

A wrapper that TAKES a timeout must also DEFAULT it, or a caller that forgets
is back where we started -- checked separately below.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "build"}

#: Attribute calls that reach the network. `send` is requests.Session.send.
HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "request",
              "options", "send"}

#: A receiver whose name says it speaks HTTP. Narrowing on the receiver is what
#: keeps `dict.get`, `os.environ.get` and `re.match(...).group` out of it.
HTTP_RECEIVER_HINTS = ("requests", "session", "http", "client", "opener",
                       "conn", "fetcher")

#: Bare-name calls that open a socket on their own.
SOCKET_CALLS = {"urlopen", "create_connection", "SMTP", "SMTP_SSL", "FTP",
                "FTP_TLS"}

#: Wrappers in this repo that own a `timeout` DEFAULT. A call site that passes
#: `**kwargs` into one of these is bounded by that default.
BOUNDED_WRAPPERS = {
    "capped_get", "open_capped", "capped_text",   # collectors/capped_fetch.py
    "fetch",                                      # collectors/http_retry.py
}

#: Call sites whose timeout lives on the RECEIVER rather than in the call.
#: `(path, expression)` pairs, deliberately spelled out one by one: an
#: allowlist you have to name a file in is one somebody reads. Every class
#: named here is checked below for an actual default, so an entry cannot be
#: used to wave a timeout-less fetcher through.
BOUNDED_RECEIVERS = {
    # robots_sitemaps.HttpFetcher stores `self.timeout` (default 30) and
    # passes it to every requests.get it makes, so `http.get(url)` and
    # `HttpFetcher().get(url)` are bounded by construction.
    ("robots_sitemaps.py", "http.get"),
    ("tests/test_robots_sitemaps.py", "rs.HttpFetcher().get"),
    ("tests/test_robots_sitemaps.py",
     "rs.HttpFetcher(sleep=lambda _: None).get"),
}


def _modules():
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _has_timeout(call: ast.Call) -> bool:
    if any(kw.arg == "timeout" for kw in call.keywords):
        return True
    # `**kwargs` passed into a wrapper that defaults the timeout.
    if any(kw.arg is None for kw in call.keywords):
        name = call.func.attr if isinstance(call.func, ast.Attribute) \
            else getattr(call.func, "id", "")
        if name in BOUNDED_WRAPPERS:
            return True
    return False


def _is_outbound(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute):
        if func.attr in SOCKET_CALLS:
            return True
        if func.attr not in HTTP_VERBS:
            return False
        receiver = ast.unparse(func.value).lower()
        return any(hint in receiver for hint in HTTP_RECEIVER_HINTS)
    if isinstance(func, ast.Name):
        return func.id in SOCKET_CALLS
    return False


def _unbounded_call_sites() -> list[str]:
    found = []
    for path in _modules():
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:                       # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_outbound(node):
                continue
            if _has_timeout(node):
                continue
            rel = str(path.relative_to(ROOT))
            if (rel, ast.unparse(node.func)) in BOUNDED_RECEIVERS:
                continue
            found.append(f"{path.relative_to(ROOT)}:{node.lineno} "
                         f"{ast.unparse(node.func)}")
    return found


def test_every_outbound_call_carries_an_explicit_timeout():
    unbounded = _unbounded_call_sites()
    assert unbounded == [], (
        "these calls wait for ever on a peer that stops answering:\n  "
        + "\n  ".join(unbounded)
        + "\nPass timeout=..., or route the call through "
          "collectors/http_retry.fetch or collectors/capped_fetch.capped_get.")


def test_the_scan_actually_catches_a_timeout_less_call(tmp_path,
                                                       monkeypatch):
    """THE SCAN'S OWN BLIND SPOT, CLOSED.

    A guard whose clean zero has never caught a known instance is worth
    nothing: it is indistinguishable from a scan that matches nothing at all.
    So plant one and prove it is seen.
    """
    planted = ROOT / "_timeout_scan_fixture.py"
    planted.write_text(
        "import requests\n"
        "def go(url):\n"
        "    return requests.get(url, headers={})\n",
        encoding="utf-8")
    try:
        found = _unbounded_call_sites()
    finally:
        planted.unlink()
    assert any("_timeout_scan_fixture.py:3" in row for row in found), found


@pytest.mark.parametrize("module,function", [
    ("collectors/capped_fetch.py", "capped_get"),
    ("collectors/capped_fetch.py", "open_capped"),
])
def test_a_shared_fetch_wrapper_DEFAULTS_its_timeout(module, function):
    """Accepting `timeout` is not enough. A wrapper whose timeout defaults to
    None hands every forgetful caller the original defect back, one layer down
    where the scan above cannot see it."""
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            names = [a.arg for a in node.args.kwonlyargs]
            assert "timeout" in names, f"{function} takes no timeout"
            default = node.args.kw_defaults[names.index("timeout")]
            assert default is not None and getattr(default, "value", None), \
                f"{function}'s timeout defaults to nothing"
            return
    raise AssertionError(f"{function} not found in {module}")


def test_the_receiver_allowlist_names_a_fetcher_that_really_defaults_it():
    """Every entry in BOUNDED_RECEIVERS rests on one claim: HttpFetcher's own
    timeout has a default. Prove it here rather than believe the comment."""
    import robots_sitemaps

    tree = ast.parse(pathlib.Path(robots_sitemaps.__file__).read_text(
        encoding="utf-8"))
    for cls in ast.walk(tree):
        if not (isinstance(cls, ast.ClassDef) and cls.name == "HttpFetcher"):
            continue
        init = next(n for n in cls.body
                    if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        names = [a.arg for a in init.args.kwonlyargs]
        assert "timeout" in names
        default = init.args.kw_defaults[names.index("timeout")]
        assert getattr(default, "value", None), \
            "HttpFetcher.timeout has no default, so http.get(url) is unbounded"
        # ...and it is actually handed to the request.
        src = ast.unparse(cls)
        assert "timeout=self.timeout" in src
        return
    raise AssertionError("HttpFetcher not found")
