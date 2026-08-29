"""A COLLECTOR MUST NOT WRITE INTO A SCHEMA THE SITE HAS NOT GOT YET.

WHY THIS FILE EXISTS. For a week in August 2026 production ran plugin 1.86.0
while main was 1.87.1, and four workflows were red the whole time for that one
reason:

    collect                 row 4: WordPress database error: Processing the
    collect national press  value for the following field failed: deal_type.
    collect-structured      The supplied value may be too long ...
    indeed-index            404 rest_no_route

#97 had widened `deal_type` to VARCHAR(32) because `outbound_investment` is 19
characters; the migration runs off the version bump, so on 1.86.0 the column
was still VARCHAR(16). The indeed-index route simply did not exist before
1.87.0. Four workflows, one cause, and not one of those messages says "the
plugin is old" -- the first three name a column and a row number, which sends
whoever reads them into the classifier instead of into the deploy.

The unit tests were green throughout, correctly: nothing they run touches the
live plugin. Green tests never proved the fixed plugin had reached WordPress,
and nothing else asked.

WHAT IS PINNED HERE:

  * a live plugin older than the floor STOPS the run before the first batch,
    with a message naming both versions and the deploy command;
  * an unreachable or silent site is UNKNOWN -- neither a pass nor a failure --
    because a preflight that reddens on a network hiccup gets removed;
  * versions compare NUMERICALLY, so 1.87.10 is newer than 1.87.9;
  * the floor is never ahead of the plugin actually in this repo, which would
    make every run refuse to write for ever.

PROVEN BY MUTATION: drop the check_plugin_version() call from publish() and
test_publish_refuses_to_write_to_an_old_plugin fails, having sent the batch.
"""

import json
import re
from pathlib import Path

import pytest

from pipeline import publish

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MAIN = (ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
               / "talent-intelligence-tracker.php")


@pytest.fixture(autouse=True)
def _armed(monkeypatch):
    """conftest disarms the preflight for the whole suite. This file is the
    exception: it is the tests ABOUT the preflight, and every session below is
    stubbed, so nothing here reaches the network either."""
    monkeypatch.setenv("TIT_PLUGIN_PREFLIGHT", "on")


class _Resp:
    def __init__(self, status=200, payload=None, boom=False):
        self.status_code, self._payload, self._boom = status, payload, boom

    def json(self):
        if self._boom:
            raise ValueError("not json")
        return self._payload


class _Session:
    """Records what was asked for, so a test can prove nothing was written."""

    def __init__(self, resp):
        self._resp, self.gets, self.posts = resp, [], []

    def get(self, url, **kw):
        self.gets.append(url)
        return self._resp

    def post(self, url, **kw):
        self.posts.append(url)
        return _Resp(200, {"stored": 0, "duplicate": 0})


# --- the floor itself -----------------------------------------------------

def test_the_floor_is_not_ahead_of_the_plugin_in_this_repo():
    """A floor above the shipped plugin refuses every write, for ever.

    It can never be satisfied by deploying, because the thing you would deploy
    is already older than the floor.
    """
    src = PLUGIN_MAIN.read_text(encoding="utf-8")
    m = re.search(r"define\('TIT_VERSION',\s*'([0-9.]+)'\)", src)
    assert m, "TIT_VERSION is not readable from the plugin entry point"
    shipped = publish._version_tuple(m.group(1))
    floor = publish._version_tuple(publish.REQUIRED_PLUGIN_VERSION)
    assert floor <= shipped, (
        "REQUIRED_PLUGIN_VERSION is %s but this repo ships %s. No deploy can "
        "satisfy that, so every collector would refuse to write until somebody "
        "edited the constant back down."
        % (publish.REQUIRED_PLUGIN_VERSION, m.group(1)))


def test_versions_compare_numerically_not_as_text():
    assert publish._version_tuple("1.87.10") > publish._version_tuple("1.87.9"), (
        "string comparison would call 1.87.10 older than 1.87.9 and let a real "
        "upgrade read as a downgrade")
    assert publish._version_tuple("1.87.1") > publish._version_tuple("1.86.0")


# --- the check ------------------------------------------------------------

def test_an_old_plugin_is_refused_with_both_versions_named():
    session = _Session(_Resp(200, {"plugin_version": "1.86.0"}))
    with pytest.raises(publish.PluginTooOld) as exc:
        publish.check_plugin_version("https://example.test/blog",
                                     required="1.87.1", session=session)
    message = str(exc.value)
    assert "1.86.0" in message and "1.87.1" in message, (
        "the refusal named neither what is live nor what is needed, which is "
        "the whole complaint about the deal_type error it replaces: %s" % message)
    assert "deploy-plugin" in message, (
        "the refusal does not say how to fix it. The fix is a deploy and it is "
        "a human step in this repo, so the command belongs in the message.")


def test_a_new_enough_plugin_passes():
    session = _Session(_Resp(200, {"plugin_version": "1.87.2"}))
    assert publish.check_plugin_version(
        "https://example.test/blog", required="1.87.1",
        session=session) == "1.87.2"


def test_an_equal_version_passes():
    session = _Session(_Resp(200, {"plugin_version": "1.87.1"}))
    assert publish.check_plugin_version(
        "https://example.test/blog", required="1.87.1", session=session)


@pytest.mark.parametrize("resp", [
    _Resp(503),                              # host down
    _Resp(200, boom=True),                   # a Cloudflare page, not json
    _Resp(200, {}),                          # a plugin too old to report one
])
def test_a_site_that_cannot_answer_is_unknown_and_never_a_failure(resp):
    """Absence of a signal is not a pass, and it is not a verdict either."""
    assert publish.check_plugin_version(
        "https://example.test/blog", required="1.87.1",
        session=_Session(resp)) is None


# --- the wiring -----------------------------------------------------------

def test_publish_refuses_to_write_to_an_old_plugin(monkeypatch, tmp_path):
    """The stop happens BEFORE the first batch, not partway through one."""
    from pipeline import schema

    conn = schema.connect(tmp_path / "p.db")
    conn.execute(
        "INSERT INTO signals (signal_id, headline, summary, talent_readthrough,"
        " company, company_key, pillar, signal_direction, confidence,"
        " source_url, source_name, captured_at, as_of, content_hash, collector,"
        " published_date, is_current) VALUES"
        " ('s1','h','s','t','Acme','acme','company_development','neutral',"
        " 'reported','https://example.com/x','Ex','2026-08-01','2026-08-01',"
        " 'c1','national_press','2026-08-01',1)")
    conn.commit()

    monkeypatch.setenv("WP_SITE_URL", "https://example.test/blog")
    monkeypatch.setenv("WP_API_KEY", "k")
    session = _Session(_Resp(200, {"plugin_version": "1.86.0"}))
    monkeypatch.setattr(publish.requests, "Session", lambda: session)

    with pytest.raises(publish.PluginTooOld):
        publish.publish(conn)

    assert session.posts == [], (
        "rows were POSTed to a plugin known to be too old. Half a run written "
        "into a schema that cannot hold it is exactly the state the preflight "
        "exists to prevent.")
    conn.close()


def test_the_refusal_is_a_publish_error_so_a_data_job_goes_red():
    assert issubclass(publish.PluginTooOld, publish.PublishError), (
        "a run that refused to write must fail the way every other write "
        "failure here fails: loudly, and non-zero.")
