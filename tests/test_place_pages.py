"""Country, city and industry pages, and the threshold that decides which exist.

The interesting assertions here are the structural ones, and they are the same
two company.php earned:

  * ONE predicate decides whether a cell has a page AND whether its URL is in
    the sitemap. The sibling tracker shipped noindex URLs inside its own sitemap
    and heard about it from Search Console; that defect is not prevented by care,
    only by there being a single place the question is answered.
  * ONE implementation serves all three page types, because they differ in
    exactly one thing: which column decides membership. Three near-identical
    files drift, and the drift is silent.

Whether the gate actually gates, and what a URL below it answers, are behaviours
across a change of state, so they are proved by running the code in
tests/php/render_place_pages.php rather than by reading it here. This file
asserts the properties that ARE textual: that the routing is wired in this
plugin's idiom, that no threshold is written down twice, that the query budget is
declared and enforced, and that the prose obeys the house rules.
"""

from __future__ import annotations

import re
from pathlib import Path

from phpsource import balanced_block

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
PLACES = (PLUGIN / "includes" / "places.php").read_text()
BOOTSTRAP = (PLUGIN / "talent-intelligence-tracker.php").read_text()
SHORTCODES = (PLUGIN / "includes" / "shortcodes.php").read_text()
CSS = (PLUGIN / "assets" / "dashboard.css").read_text()
WORKFLOW = (ROOT / ".github" / "workflows" / "tests.yml").read_text()


def _strip_comments(php: str) -> str:
    """Everything that reaches a reader, with the commentary removed.

    Prose rules (no em-dash, no superlative) are about UI COPY. The comments in
    this codebase are long, argumentative and full of both, on purpose, and
    asserting over them would either fail immediately or force the explanations
    out of the file.
    """
    php = re.sub(r"/\*.*?\*/", "", php, flags=re.S)
    php = re.sub(r"^[ \t]*//.*$", "", php, flags=re.M)
    return php


UI = _strip_comments(PLACES)


def _body(php: str, name: str) -> str:
    """One PHP function, from its declaration to the next top-level declaration.

    NOT bracket-matched. phpsource.balanced_block() is the right tool for an
    array literal and the wrong one here: half of these functions are templates,
    so their bodies contain HTML with braces in style attributes and prose with
    apostrophes in it, and an apostrophe inside "employer's" opens a PHP string
    that never closes. The terminator used instead is structural rather than a
    coupling to some unrelated literal: the next thing declared at column zero.
    """
    marker = f"function {name}("
    assert marker in php, (
        f"could not find {name}() in the source. It was renamed or moved, so this "
        f"test is no longer reading the code it means to assert about."
    )
    start = php.index(marker)
    rest = php[start + len(marker):]
    ends = [m.start() for m in re.finditer(r"^(?:function |const |add_action\(|add_filter\()",
                                           rest, re.M)]
    return rest[:ends[0]] if ends else rest


# --- it is actually loaded, and routed in this plugin's idiom -------------

def test_the_include_is_loaded_after_company_php():
    """It uses company.php's slug canonicaliser and its collision refusal, and
    degrades to no pages rather than fatalling if that file has not landed."""
    assert "tit_require('includes/places.php');" in BOOTSTRAP
    assert BOOTSTRAP.index("includes/company.php") < BOOTSTRAP.index("includes/places.php")


def test_all_five_routes_are_rewrite_rules_with_query_vars():
    assert "index.php?tit_place_kind=' . $kind . '&tit_place=$matches[1]" in PLACES
    assert "index.php?tit_places=1" in PLACES
    assert "index.php?tit_places_sitemap=1" in PLACES
    for var in ("tit_place", "tit_place_kind", "tit_places", "tit_places_sitemap"):
        assert f"$vars[] = '{var}';" in PLACES, var


def test_the_three_paths_are_the_ones_that_were_asked_for():
    kinds = balanced_block(PLACES, "function tit_place_kinds() {", what="tit_place_kinds")
    for path in ("talent-intelligence-tracker/country",
                 "talent-intelligence-tracker/city",
                 "talent-intelligence-tracker/industry"):
        assert f"'path'    => '{path}'" in kinds, path


def test_the_sitemap_route_cannot_be_swallowed_by_a_cell_route():
    """A rule matching /country/([^/]+)/ would match a slug called
    "places-sitemap.xml" if the sitemap lived under one of the three. It is a
    sibling path, and its dot is escaped so the rule cannot match some other
    character in that position."""
    assert "talent-intelligence-tracker/places-sitemap.xml" in PLACES
    assert "str_replace('.', '\\.', TIT_PLACES_SITEMAP_PATH)" in PLACES


def test_the_rewrite_is_registered_early_enough_for_the_shared_flush():
    """company.php flushes once per version at init priority 99, and
    flush_rewrite_rules regenerates from every rule registered by then. These
    rules go on at the default priority, so the flush picks them up and there is
    no second flush competing with it."""
    assert "add_action('init', 'tit_places_rewrite');" in PLACES
    flush = _body(PLACES, "tit_places_maybe_flush")
    assert "function_exists('tit_company_maybe_flush')" in flush, (
        "the fallback flush must stand down when company.php is there to do it, "
        "or a deploy flushes the rules twice"
    )
    assert "flush_rewrite_rules(false)" in flush


def test_these_pages_are_styled_without_naming_themselves():
    """The route check used to name each route, which is how tit_corrections
    stopped being covered. Any tit_ query var now means the request is ours."""
    enqueue = _body(SHORTCODES, "tit_enqueue_assets")
    assert "strpos((string) $name, 'tit_') === 0" in enqueue
    for named in ("'tit_company'", "'tit_sources'", "'tit_recall'"):
        assert named not in enqueue, (
            f"{named} is named again, so the next route added is unstyled"
        )


def test_the_pages_ship_no_javascript():
    """Nothing on them needs it: no filter panel, no chart that repaints, no
    list that pages. The bars are inline widths computed in PHP."""
    assert "add_filter('tit_route_needs_js', 'tit_places_needs_no_js');" in PLACES
    assert "apply_filters('tit_route_needs_js', true)" in SHORTCODES
    assert "if (!$with_js) return;" in _body(SHORTCODES, "tit_enqueue_dashboard_assets")
    assert "<script" not in UI.replace('<script type="application/ld+json">', ""), (
        "the only script on these pages is the JSON-LD block, which is data"
    )


def test_no_stylesheet_of_its_own_and_no_external_request():
    """The dashboard's stylesheet, extended, so a reader arriving from the
    tracker makes no second request. Nothing is fetched from anywhere."""
    assert "wp_enqueue_style" not in PLACES
    assert ".tit-place h1" in CSS
    assert ".tit-place-list" in CSS
    for offsite in ("http://fonts.", "https://fonts.", "cdn.", "googleapis", "unpkg"):
        assert offsite not in PLACES, offsite
        assert offsite not in CSS.split("/* --- Country, city and industry")[-1], offsite


# --- the gate ------------------------------------------------------------

def test_the_threshold_is_three_named_constants_and_not_numbers_in_a_query():
    for const in ("TIT_PLACE_MIN_DOCS", "TIT_PLACE_MIN_EMPLOYERS",
                  "TIT_PLACE_MIN_SOURCES"):
        assert re.search(rf"^const {const} = \d+;", PLACES, re.M), const


def test_the_gate_counts_documents_and_never_rows():
    """sec_execcomp splits one pay-versus-performance table into a row per
    fiscal year, so the United States shows 10,360 rows behind 7,620 documents.
    A row count measures how finely we parse a filing."""
    index = _body(PLACES, "tit_place_index")
    assert "COUNT(DISTINCT source_url) AS documents" in index
    predicate = _body(PLACES, "tit_place_meets_threshold")
    assert "$documents" in predicate
    assert "records" not in predicate, (
        "the gate must be asked about documents, employers and sources, never "
        "about a row count"
    )


def test_one_predicate_answers_the_question_for_the_page_and_the_sitemap():
    """The whole point. Two implementations of "does this cell deserve a URL" is
    exactly how a page that redirects ends up inside a sitemap."""
    assert PLACES.count("tit_place_meets_threshold(") >= 2
    published = _body(PLACES, "tit_place_published")
    assert "$c['gated']" in published, (
        "the directory and the sitemap read the flag the index computed, so they "
        "cannot ask the question a second way"
    )
    sitemap = _body(PLACES, "tit_places_sitemap_entries")
    assert "tit_place_published(" in sitemap
    assert not re.search(r"HAVING|>=\s*\d", sitemap), (
        "a second copy of the gate, in SQL, is the drift this test exists to stop"
    )


def test_every_bar_is_load_bearing_and_the_reasoning_is_recorded():
    """The document bar is the only one that excludes anything today, and that is
    precisely why the other two have to be there: they are what stops a cell
    crossing on one employer's filing history."""
    assert "TIT_PLACE_MIN_EMPLOYERS" in _body(PLACES, "tit_place_meets_threshold")
    assert "TIT_PLACE_MIN_SOURCES" in _body(PLACES, "tit_place_meets_threshold")
    assert "Belfast" in PLACES, (
        "the measured case behind the employer bar must be written down, or the "
        "next reader deletes the bar as dead weight"
    )


def test_below_the_bar_is_a_redirect_and_it_is_temporary():
    """A cell below the bar today crosses it as coverage grows, which is the
    whole design. A 301 tells every crawler never to ask again."""
    route = _body(PLACES, "tit_place_route")
    assert "'code' => 302" in route
    assert "tit_place_dashboard_url(" in route
    assert "!$cell['gated']" in route
    assert "'code' => 301" in route, "a non-canonical form still 301s"


def test_a_cell_we_hold_nothing_for_is_a_404():
    """An empty page for every possible slug is the doorway-page pattern."""
    assert "'action' => '404'" in _body(PLACES, "tit_place_route")
    assert "status_header(404)" in PLACES
    assert "get_404_template()" in PLACES


def test_the_routing_decision_has_no_side_effects_so_it_can_be_tested():
    """Four answers, three of which differ only in a status code. A decision
    wrapped around exit() can only be checked by reading it."""
    template = _body(PLACES, "tit_place_template")
    assert "tit_place_route(" in template
    assert "wp_safe_redirect($route['url'], $route['code'])" in template
    route = _body(PLACES, "tit_place_route")
    for side_effect in ("wp_safe_redirect", "status_header", "echo", "exit"):
        assert side_effect not in route, (
            f"tit_place_route() must decide and not act: found {side_effect}"
        )


# --- slugs ---------------------------------------------------------------

def test_the_slug_is_company_phps_canonicaliser_and_not_a_second_one():
    """No encoding of "&" survives both the rewrite and a sitemap, accents 404
    percent-encoded and literal, and all of that was measured live once already.
    A second implementation would have to learn it again."""
    slug = _body(PLACES, "tit_place_slug")
    assert "tit_company_slug($name)" in slug
    assert "preg_replace" not in slug, (
        "the transliteration rule lives in company.php; a copy here is a copy "
        "that drifts"
    )
    assert "preg_match('/^[a-z0-9-]+$/'" in slug, (
        "and anything that does not come out as plain ASCII gets no page, rather "
        "than a percent-encoded URL that 404s"
    )


def test_the_slug_is_of_the_name_and_not_of_the_stored_code():
    """/country/gb/ and /industry/pharma_biotech/ are URLs nobody searches for
    and nobody can read. The name is what a reader and a search engine already
    have, and the map back to the code is a closed vocabulary we ship."""
    slug = _body(PLACES, "tit_place_slug")
    assert "tit_place_name($kind, $key)" in slug
    names = _body(PLACES, "tit_place_names")
    assert "tit_country_names" in names
    assert "tit_industry_labels" in names


def test_a_value_we_have_no_name_for_gets_no_page():
    """The two vocabularies are closed and shipped in this plugin. A stored value
    absent from one is a value we cannot title a page with."""
    name = _body(PLACES, "tit_place_name")
    assert "isset($names[$key])" in name
    assert ": ''" in name, "and an unknown value gets no name rather than its key"


def test_two_cells_claiming_one_slug_are_refused_rather_than_resolved():
    """"St. Louis" and "St Louis" would both claim /city/st-louis/. That is one
    cell recorded twice, and serving either under the shared URL shows half of
    it. The fix is upstream, in normalisation."""
    index = _body(PLACES, "tit_place_index")
    assert "count($owners) > 1" in index
    assert "$collisions[$slug] = true" in index
    assert "continue;" in index


# --- one implementation, three page types --------------------------------

def test_the_three_types_differ_only_in_data():
    """They differ in exactly one thing: which column decides membership. Three
    near-identical files would drift, silently, because a fix applied to one and
    not the others is not a failing test."""
    kinds = balanced_block(PLACES, "function tit_place_kinds() {", what="tit_place_kinds")
    for kind in ("'country' =>", "'city' =>", "'industry' =>"):
        assert kind in kinds, kind
    # No per-kind branch anywhere except the two places a genuine difference
    # lives: the schema.org type and the city-name caveat.
    branches = re.findall(r"\$kind === '(country|city|industry)'", PLACES)
    assert set(branches) <= {"country", "industry", "city"}
    assert len(branches) <= 3, (
        f"{len(branches)} per-kind branches; the differences belong in "
        "tit_place_kinds() as data"
    )


def test_the_membership_clause_matches_the_dashboards_own_filter():
    """These pages link to the dashboard filtered to the same cell, so the two
    populations have to be the same one. Writing it in tit_build_where()'s shape
    also means it can use idx_geo and idx_hq, which a COALESCE on the left of a
    comparison cannot."""
    kinds = balanced_block(PLACES, "function tit_place_kinds() {", what="tit_place_kinds")
    assert "(country = %s OR (country IS NULL AND hq_country = %s))" in kinds
    assert "(city = %s OR (city IS NULL AND hq_city = %s))" in kinds
    api = (PLUGIN / "includes" / "api.php").read_text()
    assert "(city = %s OR (city IS NULL AND hq_city = %s))" in api, (
        "the clause is copied from tit_build_where() on purpose; if the API's "
        "shape has changed, this one has to change with it"
    )


def test_no_new_index_is_added_and_none_is_needed():
    """The page's own filter is served by idx_geo, idx_hq and idx_industry, which
    already exist. The index-building GROUP BY is a scan either way, of a table
    the dashboard already scans on every render. Nothing here writes to the
    committed database, and no migration is required."""
    db = (PLUGIN / "includes" / "db.php").read_text()
    for key in ("KEY idx_geo (country, city)", "KEY idx_hq (hq_country, hq_city)",
                "KEY idx_industry (industry)"):
        assert key in db, key
    assert "CREATE INDEX" not in PLACES
    assert "ALTER TABLE" not in PLACES


# --- performance ---------------------------------------------------------

def test_the_query_budget_is_a_number_and_something_enforces_it():
    """"No N+1" is true on the day it is written and false three commits later,
    because what breaks it is one innocent call inside a foreach."""
    assert re.search(r"^const TIT_PLACE_QUERY_BUDGET = \d+;", PLACES, re.M)
    harness = Path(__file__).parent / "php" / "render_place_pages.php"
    assert harness.exists()
    assert "TIT_PLACE_QUERY_BUDGET" in harness.read_text()
    assert "php tests/php/render_place_pages.php" in WORKFLOW, (
        "the harness exists but nothing runs it, which is worse than not having it"
    )


def test_every_figure_on_the_page_comes_out_of_one_bundle():
    """A page that computes a figure where it prints it is a page whose cost
    grows with its own layout."""
    render = _body(PLACES, "tit_place_render")
    assert "$wpdb" not in render, "the renderer must not query"
    assert "get_results" not in render and "get_row" not in render
    directory = _body(PLACES, "tit_places_render")
    assert "$wpdb" not in directory, "nor the directory"


def test_the_number_of_queries_is_written_down_and_adds_up():
    """The structural half of the budget. The harness counts what a render
    actually costs; this counts what the source contains, so a query added
    without moving the constant fails in both places at once."""
    per_page = len(re.findall(r"\$wpdb->get_", _body(PLACES, "tit_place_facts")))
    per_index = len(re.findall(r"\$wpdb->get_", _body(PLACES, "tit_place_index")))
    assert per_index == 1, f"the cells index must be one query, found {per_index}"
    assert per_page == 5, f"the page bundle must be five queries, found {per_page}"
    budget = int(re.search(r"const TIT_PLACE_QUERY_BUDGET = (\d+);", PLACES).group(1))
    # Five for the page, plus the index for its own kind and the index for the
    # kind it cross-links to.
    assert budget == per_page + 2 * per_index, (
        f"the budget says {budget} and the source contains "
        f"{per_page} + {2 * per_index}"
    )
    assert len(re.findall(r"\$wpdb->get_", PLACES)) == per_page + per_index, (
        "every query in this file belongs to one of those two functions, so the "
        "budget cannot be dodged by querying somewhere else"
    )


def test_every_recent_query_has_a_limit():
    facts = _body(PLACES, "tit_place_facts")
    for fragment in ("TIT_PLACE_RECENT", "TIT_PLACE_TOP_EMPLOYERS", "$spec['cross_n']"):
        assert f"LIMIT \" . (int) {fragment}" in facts, fragment


def test_the_cache_is_keyed_on_the_slug_and_the_version_and_expires_quickly():
    """The version is about SHAPE: a deploy that adds a figure would otherwise
    read a cached array from the previous version that does not contain it, and
    print a zero for a fact it holds. Freshness is tit_flush_caches()."""
    facts = _body(PLACES, "tit_place_facts")
    assert "md5($kind . '|' . $key . '|' . TIT_VERSION)" in facts
    assert "set_transient($cache_key, $facts, tit_place_ttl())" in facts
    ttl = _body(PLACES, "tit_place_ttl")
    assert "TIT_CACHE_TTL" in ttl
    assert "300" in ttl, "and a value of its own if api.php has not landed yet"


def test_the_caches_are_dropped_by_the_existing_flush_path():
    """tit_flush_caches() deletes every _transient_tit_% row, so every key here
    has to start tit_ or a collect run's changes wait for the TTL."""
    db = (PLUGIN / "includes" / "db.php").read_text()
    assert "'_transient_tit_%'" in db
    for key in re.findall(r"(?:get|set)_transient\(\s*'([^']+)'", PLACES):
        assert key.startswith("tit_"), key
    for key in re.findall(r"\$cache_key = '([^']+)'", PLACES):
        assert key.startswith("tit_"), key


def test_nothing_is_generated_and_nothing_is_written_to_disk():
    """The page has to be right the moment the data changes, so there is no
    generated artefact in this path and no hardcoded list of places."""
    assert "file_put_contents" not in PLACES
    assert "$wpdb->get_results" in PLACES
    assert "$wpdb->query" not in PLACES, "these pages only ever read"
    assert "$wpdb->insert" not in PLACES
    assert "$wpdb->update" not in PLACES


# --- what the page carries -----------------------------------------------

def test_the_heading_needs_no_article_and_the_prose_derives_one():
    """"Hiring And Funding Signals In United States" is not a sentence in
    English. The heading leads with the name and a colon, which needs no article
    and puts the searched-for word first; the prose derives the article from a
    pattern rather than from a list of exceptions, because tit_country_names() is
    the whole of ISO 3166-1 and grows."""
    assert "'%s: Hiring, Funding And Leadership Signals'" in PLACES
    the = _body(PLACES, "tit_place_the")
    assert "preg_match(" in the
    assert "$kind !== 'country'" in the
    assert "return ''" in the, "and an unmatched name gets none, which is only terse"


def test_an_industry_is_not_somewhere_a_source_placed_a_row():
    """It is a classification we made. "Every update a source placed in
    Technology" claims something no source said."""
    kinds = balanced_block(PLACES, "function tit_place_kinds() {", what="tit_place_kinds")
    assert kinds.count("'lede'       => 'Every update we hold that a source placed in %1$s'") == 2
    assert "we have filed under %1$s" in kinds


def test_the_page_states_what_its_numbers_mean():
    """A count here is a count of documents we have read and can link to, which
    is not a count of things that happened. A page of numbers that does not say
    so is inviting the wrong reading."""
    assert "counts of documents we have read" in UI


def test_the_one_source_caveat_is_computed_and_shares_the_dashboards_bar():
    """The model is the United Kingdom: 4,761 of its 4,808 rows are the gender
    pay gap filing, one mandatory annual return that every large employer files.
    A reader scanning that count would take it as market activity."""
    note = _body(PLACES, "tit_place_source_note")
    assert "TIT_PLACE_ONE_SOURCE_SHARE" in note
    assert "return ''" in note, "and it vanishes when it stops being true"
    assert "filing volume rather than" in note
    caveat = _body(SHORTCODES, "tit_place_caveat")
    assert "0.66" in caveat and "TIT_PLACE_ONE_SOURCE_SHARE = 0.66" in PLACES, (
        "the page and the dashboard must mean the same thing by dominated"
    )


def test_a_city_page_admits_that_a_city_name_is_not_unique():
    """We store the name a source printed, with no country qualifier. London
    holds 1,339 rows placed in the United Kingdom and 5 that are not."""
    note = _body(PLACES, "tit_place_city_note")
    assert "$kind !== 'city'" in note
    assert "grouped by the city name a source printed" in note


def test_money_never_appears_without_its_coverage_sentence():
    """Only some funding updates carry a US dollar figure. A total shown as if it
    covered every round is the plausible-but-wrong number this product cannot
    carry."""
    render = _body(PLACES, "tit_place_render")
    assert "tit_money_coverage_sentence" in render
    assert "tit_funding_where" in _body(PLACES, "tit_place_facts"), (
        "and the population has to be the one the rest of the site counts"
    )


def test_employers_link_to_the_pages_that_already_exist():
    links = _body(PLACES, "tit_place_employer_links")
    assert "tit_company_url($key)" in links
    assert "tit_company_servable_slug($key)" in links, (
        "linking an indexable page at a URL that 404s is worse than printing the "
        "name without a link, and company.php already knows which ones those are"
    )


def test_the_cross_links_carry_both_cases():
    """A target with a page links to it. One without links to the dashboard
    filtered to it, which is where a below-threshold URL would have redirected,
    so both routes land in the same place."""
    cross = _body(PLACES, "tit_place_cross_links")
    assert "tit_place_url($kind, $key)" in cross
    assert "tit_place_dashboard_url($kind, $key)" in cross
    assert "$cell['gated']" in cross


def test_the_dashboard_link_uses_the_parameters_the_dashboard_reads():
    kinds = balanced_block(PLACES, "function tit_place_kinds() {", what="tit_place_kinds")
    for param in ("'param'   => 'country'", "'param'   => 'city'", "'param'   => 'industry'"):
        assert param in kinds, param
    js = (PLUGIN / "assets" / "dashboard.js").read_text()
    for element in ("country: document.getElementById('tit-f-country')",
                    "city: document.getElementById('tit-f-city')",
                    "industry: document.getElementById('tit-f-industry')"):
        assert element in js, (
            f"the querystring is only a filter if dashboard.js hydrates it: {element}"
        )
    assert "applyUrlState()" in js


def test_the_directory_is_linked_from_the_dashboard_hero():
    """These routes are in no theme menu. A set of pages a crawler can only find
    through a sitemap gets crawled slowly and trusted less."""
    hero = SHORTCODES[SHORTCODES.index('<p class="tit-hero-links">'):]
    hero = hero[:hero.index("</p>")]
    assert "talent-intelligence-tracker/places/" in hero
    assert "Every source" in hero, "next to the link it belongs beside"


# --- SEO furniture -------------------------------------------------------

def test_title_and_description_are_computed_from_live_figures():
    assert "add_filter('pre_get_document_title', 'tit_place_title');" in PLACES
    assert "add_action('wp_head', 'tit_place_head', 1);" in PLACES
    title = _body(PLACES, "tit_place_title")
    assert "tit_place_facts(" in title
    assert "$facts['records']" in title, "the title must carry a live figure"
    head = _body(PLACES, "tit_place_head")
    assert 'name="description"' in head
    assert 'rel="canonical"' in head
    assert "!$current['cell']['gated']" in head, (
        "a cell that redirects has no page for a canonical tag to describe"
    )


def test_no_robots_tag_is_printed_and_the_reason_is_recorded():
    """company.php buffers the head and replaces every robots tag because a
    below-threshold profile is noindex and the site's SEO plugin printed an
    "index" beside it. Here there is no such page: below the bar redirects. A
    second tag agreeing with the default is the only defect available."""
    assert 'name="robots"' not in PLACES
    assert "X-Robots-Tag: noindex'" in PLACES, "except on the sitemap, which is not a page"
    assert "ob_start()" not in PLACES


def test_the_canonical_url_comes_from_the_resolved_key():
    """Two slugs can reach a page (the canonical one and a capitalised form that
    301s), so the requested one is not an identity."""
    assert "tit_place_url($current['kind'], $cell['key'])" in PLACES
    assert "tit_place_url($kind, $cell['key'])" in PLACES
    assert "tit_place_url($kind, $current['slug'])" not in PLACES
    assert "tit_place_url($kind, $current['requested'])" not in PLACES


def test_structured_data_describes_only_what_is_on_the_page():
    """The sibling earned a manual-action risk emitting identical FAQPage markup
    on ~1,830 URLs where the text was nowhere in the document."""
    assert "FAQPage" not in UI
    ld = PLACES[PLACES.index("application/ld+json"):]
    ld = ld[:ld.index("</script>")]
    assert "'@type'    => 'CollectionPage'" in ld
    assert "'numberOfItems'   => count($listed)" in ld, (
        "numberOfItems has to count the items LISTED; the number of records we "
        "hold is on the page in words, and claiming it here would describe a "
        "list that is not present"
    )
    assert "'headline'      => $r['headline']" in ld
    assert "'url'           => $r['source_url']" in ld


def test_an_industry_is_not_marked_up_as_a_place():
    kinds = balanced_block(PLACES, "function tit_place_kinds() {", what="tit_place_kinds")
    assert kinds.count("'schema'  => 'Place'") == 2
    assert "'schema'  => 'Thing'" in kinds


def test_the_sitemap_is_xml_and_is_itself_noindex():
    assert "Content-Type: application/xml; charset=UTF-8" in PLACES
    assert '<?xml version="1.0" encoding="UTF-8"?>' in PLACES
    assert "http://www.sitemaps.org/schemas/sitemap/0.9" in PLACES
    assert "'X-Robots-Tag: noindex'" in PLACES


def test_the_sitemap_url_does_not_redirect():
    """WordPress trailing-slashes anything it does not recognise as a file, so
    the sitemap would answer 301 before serving, on every fetch."""
    assert "add_filter('redirect_canonical', 'tit_places_sitemap_no_canonical_redirect');" in PLACES


def test_a_future_dated_row_never_becomes_a_future_lastmod():
    """A pay-versus-performance table is filed for a fiscal year that has not
    ended, so published_date is legitimately in the future."""
    assert "$entry['lastmod'] <= $today" in PLACES


def test_the_sitemap_is_a_string_so_its_urls_can_be_checked():
    """Built inside the function that also sends headers and exits, it could only
    be checked by reading it."""
    assert "function tit_places_sitemap_xml()" in PLACES
    template = _body(PLACES, "tit_places_sitemap_template")
    assert "echo tit_places_sitemap_xml();" in template


def test_the_robots_txt_filter_is_not_counted_as_working():
    """It is registered and it is inert, for the reasons company.php records:
    /blog/robots.txt is a physical file Apache serves from disk, and the
    robots.txt a crawler reads for this host belongs to the separate root app."""
    assert "add_filter('robots_txt', 'tit_places_robots_txt');" in PLACES
    doc = PLACES[:PLACES.index("function tit_places_robots_txt")]
    assert "INERT" in doc[-1200:] and "MANUAL STEP" in doc[-1200:]


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


def test_headings_are_title_case():
    """The owner asked for Title Case on these pages."""
    for heading in re.findall(r"<h[12][^>]*>([^<]{4,})</h[12]>", UI):
        words = [w for w in heading.split() if w.isalpha() and len(w) > 3]
        if not words:
            continue
        assert all(w[0].isupper() for w in words), heading


def test_the_body_never_scrolls_sideways():
    """Mobile first: wide content scrolls inside its own container. These pages
    carry no table, and the one wide thing they can hold is a long employer name,
    which wraps."""
    assert "<table" not in UI
    place_css = CSS.split("/* --- Country, city and industry")[-1]
    assert "flex-wrap:wrap" in place_css
