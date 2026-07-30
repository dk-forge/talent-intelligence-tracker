<?php
/**
 * Country, city and industry pages:
 *
 *   /talent-intelligence-tracker/country/{slug}/
 *   /talent-intelligence-tracker/city/{slug}/
 *   /talent-intelligence-tracker/industry/{slug}/
 *   /talent-intelligence-tracker/places/            the crawlable directory
 *   /talent-intelligence-tracker/places-sitemap.xml
 *
 * ONE FILE AND ONE IMPLEMENTATION FOR THREE PAGE TYPES, deliberately. The three
 * differ in exactly one thing: which column decides membership. Everything else
 * (the gate, the slug, the aggregates, the caching, the SEO furniture) is the
 * same question asked of a different WHERE clause, so it is written once and the
 * differences live in tit_place_kinds() as data. Three near-identical files
 * would drift within a month, and the drift would be silent: a fix applied to
 * the country page and not the city page is not a failing test, it is two pages
 * that quietly disagree.
 *
 * This follows includes/company.php closely on purpose. Same canonical ASCII
 * slug, same refusal of collisions rather than resolution of them, same
 * threshold gate enforced in ONE predicate that both the page and the sitemap go
 * through, same "everything is computed on render" rule. A reader who
 * understands company.php understands this file, and the two places where it
 * deliberately diverges say so where they diverge.
 *
 * EVERYTHING HERE IS COMPUTED ON RENDER off {prefix}tit_signals. There is no
 * generated artefact, no build step and no hardcoded list of places: a country
 * gets a page the day its coverage crosses the bar, and loses it again if rows
 * are retracted, without anybody editing this file.
 */

if (!defined('ABSPATH')) exit;

const TIT_PLACES_PATH = 'talent-intelligence-tracker/places';
const TIT_PLACES_SITEMAP_PATH = 'talent-intelligence-tracker/places-sitemap.xml';

/*
 * ---------------------------------------------------------------------------
 * THE THRESHOLD GATE
 * ---------------------------------------------------------------------------
 *
 * A cell below the bar gets NO PAGE AT ALL. It is not a thin page marked
 * noindex, it is not in the sitemap, and it is not a 404 either: it answers 302
 * to the dashboard filtered to that same cell, which already exists and shows
 * the same rows. See tit_place_template() for why that is a 302 and not a 301.
 *
 * MEASURED 2026-07-29 against data/talent_intel.db, read-only: 15,711 current
 * rows, 7,377 employers, 57 countries, 34 cities, 18 industries. Distinct
 * SOURCE DOCUMENTS per cell, which is the unit the gate counts:
 *
 *   countries   US 7,620 · GB 4,808 · CA 69 · IE 30 | IL 24 · AU 21 · CN 20 ·
 *               IN 15 · then a tail of 49 cells at 14 or fewer, 27 of them at 1
 *   cities      London 1,345 · New York 554 · San Francisco 185 · Austin 116 ·
 *               Manchester 108 · Boston 102 · Edinburgh 49 · Seattle 46 ·
 *               Toronto 30 · Dublin 28 | Belfast 9 · then 23 cells at 8 or fewer
 *   industries  every one of the 18 between 83 and 1,672
 *
 * Three things that distribution decides, in the order they change the answer:
 *
 * 1. ROWS ARE THE WRONG UNIT, for the reason company.php gives: sec_execcomp
 *    splits one pay-versus-performance table into a row per fiscal year, so the
 *    United States shows 10,360 rows behind 7,620 documents. A row count
 *    measures how finely we parse a filing. The gate counts DISTINCT
 *    source_url, like the company gate does.
 *
 * 2. A CELL SMALLER THAN ONE SCREEN OF THE DASHBOARD IS NOT A PAGE. /query
 *    serves 50 rows a page, so everything we hold for a cell of two dozen
 *    documents is already visible in a single screen of the dashboard filtered
 *    to it. A dedicated URL there adds a heading and nothing a reader cannot
 *    already see, which is the doorway-page shape. 25 is also where the city
 *    distribution actually breaks: Dublin has 28 documents and the next city
 *    down, Belfast, has 9.
 *
 * 3. ONE EMPLOYER, OR ONE SOURCE, IS NOT A MARKET. Belfast's 9 documents are
 *    nine annual filings from a SINGLE employer, and a page titled "signals in
 *    Belfast" built out of one employer's filings is a company profile wearing a
 *    city's name. Neither of these two bars is what excludes anything today, and
 *    saying so is the point: they are the guard that stops a cell crossing the
 *    document bar on one employer's filing history, which is precisely how
 *    Belfast would qualify if the document bar were the only one.
 *
 * The resulting set today is 4 countries, 10 cities and 18 industries. It is
 * small because coverage is small, and a bigger set of thinner pages would take
 * the strong ones down with it: a programmatic set is judged as a set.
 */
const TIT_PLACE_MIN_DOCS = 25;       // distinct source documents
const TIT_PLACE_MIN_EMPLOYERS = 3;   // distinct employers behind them
const TIT_PLACE_MIN_SOURCES = 2;     // distinct sources behind them

/**
 * When one source is so much of a cell that the count means filing volume
 * rather than market activity, the page says so. Two thirds, matching
 * tit_place_caveat()'s bar on the dashboard, so the two cannot disagree about
 * what "dominated" means.
 */
const TIT_PLACE_ONE_SOURCE_SHARE = 0.66;

/** How many recent updates a page prints. Also the length of its JSON-LD list. */
const TIT_PLACE_RECENT = 12;

/** How many employers the page names, and how many cross-links it offers. */
const TIT_PLACE_TOP_EMPLOYERS = 8;

/*
 * WHAT ONE COLD PAGE RENDER COSTS THE DATABASE, as a number rather than as an
 * intention.
 *
 * The owner asked for these pages to be fast on desktop and on mobile, and the
 * only version of that claim a test can hold is a bound on the work a render
 * does. So it is a constant, and tests/php/render_place_pages.php asserts the
 * EXACT figure against a real SQLite database, including against the same cell
 * after its row count is tripled. A query added inside a loop over rows fails
 * there instead of on the live site under a crawl.
 *
 *   1  the index of cells for this kind          (tit_place_index)
 *   1  every scalar the page prints, in one pass (counts, span, money, mix)
 *   1  the employers we hold the most on
 *   1  the largest single source, for the caveat
 *   1  the recent updates, with a LIMIT
 *   1  the cross-link counts, with a LIMIT
 *   1  the index of cells for the kind it cross-links to
 *
 * Two more are paid the FIRST time anything in a process needs company.php's
 * slug index, which is how an employer whose profile URL is not servable gets
 * printed without a link instead of linked at a 404. They are not counted here
 * because they are not this page's: the index is memoised for the request and
 * shared with the dashboard, which builds it too.
 *
 * A warm render costs NONE of them: the five page queries are cached as one
 * bundle and each index as one entry.
 */
const TIT_PLACE_QUERY_BUDGET = 7;

/**
 * The per-request memos, in one place so there is one place to clear them.
 *
 * WordPress serves one page per process, so nothing in the plugin ever needs to
 * forget them, and nothing does. tests/php/render_place_pages.php serves many
 * pages in one process, because a query-count assertion is meaningless unless
 * the same code path can be run cold twice, and it calls tit_place_forget()
 * between them. Statics inside the functions would make that impossible and the
 * budget unprovable.
 */
function &tit_place_memo() {
    if (!isset($GLOBALS['tit_place_memo'])) {
        $GLOBALS['tit_place_memo'] = array(
            'current' => null, 'index' => array(), 'facts' => array());
    }
    return $GLOBALS['tit_place_memo'];
}

function tit_place_forget() {
    $GLOBALS['tit_place_memo'] = array(
        'current' => null, 'index' => array(), 'facts' => array());
}

/**
 * THE THREE PAGE TYPES, AS DATA.
 *
 * `where` is the membership test, and it is written in the SAME shape
 * tit_build_where() uses for the equivalent filter rather than as a COALESCE
 * comparison. That is not a style choice, it buys two things:
 *
 *  - the population is IDENTICAL to the one the dashboard shows for that
 *    filter, which it has to be, because this page links to that filtered
 *    dashboard and a reader who follows the link must not find a different
 *    number waiting;
 *  - it can use idx_geo / idx_hq / idx_industry. COALESCE(country, hq_country)
 *    on the left of a comparison cannot.
 *
 * `group` is the COALESCE form, used only for the GROUP BY that builds the
 * index of cells. Grouping is a scan either way, and the two forms select
 * exactly the same rows: COALESCE(a, b) = x is true precisely when
 * a = x OR (a IS NULL AND b = x). Verified against the live database for both
 * geography columns.
 */
function tit_place_kinds() {
    return array(
        'country' => array(
            'path'    => 'talent-intelligence-tracker/country',
            'var'     => 'tit_place',
            'group'   => 'COALESCE(country, hq_country)',
            'where'   => '(country = %s OR (country IS NULL AND hq_country = %s))',
            'args'    => 2,
            'param'   => 'country',   // the dashboard querystring key
            'one'     => 'country',
            'many'    => 'Countries',
            'schema'  => 'Place',
            // Which kind this one cross-links to, under what heading, and how
            // many. The heading names no place, so it stays grammatical for
            // every country in the list ("Cities In United Kingdom" does not).
            'cross'      => 'city',
            'cross_n'    => 8,
            'cross_head' => 'Cities In This Country',
            // "placed in" is right for a place and wrong for an industry, which
            // is a classification we made rather than something a source said.
            'lede'       => 'Every update we hold that a source placed in %1$s',
        ),
        'city' => array(
            'path'    => 'talent-intelligence-tracker/city',
            'var'     => 'tit_place',
            'group'   => 'COALESCE(city, hq_city)',
            'where'   => '(city = %s OR (city IS NULL AND hq_city = %s))',
            'args'    => 2,
            'param'   => 'city',
            'one'     => 'city',
            'many'    => 'Cities',
            'schema'  => 'Place',
            'cross'      => 'country',
            'cross_n'    => 3,
            'cross_head' => 'Countries This City Appears In',
            'lede'       => 'Every update we hold that a source placed in %1$s',
        ),
        'industry' => array(
            'path'    => 'talent-intelligence-tracker/industry',
            'var'     => 'tit_place',
            'group'   => 'industry',
            'where'   => 'industry = %s',
            'args'    => 1,
            'param'   => 'industry',
            'one'     => 'industry',
            'many'    => 'Industries',
            // An industry is not a Place. schema.org/Thing is the honest parent
            // for "a category of employer", and asserting Place for it would be
            // structured data that contradicts the page above it.
            'schema'  => 'Thing',
            'cross'      => 'country',
            'cross_n'    => 8,
            'cross_head' => 'Countries In This Industry',
            'lede'       => 'Every update we hold from an employer we have filed under %1$s',
        ),
    );
}

/**
 * The gate, as ONE predicate over three measured counts.
 *
 * The page, the directory and the sitemap all go through here, so a cell cannot
 * have a page that the sitemap omits, or appear in the directory without being
 * servable. company.php had to write the same predicate a second time as a SQL
 * HAVING clause because asking it in PHP would mean loading 7,301 employers'
 * counts on every sitemap request. There are 57 countries, 34 cities and 18
 * industries, so this file does NOT need that second copy, and not having it is
 * one fewer thing that can drift.
 */
function tit_place_meets_threshold($documents, $employers, $sources) {
    return (int) $documents >= TIT_PLACE_MIN_DOCS
        && (int) $employers >= TIT_PLACE_MIN_EMPLOYERS
        && (int) $sources   >= TIT_PLACE_MIN_SOURCES;
}

/**
 * key -> reader-facing name, for the kinds whose stored value is a code.
 *
 * NULL means the stored value IS the name, which is true only of cities. Both
 * maps are closed vocabularies shipped in this plugin, so a stored value absent
 * from one is a value we do not have a name for, and a cell we cannot name gets
 * no page rather than a page titled with a raw key.
 */
function tit_place_names($kind) {
    if ($kind === 'country') {
        return function_exists('tit_country_names') ? tit_country_names() : array();
    }
    if ($kind === 'industry') {
        return function_exists('tit_industry_labels') ? tit_industry_labels() : array();
    }
    return null;
}

/** The name we print for one cell, or '' when we have none for it. */
function tit_place_name($kind, $key) {
    $names = tit_place_names($kind);
    if ($names === null) return (string) $key;
    return isset($names[$key]) ? (string) $names[$key] : '';
}

/**
 * The canonical slug: the ASCII slug of the name we PRINT, through
 * company.php's tit_company_slug().
 *
 * One rule for all three types, and it is the rule that is already proven live:
 * plain [a-z0-9-], "&" transliterated to "and" rather than encoded (no encoding
 * of "&" survives both the rewrite and a sitemap), accents folded. So
 * "Pharma & biotech" is /industry/pharma-and-biotech/ and "United Kingdom" is
 * /country/united-kingdom/.
 *
 * SLUGGING THE NAME AND NOT THE STORED KEY is the one deliberate difference
 * from company.php, where the key IS the name. Here the stored value is a code:
 * /country/gb/ and /industry/pharma_biotech/ are URLs nobody searches for and
 * nobody can read. The name is the thing a reader and a search engine both
 * already have, and the map that recovers the code from it is a closed
 * vocabulary this plugin ships, so there is no lookup table to maintain.
 *
 * Returns '' when there is no publishable slug: a cell we have no name for, or
 * a name with nothing ASCII in it. Every caller treats '' as "no URL", so such
 * a cell is absent from the directory and from the sitemap rather than
 * appearing as /country//.
 */
function tit_place_slug($kind, $key) {
    $name = tit_place_name($kind, $key);
    if ($name === '' || !function_exists('tit_company_slug')) return '';
    $slug = tit_company_slug($name);
    // tit_company_slug() falls back to percent-encoding when nothing survives
    // canonicalisation. That is right for an employer, whose old URL has to keep
    // resolving; here there is no legacy URL to preserve, so anything that is
    // not plain ASCII simply has no page.
    return preg_match('/^[a-z0-9-]+$/', (string) $slug) ? $slug : '';
}

function tit_place_url($kind, $key) {
    $kinds = tit_place_kinds();
    $slug = tit_place_slug($kind, $key);
    if ($slug === '' || !isset($kinds[$kind])) return '';
    return home_url('/' . $kinds[$kind]['path'] . '/' . $slug . '/');
}

/**
 * The dashboard, filtered to this cell.
 *
 * Where a below-threshold cell is sent, and the "see every update" link on a
 * page that has one. The parameter names are the ones tit_build_where() reads
 * and dashboard.js hydrates from the querystring, so the link lands on a
 * filtered page rather than on an unfiltered one with a stray parameter.
 */
function tit_place_dashboard_url($kind, $key) {
    $kinds = tit_place_kinds();
    if (!isset($kinds[$kind])) return home_url('/talent-intelligence-tracker/');
    return add_query_arg(
        $kinds[$kind]['param'],
        (string) $key,
        home_url('/talent-intelligence-tracker/')
    );
}

function tit_places_url() {
    return home_url('/' . TIT_PLACES_PATH . '/');
}

function tit_places_sitemap_url() {
    return home_url('/' . TIT_PLACES_SITEMAP_PATH);
}

/*
 * ---------------------------------------------------------------------------
 * THE INDEX OF CELLS
 * ---------------------------------------------------------------------------
 *
 * ONE query per kind answers four questions at once: which slugs exist, which
 * of them clear the gate, what the directory lists, and what the sitemap
 * contains. 57, 34 and 18 rows respectively, so it is cheap enough to be the
 * single source for all four rather than four queries that can disagree.
 *
 * COLLISIONS ARE REFUSED, NOT RESOLVED, exactly as in company.php. Two stored
 * values whose names canonicalise to one slug ("St. Louis" and "St Louis" would)
 * are one cell recorded twice, and serving either under the shared URL would
 * silently show half of it. So neither is served and neither is published. The
 * fix is upstream, in the pipeline's normalisation, and the refusal is what
 * makes an un-normalised pair harmless in the meantime.
 */
function tit_place_index($kind) {
    $memo = &tit_place_memo();
    if (isset($memo['index'][$kind])) return $memo['index'][$kind];

    $kinds = tit_place_kinds();
    if (!isset($kinds[$kind])) return array('cells' => array(), 'collisions' => array());

    $cache_key = 'tit_place_index_' . $kind . '_' . md5(TIT_VERSION);
    $cached = get_transient($cache_key);
    if (is_array($cached) && isset($cached['cells'])) {
        $memo['index'][$kind] = $cached;
        return $cached;
    }

    global $wpdb;
    $table = tit_table_name();
    $expr = $kinds[$kind]['group'];

    $rows = $wpdb->get_results(
        "SELECT {$expr} AS k,
                COUNT(*) AS records,
                COUNT(DISTINCT source_url) AS documents,
                COUNT(DISTINCT company_key) AS employers,
                COUNT(DISTINCT source_name) AS sources,
                MAX(COALESCE(published_date, DATE(captured_at))) AS lastmod
           FROM {$table}
          WHERE is_current = 1 AND {$expr} IS NOT NULL AND {$expr} <> ''
          GROUP BY k",
        ARRAY_A
    );
    $rows = is_array($rows) ? $rows : array();

    // Claims first, so a slug two keys want is known before either is stored.
    $claims = array();
    foreach ($rows as $row) {
        $slug = tit_place_slug($kind, $row['k']);
        if ($slug === '') continue;
        $claims[$slug][] = $row;
    }

    $cells = array();
    $collisions = array();
    foreach ($claims as $slug => $owners) {
        if (count($owners) > 1) {
            $collisions[$slug] = true;
            continue;
        }
        $row = $owners[0];
        $cells[$slug] = array(
            'key'       => (string) $row['k'],
            'name'      => tit_place_name($kind, $row['k']),
            'records'   => (int) $row['records'],
            'documents' => (int) $row['documents'],
            'employers' => (int) $row['employers'],
            'sources'   => (int) $row['sources'],
            'lastmod'   => (string) $row['lastmod'],
            'gated'     => tit_place_meets_threshold(
                $row['documents'], $row['employers'], $row['sources']),
        );
    }

    $index = array('cells' => $cells, 'collisions' => $collisions);

    // An index built while company.php had not yet uploaded has no slugs at
    // all, because tit_place_slug() needs its canonicaliser. Caching that would
    // keep every one of these pages 404ing for the rest of the TTL after the
    // deploy had finished, and FTP lands files one at a time.
    if (function_exists('tit_company_slug')) {
        set_transient($cache_key, $index, tit_place_ttl());
    }
    $memo['index'][$kind] = $index;
    return $index;
}

/**
 * One TTL for everything on these pages, and it is the API's.
 *
 * Five minutes, so a collect run's new rows show up on the next request rather
 * than on the next hour. It is a ceiling and not the normal path:
 * tit_flush_caches() drops every tit_ transient on any write, so the ordinary
 * case is already immediate, and this only bounds the damage when something
 * writes without flushing.
 */
function tit_place_ttl() {
    return defined('TIT_CACHE_TTL') ? TIT_CACHE_TTL : 300;
}

/** The gated cells of one kind, biggest first, for the directory and the sitemap. */
function tit_place_published($kind) {
    $cells = tit_place_index($kind)['cells'];
    $cells = array_filter($cells, function ($c) { return $c['gated']; });
    uasort($cells, function ($a, $b) {
        return $b['records'] <=> $a['records'] ?: strcmp($a['name'], $b['name']);
    });
    return $cells;
}

/*
 * ---------------------------------------------------------------------------
 * THE AGGREGATES
 * ---------------------------------------------------------------------------
 *
 * A BOUNDED NUMBER OF QUERIES PER PAGE, and the bound is structural rather than
 * a target: nothing below runs inside a loop over rows, and every figure on the
 * page comes out of this one function. Five queries here, plus the cells index
 * for this kind, plus the cells index for the kind it cross-links to, plus
 * company.php's slug index. Eight on a cold render, none on a warm one, for a
 * page of any size.
 *
 * The whole bundle is cached under one key, so the eight are paid once per five
 * minutes per page and not once per reader.
 */
function tit_place_facts($kind, $key) {
    // The title filter, the head tags and the body each ask for this, at three
    // different points in the request. Once per request, not three times.
    $memo = &tit_place_memo();
    $memo_key = $kind . '|' . $key;
    if (isset($memo['facts'][$memo_key])) return $memo['facts'][$memo_key];

    /*
     * The cache key carries TIT_VERSION, and that is about SHAPE rather than
     * freshness. A deploy that adds a figure to this array would otherwise read
     * a cached array from the previous version that does not contain it, and the
     * page would print a zero for a fact it holds. Freshness is
     * tit_flush_caches(), which drops every tit_ transient on any write, plus
     * the five-minute ceiling as a backstop.
     */
    $cache_key = 'tit_place_facts_' . md5($kind . '|' . $key . '|' . TIT_VERSION);
    $cached = get_transient($cache_key);
    if (is_array($cached)) {
        $memo['facts'][$memo_key] = $cached;
        return $cached;
    }

    global $wpdb;
    $table = tit_table_name();
    $kinds = tit_place_kinds();
    $spec = $kinds[$kind];

    // The membership clause and its parameters, built once and reused by every
    // query below. $args is 2 for the geography kinds because the clause names
    // the column twice; writing the count down beside the clause is what stops
    // the two drifting.
    $where = 'is_current = 1 AND ' . $spec['where'];
    $args = array_fill(0, (int) $spec['args'], (string) $key);

    $funding = function_exists('tit_funding_where')
        ? tit_funding_where()
        : "((funding_amount IS NOT NULL AND funding_amount <> '')"
          . " OR (funding_stage IS NOT NULL AND funding_stage <> ''))";

    // --- 1. every scalar the page prints, in one pass -----------------------
    // Counts, the date span, the pillar mix and the money all read the same
    // rows, so they are one query. Two queries here would let the headline
    // count and the mix under it describe different sets.
    $scalars = $wpdb->get_row($wpdb->prepare(
        "SELECT COUNT(*) AS records,
                COUNT(DISTINCT company_key) AS employers,
                COUNT(DISTINCT source_name) AS sources,
                COUNT(DISTINCT source_url) AS documents,
                SUM(confidence = 'verified') AS verified,
                SUM({$funding}) AS funding_rows,
                SUM(funding_amount_usd IS NOT NULL) AS funding_with_usd,
                COALESCE(SUM(funding_amount_usd), 0) AS funding_usd,
                SUM(pillar = 'company_development') AS p_company_development,
                SUM(pillar = 'leadership_change') AS p_leadership_change,
                SUM(pillar = 'rewards_comp') AS p_rewards_comp,
                SUM(pillar = 'how_we_work') AS p_how_we_work,
                MIN(COALESCE(published_date, DATE(captured_at))) AS lo,
                MAX(COALESCE(published_date, DATE(captured_at))) AS hi
           FROM {$table} WHERE {$where}",
        $args
    ), ARRAY_A) ?: array();

    /*
     * --- 2. the employers we hold the most on ------------------------------
     *
     * MAX(company) rather than any company, so the display name is the same on
     * every render: one employer key can carry several spellings.
     *
     * THE TIE-BREAK IS THE WHOLE ORDERING, and getting it wrong made this list
     * useless the first time. On the United Kingdom every large employer files
     * the gender pay gap return once a year, so several hundred of them sit at
     * exactly nine updates, and a plain "most updates, then alphabetical" list
     * printed AIRBUS, ALDI, ALLIANCE, AMAZON, ARGOS, ARNOLD CLARK, ASDA,
     * ASTRAZENECA: the alphabetically first eight of a tie, dressed up as a
     * ranking. So a tie is broken by how many INDEPENDENT SOURCES are behind
     * those updates, then by which employer we heard from most recently. Both
     * are facts about the evidence, and both put an employer a reader has a
     * reason to click ahead of one whose name starts with A.
     */
    $employers = $wpdb->get_results($wpdb->prepare(
        "SELECT company_key, MAX(company) AS company, COUNT(*) AS n,
                COUNT(DISTINCT source_name) AS kinds,
                MAX(COALESCE(published_date, DATE(captured_at))) AS latest
           FROM {$table} WHERE {$where}
          GROUP BY company_key
          ORDER BY n DESC, kinds DESC, latest DESC, company_key ASC
          LIMIT " . (int) TIT_PLACE_TOP_EMPLOYERS,
        $args
    ), ARRAY_A) ?: array();

    // --- 3. the biggest single source, for the concentration note ----------
    $top_source = $wpdb->get_row($wpdb->prepare(
        "SELECT source_name, COUNT(*) AS n
           FROM {$table} WHERE {$where}
          GROUP BY source_name ORDER BY n DESC, source_name ASC LIMIT 1",
        $args
    ), ARRAY_A) ?: array();

    // --- 4. recent updates -------------------------------------------------
    // The SAME sort /query defaults to, so this list and the dashboard filtered
    // to the same cell put the same update first. A page that reorders the
    // rows it links to is a page that disagrees with the product it is part of.
    $recent = $wpdb->get_results($wpdb->prepare(
        "SELECT headline, talent_readthrough, company, company_key, pillar,
                signal_direction, city, country, hq_city, hq_country, headcount,
                funding_amount, confidence, source_url, source_name, archive_url,
                published_date, captured_at
           FROM {$table} WHERE {$where}
          ORDER BY CASE materiality WHEN 'high' THEN 0 WHEN 'medium' THEN 1
                                    WHEN 'routine' THEN 3 ELSE 2 END ASC,
                   COALESCE(published_date, DATE(captured_at)) DESC, row_id DESC
          LIMIT " . (int) TIT_PLACE_RECENT,
        $args
    ), ARRAY_A) ?: array();

    // --- 5. the cross-links ------------------------------------------------
    // A country lists its cities, a city and an industry list their countries.
    // Resolved to hrefs HERE rather than at render time, so the cached bundle
    // is what the page prints and the target kind's index is consulted once.
    $cross_kind = $spec['cross'];
    $cross_expr = $kinds[$cross_kind]['group'];
    $cross_rows = $wpdb->get_results($wpdb->prepare(
        "SELECT {$cross_expr} AS k, COUNT(*) AS n
           FROM {$table}
          WHERE {$where} AND {$cross_expr} IS NOT NULL AND {$cross_expr} <> ''
          GROUP BY k ORDER BY n DESC, k ASC
          LIMIT " . (int) $spec['cross_n'],
        $args
    ), ARRAY_A) ?: array();

    $facts = array(
        'records'   => (int) ($scalars['records'] ?? 0),
        'employers' => (int) ($scalars['employers'] ?? 0),
        'sources'   => (int) ($scalars['sources'] ?? 0),
        'documents' => (int) ($scalars['documents'] ?? 0),
        'verified'  => (int) ($scalars['verified'] ?? 0),
        'funding'   => array(
            'rows'  => (int) ($scalars['funding_rows'] ?? 0),
            'with'  => (int) ($scalars['funding_with_usd'] ?? 0),
            'usd'   => (float) ($scalars['funding_usd'] ?? 0),
        ),
        'span'      => array('lo' => (string) ($scalars['lo'] ?? ''),
                             'hi' => (string) ($scalars['hi'] ?? '')),
        'pillars'   => array(
            'company_development' => (int) ($scalars['p_company_development'] ?? 0),
            'leadership_change'   => (int) ($scalars['p_leadership_change'] ?? 0),
            'rewards_comp'        => (int) ($scalars['p_rewards_comp'] ?? 0),
            'how_we_work'         => (int) ($scalars['p_how_we_work'] ?? 0),
        ),
        'top_employers' => tit_place_employer_links($employers),
        'top_source'    => array(
            'name' => (string) ($top_source['source_name'] ?? ''),
            'n'    => (int) ($top_source['n'] ?? 0),
        ),
        'recent'    => $recent,
        'cross'     => tit_place_cross_links($cross_kind, $cross_rows),
        'cross_kind' => $cross_kind,
    );

    set_transient($cache_key, $facts, tit_place_ttl());
    $memo['facts'][$memo_key] = $facts;
    return $facts;
}

/**
 * Employer rows turned into name plus URL, refusing the ones company.php
 * refuses.
 *
 * A collided or unslugable employer key has no servable profile URL, and
 * linking an indexable page at a URL that 404s is worse than printing the name
 * without a link. This is the same single decision company.php makes, called
 * rather than reimplemented, so the two can only ever agree.
 */
function tit_place_employer_links(array $rows) {
    $out = array();
    foreach ($rows as $row) {
        $key = (string) $row['company_key'];
        $servable = function_exists('tit_company_servable_slug')
            ? tit_company_servable_slug($key) : false;
        $out[] = array(
            'name' => (string) $row['company'],
            'n'    => (int) $row['n'],
            'url'  => ($servable && function_exists('tit_company_url'))
                      ? tit_company_url($key) : '',
        );
    }
    return $out;
}

/**
 * Cross-link rows turned into a label and a destination.
 *
 * A target cell that has its own page is linked to it. One that does not is
 * linked to the dashboard filtered to it, which is the same destination a
 * below-threshold URL would have redirected to, so a reader following either
 * route lands in the same place. Nothing is ever printed as a dead label: the
 * count is real either way and the reader can always see the rows behind it.
 */
function tit_place_cross_links($kind, array $rows) {
    $index = tit_place_index($kind);
    $out = array();
    foreach ($rows as $row) {
        $key = (string) $row['k'];
        $slug = tit_place_slug($kind, $key);
        $cell = ($slug !== '' && isset($index['cells'][$slug])) ? $index['cells'][$slug] : null;
        $name = tit_place_name($kind, $key);
        if ($name === '') continue;   // a value we have no name for is not a link
        $out[] = array(
            'key'   => $key,
            'name'  => $name,
            'n'     => (int) $row['n'],
            'url'   => ($cell && $cell['gated'])
                       ? tit_place_url($kind, $key)
                       : tit_place_dashboard_url($kind, $key),
            'page'  => (bool) ($cell && $cell['gated']),
        );
    }
    return $out;
}

/*
 * ---------------------------------------------------------------------------
 * ROUTING
 * ---------------------------------------------------------------------------
 */
function tit_places_rewrite() {
    foreach (tit_place_kinds() as $kind => $spec) {
        add_rewrite_rule(
            '^' . $spec['path'] . '/([^/]+)/?$',
            'index.php?tit_place_kind=' . $kind . '&tit_place=$matches[1]',
            'top'
        );
    }
    add_rewrite_rule('^' . TIT_PLACES_PATH . '/?$', 'index.php?tit_places=1', 'top');
    // A sibling route rather than a child of any of the three, so no cell rule
    // can swallow it. Only the dot needs escaping, and it is escaped rather
    // than left as "any character" so nothing else can match.
    add_rewrite_rule(
        '^' . str_replace('.', '\.', TIT_PLACES_SITEMAP_PATH) . '$',
        'index.php?tit_places_sitemap=1',
        'top'
    );
}
add_action('init', 'tit_places_rewrite');

/**
 * These pages ship NO JavaScript, and this is where that is stated.
 *
 * Nothing on a place page or the directory needs it: there is no filter panel,
 * no chart that repaints and no list that pages. The bars under "What The
 * Updates Are About" are inline widths computed in PHP. So the stylesheet is
 * enqueued and dashboard.js is not, which is one fewer request and no parse
 * cost on the pages a stranger arrives at first.
 *
 * Not an absence of a feature: if one of these pages ever needs a script it
 * flips this to true, and the page still has to work without it.
 */
function tit_places_needs_no_js($needs) {
    if (get_query_var('tit_place') || get_query_var('tit_places')) return false;
    return $needs;
}
add_filter('tit_route_needs_js', 'tit_places_needs_no_js');

function tit_places_query_vars($vars) {
    $vars[] = 'tit_place';
    $vars[] = 'tit_place_kind';
    $vars[] = 'tit_places';
    $vars[] = 'tit_places_sitemap';
    return $vars;
}
add_filter('query_vars', 'tit_places_query_vars');

/**
 * Rewrite rules live in the database and an FTP deploy runs no activation hook,
 * so they have to be flushed once per version.
 *
 * company.php already owns that flush, on init at priority 99, and
 * flush_rewrite_rules() regenerates from EVERY rule registered by then. The
 * rules above are added at the default priority 10, so they are included and
 * there is nothing to do here. This exists only for the deploy window where
 * company.php has not uploaded yet: without it these routes would 404 until it
 * did, and the version option would already have been written.
 */
function tit_places_maybe_flush() {
    if (function_exists('tit_company_maybe_flush')) return;
    if (get_option('tit_places_rewrites_version') === TIT_VERSION) return;
    tit_places_rewrite();
    flush_rewrite_rules(false);
    update_option('tit_places_rewrites_version', TIT_VERSION, false);
}
add_action('init', 'tit_places_maybe_flush', 99);

/**
 * The cell being rendered, resolved once.
 *
 * The title filter, the head tags and the body all need it and they run at
 * three different points in the request.
 */
function tit_place_current() {
    $memo = &tit_place_memo();
    if ($memo['current'] !== null) return $memo['current'] ?: null;

    $kind = sanitize_text_field((string) get_query_var('tit_place_kind'));
    $requested = sanitize_text_field(rawurldecode((string) get_query_var('tit_place')));
    $kinds = tit_place_kinds();
    if ($kind === '' || $requested === '' || !isset($kinds[$kind])) {
        $memo['current'] = false;
        return null;
    }

    // Looked up lower-cased, so /country/United-Kingdom/ resolves and then 301s
    // to the canonical form rather than 404ing on a capital letter somebody
    // typed. The form that was ASKED FOR is kept, because that is what the
    // redirect comparison has to be made against: comparing the canonical slug
    // to the lower-cased one would always be equal and the redirect would never
    // fire, leaving the reader on a URL whose own canonical tag disowns it.
    $cells = tit_place_index($kind)['cells'];
    $slug = strtolower($requested);
    if (!isset($cells[$slug])) {
        $memo['current'] = false;
        return null;
    }

    $memo['current'] = array(
        'kind'      => $kind,
        'slug'      => $slug,
        'requested' => $requested,
        'cell'      => $cells[$slug],
    );
    return $memo['current'];
}

/**
 * WHAT A REQUEST FOR ONE OF THESE URLS ANSWERS, as a decision with no side
 * effects.
 *
 * Separated from tit_place_template() below because there are four answers, the
 * difference between three of them is a status code, and a status code across a
 * change of state is exactly what reading the source cannot settle:
 * company.php shipped 22 broken sitemap URLs on 1.45.4 with source that read
 * correctly. This returns the decision so tests/php/render_place_pages.php can
 * run every one of the four against real SQL; the template below does nothing
 * but carry it out.
 *
 *   404      a cell we hold nothing for, a slug two cells claim, or a value we
 *            have no name for. An empty page for every possible slug is the
 *            doorway-page pattern.
 *   301      a slug that resolved but is not the canonical form, so a cell is
 *            never indexable at two addresses. Cannot loop: the canonical slug
 *            is what the index is keyed by, so it compares equal next time.
 *   302      BELOW THE BAR THERE IS NO PAGE. Not a thin page marked noindex,
 *            which is the choice company.php makes for a reason that does not
 *            apply here: the dashboard table links every employer to its
 *            profile, so 404ing a thin employer would break an internal link a
 *            recruiter has just clicked. NOTHING links to a below-threshold
 *            cell, and for a reader who typed the URL the dashboard filtered to
 *            the same cell is strictly better: it exists, it shows every row we
 *            hold, and it is the page this one would have summarised.
 *
 *            302 and not 301, against the obvious reading. A cell below the bar
 *            today is one that crosses it as coverage grows, which is the whole
 *            design: pages appear by themselves. A permanent redirect tells
 *            every crawler and browser never to ask again, so the day Israel
 *            crosses 25 documents its page would exist and nothing would come
 *            back for it. The redirect is temporary because the STATE is
 *            temporary, and saying so in the status code is the difference
 *            between a correct answer and a convenient one.
 *   render   a cell with a page.
 */
function tit_place_route($current) {
    if (!$current) return array('action' => '404');

    $kind = $current['kind'];
    $cell = $current['cell'];

    $canonical = tit_place_slug($kind, $cell['key']);
    if ($canonical !== '' && $canonical !== $current['requested']) {
        return array('action' => 'redirect', 'code' => 301,
                     'url' => tit_place_url($kind, $cell['key']));
    }
    if (!$cell['gated']) {
        return array('action' => 'redirect', 'code' => 302,
                     'url' => tit_place_dashboard_url($kind, $cell['key']));
    }
    return array('action' => 'render');
}

function tit_place_template() {
    if (!get_query_var('tit_place')) return;

    $current = tit_place_current();
    $route = tit_place_route($current);

    if ($route['action'] === '404') {
        status_header(404);
        nocache_headers();
        include get_404_template();
        exit;
    }
    if ($route['action'] === 'redirect') {
        wp_safe_redirect($route['url'], $route['code']);
        exit;
    }

    tit_place_render($current['kind'], $current['cell'],
                     tit_place_facts($current['kind'], $current['cell']['key']));
    exit;
}
add_action('template_redirect', 'tit_place_template');

/*
 * ---------------------------------------------------------------------------
 * THE PAGE
 * ---------------------------------------------------------------------------
 */

/**
 * The heading, built once so the h1, the title tag and the crumb agree.
 *
 * NAME FIRST, then the colon. "Hiring And Funding Signals In United States" is
 * not a sentence in English, and the fix is not to work out where the article
 * goes, it is to write a heading that does not need one. It also puts the word
 * a reader searched for at the front.
 */
function tit_place_heading($kind, $cell) {
    return sprintf('%s: Hiring, Funding And Leadership Signals', $cell['name']);
}

/**
 * "the ", where English wants it before a country name, and '' otherwise.
 *
 * A PATTERN AND NOT A LIST, on purpose. English takes the article before a
 * country name that is a plural or a compound containing a common noun ("the
 * United Kingdom", "the Netherlands", "the Cayman Islands") and not before a
 * simple proper noun ("France", "Israel"). tit_country_names() is the whole of
 * ISO 3166-1 and grows, and a hardcoded list of exceptions is exactly how "PR"
 * reached a live chart as a bare code. The pattern covers the common nouns those
 * names are built from, so a country added later is handled by the same rule.
 *
 * Wrong in one direction only: an unmatched name gets no article, which reads as
 * a slightly terse sentence rather than as a mistake.
 */
function tit_place_the($kind, $name) {
    if ($kind !== 'country') return '';
    return preg_match(
        '/\b(Kingdom|States|Republic|Emirates|Islands|Federation|Territories|'
        . 'Netherlands|Philippines|Bahamas|Maldives|Gambia|Comoros|Seychelles|'
        . 'Congo|Sudan|Vatican|Ivoire)\b/',
        (string) $name
    ) ? 'the ' : '';
}

/**
 * The stats strip. Only what we hold: a tile reading "0" is not a fact, it is
 * an empty slot, and four of those make a page look broken.
 */
function tit_place_stat_tiles($facts) {
    $tiles = array();
    $tiles[] = array(
        number_format_i18n($facts['records']),
        $facts['records'] === 1 ? 'update tracked' : 'updates tracked',
    );
    $tiles[] = array(
        number_format_i18n($facts['employers']),
        $facts['employers'] === 1 ? 'employer' : 'employers',
    );
    $tiles[] = array(
        number_format_i18n($facts['sources']),
        $facts['sources'] === 1 ? 'source' : 'sources',
    );
    if ($facts['funding']['usd'] > 0 && function_exists('tit_money_short')) {
        $tiles[] = array(tit_money_short($facts['funding']['usd']), 'disclosed funding');
    } elseif ($facts['verified'] > 0) {
        $tiles[] = array(number_format_i18n($facts['verified']), 'from official filings');
    }
    return $tiles;
}

/**
 * The one caveat this page is required to carry when it is true, and to leave
 * out when it is not.
 *
 * The model is the United Kingdom: 4,761 of its 4,808 rows are the gender pay
 * gap filing, one mandatory annual return that every large employer files. A
 * reader scanning a count that size would take it as a measure of how much is
 * happening there. Computed on render, never written down, so it names whichever
 * source currently dominates and disappears the day none does.
 */
function tit_place_source_note($cell, $facts) {
    $n = (int) $facts['top_source']['n'];
    $total = (int) $facts['records'];
    $name = (string) $facts['top_source']['name'];
    if ($name === '' || $total <= 0) return '';
    if ($n < $total * TIT_PLACE_ONE_SOURCE_SHARE) return '';

    return sprintf(
        '%1$s of the %2$s updates on this page (%3$s%%) come from one source, %4$s.'
        . ' Read the count as filing volume rather than as how much is happening'
        . ' there.',
        number_format_i18n($n),
        number_format_i18n($total),
        number_format_i18n((int) round(100 * $n / max(1, $total))),
        $name
    );
}

/**
 * The caveat a CITY page needs and the other two do not.
 *
 * A city name is not unique in the world and we store the name a source
 * printed, with no country qualifier of its own. London holds 1,339 rows placed
 * in the United Kingdom and 5 that name a city of the same name elsewhere;
 * Toronto holds 25 Canadian and 5 that are not. Saying which country the page is
 * mostly about, and how many rows are not, is the honest version of a heading
 * that just says "London".
 */
function tit_place_city_note($kind, $cell, $facts) {
    if ($kind !== 'city' || $facts['cross_kind'] !== 'country') return '';
    $cross = $facts['cross'];
    if (!$cross) return '';

    $lead = $cross[0];
    $others = (int) $facts['records'] - (int) $lead['n'];
    $note = sprintf(
        'Updates are grouped by the city name a source printed. %1$s of the %2$s'
        . ' here are placed in %3$s.',
        number_format_i18n((int) $lead['n']),
        number_format_i18n((int) $facts['records']),
        tit_place_the('country', $lead['name']) . $lead['name']
    );
    if ($others > 0) {
        $note .= ' ' . sprintf(
            _n('%s names a place of the same name in another country.',
               '%s name a place of the same name in another country.', $others, 'tit'),
            number_format_i18n($others)
        );
    }
    return $note;
}

function tit_place_render($kind, $cell, $facts) {
    $kinds = tit_place_kinds();
    $spec = $kinds[$kind];
    $heading = tit_place_heading($kind, $cell);
    $tiles = tit_place_stat_tiles($facts);
    $pillar_labels = function_exists('tit_company_pillar_labels')
        ? tit_company_pillar_labels() : array();
    $direction_labels = function_exists('tit_company_direction_labels')
        ? tit_company_direction_labels() : array();
    $conf_labels = function_exists('tit_confidence_labels') ? tit_confidence_labels() : array();
    $dashboard = tit_place_dashboard_url($kind, $cell['key']);

    // Block theme, so never get_header(): see tit_render_header(). These are the
    // pages a stranger arrives at first, and the classic call ships them with no
    // logo and no navigation.
    if (function_exists('tit_render_header')) tit_render_header(); else get_header();
    ?>
    <div class="tit-wrap tit-place">
      <nav class="tit-crumb">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Talent Intelligence Tracker</a>
        <span aria-hidden="true">&rsaquo;</span>
        <a href="<?php echo esc_url(tit_places_url()); ?>"><?php echo esc_html($spec['many']); ?></a>
        <span aria-hidden="true">&rsaquo;</span> <?php echo esc_html($cell['name']); ?>
      </nav>

      <h1><?php echo esc_html($heading); ?></h1>

      <?php
      /*
        The lede says what the page IS and what its numbers MEAN, in that order,
        because the second is the part a reader cannot infer. A count here is a
        count of documents we have read and can link to, which is not the same
        as a count of things that happened, and a page of numbers that does not
        say so is inviting the wrong reading.
      */
      ?>
      <p class="tit-note">
        <?php printf(
          esc_html($spec['lede'] . ': %2$s from %3$s, across %4$s.'),
          esc_html(tit_place_the($kind, $cell['name']) . $cell['name']),
          esc_html(sprintf(_n('%s update', '%s updates', $facts['records'], 'tit'),
                           number_format_i18n($facts['records']))),
          esc_html(sprintf(_n('%s source', '%s sources', $facts['sources'], 'tit'),
                           number_format_i18n($facts['sources']))),
          esc_html(sprintf(_n('%s employer', '%s employers', $facts['employers'], 'tit'),
                           number_format_i18n($facts['employers'])))
        ); ?>
        Each one links to the filing or report that makes the claim. These are
        counts of documents we have read, so they measure what we have found and
        can cite, not everything that happened.
      </p>

      <?php if (function_exists('tit_span_note')) :
        $span_note = tit_span_note($facts['span']['lo'], $facts['span']['hi']);
        if ($span_note) : ?>
          <p class="tit-span"><?php echo esc_html($span_note); ?></p>
      <?php endif; endif; ?>

      <div class="tit-stats tit-stats-<?php echo count($tiles); ?>">
        <?php foreach ($tiles as [$n, $label]) : ?>
          <div class="tit-stat">
            <span class="tit-n<?php echo is_string($n) && strlen($n) > 3 ? ' tit-n-word' : ''; ?>"><?php echo esc_html($n); ?></span>
            <span class="tit-l"><?php echo esc_html($label); ?></span>
          </div>
        <?php endforeach; ?>
      </div>

      <?php
      // Money gets its coverage sentence or it does not get printed. Only some
      // funding updates carry a US dollar figure, and a total shown as if it
      // covered every round is the plausible-but-wrong number this product
      // cannot carry.
      if ($facts['funding']['usd'] > 0 && function_exists('tit_money_coverage_sentence')) : ?>
        <p class="tit-note">
          <strong><?php echo esc_html(function_exists('tit_money_full')
              ? tit_money_full($facts['funding']['usd'])
              : (string) $facts['funding']['usd']); ?></strong>
          disclosed across <?php echo esc_html(sprintf(
              _n('%s funding update', '%s funding updates', $facts['funding']['rows'], 'tit'),
              number_format_i18n($facts['funding']['rows']))); ?>.
          <?php echo esc_html(tit_money_coverage_sentence(array(
              'with' => $facts['funding']['with'],
              'all'  => max($facts['funding']['with'], $facts['funding']['rows']),
          ))); ?>
        </p>
      <?php endif; ?>

      <?php $city_note = tit_place_city_note($kind, $cell, $facts);
            if ($city_note) : ?>
        <div class="tit-callout"><?php echo esc_html($city_note); ?></div>
      <?php endif; ?>

      <?php $source_note = tit_place_source_note($cell, $facts);
            if ($source_note) : ?>
        <div class="tit-callout"><strong>One source dominates this count.</strong>
          <?php echo esc_html($source_note); ?></div>
      <?php endif; ?>

      <?php if ($facts['top_employers']) : ?>
        <h2 class="tit-place-h2">Employers We Hold The Most On</h2>
        <p class="tit-note">
          Ranked by how many updates we hold, then by how many independent
          sources are behind them. That is a measure of our own coverage rather
          than of an employer's size. Each name links to everything we hold for
          that employer.
        </p>
        <ul class="tit-place-list">
          <?php foreach ($facts['top_employers'] as $employer) : ?>
            <li>
              <?php if ($employer['url']) : ?>
                <a href="<?php echo esc_url($employer['url']); ?>"><?php echo esc_html($employer['name']); ?></a>
              <?php else : ?>
                <?php echo esc_html($employer['name']); ?>
              <?php endif; ?>
              <span class="tit-place-n"><?php echo esc_html(sprintf(
                _n('%s update', '%s updates', $employer['n'], 'tit'),
                number_format_i18n($employer['n']))); ?></span>
            </li>
          <?php endforeach; ?>
        </ul>
      <?php endif; ?>

      <?php
      // What the updates are about, using the same pillar wording as the
      // dashboard and the employer profiles. Bars are drawn from the counts, so
      // there is nothing to keep in step and no JavaScript involved.
      $pillars = array_filter($facts['pillars']);
      if ($pillars) : arsort($pillars); $pillar_max = max($pillars); ?>
        <h2 class="tit-place-h2">What The Updates Are About</h2>
        <div class="tit-pillars">
          <?php foreach ($pillars as $pillar => $n) : ?>
            <div class="tit-pillar">
              <div class="tit-pillar-head">
                <span class="tit-pillar-name"><?php
                  echo esc_html($pillar_labels[$pillar] ?? str_replace('_', ' ', $pillar)); ?></span>
                <span class="tit-pillar-n"><?php echo esc_html(number_format_i18n($n)); ?></span>
              </div>
              <div class="tit-bar"><span style="width:<?php
                echo esc_attr(round(100 * $n / max(1, $pillar_max), 1)); ?>%"></span></div>
            </div>
          <?php endforeach; ?>
        </div>
      <?php endif; ?>

      <?php if ($facts['cross']) : ?>
        <h2 class="tit-place-h2"><?php echo esc_html($spec['cross_head']); ?></h2>
        <p class="tit-note">
          A name with a page of its own links to it. One we do not hold enough on
          for a page of its own links to the tracker filtered to it, which shows
          every update behind the count.
        </p>
        <ul class="tit-place-list">
          <?php foreach ($facts['cross'] as $link) : ?>
            <li>
              <a href="<?php echo esc_url($link['url']); ?>"><?php echo esc_html($link['name']); ?></a>
              <span class="tit-place-n"><?php echo esc_html(sprintf(
                _n('%s update', '%s updates', $link['n'], 'tit'),
                number_format_i18n($link['n']))); ?></span>
            </li>
          <?php endforeach; ?>
        </ul>
      <?php endif; ?>

      <h2 class="tit-place-h2">Recent Updates</h2>
      <p class="tit-note">
        The <?php echo esc_html(number_format_i18n(count($facts['recent']))); ?>
        most recent, in the order the tracker itself uses. Every line links to
        the document it came from.
        <a href="<?php echo esc_url($dashboard); ?>">See all
        <?php echo esc_html(number_format_i18n($facts['records'])); ?> updates for
        <?php echo esc_html($cell['name']); ?></a>.
      </p>

      <ol class="tit-timeline">
        <?php foreach ($facts['recent'] as $r) :
          $place = $r['city'] ?: $r['hq_city'];
          $cc    = $r['country'] ?: $r['hq_country'];
          $where_label = trim(($place ? $place . ', ' : '')
            . (function_exists('tit_country_name') ? tit_country_name($cc) : (string) $cc), ', ');
          $employer_url = (function_exists('tit_company_servable_slug')
                           && function_exists('tit_company_url')
                           && tit_company_servable_slug($r['company_key']))
                          ? tit_company_url($r['company_key']) : '';
          ?>
          <li class="tit-event">
            <div class="tit-event-when">
              <?php echo esc_html($r['published_date'] ?: substr((string) $r['captured_at'], 0, 10)); ?>
            </div>
            <div class="tit-event-body">
              <span class="tit-tag tit-<?php echo esc_attr($r['signal_direction']); ?>">
                <?php echo esc_html($direction_labels[$r['signal_direction']] ?? $r['signal_direction']); ?>
              </span>
              <span class="tit-tag"><?php
                echo esc_html($pillar_labels[$r['pillar']] ?? $r['pillar']); ?></span>
              <h3 class="tit-h"><?php echo esc_html($r['headline']); ?></h3>
              <p class="tit-rt"><?php echo esc_html($r['talent_readthrough']); ?></p>
              <p class="tit-event-meta">
                <?php if ($employer_url) : ?>
                  <a href="<?php echo esc_url($employer_url); ?>"><?php echo esc_html($r['company']); ?></a> ·
                <?php else : ?>
                  <?php echo esc_html($r['company']); ?> ·
                <?php endif; ?>
                <?php if ($where_label) : ?><?php echo esc_html($where_label); ?> · <?php endif; ?>
                <?php if ($r['headcount']) : ?><strong><?php echo (int) $r['headcount']; ?></strong> roles · <?php endif; ?>
                <?php if ($r['funding_amount']) : ?><strong><?php echo esc_html($r['funding_amount']); ?></strong> raised · <?php endif; ?>
                <span class="tit-conf tit-c-<?php echo esc_attr($r['confidence']); ?>"><?php
                  echo esc_html($conf_labels[$r['confidence']] ?? $r['confidence']); ?></span>
                · <a href="<?php echo esc_url($r['source_url']); ?>" rel="nofollow noopener" target="_blank"><?php
                  echo esc_html($r['source_name']); ?></a>
                <?php if (!empty($r['archive_url'])) : ?>
                  · <a href="<?php echo esc_url($r['archive_url']); ?>" rel="nofollow noopener" target="_blank">archived copy</a>
                <?php endif; ?>
              </p>
            </div>
          </li>
        <?php endforeach; ?>
      </ol>

      <p class="tit-cite">
        The read-through on each line is our interpretation. The headline and
        figures come from the linked source. Data licensed CC BY 4.0.
        <a href="<?php echo esc_url(tit_places_url()); ?>">Every country, city and industry we cover</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">Every source</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Back to the tracker</a>
      </p>
    </div>

    <?php
    /*
     * Structured data describes ONLY what is on the page above it: the cell this
     * page collects, and the updates actually printed in the timeline. The
     * sibling tracker earned a manual-action risk emitting identical FAQPage
     * markup across roughly 1,830 URLs where the answers were nowhere in the
     * document, so nothing is asserted here that a reader cannot read.
     *
     * numberOfItems is the number of items LISTED, not the number of records we
     * hold. The larger figure is on the page in words; putting it here would
     * make the markup describe a list that is not present.
     */
    ?>
    <script type="application/ld+json"><?php
      $listed = $facts['recent'];
      echo wp_json_encode(array(
        '@context' => 'https://schema.org',
        '@type'    => 'CollectionPage',
        'name'     => $heading,
        'url'      => tit_place_url($kind, $cell['key']),
        'about'    => array('@type' => $spec['schema'], 'name' => $cell['name']),
        'isPartOf' => array(
            '@type' => 'WebSite',
            'name'  => 'Talent Intelligence Tracker',
            'url'   => home_url('/talent-intelligence-tracker/'),
        ),
        'mainEntity' => array(
            '@type'           => 'ItemList',
            'numberOfItems'   => count($listed),
            'itemListElement' => array_map(function ($r) {
                return array(
                    '@type'         => 'NewsArticle',
                    'headline'      => $r['headline'],
                    'datePublished' => $r['published_date'],
                    'url'           => $r['source_url'],
                );
            }, $listed),
        ),
      ), JSON_UNESCAPED_SLASHES);
    ?></script>
    <?php
    if (function_exists('tit_render_footer')) tit_render_footer(); else get_footer();
}

/*
 * ---------------------------------------------------------------------------
 * THE DIRECTORY
 * ---------------------------------------------------------------------------
 *
 * One page listing every cell that has a page, linked from the dashboard hero's
 * fine print. These routes are not in any theme menu, so without a crawlable
 * index the only way in is the sitemap, and a set of pages reachable only from
 * a sitemap is a set that gets crawled slowly and trusted less.
 *
 * It also states how many cells are BELOW the bar and what happens to them,
 * because a directory that lists 32 pages while the tracker holds 109 cells
 * invites the reading that the other 77 do not exist.
 */
function tit_places_template() {
    if (!get_query_var('tit_places')) return;
    tit_places_render();
    exit;
}
add_action('template_redirect', 'tit_places_template');

function tit_places_render() {
    $kinds = tit_place_kinds();
    $published = array();
    $below = 0;
    $total = 0;
    foreach ($kinds as $kind => $spec) {
        $index = tit_place_index($kind);
        $published[$kind] = tit_place_published($kind);
        $total += count($index['cells']);
        $below += count($index['cells']) - count($published[$kind]);
    }
    $pages = array_sum(array_map('count', $published));

    if (function_exists('tit_render_header')) tit_render_header(); else get_header();
    ?>
    <div class="tit-wrap tit-place tit-directory">
      <nav class="tit-crumb">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Talent Intelligence Tracker</a>
        <span aria-hidden="true">&rsaquo;</span> Countries, Cities And Industries
      </nav>

      <h1>Countries, Cities And Industries We Cover</h1>
      <p class="tit-note">
        <?php printf(
          esc_html('%1$s of the %2$s we hold updates for have a page of their own.'),
          esc_html(number_format_i18n($pages)),
          esc_html(number_format_i18n($total))
        ); ?>
        A country, city or industry earns one once we hold at least
        <?php echo esc_html(number_format_i18n(TIT_PLACE_MIN_DOCS)); ?> source
        documents for it, from at least
        <?php echo esc_html(number_format_i18n(TIT_PLACE_MIN_SOURCES)); ?> sources
        and at least
        <?php echo esc_html(number_format_i18n(TIT_PLACE_MIN_EMPLOYERS)); ?>
        employers. Below that the tracker filtered to it already shows everything
        we hold on one screen, so a page of its own would add a heading and
        nothing else.
      </p>
      <div class="tit-callout">
        <strong>Pages appear by themselves.</strong> This list is computed from
        the database on every request, so a country crosses the bar and gets a
        page without anybody publishing it, and loses it again if its records are
        withdrawn. The
        <?php echo esc_html(number_format_i18n($below)); ?> below the bar are not
        hidden: each one is a click away on the tracker, filtered to it.
      </div>

      <?php foreach ($kinds as $kind => $spec) : ?>
        <h2 class="tit-place-h2"><?php echo esc_html($spec['many']); ?>
          <span class="tit-place-n"><?php echo esc_html(sprintf(
            _n('%s page', '%s pages', count($published[$kind]), 'tit'),
            number_format_i18n(count($published[$kind])))); ?></span></h2>
        <?php if (!$published[$kind]) : ?>
          <p class="tit-note">
            No <?php echo esc_html($spec['one']); ?> has crossed the bar yet.
            <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Every update
            we hold is on the tracker</a>.
          </p>
        <?php else : ?>
          <ul class="tit-place-list tit-place-grid">
            <?php foreach ($published[$kind] as $slug => $cell) : ?>
              <li>
                <a href="<?php echo esc_url(tit_place_url($kind, $cell['key'])); ?>"><?php
                  echo esc_html($cell['name']); ?></a>
                <span class="tit-place-n"><?php echo esc_html(sprintf(
                  _n('%s update', '%s updates', $cell['records'], 'tit'),
                  number_format_i18n($cell['records']))); ?></span>
              </li>
            <?php endforeach; ?>
          </ul>
        <?php endif; ?>
      <?php endforeach; ?>

      <p class="tit-cite">
        Every figure here is counted from the tracker's own table on render.
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">Every source</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/recall/')); ?>">What we miss, measured</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Back to the tracker</a>
      </p>
    </div>
    <?php
    if (function_exists('tit_render_footer')) tit_render_footer(); else get_footer();
}

/*
 * ---------------------------------------------------------------------------
 * TITLE, DESCRIPTION AND CANONICAL
 * ---------------------------------------------------------------------------
 *
 * These routes are not queries an SEO plugin recognises (the same reason the
 * sources page shipped with no title on 1.30.1), so if this file does not print
 * them nothing does.
 *
 * NO ROBOTS TAG IS PRINTED, and that is a decision rather than an omission.
 * company.php has to buffer the head and replace every robots tag in it because
 * a below-threshold profile is served noindex and the site's SEO plugin printed
 * a contradicting "index" beside it. Here there is no such page: a cell below
 * the bar redirects and never renders, so every place page that exists is
 * indexable and agrees with whatever default the site prints. Adding a second
 * tag that says the same thing would create the only defect available.
 */
function tit_place_title($title) {
    if (get_query_var('tit_places')) {
        return 'Countries, Cities And Industries We Cover · Talent Intelligence Tracker';
    }
    $current = tit_place_current();
    if (!$current) return $title;
    $facts = tit_place_facts($current['kind'], $current['cell']['key']);
    return sprintf(
        '%s: %s tracked %s on hiring, funding and leadership',
        $current['cell']['name'],
        number_format_i18n($facts['records']),
        $facts['records'] === 1 ? 'update' : 'updates'
    );
}
add_filter('pre_get_document_title', 'tit_place_title');

function tit_place_head() {
    if (get_query_var('tit_places')) {
        echo "\n" . '<meta name="description" content="'
           . esc_attr('Every country, city and industry the Talent Intelligence '
                      . 'Tracker holds enough on to publish a page, counted from '
                      . 'the database on request.') . '" />' . "\n";
        echo '<link rel="canonical" href="' . esc_url(tit_places_url()) . '" />' . "\n";
        return;
    }

    $current = tit_place_current();
    if (!$current || !$current['cell']['gated']) return;
    $facts = tit_place_facts($current['kind'], $current['cell']['key']);
    $cell = $current['cell'];

    $bits = array(sprintf(
        '%s: %s tracked %s across %s %s',
        $cell['name'],
        number_format_i18n($facts['records']),
        $facts['records'] === 1 ? 'update' : 'updates',
        number_format_i18n($facts['employers']),
        $facts['employers'] === 1 ? 'employer' : 'employers'
    ));
    if ($facts['funding']['usd'] > 0 && function_exists('tit_money_short')) {
        $bits[] = tit_money_short($facts['funding']['usd']) . ' disclosed funding';
    }
    $bits[] = 'Each linked to the filing or report behind it';
    $desc = implode('. ', $bits) . '.';
    // Search results cut a description around 160 characters; a sentence that
    // ends mid-figure is worse than a shorter one.
    if (strlen($desc) > 300) $desc = rtrim(substr($desc, 0, 297)) . '...';

    echo "\n" . '<meta name="description" content="' . esc_attr($desc) . '" />' . "\n";
    echo '<link rel="canonical" href="'
       . esc_url(tit_place_url($current['kind'], $cell['key'])) . '" />' . "\n";
}
add_action('wp_head', 'tit_place_head', 1);

/*
 * ---------------------------------------------------------------------------
 * THE SITEMAP
 * ---------------------------------------------------------------------------
 *
 * /talent-intelligence-tracker/places-sitemap.xml, generated on request from
 * the same index the pages are served from, through the SAME gate. Nothing is
 * written to disk, so it cannot go stale, and a URL cannot be listed here while
 * the page it points at redirects or 404s.
 */
function tit_places_sitemap_entries() {
    $entries = array(array('loc' => tit_places_url(), 'lastmod' => ''));
    foreach (tit_place_kinds() as $kind => $spec) {
        foreach (tit_place_published($kind) as $cell) {
            $url = tit_place_url($kind, $cell['key']);
            if ($url === '') continue;
            $entries[] = array('loc' => $url, 'lastmod' => $cell['lastmod']);
        }
    }
    return $entries;
}

/**
 * WordPress adds a trailing slash to anything it does not recognise as a file,
 * so the sitemap would answer 301 to .../places-sitemap.xml/ before serving. A
 * sitemap URL that redirects is a redirect reported in Search Console on every
 * fetch, and the slashed form is not a name anyone would submit.
 */
function tit_places_sitemap_no_canonical_redirect($redirect) {
    return get_query_var('tit_places_sitemap') ? false : $redirect;
}
add_filter('redirect_canonical', 'tit_places_sitemap_no_canonical_redirect');

/**
 * The sitemap as a string, so the harness can compare every URL in it against
 * what that URL actually answers. A sitemap built inside the function that also
 * sends headers and exits can only be checked by reading it.
 */
function tit_places_sitemap_xml() {
    $today = gmdate('Y-m-d');
    $xml = '<?xml version="1.0" encoding="UTF-8"?>' . "\n"
         . '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' . "\n";
    foreach (tit_places_sitemap_entries() as $entry) {
        // A future-dated row (a fiscal year that has not ended) must not become
        // a future lastmod: crawlers treat that as a broken date.
        $lastmod = ($entry['lastmod'] && $entry['lastmod'] <= $today)
                   ? $entry['lastmod'] : $today;
        $xml .= '  <url><loc>' . esc_url($entry['loc']) . '</loc>'
              . '<lastmod>' . esc_html($lastmod) . '</lastmod></url>' . "\n";
    }
    return $xml . '</urlset>' . "\n";
}

function tit_places_sitemap_template() {
    if (!get_query_var('tit_places_sitemap')) return;

    status_header(200);
    header('Content-Type: application/xml; charset=UTF-8', true);
    header('X-Robots-Tag: noindex', true); // the sitemap itself is not a page
    echo tit_places_sitemap_xml();
    exit;
}
add_action('template_redirect', 'tit_places_sitemap_template');

/**
 * Advertise it in robots.txt, WHICH IS CURRENTLY INERT, for exactly the reasons
 * tit_company_robots_txt() records: /blog/robots.txt is a physical file Apache
 * serves from disk, and the robots.txt a crawler reads for this host belongs to
 * the separate root app. Neither is reachable from this repo.
 *
 * So discovery today is the internal links: the dashboard hero links the
 * directory, the directory links every page, and every page links back. Getting
 * this sitemap in front of a crawler is the same ONE-LINE MANUAL STEP the
 * company sitemap needs, and it is recorded rather than assumed.
 */
function tit_places_robots_txt($output) {
    return $output . "\nSitemap: " . tit_places_sitemap_url() . "\n";
}
add_filter('robots_txt', 'tit_places_robots_txt');
