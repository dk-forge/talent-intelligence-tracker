"""GETTING THE INSTRUMENT IS NOT THE MEASUREMENT, AND IT HAS TO SAY WHY IT FAILED.

Seven test files in this repository render real markup in real headless Chrome
through `cdp.py`. Every one of them is only as trustworthy as "Chrome started".
On 2026-08-18 run 32152450241 went red on main with

    cdp.CDPUnavailable: Chrome never exposed a debug target: timed out
    FAILED tests/test_control_boundaries.py::ControlBoundaries::
           test_a_drawing_keeps_its_scroll_box_on_a_phone

and that message is the whole problem. It is a description of the WAIT, not of
what happened to Chrome. Measured: one launch takes 1.1 seconds on the owner's
Mac against a 30 second deadline, the suite is serial so exactly one Chrome
exists at a time, the PHP steps have finished before pytest starts, and the job
lands at 7m36s against its 15 minute ceiling. Nothing was starved. So a 30x
margin ran out, which does not happen to a browser that is merely slow -- it
happens to one that is not coming up at all. And the code could not tell the
two apart, because it never looked at the process and threw its stderr away.

Two properties, and neither of them relaxes anything:

1. **A dead Chrome is reported as dead.** If the process has exited, waiting
   the rest of the deadline out cannot help, and calling it a timeout names the
   wrong thing. It fails at once, with the exit status.
2. **Chrome's own stderr survives the failure.** It went to DEVNULL, so the one
   run that could have explained this had nothing to explain it with.

And the cause this removes: the debug port used to be picked by binding port 0
in Python, reading the number, closing the socket, and passing that number to
Chrome. Between the close and Chrome's bind, anything else on the machine can
take it -- and on a shared runner recycling ephemeral ports, "anything else"
includes the runner. Chrome that cannot bind its debug port exits, and what the
caller saw was "timed out". Chrome picks its own port now (`0`) and writes the
real one to DevToolsActivePort in the profile directory, which closes the race
rather than making it less likely.

NO RETRY WAS ADDED. One red in 200 runs over 5.5 days is not a browser that
fails to start often; it is one that failed once, unexplained. Relaunching
would have hidden exactly the evidence these tests exist to keep.
"""
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cdp  # noqa: E402


def _fake_chrome(body):
    """A shell script standing in for the browser. Returns its path."""
    path = tempfile.mktemp(prefix='fake-chrome-', suffix='.sh')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('#!/bin/sh\n' + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP)
    return path


class ALaunchThatFailsSaysWhat(unittest.TestCase):
    """The failure path, driven with a browser that is not a browser."""

    def setUp(self):
        self._real = cdp.find_chrome
        self._scripts = []

    def tearDown(self):
        cdp.find_chrome = self._real
        for path in self._scripts:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _use(self, body):
        path = _fake_chrome(body)
        self._scripts.append(path)
        cdp.find_chrome = lambda: path
        return path

    def test_a_chrome_that_exits_is_reported_as_dead_and_not_as_slow(self):
        """It must not spend the deadline waiting for a process that is gone,
        and it must not call that a timeout."""
        self._use('exit 3\n')
        started = time.time()
        with self.assertRaises(cdp.CDPUnavailable) as caught:
            with cdp.Browser():
                pass
        elapsed = time.time() - started
        message = str(caught.exception)
        self.assertLess(
            elapsed, 10.0,
            'Chrome exited immediately and the launch still took %.1fs. It is '
            'waiting out a deadline for a process that is already gone.'
            % elapsed)
        self.assertIn(
            'status 3', message,
            'Chrome exited with status 3 and the error does not carry the '
            'status, so nobody reading the run log can tell a crash from a '
            'slow start. Got: %s' % message)
        self.assertNotIn(
            'timed out', message,
            'Chrome did not time out, it exited. Naming the wait instead of '
            'the cause is what made run 32152450241 undiagnosable. Got: %s'
            % message)

    def test_chromes_own_stderr_reaches_the_person_reading_the_run(self):
        """stderr went to DEVNULL, so the one failure that needed explaining
        had nothing to explain it with."""
        self._use('echo "cannot bind the debug port" 1>&2\nexit 1\n')
        with self.assertRaises(cdp.CDPUnavailable) as caught:
            with cdp.Browser():
                pass
        self.assertIn(
            'cannot bind the debug port', str(caught.exception),
            'Chrome said why it died and the error threw it away. Got: %s'
            % caught.exception)

    def test_chrome_picks_the_debug_port_rather_than_being_handed_one(self):
        """A port reserved in Python and released before Chrome binds it is a
        race, and the loser of that race looks exactly like a timeout."""
        record = tempfile.mktemp(prefix='fake-chrome-argv-')
        self._use('printf "%s\\n" "$@" > ' + record + '\nexit 1\n')
        with self.assertRaises(cdp.CDPUnavailable):
            with cdp.Browser():
                pass
        argv = Path(record).read_text(encoding='utf-8').splitlines()
        os.unlink(record)
        ports = [a for a in argv if a.startswith('--remote-debugging-port=')]
        self.assertEqual(
            ports, ['--remote-debugging-port=0'],
            'Chrome must choose its own debug port and report it in '
            'DevToolsActivePort. A number chosen here can be taken by anything '
            'else on the runner between the reservation and the bind. Got: %s'
            % (ports or 'no --remote-debugging-port at all'))


class ARealChromeStillComesUp(unittest.TestCase):
    """The change above is worth nothing if it breaks the working path."""

    def test_a_browser_launches_and_evaluates(self):
        if not cdp.find_chrome():
            self.skipTest('no Chrome on this machine: UNKNOWN, not a pass')
        with cdp.Browser(width=400, height=300) as browser:
            self.assertEqual(browser.eval_js('1 + 1'), 2)


if __name__ == '__main__':
    unittest.main()
