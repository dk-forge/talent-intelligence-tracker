"""THE OFFLINE TEST SUITE MUST NOT USE THE PRODUCTION WEBSITE AS TEST DATA.

Written on 2026-09-13, during an outage. asktherecruiter.com/blog and the
sibling layoff tracker sit on one ChemiCloud shared account, and on 2026-09-12
and again on 2026-09-13 every PHP request on that account timed out at 10s,
account-wide, twice inside twelve hours. It was LOAD, not a code fault. One
measurable contributor was our own test suites: this repository runs its whole
pytest suite on `cron: '43 * * * *'` -- hourly, forever -- plus on every push
and every pull request, and that suite was making 15 HTTP requests to the live
public site every time it ran.

HOW 15 REQUESTS HID IN A SUITE NOBODY THOUGHT WAS ONLINE. Three tests in
tests/test_rejection_audit_surfaced.py spawn `ops_status.py` as a SUBPROCESS,
because what they assert is what the dashboard PRINTS. `ops_status.main()` calls
`_report_published_figures()`, which calls `published_figures.check_all()`,
which is five read-only GETs against production. Three subprocesses times five
reads is fifteen. Not one frame of the stack was a test frame, so no amount of
reading the test file showed it, and grepping the tests for the live hostname
found nothing, because the hostname is in published_figures.py.

THE GUARD, AND WHY IT IS SHAPED LIKE THIS. Asserting on source text would not
have caught this one: no test file mentions the host, the module or the URL. So
this runs the real subprocess with the real suite environment under a
`sitecustomize.py` that refuses and RECORDS every non-local socket, and reads
the recording.

AN EMPTY LOG IS A NOREAD, NOT A ZERO. A blocker that silently failed to install
produces exactly the same empty file as a clean run, and that file would be read
as a pass forever. So the positive control below runs FIRST in this module and
fetches a deliberately unresolvable host -- never the real one, which is the
whole point of the exercise -- and the zero is only trusted after the probe has
been seen to catch something.
"""

import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
OPS = ROOT / "ops_status.py"
CONFTEST = pathlib.Path(__file__).resolve().parent / "conftest.py"

#: Deliberately unresolvable, and deliberately NOT the production host. The
#: control has to prove the probe records a request without making one.
CONTROL_HOST = "probe.invalid.example"

LOCALHOSTS = ("localhost", "127.0.0.1", "::1", "", "None")

PROBE = '''
import os, socket, sys, traceback
_LOG = os.environ["NETPROBE_LOG"]
_LOCAL = ("localhost", "127.0.0.1", "::1", "", None)

def _record(host, port):
    if host in _LOCAL:
        return
    with open(_LOG, "a") as fh:
        fh.write("HOST\\t%s\\tPORT\\t%s\\tARGV\\t%r\\n" % (host, port, sys.argv))
        fh.write("".join(traceback.format_stack()[:-2]))
        fh.write("---END---\\n")

def getaddrinfo(host, port, *a, **k):
    _record(host, port)
    raise OSError("NETPROBE: blocked DNS for %r" % (host,))
socket.getaddrinfo = getaddrinfo

_connect = socket.socket.connect
def connect(self, addr):
    try:
        host, port = addr[0], addr[1]
    except Exception:
        host, port = addr, None
    _record(host, port)
    raise OSError("NETPROBE: blocked connect to %r" % (addr,))
socket.socket.connect = connect
'''


def _probe_dir(tmp):
    d = pathlib.Path(tmp) / "netprobe"
    d.mkdir(parents=True, exist_ok=True)
    # sitecustomize, NOT usercustomize: usercustomize is not loaded under this
    # repository's .venv, and a probe that never loads is the NOREAD above.
    (d / "sitecustomize.py").write_text(PROBE)
    return d


def _run_under_probe(argv, tmp, extra_env=None):
    """Run argv in a child whose every outbound socket is recorded and refused."""
    d = _probe_dir(tmp)
    log = pathlib.Path(tmp) / "netprobe.log"
    env = dict(os.environ)
    # The suite's own environment is inherited deliberately: what is under test
    # is what a child of THIS process does, and tests/conftest.py is what makes
    # that child offline.
    env["PYTHONPATH"] = os.pathsep.join([str(d), str(ROOT), str(ROOT / "tests")])
    env["NETPROBE_LOG"] = str(log)
    env.update(extra_env or {})
    proc = subprocess.run(argv, cwd=str(ROOT), env=env, capture_output=True,
                          text=True, timeout=600)
    recorded = []
    if log.exists():
        for line in log.read_text().splitlines():
            if line.startswith("HOST\t"):
                recorded.append(line.split("\t")[1])
    return proc, recorded, (log.read_text() if log.exists() else "")


class ProbeIsLive(unittest.TestCase):
    """The positive control. Everything below is worthless without it."""

    def test_the_probe_records_a_request_that_is_actually_made(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _, hosts, _ = _run_under_probe(
                [sys.executable, "-c",
                 "import urllib.request\n"
                 "try:\n"
                 f"    urllib.request.urlopen('https://{CONTROL_HOST}/', timeout=2)\n"
                 "except Exception:\n"
                 "    pass\n"],
                tmp)
        self.assertIn(CONTROL_HOST, hosts,
                      "the network probe recorded nothing for a request that "
                      "was definitely made, so every zero it reports below is "
                      "a NOREAD and not a zero. Check that sitecustomize.py "
                      "landed on PYTHONPATH")


class TheSuiteIsOffline(unittest.TestCase):
    def test_ops_status_as_a_test_subprocess_reaches_no_host(self):
        """The exact 15 requests, at their exact source.

        Three tests spawn this same command. If it is silent, they are.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            proc, hosts, dump = _run_under_probe([sys.executable, str(OPS)], tmp)
        offenders = [h for h in hosts if h not in LOCALHOSTS]
        self.assertEqual(
            offenders, [],
            "running ops_status.py the way tests/test_rejection_audit_surfaced.py "
            "runs it reached " + ", ".join(sorted(set(offenders))) + ". The "
            "offline suite is using a live website as test data again, hourly, "
            "on a shared host that has already fallen over from it twice. "
            "Stack:\n" + dump[:4000])

    def test_the_subprocess_still_prints_the_section_the_tests_read(self):
        """Offline must not be bought by breaking the thing being tested.

        A fix that made ops_status.py fall over without a network would turn
        three green tests red somewhere else, and the next session would answer
        it by putting the network back.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            proc, _, _ = _run_under_probe([sys.executable, str(OPS)], tmp)
        self.assertIn("[3c]", proc.stdout,
                      "ops_status.py no longer prints the rejection-audit "
                      "section when it cannot reach the network:\n"
                      + proc.stderr[-3000:])

    def test_an_unchecked_figure_says_unknown_out_loud(self):
        """UNKNOWN in those words, in the output a human reads.

        This is the half of the fix that is not about the network. Turning a
        live check off is only acceptable while the run says, loudly, that it
        did not run it. A silent skip would leave a green ops_status implying
        five published figures had been verified against production when
        nothing had been asked.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            proc, _, _ = _run_under_probe([sys.executable, str(OPS)], tmp)
        block = proc.stdout[proc.stdout.index("[PUBLISHED FIGURES]"):]
        block = block[:block.index("\n[")] if "\n[" in block[4:] else block
        self.assertIn("UNKNOWN", block)
        self.assertIn("NOT checked, NOT passing", block)
        self.assertIn("LIVE READS ARE OFF", block,
                      "the reason the figures were not checked has to be in "
                      "the output, or the next session reads five UNKNOWNs as "
                      "an egress problem and goes looking for one")


class TheSwitchItself(unittest.TestCase):
    def test_conftest_turns_live_figure_reads_off_for_the_whole_suite(self):
        src = CONFTEST.read_text()
        self.assertIn("TIT_LIVE_FIGURES", src,
                      "tests/conftest.py is where this suite states that it is "
                      "offline. Two other network seams are already switched "
                      "off there; this is the third")
        self.assertEqual(os.environ.get("TIT_LIVE_FIGURES"), "off",
                         "the switch is not actually off in this running suite")

    def test_a_default_ctx_refuses_rather_than_fetching(self):
        import published_figures as pf
        ctx = pf.Ctx()
        self.assertFalse(ctx.consults_production())
        with self.assertRaises(pf.LiveReadsOff):
            ctx.fetch(pf.HOME_URL)

    def test_an_injected_fetch_is_never_gated(self):
        """A stub is not a live read, so switching live reads off must not
        disable the tests that are ABOUT these checks."""
        import published_figures as pf
        seen = []

        def stub(url, timeout=30):
            seen.append(url)
            return b"{}"

        ctx = pf.Ctx(fetch=stub)
        self.assertFalse(ctx.consults_production())
        self.assertEqual(ctx.fetch("https://example.invalid/x"), b"{}")
        self.assertEqual(len(seen), 1)

    def test_check_all_resolves_to_unknown_and_never_to_pass(self):
        import published_figures as pf
        report = pf.check_all()
        self.assertEqual(report.verdict, pf.UNKNOWN)
        self.assertEqual(len(report.unknown), len(report.results))
        for r in report.results:
            self.assertIn("LIVE READS ARE OFF", r.detail)

    def test_the_switch_defaults_on_so_a_real_session_still_checks(self):
        """The bug this guard exists to stop has a twin: answering it by
        switching the live check off everywhere. A session running ops_status.py
        at the start of its day is exactly the run that should read the site."""
        import published_figures as pf
        self.assertTrue(pf.live_reads_enabled(env={}))
        self.assertTrue(pf.live_reads_enabled(env={"TIT_LIVE_FIGURES": "on"}))
        self.assertFalse(pf.live_reads_enabled(env={"TIT_LIVE_FIGURES": "off"}))


if __name__ == "__main__":
    unittest.main()
