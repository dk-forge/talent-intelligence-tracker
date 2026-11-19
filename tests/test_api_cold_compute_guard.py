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
        self.assertIn("finally", g)
        b = body(API, "tit_busy_response")
        for s in ("503", "Retry-After", "no-store"):
            self.assertIn(s, b)

    def test_key_lock_timeout_is_busy_not_the_slot_loop(self):
        g = body(API, "tit_cold_compute_guard")
        self.assertLess(g.index("if ($key_lock === false) return tit_busy_response();"),
                        g.index("for ($i = 0;"))

    def test_only_the_visitors_own_rest_call_is_capped(self):
        for fn in ("tit_api_query_guarded", "tit_api_aggregate_guarded"):
            self.assertIn("tit_is_outer_rest_call($req)", body(API, fn))
        g = body(API, "tit_cold_compute_guard")
        internal = g[g.index("if (!$outer) {"):g.index("$key_lock =")]
        self.assertNotIn("busy_response", internal)
        self.assertIn("tit_named_lock('k|'", internal)

    def test_waiters_drop_request_local_cache_before_rereading(self):
        g = body(API, "tit_cold_compute_guard")
        lock = g.index("tit_named_lock('k|'")
        forget = g.index("tit_forget_transient_cache(")
        reread = g.index("get_transient", lock)
        self.assertLess(lock, forget)
        self.assertLess(forget, reread)
        f = body(API, "tit_forget_transient_cache")
        self.assertIn("'notoptions'", f)
        # A persistent object cache holds the SHARED value: never delete it.
        # A persistent cache's value is re-fetched, never deleted.
        self.assertIn("wp_cache_get($key, 'transient', true)", f)
        self.assertNotIn("wp_cache_delete($key", f)

    def test_no_lock_support_falls_back(self):
        self.assertIn("if ($got === null) return null;", body(API, "tit_named_lock"))


class SecondReview(unittest.TestCase):
    def test_a_hit_takes_no_lock(self):
        g = body(API, "tit_cold_compute_guard")
        self.assertLess(g.index("get_transient($key) !== false) return call_user_func($run);"),
                        g.index("tit_named_lock("))
        self.assertIn("$key_lock = tit_named_lock('k|' . $key, 2);", g)

    def test_four_requests_per_filter_change_fit_in_the_cap(self):
        self.assertIn("define('TIT_COLD_SLOTS', 10)", API)

    def test_the_caps_503_and_the_db_error_page_are_retried(self):
        self.assertIn("titRetryWait(r, left)", body(JS, "titFetch"))
        self.assertIn("r.status === 503 && r.headers.get('Retry-After')", body(JS, "titRetryWait"))
        w = body(JS, "titRetryWait")
        self.assertIn("TIT_DB_ERR.test(t)", w)  # only the DB-connection page, not plugin fatals
        self.assertIn("left <= 1) return Promise.resolve(0);", w)  # at most two host retries
        self.assertIn("Math.random()", w)  # jitter

    def test_geo_table_supersedes_and_drops_stale_answers(self):
        b = body(JS, "refreshGeo")
        self.assertIn("geoCtrl.abort()", b)
        self.assertEqual(b.count("if (seq !== geoSeq) return;"), 2)

    def test_a_burst_retires_requests_already_in_flight(self):
        b = body(JS, "refresh")
        self.assertIn("busyBegin('tit-rows'", b)
        self.assertIn("geoSeq++", b)


class Client(unittest.TestCase):
    def test_refresh_coalesces(self):
        b = body(JS, "refresh")
        self.assertIn("REFRESH_GAP_MS", b)
        self.assertIn("refreshNow()", b)

    def test_controls_follow_every_click_inside_a_burst(self):
        b = body(JS, "refresh")
        self.assertLess(b.index("refreshNow(true)"), b.index("setTimeout"))
        n = body(JS, "refreshNow")
        self.assertLess(n.index("if (stateOnly === true) return;"), n.index("refreshAggregate(params)"))

    def test_api_fetches_go_through_the_retrying_fetch(self):
        self.assertIn("return titFetch(url, opts, left - 1);", body(JS, "titFetch"))
        self.assertNotRegex(JS, r"[^t]fetch\(TIT\.api \+ '(aggregate|query)\?")


if __name__ == "__main__":
    unittest.main()
