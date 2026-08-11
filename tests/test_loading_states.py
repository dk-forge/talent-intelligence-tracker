"""Loading, loaded, failed: the three states every async region has to reach.

WHY THIS FILE EXISTS. The owner read both dashboards on 2026-08-10 and reported
that the page "looks stalled while data loads". Measured against the code, he
was describing something exact. Every one of the three fetches in dashboard.js
ended in a catch that said, in words, "leave the existing rows in place" or
"leave the server-rendered numbers alone". As a fallback that is right. As a
signal it is silence: the previous figures stayed on screen, fully styled,
looking final, and nothing anywhere told a reader the difference between a slow
host and a finished page. Worse, a non-ok response resolved to null and took the
same quiet path as success, so an HTTP 500 and a healthy repaint looked
identical from the outside.

THE THIRD STATE IS THE ONE THAT MATTERS HERE. Both repos have hit the same
defect class repeatedly and have a name for it: a mechanism that looks alive
while doing nothing. An indicator that spins forever is that defect with a
sprite on it. So the assertions below are weighted toward the failure path:
that a rejected fetch lands in a VISIBLE error state, that a request which never
answers is given up on rather than spun over, that the abandoned request is
actually aborted, and that the failed state offers a way back.

AND ONE FALSE POSITIVE IS GUARDED TOO. This dashboard aborts the request a new
filter change replaces, which surfaces as an AbortError. Reporting that as a
failure would put an error in front of a reader for something they themselves
caused by typing quickly. The token check is what keeps those apart, and
test_a_superseded_request_is_not_reported_as_a_failure is what keeps it honest.

HOW IT MATCHES, AND WHY IT IS NOT ALL REGEX. An adversarial sweep across these
two repos found checks passing against defective code because they matched a
COMMENT describing a call rather than the call. Two defences here: every string
assertion runs against source with comments stripped, using style_check.py's own
stripper; and the state machine is not grepped at all. The real bodies of
busyBegin / busyClear / busyFail / busyTrack are lifted off disk and executed in
node against a stub document. The stubs are plumbing (a node with a class list,
a child list and attributes); nothing that decides a state is stubbed.

PROVEN TO FAIL ON THE PRE-FIX TREE. Every test here was run against
origin/main@8a4ae9c, where this work started. All 17 of the original tests
failed there: the four functions do not exist on that tree, so the extraction
raises rather than silently testing nothing, and the wiring and stylesheet
assertions have nothing to match.

The two companion-region tests were added later, against the shipped 1.74.3, and
both failed on it. The one that matters,
test_a_companion_region_cannot_outlive_the_deadline_it_shares, is not
hypothetical: the equivalent region was observed spinning forever on the sibling
tracker's live page after the tiles beside it had correctly reported the
timeout.
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin/talent-intelligence-tracker"
JS_PATH = PLUGIN / "assets/dashboard.js"
CSS_PATH = PLUGIN / "assets/dashboard.css"
TPL_PATH = PLUGIN / "includes/shortcodes.php"

sys.path.insert(0, str(ROOT))
import style_check as sc                                        # noqa: E402

JS_RAW = JS_PATH.read_text(encoding="utf-8")
JS = sc.strip_comments(JS_RAW, "js")
TPL = sc.strip_comments(TPL_PATH.read_text(encoding="utf-8"), "php")
CSS = re.sub(r"/\*.*?\*/", " ", CSS_PATH.read_text(encoding="utf-8"), flags=re.S)

NODE = shutil.which("node")

# The regions a reader watches while a fetch is in flight, and the id each one
# is marked busy under. Adding an async surface without adding it here is the
# regression this list exists to catch.
REGIONS = {
    "tit-fresh-stats": "the headline tiles",
    "tit-zone-insight": "the chart zone",
    "tit-glance": "the at-a-glance board",
    "tit-rows": "the results table",
    "tit-more": "the facet dropdowns",
}

STATE_FNS = ["busyOverlay", "busyBegin", "busyClear", "busyFail", "busyTrack"]


def extract(name):
    """Source text of one `function <name>(` declaration in dashboard.js.

    Brace matched and string aware, so a nested function or a brace inside a
    literal does not truncate it. Raises when the name is absent: a test that
    silently extracted nothing would pass for the worst possible reason.
    """
    src = JS_RAW
    needle = "function %s(" % name
    start = src.find(needle)
    if start == -1:
        raise AssertionError("dashboard.js has no `%s`" % needle)
    if src.find(needle, start + 1) != -1:
        raise AssertionError("`%s` appears more than once in dashboard.js" % needle)
    i = src.index("{", start)
    depth, j = 0, i
    quote = None
    in_line = in_block = False
    while j < len(src):
        c = src[j]
        if in_line:
            if c == "\n":
                in_line = False
        elif in_block:
            if c == "/" and src[j - 1] == "*":
                in_block = False
        elif quote:
            if c == "\\":
                j += 2
                continue
            if c == quote:
                quote = None
        elif src.startswith("//", j):
            in_line = True
        elif src.startswith("/*", j):
            in_block = True
        elif c in "'\"`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
        j += 1
    raise AssertionError("unbalanced braces extracting %s" % name)


# A document stub, and only a document stub. Everything that decides which of
# the three states a region is in comes off disk.
DOM_STUB = r"""
var LOAD_TIMEOUT_MS = 40;      /* the real constant is asserted separately */
var LOAD_MIN_H = 132;
var BUSY = {};
var BUSY_TOKEN = 0;
var REG = {};

function makeEl(tag) {
    var el = { tagName: tag, children: [], attrs: {}, style: {}, parentNode: null,
               hidden: false, offsetHeight: 0, textContent: '', _cls: {} };
    el.classList = {
        add: function (c) { el._cls[c] = 1; },
        remove: function (c) { delete el._cls[c]; },
        contains: function (c) { return !!el._cls[c]; }
    };
    el.setAttribute = function (k, v) { el.attrs[k] = String(v); };
    el.getAttribute = function (k) {
        return Object.prototype.hasOwnProperty.call(el.attrs, k) ? el.attrs[k] : null;
    };
    el.appendChild = function (c) { c.parentNode = el; el.children.push(c); return c; };
    el.removeChild = function (c) {
        var i = el.children.indexOf(c);
        if (i > -1) { el.children.splice(i, 1); c.parentNode = null; }
        return c;
    };
    el.querySelector = function (sel) {
        var want = String(sel).replace(/^\./, '');
        for (var i = 0; i < el.children.length; i++) {
            if (el.children[i]._cls[want]) return el.children[i];
        }
        return null;
    };
    Object.defineProperty(el, 'className', {
        get: function () { return Object.keys(el._cls).join(' '); },
        set: function (v) {
            el._cls = {};
            String(v).split(/\s+/).forEach(function (c) { if (c) el._cls[c] = 1; });
        }
    });
    Object.defineProperty(el, 'innerHTML', {
        get: function () { return ''; },
        set: function (html) {
            el.children = [];
            var re = /<(\w+)([^>]*)>/g, m;
            while ((m = re.exec(html))) {
                var attrs = m[2] || '';
                var child = makeEl(m[1]);
                var cm = /class="([^"]*)"/.exec(attrs);
                if (cm) child.className = cm[1];
                if (/\bhidden\b/.test(attrs.replace(/class="[^"]*"/, ''))) child.hidden = true;
                el.appendChild(child);
            }
        }
    });
    return el;
}

var document = { getElementById: function (id) { return REG[id] || null; },
                 createElement: makeEl };
var window = { requestAnimationFrame: function (f) { setTimeout(f, 0); } };

function region(id, height) {
    var el = makeEl('div');
    el.offsetHeight = height || 0;
    REG[id] = el;
    return el;
}

// What a reader and a screen reader can actually tell about a region.
function snapshot(id) {
    var el = REG[id];
    var ov = null;
    for (var i = 0; i < el.children.length; i++) {
        if (el.children[i]._cls['tit-load']) ov = el.children[i];
    }
    return {
        ariaBusy: el.getAttribute('aria-busy'),
        minHeight: el.style.minHeight || '',
        hostClass: el.classList.contains('tit-load-host'),
        overlay: !ov ? null : {
            role: ov.getAttribute('role'),
            failed: ov.classList.contains('tit-load-failed'),
            message: (ov.querySelector('.tit-load-msg') || {}).textContent,
            spinner: !!ov.querySelector('.tit-load-spin'),
            retryHidden: (ov.querySelector('.tit-load-retry') || {}).hidden
        }
    };
}
"""


def js_states(scenario_body):
    """Run the real state machine in node and return its JSON report."""
    if not NODE:
        raise unittest.SkipTest("node is not installed; cannot execute dashboard.js")
    bodies = "\n".join(extract(n) for n in STATE_FNS)
    script = "%s\n%s\n(async function () {\n%s\n})().then(function (out) {\n  console.log(JSON.stringify(out));\n}, function (e) {\n  console.error(e && e.stack || e); process.exit(1);\n});\n" % (
        DOM_STUB, bodies, scenario_body)
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError("node failed:\n%s" % proc.stderr.strip())
    return json.loads(proc.stdout)


class LoadingStateMachineTests(unittest.TestCase):
    def setUp(self):
        if not NODE:
            self.skipTest("node is not installed; cannot execute dashboard.js")

    def test_the_loading_state_says_so_and_marks_the_region_busy(self):
        out = js_states("""
            region('r', 300);
            var never = new Promise(function () {});
            busyTrack('r', 'Loading the totals', function () { return never; }, null);
            return snapshot('r');
        """)
        self.assertEqual(out["ariaBusy"], "true",
                         "a region whose content is stale must say so to assistive tech")
        self.assertIsNotNone(out["overlay"], "nothing visible said the page was working")
        self.assertEqual(out["overlay"]["role"], "status",
                         "the indicator must be announced, not merely drawn")
        self.assertEqual(out["overlay"]["message"], "Loading the totals")
        self.assertFalse(out["overlay"]["failed"])
        self.assertTrue(out["overlay"]["retryHidden"],
                        "a retry offered while the request is still running invites a double fetch")

    def test_the_loading_state_reserves_the_space_it_is_using(self):
        out = js_states("""
            region('empty', 0); region('full', 480);
            var never = new Promise(function () {});
            busyTrack('empty', 'Loading', function () { return never; }, null);
            busyTrack('full', 'Loading', function () { return never; }, null);
            return { empty: snapshot('empty'), full: snapshot('full') };
        """)
        self.assertEqual(out["empty"]["minHeight"], "132px")
        self.assertEqual(out["full"]["minHeight"], "480px")
        self.assertTrue(out["empty"]["hostClass"] and out["full"]["hostClass"])

    def test_the_loaded_state_removes_the_indicator_and_the_reservation(self):
        out = js_states("""
            region('r', 300);
            await busyTrack('r', 'Loading', function () { return Promise.resolve({ ok: 1 }); }, null);
            await new Promise(function (res) { setTimeout(res, 5); });
            return snapshot('r');
        """)
        self.assertIsNone(out["overlay"], "the indicator outlived the data it was waiting for")
        self.assertEqual(out["ariaBusy"], "false")
        self.assertFalse(out["hostClass"])
        self.assertEqual(out["minHeight"], "",
                         "the reserved height has to be released or the region can never shrink")

    def test_a_failed_fetch_lands_in_a_visible_error_state_with_a_way_out(self):
        out = js_states("""
            region('r', 300);
            var retried = 0;
            try {
                await busyTrack('r', 'Loading', function () {
                    return Promise.reject(new Error('HTTP 500'));
                }, function () { retried += 1; });
            } catch (e) { /* the caller still sees the rejection */ }
            var before = snapshot('r');
            REG['r'].children[0].querySelector('.tit-load-retry').onclick();
            return { before: before, after: snapshot('r'), retried: retried };
        """)
        self.assertIsNotNone(out["before"]["overlay"], "a failed fetch left nothing on screen")
        self.assertTrue(out["before"]["overlay"]["failed"])
        self.assertEqual(out["before"]["overlay"]["message"], "We could not load this data.")
        self.assertEqual(out["before"]["ariaBusy"], "false",
                         "a region that has given up must stop claiming to be working")
        self.assertFalse(out["before"]["overlay"]["retryHidden"],
                         "an error with no retry makes a page reload the only way out")
        self.assertEqual(out["retried"], 1)
        self.assertIsNone(out["after"]["overlay"],
                          "retrying has to clear the error it is retrying")

    def test_a_request_that_never_answers_is_given_up_on_and_aborted(self):
        out = js_states("""
            region('r', 300);
            var seen = null, retried = 0;
            busyTrack('r', 'Loading', function (signal) {
                seen = signal;
                return new Promise(function () {});
            }, function () { retried += 1; });
            await new Promise(function (res) { setTimeout(res, 120); });
            var snap = snapshot('r');
            REG['r'].children[0].querySelector('.tit-load-retry').onclick();
            return { snap: snap, aborted: !!(seen && seen.aborted), retried: retried,
                     gotSignal: !!seen };
        """)
        self.assertTrue(out["gotSignal"], "no signal reached the request, so nothing could cancel it")
        self.assertIsNotNone(out["snap"]["overlay"])
        self.assertTrue(out["snap"]["overlay"]["failed"],
                        "the indicator was still in its loading state after the deadline")
        self.assertEqual(out["snap"]["overlay"]["message"], "This is taking longer than usual.")
        self.assertEqual(out["snap"]["ariaBusy"], "false")
        self.assertTrue(out["aborted"], "the abandoned request was left in flight")
        self.assertEqual(out["retried"], 1)

    def test_a_companion_region_cannot_outlive_the_deadline_it_shares(self):
        # MEASURED ON THE SIBLING TRACKER'S LIVE PAGE, not reasoned about. The
        # charts and the board are painted by the same /aggregate call as the
        # tiles, and the first shipped version moved them from that call's
        # then/catch. A promise that neither resolves nor rejects reaches
        # neither, so the tiles reported the timeout honestly and the other two
        # spun underneath them forever, which is the defect this whole file
        # exists to prevent, reintroduced by the fix for it. `make` here
        # deliberately ignores the abort signal, because a companion whose only
        # exit is the tracked promise settling has no deadline at all.
        out = js_states("""
            region('lead', 200); region('mate', 200);
            busyTrack('lead', 'Loading the totals', function () {
                return new Promise(function () {});
            }, function () {}, [['mate', 'Loading the charts']]);
            var begun = snapshot('mate');
            await new Promise(function (res) { setTimeout(res, 120); });
            return { begun: begun, lead: snapshot('lead'), mate: snapshot('mate') };
        """)
        self.assertIsNotNone(out["begun"]["overlay"],
                             "the companion region never entered the loading state")
        self.assertEqual(out["begun"]["overlay"]["message"], "Loading the charts")
        self.assertTrue(out["lead"]["overlay"]["failed"])
        self.assertIsNotNone(out["mate"]["overlay"])
        self.assertTrue(out["mate"]["overlay"]["failed"],
                        "the companion was still spinning after the deadline the "
                        "request it shares had already given up on")
        self.assertEqual(out["mate"]["ariaBusy"], "false")
        self.assertFalse(out["mate"]["overlay"]["retryHidden"])

    def test_a_companion_region_clears_with_the_request_it_shares(self):
        # The busy snapshot is taken BEFORE the await on purpose. Without it a
        # tree that ignores the companions argument entirely would pass for the
        # worst possible reason: a region that was never marked busy is, at the
        # end, indistinguishable from one that was cleared.
        out = js_states("""
            region('lead', 200); region('mate', 200);
            var p = busyTrack('lead', 'Loading', function () { return Promise.resolve(1); },
                              null, [['mate', 'Loading the charts']]);
            var busy = snapshot('mate');
            await p;
            await new Promise(function (res) { setTimeout(res, 5); });
            return { busy: busy, lead: snapshot('lead'), mate: snapshot('mate') };
        """)
        self.assertIsNotNone(out["busy"]["overlay"],
                             "the companion region never entered the loading state")
        self.assertEqual(out["busy"]["ariaBusy"], "true")
        self.assertIsNone(out["lead"]["overlay"])
        self.assertIsNone(out["mate"]["overlay"],
                          "the companion outlived the data it was waiting for")
        self.assertEqual(out["mate"]["minHeight"], "")

    def test_a_late_answer_cannot_resurrect_a_region_that_already_failed(self):
        out = js_states("""
            region('r', 300);
            var settle;
            var p = busyTrack('r', 'Loading', function () {
                return new Promise(function (res) { settle = res; });
            }, function () {});
            p.then(function () {}, function () {});
            await new Promise(function (res) { setTimeout(res, 120); });
            settle({ late: true });
            await new Promise(function (res) { setTimeout(res, 20); });
            return snapshot('r');
        """)
        self.assertIsNotNone(out["overlay"])
        self.assertTrue(out["overlay"]["failed"])

    def test_a_superseded_request_is_not_reported_as_a_failure(self):
        # A reader typing quickly replaces their own request. busyBegin aborts
        # the one it replaces, and the rejection that produces must not put an
        # error in front of them for something they caused themselves.
        out = js_states("""
            region('r', 300);
            var firstSignal = null;
            busyTrack('r', 'Loading the records', function (sig) {
                firstSignal = sig;
                return new Promise(function (res, rej) {
                    sig.addEventListener('abort', function () {
                        var e = new Error('aborted'); e.name = 'AbortError'; rej(e);
                    });
                });
            }, function () {}).catch(function () {});
            busyTrack('r', 'Loading the records', function () {
                return new Promise(function () {});
            }, function () {}).catch(function () {});
            await new Promise(function (res) { setTimeout(res, 10); });
            return { snap: snapshot('r'), firstAborted: !!(firstSignal && firstSignal.aborted) };
        """)
        self.assertTrue(out["firstAborted"],
                        "the replaced request was left running against a page that moved on")
        self.assertIsNotNone(out["snap"]["overlay"])
        self.assertFalse(out["snap"]["overlay"]["failed"],
                         "a request the reader replaced was reported to them as an error")
        self.assertEqual(out["snap"]["ariaBusy"], "true")

    def test_two_overlapping_begins_do_not_stack_two_indicators(self):
        out = js_states("""
            region('r', 300);
            var never = new Promise(function () {});
            busyTrack('r', 'Loading the records', function () { return never; }, null);
            busyTrack('r', 'Loading the records', function () { return never; }, null);
            var n = 0;
            REG['r'].children.forEach(function (c) { if (c._cls['tit-load']) n += 1; });
            return { overlays: n, minHeight: REG['r'].style.minHeight };
        """)
        self.assertEqual(out["overlays"], 1)
        self.assertEqual(out["minHeight"], "300px",
                         "a second begin re-measured a region it had already covered")


class WiringTests(unittest.TestCase):
    def test_every_async_region_is_marked_busy_by_name(self):
        for rid, what in REGIONS.items():
            # Either tracked directly, or riding along as a companion pair,
            # which is the ['id', 'Loading ...'] literal busyTrack takes. A
            # companion is NOT weaker wiring: busyTrack begins, clears and fails
            # it on the same deadline as the region it accompanies, which is the
            # whole reason those pairs live there and not in a caller's catch.
            self.assertRegex(
                JS,
                r"(busy(?:Track|Begin)\(\s*(?:'%s'|AGG_REGIONS))|(\[\s*'%s'\s*,\s*'Loading)"
                % (re.escape(rid), re.escape(rid)),
                "%s (#%s) starts a fetch with no loading state" % (what, rid))

    def test_every_async_region_exists_in_the_markup(self):
        for rid, what in REGIONS.items():
            self.assertIn('id="%s"' % rid, TPL,
                          "%s is marked busy under an id the page never renders" % what)

    def test_the_deadline_is_a_real_finite_number_in_the_shipped_source(self):
        m = re.search(r"var LOAD_TIMEOUT_MS\s*=\s*(\d+);", JS)
        self.assertTrue(m, "there is no deadline, so an unanswered fetch spins forever")
        ms = int(m.group(1))
        self.assertGreater(ms, 2000, "a deadline this short would fail healthy slow loads")
        self.assertLessEqual(ms, 30000, "a reader will not wait past half a minute for a verdict")

    def test_a_non_ok_response_is_a_failure_and_not_a_quiet_null(self):
        # It used to resolve to null on any status and take the success path.
        self.assertNotRegex(JS, r"return r\.ok \? r\.json\(\) : null;\s*\}\);\s*\n\s*\.then")
        self.assertGreaterEqual(len(re.findall(r"throw new Error\('HTTP ' \+ r\.status\)", JS)), 3,
                                "at least the three data fetches must treat a bad status as one")

    def test_the_filter_path_is_covered_and_not_only_the_first_paint(self):
        # "Stalled" is felt hardest on a filter change, so the function a filter
        # change calls, and the aggregate it triggers, both carry the state.
        for fn in ("refresh", "refreshAggregate"):
            body = sc.strip_comments(extract(fn), "js")
            self.assertIn("busy", body,
                          "%s runs on every filter change with no loading state" % fn)


class PresentationTests(unittest.TestCase):
    def _rule(self, selector):
        i = CSS.find(selector)
        self.assertNotEqual(i, -1, "dashboard.css has no rule %r" % selector)
        j = CSS.index("{", i)
        depth, k = 0, j
        while k < len(CSS):
            if CSS[k] == "{":
                depth += 1
            elif CSS[k] == "}":
                depth -= 1
                if depth == 0:
                    return CSS[j + 1:k]
            k += 1
        raise AssertionError("unbalanced braces after %r" % selector)

    def test_the_overlay_takes_no_flow_space(self):
        body = self._rule(".tit-load {")
        self.assertRegex(body, r"position:\s*absolute",
                         "an in-flow indicator moves the content it is announcing")
        self.assertRegex(body, r"inset:\s*0")

    def test_reduced_motion_gets_a_state_and_not_an_animation(self):
        blocks = re.findall(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\n\}",
                            CSS, flags=re.S)
        self.assertTrue(any(".tit-load-spin" in b and re.search(r"animation:\s*none", b)
                            for b in blocks),
                        "the indicator keeps spinning for a reader who asked it not to")
        self.assertIn("tit-load-msg", JS)

    def test_the_scrim_is_defined_in_all_three_theme_blocks(self):
        self.assertEqual(len(re.findall(r"--tit-load-scrim\s*:", CSS)), 3)

    def test_the_failed_state_is_styled_as_a_state_not_as_a_spinner(self):
        self.assertRegex(CSS, r"\.tit-load-failed\s+\.tit-load-spin\s*\{[^}]*display:\s*none")


if __name__ == "__main__":
    unittest.main()
