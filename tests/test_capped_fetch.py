"""Untrusted response bodies are bounded, and the drift check runs first.

WHY THIS FILE EXISTS. The catalogue is several hundred publisher feeds in ~90
countries, fetched twice a day, unattended. Every one of them was read with a
plain `requests.get`, which buffers the WHOLE body before it returns and
inflates gzip and brotli on the way. So a `Content-Length` of four kilobytes
could be a gigabyte in this process, and the run had no ceiling of any kind.

That is not hypothetical for THIS catalogue. Its documented hazard is a domain
that expires and gets taken over (`botswanaguardian.co.bw` became a betting
site), and a taken-over domain is precisely the party that would serve a
decompression bomb instead of RSS. The same list that made the domain-drift
guard necessary made this necessary.

The second half is an ordering defect the first half exposed. The drift check
sat above `parse(resp.content, feed)`, which reads as "before the body" and was
not: the body had already been read, in full, inside `http.get`. The guard
against citing a betting site was declining to PARSE bytes it had already paid
to receive. Streaming is what makes the ordering real.

Every test here fails on the pre-`capped_fetch` code.
"""
import io
import unittest

from collectors import capped_fetch, national_press


class Raw:
    """Behaves like urllib3's raw stream: hands over only what is asked for."""

    def __init__(self, payload):
        self.stream = io.BytesIO(payload)
        self.reads = []

    def read(self, n, decode_content=True):
        self.reads.append(n)
        return self.stream.read(n)


class Resp:
    def __init__(self, payload=b"", status=200, url="", headers=None):
        self.raw = Raw(payload)
        self.status_code = status
        self.url = url
        self.headers = headers or {}
        self.closed = False
        self._payload = payload

    @property
    def content(self):                      # pragma: no cover - see below
        raise AssertionError(
            "the whole body was read; that is the defect this file is about")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")

    def close(self):
        self.closed = True


class Session:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert kwargs.get("stream") is True, (
            "untrusted bytes must arrive streamed, not buffered whole")
        return self.resp


class TheBodyIsBounded(unittest.TestCase):
    def test_a_huge_body_costs_the_cap_and_not_the_body(self):
        resp = Resp(b"A" * 50_000_000)
        _r, body = capped_fetch.capped_get("https://p.example/feed",
                                           session=Session(resp),
                                           max_bytes=1_000)
        self.assertEqual(len(body), 1_000)
        self.assertEqual(resp.raw.reads, [1_000],
                         "exactly one bounded read, not a loop to exhaustion")

    def test_the_cap_is_asked_for_on_the_DECOMPRESSED_stream(self):
        """`decode_content=True` is the whole point: a cap on the compressed
        stream is not a cap, because compression is where a small response
        becomes a large one."""
        seen = {}

        class Checking(Raw):
            def read(self, n, decode_content=True):
                seen["decode_content"] = decode_content
                return super().read(n, decode_content)

        resp = Resp(b"x" * 100)
        resp.raw = Checking(b"x" * 100)
        capped_fetch.capped_get("https://p.example/f", session=Session(resp),
                                max_bytes=10)
        self.assertIs(seen.get("decode_content"), True)

    def test_the_response_is_always_closed(self):
        resp = Resp(b"x" * 100)
        capped_fetch.capped_get("https://p.example/f", session=Session(resp),
                                max_bytes=10)
        self.assertTrue(resp.closed, "a streamed response left open leaks a "
                                     "connection on every feed in the run")


FEED_XML = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
            b"<item><title>Acme raises</title>"
            b"<link>https://publisher.example/a</link>"
            b"<pubDate>Mon, 07 Jul 2026 10:00:00 GMT</pubDate>"
            b"</item></channel></rss>")


def a_feed():
    """A real Feed through its real constructor. `expected_domains` is derived
    from `rss`/`site`, so pointing the response at a different host is what
    simulates a taken-over domain, exactly as it happens in production."""
    return national_press.Feed(
        name="Publisher", rss="https://publisher.example/feed",
        country="Botswana", city="Gaborone", coverage="national",
        language="en", source_type="News Outlet")


class NationalPressFeedFetch(unittest.TestCase):
    """The 600-odd-feed call site, which is the one that runs twice a day."""

    def test_a_feed_is_streamed_not_buffered(self):
        resp = Resp(FEED_XML, url="https://publisher.example/feed")
        session = Session(resp)
        items, used = national_press.fetch(a_feed(),
                                           session=session)
        self.assertEqual(used, "rss")
        self.assertEqual(len(items), 1)
        self.assertTrue(all(kw.get("stream") for _u, kw in session.calls))

    def test_a_drifted_domain_is_refused_BEFORE_its_body_is_read(self):
        """The ordering assertion, and the reason the two fixes are one.

        `Raw.reads` is empty if and only if nothing was read from the hijacked
        host. On the old code the body was already in memory by the time this
        check ran, so there was nothing left to refuse to read.
        """
        resp = Resp(FEED_XML, url="https://sports-betting.example/feed")
        with self.assertRaises(national_press.DomainDrift):
            national_press.fetch(a_feed(),
                                 session=Session(resp))
        self.assertEqual(resp.raw.reads, [],
                         "a hijacked domain's body must never be read at all")
        self.assertTrue(resp.closed)

    def test_a_feed_larger_than_the_cap_does_not_take_the_run_with_it(self):
        """A bomb is truncated, the parse then fails, and the feed is counted
        dead. Loud and bounded beats silent and unbounded."""
        resp = Resp(b"<rss>" + b"A" * 20_000_000,
                    url="https://publisher.example/feed")
        feed = a_feed()
        try:
            national_press.fetch(feed, session=Session(resp))
        except Exception:
            pass
        self.assertLessEqual(sum(resp.raw.reads), capped_fetch.FEED_BYTES,
                             "the run must never read more than the cap")


class EveryUntrustedCallSiteUsesIt(unittest.TestCase):
    """A shared helper that half the collectors ignore is not a fix."""

    CALLERS = ("national_press", "news_backstop", "google_news", "gdelt",
               "press_archive", "benchmark_chase")

    def test_the_collectors_that_read_third_party_bytes_route_through_it(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1] / "collectors"
        for name in self.CALLERS:
            src = (root / f"{name}.py").read_text()
            with self.subTest(collector=name):
                self.assertIn("capped_fetch", src,
                              f"{name} still reads a third-party body whole")

    def test_press_archive_no_longer_keeps_its_own_copy_of_the_pattern(self):
        """It was the only correct implementation and it was private. Now it is
        a user of the shared one, so there is a single definition to fix."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "collectors" / "press_archive.py").read_text()
        head = src[src.index("def head_text"):]
        head = head[:head.index("\ndef ", 1)]
        self.assertIn("capped_fetch", head)
        self.assertNotIn("resp.raw.read(", head,
                         "the second copy of the capped read is still here")


class AThirdPartysFailureIsData(unittest.TestCase):
    """A publisher that goes silent MID-BODY is that publisher's outcome.

    On 2026-08-05 neweralive.na accepted the connection, returned 200, and
    stopped sending mid-body. `raw.read` raised urllib3's ReadTimeoutError,
    which is NOT a `requests.RequestException`, so it sailed past `head_text`'s
    except clause and killed the whole 19-minute press slice (run 31013599896),
    whose crash-manufactured FAILED ticket then reddened drain-writers.
    Every test here fails on the pre-fix tree.
    """

    def test_a_mid_body_timeout_is_raised_as_a_requests_exception(self):
        import requests
        import urllib3

        class DyingRaw:
            def read(self, n, decode_content=True):
                raise urllib3.exceptions.ReadTimeoutError(
                    None, "neweralive.na", "Read timed out.")

        resp = Resp()
        resp.raw = DyingRaw()
        with self.assertRaises(requests.RequestException):
            capped_fetch.read_capped(resp, 1_000)
        self.assertTrue(resp.closed, "the dead response must still be closed")

    def test_head_text_records_the_dead_host_rather_than_raising(self):
        import urllib3
        from collectors import press_archive

        class DyingRaw:
            def read(self, n, decode_content=True):
                raise urllib3.exceptions.ReadTimeoutError(
                    None, "neweralive.na", "Read timed out.")

        resp = Resp()
        resp.raw = DyingRaw()
        original = press_archive.robots_allows
        press_archive.robots_allows = lambda url, session=None: True
        try:
            title, description = press_archive.head_text(
                "https://neweralive.na/posts/x", session=Session(resp))
        finally:
            press_archive.robots_allows = original
        self.assertEqual((title, description), ("", ""))

    def test_the_walk_finishes_the_slice_when_one_publisher_escapes(self):
        """Even an exception the reader did not anticipate is one publisher's
        `dead` record, and the publishers after it are still read."""
        import requests
        from collectors import press_archive
        from collectors.national_press import Feed

        def feed(name):
            return Feed(name=name, rss=f"https://{name}.example/rss",
                        country="Namibia", city="", coverage="national",
                        language="en", source_type="press")

        walked = []

        def reader(feed_, lo, hi, **kwargs):
            walked.append(feed_.name)
            if feed_.name == "dead-host":
                raise requests.exceptions.ConnectionError("mid-body timeout")
            return [], {"name": feed_.name, "country": feed_.country,
                        "site": feed_.rss, "status": "no_window",
                        "urls": 0, "heads": 0, "items": 0, "detail": ""}

        original = press_archive.read_publisher
        press_archive.read_publisher = reader
        try:
            press_archive.collect(
                start="2026-01-01", end="2026-01-31",
                feeds=[feed("dead-host"), feed("alive")], dry_run=True)
        finally:
            press_archive.read_publisher = original

        self.assertEqual(walked, ["dead-host", "alive"],
                         "the walk must survive the dead host and finish")
        by_name = {r["name"]: r for r in press_archive.PUBLISHER_HEALTH}
        self.assertEqual(by_name["dead-host"]["status"], "dead",
                         "the dead host must be RECORDED as an outcome")
        self.assertIn("ConnectionError", by_name["dead-host"]["detail"])


if __name__ == "__main__":
    unittest.main()
