"""The guards on the robots.txt append, which are the whole point of it.

A broken robots.txt does not 500 and does not go red. It answers 200, the site
renders identically, and the domain quietly stops being crawled. There is no
alarm to wait for, so the alarm has to be here: every refusal, the idempotent
re-run, and the rollback are asserted offline, against fakes, and the workflow
runs this file before it is allowed to reach for a credential.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import robots_sitemaps as rs  # noqa: E402

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-robots.yml"

LIVE = ("User-agent: *\n"
        "Disallow: /wp-admin/\n"
        "Allow: /wp-admin/admin-ajax.php\n"
        "\n"
        f"Sitemap: {rs.EXISTING_SITEMAP}\n")


# --- fakes -------------------------------------------------------------------

class FakeFtp:
    """A remote filesystem of bytes. Writes are visible to the fake HTTP."""

    def __init__(self, files: dict[str, bytes]):
        self.files = dict(files)
        self.writes: list[tuple[str, bytes]] = []
        self.reads: list[str] = []
        self.corrupt = None          # callable(bytes) -> bytes, the bad-write case
        self.corrupt_every_write = False

    def read(self, path):
        self.reads.append(path)
        return self.files.get(path)

    def write(self, path, data):
        self.writes.append((path, data))
        if self.corrupt is not None:
            data = self.corrupt(data)
            # Only the first write goes wrong unless the test says otherwise:
            # a rollback that is corrupted too is a different scenario, and it
            # has its own test.
            if not self.corrupt_every_write:
                self.corrupt = None
        self.files[path] = data


class FakeHttp:
    """Serves whatever the fake FTP holds, so a write really does change it."""

    def __init__(self, ftp: FakeFtp, routes: dict[str, str], *, status=200):
        self.ftp = ftp
        self.routes = routes
        self.status = status
        self.requested: list[str] = []
        self.body_override = None
        self.frozen = None           # text served regardless: a failed rollback
        self.freeze_after = 1        # ...but only once the write has happened

    def get(self, url):
        self.requested.append(url)
        if self.body_override is not None:
            return self.status, self.body_override
        if self.frozen is not None and len(self.requested) > self.freeze_after:
            return self.status, self.frozen
        path = self.routes.get(url)
        data = self.ftp.files.get(path)
        if data is None:
            return 404, ""
        return self.status, data.decode("utf-8")


def world(root_body=LIVE, blog_body=None):
    blog_body = LIVE if blog_body is None else blog_body
    ftp = FakeFtp({"/robots.txt": root_body.encode("utf-8"),
                   "/blog/robots.txt": blog_body.encode("utf-8")})
    http = FakeHttp(ftp, {f"{rs.SITE}/robots.txt": "/robots.txt",
                          f"{rs.SITE}/blog/robots.txt": "/blog/robots.txt"})
    return ftp, http


def target(name="root"):
    return next(t for t in rs.default_targets() if t.name == name)


# --- reading the file we are about to extend ---------------------------------

def test_sitemap_urls_reads_the_directive_case_insensitively():
    body = ("SITEMAP:  https://example.com/a.xml\n"
            "sitemap: https://example.com/b.xml\n"
            "# Sitemap: https://example.com/commented.xml\n")
    assert rs.sitemap_urls(body) == ["https://example.com/a.xml",
                                     "https://example.com/b.xml"]


# --- the refusals ------------------------------------------------------------

def test_an_empty_fetch_is_refused():
    """An empty robots.txt is not a robots.txt with nothing in it. It is a
    fetch that went wrong, and appending to it would publish a file whose only
    content is our two lines — no Disallow, no original sitemap."""
    with pytest.raises(rs.Refusal, match="empty"):
        rs.guard_fetched(target(), 200, "")
    with pytest.raises(rs.Refusal, match="empty"):
        rs.guard_fetched(target(), 200, "   \n\n  ")


@pytest.mark.parametrize("status", [301, 403, 404, 500, 503])
def test_anything_but_200_is_refused(status):
    with pytest.raises(rs.Refusal, match=str(status)):
        rs.guard_fetched(target(), status, LIVE)


def test_html_served_with_200_is_refused():
    """Bluehost and Cloudflare both answer 200 with an error page. That page
    contains no Sitemap line either, but saying so as 'served HTML' is the
    message that tells a human what actually happened."""
    with pytest.raises(rs.Refusal, match="HTML"):
        rs.guard_fetched(target(), 200,
                         "<!DOCTYPE html><html><body>503 backend</body></html>")


def test_the_apex_landing_page_refusal_explains_what_it_found():
    """Measured 2026-07-30: https://asktherecruiter.com/robots.txt does not
    exist. The apex answers it — and every other unmatched path — with the same
    13,181-byte 'Coming soon' page at HTTP 200. 'Served HTML' is true and
    useless; the refusal has to say that this would be a CREATE."""
    with pytest.raises(rs.Refusal) as caught:
        rs.guard_fetched(target("root"), 200,
                         '<!DOCTYPE html>\n<html lang="en"><head>'
                         '<title>Ask The Recruiter</title></head></html>')
    message = str(caught.value)
    assert "NO robots.txt" in message and "CREATE" in message


def test_the_blog_target_has_no_such_note():
    """Only the apex is missing its file. Attaching the explanation to both
    would make the blog copy's refusals misleading."""
    assert target("blog").absent_note == ""


def test_a_file_without_the_expected_sitemap_line_is_refused():
    """The precondition the brief names. If sitemap_index.xml is not there, the
    file is not the one we were told about, and a surprise means stop."""
    body = "User-agent: *\nDisallow: /wp-admin/\n"
    with pytest.raises(rs.Refusal, match="not in the file we fetched"):
        rs.guard_fetched(target(), 200, body)


def test_a_near_miss_on_the_expected_line_is_still_a_refusal():
    """A different sitemap URL is not the expected one. Substring-matching
    'sitemap_index.xml' would have accepted this."""
    body = f"Sitemap: https://example.com/blog/sitemap_index.xml\n"
    with pytest.raises(rs.Refusal, match="not in the file we fetched"):
        rs.guard_fetched(target(), 200, body)


def test_an_implausibly_large_file_is_refused():
    body = LIVE + ("# padding\n" * 20000)
    with pytest.raises(rs.Refusal, match="not a robots.txt"):
        rs.guard_fetched(target(), 200, body)


def test_the_expected_file_passes():
    assert rs.guard_fetched(target(), 200, LIVE) is None


# --- append only, and idempotent ---------------------------------------------

def test_the_plan_appends_and_never_rewrites():
    intended, added = rs.plan_append(LIVE)
    assert intended.startswith(LIVE), "the original text was re-emitted, not extended"
    assert len(added) == 2
    assert rs.sitemap_urls(intended) == [rs.EXISTING_SITEMAP, *rs.NEW_SITEMAPS]


def test_a_file_with_no_trailing_newline_still_gets_its_own_line():
    intended, added = rs.plan_append(LIVE.rstrip("\n"))
    assert f"sitemap_index.xml\nSitemap: {rs.NEW_SITEMAPS[0]}" in intended
    assert len(added) == 2


def test_a_second_run_adds_nothing():
    once, _ = rs.plan_append(LIVE)
    twice, added = rs.plan_append(once)
    assert added == []
    assert twice == once, "a re-run rewrote the file it had nothing to add to"


def test_only_the_missing_line_is_added():
    """The half-applied case: one line landed, the run died before the second."""
    half = LIVE + f"Sitemap: {rs.NEW_SITEMAPS[0]}\n"
    intended, added = rs.plan_append(half)
    assert added == [f"Sitemap: {rs.NEW_SITEMAPS[1]}"]
    assert intended.count(rs.NEW_SITEMAPS[0]) == 1


# --- what counts as a verified write -----------------------------------------

def test_verify_passes_the_file_we_meant_to_write():
    intended, _ = rs.plan_append(LIVE)
    assert rs.verify_after(LIVE, intended, intended, 200) == []


def test_verify_catches_a_lost_original_line():
    intended, _ = rs.plan_append(LIVE)
    mangled = intended.replace(f"Sitemap: {rs.EXISTING_SITEMAP}\n", "")
    problems = rs.verify_after(LIVE, intended, mangled, 200)
    assert any("original" in p and "gone" in p for p in problems)


def test_verify_catches_truncation_that_keeps_the_right_length():
    """The case a length check alone would pass: the head is cut off and the
    appended bytes make up the shortfall."""
    intended, _ = rs.plan_append(LIVE)
    truncated = intended.replace("Disallow: /wp-admin/\n", "")
    problems = rs.verify_after(LIVE, intended, truncated, 200)
    assert any("missing" in p for p in problems)


def test_verify_catches_a_file_that_did_not_grow():
    problems = rs.verify_after(LIVE, rs.plan_append(LIVE)[0], LIVE, 200)
    assert any("length" in p for p in problems)


def test_verify_catches_a_non_200_and_says_nothing_else():
    intended, _ = rs.plan_append(LIVE)
    problems = rs.verify_after(LIVE, intended, "", 502)
    assert problems == ["re-fetch answered HTTP 502, not 200"]


def test_verify_tolerates_a_trailing_newline_difference():
    intended, _ = rs.plan_append(LIVE)
    assert rs.verify_after(LIVE, intended, intended.rstrip("\n"), 200) == []


# --- the remote path is proven, never guessed --------------------------------

def test_the_path_is_bound_by_content():
    ftp, _ = world()
    assert rs.locate(target("root"), LIVE, ftp) == "/robots.txt"


def test_a_path_whose_bytes_do_not_match_is_not_used():
    ftp, _ = world()
    ftp.files["/robots.txt"] = b"User-agent: *\nDisallow: /\n"
    with pytest.raises(rs.Refusal, match="no remote file matched"):
        rs.locate(target("root"), LIVE, ftp)


def test_the_root_target_never_binds_to_the_blog_file():
    """Both copies may hold identical bytes. Content alone would then let the
    root target adopt /blog/robots.txt and write the blog file twice while
    reporting two successes."""
    ftp = FakeFtp({"/blog/robots.txt": LIVE.encode("utf-8")})
    with pytest.raises(rs.Refusal):
        rs.locate(target("root"), LIVE, ftp)
    assert rs.locate(target("blog"), LIVE, ftp) == "/blog/robots.txt"


def test_a_line_ending_difference_still_binds():
    ftp = FakeFtp({"/robots.txt": LIVE.replace("\n", "\r\n").encode("utf-8")})
    assert rs.locate(target("root"), LIVE, ftp) == "/robots.txt"


# --- end to end --------------------------------------------------------------

def test_a_dry_run_writes_nothing():
    ftp, http = world()
    outcome = rs.process(target("root"), http=http, ftp=ftp, apply=False)
    assert outcome["result"] == "dry-run"
    assert ftp.writes == []


def test_an_applied_run_appends_and_verifies():
    ftp, http = world()
    outcome = rs.process(target("root"), http=http, ftp=ftp, apply=True)
    assert outcome["result"] == "written"
    assert len(ftp.writes) == 1
    after = ftp.files["/robots.txt"].decode("utf-8")
    assert rs.sitemap_urls(after) == [rs.EXISTING_SITEMAP, *rs.NEW_SITEMAPS]
    assert after.startswith(LIVE)


def test_re_running_an_applied_change_is_a_no_op():
    """Safely re-dispatchable, which is the property that lets the owner run it
    twice without thinking about it."""
    ftp, http = world()
    rs.process(target("root"), http=http, ftp=ftp, apply=True)
    outcome = rs.process(target("root"), http=http, ftp=ftp, apply=True)
    assert outcome["result"] == "already-present"
    assert len(ftp.writes) == 1, "the second run wrote the file again"


def test_a_write_that_truncates_is_rolled_back():
    ftp, http = world()
    ftp.corrupt = lambda data: data.split(b"Disallow")[0]
    with pytest.raises(rs.Rolledback):
        rs.process(target("root"), http=http, ftp=ftp, apply=True)
    assert ftp.files["/robots.txt"].decode("utf-8") == LIVE, "the live file was left broken"
    assert len(ftp.writes) == 2, "the rollback write never happened"


def test_a_write_the_host_serves_as_500_is_rolled_back_not_kept():
    ftp, http = world()
    original_get = http.get

    def flaky(url):
        status, body = original_get(url)
        # The verification fetch alone comes back 500 — the fetch that follows
        # the rollback has to be believable, or this would be testing the
        # rollback-failed path instead.
        return (500, "") if len(http.requested) == 2 else (status, body)

    http.get = flaky
    with pytest.raises(rs.Rolledback):
        rs.process(target("root"), http=http, ftp=ftp, apply=True)
    assert ftp.files["/robots.txt"].decode("utf-8") == LIVE


def test_a_rollback_that_does_not_take_is_loud():
    """The one state nobody may ever discover from a traffic graph."""
    ftp, http = world()
    ftp.corrupt = lambda data: b""
    http.frozen = "totally wrong"
    with pytest.raises(rs.RollbackFailed, match="unknown state"):
        rs.process(target("root"), http=http, ftp=ftp, apply=True)


def test_the_rollback_copy_is_kept_before_anything_is_written(tmp_path):
    ftp, http = world()
    ftp.corrupt = lambda data: b"x"
    with pytest.raises(rs.Rolledback):
        rs.process(target("root"), http=http, ftp=ftp, apply=True,
                   backup_dir=str(tmp_path))
    saved = (tmp_path / "robots-root-before.txt").read_text()
    assert saved == LIVE


def test_each_target_stands_on_its_own():
    """A failure on one copy must not leave the other in an unknown state: the
    blog file is fetched, guarded and written from scratch after the root file
    has already refused."""
    ftp, http = world(root_body="User-agent: *\nDisallow: /\n")
    with pytest.raises(rs.Refusal):
        rs.process(target("root"), http=http, ftp=ftp, apply=True)
    assert ftp.writes == []

    outcome = rs.process(target("blog"), http=http, ftp=ftp, apply=True)
    assert outcome["result"] == "written"
    assert ftp.files["/robots.txt"].decode("utf-8") == "User-agent: *\nDisallow: /\n"


def test_main_reports_a_refusal_as_a_failed_run(monkeypatch, capsys):
    ftp, http = world(root_body="User-agent: *\nDisallow: /\n")
    monkeypatch.setattr(rs, "_ftp_from_env", lambda: ftp)
    monkeypatch.setattr(rs, "HttpFetcher", lambda **kw: http)
    monkeypatch.setattr(ftp, "close", lambda: None, raising=False)
    assert rs.main(["--apply", "--targets", "root"]) == 1
    assert "Refusal" in capsys.readouterr().out


def test_main_returns_two_when_a_rollback_failed(monkeypatch, capsys):
    ftp, http = world()
    ftp.corrupt = lambda data: b""
    http.frozen = "wrong"
    monkeypatch.setattr(rs, "_ftp_from_env", lambda: ftp)
    monkeypatch.setattr(rs, "HttpFetcher", lambda **kw: http)
    monkeypatch.setattr(ftp, "close", lambda: None, raising=False)
    assert rs.main(["--apply", "--targets", "root"]) == 2
    assert "::error::" in capsys.readouterr().err


def test_main_is_green_when_both_copies_are_appended(monkeypatch):
    ftp, http = world()
    monkeypatch.setattr(rs, "_ftp_from_env", lambda: ftp)
    monkeypatch.setattr(rs, "HttpFetcher", lambda **kw: http)
    monkeypatch.setattr(ftp, "close", lambda: None, raising=False)
    assert rs.main(["--apply", "--targets", "both"]) == 0
    for path in ("/robots.txt", "/blog/robots.txt"):
        assert rs.sitemap_urls(ftp.files[path].decode("utf-8")) == \
            [rs.EXISTING_SITEMAP, *rs.NEW_SITEMAPS]


# --- the fetcher's own two habits --------------------------------------------

def test_the_probe_busts_the_edge_cache(monkeypatch):
    """Cloudflare will serve the pre-write copy back and make a failed write
    look like a success (gotcha 7)."""
    import requests

    seen = []

    class Resp:
        status_code = 200
        text = LIVE

    monkeypatch.setattr(requests, "get",
                        lambda url, **kw: (seen.append((url, kw)), Resp())[1])
    status, body = rs.HttpFetcher().get(f"{rs.SITE}/robots.txt")
    assert status == 200 and body == LIVE
    url, kwargs = seen[0]
    assert "cb=" in url
    assert "Mozilla" in kwargs["headers"]["User-Agent"], (
        "ModSecurity blocks python-requests outright")


def test_a_random_5xx_is_retried_rather_than_believed(monkeypatch):
    """This host 500s at random under load (gotcha 8). Rolling back a good
    write because of somebody else's bad minute is an outage we caused."""
    import requests

    codes = iter([503, 500, 200])

    class Resp:
        def __init__(self, code):
            self.status_code = code
            self.text = LIVE if code == 200 else "busy"

    monkeypatch.setattr(requests, "get", lambda url, **kw: Resp(next(codes)))
    status, body = rs.HttpFetcher(sleep=lambda _: None).get(f"{rs.SITE}/robots.txt")
    assert status == 200 and body == LIVE


def test_a_persistent_5xx_eventually_gives_up(monkeypatch):
    import requests

    class Resp:
        status_code = 500
        text = "busy"

    monkeypatch.setattr(requests, "get", lambda url, **kw: Resp())
    status, _ = rs.HttpFetcher(sleep=lambda _: None).get(f"{rs.SITE}/robots.txt")
    assert status == 500


# --- the workflow itself -----------------------------------------------------

def _workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def test_the_workflow_has_no_cron_and_must_never_grow_one():
    """One edit to one file. A schedule would re-run it against the live site
    forever, unattended, for nothing."""
    parsed = _workflow()
    triggers = parsed.get("on") or parsed.get(True)
    assert "schedule" not in triggers
    assert "push" not in triggers
    assert "workflow_dispatch" in triggers


def test_the_default_is_a_dry_run():
    inputs = (_workflow().get("on") or _workflow().get(True))["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["default"] is True


def test_the_default_target_is_the_copy_that_exists():
    """`both` stays available and stays honest — it refuses the apex and says
    why — but defaulting to it would make every dispatch a red run reporting a
    thing the owner already decided not to do."""
    inputs = (_workflow().get("on") or _workflow().get(True))["workflow_dispatch"]["inputs"]
    assert inputs["targets"]["default"] == "blog"
    assert set(inputs["targets"]["options"]) == {"blog", "both", "root"}


def test_the_workflow_does_not_widen_the_plugin_deploys_write_path():
    """deploy-plugin.yml refuses to write anywhere but WP_PLUGIN_REMOTE_DIR,
    and that guard is what keeps it away from the live sibling product. This
    file exists so that guard never has to be relaxed."""
    text = WORKFLOW.read_text()
    assert "secrets.WP_PLUGIN_REMOTE_DIR" not in text, (
        "this workflow reaches for the plugin deploy's scoped directory")
    plugin = (ROOT / ".github/workflows/deploy-plugin.yml").read_text()
    assert "robots" not in plugin, "the plugin deploy grew a robots.txt write path"


def test_the_workflow_is_not_in_the_writer_lock_group():
    """It writes no database and no repository file, so queueing for that lock
    would only add one more body able to evict a pending writer."""
    assert (_workflow().get("concurrency") or {}).get("group") == "deploy-robots"


def test_the_workflow_runs_these_guards_before_it_reaches_for_a_credential():
    steps = [s for job in _workflow()["jobs"].values() for s in job.get("steps", [])]
    guard = next(i for i, s in enumerate(steps)
                 if "test_robots_sitemaps" in (s.get("run") or ""))
    writing = next(i for i, s in enumerate(steps)
                   if "robots_sitemaps.py" in (s.get("run") or "")
                   and "pytest" not in (s.get("run") or ""))
    assert guard < writing
    assert "FTP_PASSWORD" not in (steps[guard].get("run") or "")


def test_the_rollback_copies_are_kept_even_when_the_run_fails():
    steps = [s for job in _workflow()["jobs"].values() for s in job.get("steps", [])]
    upload = next(s for s in steps if "upload-artifact" in (s.get("uses") or ""))
    assert "always()" in str(upload.get("if")), (
        "the only copy of the old file is dropped exactly when it is needed")
