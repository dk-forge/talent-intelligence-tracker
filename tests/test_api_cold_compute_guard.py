"""todo#1881 item 4: fast chip clicks gave 500s from /aggregate (review B1).

Pins the guards: the public read routes go through the single-flight + slot
cap guard, a full cap answers 503 + Retry-After, the dashboard retries a 503
once, and refresh() coalesces bursts.
"""
import re
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "wordpress-plugin" / "talent-intelligence-tracker"
API = (PLUGIN / "includes" / "api.php").read_text()
JS = (PLUGIN / "assets" / "dashboard.js").read_text()


def body(src, name):
    m = re.search(r"function %s\([^)]*\)\s*\{" % re.escape(name), src)
    assert m, name
    i, depth = m.end(), 1
    while depth:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.end():i]


class Server(unittest.TestCase):
    def test_routes_use_guarded_callbacks(self):
        self.assertIn("'callback' => 'tit_api_query_guarded'", API)
        self.assertIn("'callback' => 'tit_api_aggregate_guarded'", API)

    def test_guard_validates_before_locking(self):
        for fn in ("tit_api_query_guarded", "tit_api_aggregate_guarded"):
            b = body(API, fn)
            self.assertLess(b.index("tit_validate_filters"), b.index("tit_cold_compute_guard"))

    def test_full_cap_is_a_503_with_retry_after(self):
        g = body(API, "tit_cold_compute_guard")
        for s in ("503", "Retry-After", "no-store", "finally"):
            self.assertIn(s, g)

    def test_no_lock_support_falls_back(self):
        self.assertIn("if ($got === null) return null;", body(API, "tit_named_lock"))


class Client(unittest.TestCase):
    def test_refresh_coalesces(self):
        b = body(JS, "refresh")
        self.assertIn("REFRESH_GAP_MS", b)
        self.assertIn("refreshNow()", b)

    def test_api_fetches_retry_503_once(self):
        self.assertIn("r.status !== 503", body(JS, "titFetch"))
        self.assertNotRegex(JS, r"[^t]fetch\(TIT\.api \+ '(aggregate|query)\?")


if __name__ == "__main__":
    unittest.main()
