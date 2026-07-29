"""Company profile pages, and the threshold that decides which of them exist.

The interesting assertion in this file is not that the page renders. It is that
ONE predicate decides both whether a profile is indexable and whether its URL is
in the sitemap. The sibling tracker shipped noindex URLs inside its own sitemap
and heard about it from Search Console; that defect is not prevented by care,
only by there being a single place the question is answered.

The suite cannot execute PHP, so the source is read as text.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN = Path(__file__).parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
COMPANY = (PLUGIN / "includes" / "company.php").read_text()
BOOTSTRAP = (PLUGIN / "talent-intelligence-tracker.php").read_text()
CSS = (PLUGIN / "assets" / "dashboard.css").read_text()


def _strip_comments(php: str) -> str:
    """Everything that reaches a reader, with the commentary removed.

    Prose rules (no em-dash, no superlative) are about UI COPY. The comments in
    this codebase are long, argumentative and full of both, on purpose, and
    asserting over them would either fail immediately or force the explanations
    out of the file. So they are removed first, and what is left is what can
    actually be rendered.
    """
    php = re.sub(r"/\*.*?\*/", "", php, flags=re.S)
    php = re.sub(r"^[ \t]*//.*$", "", php, flags=re.M)
    return php


UI = _strip_comments(COMPANY)


# --- routing, in this plugin's idiom -------------------------------------

def test_the_include_is_actually_loaded():
    assert "tit_require('includes/company.php');" in BOOTSTRAP


def test_both_routes_are_rewrite_rules_with_a_query_var():
    assert "index.php?tit_company=$matches[1]" in COMPANY
    assert "index.php?tit_company_sitemap=1" in COMPANY
    assert "$vars[] = 'tit_company';" in COMPANY
    assert "$vars[] = 'tit_company_sitemap';" in COMPANY


def test_the_flush_is_guarded_by_the_version_because_ftp_runs_no_activation_hook():
    """FTP deploys never fire an activation hook, and flush_rewrite_rules on
    every request would rewrite the option on every page load."""
    assert "get_option('tit_rewrites_version') === TIT_VERSION" in COMPANY
    assert "flush_rewrite_rules(false)" in COMPANY


def test_the_sitemap_route_cannot_be_swallowed_by_the_profile_route():
    """/company/([^/]+)/ would match a slug called "company-sitemap.xml" if the
    sitemap lived under /company/. It is a sibling path, and its dot is escaped
    so the rule cannot match some other character in that position."""
    assert "talent-intelligence-tracker/company-sitemap.xml" in COMPANY
    assert "str_replace('.', '\\.', TIT_COMPANY_SITEMAP_PATH)" in COMPANY


# --- the gate ------------------------------------------------------------

def test_the_threshold_is_three_named_constants_and_not_numbers_in_a_query():
    for const in ("TIT_COMPANY_MIN_DOCS", "TIT_COMPANY_MIN_KINDS",
                  "TIT_COMPANY_MIN_DOCS_ONE_KIND"):
        assert re.search(rf"^const {const} = \d+;", COMPANY, re.M), const


def test_the_gate_counts_documents_and_never_rows():
    """235 employers carry four rows behind ONE document, because one
    pay-versus-performance table becomes a row per fiscal year. A row count
    measures how finely we parse a filing, not how much we know."""
    predicate = COMPANY[COMPANY.index("function tit_company_meets_threshold"):]
    predicate = predicate[:predicate.index("\n}")]
    assert "$docs" in predicate and "$kinds" in predicate
    assert "count(" not in predicate.lower(), (
        "the predicate must take counts, not rows: anything that counts inside "
        "it can be handed a different population by its two callers"
    )


def test_the_page_and_the_sitemap_share_one_predicate():
    """The whole point. Two implementations of "is this employer worth a URL"
    is exactly how a noindex page ends up in a sitemap."""
    assert "tit_company_meets_threshold(" in COMPANY
    having = COMPANY[COMPANY.index("function tit_company_gate_having"):]
    having = having[:having.index("\n}")]
    for const in ("TIT_COMPANY_MIN_DOCS", "TIT_COMPANY_MIN_KINDS",
                  "TIT_COMPANY_MIN_DOCS_ONE_KIND"):
        assert const in having, (
            f"the sitemap's HAVING clause must be built from {const}, not from "
            "a number typed a second time"
        )
    assert not re.search(r">=\s*\d", having), (
        "a literal threshold in the SQL is the drift this test exists to stop"
    )
    assert "tit_company_gate_having()" in COMPANY


def test_a_single_source_employer_needs_more_documents_than_a_multi_source_one():
    """Three UK pay gap filings are one thing said three times: the read-through
    sentence is identical and only the percentage moves. That template-plus-a-
    number shape is what gets a whole set filtered."""
    body = COMPANY[COMPANY.index("function tit_company_meets_threshold"):]
    body = body[:body.index("\n}")]
    assert "TIT_COMPANY_MIN_DOCS_ONE_KIND" in body
    assert re.search(r"const TIT_COMPANY_MIN_DOCS_ONE_KIND = (\d+);", COMPANY)
    one_kind = int(re.search(r"const TIT_COMPANY_MIN_DOCS_ONE_KIND = (\d+);", COMPANY).group(1))
    floor = int(re.search(r"const TIT_COMPANY_MIN_DOCS = (\d+);", COMPANY).group(1))
    assert one_kind > floor


def test_below_the_threshold_is_noindex_rather_than_a_404():
    """The dashboard table links every employer to its profile. 404ing the thin
    ones would break an internal link a recruiter just clicked."""
    assert "X-Robots-Tag: noindex, follow" in COMPANY
    assert '<meta name="robots" content="noindex, follow" />' in COMPANY
    assert "!$p['indexable']" in COMPANY or "!$profile['indexable']" in COMPANY


def test_only_one_robots_tag_reaches_the_head():
    """Measured live on 1.45.0: Yoast prints its own robots tag on these routes,
    so a below-threshold profile served "noindex, follow" from us AND
    "follow, index" from Yoast. The most restrictive wins, so the page really
    was noindex, but two head tags contradicting each other is a defect an audit
    reports. Yoast is told; we stay quiet when it is there. The header goes out
    either way, so a silent Yoast cannot leave the page indexable."""
    assert "add_filter('wpseo_robots_array', 'tit_company_yoast_robots');" in COMPANY
    assert "!defined('WPSEO_VERSION')" in COMPANY
    assert "$robots['index'] = 'noindex'" in COMPANY
    header = COMPANY[COMPANY.index("function tit_company_template"):]
    header = header[:header.index("\n}\n")]
    assert "X-Robots-Tag: noindex, follow" in header
    assert "WPSEO" not in header, "the header must not be conditional on a plugin"


def test_the_sitemap_url_does_not_redirect():
    """WordPress trailing-slashes anything it does not recognise as a file, so
    the sitemap answered 301 before serving. A sitemap that redirects is a
    redirect reported in Search Console on every fetch."""
    assert "add_filter('redirect_canonical', 'tit_company_sitemap_no_canonical_redirect');" in COMPANY


def test_an_employer_we_hold_nothing_for_is_still_a_404():
    """An empty page for every possible slug is a doorway-page pattern."""
    assert "status_header(404)" in COMPANY
    assert "get_404_template()" in COMPANY


def test_the_sitemap_lists_only_gated_employers_and_says_when_they_changed():
    sitemap = COMPANY[COMPANY.index("function tit_company_sitemap_entries"):]
    sitemap = sitemap[:sitemap.index("\nfunction tit_company_sitemap_template")]
    assert "tit_company_gate_having()" in sitemap
    assert "is_current = 1" in sitemap
    assert "GROUP BY company_key" in sitemap
    assert "<lastmod>" in COMPANY


def test_a_future_dated_row_never_becomes_a_future_lastmod():
    """A pay-versus-performance table is filed for a fiscal year that has not
    ended, so published_date is legitimately in the future. A crawler reads a
    future lastmod as a broken date."""
    assert "$e['lastmod'] <= $today" in COMPANY


def test_the_sitemap_is_xml_and_is_itself_noindex():
    assert "Content-Type: application/xml; charset=UTF-8" in COMPANY
    assert '<?xml version="1.0" encoding="UTF-8"?>' in COMPANY
    assert "http://www.sitemaps.org/schemas/sitemap/0.9" in COMPANY
    assert "'X-Robots-Tag: noindex'" in COMPANY


def test_crawlers_are_told_where_the_sitemap_is():
    assert "add_filter('robots_txt', 'tit_company_robots_txt');" in COMPANY


# --- live figures, never a build step ------------------------------------

def test_nothing_is_generated_and_nothing_is_cached_into_a_file():
    """The page must be correct the moment the data changes, so there is no
    generated artefact anywhere in this path."""
    assert "file_put_contents" not in COMPANY
    assert "$wpdb->get_results" in COMPANY


def test_title_and_description_are_computed_from_the_row_set():
    assert "add_filter('pre_get_document_title', 'tit_company_title');" in COMPANY
    assert "add_action('wp_head', 'tit_company_head', 1);" in COMPANY
    title = COMPANY[COMPANY.index("function tit_company_title"):]
    title = title[:title.index("\nadd_filter")]
    assert "tit_company_current()" in title
    assert "$p['updates']" in title, "the title must carry a live figure"
    head = COMPANY[COMPANY.index("function tit_company_head"):]
    head = head[:head.index("\nadd_action")]
    assert 'name="description"' in head
    assert 'rel="canonical"' in head
    assert "tit_company_current()" in head


def test_the_status_line_reads_the_newest_row_only():
    """An employer whose last three years were quiet and whose last week was a
    funding round is described by the funding round, not by an average."""
    body = COMPANY[COMPANY.index("function tit_company_status_line"):]
    body = body[:body.index("\n}\n")]
    assert "$rows[0]" in body
    assert "human_time_diff" in body
    assert "$ts > time()" in body, (
        "a fiscal-year date can sit in the future; 'in 3 months ago' is not a "
        "sentence"
    )


def test_the_stats_strip_carries_the_four_facts_and_omits_the_ones_we_lack():
    body = COMPANY[COMPANY.index("function tit_company_facts"):]
    body = body[:body.index("\n}\n")]
    for label in ("updates tracked", "disclosed funding", "leadership change",
                  "tracked since"):
        assert label in body, label
    assert "$profile['funding_usd'] > 0" in body
    assert "$profile['leadership'] > 0" in body


def test_the_board_sparkline_is_rendered_here_and_survives_a_partial_deploy():
    assert "function_exists('tit_board_series_panel')" in COMPANY
    assert "tit_board_series_panel($key)" in COMPANY


def test_the_timeline_still_uses_the_existing_row_shape():
    """Not a new renderer. The same what happened / what it means / evidence /
    source / date the dashboard table and the old profile already used."""
    for cls in ("tit-timeline", "tit-event", "tit-event-when", "tit-event-meta",
                "tit-rt", "tit-conf"):
        assert cls in COMPANY, cls
    assert "tit_confidence_labels" in COMPANY


# --- prose rules ---------------------------------------------------------

def test_no_em_dashes_in_anything_that_reaches_the_page():
    assert "—" not in UI, "em-dash in UI copy"


def test_no_superlatives_on_page_meta_or_structured_data():
    banned = ("best", "biggest", "largest", "leading", "most comprehensive",
              "definitive", "unrivalled", "unrivaled", "world-class",
              "fastest", "#1", "number one")
    lowered = UI.lower()
    for word in banned:
        assert word not in lowered, f"superlative in UI copy: {word}"


def test_structured_data_describes_only_what_is_on_the_page():
    """The sibling earned a manual-action risk emitting identical FAQPage markup
    on ~1,830 URLs where the text was nowhere in the document."""
    assert "FAQPage" not in UI
    ld = COMPANY[COMPANY.index("application/ld+json"):]
    ld = ld[:ld.index("</script>")]
    assert "'@type'    => 'Organization'" in ld
    # Every field asserted is rendered in the timeline above it.
    assert "'headline'      => $r['headline']" in ld
    assert "'url'           => $r['source_url']" in ld
    assert "$visible" in ld, "the markup must describe the rows actually printed"


def test_structured_data_is_not_emitted_on_a_noindex_profile():
    ld_at = COMPANY.index("application/ld+json")
    before = COMPANY[:ld_at]
    assert "if ($profile['indexable']) : ?>" in before[-2000:]


def test_the_status_line_has_a_style_of_its_own():
    assert ".tit-company .tit-status" in CSS
