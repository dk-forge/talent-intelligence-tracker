"""One way for a test module to satisfy `import requests`, and only one.

`pipeline/publish.py` and several collectors touch `requests` at IMPORT time
(`class DomainDrift(requests.RequestException)`, `session: requests.Session`),
so a test file that imports them needs the name to exist. On the machine that
runs the unittest-only files, requests is not installed — hence the stub that
three test modules used to install for themselves:

    sys.modules.setdefault("requests", types.ModuleType("requests"))

`setdefault` looks scoped and is not. It is a no-op only when something else
already imported the real library, which in a FULL suite run is always true and
in a SUBSET run depends entirely on which files pytest was given. Run
`tests/test_identity.py` alongside `tests/test_city_country_correction.py` and
nothing else, and the bare ModuleType wins the race: every later import of
`collectors/national_press.py` dies on `module 'requests' has no attribute
'RequestException'`. Thirty-five tests failed that way while `pytest -q` was
green — exactly the shadowing CLAUDE.md warns about under "Test gotcha", just
arriving from the other direction.

So: prefer the REAL library, always, and fall back to a stub only when it is
genuinely absent. The stub then carries the attributes that are read at import
time, because a fake that fails later is worse than no fake at all.

`tests/test_no_module_stubs.py` is the guard that keeps the old one-liner from
coming back.
"""

from __future__ import annotations

import importlib
import sys
import types


def _build_stub() -> types.ModuleType:
    """A requests-shaped module with the names that are touched at import time.

    Anything that would make a network call is deliberately absent: a test that
    reaches for one should fail loudly on AttributeError rather than quietly
    receive a Mock and pretend it talked to a server.
    """
    stub = types.ModuleType("requests")

    class RequestException(Exception):
        """Mirrors requests.RequestException; subclassed by collectors."""

    class ConnectionError(RequestException):
        pass

    class HTTPError(RequestException):
        pass

    class Timeout(RequestException):
        pass

    class TooManyRedirects(RequestException):
        pass

    class Session:
        """Present so annotations resolve. Using one is an error, not a no-op."""

        def __init__(self, *a, **k):
            raise RuntimeError(
                "the requests stub cannot make a request — the real library is "
                "not installed in this environment"
            )

    exceptions = types.ModuleType("requests.exceptions")
    for _exc in (RequestException, ConnectionError, HTTPError, Timeout,
                 TooManyRedirects):
        setattr(stub, _exc.__name__, _exc)
        setattr(exceptions, _exc.__name__, _exc)

    stub.exceptions = exceptions
    stub.Session = Session
    stub.__stub__ = True
    sys.modules["requests.exceptions"] = exceptions
    return stub


def ensure_requests() -> types.ModuleType:
    """Return the real `requests` if it can be imported; stub it if it cannot.

    Idempotent, and it NEVER replaces a module that is already loaded.
    """
    existing = sys.modules.get("requests")
    if existing is not None:
        return existing
    try:
        return importlib.import_module("requests")
    except ImportError:
        stub = _build_stub()
        sys.modules["requests"] = stub
        return stub
