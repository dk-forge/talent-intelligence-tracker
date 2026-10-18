"""City and company alerts, step 1: follow a page through its own filtered RSS feed.

TECHLOG 2026-10-12 designed it: GET talent/v1/feed already takes /query's
filters, so a per-city or per-company alert is a URL that exists today. Step 1
prints it on the page and in the head. No email, no store, no consent question,
no personal data; step 2 (email follows) needs the sibling's subscriber store
and an owner ruling, and nothing here builds it.

Place pages are run in tests/php/render_place_pages.php (the link carries
exactly the cell's own filter). Company pages have no harness, so these assert
the source: both the head and the page build the URL through ONE helper, with
the resolved company_key, never the requested slug.
"""
import re
from pathlib import Path

PLUGIN = Path(__file__).parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
FEED = (PLUGIN / "includes" / "feed.php").read_text()
COMPANY = (PLUGIN / "includes" / "company.php").read_text()
PLACES = (PLUGIN / "includes" / "places.php").read_text()


def _fn(src, name):
    i = src.index("function " + name + "(")
    j = src.find("\nfunction ", i + 1)
    return src[i:j if j > 0 else len(src)]


def test_one_helper_builds_the_follow_url_from_the_feed_route():
    body = _fn(FEED, "tit_feed_follow_url")
    assert "rest_url('talent/v1/feed')" in body
    assert "rawurlencode" in body, "a city name with a space must not break the URL"


def test_company_head_and_page_follow_the_resolved_key():
    head = _fn(COMPANY, "tit_company_head")
    page = _fn(COMPANY, "tit_company_render")
    assert re.search(r"tit_feed_follow_link\(.*tit_feed_follow_url\('company', \$current\['rows'\]\[0\]\['company_key'\]\)", head), head[-900:]
    assert "tit_feed_follow_html($name, tit_feed_follow_url('company', $company_key))" in page


def test_place_pages_follow_their_own_param():
    assert "tit_feed_follow_url($spec['param'], $cell['key'])" in _fn(PLACES, "tit_place_render")
    assert "tit_feed_follow_url($kinds[$current['kind']]['param'], $cell['key'])" in _fn(PLACES, "tit_place_head")


def test_follow_copy_says_rss_and_promises_no_email():
    html = _fn(FEED, "tit_feed_follow_html")
    assert "(RSS)" in html
    assert "email" not in html.lower()
    assert "—" not in html


def test_guarded_for_the_ftp_race():
    # feed.php can land after company.php / places.php mid-upload.
    for src, fn in ((COMPANY, "tit_company_head"), (COMPANY, "tit_company_render"),
                    (PLACES, "tit_place_head"), (PLACES, "tit_place_render")):
        assert "function_exists('tit_feed_follow_url')" in _fn(src, fn), fn


FOLLOW = (PLUGIN / "includes" / "follow.php").read_text()


def test_email_follows_store_nobody_here():
    # The sibling owns subscribers; this plugin must never write a follower.
    assert "INSERT" not in FOLLOW.upper().replace("INSERTED", "")
    assert "email" not in re.sub(r"/\*.*?\*/", "", FOLLOW, flags=re.S).lower()
    assert "function_exists('alt_follow_form_html')" in FOLLOW


def test_email_form_only_on_city_and_company_pages():
    assert "$kind === 'city' && function_exists('tit_follow_form')" in _fn(PLACES, "tit_place_render")
    assert "tit_follow_form('company', $company_key, $name)" in _fn(COMPANY, "tit_company_render")
