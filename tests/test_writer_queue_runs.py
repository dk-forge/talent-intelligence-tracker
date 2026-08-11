"""The drainer must survive GitHub's own bad minute, and only that.

On 2026-07-30 a `HTTP 502: Server Error` on page 2 of the run list took the
whole drain-writers run down, minutes after the queue had started moving for
the first time. Nothing was wrong with the queue; GitHub's API was briefly
unwell and the drainer had no tolerance for it.

Retrying is safe here and nowhere else in this workflow: every call in
writer_queue_runs is a READ. It dispatches nothing and writes nothing, so a
repeated call costs one request. The dispatch step deliberately does NOT
retry, because a repeated dispatch is how you get two runs for one ticket.

The other half matters as much. A 422 means the call itself is wrong -- the
malformed `slice` input that stalled this queue for a day returned exactly
that -- and retrying a deterministic refusal is the infinite silent loop the
queue already learned about. Those must fail on the first attempt, loudly.
"""

from __future__ import annotations

import types
import unittest

import writer_queue_runs


class _FakeGh:
    """Stands in for subprocess.run, replaying a scripted sequence."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def __call__(self, argv, capture_output, text):
        self.calls += 1
        index = min(self.calls - 1, len(self.responses) - 1)
        code, out, err = self.responses[index]
        return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)


class TransientFailureTests(unittest.TestCase):
    def setUp(self):
        self._real_run = writer_queue_runs.subprocess.run
        self._real_sleep = writer_queue_runs.time.sleep
        writer_queue_runs.time.sleep = lambda _seconds: None

    def tearDown(self):
        writer_queue_runs.subprocess.run = self._real_run
        writer_queue_runs.time.sleep = self._real_sleep

    def _install(self, responses):
        fake = _FakeGh(responses)
        writer_queue_runs.subprocess.run = fake
        return fake

    def test_a_502_is_retried_and_the_drain_survives_it(self):
        fake = self._install([
            (1, "", "failed to get runs: HTTP 502: Server Error"),
            (0, "[]", ""),
        ])
        self.assertEqual(writer_queue_runs._gh(["run", "list"]), "[]")
        self.assertEqual(fake.calls, 2, "the 502 should have been retried once")

    def test_every_transient_shape_we_have_actually_seen_is_retried(self):
        for stderr in (
            "HTTP 502: Server Error",
            "HTTP 503: Server Error",
            "HTTP 429: Too Many Requests",
            "connection reset by peer",
            "context deadline exceeded: timeout",
            "EOF occurred in violation of protocol",
            "TLS handshake timeout",
        ):
            with self.subTest(stderr=stderr):
                fake = self._install([(1, "", stderr), (0, "[]", "")])
                self.assertEqual(writer_queue_runs._gh(["run", "list"]), "[]")
                self.assertEqual(fake.calls, 2, f"{stderr!r} should retry")

    def test_a_deterministic_refusal_fails_on_the_first_attempt(self):
        # The exact stderr that stalled this queue on 2026-07-30. Retrying it
        # would have burned four calls to reach the same refusal.
        for stderr in (
            'Unexpected inputs provided: ["slice"] (HTTP 422)',
            "HTTP 404: Not Found",
            "HTTP 401: Bad credentials",
            "HTTP 403: Resource not accessible by integration",
        ):
            with self.subTest(stderr=stderr):
                fake = self._install([(1, "", stderr)])
                with self.assertRaises(RuntimeError):
                    writer_queue_runs._gh(["api", "whatever"])
                self.assertEqual(
                    fake.calls, 1,
                    f"{stderr!r} is deterministic and must not be retried")

    def test_a_transient_failure_that_never_clears_still_gives_up(self):
        fake = self._install([(1, "", "HTTP 503: Server Error")])
        with self.assertRaises(RuntimeError):
            writer_queue_runs._gh(["run", "list"])
        self.assertEqual(fake.calls, writer_queue_runs._ATTEMPTS)

    def test_the_error_carries_the_last_message_not_a_generic_one(self):
        self._install([(1, "", "HTTP 503: Server Error")])
        with self.assertRaises(RuntimeError) as caught:
            writer_queue_runs._gh(["run", "list"])
        self.assertIn("503", str(caught.exception))

    def test_an_unreadable_job_count_stays_unknown_rather_than_zero(self):
        # Pre-existing behaviour, pinned here because the retry now sits under
        # it: a run whose job count cannot be read must not be recorded as
        # having created zero jobs, which is the signature of an eviction.
        fake = self._install([
            (0, '[{"databaseId": 1, "conclusion": "cancelled"}]', ""),
            (1, "", "HTTP 404: Not Found"),
        ])
        runs = writer_queue_runs.fetch(limit=1)
        self.assertIsNone(runs[0]["job_count"])
        self.assertEqual(fake.calls, 2, "the 404 must not have been retried")


if __name__ == "__main__":
    unittest.main()
