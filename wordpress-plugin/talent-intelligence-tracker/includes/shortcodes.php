<?php
/**
 * Renders the dashboard. Server-side first so the page is useful (and
 * indexable) before any JavaScript runs; the filters then talk to /query.
 *
 * UI copy here uses no em-dashes, matching the house style.
 */

if (!defined('ABSPATH')) exit;

/**
 * WHAT ONE DASHBOARD RENDER COSTS, AS A NUMBER.
 *
 * The owner asked for this page to be fast on desktop and on mobile, and the
 * only version of that claim a test can hold is a bound on the work a render
 * does. So it is a constant, and tests/php/render_dashboard.php asserts the
 * EXACT figure against a real SQLite database, including after five thousand
 * rows are added. A query added inside a loop over rows fails there instead of
 * on the live site under a crawl.
 *
 * It was 21, measured. Nine of those asked the database for a number another one
 * of them had already returned. What is left is thirteen scans that each fetch
 * something nothing else does:
 *
 *   1  every scalar the page prints, in one pass (the total, both sides of the
 *      detail control, employers, official filings, both date spans, the newest
 *      capture)
 *   1  what kind of update, for the first ranking
 *   1  which way headcount is going, for the third, and the toggle's own figure
 *   1  every country's count, which the region strip, the country buttons, the
 *      place ranking and the concentration caveat's denominator all read
 *   1  the largest single source inside one country, for the caveat
 *   1  the top cities row
 *   1  the at-a-glance matrix, conditional aggregation over one scan
 *   1  the daily rollup behind the trend chart, every signal in one GROUP BY
 *   1  the money head: the total, its coverage, and what each card can place
 *   3  money by country, by city and by industry
 *   1  which industry, by count, for the sixth ranking
 *   1  the first page of rows, with a LIMIT
 *
 * RAISED 13 -> 14 when the grid went to nine cards. Two cards were added and
 * only one of them cost a query: how solid the evidence is rides the head scan
 * above as three more CASE expressions over rows it was already counting, so a
 * ranking of two buckets costs nothing. Which industry cannot: it is a GROUP BY
 * over a column nothing else on the page groups on.
 *
 * RAISED 14 -> 15 on 2026-08-03, and this one bought a correction rather than a
 * feature: tit_trend_ingest_breadth(), the scan behind the trend panel's
 * statement about how much of a movement is us reading more.
 *
 *   1  which collectors STORED rows for this view in the first and the last week
 *      of the window, GROUP BY DATE(captured_at)
 *
 * It cannot ride the daily rollup above it, and the reason is the whole point of
 * the fix. That scan groups by COALESCE(published_date, DATE(captured_at)), and
 * the sentence was previously counting collector names out of it, so a collector
 * switched on last week that ingests back-dated articles was counted as having
 * fed the START of the window. A measurement whose one job is to detect
 * collection growth was blind to the commonest shape of it. Bucketing by ingest
 * needs a different GROUP BY, and a different GROUP BY is a different scan.
 *
 * It is the same order of cost as the scan beside it: neither is sargable, since
 * both wrap their date column in an expression, so both were always going to
 * read the slice rather than seek it. Both are behind the same transient, so a
 * warm render still costs zero.
 *
 * HELD AT 15 on 2026-08-05, two out and two in. The collection-rate chart left
 * this page for the sources page, taking its daily rollup and the ingest
 * breadth scan with it. Its slot holds the market trend now, which costs the
 * same two:
 *
 *   1  every collector's first and last ingest day, GROUP BY collector, which
 *      is what decides the fixed panel (the sources live for the whole window)
 *   1  the weekly split by stated headcount direction, GROUP BY event date,
 *      restricted to the panel when the panel is wide enough to count from
 *
 * Neither can ride an existing scan for the same reason the breadth scan could
 * not: the panel needs GROUP BY collector over ingest dates and nothing else
 * on the page groups on either. Both sit behind the same transient, so a warm
 * render still costs zero. The direction ranking's own GROUP BY stays although
 * its card merged into the market chart, because the stated-headcount toggle's
 * figure is summed from it and /aggregate still serves the group.
 */
const TIT_DASH_QUERY_BUDGET = 15;

/**
 * How many rows the server prints before JavaScript is involved.
 *
 * Matches /query's own default page, so the first paint and the first repaint
 * cannot hand the reader a different number of rows. It is also most of what
 * this page weighs (roughly 1.2KB of markup per row), which is why it is a
 * named number with a test on it rather than a literal inside a LIMIT.
 */
const TIT_DASH_ROWS = 50;

function tit_dashboard_shortcode() {
    // Render ONCE per request, whatever the theme does. This block theme runs
    // the content through the shortcode pass more than once, so the whole
    // dashboard appeared twice on the live page: two filter panels, two sets
    // of charts, two of every element id. Every getElementById bound to the
    // first copy, which left the second a dead mirror: visible controls that
    // did nothing, multi selects that never became pills, counts that never
    // moved. Nearly every "the page is broken" report traced back to the
    // reader looking at the twin. Duplicate ids are also invalid HTML, and
    // the second render doubled every query this shortcode runs.
    static $rendered = false;
    if ($rendered) return '';
    $rendered = true;
    return tit_dashboard_html();
}

/**
 * EVERYTHING THE DASHBOARD ASKS THE DATABASE, ONCE, AS ONE CACHED BUNDLE.
 *
 * WHY THIS IS SAFE TO CACHE AT ALL, which is the only question that matters
 * about a cache on a page with nine filters on it.
 *
 * Nothing here reads the request. Not $_GET, not a cookie, not a user. The
 * server always renders the SAME default view, and a filtered view is
 * JavaScript's job against /query, which does its own caching keyed on its own
 * parameters. So there is exactly one bundle, it is identical for every reader,
 * and there is no shape of this cache that can hand one reader another's view.
 * If a future session ever makes this render read a filter, it has to split the
 * key or delete the cache, and the comment above the key says so.
 *
 * THE KEY carries TIT_VERSION, so a deploy that changes what the page prints
 * cannot serve the previous version's numbers, and the current DATE, because the
 * at-a-glance matrix derives "this week", "this month", "this quarter" and YTD
 * from today. It reads that date from current_time() -- the same call the matrix
 * itself uses -- so the bundle expires exactly when its own columns move rather
 * than at some UTC boundary the matrix does not observe.
 *
 * THE TTL matches the REST endpoints' (TIT_CACHE_TTL, 5 minutes), because both
 * layers publish the same figures and a page that disagreed with /aggregate for
 * half an hour would be a dashboard arguing with itself. It is also only ever a
 * backstop: every write route calls tit_flush_caches(), which drops every tit_
 * transient, so a correction or a fresh run appears immediately.
 *
 * WHAT IT IS WORTH. Twelve aggregate scans of the whole table, on shared
 * hosting, on the page every reader lands on. Cold origin renders were measured
 * at 2.5 to 4.0 seconds; warm ones now ask the database nothing at all, which
 * tests/php/render_dashboard.php asserts as a zero.
 */
function tit_dashboard_facts($table) {
    /*
      One key, no request state in it. See the note above before adding any:
      a key that varies by filter is a cache that can serve one reader another
      reader's page, and this render has no business knowing about filters.
    */
    $key = 'tit_dash_' . md5(TIT_VERSION . '|' . current_time('Y-m-d'));
    $cached = get_transient($key);
    if (is_array($cached) && isset($cached['total_all'])) return $cached;

    global $wpdb;

    /*
      EVERY SCALAR THE PAGE PRINTS, IN ONE PASS.

      This was six separate queries, each of them a full scan of the same rows
      under the same clause: the total, both sides of the detail control, the
      employer count, the filings count, the two date spans and the newest
      capture. Six scans of a table this page is about to scan several more
      times, to fetch numbers that all come off the same rows.

      Conditional aggregation instead: the CASE expressions carry the notable
      clause where a WHERE used to, so the whole hero comes back on one pass.
      Same shape places.php uses, and the same reason beyond the count -- a
      figure that shares its scan with the figure beside it cannot end up
      describing a different set of rows.

      The date bounds are deliberately TWO pairs from the one row. The note
      describes the set the page is showing, so it agrees with every other
      figure in the hero; the date inputs keep the full range, so the control
      can never refuse a date that exists. Reading those from two queries is how
      a range and its own label drift apart.
    */
    $notable_sql = function_exists('tit_notable_where') ? tit_notable_where() : '1 = 1';
    $base = "is_current = 1 AND {$notable_sql}";
    $date_expr = 'COALESCE(published_date, DATE(captured_at))';

    /*
      THE CURRENT-YEAR SLICE RIDES ON THE SAME PASS. The freshness panel shows
      each figure twice, this year first and all time under it, because the
      whole-record totals alone read as this year's; the owner misread them
      himself. The year is DERIVED from the clock (current_time), never typed,
      so the labels roll over on January 1 without an edit, and the daily cache
      key above already carries the date so a cached December render cannot
      serve January. Four more CASE expressions on a scan the page was paying
      for anyway, so the pairing costs no extra query.
    */
    $ytd_year = (int) current_time('Y');
    $jan1 = sprintf('%04d-01-01', $ytd_year);
    $ytd_sql = "({$notable_sql}) AND {$date_expr} >= '{$jan1}'";

    $head = $wpdb->get_row(
        "SELECT COUNT(*) total_all,
                SUM(materiality = 'routine') routine,
                SUM({$notable_sql}) notable,
                SUM(({$notable_sql}) AND confidence = 'verified') verified,
                SUM(({$notable_sql}) AND confidence = 'reported') reported,
                SUM(({$notable_sql}) AND confidence = 'rumored') rumored,
                COUNT(DISTINCT CASE WHEN {$notable_sql} THEN company_key END) companies,
                SUM({$ytd_sql}) ytd_total,
                SUM(({$ytd_sql}) AND confidence = 'verified') ytd_verified,
                COUNT(DISTINCT CASE WHEN {$ytd_sql} THEN company_key END) ytd_companies,
                COALESCE(SUM(CASE WHEN {$ytd_sql} THEN funding_amount_usd END), 0) ytd_money,
                MIN({$date_expr}) lo_all,
                MAX({$date_expr}) hi_all,
                MIN(CASE WHEN {$notable_sql} THEN {$date_expr} END) lo,
                MAX(CASE WHEN {$notable_sql} THEN {$date_expr} END) hi,
                MAX(captured_at) newest_run
           FROM {$table} WHERE is_current = 1", ARRAY_A) ?: array();

    $total_all = (int) ($head['total_all'] ?? 0);
    $span_lo = $head['lo_all'] ?? '';
    $span_hi = $head['hi_all'] ?? '';

    $facts = array(
        'total_all' => $total_all,
        'routine'   => (int) ($head['routine'] ?? 0),
        'notable'   => (int) ($head['notable'] ?? $total_all),
        'verified'  => (int) ($head['verified'] ?? 0),
        'companies' => (int) ($head['companies'] ?? 0),
        // This year's slice of the same four figures, plus the year they
        // belong to, so the render never re-reads the clock the query used.
        'ytd_year'      => $ytd_year,
        'ytd_total'     => (int) ($head['ytd_total'] ?? 0),
        'ytd_verified'  => (int) ($head['ytd_verified'] ?? 0),
        'ytd_companies' => (int) ($head['ytd_companies'] ?? 0),
        'ytd_money'     => (float) ($head['ytd_money'] ?? 0),
        // Bounds for the date inputs. The sibling can offer years, quarters and
        // months because it holds years; we hold days. Letting the control ask
        // for a period we have nothing in is a control that manufactures empty
        // states and makes thin coverage look like a broken filter.
        'span_lo'   => $span_lo,
        'span_hi'   => $span_hi,
        'view_lo'   => $head['lo'] ?? $span_lo,
        'view_hi'   => $head['hi'] ?? $span_hi,
        'newest_run' => $head['newest_run'] ?? '',
        'stated'    => 0,
        'by_pillar' => array(),
        'by_direction' => array(),
        'by_confidence' => array(),
        'by_industry' => array(),
        'counts_by_country' => array(),
        'held_by_country' => array(),
        'countries' => 0,
        'by_country' => array(),
        'glance'    => array(),
        'market'    => array(),
        'money'     => array('total' => 0, 'coverage' => array('with' => 0, 'all' => 0),
                             'placed' => array(), 'by_country' => array(),
                             'by_city' => array(), 'by_industry' => array()),
        'place_caveat' => '',
        'rows'      => array(),
        'cities'    => array(),
    );

    // An empty table gets the empty-state panel and nothing else, so none of
    // the eleven scans below is worth paying for. Cached all the same: this is
    // the state before collection is armed, and it is dropped the moment the
    // first row lands, because storing one calls tit_flush_caches().
    if ($total_all === 0) {
        set_transient($key, $facts, tit_dash_ttl());
        return $facts;
    }

    $facts['by_pillar'] = $wpdb->get_results(
        "SELECT pillar, COUNT(*) n FROM {$table} WHERE {$base} GROUP BY pillar ORDER BY n DESC",
        ARRAY_A
    ) ?: array();

    $facts['by_direction'] = $wpdb->get_results(
        "SELECT signal_direction k, COUNT(*) n FROM {$table} WHERE {$base}
          GROUP BY signal_direction ORDER BY n DESC", ARRAY_A) ?: array();

    /*
      How many updates in the default view actually state a headcount. Printed
      beside the toggle, so a reader sees what it would do before using it.

      Summed from the direction ranking rather than counted again. It was its own
      COUNT(*) with `signal_direction IN ('hiring', 'displacement')`, which is
      the same two buckets the query above has just returned with their counts.
      One clause, one place: if the definition of "moves headcount" ever changes,
      it changes here and the ranking cannot disagree with the toggle beside it.
    */
    $stated_dirs = array('hiring' => true, 'displacement' => true);
    foreach ($facts['by_direction'] as $d) {
        if (isset($stated_dirs[$d['k']])) $facts['stated'] += (int) $d['n'];
    }

    /*
      WHAT THE EVIDENCE IS, RANKED, AND IT COSTS NO QUERY.

      Confidence is a closed vocabulary of three, and the head scan above
      already counts one of them because the hero prints it. Counting the other
      two there as well is two more CASE expressions on a pass the page was
      paying for anyway, so a whole card arrives for nothing. A GROUP BY here
      would have been a fifteenth scan of the table to return three numbers.

      Buckets holding nothing are dropped rather than drawn at zero, the same
      rule the region strip follows: a bar at zero reads as a filter that broke.
      Order is the vocabulary's own -- strongest evidence first -- not by size,
      because this is a ladder and a reader reads it as one.
    */
    foreach (array_keys(tit_confidence_labels()) as $conf) {
        $n = (int) ($head[$conf] ?? 0);
        if ($n > 0) $facts['by_confidence'][] = array('k' => $conf, 'n' => $n);
    }

    /*
      WHICH INDUSTRY, BY COUNT, and it is not the money card by another name.
      That one ranks summed dollars and can only see the rows carrying a
      figure; this counts every update, so an industry that is hiring hard and
      raising nothing appears here and nowhere else on the page.
    */
    $facts['by_industry'] = $wpdb->get_results(
        "SELECT industry k, COUNT(*) n FROM {$table} WHERE {$base}
          AND industry IS NOT NULL AND industry != ''
          GROUP BY industry ORDER BY n DESC LIMIT 40", ARRAY_A) ?: array();

    $facts['glance'] = tit_glance_matrix($table, $base);
    // The market trend, under the same clause as every other card. The
    // collection-rate chart this slot used to hold (tit_signal_trend) now
    // renders on the sources page, where an operations measure belongs; see
    // the note above tit_market_trend().
    $facts['market'] = tit_market_trend($table, $base);
    // The money views and the matrix's money row share one coverage figure, so
    // a dollar total can never sit next to a sentence describing a different
    // set of rows.
    $facts['money'] = tit_money_aggregate($table, $base);
    $facts['glance']['coverage'] = $facts['money']['coverage'];

    /*
      EVERY COUNTRY'S COUNT, ONCE.

      Four things read this: the region strip (which sums the codes inside each
      region), the top-country buttons, the place ranking, and the concentration
      caveat's denominator. Three of them used to ask the database for it
      separately -- the same GROUP BY twice, once whole and once ordered with a
      LIMIT, plus a third COUNT(DISTINCT) for how many countries there are.

      They are all the same map, so it is fetched once and the rest is array
      work. COUNT(DISTINCT COALESCE(country, hq_country)) is exactly the number
      of keys in it, because COUNT(DISTINCT) skips NULLs and so does the WHERE
      here. That equality is the whole reason the query could go, and it is why
      the two must stay in one place: a filter added to one and not the other
      would put a country count next to a country chart that disagreed with it.

      TWO MAPS OFF THE ONE SCAN, because two surfaces here count two different
      things and both of them say so.

        `n`    rows in the DEFAULT VIEW (the notable clause). The region strip,
               the country ranking and the concentration caveat read this,
               because each of those is a figure about the set the table below
               is showing, and the region badge is asserted to equal what its
               own tab returns (tests/test_region_badge_reconciles.py).
        `held` EVERY CURRENT ROW. The country ribbon reads this, because its
               caption is "Countries by Updates Held" and a routine officer
               change is an update we hold. Under the notable clause that
               caption was false by 3,023 US rows, 3,020 of them sec_edgar
               leadership_change, which put the United Kingdom above the United
               States on a ribbon that /aggregate had the other way round
               (US 10,570 against GB 8,047, measured 2026-08-13).

      The conditional aggregation is the same shape the head scan above uses,
      so the second map costs no second query.

      ZEROES ARE DROPPED FROM `n` DELIBERATELY: a country holding nothing but
      routine rows is a key in the scan and not a country in the default view,
      and $facts['countries'] is COUNT(DISTINCT ...) under the notable clause.
      Leaving it in would print one more country than the hero counts.
    */
    $per_country = $wpdb->get_results(
        "SELECT COALESCE(country, hq_country) k, SUM({$notable_sql}) n, COUNT(*) held
           FROM {$table}
          WHERE is_current = 1 AND COALESCE(country, hq_country) IS NOT NULL
          GROUP BY k", ARRAY_A) ?: array();
    $counts = array_filter(array_map('intval', array_column($per_country, 'n', 'k')));
    $facts['counts_by_country'] = $counts;
    $facts['countries'] = count($counts);
    $facts['held_by_country'] = array_map('intval', array_column($per_country, 'held', 'k'));

    // 40, matching /aggregate, not 6. The chart scrolls and expands, so a short
    // list is no longer what keeps the card small -- and a hard six meant the
    // World view could not show two of the eight countries we actually hold.
    arsort($counts);
    $top = array_slice($counts, 0, 40, true);
    $facts['by_country'] = array_map(
        function ($k, $n) { return array('k' => $k, 'n' => $n); },
        array_keys($top), $top
    );

    /*
      When ONE collector accounts for most of a country, say so.

      The United Kingdom shows 4,804 rows, of which 4,761 come from the gender
      pay gap filing. That is not a parser bug and not UK business activity: it
      is one mandatory annual return that every large employer files, and a
      reader scanning the country chart would take that bar as a measure of how
      much is happening there. Computed, never written down, so it names
      whichever country is currently dominated and disappears when none is.

      The country totals it needs to divide by are the map above, handed over
      rather than counted a second time.
    */
    $facts['place_caveat'] = tit_place_caveat($table, $base, array(),
                                              $facts['counts_by_country']);

    // Materiality first, recency inside it, matching /query's default sort so
    // the first paint and the first repaint cannot put the rows in a different
    // order. A stated headcount or a real funding amount outranks a bare
    // officer change; an unjudged row outranks a judged-routine one.
    $facts['rows'] = $wpdb->get_results(
        // industry and funding_amount_usd joined the list when the results
        // became cards: the card's rail names the sector and its third badge
        // names the amount, and a column missing here renders as a card that
        // silently drops both on the first paint and grows them on the first
        // repaint. See docs/card-contract.json.
        "SELECT signal_id, headline, talent_readthrough, company, company_key, pillar, signal_direction,
                city, country, hq_city, hq_country, confidence, source_url, source_name,
                archive_url, collector, published_date, industry, funding_amount_usd
           FROM {$table} WHERE {$base}
          ORDER BY CASE materiality WHEN 'high' THEN 0 WHEN 'medium' THEN 1
                                    WHEN 'routine' THEN 3 ELSE 2 END ASC,
                   {$date_expr} DESC, row_id DESC
          LIMIT " . TIT_DASH_ROWS,
        ARRAY_A
    ) ?: array();

    /*
      CITIES BY UPDATES HELD, which is what the caption over this strip says and
      is therefore every current row, routine filings included.

      Three things were wrong with this one query, and each of them put a number
      on the page that the page itself contradicted one click later.

      1. It grouped by bare `city` while the pill writes `city=<name>`, which
         api.php resolves as `city = %s OR (city IS NULL AND hq_city = %s)`. So
         the London pill read 18 and returned 1,338: almost every London row is
         placed by its employer's head office, and this count could not see one
         of them. Manchester (108) and Edinburgh (49) were absent from a strip
         that carried Seattle (42) and Toronto (25). It groups by
         tit_city_expr() now, which is the same rule the filter uses.

      2. `cc` was a non-aggregated column under GROUP BY city, so the flag was
         whichever row the engine happened to reach first -- and MySQL and SQLite
         need not agree. Toronto (22 Canadian rows, 2 American) flew a US flag.
         It is now the MODAL country for that city, ties broken alphabetically,
         so it is deterministic and it is the answer a reader would give.

      THE THIRD FIX WAS TAKEN BACK OUT, on purpose, and this is the note that
      says why. The strip was moved onto {$base} so that a pill counted the set
      the table was showing. That made the number honest against the table and
      false against the pill's own caption: San Francisco holds routine officer
      filings and nearly nothing else, and a strip captioned "Cities by Updates
      Held" that cannot see them is counting something narrower than its own
      words. The caption is the contract, so the count is every current row and
      matches /aggregate's own by_city, which applies no detail filter either.
      The basis line above the strip states that these counts include the
      routine filings the table sets aside.

      Still one query. The scalar subquery runs once per pill, ten times, on a
      render that is cached for TIT_CACHE_TTL.
    */
    $city_expr    = function_exists('tit_city_expr') ? tit_city_expr() : 'COALESCE(city, hq_city)';
    $country_expr = function_exists('tit_country_expr') ? tit_country_expr() : 'COALESCE(country, hq_country)';
    /*
      AND `stated` RIDES ALONG, because the caption alone cannot say what the
      number is made of.

      {$city_expr} is COALESCE(city, hq_city). `city` is a STATED job location;
      `hq_city` is where the employer is based. Union them and London reads
      2,296, of which 22 state London and 2,274 are employers registered there
      (measured 2026-08-13). A reader takes "London 2,296" as 2,296 events in
      London, and it is not that.

      THE COUNT IS NOT THE DEFECT AND MUST NOT BE "FIXED". The pill under it
      resolves as `city = %s OR (city IS NULL AND hq_city = %s)`, so a strip
      counting only stated cities would disagree with what its own click
      returns -- the exact defect fixed above in item 1, arriving from the other
      side. The union is also what /aggregate's by_city applies and what the
      sibling exposes as country_basis=any. So the number stays and the page
      says what it counts, which is the shape the country ribbon's own fix took
      on 2026-08-13: make the words and the number agree, and change whichever
      of the two is wrong.

      One conditional SUM on a scan that already runs. No second query.
    */
    $facts['cities'] = $wpdb->get_results(
        "SELECT c.k, c.n, c.stated,
                (SELECT {$country_expr} FROM {$table}
                  WHERE is_current = 1 AND {$city_expr} = c.k AND {$country_expr} IS NOT NULL
                  GROUP BY {$country_expr}
                  ORDER BY COUNT(*) DESC, {$country_expr} ASC LIMIT 1) cc
           FROM (SELECT {$city_expr} k, COUNT(*) n,
                        SUM(CASE WHEN city IS NOT NULL AND city <> '' THEN 1 ELSE 0 END) stated
                   FROM {$table}
                  WHERE is_current = 1 AND {$city_expr} IS NOT NULL AND {$city_expr} <> ''
                  GROUP BY k ORDER BY n DESC, k ASC LIMIT 10) c
          ORDER BY c.n DESC, c.k ASC", ARRAY_A) ?: array();

    set_transient($key, $facts, tit_dash_ttl());
    return $facts;
}

/**
 * The bundle's lifetime, asked of api.php rather than written down twice.
 *
 * Read through defined() because a partial FTP upload can leave api.php missing
 * for a few seconds, and a dashboard that fatals on a missing constant during a
 * deploy is worse than one that caches for the default five minutes.
 */
function tit_dash_ttl() {
    return defined('TIT_CACHE_TTL') ? TIT_CACHE_TTL : 300;
}

/**
 * The render itself, callable more than once per process.
 *
 * Split from the shortcode so the once-per-request guard above is the only
 * thing that is once-per-request. A harness that can render only once can
 * measure a cold render or a warm one but never both, and "warm costs nothing"
 * is the half of the caching claim that actually reaches a reader.
 */
function tit_dashboard_html() {
    // Enqueue from INSIDE the shortcode as well as from wp_enqueue_scripts.
    // The hook's guard asks has_shortcode($post->post_content, ...), which is
    // FALSE whenever the shortcode reaches the page through a block, pattern,
    // template part or reusable block rather than sitting raw in post_content.
    // The dashboard then rendered with no stylesheet at all -- every tit- class
    // inert, the page raw HTML (observed live 2026-07-28). Enqueuing where the
    // markup is actually produced cannot drift from where it is used.
    if (function_exists('tit_enqueue_dashboard_assets')) tit_enqueue_dashboard_assets();
    $facts = tit_dashboard_facts(tit_table_name());

    $total_all = (int) $facts['total_all'];

    ob_start();

    if ($total_all === 0) {
        ?>
        <div class="tit-wrap">
          <div class="tit-empty">
            <h2>No entries published yet</h2>
            <p>Collection has not been switched on. Every record here has to
               link to the article that makes the claim, and we are still
               proving that sourcing out. So an empty table is the honest state
               rather than a broken one.</p>
            <p class="tit-empty-note">This page will fill in once collection is
               armed. It will not be backfilled with anything unsourced.</p>
          </div>
        </div>
        <?php
        return ob_get_clean();
    }

    /*
      The page LEADS with what is worth a reader's time, and says so.

      An SEC backfill that worked landed thousands of routine officer changes:
      each one accurate, each one verified, and together they buried the hiring
      and funding news anyone actually opened this page for. The first screen
      was micro-cap CFO appointments as far as you could scroll.

      So the default view sets routine filings aside. Nothing is deleted and
      nothing is hidden: the control is right above the table, it states both
      counts, and it says in one sentence what we mean by routine, so a reader
      can judge our judgement instead of trusting it. Every server-rendered
      figure below sits under the SAME clause the table does, or the hero would
      describe a set the rows do not belong to.
    */
    $n_routine        = (int) $facts['routine'];
    $n_notable        = (int) $facts['notable'];
    $total            = $n_notable;
    $companies        = (int) $facts['companies'];
    $verified         = (int) $facts['verified'];
    $span_lo          = $facts['span_lo'];
    $span_hi          = $facts['span_hi'];
    $view_lo          = $facts['view_lo'];
    $view_hi          = $facts['view_hi'];
    $newest_run       = $facts['newest_run'];
    $n_stated         = (int) $facts['stated'];
    $by_pillar        = $facts['by_pillar'];
    $by_confidence    = $facts['by_confidence'] ?? array();
    $by_industry_n    = $facts['by_industry'] ?? array();
    $counts_by_country = $facts['counts_by_country'];
    // The ribbon's own map: every current row, which is what its caption says.
    // See the two-maps note in tit_dashboard_facts(). The fallback is for a
    // transient written by an older build, which carries no such key: the cache
    // key holds TIT_VERSION so a deploy cannot actually serve one, and a ribbon
    // that vanishes for a cache lifetime is a worse way to find that out.
    $held_by_country  = !empty($facts['held_by_country'])
        ? $facts['held_by_country'] : $counts_by_country;
    $countries        = (int) $facts['countries'];
    $by_country       = $facts['by_country'];
    $glance           = $facts['glance'];
    $market           = is_array($facts['market'] ?? null) ? $facts['market'] : array();
    $money            = $facts['money'];
    $place_caveat     = $facts['place_caveat'];
    $rows             = $facts['rows'];
    $tit_cities       = $facts['cities'];
    // Recruiter language, not ours. "Pillar" and "signal direction" are
    // internal vocabulary and never appear on the page.
    /*
      ONE VOCABULARY FOR THE WHOLE PAGE, IN TITLE CASE.

      The owner asked twice: "What happend to title case on everything" and
      "What is hiring up mean? use a differne word, What is pay news what is all
      updates. See these aren't human".

      Both complaints had the same root. The page carried TWO sets of words for
      the same facts. The charts said "Pay and benefits" and "Growing and
      expanding"; the matrix beside them said "Pay news" and "Funding raised"
      for the same rows. A reader had to work out that "Pay news" and "Pay and
      benefits" were one thing. So there is now one list, these are it, and
      every other surface reads from it or mirrors it.

      SAFE TO RENAME, checked before touching anything. These are DISPLAY
      strings only. Every chart row carries its key on `data-k`, every matrix row
      on `data-signal`, and the filter a click applies is a separate `filter`
      field; tit_glance_matrix() keys its cells `c_{$di}_{$pi}` by index and
      never by label. So no query, cache key or handler reads any of these. (On
      the sibling this same edit was a two-file data join because an aggregate
      keyed its rows BY LABEL and a cached response spanning the deploy would
      have killed click-to-filter. That coupling does not exist here, and the
      assertions in tests/php/render_dashboard.php now pin that it stays absent.)

      Title Case here is conventional Title Case, not every-word-capitalised:
      short conjunctions and prepositions stay lowercase ("Pay and Benefits",
      "Ways of Working"), because "Pay And Benefits" is not how a person writes
      and the owner's other complaint was that these did not read as if a person
      had. tit_title_case_ok() is the shared rule and the test uses it.
    */
    $labels = array(
        'company_development' => 'Growing and Expanding',
        'leadership_change'   => 'Leadership Moves',
        'rewards_comp'        => 'Pay and Benefits',
        'how_we_work'         => 'Ways of Working',
    );
    // One definition, in tit_direction_labels(), because these four strings are
    // now SHARED WITH THE SIBLING AI LAYOFF TRACKER and a second copy here is a
    // second place for them to drift. See docs/card-contract.json.
    $directions = tit_direction_labels();
    $functions = array(
        'engineering' => 'Engineering', 'data_ai' => 'Data & AI',
        'it_infrastructure' => 'IT & Infrastructure', 'product' => 'Product',
        'design' => 'Design', 'finance' => 'Finance', 'hr_people' => 'HR & People',
        'sales' => 'Sales', 'marketing' => 'Marketing',
        'customer_support' => 'Customer Support', 'operations' => 'Operations',
        /*
          "Production & manufacturing", not "Manufacturing", and the reason is
          that the word appeared TWICE in this panel: once here, as the team a
          job sits in, and once in Industry, as the sector the employer trades
          in. Two groups, one word, two meanings, and nothing on the page
          telling a reader which was which. The stored vocabulary value is
          untouched (`manufacturing` in pipeline/vocab.FUNCTIONS); only the
          words a reader sees are disambiguated, because a vocabulary is fixed
          and a label is ours.
        */
        'supply_chain' => 'Supply Chain',
        'manufacturing' => 'Production & Manufacturing',
        'legal_compliance' => 'Legal & Compliance', 'research' => 'Research',
        'clinical_healthcare' => 'Clinical & Healthcare', 'executive' => 'Executive',
    );
    $industries = tit_industry_labels();
    $confidences = tit_confidence_labels();

    /*
      Quick views, cut back to the ones the at-a-glance matrix cannot express.

      This row used to hold nine chips mixing two different axes: four time
      periods and four signal types, side by side, with nothing saying that
      picking "This month" and picking "Hiring up" narrow the page in completely
      different ways. Since the signal-by-period matrix shipped, every one of
      those eight is a cell in that matrix, done better: the matrix shows the
      count BEFORE you click it, and it crosses time with signal instead of
      making you apply two chips and hope.

      What survives is what the matrix has no axis for. "From Official Filings"
      is a confidence filter, and the matrix has no confidence dimension.
      "Biggest Raises" is a SORT, which a matrix cell cannot be at all, and it
      only became possible when funding_amount_usd gave us a number to sort on
      (the old display string put $9M above $10B).

      DEFINED HERE, three hundred lines above the strip that prints it, because
      it was defined three hundred lines BELOW it. PHP runs a function body top
      to bottom, so the foreach ran over an undefined variable: the live page
      emitted two notices on every single render and shipped the quick views as
      a label and a hint with no buttons between them. Nothing about the markup
      looked wrong, which is why it survived several sessions.
    */
    $quick_views = array(
        '' => 'Everything',
        'confidence=verified' => 'From Official Filings',
        'funding=1&sort=raised' => 'Biggest Raises',
        /*
          MOVED HERE OUT OF THE FILTER PANEL, and relabelled, and this is the
          one control on the page whose own name was the defect.

          It shipped in the primary filter row as a checkbox reading "Only
          Updates That Move Headcount (54)", and the owner asked what it meant.
          It filters `signal_direction IN ('hiring','displacement')`, which is
          "the source said which way headcount is going". It does NOT read the
          `headcount` column, and the difference is not academic: measured
          2026-07-29 over 15,711 current rows, `headcount` is non-null on 11
          (0.07%) while hiring-or-displacement is true on 53 (0.34%), of which
          51 are hiring and 2 are displacement.

          So it was doing something real and rare under a name that promised
          something else. Three options were on the table. Removing it loses a
          filter that /query still accepts and that existing share links carry.
          Leaving it in the panel keeps a control returning 0.3% at the same
          visual weight as Industry, which returns thousands. Making it a QUICK
          VIEW is what the design mock does and what the numbers support: a
          quick view is explicitly a narrow, named cut of the page rather than a
          general-purpose filter, it sits beside the other two narrow cuts, and
          it carries its own count so a reader sees the size of what they are
          asking for BEFORE clicking rather than after.

          The count is printed and computed, never typed, and it is the count
          under the current filters, so it moves with them.

          The checkbox itself survives in .tit-state as the hidden state this
          button drives, which is what keeps the querystring, the chips bar and
          the exports working unchanged.
        */
        'stated_headcount=1' => 'Moves Headcount',
    );
    ?>
    <!--
      The config also rides on the element, not only on wp_localize_script.
      Autoptimize aggregates INLINE scripts into a bundle, and our
      autoptimize_filter_js_exclude only protects assets matched by path, so
      dashboard.js stays where it is while the inline `TIT` it depends on gets
      swept into a bundle that loads after it. The script then saw
      `typeof TIT === "undefined"`, returned on its first statement, and every
      filter, the region strip and the quick views did nothing at all on the
      live site while looking perfectly fine. Markup cannot be reordered away
      from the element it describes.
    -->
    <div class="tit-wrap" id="tit-dashboard"
         data-api="<?php echo esc_attr(rest_url('talent/v1/')); ?>"
         data-countries="<?php echo esc_attr(wp_json_encode(tit_country_names())); ?>"
         data-states="<?php echo esc_attr(wp_json_encode(tit_state_names())); ?>"
         <?php
         /* The pending-archive note dashboard.js repaints cards with. The
            SERVER composes the sentence (tit_archive_pending_note derives the
            date from data/archive_promise.json) and the JS only ever prints it,
            so both paints are byte-identical and the date has one derivation.
            Absent entirely when the promise file is unreadable, and the JS
            renders nothing in that case — same rule as the PHP. */
         $tit_ap = tit_archive_promise();
         if ($tit_ap) {
             echo ' data-archive-note="' . esc_attr(wp_json_encode(array(
                 'collectors' => array_values($tit_ap['collectors']),
                 'text'       => tit_archive_pending_note($tit_ap['collectors'][0]),
             ))) . '"';
         }
         ?>>

      <div class="tit-hero">
        <div class="tit-hero-top">
          <?php
          /*
            The benefit, then the proof.

            "Who is hiring, who is raising money, and who is changing
            leadership" described the CONTENTS and buried the reason to be
            here. A funding round, a new CEO or a new office all happen weeks
            before the roles get posted: for a recruiter that is prospecting
            ahead of everyone else, for a job seeker it is applying before the
            flood. That is the product, and it now says so.

            Deliberately an h2 and not an h1, though the copy is the page's
            headline: the theme already renders "Talent Intelligence Tracker"
            as the h1, that is the keyword-bearing heading we were asked to
            keep for search, and a second h1 would split the signal it exists
            to carry. It is styled as the dominant heading, which is what
            actually matters to a reader.

            No superlatives here or anywhere else on the page: "most advanced"
            and its family are the one class of claim a skeptic can disprove in
            thirty seconds, and everything visible has to survive a check.
          */
          ?>
          <?php
          /*
            THE EDITORIAL HERO (the owner's shared design, adopted 2026-08-02).
            Serif thesis headline, ONE subhead sentence carrying the trust
            claim, exactly two actions: search the updates, or read how the
            thing is built. The prose this replaces (the undated figure lump
            and its two fine-print lines) is not deleted, it is re-homed as
            FIGURES: the freshness panel on the right holds the four totals,
            the coverage ribbon below holds the date span and country count.
            Net words on the first screen go DOWN, which was the owner's ask
            ("so much text and so many areas - we need colored narratives").
          */
          ?>
          <div class="tit-hero-head">
            <h2>Know who's hiring before the job ad appears.</h2>
            <p class="tit-hero-sub">Every update links to the filing or report it
              came from; nothing here is inferred, modelled or estimated.</p>
            <div class="tit-hero-cta">
              <?php /* A button, not a link: it focuses the employer search in
                       the filter bar, which is state, not navigation. The
                       count is computed and dashboard.js repaints it under
                       the active filters, so it never promises a number the
                       table is not showing. */ ?>
              <button type="button" class="tit-cta tit-cta-search" id="tit-cta-search">Search
                <span id="tit-cta-n"><?php echo esc_html(number_format_i18n($total)); ?></span>
                updates</button>
              <a class="tit-cta tit-cta-how" href="#tit-trust">How this is built</a>
            </div>
          </div>
          <?php
          /*
            THE FRESHNESS PANEL, top-right. It absorbs the old status line and
            the old "Everything We Hold" figures: the Live pill (last
            collection, absolute time with timezone), four figures, the
            promise line, and Roo, whose line derives the next run from the
            committed collect.yml schedule (tit_next_run). dashboard.js
            repaints the figures under the active filters, the same contract
            the old fine print had, so a filtered page never shows worldwide
            totals in its own header.
          */
          ?>
          <div class="tit-fresh">
            <div class="tit-live"><span class="tit-live-dot"></span>
              Live<?php if ($newest_run) : ?> · updated
              <?php echo esc_html(tit_local_datetime($newest_run)); ?>
              <?php endif; ?>
            </div>
            <div class="tit-fresh-stats" id="tit-fresh-stats"><?php
              echo tit_fresh_stats_html($total, $companies, $money, $verified, array(
                  'year'      => (int) ($facts['ytd_year'] ?? 0),
                  'total'     => (int) ($facts['ytd_total'] ?? 0),
                  'companies' => (int) ($facts['ytd_companies'] ?? 0),
                  'verified'  => (int) ($facts['ytd_verified'] ?? 0),
                  'money'     => (float) ($facts['ytd_money'] ?? 0),
              ));
            ?></div>
            <span class="tit-fine-say">No figure appears unless its source states it.</span>
            <div class="tit-roo-row"><?php tit_roo($newest_run); ?></div>
          </div>
        </div>

        <?php
        /*
          THE COVERAGE RIBBON: one line under the hero. The date span and the
          country count are DERIVED (tit_span_note reads the view's own MIN
          and MAX dates; a typed date here is the corrections-page "$124.0bn"
          mistake with a slower fuse), and the trust links live beside them.
          The class stays .tit-hero-links because tests and styles key on it;
          the places link stays because these routes are in no theme menu and
          this line is how a crawler finds them.
        */
        ?>
        <p class="tit-hero-links">
          <span class="tit-ribbon-cov"><span id="tit-span"><?php
            echo esc_html(tit_span_note($view_lo, $view_hi)); ?></span>
            <span aria-hidden="true">·</span> <span id="tit-ribbon-c"><?php
            echo esc_html(number_format_i18n($countries)); ?></span> countries</span>
          · <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">Every source</a>
          · <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/recall/')); ?>">What we miss, measured</a>
          · <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/corrections/')); ?>">Corrections</a>
          · <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/places/')); ?>">Every country, city and industry we cover</a>
          · <a href="/blog/ai-layoff-tracker/">Layoffs are tracked separately</a>
        </p>

        <?php
        /*
          PLACE FIRST, THEN THE CROSS-TAB. The owner asked for the geographic
          strips to move above the matrix, and the sequence is better for it:
          picking a place is how most readers start, and a time-by-signal
          cross-tab means more once a place is chosen than it does cold.

          Moving it invalidated a POINTER, which is the part worth remembering.
          The quick-views hint read "For a period, click a number in the matrix
          at the top", and the matrix is not at the top any more. A stale
          direction is worse than none, because a reader follows it and finds
          nothing. Grepped for the others; this was the only one.
        */
        ?>
      <?php /* No heading here. "The market right now" over "Pick a region to
                 narrow the updates below" said nothing the strip beneath it did
                 not already say with its own labels and counts, and a heading
                 that only restates its contents is a row of dead pixels between
                 the reader and the control. */ ?>
        <?php
        /*
          Two tiers, visually distinct, because they are two granularities and
          showing them at one weight was the bug. Regions cover the world once
          each and CONTAIN their countries; the country row is derived from live
          counts and is clearly subordinate.

          Picking a country replaces a region rather than stacking with it. They
          answer the same question, "where", so ANDing them would let a reader
          select Europe and then the United States and get an empty page that
          looks broken. Replacing always narrows, never contradicts, and the
          chips bar names whichever one is applied.
        */
        // $total is the view's own row count, the one the hero prints and the
        // one the World tab's request returns. World carries no country filter,
        // so its badge has to be that, not the sum of a country map that skips
        // every row with no geography.
        $tit_regions = tit_regions($counts_by_country, $total);
        $tit_top = tit_top_countries($held_by_country);
        ?>
        <div class="tit-places">
          <div class="tit-regions" role="group" aria-label="Filter by region">
            <?php foreach ($tit_regions as $r) : ?>
              <button type="button" class="tit-region<?php echo $r['codes'] === '' ? ' is-on' : ''; ?>"
                      data-codes="<?php echo esc_attr($r['codes']); ?>">
                <span class="tit-region-flag" aria-hidden="true"><?php echo tit_region_emoji($r['name']); ?></span>
                <span class="tit-region-name"><?php echo esc_html($r['name']); ?></span>
                <span class="tit-region-n"><?php echo esc_html(number_format_i18n($r['n'])); ?></span>
              </button>
            <?php endforeach; ?>
          </div>
          <?php
          /*
            THE BASIS, ABOVE THE TWO ROWS IT QUALIFIES.

            "Top Countries" over a descending list of flags is a leaderboard,
            and it was read as one: the owner asked why the United Kingdom
            outranks the United States. It does not. Companies House publishes
            structured filings for very nearly every UK company and we ingest
            the lot; the US equivalent reaches public companies only. The
            ordering is a picture of how we collect, so the label has to say
            so, in the same vocabulary the chart below already uses ("a count
            of updates we hold, never a count of jobs").

            AND THE COUNT UNDER THE LABEL IS NOW THE THING THE LABEL NAMES.
            Both rows were built under the notable clause while saying "Updates
            Held", which silently dropped 3,023 United States rows, 3,020 of
            them routine sec_edgar officer changes. That alone was what put the
            United Kingdom above the United States here: the UK holds 14 routine
            rows in total, because its bulk is Companies House pay gap filings
            graded medium. So one country's dominant collector was graded out of
            the ribbon and another's was not, and /aggregate, which applies no
            detail filter, reported the pair the other way round (US 10,570
            against GB 8,047 on 2026-08-13). Both rows now count every current
            row and agree with that endpoint.

            THE PRICE, AND IT IS REAL. The region strip above still counts the
            default view, because its badge is asserted to equal what its own
            tab returns, and the table below the ribbon still opens on the
            notable clause. So a country pill can read higher than the region
            containing it and higher than the rows a click returns. The basis
            line says the ribbon counts routine filings; it does not make the
            two strips one number, and only widening the detail control's reach
            would.

            VISIBLE PROSE, and deliberately NOT a .tit-chart-note. Every one of
            those panels is closed by dashboard.js on load, which is how three
            caveats on this page ended up computing display:none and being read
            by nobody; the two nearest guards in
            tests/test_chart_titles_and_basis.py exist because of exactly that.
            This is a plain <p> in the flow, styled by .tit-places-note, which
            the 1.74.5 contrast pass already gave an explicit colour in both
            themes.

            ABOVE the rows rather than below them, for the reason the place
            chart's caveat is: the misread country is by definition near the
            front of a descending list, so a correction printed after the list
            arrives after the misreading it exists to prevent.
          */
          ?>
          <?php /* Printed on ONE source line on purpose. This template's
                   indentation is emitted, and tit_chart_head() already records
                   the measurement behind that: the page has a byte budget
                   (TIT_DASH_BYTE_BUDGET in tests/php/render_dashboard.php) and
                   five wrapped lines of copy at this depth cost about sixty
                   bytes of leading spaces no reader ever sees. Wrapping this
                   paragraph the pretty way put the page over the ceiling. */ ?>
          <?php if ($tit_top || $tit_cities) : ?>
            <p class="tit-places-note tit-places-basis">These counts are updates we hold, not a ranking of the market, and they count the routine filings the table sets aside. A place counts an update when the update states that job location or when the employer is based there.<?php echo tit_city_basis_note($tit_cities); ?> Where a country publishes a company registry we read in full, we hold far more updates per employer than we do where we rely on news.</p>
          <?php endif; ?>
          <?php if ($tit_top) : ?>
            <div class="tit-countries" role="group" aria-label="Filter by country">
              <span class="tit-countries-label">Countries by Updates Held</span>
              <?php foreach ($tit_top as $c) : ?>
                <button type="button" class="tit-cbtn" data-code="<?php echo esc_attr($c['code']); ?>"
                        aria-pressed="false">
                  <?php /* Flag is decoration and aria-hidden; the NAME is always
                           printed, because a platform with no font for a flag
                           draws two letters or a blank box. */ ?>
                  <?php echo tit_country_label_html($c['code']); ?>
                  <span class="tit-cbtn-n"><?php echo esc_html(number_format_i18n($c['n'])); ?></span>
                </button>
              <?php endforeach; ?>
            </div>
          <?php endif; ?>
          <?php if ($tit_cities) : ?>
            <div class="tit-countries" role="group" aria-label="Filter by city">
              <span class="tit-countries-label">Cities by Updates Held</span>
              <?php foreach ($tit_cities as $c) : ?>
                <button type="button" class="tit-cbtn tit-citybtn" data-city="<?php echo esc_attr($c['k']); ?>"
                        aria-pressed="false">
                  <span aria-hidden="true"><?php echo tit_flag($c['cc'] ?? ''); ?></span>
                  <span><?php echo esc_html($c['k']); ?></span>
                  <span class="tit-cbtn-n"><?php echo esc_html(number_format_i18n($c['n'])); ?></span>
                </button>
              <?php endforeach; ?>
            </div>
          <?php endif; ?>
          <p class="tit-places-note">Regions include every country inside them, so
             Europe counts the United Kingdom and Asia counts India. Picking a
             country replaces the region rather than narrowing inside it.</p>
        </div>

        <?php
        /*
          THE SIGNAL BOARD (the owner's "colored narratives", 2026-08-02). It
          REPLACES the dated daily strip's four text lines: the same facts,
          one colored, readable, tappable matrix instead of four sentences.
          The strip's header survives as the board's (the date, and Copy as
          Post, which now copies the board's rows as clean text lines); the
          matrix inside is the existing signal-by-period cross-tab, cells
          heat-tinted within their own row, every cell a filter. The trend
          chart still owns the shape BETWEEN the columns, as a card in the
          chart grid below.

          #tit-glance stays the repaint target dashboard.js rewrites under
          the active filters; the head and legend around it are static, so
          the Copy button is bound once and never lost to a repaint.
        */
        ?>
        <div class="tit-board" id="tit-board">
          <div class="tit-board-head">
            <h3 class="tit-board-title">Today, <?php echo esc_html(tit_board_date_label()); ?>
              <span aria-hidden="true">·</span> Sourced Talent Signals Worldwide</h3>
            <?php /* The heat scale, named. "less/more" is real text; the
                     swatches are decoration over it, so a screen reader hears
                     the words and nothing else. */ ?>
            <span class="tit-board-legend">less<span class="tit-lg" style="--i:.14" aria-hidden="true"></span><span
              class="tit-lg" style="--i:.4" aria-hidden="true"></span><span
              class="tit-lg" style="--i:.7" aria-hidden="true"></span><span
              class="tit-lg" style="--i:1" aria-hidden="true"></span>more</span>
            <?php /* Rendered hidden; dashboard.js reveals it. Its entire
                     function is navigator.clipboard, so with no JavaScript it
                     would be a control that visibly does nothing. */ ?>
            <button type="button" class="tit-dg-copy" id="tit-dg-copy" hidden>Copy as Post</button>
          </div>
          <div class="tit-glance" id="tit-glance">
            <?php echo tit_glance_matrix_html($glance); ?>
          </div>
          <?php /* WHAT A ROW IS, UNDER THE BOARD THAT COUNTS THEM.

                   The sibling layoff tracker learned this the hard way: the
                   owner could not tell what a count of rows was counting, and
                   no amount of correct arithmetic above the number fixes a
                   word nobody can parse. So the board says it.

                   It names Total Raised on purpose. Five of the six rows are
                   counts of records and one is a sum of dollars, and a reader
                   who assumes the sixth works like the other five reads a
                   money figure as a headcount. The sub-label inside that row
                   says "sum of dollars"; this says which row is the odd one.

                   Deliberately a SIBLING of #tit-glance and not a child:
                   dashboard.js rewrites #tit-glance on every repaint, exactly
                   like the head and the legend above, so a definition inside
                   it would survive until the reader's first filter and then
                   vanish. It is also outside any disclosure, because a closed
                   <details> still carries textContent for text nobody can
                   read, and innerText on a subtree that is not rendered falls
                   back to textContent too. Asserted on the rendered page in
                   tests/test_reader_copy_says_entries.py. */ ?>
          <p class="tit-board-def" id="tit-board-def">An entry is one update about one employer. Total Raised counts dollars, and every other row counts entries.</p>
        </div>

      </div>

      <?php
      /*
        THE CROSS-TRACKER PAIRS, when there are any that can be defended.
        Renders nothing today: the module ships disabled, because a name match
        across two identity caches produced a pair claiming a Greek bank's
        redundancies against a US bank. The measurement is in the header of
        includes/cross_tracker.php. Called with rows the bundle already holds,
        so it costs no query whether it renders or not.
      */
      if (function_exists('tit_cross_tracker_html')) {
          echo tit_cross_tracker_html(array_values(array_filter(
              $facts['rows'],
              fn($r) => ($r['signal_direction'] ?? '') === 'hiring'
          )));
      }
      ?>

      <?php
      /*
        WHERE THE MACHINERY STARTS. One sentence, no heading.

        This block used to be a heading, "Narrow It Down", over the sentence
        below it. The owner asked whether it was needed and the heading is the
        half that was not: the very next thing on the page is a control group
        labelled "Quick Views" and after that a bar labelled "Filters", and on
        a phone the word Filters appears TWICE within one screen of it (the bar
        head and the collapse toggle). A heading whose only job is to say
        "filters follow" one line above something that says "Filters" is a row
        of dead pixels.

        THE SENTENCE STAYS, SHORTENED, because it is the one thing the layout
        cannot say by itself: a reader who narrows the page has no reason to
        expect the CHARTS to move with it, and they do. Since 1.63.0 the charts
        sit directly under this bar, so the arrangement now carries most of the
        claim and the sentence only has to name what is included.

        It does NOT say "below" any more. The charts and the updates are named,
        so moving either one cannot turn this line into a wrong direction --
        which is exactly what the reorder did to four other lines on this page.

        THE ELEMENT AND ITS ID SURVIVE ON PURPOSE. The phone jump bar in
        dashboard.js scrolls to #tit-filter-sec, so deleting the wrapper would
        take the Filters button on every phone with it, silently and with
        nothing red anywhere.
      */
      ?>
      <?php
      /*
        THE ZONE CLASSES (tit-zone-controls here, tit-zone-insight and
        tit-zone-updates further down) are PAINT, not structure. The page reads
        as three bands now: controls (quick views, the filter bar, the chips),
        insight (every chart), updates (sort, cards, export), each with one
        background tint. The controls band CANNOT be a wrapper div: the filter
        bar is position:sticky and its sticking range is its parent (.tit-feed),
        so wrapping it in a band that ends at the chips would stop it sticking
        the moment the reader scrolled past them. So the band is a shared class
        on the elements themselves and the stylesheet closes the gaps.
      */
      ?>
      <div class="tit-sec tit-zone-controls" id="tit-filter-sec">
        <p>The charts and the updates both follow these filters.</p>
      </div>

      <div class="tit-quick tit-zone-controls" role="group" aria-label="Quick views">
        <span class="tit-quick-label">Quick Views</span>
        <?php foreach ($quick_views as $spec => $label) : ?>
          <button type="button" class="tit-qv" data-qv="<?php echo esc_attr($spec); ?>"><?php
            echo esc_html($label);
            /* The one quick view whose set is small enough that its size is
               part of what it means. See the note beside $quick_views.

               THE SPACE BEFORE THE BRACKET IS A REAL SPACE, not a margin. It
               shipped as `Moves Headcount<span>(1,869)</span>` with a 5px
               margin-left doing the separating, which looks right and is right
               nowhere else: the accessible name a screen reader reads out, the
               text a reader copies, and the string an answer engine scrapes all
               came back "Moves Headcount(1,869)". That is what the owner saw.
               A text node fixes all three at once, so the margin goes and the
               gap stays the same width. */
            if ($spec === 'stated_headcount=1') : ?> <span class="tit-qv-n"
              id="tit-stated-n"><?php echo esc_html('(' . number_format_i18n($n_stated) . ')');
            ?></span><?php endif; ?></button>
        <?php endforeach; ?>
        <?php /* The watchlist chip. Ships hidden and is revealed by script ONLY
                 when localStorage is usable: the whole feature lives in this
                 browser (no account, no server storage, no PII), so without
                 storage there is nothing it could do and it must not appear.
                 Not a member of $quick_views: those are saved filter specs the
                 querystring can carry, and this narrows client-side (see the
                 watchlist note in the (i) panel). */ ?>
        <button type="button" class="tit-qv tit-watch-chip" id="tit-watch-chip" hidden
                aria-pressed="false" aria-describedby="tit-help-watch">Watchlist <span
          class="tit-qv-n" id="tit-watch-n">(0)</span><span class="tit-watch-new"
          id="tit-watch-new" hidden></span></button>
        <?php
        /*
          WHAT THE STAR DOES, IN THE OPEN. The owner asked three times and could
          not tell from the page, and the answer existed in exactly two places a
          reader never reaches: a comment in dashboard.js, and #tit-help-watch
          inside <details id="tit-help">, which ships CLOSED. A sentence behind a
          disclosure is a sentence for the people who already know.

          So it is prose in the controls band, on its own row, beside the control
          it explains. Same lesson as the place caveat above (see the note by
          chart-place): it is NOT passed through tit_chart_head() as note_html,
          because dashboard.js closes every .tit-chart-note on load and the
          element would compute display:none on every browser that ran the
          script. Nothing on this page may explain itself from inside a container
          the script shuts.

          IT SHIPS hidden FOR ONE REASON ONLY, the same one that hides the chip:
          without usable localStorage there are no stars, and prose about a
          control that is not there is worse than silence. paintWatch() reveals
          the chip and this sentence on the same pass, so on any browser that can
          hold a watchlist it is visible at load with nothing to open or expand.

          #tit-watch-short is the shortfall line, written by applyWatchFilter().
          It stays empty until the watchlist is on AND some starred employer has
          nothing in the loaded window, which is the moment the feature reads as
          broken.
        */
        ?>
        <p class="tit-quick-hint tit-watch-hint" id="tit-watch-hint" hidden><span
          id="tit-watch-hint-t">Star an employer on any update to follow it.
          Stars stay on this device and are never sent to us. Turn Watchlist on
          to show just the employers you starred.</span> <span
          class="tit-watch-short" id="tit-watch-short" hidden></span></p>
        <?php /* Names the control, not a direction. The signal table is still
                 above this strip, but it was above it the last time somebody
                 wrote "at the top" here too, and that line survived the move
                 that made it wrong (see the note by the region strip). */ ?>
        <span class="tit-quick-hint">For a time period, tap a number in the board above.</span>
      </div>
      <?php
      /*
        THE STAR CONFIRMATION, one region, outside the chip strip.

        NOT `hidden`, and that is the whole reason it sits here rather than as a
        fourth item in .tit-quick. An aria-live region has to be IN the
        accessibility tree before the text lands in it; a region that is
        display:none until the moment it is written is a region several screen
        readers never announce. So it ships empty and permanent, and the
        stylesheet gives it no padding, no border and no background while it is
        :empty, which is why an empty one measures zero and the strip above
        keeps its spacing. Inside the flex strip it would have cost two 7px gaps
        on every load for a message that is usually not there.

        POLITE, and ONE region. A reader starring six employers in six seconds
        gets the sixth confirmation, not a queue of six: each write replaces the
        text and restarts the single timer. Nothing takes focus, so the keyboard
        stays on the star that was just pressed, and the message is above the
        cards rather than over them, so it cannot cover that star.
      */
      ?>
      <p class="tit-watch-toast" id="tit-watch-toast" aria-live="polite"></p>

      <?php
      /*
        The filter block, in two deliberately different registers.

        The PRIMARY ROW asks questions, because it is where a recruiter or job
        seeker begins and should feel spoken to. MORE FILTERS uses short nouns,
        because it is a reference list to scan rather than a conversation.

        Every label in both groups is SENTENCE CASE, and none of them is
        uppercased by the stylesheet. It used to be that a stack label was
        transformed to uppercase and the Where label was not, because the
        uppercase rule matched on being a direct child of the field and the
        Where label sits one level deeper. So one row read "WHAT ARE YOU
        LOOKING FOR?", "Where?", "WHICH EMPLOYER?" and looked like three
        unrelated controls. One convention, one rule, no transform.

        The front controls do NOT replace the underlying filters. The pillar,
        direction, country, state and city selects still exist, still carry the
        same values, and are still what the querystring, the chips bar, the
        exports and the click-to-filter charts read and write. They are hidden,
        and the visible controls drive them. That keeps this a presentation
        change: nothing about what any filter MEANS has moved.

        No internal vocabulary is reachable from here. "Pillar", "signal",
        "direction", "confidence" and "basis" appear in the code and never on
        the page. That claim was FALSE until 2026-08-13: "signal" was on the
        page in nine places, three of them counting rows. A count of rows is
        now "entries"; the axis the four kinds sit on is a kind, a line or a
        measure; and the word survives only as the name of the subject
        matter ("Sourced Talent Signals Worldwide"), which counts nothing.
        tests/test_reader_copy_says_entries.py is what keeps it true now,
        because a comment could not.
      */
      ?>
      <?php
      /*
        THE FEED: a compact filter BAR above the rows, frozen to the top of the
        viewport as the reader scrolls.

        THIS REVERSES THE COLUMN THAT SHIPPED IN 1.54.0. That pass read the
        owner's "filters dont move with the page a like the layoff one" as a
        request for the sibling's SIDEBAR, and built one: a 262px column of
        seven capped scrolling checkbox boxes, sticky at 1000px and up. The
        owner has now seen it on the live page and asked for the opposite, in
        two messages that are really one message:

          "the formatting do you see this? Make them more compact"
          "the filter so complicated with the scrolling up and down should we
           move those to above the stuff and compact and have it frozen on top
           when you scroll down??"

        The two are cause and effect. A 262px column plus its 20px gap took 282
        of a 1340px content width, so the table rendered into ~1038px across
        seven columns and What Happened -- the column carrying a headline AND a
        read-through -- was squeezed to about 210px, which wraps a sentence to
        one word per line. Widening that column alone would only have taken the
        space from another. The column IS the width problem, so the column goes
        and the table gets the whole 1340px back.

        WHY A BAR CAN BE COMPACT WHEN A COLUMN COULD NOT. In a column every
        group is stacked, so seven option lists have to be seven boxes and each
        one has to scroll to fit. Across a bar each group is a BUTTON that
        states its own name and how many of its options are on, and its options
        live in a panel that exists only while it is open. Thirteen controls
        become thirteen buttons on two wrapped rows, and no list has to be
        pre-emptively squeezed into a scroller. That is also why the options are
        not laid out flat across the top: seven open checkbox lists side by side
        is the same wall of options in a worse place.

        Sticky is CANCELLED OUTRIGHT by any scrollable ancestor, and this
        stylesheet already carries the rules that keep html and body from
        becoming one (see the overflow-x:clip note in dashboard.css). Nothing
        between #tit-dashboard and this element may set overflow, which is why
        the bar is a direct child of .tit-feed and .tit-feed sets none.
      */
      ?>
      <div class="tit-feed">
        <div class="tit-filterbar tit-zone-controls" id="tit-panel" aria-labelledby="tit-panel-t">
          <div class="tit-panel-head">
            <?php /* The phone affordance, and the ONLY thing that collapses.
                     A bar wide enough for thirteen controls is four rows on a
                     390px screen, which would pin most of the viewport under
                     chrome; so on a phone the bar is this one button and the
                     controls open below it as a sheet, in normal flow rather
                     than fixed, so nothing traps the page scroll.

                     Ships `hidden` and is revealed by script, because a reader
                     with no JavaScript must never meet a button that does
                     nothing -- they get the whole bar, uncollapsed, which is
                     the markup as served. */ ?>
            <button type="button" class="tit-bar-toggle" id="tit-bar-toggle"
                    aria-expanded="true" aria-controls="tit-panel-body" hidden>
              <span class="tit-bar-toggle-t">Filters</span>
              <span class="tit-bar-n" id="tit-bar-n" hidden></span>
            </button>
            <span class="tit-panel-t" id="tit-panel-t">Filters</span>
            <?php
            /*
              ONE affordance where there were eight blocks of instruction, and it
              lives in the bar head rather than in the control flow.

              The panel used to shout "CHOOSE MORE THAN ONE IF YOU LIKE" under
              every one of seven multi-selects, in uppercase, saying the same
              thing each time. Repeating an instruction seven times adds nothing
              to the seventh reader and makes it the loudest thing on a block
              whose job is to be quiet.

              So instructions live here, once, behind an (i) that opens when
              somebody wants them. Native <details>: keyboard reachable and
              screen reader reachable without a line of our own code, and it
              works with JavaScript off. Each paragraph carries an id and every
              control it explains points at it with aria-describedby, so the
              explanation is announced WITH the control whether or not the panel
              is open. A title attribute would have reached neither a keyboard
              nor a screen reader reliably.

              In the head because on the bar it would otherwise be a fourteenth
              control-shaped thing sitting among thirteen actual controls, and
              it is not a filter.
            */
            ?>
            <details class="tit-help" id="tit-help">
              <summary class="tit-help-s">
                <span class="tit-help-i" aria-hidden="true">i</span>
                <span class="tit-help-w">How These Filters Work</span>
              </summary>
              <div class="tit-help-b">
                <p id="tit-help-multi"><strong>Filters that take more than one.</strong>
                  Tap every one that applies. Tap again to remove it. Every choice
                  also becomes its own chip in the Filtering row, so you can drop
                  one without clearing the rest.</p>
                <p id="tit-help-watch"><strong>Watchlist.</strong> Star an
                  employer on any update to follow it. Stars are saved in this
                  browser only; nothing is sent to us and no account exists.
                  Turning the watchlist on narrows the updates loaded on this
                  page (the newest 50 of the current view) to your starred
                  employers, right here in the browser.</p>
                <p id="tit-help-basis"><strong>Where.</strong> Places come from what a
                  source named. When a source names no place we use the employer's head
                  office instead, so a company known only by its headquarters still
                  appears. Tick "Only Countries A Source Named" to leave those out.</p>
                <?php /* The mechanism has been there all along (refresh()
                         writes every filter into the querystring with
                         replaceState); this sentence exists because an
                         invisible feature is one nobody uses. */ ?>
                <p id="tit-help-share"><strong>Saving a view.</strong> The address
                  bar always matches the filters, so bookmark or share the page
                  at any moment and the link reopens this exact view.</p>
              </div>
            </details>
            <?php /* Reset lives at the head of the bar. It was the last cell of
                     the filter grid, below seven scrolling boxes, which is the
                     one place a reader who wants to start over will not look.
                     Same id, so the same handler binds it. */ ?>
            <button type="button" class="tit-panel-reset" id="tit-reset">Reset All</button>
          </div>
          <div class="tit-panel-body" id="tit-panel-body">
      <?php
      /*
        EVERY CONTROL BELOW IS ONE FLEX ITEM OF THE BAR.

        .tit-primary and .tit-filters are still two containers in this file and
        still hold the fields they always held, in the order they always held
        them. The stylesheet gives both `display:contents`, so the bar packs all
        thirteen controls with one wrap rather than two grids each padding out
        its own last row. Nothing was re-nested to make the layout change, which
        is what keeps this a presentation change.

        Give either container a border, a background or padding and that rule
        silently drops it. If one of them ever needs to paint, it stops being a
        `display:contents` box and the bar has to become a single container.
      */
      ?>
      <div class="tit-primary">
        <label class="tit-field tit-field--stack tit-primary-main">
          <span class="tit-field-l">Looking For</span>
          <select id="tit-f-looking" aria-label="Looking for">
            <?php foreach (tit_looking_options() as $spec => $label) : ?>
              <option value="<?php echo esc_attr($spec); ?>"><?php echo esc_html($label); ?></option>
            <?php endforeach; ?>
          </select>
        </label>

        <?php /* One place control, not three. Options are grouped Countries,
                 then US States, then Cities, and each one knows which parameter
                 it sets, so the reader picks a place and never a column.

                 How places are decided is a real choice and it stays here, as
                 a control. What left is the three-line paragraph that used to
                 explain it in place: it wrapped over three lines between two
                 controls and shoved the row around, and the toggle itself was
                 a word inside that sentence, underlined, which is not how a
                 control announces itself.

                 It is a checkbox now, with a label that does NOT change when
                 you tick it. The old button rewrote its own text to name the
                 destination, so it read one way when off and the opposite way
                 when on, and a reader glancing at it could not tell which
                 state they were in without reading the sentence beside it.
                 A checkbox states one thing and shows whether it is on. The
                 explanation lives in the (i) above, and aria-describedby
                 carries it to a screen reader with the control. */ ?>
        <div class="tit-field tit-field--stack tit-primary-where">
          <label class="tit-where-label">
            <span class="tit-field-l">Location</span>
            <select id="tit-f-place" aria-label="Location">
              <option value="">All Locations</option>
            </select>
          </label>
          <?php
          /*
            "Only Places A Source Named", not "Exact Locations Only".

            The owner read the old label and said it did not make sense, which
            was fair: "exact" invites a reader to think about precision -- a city
            rather than a region, a street rather than a city -- and this control
            has nothing to do with precision.

            WHAT IT ACTUALLY DOES, read out of tit_build_where() in api.php
            before touching it. Ticked, it sends country_basis=location, and the
            country clause changes from
              (country IN (..) OR (country IS NULL AND hq_country IN (..)))
            to
              country IN (..)
            So it drops rows that are only in a country because we substituted
            the employer's head office when the source named no place. That is a
            real and meaningful thing to ask for, and it is exactly the sentence
            already sitting in the (i) panel, which called it "Only places a
            source named" while the control called itself something else. One
            name now, in both places.

            KNOWN LIMIT, stated rather than papered over: it narrows the COUNTRY
            clause only. The city clause in tit_build_where() is unconditionally
            the union form, so a city pick still admits a head-office match.
            Closing that is an api.php change and api.php is not this pass's
            lane, so the label says country and does not claim the city.
          */
          ?>
          <label class="tit-check tit-check--slim tit-basis-check">
            <input type="checkbox" id="tit-basis-chk" value="1"
                   aria-describedby="tit-help-basis">
            <span class="tit-check-t">Only Countries A Source Named</span>
          </label>
        </div>

        <label class="tit-field tit-field--stack">
          <span class="tit-field-l">Employer</span>
          <input type="search" id="tit-f-company" placeholder="e.g. Apple"
                 aria-label="Employer">
        </label>

        <?php /* The headcount control used to be a fourth cell in this row. It
                 is a quick view now, beside the other two narrow cuts, with its
                 count on it; the checkbox it drives moved to .tit-state. The
                 whole argument is written out beside $quick_views above. */ ?>
      </div>

      <?php /* Not a disclosure at all any more. It shipped as <details> with a
               "More filters (1)" summary; the owner asked three times for that
               wording to go. Keeping <details open> still rendered the summary,
               so the words stayed on the page while the behaviour changed
               underneath - the label was also dead by then, since the sync
               had stopped writing to it and only forced the panel open.

               A permanently-open disclosure is not a disclosure, so this is a
               plain container. The controls inside carry their own labels and
               the chips bar already names what is applied, which is what the
               summary was standing in for. Classes and id are unchanged so the
               existing CSS keeps working. The JavaScript that used to force it
               open, count what was inside it and write that label is gone: it
               was setting .open on an element that has no .open. */ ?>
      <div class="tit-more tit-more--open" id="tit-more">
        <div class="tit-filters">
          <?php
          /*
            Seven of these take SEVERAL values at once, because a recruiter wants
            "Technology or Healthcare" and not one at a time. They are native
            multiple selects: keyboard reachable without a line of our own code,
            scrollable in place, and every choice becomes its own removable chip
            in the filtering bar. /query takes them comma separated and each
            value is checked against its closed vocabulary before it reaches SQL.

            None of them carries a helper line any more. All seven said "Choose
            more than one if you like", in uppercase, one under each control:
            the same instruction, seven times, in the loudest type on the panel.
            It is in the (i) at the top now, once, and each select points at it
            with aria-describedby so a screen reader still gets it.

            No aria-label either. The visible label beside each one IS its name,
            and an aria-label OVERRIDES a visible label rather than adding to it,
            so the old ones were quietly renaming every control to a string
            nobody could see ("Team or function, choose more than one if you
            like"). Wrapping the label around the select is enough.

            Employer type, Work setup, Deal type, Funding stage and Site change
            are filled from /facets and HIDE THEMSELVES when their column is
            empty. Shipping a control that always returns nothing is worse than
            shipping no control. That has been the intent since they shipped and
            it was NOT what happened: the `hidden` attribute is a user agent
            rule, `.tit-field` sets `display:flex`, and any author rule beats the
            user agent, so Site Change - a new column with nothing in it yet -
            rendered as an empty box. The fix is one line in the stylesheet, not
            here. These now genuinely appear by themselves the day the pipeline
            fills them.

            ORDER: the multi-selects first, then the single selects, then the two
            full-width fields. Amount raised is one short select and it used to
            sit in the middle of the tall ones, in a grid row sized for a list
            box, so it hung under a control's worth of blank space and lined up
            with nothing beside it. The row no longer stretches a short control
            (`align-items:start`), and grouping by height means it does not have
            to.
          */
          ?>
          <label class="tit-field tit-field--stack">
            <span class="tit-field-l">Team or Function</span>
            <select id="tit-f-function" multiple size="5"
                    aria-describedby="tit-help-multi">
              <?php foreach ($functions as $k => $v) : ?>
                <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
              <?php endforeach; ?>
            </select>
          </label>
          <label class="tit-field tit-field--stack">
            <span class="tit-field-l">Industry</span>
            <select id="tit-f-industry" multiple size="5"
                    aria-describedby="tit-help-multi">
              <?php foreach ($industries as $k => $v) : ?>
                <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
              <?php endforeach; ?>
            </select>
          </label>
          <label class="tit-field tit-field--stack" id="tit-field-employer_type" hidden>
            <span class="tit-field-l">Employer Type</span>
            <select id="tit-f-employer_type" multiple size="5"
                    aria-describedby="tit-help-multi"></select>
          </label>
          <label class="tit-field tit-field--stack" id="tit-field-work_mode" hidden>
            <span class="tit-field-l">Work Setup</span>
            <select id="tit-f-work_mode" multiple size="5"
                    aria-describedby="tit-help-multi"></select>
          </label>
          <label class="tit-field tit-field--stack" id="tit-field-funding_stage" hidden>
            <span class="tit-field-l">Funding Stage</span>
            <select id="tit-f-funding_stage" multiple size="5"
                    aria-describedby="tit-help-multi"></select>
          </label>
          <label class="tit-field tit-field--stack" id="tit-field-deal_type" hidden>
            <span class="tit-field-l">Deal Type</span>
            <select id="tit-f-deal_type" multiple size="5"
                    aria-describedby="tit-help-multi"></select>
          </label>
          <label class="tit-field tit-field--stack" id="tit-field-site_event" hidden>
            <span class="tit-field-l">Site Change</span>
            <select id="tit-f-site_event" multiple size="5"
                    aria-describedby="tit-help-multi"></select>
          </label>
          <?php /* Bands, not a box to type a number in. A recruiter thinks in
                   orders of magnitude, and an exact figure produces an
                   empty-looking page for anyone who guesses a threshold nothing
                   sits above. Single choice: bands already nest. */ ?>
          <label class="tit-field tit-field--stack">
            <span class="tit-field-l">Amount Raised</span>
            <select id="tit-f-min_funding_usd" aria-label="Smallest amount raised">
              <option value="">Any Amount</option>
              <?php foreach (tit_funding_bands() as $value => $label) : ?>
                <option value="<?php echo esc_attr($value); ?>"><?php echo esc_html($label); ?></option>
              <?php endforeach; ?>
            </select>
          </label>
          <label class="tit-field tit-field--stack">
            <span class="tit-field-l">Evidence</span>
            <select id="tit-f-confidence" aria-label="What the record is based on">
              <option value="">Any Evidence</option>
              <?php foreach (tit_confidence_labels() as $k => $v) : ?>
                <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
              <?php endforeach; ?>
            </select>
          </label>
          <label class="tit-field tit-field--stack tit-field--wide">
            <span class="tit-field-l">Keyword Search</span>
            <input type="search" id="tit-f-q" placeholder="Company, industry or keyword"
                   aria-label="Search headlines and read-throughs">
          </label>
          <?php /* One row, always. As two separate grid cells these landed in
                   different rows whenever the column count was even, so a range
                   read as two unrelated dates.

                   Each input has its OWN visible label now. It was one label,
                   "Date From / Date To", over two boxes with a cramped lowercase
                   "to" jammed between them, so the only thing telling you which
                   box was the start was the order they happened to be in, and a
                   screen reader got a name the eye could not see. From and To
                   are printed, the separator is gone, and the group keeps its
                   own name for anyone arriving by keyboard. This is the sibling
                   tracker's pattern: a labelled From and a labelled To. */ ?>
          <?php /* The id is what dashboard.js finds this cell by to make it the
                   one dropdown whose panel holds inputs rather than a checkbox
                   group. Same shape as the facet cells' `tit-field-<key>` ids so
                   there is one convention for "script addresses this cell". */ ?>
          <div class="tit-field tit-field--stack tit-field--wide"
               id="tit-field-daterange"
               role="group" aria-labelledby="tit-daterange-l">
            <span class="tit-field-l" id="tit-daterange-l">Date Range</span>
            <?php
            /*
              YEAR / QUARTER / MONTH, the sibling's period selects, but wired
              as SHORTHAND for the two date boxes underneath rather than as
              parameters of their own. Picking one WRITES the window into
              From and To where the reader can see it, so the mechanism is
              legible, and everything that already reads since/until (the
              querystring, the chips, the exports, Reset All, the signal
              board's cells) keeps one source of truth. dashboard.js does the
              reverse read too: dates that exactly span a year, quarter or
              month light these up, which is what makes a shared link round
              trip; any other hand-edited dates blank them.

              The year list is DERIVED from the data's own bounds, never
              typed, newest first. A year we hold nothing in would be a
              control that manufactures empty states.
            */
            $yr_hi = (int) substr((string) $span_hi, 0, 4);
            $yr_lo = (int) substr((string) $span_lo, 0, 4);
            if ($yr_hi < $yr_lo) { $t = $yr_hi; $yr_hi = $yr_lo; $yr_lo = $t; }
            ?>
            <div class="tit-period" role="group" aria-label="Pick a period">
              <label class="tit-daterange-part">
                <span class="tit-daterange-l">Year</span>
                <select id="tit-f-yearsel">
                  <option value="">Any</option>
                  <?php if ($yr_hi > 0) : for ($y = $yr_hi; $y >= $yr_lo; $y--) : ?>
                    <option value="<?php echo esc_attr($y); ?>"><?php echo esc_html($y); ?></option>
                  <?php endfor; endif; ?>
                </select>
              </label>
              <label class="tit-daterange-part">
                <span class="tit-daterange-l">Quarter</span>
                <select id="tit-f-quartersel">
                  <option value="">Any</option>
                  <?php foreach (array(1, 2, 3, 4) as $q) : ?>
                    <option value="<?php echo esc_attr($q); ?>">Q<?php echo esc_html($q); ?></option>
                  <?php endforeach; ?>
                </select>
              </label>
              <label class="tit-daterange-part">
                <span class="tit-daterange-l">Month</span>
                <select id="tit-f-monthsel">
                  <option value="">Any</option>
                  <?php for ($m = 1; $m <= 12; $m++) : ?>
                    <option value="<?php echo esc_attr($m); ?>"><?php
                      echo esc_html(date_i18n('M', mktime(12, 0, 0, $m, 1, 2026))); ?></option>
                  <?php endfor; ?>
                </select>
              </label>
            </div>
            <div class="tit-daterange">
              <label class="tit-daterange-part">
                <span class="tit-daterange-l">From</span>
                <input type="date" id="tit-f-since"
                       min="<?php echo esc_attr($span_lo); ?>" max="<?php echo esc_attr($span_hi); ?>">
              </label>
              <label class="tit-daterange-part">
                <span class="tit-daterange-l">To</span>
                <input type="date" id="tit-f-until"
                       min="<?php echo esc_attr($span_lo); ?>" max="<?php echo esc_attr($span_hi); ?>">
              </label>
            </div>
          </div>
        </div>
      </div>

          </div>
        </div>

        <div class="tit-results">
      <?php /* The state the visible controls drive. Hidden, never focusable,
               and deliberately still real select elements: every existing
               mechanism (the querystring, the chips bar, the exports, the
               click-to-filter charts, the matrix cells) reads and writes these,
               and re-pointing all of it at new state would have turned a
               presentation change into a semantics change.

               `direction` lives here now rather than in More filters. It was
               showing as a second control also labelled "Headcount", beside the
               primary-row checkbox, with different behaviour: one label, two
               controls, which is worse than either alone. Hiring is still
               reachable through "What are you looking for". */ ?>
      <div class="tit-state" hidden aria-hidden="true">
        <select id="tit-f-pillar" tabindex="-1">
          <option value=""></option>
          <?php foreach ($labels as $k => $v) : ?>
            <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
          <?php endforeach; ?>
        </select>
        <select id="tit-f-direction" tabindex="-1">
          <option value=""></option>
          <?php foreach ($directions as $k => $v) : ?>
            <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
          <?php endforeach; ?>
        </select>
        <select id="tit-f-country" tabindex="-1"><option value=""></option></select>
        <select id="tit-f-state" tabindex="-1"><option value=""></option></select>
        <select id="tit-f-city" tabindex="-1"><option value=""></option></select>
        <select id="tit-f-country_basis" tabindex="-1">
          <option value="any"></option>
          <option value="location"></option>
        </select>
        <?php /* Driven by the "Moves Headcount" quick view now rather than by a
                 checkbox in the panel. It stays a real input in the same place
                 as the rest of the state, so applyUrlState(), the chips bar, the
                 exports and every share link already in the wild keep reading
                 and writing it with nothing changed. */ ?>
        <input type="checkbox" id="tit-f-stated_headcount" value="1" tabindex="-1">
      </div>

      <!--
        What is currently being filtered, in words, with a way out of each one.
        Nine controls spread over three rows means a reader can easily be looking
        at a narrowed page without remembering why. This is the sibling's best
        idea and the cheapest thing on the page: the dashboard states its own
        scope instead of leaving the reader to reconstruct it.
      -->
      <div class="tit-active tit-zone-controls" id="tit-active" hidden>
        <span class="tit-active-label">Filtering</span>
        <span class="tit-active-chips" id="tit-active-chips"></span>
      </div>

      <?php
      /*
        THE CHARTS SIT BETWEEN THE FILTERS AND THE UPDATES, at the owner's ask
        (1.63.0). They were below the updates, which put twelve chart cards
        behind fifty result cards and, on a phone, about twenty thousand pixels
        of scrolling; and the sentence by the filter bar promising that the
        charts follow the filters was making a claim about something a reader
        had no way of seeing.

        The ORDER is now: quick views, filters, what is currently filtered,
        the charts, then the control that orders the updates, then the updates.
        Two rules decided the two seams. The chips bar states what the filters
        are doing, so it stays WITH the filters and reads as their result. The
        Routine Filings and Sort controls order the update list and nothing
        else, so they stay directly on top of it.

        Nothing about what a chart click MEANS has changed: every row is still
        a button that writes the same hidden select, so click-to-filter, the
        querystring and every share link in the wild are untouched. This block
        moved as one, unedited, other than the directional lines noted through
        the file.

        The charts are INSIDE .tit-results, which is the same subtree the rows
        are in. .tit-results sets no overflow and neither does .tit-feed, so
        the sticky filter bar above is unaffected -- a scrollable ancestor is
        the one thing that cancels `position:sticky` outright, and it fails
        silently, so read the note by .tit-filterbar before nesting anything
        else here.
      */
      ?>
      <?php /* id, not just a class: dashboard.js marks this whole zone busy
               while the one /aggregate call every chart in it depends on is in
               flight (busyTrack in assets/dashboard.js). */ ?>
      <div class="tit-zone tit-zone-insight" id="tit-zone-insight">
      <div class="tit-sec">
        <h3>What The Data Says</h3>
        <p>Click any row to narrow the whole page to it.</p>
      </div>

      <?php
      /*
        THE NINE CHARTS ARE GROUPED UNDER THE QUESTION EACH GROUP ANSWERS,
        which is the sibling layoff tracker's pattern ("Where the cuts are",
        "How it is trending", "Who is cutting, and why") ported here. One
        generic "What The Data Says" over nine cards made a reader scan all
        nine to find the one that answers their question. Geography leads
        because it is the first thing all three audiences filter by, then
        time, then the rest, then money. The grouping is headings and grid
        containers only: every card keeps its id, so click-to-filter, the
        share links in the wild and every repaint in dashboard.js are
        untouched.
      */
      ?>
      <?php
      /*
        EVERY CARD TITLE NAMES ITS DIMENSION AND ITS UNIT, and this overrides an
        earlier decision on this page that is worth stating rather than quietly
        reversing. The comment on the "kind" card below still argues the old
        line: that a heading should name what a reader GETS from the chart and
        not what the chart is made of. That produced "Where the Jobs Are" over a
        ranking of RECORD COUNTS, so a bar reading "United Kingdom 7,955" told a
        reader there were 7,955 jobs there when the figure was 7,955 updates, 97
        percent of them one filing. A title that names the wrong quantity is a
        wrong number written in words, and it travels further than the chart
        does: it is what a share link, a screenshot and a headline all carry.

        The reader-facing voice did not go anywhere. It moved UP, to these four
        section headings, which are questions ("Where The Activity Is", "How It
        Is Trending") and carry the interest. The cards underneath them are
        allowed to be flat and exact, and being flat is what lets a reader tell
        this card from the money card of nearly the same name.

        The unit is one of three words on this page and never a fourth: UPDATES
        (a count of rows we hold), US DOLLARS (summed, and only the rows carrying
        a figure), or a rate of updates per day. Nothing here counts jobs, and
        nothing here counts companies. If a future card counts either, it says so
        in its own title.
      */
      ?>
      <h4 class="tit-charts-h">Where The Activity Is</h4>
      <div class="tit-charts tit-charts--one">
        <div class="tit-chart" id="chart-place">
          <?php
          /*
            THE CAVEAT IS PROSE ON THE CARD, AND IT IS NOT A LINE IN THE (i).

            It used to be passed into tit_chart_head() as note_html, which put it
            inside .tit-chart-note. That panel ships open from the server and is
            then CLOSED by dashboard.js on load (see setNote(chart, false) in the
            wiring loop), so on every browser that ran the script the element
            computed display:none and measured 0x0. A live audit found it that
            way. Every argument for putting it behind the (i) was sound and every
            one of them was about prose a reader can choose to read; this is not
            that. It is a retraction of the number sitting next to it, and a
            retraction nobody has ever seen is not a retraction.

            ABOVE THE RANKING, not under it. The base .tit-chart-caveat rule has
            carried `margin:0 0 9px` since it was written, which is the spacing
            of something that sits on top of what it qualifies, and the reason is
            the one that matters here: the dominated country is by definition one
            of the longest bars, so it is near the top of a list that is forty
            rows long on a phone. A correction printed below all forty arrives
            after the misreading it exists to prevent.

            Same element, same id, so dashboard.js keeps rewriting the text and
            re-hiding it when no country is dominated under the filters in force.
            It is now genuinely hidden only when it is genuinely not true.
          */
          tit_chart_head('Updates by Country',
            "Counted where the work sits; head office stands in when no place is"
            . " named. Every bar is a count of updates we hold, never a count of jobs.",
            'place'); ?>
          <p class="tit-chart-caveat" id="tit-place-caveat"<?php
            echo $place_caveat === '' ? ' hidden' : ''; ?>><?php
            echo esc_html($place_caveat); ?></p>
          <div class="tit-rank" tabindex="0" role="group" aria-label="Activity by place"
               aria-describedby="<?php echo esc_attr(tit_chart_note_id('place')); ?>">
            <?php
            $cmax = $by_country ? max(array_map('intval', array_column($by_country, 'n'))) : 1;
            foreach ($by_country as $c) : ?>
              <button type="button" class="tit-rank-row" data-k="<?php echo esc_attr($c['k']); ?>" aria-pressed="false">
                <span class="tit-rank-name"><?php echo tit_country_label_html($c['k']); ?></span>
                <span class="tit-rank-track"><span class="tit-rank-fill"
                  style="width:<?php echo esc_attr(max(4, round(100 * $c['n'] / $cmax))); ?>%"></span></span>
                <span class="tit-rank-n"><?php echo (int) $c['n']; ?></span>
              </button>
            <?php endforeach; ?>
          </div>
        </div>
      </div>

      <h4 class="tit-charts-h">How It Is Trending</h4>
      <div class="tit-charts tit-charts--one">
        <?php
        /*
          THE MARKET TREND, and it is deliberately NOT the collection-rate
          chart that used to sit here. "Updates Collected a Day" plotted our
          own collection rate, which is an operations measure, and it renders
          on the sources page now beside the collectors it describes. This
          card makes the market claim instead, on same-store-sales logic; the
          whole argument is above tit_market_trend().

          It is a whole-tracker card and it does NOT move with the filters:
          the panel is a property of the collector fleet, not of a view, and
          a filtered fixed-panel trend thins out faster than it can stay
          honest. The visible caveat says so on the card. dashboard.js
          repaints nothing inside it.

          The direction ranking that used to be its own card ("Updates by
          Stated Headcount Direction") is the SPLIT inside this chart now; the
          by_direction group stays on /aggregate for anything that consumed
          it. tit-chart-nodl keeps the CSV button off a card whose bars are
          not rank rows; the download would have been an empty file.
        */
        ?>
        <div class="tit-chart tit-chart-nodl" id="chart-market">
          <?php tit_chart_head('Weekly Updates by Stated Headcount Direction',
            'Each bar is one week of updates, split by the headcount direction the source '
            . 'itself stated. Most updates say nothing about headcount, and those are counted '
            . 'as such rather than guessed.', 'market'); ?>
          <?php /* Visible prose, ABOVE the drawing, never note_html: the (i)
                   panel is closed by dashboard.js on load, and this sentence
                   is the basis of the whole card. */ ?>
          <p class="tit-chart-caveat" id="tit-market-caveat"><?php
            echo esc_html(tit_market_caveat($market)); ?></p>
          <div class="tit-market-box" role="group" aria-label="Weekly updates by stated headcount direction"
               aria-describedby="<?php echo esc_attr(tit_chart_note_id('market')); ?>">
            <?php echo tit_market_trend_html($market); ?>
          </div>
        </div>
      </div>

      <h4 class="tit-charts-h">What Kind Of Moves, And How We Know</h4>
      <?php /* Three cards since the direction ranking merged into the market
               trend, so the base three-column grid, not the 2x2 --four. */ ?>
      <div class="tit-charts">
      <div class="tit-chart" id="chart-kind">
        <?php /* Headings name what a recruiter or job seeker GETS from the chart,
                 not what the chart is made of. "What kind of update" described
                 the axis; "What is moving" answers the question they opened the
                 page with. The rows are buttons because they ARE filters:
                 dashboard.js routes a click through the same state as the
                 dropdowns, so the subtitle may promise it. Buttons hold span
                 children only (phrasing content), never divs. */ ?>
        <?php tit_chart_head('Updates by Kind of Move',
          'How many updates we hold of each kind. The section heading above is the'
          . ' question; this is the quantity that answers it.', 'kind'); ?>
      <div class="tit-pillars" role="group" aria-label="Activity by kind"
           aria-describedby="<?php echo esc_attr(tit_chart_note_id('kind')); ?>">
        <?php foreach ($by_pillar as $p) :
            $key = $p['pillar'];
            $pct = $total ? round(100 * $p['n'] / $total) : 0; ?>
          <button type="button" class="tit-pillar" data-k="<?php echo esc_attr($key); ?>" aria-pressed="false">
            <span class="tit-pillar-head">
              <span class="tit-pillar-name"><?php echo esc_html($labels[$key] ?? $key); ?></span>
              <span class="tit-pillar-n"><?php echo esc_html(number_format_i18n($p['n'])); ?></span>
            </span>
            <span class="tit-bar"><span style="width:<?php echo esc_attr($pct); ?>%"></span></span>
          </button>
        <?php endforeach; ?>
      </div>
      </div>
        <?php
        /*
          NO STANDALONE DIRECTION CARD. "Updates by Stated Headcount
          Direction" is the split inside the market trend above now, so a
          second card of the same numbers was a duplicate. The direction
          FILTER control stays (tit-f-direction), the by_direction group
          stays on /aggregate, and the query behind it stays in
          tit_dashboard_facts because the stated-headcount toggle's count is
          summed from it.
        */
        ?>
        <?php
        /*
          HOW SOLID THE EVIDENCE IS. The credibility claim, drawn.

          Every other card on this page counts what happened. This one counts
          how we know, and it is the only card that answers the question a
          reader should be asking of a tracker. The vocabulary is the shared
          one (docs/card-contract.json: Official Filing, News Report,
          Unconfirmed), it is the same word the badge on every result card
          carries, and clicking a bar sets the Evidence control, so a reader can
          go from "how much of this is filed" to reading only the filed rows in
          one click.

          It is TWO bars on a live view rather than three, because nothing here
          is stored as rumored. That is the honest shape of the data and not a
          thin card: a ranking that shows a bucket at zero would be inventing a
          category to fill a box.
        */
        ?>
        <div class="tit-chart" id="chart-confidence">
          <?php tit_chart_head('Updates by Strength of Evidence', 'What each update is based on. A news report is never promoted to a filing.', 'confidence'); ?>
          <div class="tit-rank" tabindex="0" role="group" aria-label="Activity by evidence"
               aria-describedby="<?php echo esc_attr(tit_chart_note_id('confidence')); ?>">
            <?php
            $conf_labels = tit_confidence_labels();
            $fmax = $by_confidence ? max(array_map('intval', array_column($by_confidence, 'n'))) : 1;
            foreach ($by_confidence as $f) : ?>
              <button type="button" class="tit-rank-row" data-k="<?php echo esc_attr($f['k']); ?>" aria-pressed="false">
                <span class="tit-rank-name"><?php echo esc_html($conf_labels[$f['k']] ?? $f['k']); ?></span>
                <span class="tit-rank-track"><span class="tit-rank-fill"
                  style="width:<?php echo esc_attr(max(4, round(100 * $f['n'] / $fmax))); ?>%"></span></span>
                <span class="tit-rank-n"><?php echo (int) $f['n']; ?></span>
              </button>
            <?php endforeach; ?>
          </div>
        </div>

        <?php
        /*
          WHICH INDUSTRIES ARE MOVING, BY COUNT, and it is not the money card
          with different numbers. That one ranks summed dollars and can only see
          the rows that carry a figure; a sector hiring hard and raising nothing
          is invisible there and is here. For a job seeker choosing where to
          look, the count is the more useful of the two, which is why both are
          on the page rather than one standing in for the other.
        */
        ?>
        <div class="tit-chart" id="chart-industry">
          <?php /* "Updates by Industry" against the money card's "Money Raised
                   by Industry": one word apart in the title is what stops the
                   two being read as the same ranking with different numbers. */ ?>
          <?php tit_chart_head('Updates by Industry', 'Counted by updates, not by dollars.', 'industry'); ?>
          <div class="tit-rank" tabindex="0" role="group" aria-label="Activity by industry"
               aria-describedby="<?php echo esc_attr(tit_chart_note_id('industry')); ?>">
            <?php
            $imax = $by_industry_n ? max(array_map('intval', array_column($by_industry_n, 'n'))) : 1;
            foreach ($by_industry_n as $i) : ?>
              <button type="button" class="tit-rank-row" data-k="<?php echo esc_attr($i['k']); ?>" aria-pressed="false">
                <span class="tit-rank-name"><?php echo esc_html($industries[$i['k']] ?? $i['k']); ?></span>
                <span class="tit-rank-track"><span class="tit-rank-fill"
                  style="width:<?php echo esc_attr(max(4, round(100 * $i['n'] / $imax))); ?>%"></span></span>
                <span class="tit-rank-n"><?php echo (int) $i['n']; ?></span>
              </button>
            <?php endforeach; ?>
          </div>
        </div>
      </div>

      <?php
      /*
        THE MONEY CARDS LOST THEIR SECTION HEADING, AND ONLY THEIR HEADING.

        The owner pasted this exact text and said "remove this":

          Where The Money Went
          Funding rounds added up in US dollars. Click a row to narrow the page.

        That is the standalone heading block, and it earned the complaint: it sat
        eight lines under "What The Data Says / Click any row to narrow the whole
        page to it" and repeated the second half of it word for word, so the page
        gave the same instruction twice and split one grid of cards into two
        sections for no reason a reader could see.

        The CARDS are not the heading and they stay. The owner separately said of
        this panel "love this format for both sites", and the design mock keeps a
        money card inside the same grid, so deleting the cards would have removed
        the thing that was praised because it happened to sit under the words that
        were not. Three rankings of summed US dollars, each still printing what
        its total is based on, now reading as a continuation of the grid above
        rather than as a second section.
      */
      ?>
      <h4 class="tit-charts-h">Where The Money Is Going</h4>
      <?php
      /*
        THE CURRENCY CAVEAT HAS ONE HOME NOW, AND THIS IS IT.

        The full sentence ("Totals cover the N of M funding updates that state
        a US dollar amount; amounts in other currencies are left out rather
        than converted at a rate nobody published") was printed five times on
        this page: under the dated strip, in the matrix note, and once per
        money card. One caveat, one home: the full sentence lives here, once,
        over the cards it governs, and every other money figure carries a
        short "USD-stated amounts only" pointer instead of the paragraph.
        dashboard.js repaints the sentence under the filters in force
        (#tit-usd-note-p), so it always describes the view on screen.

        A <details> rather than the chart-card (i) pattern because it is not a
        chart: a native disclosure needs no script to open, so a reader with
        no JavaScript can still reach every word, and a crawler sees it in the
        initial HTML.
      */
      ?>
      <details class="tit-note-details tit-usd-note" id="tit-usd-note">
        <summary>About The Money Figures</summary>
        <p id="tit-usd-note-p"><?php
          echo esc_html(tit_money_coverage_sentence($money['coverage'] ?? null)); ?></p>
      </details>
      <div class="tit-charts tit-charts-money">
        <?php
        tit_money_chart(
            'country', 'Money Raised by Country',
            "Funding rounds added up, in US dollars, by country.",
            $money['by_country'], $money, 'country',
            function ($k) { return tit_country_label_html($k); }, true
        );
        tit_money_chart(
            /* "Money Raised by City", matching its two siblings, and the mock's
               "Where the money went" is retired here. It was the only card in
               the money trio whose title named neither its dimension nor its
               unit, and it sat between two that named both, so the odd one out
               read as a different KIND of figure rather than the same figure cut
               a third way. docs/HANDOVER.md already recorded this rename as the
               right one; this is it landing. */
            /* The subtitle names the BASIS as well as the dimension, in the
               same words the place ribbon uses. This chart groups by
               tit_city_expr(), which is COALESCE(city, hq_city), so a round is
               counted where the update states it happened OR where the
               employer is based. Naming that here and not there would leave one
               of two surfaces reading the same column saying what it counts. */
            'city', 'Money Raised by City',
            "Funding rounds added up, in US dollars, by the city an update"
            . " states or, failing that, the city the employer is based in.",
            $money['by_city'], $money, 'city',
            function ($k) { return $k; }
        );
        tit_money_chart(
            'industry', 'Money Raised by Industry',
            'Funding rounds added up, in US dollars, by industry.',
            $money['by_industry'], $money, 'industry',
            function ($k) use ($industries) { return $industries[$k] ?? $k; }
        );
        ?>
      </div>
      </div><!-- /.tit-zone-insight -->

      <div class="tit-zone tit-zone-updates">
      <!--
        The detail control, and the promise that goes with it.

        A default that sets thousands of rows aside has to be visible, has to
        state both counts, and has to say what it means by routine, all in the
        place the reader is about to look at rows. A quiet default with no
        explanation would be withholding data; a page that leads with two
        thousand CFO appointments would be burying it. This is the only way to
        do both.
      -->
      <div class="tit-detail">
        <?php /* The control is named by WHAT IT FILTERS, not by the abstract
                 job it does. "Show: Notable updates" told a reader neither what
                 was being hidden nor why, and made them hold three numbers in
                 their head to work out what they were looking at. */ ?>
        <?php /* ONE name for the control, and options that read as values of
                 that name rather than as two more labels. It was
                 "Officer and director filings" over "Hide the routine ones",
                 which is a noun phrase over an imperative: neither one told a
                 reader what state the control was IN. "Routine filings: Hidden /
                 Shown" is a setting and its value, which is what this is. */ ?>
        <label class="tit-detail-pick">
          <span class="tit-detail-l">Routine Filings</span>
          <select id="tit-f-detail" aria-label="Routine filings">
            <option value="notable">Hidden</option>
            <option value="all">Shown</option>
          </select>
        </label>
        <p class="tit-detail-note" id="tit-detail-note"><?php
          echo esc_html(tit_detail_note('notable', $n_notable, $n_routine)); ?></p>
        <?php /* The sort belongs with the rows it orders. It used to sit up in
                 the quick-views strip, three screens above the table, which is
                 where a reader chooses a VIEW and not where they reorder one
                 they are already reading.

                 IT IS NOW THE ONLY SORT CONTROL. Four sortable column headers
                 used to sit below it, and a card list has no column headers to
                 hang them on. Every ordering they offered is an option here
                 instead, so nothing a reader could reach before became
                 unreachable, and the `sort` parameter they wrote is unchanged:
                 old share links still land on the ordering they name. */ ?>
        <label class="tit-detail-sort">
          <span class="tit-detail-l">Sort</span>
          <select id="tit-f-sort" class="tit-sort" aria-label="Sort the updates">
            <?php /* Its own option, never a silent tweak to "Newest first": a
                     control labelled newest that does not put the newest row
                     first is a control that lies. */ ?>
            <option value="notable">Most Useful First</option>
            <option value="newest">Newest First</option>
            <option value="oldest">Oldest First</option>
            <option value="employer">Employer A to Z</option>
            <?php /* Inherited from the retired column headers. The values are
                     the ones those headers already sent to /query, which is why
                     a link somebody saved from the old page still works. */ ?>
            <option value="employer_desc">Employer Z to A</option>
            <option value="place">By Place</option>
            <option value="place_desc">By Place, Reversed</option>
            <option value="evidence">Strongest Evidence First</option>
            <option value="evidence_desc">Weakest Evidence First</option>
            <?php /* Sorting on money only works because funding_amount_usd is
                     a number; the display string beside it cannot be ordered. */ ?>
            <option value="raised">Biggest Raises First</option>
          </select>
        </label>
        <?php /* Cards or a compact table, one view at a time. Ships hidden and
                 is revealed by script: the table is built client-side from the
                 same rows the cards show, so without JavaScript the buttons
                 would be chrome that does nothing and the reader keeps the
                 server-rendered cards. The choice persists in localStorage and
                 the CARD markup is untouched either way; the table is a
                 sibling rendering, not a restyle of the card (see
                 docs/card-contract.json). */ ?>
        <div class="tit-viewtoggle" id="tit-viewtoggle" role="group"
             aria-label="How to show the updates" hidden>
          <span class="tit-detail-l">View</span>
          <button type="button" class="tit-vt" id="tit-vt-cards"
                  aria-pressed="true">Cards</button>
          <button type="button" class="tit-vt" id="tit-vt-table"
                  aria-pressed="false">Table</button>
        </div>
      </div>

      <?php
      /*
        THE JOB-BOARD CAVEAT, ONCE, FOR EVERY BOARD-READ CARD AT ONCE.

        Every update our board counter produces used to carry the identical
        44-word read-through, verbatim, card after card; the pay-band rows had
        their own twin. The records keep the text (a revision is never
        rewritten), but the page now prints it once here and the cards say
        only their fact: tit_card_html() and renderCard() both suppress a
        read-through that exactly matches tit_boilerplate_readthroughs().
      */
      ?>
      <details class="tit-note-details tit-rt-note">
        <summary>About Job Board Readings</summary>
        <p>Some updates come from our own daily count of an employer's
           published job board rather than from a document. A growing board
           moves before any announcement or filing, so treat the direction as
           the signal and the exact count as approximate: roles get reposted,
           split across locations and withdrawn without notice. Advertised pay
           bands are the employer's ask, not the settlement.</p>
      </details>

      <?php /* The comment that stood here described a section marker that is
               not here and has not been for two releases: it belonged to
               #tit-filter-sec, which sits with the quick views. Its one live
               fact -- that the id is the phone jump bar's scroll target -- is
               written down beside that element instead, where deleting the
               element would put it in front of whoever was about to. */ ?>
      <?php
      /*
        THE RESULTS ARE CARDS, AND THEY ARE THE SIBLING'S CARDS.

        This was a seven-column table that turned itself into cards below 860px
        with a stack of @media rules, and every one of those rules was a second
        description of the same layout waiting to disagree with the first. It is
        one card now, at every width.

        The shape is fixed in docs/card-contract.json, which is BYTE-IDENTICAL to
        the copy in the AI Layoff Tracker: same regions, same class suffixes,
        same badge order, same four direction words. The two products render the
        same kind of fact and had drifted into two designs and two vocabularies,
        with neither side able to say which was current. tit_card_html() below
        is the one renderer; renderCard() in dashboard.js reprints the same
        markup on every repaint; tests/test_card_contract.py pins both against
        the contract, and .github/workflows/card-contract.yml pins the contract
        against the sibling's copy.

        The <ul> keeps the id `tit-rows` the JavaScript already replaces, so the
        filter path is untouched by this change.
      */
      ?>
      <ul class="tit-cards" id="tit-rows">
        <?php foreach ($rows as $r) { echo tit_card_html($r); } ?>
      </ul>

      <?php /* The table the View toggle swaps in. Empty on the server on
               purpose: it is filled client-side from the SAME /query rows the
               cards render, so it can never show a different set, and a
               reader without JavaScript never meets an empty box. It scrolls
               inside its own container at every width (its own class, NOT
               .tit-table-scroll, whose sub-860px rules belong to other
               pages' tables). */ ?>
      <div class="tit-updates-scroll" id="tit-tablewrap" hidden></div>

      <!--
        Download exactly what the filters show. The hrefs are server-rendered
        pointing at the whole dataset; dashboard.js rewrites them with the
        current querystring on every refresh, and the scope word flips between
        "all" and "filtered" so the link says which set it hands over.
      -->
      <div class="tit-export">
        <span class="tit-export-label">Download This View</span>
        <a class="tit-export-link" id="tit-export-csv"
           data-base="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_csv')); ?>"
           href="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_csv')); ?>">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/></svg>
          CSV<span class="tit-export-scope" id="tit-export-csv-scope"> · all</span></a>
        <a class="tit-export-link" id="tit-export-json"
           data-base="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_json')); ?>"
           href="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_json')); ?>">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/></svg>
          JSON<span class="tit-export-scope" id="tit-export-json-scope"> · all</span></a>
        <?php /* The same rows with headers each CRM's import wizard maps by
                 name. Mapping and vendor docs: includes/export_crm.php. */ ?>
        <a class="tit-export-link" id="tit-export-hubspot"
           data-base="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_hubspot')); ?>"
           href="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_hubspot')); ?>">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/></svg>
          CSV for HubSpot<span class="tit-export-scope" id="tit-export-hubspot-scope"> · all</span></a>
        <a class="tit-export-link" id="tit-export-salesforce"
           data-base="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_salesforce')); ?>"
           href="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_salesforce')); ?>">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/></svg>
          CSV for Salesforce<span class="tit-export-scope" id="tit-export-salesforce-scope"> · all</span></a>
        <?php /* The feed of this view. Same filter params as /query; the
                 unfiltered feed is also announced with a <link rel=alternate>
                 in the head (includes/feed.php). */ ?>
        <a class="tit-export-link tit-export-rss" id="tit-export-rss"
           data-base="<?php echo esc_url(rest_url('talent/v1/feed')); ?>"
           href="<?php echo esc_url(rest_url('talent/v1/feed')); ?>"
           title="RSS feed of this view">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M5 19a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM4 10a10 10 0 0 1 10 10M4 4a16 16 0 0 1 16 16"/></svg>
          RSS<span class="tit-export-scope" id="tit-export-rss-scope"> · all</span></a>
        <span class="tit-export-note">Every matching update, not just this page,
          and routine filings are included whichever way Show is set. Each row
          carries its own materiality, so you can set them aside yourself.
          The HubSpot and Salesforce files hold the same rows with headers
          their import wizards map by name. RSS carries the newest 50 of this
          view. Free to reuse, CC BY 4.0.</span>
      </div>
      </div><!-- /.tit-zone-updates -->

        </div><!-- /.tit-results -->
      </div><!-- /.tit-feed -->

      <?php echo tit_trust_panel_html($facts); ?>

      <?php /* Shared email digest signup. The subscriber store, the consent
               flow and the sender live in the SIBLING plugin (AI Layoff
               Tracker, includes/subscribe.php): one WordPress install, one
               subscriber list, one consent record per person. This call is
               deliberately function_exists-guarded and never a require, so
               the isolation promise at the top of this plugin holds: if the
               sibling is missing or mid-deploy, nothing renders and nothing
               fatals. */
      if (function_exists('alt_digest_subscribe_form')) {
          echo alt_digest_subscribe_form('talent');
      } ?>

      <p class="tit-cite">
        Data licensed CC BY 4.0. Cite as: Talent Intelligence Tracker,
        asktherecruiter.com. Layoff and redundancy data is not collected here;
        see the
        <a href="/blog/ai-layoff-tracker/">AI Layoff Tracker</a>.
        <?php /* In the footer, not buried: a corrections log nobody can find
                 is not a disclosure. */ ?>
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/corrections/')); ?>">Corrections</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">Sources</a>
        &middot;
        <?php /* These routes are in no theme menu, so a page linked from
                 nowhere is a page a crawler finds slowly through the sitemap
                 and trusts less. The press page is the one addressed to
                 somebody about to quote us, which makes it the worst one to
                 leave unreachable. */ ?>
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/press/')); ?>">Press</a>
      </p>
    </div>
    <?php
    return ob_get_clean();
}
add_shortcode('talent_intelligence_dashboard', 'tit_dashboard_shortcode');

/**
 * "WHY YOU CAN TRUST THIS", AND THE FAQ, AS TWO TABS OVER ONE PANEL.
 *
 * The trust half is the design mock's, and it did not exist anywhere before
 * this: not in this repo, not in the sibling, not on the live page. The first
 * design pass declined to author it from a description of a screenshot, which
 * was right; the mock itself is on disk now, so this is built from it.
 *
 * TWO FIXES TO THE MOCK, both structural.
 *
 * The mock lays the four numbered items out in a `repeat(auto-fit, minmax(210px,
 * 1fr))` grid inside a `flex: 999 1 420px` column, which at most desktop widths
 * resolves to three columns and drops the fourth item alone onto a second row.
 * Four items in a three-column grid is an orphan at every width where it fits
 * three. It is 2x2 and then four-across here, and stacks at 390px.
 *
 * And the mock has no FAQ at all. The owner asked for one tucked into a tab
 * here, and there was no existing FAQ anywhere in this product to move — checked
 * before writing, because two FAQs that drift apart is worse than one. The only
 * FAQ-shaped thing in the codebase is a WARNING: company.php and places.php both
 * record that the sibling earned a manual-action risk emitting identical
 * FAQPage structured data across ~1,830 URLs where the answers were not visible
 * in the document.
 *
 * WHICH IS WHY EVERY PANEL IS IN THE INITIAL HTML AND NOTHING IS FETCHED.
 *
 * A tab that loads its content on click hides that content from a crawler, and
 * an FAQ is among the most valuable blocks on a page for search. So both panels
 * are rendered server-side, always, in full. JavaScript's entire job here is to
 * put a class on the container; the stylesheet does the hiding, and only once
 * that class is present. With JavaScript off — or before it runs, or if
 * Autoptimize swallows it — a reader gets both panels stacked with their own
 * headings, which is a slightly longer page and not a broken one. The tab strip
 * itself is hidden until the class lands, so nobody is offered a control that
 * cannot work.
 *
 * EVERY NUMBER IN THE COPY IS COMPUTED. The sibling's press page still carries
 * a hardcoded "51 of the most significant layoffs ... we currently carry every
 * one of them" with no query behind it, and corrections.php here once shipped a
 * typed "$124.0bn" captioned "Measured now" against a live figure of $101B. A
 * panel whose entire subject is trustworthiness is the last place on the site
 * that can afford a stale figure, so the ones here come off the same bundle the
 * hero does and move with it.
 */
function tit_trust_panel_html(array $facts) {
    $notable  = (int) ($facts['notable'] ?? 0);
    $routine  = (int) ($facts['routine'] ?? 0);
    $verified = (int) ($facts['verified'] ?? 0);
    $total_all = (int) ($facts['total_all'] ?? 0);
    $companies = (int) ($facts['companies'] ?? 0);
    $countries = (int) ($facts['countries'] ?? 0);
    $newest    = (string) ($facts['newest_run'] ?? '');
    $money     = $facts['money'] ?? array();
    $cov_with  = (int) ($money['coverage']['with'] ?? 0);
    $cov_all   = (int) ($money['coverage']['all'] ?? 0);

    // How many updates state which way headcount is going, and how many do not.
    // Both computed: "most say nothing" is a claim, and this is the arithmetic
    // behind it.
    $stated = (int) ($facts['stated'] ?? 0);
    $unstated = max(0, $notable - $stated);

    $n = function ($v) { return number_format_i18n((int) $v); };

    /*
      THE FOUR ITEMS. Numbered because the mock numbers them, and the numbers
      are ordinals rather than data, so they are written here rather than
      computed. Everything inside the prose is not.
    */
    $items = array(
        array('Sourced', sprintf(
            'Every line links to the filing or report behind it, and a record '
            . 'with no source URL is rejected rather than published. %s of the '
            . '%s updates in this view come straight from an official filing.',
            $n($verified), $n($notable))),
        array('Unconverted', sprintf(
            'Amounts stated in another currency are left out of the totals '
            . 'rather than converted at a rate nobody published. The money '
            . 'figures cover the %s of %s funding updates that state a US '
            . 'dollar amount, and every total says so beside itself.',
            $n($cov_with), $n($cov_all))),
        array('Unguessed', sprintf(
            'Most updates say nothing about headcount. We label %s of %s as '
            . 'not stated rather than infer a direction, and no figure appears '
            . 'in a summary unless the source states it in those words.',
            $n($unstated), $n($notable))),
        array('Correctable', sprintf(
            'A correction appends a revision and never overwrites the record, '
            . 'so what we said on an earlier date stays answerable. Every one '
            . 'is logged in public with its date, the fields it touched and '
            . 'the number of records it reached.')),
    );

    /*
      THE FAQ. Written for this project rather than adapted from anywhere, and
      deliberately answering the questions this page actually raises: why some
      rows are missing, what the evidence labels mean, and what we know we do
      not hold. Nothing here claims more than the project can support — there is
      no "comprehensive", no "real time", and the automation claim names the
      human sliver rather than rounding it to 100%.
    */
    /*
      The cadence sentence is DERIVED from data/ingest-schedule.json, which is
      generated from collect.yml's own cron: the typed "twice a day, at 06:00
      and 18:00 UTC" it replaces stayed on the page after the schedule moved
      to once daily at 16:00 UTC. Without a schedule file the sentence simply
      states the last capture; an absent promise is honest, a stale one lies.
    */
    $cadence_phrase = tit_ingest_cadence_phrase();
    $cadence_times  = tit_ingest_times_label();
    $freshness_answer = ($cadence_phrase && $cadence_times)
        ? sprintf('Collection runs %s, at %s. ', $cadence_phrase, $cadence_times)
        : '';
    $freshness_answer .= sprintf(
        'The most recent capture was %s. Figures on this page are computed on '
        . 'request and cached for five minutes, so a correction or a fresh '
        . 'run appears immediately rather than on a schedule.',
        $newest ? tit_local_datetime($newest) : 'not recorded yet');

    $faqs = array(
        array('How often does this update?', $freshness_answer),
        array('What do the evidence labels mean?',
              'Verified means the claim was read from a primary document, '
              . 'usually a regulatory filing or an employer\'s own statement. '
              . 'Reported means a publication made the claim. Rumoured means '
              . 'the source itself hedged it. A label is capped by the kind of '
              . 'source it came from: a news article cannot become verified '
              . 'however confident it sounds, and nothing is promoted quietly.'),
        array('Why are layoffs not on this tracker?',
              'They are collected by the AI Layoff Tracker instead, and read '
              . 'from it rather than duplicated here, so there is one source of '
              . 'truth per fact. This tracker is the hiring side: who is '
              . 'adding roles, raising money, changing leadership or moving '
              . 'site.'),
        array('What does "headcount not stated" mean?',
              sprintf('It means the source said nothing about which way '
                    . 'headcount is going, which is true of %s of the %s '
                    . 'updates in this view. A funding round with no hiring '
                    . 'plan and a chief executive succession are both real '
                    . 'updates that state no direction. Guessing a direction '
                    . 'from them would be our claim rather than the document\'s.',
                    $n($unstated), $n($notable))),
        array('Why are some updates hidden by default?',
              sprintf('%s of the %s records we hold are routine officer and '
                    . 'director filings: accurate, verified, and in such volume '
                    . 'that they bury everything else. The default view sets '
                    . 'them aside and the Routine Filings control turns them '
                    . 'back on, states both counts, and says what we mean by '
                    . 'routine. Nothing is deleted.',
                    $n($routine), $n($total_all))),
        array('What do you know you are missing?',
              'We measure it rather than assert it. Every week we grade the '
              . 'collectors against a fixed set of real events, assembled from '
              . 'public sources without ever looking at our own database. We '
              . 'publish the result, including the countries and document '
              . 'types where we come off badly. The countries scoring zero are '
              . 'the roadmap.'),
        array('How much of this is automated?',
              'About 99%. Collection, classification, validation, deduplication '
              . 'and publishing all run without a human. Three things stay '
              . 'human: repairing a scraper when a site changes, judging '
              . 'whether a novel source is worth reading, and assembling each '
              . 'new recall test set. The last of those is human by design, '
              . 'because a test set built out of what is easy to find measures '
              . 'memory rather than reach.'),
        array('Can I reuse the data?',
              sprintf('Yes, under CC BY 4.0, citing the Talent Intelligence '
                    . 'Tracker. The CSV and JSON links take the current view '
                    . 'with its filters applied, and the press page carries the '
                    . 'headline figures with a link behind each one. There are '
                    . '%s updates across %s employers and %s countries in this '
                    . 'view right now.',
                    $n($notable), $n($companies), $n($countries))),
    );

    ob_start(); ?>
    <div class="tit-trust" id="tit-trust">
      <div class="tit-trust-lede">
        <h3>Why You Can Trust This</h3>
        <p>We publish the gaps as loudly as the numbers. If a source did not
           state something, we say so rather than fill it in.</p>
        <p class="tit-trust-btns">
          <a class="tit-btn tit-btn-solid"
             href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">Read the Method</a>
          <a class="tit-btn"
             href="<?php echo esc_url(home_url('/talent-intelligence-tracker/recall/')); ?>">What We Miss, Measured</a>
        </p>
      </div>

      <div class="tit-trust-main">
        <?php /* Hidden by the stylesheet until dashboard.js marks the panel as
                 tabbed, so a reader with no JavaScript is never shown a control
                 that cannot do anything. */ ?>
        <div class="tit-tabs" role="tablist" aria-label="How this tracker works">
          <button type="button" class="tit-tab" id="tit-tab-how" role="tab"
                  aria-controls="tit-panel-how" aria-selected="true">How It Works</button>
          <button type="button" class="tit-tab" id="tit-tab-faq" role="tab"
                  aria-controls="tit-panel-faq" aria-selected="false" tabindex="-1">Questions</button>
        </div>

        <section class="tit-tabpanel" id="tit-panel-how" role="tabpanel"
                 aria-labelledby="tit-tab-how" tabindex="0">
          <?php /* This heading is the no-JavaScript label for the panel. Once
                   the tab strip is live the tab is the label, so the stylesheet
                   hides it there rather than the markup omitting it. */ ?>
          <h4 class="tit-tabpanel-h">How It Works</h4>
          <ol class="tit-trust-items">
            <?php foreach ($items as $i => $it) : ?>
              <li>
                <span class="tit-trust-k"><?php
                  echo esc_html(sprintf('%02d', $i + 1)); ?> <?php
                  echo esc_html($it[0]); ?></span>
                <span class="tit-trust-t"><?php echo esc_html($it[1]); ?></span>
              </li>
            <?php endforeach; ?>
          </ol>
        </section>

        <section class="tit-tabpanel" id="tit-panel-faq" role="tabpanel"
                 aria-labelledby="tit-tab-faq" tabindex="0">
          <h4 class="tit-tabpanel-h">Questions</h4>
          <div class="tit-faq">
            <?php foreach ($faqs as $q) : ?>
              <h5 class="tit-faq-q"><?php echo esc_html($q[0]); ?></h5>
              <p class="tit-faq-a"><?php echo esc_html($q[1]); ?></p>
            <?php endforeach; ?>
          </div>
        </section>
      </div>
    </div>
    <?php
    /*
      FAQPage structured data, describing ONLY what is rendered above it.

      company.php and places.php both carry the warning this obeys: the sibling
      earned a manual-action risk emitting identical FAQPage markup across
      roughly 1,830 URLs where the answers were not visible anywhere in the
      document. Every question and answer below is in the initial HTML of this
      one page, in full, visible to a reader with no JavaScript — which is the
      condition that makes the markup honest, and is the same reason the panels
      are not lazy-loaded. One page, one block, answers on screen.
    */
    ?>
    <script type="application/ld+json"><?php
      echo wp_json_encode(array(
        '@context'   => 'https://schema.org',
        '@type'      => 'FAQPage',
        'mainEntity' => array_map(function ($q) {
            return array(
                '@type' => 'Question',
                'name'  => $q[0],
                'acceptedAnswer' => array('@type' => 'Answer', 'text' => $q[1]),
            );
        }, $faqs),
      ), JSON_UNESCAPED_SLASHES | JSON_HEX_TAG | JSON_HEX_AMP);
    ?></script>
    <?php
    return ob_get_clean();
}

/**
 * The date a row counts as happening on: the source's own reporting date, and
 * our capture date only when the source carried none.
 *
 * One expression, because the matrix, the dated panel and the trend chart all
 * bucket by it and a page whose three time views disagreed about what day a row
 * belongs to would be wrong in a way no reader could ever diagnose.
 */
function tit_signal_date_expr() {
    return 'COALESCE(published_date, DATE(captured_at))';
}

/**
 * THE SIGNAL ROWS: key, reader-facing label, the filter a click applies, the
 * SQL condition, and what the figures are (a COUNT of updates or a SUM of
 * dollars).
 *
 * Extracted from tit_glance_matrix() when the trend chart arrived, because the
 * chart plots the same signals the matrix counts and the two must not be able
 * to mean different things by "Adding Roles". They OVERLAP by design (a funded
 * employer can also be hiring), which the note under the matrix says out loud
 * so the rows are not read as a partition:
 *   Adding Roles       signal_direction = 'hiring'
 *   Funding Rounds     a funding amount or stage is present
 *   Total Raised       the dollars behind those rounds
 *   Leadership Moves   pillar = 'leadership_change'
 *   Pay and Benefits   pillar = 'rewards_comp'
 *   Everything in This View   every row, under the view's own clause
 *
 * "Money raised" is the one row that is not a count, which is exactly why it is
 * labelled, prefixed and coloured as money everywhere it appears. A reader who
 * mistakes a dollar sum for a number of updates has been misled by the table,
 * not by their own carelessness.
 *
 * THE LABELS ARE THE PAGE'S ONE VOCABULARY. See the note beside $labels in
 * tit_dashboard_html(); these are the same words the charts use, which they
 * were not before.
 *
 * Two of them earned more than a case change.
 *
 * "Total Raised" was "Money raised", sitting in a column of rows that count
 * updates while it alone sums dollars. That mismatch is why the block under the
 * matrix needs a paragraph to explain itself, and a label that forces an
 * explanation is the wrong label. "Total" says sum, and the unit rides on the
 * row as it always did.
 *
 * "Everything in This View" was "All updates", which a reader could not tell
 * included the 3,143 routine filings the page hides by default. It does not:
 * every figure sits under the same notable clause as the rows, and "in this
 * view" is the only phrase that says so without a footnote.
 */
function tit_signal_defs() {
    $funding = function_exists('tit_funding_where')
        ? tit_funding_where()
        : "((funding_amount IS NOT NULL AND funding_amount <> '')"
          . " OR (funding_stage IS NOT NULL AND funding_stage <> ''))";

    return array(
        array('hiring',     'Adding Roles',      'direction=hiring',         "signal_direction = 'hiring'", 'count'),
        array('funded',     'Funding Rounds',    'funding=1',                $funding,                      'count'),
        array('money',      'Total Raised',      'funding=1',                '',                            'money'),
        array('leadership', 'Leadership Moves',  'pillar=leadership_change', "pillar = 'leadership_change'", 'count'),
        array('pay',        'Pay and Benefits',  'pillar=rewards_comp',      "pillar = 'rewards_comp'", 'count'),
        array('total',      'Everything in This View', '',                    '1 = 1',                      'count'),
    );
}

/**
 * The at-a-glance matrix: signals down the side, periods across the top.
 *
 * This replaced five period tiles that each printed the same sentence shape.
 * On a young dataset several periods hold the same records, so the strip said
 * almost the same thing five times and never answered "what kind, and is it
 * accelerating". Rows are what a recruiter or job seeker actually asks for.
 *
 * Row definitions use existing columns only, and they OVERLAP by design (a
 * funded employer can also be hiring), which the note under the matrix says
 * out loud so the columns are not read as sums:
 *   Hiring up          signal_direction = 'hiring'
 *   Funding raised     funding_amount present
 *   Leadership moves   pillar = 'leadership_change'
 *   Pay changes        signal_direction = 'comp_shift' — the direction column,
 *                      NOT pillar = 'rewards_comp': direction is a closed
 *                      vocabulary the pipeline always sets, and it also
 *                      catches a pay change filed under another pillar.
 *   All updates        every row in the period (the emphasised total row)
 *
 * One query, not twenty-five: conditional aggregation over the widest window,
 * one SUM per cell. Dates come from published_date, the date the source
 * carries, falling back to capture date — same rule as everywhere else.
 * Quarter start stays derived, never hardcoded.
 *
 * $where/$params let /aggregate reuse this under the caller's own filters, so
 * the matrix the server renders and the one JavaScript repaints on a filtered
 * page cannot describe the world differently.
 */
function tit_glance_matrix($table, $where = 'is_current = 1', array $params = array()) {
    global $wpdb;
    $today = current_time('Y-m-d');
    $q_month = (intdiv((int) date('n', strtotime($today)) - 1, 3) * 3) + 1;
    /*
      No "Today" column.

      Collection runs on collect.yml's daily cron and every row carries the SOURCE's
      own reporting date, not the moment we captured it, so "Today" is
      structurally near-empty for most of the day: it read 0 across every single
      row. A column that is always zero does not report quiet news, it teaches
      the reader the tracker is dead. The shortest real window we can fill is a
      week.

      "YTD" rather than "so far", which the owner asked for; the year itself is
      still computed, never typed.
    */
    $periods = array(
        array('This week',    date('Y-m-d', strtotime($today . ' -6 days'))),
        array('This month',   date('Y-m-01', strtotime($today))),
        array('This quarter', sprintf('%s-%02d-01', date('Y', strtotime($today)), $q_month)),
        array(date('Y', strtotime($today)) . ' YTD', date('Y-01-01', strtotime($today))),
    );

    // The rows, their labels and their SQL, from the one place that holds them.
    $defs = tit_signal_defs();

    $date_expr = tit_signal_date_expr();
    $select = array();
    $select_params = array();
    foreach ($periods as $pi => $p) {
        foreach ($defs as $di => $d) {
            // A NULL funding_amount_usd is a figure we could not read as US
            // dollars, not a round of nothing, so SUM skips it rather than
            // treating it as zero. COALESCE only turns "no rows at all" into 0.
            $select[] = ($d[4] === 'money')
                ? "COALESCE(SUM(CASE WHEN {$date_expr} >= %s THEN funding_amount_usd END), 0) AS c_{$di}_{$pi}"
                : "SUM(({$d[3]}) AND {$date_expr} >= %s) AS c_{$di}_{$pi}";
            $select_params[] = $p[1];
        }
    }
    /*
      THE QUERY BUDGET RULE SURVIVES THE DATED PANEL IT WAS WRITTEN FOR. The
      four time buckets are four SETS OF COLUMNS in this one statement, never
      four round trips; the N+1 tripwire in tests/php/render_dashboard.php
      re-checks TIT_DASH_QUERY_BUDGET after five thousand more rows land, so a
      future session that gives one bucket its own query fails there rather
      than on the live site under a crawl. The dated strip this scan also fed
      was replaced by the signal board on 2026-08-02 (the owner's shared
      design): its per-bucket employer/verified/largest-raise columns went
      with it, and the board reads the matrix cells alone.
    */
    // SELECT placeholders precede WHERE placeholders in statement order, so
    // the select params go first.
    $sql = 'SELECT ' . implode(', ', $select) . " FROM {$table} WHERE {$where}";
    $row = $wpdb->get_row(
        $wpdb->prepare($sql, array_merge($select_params, $params)),
        ARRAY_A
    ) ?: array();

    $rows = array();
    foreach ($defs as $di => $d) {
        $cells = array();
        foreach ($periods as $pi => $p) {
            $cells[] = (int) ($row["c_{$di}_{$pi}"] ?? 0);
        }
        $rows[] = array('key' => $d[0], 'label' => $d[1], 'filter' => $d[2],
                        'kind' => $d[4], 'cells' => $cells);
    }

    return array(
        'periods' => array_column($periods, 0),
        'starts'  => array_column($periods, 1),
        'rows'    => $rows,
        /*
          The week's own dates ride along for the column header. Early in a
          month the week column can legitimately exceed the month column (the
          week reaches back into the previous month), and without the dates
          that CORRECT pair of figures reads as a bug -- the owner asked for
          this on the strip (2026-08-02) and the board keeps it. Derived from
          the same boundary the SQL counted under, formatted server-side so
          every paint says the same thing.
        */
        'week_range' => date_i18n('M j', strtotime($periods[0][1] . ' 00:00:00 UTC'))
                      . '-' . date_i18n('M j', strtotime($today . ' 00:00:00 UTC')),
    );
}

/*
 * ---------------------------------------------------------------------------
 * THE TREND CHART
 * ---------------------------------------------------------------------------
 *
 * Every other figure on this page is a snapshot. The matrix answers "how many
 * this week, this month, this quarter" and a reader who wants to know whether
 * hiring is picking up has to do arithmetic across four columns and guess at
 * the shape in between. This draws the shape.
 *
 * WHY A TRAILING AVERAGE AND NOT THE DAILY COUNT. Measured against
 * data/talent_intel.db on 2026-07-30, 17,539 current rows: the last 90 days
 * hold data on 72 of them and the missing 18 are weekends and public holidays.
 * Filings are a working-week activity, so a raw daily line is a comb, and every
 * reader's eye would be drawn to the teeth rather than the direction. Seven days
 * is the smallest window that contains exactly one of each weekday, so it
 * removes the week's own shape without removing anything else.
 *
 * WHY THE WINDOW IS 90 DAYS. It is the horizon the matrix already names ("this
 * quarter"), so the chart leads the table in the table's own vocabulary. It is
 * also as far back as this corpus can be trusted day by day: further out the
 * rows come from backfill slices that were run over particular date ranges
 * rather than from continuous collection, and the boundary between a slice that
 * was run and one that was not looks exactly like a market that moved.
 *
 * WHICH IS THE HAZARD THE GATE BELOW EXISTS FOR, and it is not hypothetical.
 * Distinct rows per signal in the 90 days to 2026-07-30, with the longest run of
 * consecutive days holding nothing:
 *
 *   Leadership Moves   1,771 rows · 70 of 90 days ·  3-day longest gap
 *   Everything         3,157 rows · 72 of 90 days ·  3-day longest gap
 *   Funding Rounds     1,123 rows · 51 of 90 days · 14-day longest gap
 *   Pay and Benefits      49 rows · 27 of 90 days · 10-day longest gap
 *   Adding Roles         119 rows · 11 of 90 days · 76-day longest gap
 *
 * Adding Roles reads 223 in January, zero from February to June, and 119 in
 * July. That is not a hiring market that stopped and restarted, it is a news
 * backfill that covered January and a live collector that started on 17 July.
 * Funding's 14-day hole is the same thing in miniature: Form D is collected
 * through June and July has not been walked yet. Drawn, either line would read
 * as a collapse and a recovery, and it would be the most quotable thing on the
 * page.
 *
 * So THE GATE IS ON CONTINUITY, NOT ON VOLUME: a signal is drawn only when no
 * averaging window inside the chart is empty, which is to say its longest run of
 * silent days is shorter than the average itself. A zero on a drawn line then
 * means a quiet week. A zero on a refused line would have meant a collector that
 * was not running, and those two are indistinguishable once the line is drawn.
 *
 * This is the same rule the week-over-week comparison in tit_dated_glance_html()
 * already applies to itself, generalised per signal: print the comparison only
 * where the corpus can carry it, and say plainly where it cannot. Both turn
 * themselves on with no code change and no deploy on the day the coverage
 * arrives, and both name what is missing rather than leaving a gap a reader
 * cannot tell from a flat line.
 */

/** Days plotted. A quarter, which is the horizon the matrix already names. */
const TIT_TREND_DAYS = 90;

/** The trailing average, in days. One of each weekday, and no more. */
const TIT_TREND_AVG = 7;

/**
 * The volume floor, which excludes NOTHING today and is said so out loud.
 *
 * Continuity is what refuses every refused line above. This is the guard for the
 * other shape: a signal that lands one row most days and nothing else would pass
 * the continuity gate and draw a line hovering under 1, where a single extra row
 * doubles it. Thirty rows over ninety days is a third of a row a day, which is
 * the least that can average to anything a reader should read a direction off.
 */
const TIT_TREND_MIN_ROWS = 30;

/**
 * One colour per signal, so a colour means the same thing wherever it appears.
 * Taken from the palette already in dashboard.css rather than a new one.
 *
 * THESE VALUES ARE NO LONGER WRITTEN INTO THE MARKUP, and that is deliberate.
 * They used to be emitted as inline `stroke=` and `fill=` attributes on the
 * trend plot, which pins one scheme's hue into the HTML -- and this server
 * cannot know which scheme the reader is in, because that choice lives in the
 * browser. The plot is also swapped wholesale by dashboard.js on every filter
 * change, from this same server, so the wrong hue would have arrived twice.
 * Each series carries a CLASS now (tit_trend_series_class below) and the value
 * lives in dashboard.css as a token, light and dark. The function stays
 * because it is the one written record of which hue belongs to which signal,
 * and the CSS light values are these.
 */
function tit_trend_colours() {
    return array(
        'hiring'     => '#1f7a4d',
        'funded'     => '#a8560f',
        'leadership' => '#7a3fa8',
        'pay'        => '#c2417e',
        'total'      => '#1c5cab',
    );
}

/**
 * The stylesheet class that paints one series, for one kind of mark.
 *
 * $part is 's' for a stroked line, 'd' for an endpoint dot, 'w' for a legend
 * swatch. The key is checked against the palette rather than interpolated, so
 * a signal that is ever renamed falls back to the total hue instead of writing
 * a class that matches no rule and drawing an invisible line.
 */
function tit_trend_series_class($key, $part) {
    $known = array_keys(tit_trend_colours());
    if (!in_array($key, $known, true)) $key = 'total';
    return 'tit-tc-' . $part . '-' . $key;
}

/**
 * The daily rollup and its trailing average, in ONE query.
 *
 * One GROUP BY over a 96-day slice: the 90 plotted plus the 6 the first point's
 * average needs behind it. Every signal is a conditional SUM in the same scan,
 * for the reason tit_glance_matrix() gives about its own — five round trips
 * would be five chances for the lines to describe different sets of rows.
 *
 * $where/$params are the caller's, so the chart under a filtered page is that
 * filter's chart and not the world's.
 */
function tit_signal_trend($table, $where = 'is_current = 1', array $params = array()) {
    global $wpdb;

    $today = current_time('Y-m-d');
    $start = date('Y-m-d', strtotime($today . ' -' . (TIT_TREND_DAYS - 1) . ' days'));
    $warm  = date('Y-m-d', strtotime($start . ' -' . (TIT_TREND_AVG - 1) . ' days'));

    $defs = array();
    foreach (tit_signal_defs() as $d) {
        if ($d[4] === 'count') $defs[] = $d;   // the money row is a sum, not a series
    }

    $date_expr = tit_signal_date_expr();
    $select = array("{$date_expr} AS d");
    foreach ($defs as $i => $d) {
        $select[] = "SUM({$d[3]}) AS s{$i}";
    }
    /*
      THIS SCAN NO LONGER CARRIES THE COLLECTOR NAMES, and the removal is the
      point rather than a tidy-up. It used to append

        GROUP_CONCAT(DISTINCT collector) AS cols

      here, and the panel counted those names at each end of the window to tell a
      reader how much of the movement was us reading more. The flaw is in the
      GROUP BY: every row in this query is bucketed by tit_signal_date_expr(),
      which is COALESCE(published_date, DATE(captured_at)), so a collector that
      arrived last week and ingested a year of back-dated articles was counted as
      having fed the START of the window. That is exactly the confound the
      measurement existed to detect, and it was the one shape of it the
      measurement could not see.

      It is measured by INGEST date instead, in tit_trend_ingest_breadth() below,
      which is a separate scan because it needs a different GROUP BY and cannot
      ride along on this one.
    */
    $sql = 'SELECT ' . implode(', ', $select) . " FROM {$table} WHERE {$where}"
         . " AND {$date_expr} >= %s AND {$date_expr} <= %s GROUP BY d";
    $raw = $wpdb->get_results(
        $wpdb->prepare($sql, array_merge($params, array($warm, $today))), ARRAY_A) ?: array();

    $by_day = array();
    foreach ($raw as $r) $by_day[(string) $r['d']] = $r;

    // Every day in the span, present or not. A day the table has no row for is a
    // zero and not a hole: the average has to divide by seven days either way.
    $span = array();
    $cursor = $warm;
    while ($cursor <= $today) {
        $span[] = $cursor;
        $cursor = date('Y-m-d', strtotime($cursor . ' +1 day'));
    }
    $plot_from = count($span) - TIT_TREND_DAYS;
    if ($plot_from < 0) $plot_from = 0;

    $colours = tit_trend_colours();
    $series = array();
    $refused = array();
    $max = 0.0;

    foreach ($defs as $i => $d) {
        $daily = array();
        foreach ($span as $day) {
            $daily[] = (int) ($by_day[$day]["s{$i}"] ?? 0);
        }

        // The longest run of consecutive silent days, and the rows inside the
        // plotted window. The gap is measured across the WHOLE span, warm-up
        // included, because the first plotted point averages over those days too.
        $gap = 0; $run = 0;
        foreach ($daily as $n) {
            if ($n > 0) { $run = 0; continue; }
            $run++;
            if ($run > $gap) $gap = $run;
        }
        $rows_in_window = array_sum(array_slice($daily, $plot_from));

        if ($gap >= TIT_TREND_AVG) {
            $refused[] = array(
                'label' => $d[1],
                'gap'   => $gap,
                'why'   => sprintf(
                    'we hold nothing at all across %d days in a row inside this window, '
                    . 'so a line would show a gap in our collection rather than the market',
                    $gap),
            );
            continue;
        }
        if ($rows_in_window < TIT_TREND_MIN_ROWS) {
            $refused[] = array(
                'label' => $d[1],
                'gap'   => $gap,
                'why'   => sprintf(
                    'we hold %s update%s across the whole window, too few to average',
                    number_format_i18n($rows_in_window), $rows_in_window == 1 ? '' : 's'),
            );
            continue;
        }

        $avg = array();
        for ($p = $plot_from; $p < count($daily); $p++) {
            $sum = 0;
            for ($k = $p - (TIT_TREND_AVG - 1); $k <= $p; $k++) {
                $sum += $daily[$k] ?? 0;
            }
            $value = $sum / TIT_TREND_AVG;
            $avg[] = $value;
            if ($value > $max) $max = $value;
        }

        $series[] = array(
            'key'    => $d[0],
            'label'  => $d[1],
            'filter' => $d[2],
            'colour' => $colours[$d[0]] ?? '#1c5cab',
            'avg'    => $avg,
            'rows'   => $rows_in_window,
            'gap'    => $gap,
            'first'  => $avg ? $avg[0] : 0,
            'last'   => $avg ? $avg[count($avg) - 1] : 0,
        );
    }

    /*
      THE TWO ENDS OF THE WINDOW, AS CALENDAR DATES, for the ingest measurement.

      Both are seven days wide, matching the trailing average, so "the first week
      of the window" in the prose is the same seven days the first plotted point
      averages over.
    */
    $end_i = count($span);
    $first_lo = $span[$plot_from] ?? $start;
    $first_hi = $span[min($plot_from + TIT_TREND_AVG - 1, $end_i - 1)] ?? $first_lo;
    $last_lo  = $span[max(0, $end_i - TIT_TREND_AVG)] ?? $start;
    $last_hi  = $span[$end_i - 1] ?? $today;

    $sets = tit_trend_ingest_breadth($table, $where, $params, array(
        array($first_lo, $first_hi),
        array($last_lo,  $last_hi),
    ));
    $set_first = $sets[0] ?? array();
    $set_last  = $sets[1] ?? array();

    return array(
        'start'   => $span[$plot_from] ?? $start,
        'end'     => $today,
        'days'    => count($span) - $plot_from,
        'avg'     => TIT_TREND_AVG,
        'series'  => $series,
        'refused' => $refused,
        'max'     => $max,
        'sources_first' => count($set_first),
        'sources_last'  => count($set_last),
        /*
          THE SETS, COMPARED, and not their two counts. Three collectors at each
          end is not the same three: one can stop and another start in the same
          window and leave the count untouched, which is a change in what we read
          reported as no change at all. Both lists come back sorted, so === is a
          set comparison.
        */
        'sources_same'  => $set_first === $set_last,
    );
}

/**
 * Which collectors were STORING rows for this view at each end of the window.
 *
 * Bucketed by captured_at, which is when we wrote the row down, and never by
 * published_date, which is when the world published it. That distinction is the
 * whole reason this function exists rather than riding along on the trend's own
 * scan, and it is worth stating plainly: a collector switched on last week that
 * then ingests a year of older articles publishes nothing and stores a great
 * deal. Bucketed by publication it looks like a source that has been feeding us
 * all along, and every count it adds to the left-hand end of the chart looks
 * like something that was already there. Bucketed by ingest it appears exactly
 * where it actually arrived.
 *
 * It answers one narrow question, and the prose that prints it is held to the
 * same narrowness: how many distinct collectors put rows into THIS view during
 * those seven days. It cannot see a collector that stayed and doubled its yield,
 * so nothing built on it may claim that the rest of a movement is the market.
 *
 * $ranges is a list of array(lo, hi) inclusive date strings. Returns a list in
 * the same order, each a sorted list of collector names.
 */
function tit_trend_ingest_breadth($table, $where, array $params, array $ranges) {
    global $wpdb;
    if (!$ranges) return array();

    // One scan for both ends. Two queries would be two chances for the halves of
    // one sentence to describe different sets of rows, which is the reason the
    // trend's own signals are counted in a single pass as well.
    $clauses = array();
    $args = $params;             // The caller's filter params come FIRST: they
    foreach ($ranges as $r) {    // appear first in statement order, inside $where.
        $clauses[] = 'DATE(captured_at) BETWEEN %s AND %s';
        $args[] = (string) $r[0];
        $args[] = (string) $r[1];
    }
    // GROUP_CONCAT(DISTINCT) is standard in both MySQL and SQLite and the value
    // is a dozen short names, far inside either engine's length cap.
    $sql = "SELECT DATE(captured_at) AS d, GROUP_CONCAT(DISTINCT collector) AS cols
              FROM {$table}
             WHERE {$where} AND (" . implode(' OR ', $clauses) . ')
             GROUP BY d';
    $rows = $wpdb->get_results($wpdb->prepare($sql, $args), ARRAY_A) ?: array();

    $out = array();
    foreach ($ranges as $i => $r) $out[$i] = array();
    foreach ($rows as $row) {
        $d = (string) ($row['d'] ?? '');
        foreach ($ranges as $i => $r) {
            // A day can fall in more than one range only if the caller overlaps
            // them, and then it belongs to both. No early break.
            if ($d === '' || $d < (string) $r[0] || $d > (string) $r[1]) continue;
            foreach (explode(',', (string) ($row['cols'] ?? '')) as $name) {
                $name = trim($name);
                if ($name !== '') $out[$i][$name] = true;
            }
        }
    }
    // Sorted, so the caller can compare two of these with === and be comparing
    // sets rather than insertion order.
    foreach ($out as $i => $set) {
        ksort($set);
        $out[$i] = array_keys($set);
    }
    return $out;
}

/** "1 collector" and "5 collectors". A live view printed the first as the second. */
function tit_trend_collectors($n) {
    $n = (int) $n;
    return number_format_i18n($n) . ' ' . ($n === 1 ? 'collector' : 'collectors');
}

/** A trailing average reads as a rate, so it keeps a decimal while it is small. */
function tit_trend_rate($value) {
    return $value >= 10
        ? number_format_i18n(round($value))
        : number_format_i18n(round($value, 1), 1);
}

/**
 * The chart, as inline SVG.
 *
 * No library and no script, the same decision the recall page's chart and the
 * job-board sparkline both made: a trend that only appears once a chart library
 * has loaded is a trend nobody can rely on seeing, and this page had a
 * render-blocking stylesheet taken off its sibling last week.
 *
 * The y axis starts at zero, always. A truncated axis turns a rise from 18 a day
 * to 20 into a cliff, and this page's whole argument is that its numbers can be
 * checked.
 */
function tit_signal_trend_html(array $trend, $interactive = true) {
    $series  = $trend['series'] ?? array();
    $refused = $trend['refused'] ?? array();
    if (!$series && !$refused) return '';
    $avg = (int) ($trend['avg'] ?? TIT_TREND_AVG);

    /*
      NOTHING DRAWN IS A STATE THIS PANEL HAS TO HANDLE WELL, not an edge case.

      A narrow filter reaches it easily: pick a country outside the two we hold
      most of and no signal in that view has continuous enough coverage to
      average. Printed as the full panel it would be a heading and a sentence
      promising lines, followed by five near-identical apologies. So it collapses
      to one sentence carrying the number that decides it, and the panel says
      what would have to change for a line to appear.
    */
    $note_id = tit_chart_note_id('trend');

    if (!$series) {
        $worst = 0;
        foreach ($refused as $r) $worst = max($worst, (int) ($r['gap'] ?? 0));
        ob_start(); ?>
        <div class="tit-chart-note" id="<?php echo esc_attr($note_id); ?>">
          <p class="tit-sub">Not drawn for this view yet.<?php if ($worst >= $avg) : ?>
            The longest run of days holding nothing here is <?php echo (int) $worst; ?>,
            longer than the <?php echo $avg; ?> days the average covers. Every line would
            pass through a stretch that shows a gap in our collection rather than the market.
            <?php else : ?>
            We hold too few updates across the window to average them.
            <?php endif; ?>
            The counts are in the updates themselves, where a count of what we hold is
            exactly what is claimed.</p>
        </div>
        <p class="tit-trend-none">Not drawn for this view yet.</p>
        <?php
        return ob_get_clean();
    }

    $s_first = (int) ($trend['sources_first'] ?? 0);
    $s_last  = (int) ($trend['sources_last'] ?? 0);
    $s_same  = !empty($trend['sources_same']);

    $then = array();
    foreach ($series as $s) {
        $then[] = $s['label'] . ' ' . tit_trend_rate($s['first']);
    }

    ob_start(); ?>
    <div class="tit-chart-note" id="<?php echo esc_attr($note_id); ?>">
      <p class="tit-sub">Each line is how many updates of that kind we recorded
        per day, smoothed over the
        <?php echo $avg; ?> days ending on the day it is plotted, from
        <?php echo esc_html((string) ($trend['start'] ?? '')); ?>
        to <?php echo esc_html((string) ($trend['end'] ?? '')); ?>. It is a
        collection rate, and it moves when the market moves and when we start
        reading somewhere new.</p>
      <?php /* The click contract: every chart element that looks tappable
               filters the page, and this was the one chart that broke it.
               The plot is pointer-only (an SVG point is not focusable), so
               the sentence names the keyboard route, the same trade the
               sibling's canvas charts document. $interactive is false on the
               sources page, where there are no filters and no dashboard.js,
               so promising a tap there would be a control that does nothing. */ ?>
      <?php if ($interactive) : ?>
      <p class="tit-sub">Tap the plot to narrow the page to the
        <?php echo $avg; ?> days ending on that day; tap it again to clear.
        The Date Range control under Filters is the keyboard route to the
        same window.</p>
      <?php endif; ?>
      <?php
      /*
        THE COMPARISON THE LEGEND USED TO CARRY. Each key read "Leadership
        Moves: 12 a day now, 9 at the start of the window", which is two
        numbers and eleven words per signal inside a card that is now a third
        of the page wide. The legend keeps the figure that answers "what is it
        doing now"; the one it started at is here, once, for every line.
      */
      ?>
      <p class="tit-trend-then">A day at the start of the window:
        <?php echo esc_html(implode(', ', $then)); ?>.</p>
      <?php
      /*
        THE SENTENCE THAT STOPS THE CHART OVERCLAIMING, and it says less than it
        used to because the old version said more than it could show.

        A line here counts updates WE HOLD, so it moves when the market moves and
        it moves when we start reading another source, and no chart can tell a
        reader which. Naming which collectors were storing rows at each end of
        the window lets them tell part of it. It is measured by INGEST date now
        (tit_trend_ingest_breadth), which is the fix: bucketed by publication, a
        collector switched on last week that back-fills older articles was
        counted as having fed the START of the window, so the one confound worth
        detecting was the one the check was blind to.

        WHAT WAS REMOVED, AND WHY IT CANNOT COME BACK. The equal-basis branch
        used to end "so the movement here is not a change in how many sources we
        read", which reads as a certificate that the rise is real. Nothing here
        can certify that. The same collectors can double what they return, one
        can be swapped for another without moving a count, and a query can widen
        inside a collector that never changes its name. So the branch now reports
        the measurement and states what it does not cover, and any session
        tempted to shorten it back into a verdict should read this paragraph
        first.

        THE FIRST BRANCH IS NEW. A window whose opening week has no ingest at
        all is a window whose left-hand end is entirely backfill. That is a
        plain fact about the corpus and the old check could not report it,
        because a backfilled row carries a publication date inside the window
        and so looked like a row we had held all along.

        IT IS IN THE (i) AND IT IS NOT OPTIONAL. The panel ships open, the card
        points at it with aria-describedby and the (i) is a keyboard control.
      */
      ?>
      <?php if ($s_last > 0 && $s_first === 0) : ?>
        <p class="tit-trend-basis">No collector stored anything for this view during the
          first week of this window; those updates were written down later. The left hand
          end of these lines is backfill rather than what we were reading at the time.</p>
      <?php elseif ($s_first > 0 && $s_last > 0 && !$s_same && $s_first === $s_last) : ?>
        <?php
        /*
          EQUAL COUNTS, DIFFERENT SETS, and it gets its own sentence because the
          shared one would print "3 in the first week and 3 in the last" and
          invite the reader to conclude that nothing moved. This is the case the
          old check could not report at all: it compared two counts, so a
          collector stopping and another starting inside the window cancelled out
          and the panel said the basis had not changed.

          Two sets of equal size that are not equal cannot be a subset of one
          another, so at least one left and at least one arrived. That is
          arithmetic, not an estimate.
        */
        ?>
        <p class="tit-trend-basis"><?php
          echo esc_html(tit_trend_collectors($s_first)); ?> stored updates for this view in the
          first week of the window and the same number in the last, but not the same ones. At
          least one stopped feeding this view, and at least one started. Some of the movement
          here is a change in what we read rather than in the market.</p>
      <?php elseif ($s_first > 0 && $s_last > 0 && !$s_same) : ?>
        <?php
        /*
          NEUTRAL ABOUT DIRECTION, deliberately. The first draft of this said
          "part of any rise here is us reading more", which was written while
          looking at a view that was rising. Filter the page to Great Britain
          and the same sentence sat under two falling lines. What the
          measurement supports is that the basis moved, not which way the
          lines went, so that is all it says.
        */
        ?>
        <p class="tit-trend-basis"><?php
          echo esc_html(tit_trend_collectors($s_first)); ?> stored updates for this view in the
          first week of the window and <?php echo (int) $s_last; ?> in the last, counted by when
          we wrote each update down. Some of the movement here is a change in what we read
          rather than in the market.</p>
      <?php elseif ($s_first > 0 && $s_last > 0) : ?>
        <p class="tit-trend-basis">The same <?php
          echo esc_html(tit_trend_collectors($s_last)); ?> stored updates for this view in the
          first and the last week of this window, counted by when we wrote each update down.
          That is a count of sources, not a measure of how much each one returns. On its own
          it does not make this movement a change in the market.</p>
      <?php endif; ?>
      <?php if ($refused) : ?>
        <p class="tit-trend-refused"><?php
          // Named, and with the reason, because a signal silently missing from a
          // chart of signals reads as a signal with nothing happening in it.
          echo esc_html(count($refused) === 1 ? 'One line is not drawn.' : 'Some lines are not drawn.');
          ?>
          <?php foreach ($refused as $r) : ?>
            <span class="tit-trend-nodraw"><b><?php echo esc_html($r['label']); ?></b>:
              <?php echo esc_html($r['why']); ?>.</span>
          <?php endforeach; ?>
          They stay in the updates themselves, where a count of what we hold is exactly what is claimed.</p>
      <?php endif; ?>
    </div>
    <?php echo tit_trend_svg($trend); // phpcs:ignore — built and escaped in that function ?>
    <p class="tit-trend-legend">
      <?php foreach ($series as $s) : ?>
        <span class="tit-trend-key"><span class="tit-trend-swatch <?php
          echo esc_attr(tit_trend_series_class($s['key'], 'w')); ?>"></span><?php
          echo esc_html($s['label']); ?> <b><?php echo esc_html(tit_trend_rate($s['last'])); ?></b></span>
      <?php endforeach; ?>
    </p>
    <?php
    return ob_get_clean();
}

/**
 * The plot itself, and it is drawn to survive a card a third of the page wide.
 *
 * IT USED TO BE 720 UNITS WIDE WITH A 520px MIN-WIDTH AND ITS LABELS INSIDE IT.
 * That was right for a full-width panel: the SVG scrolled sideways inside its
 * own container on a phone and its 12px axis text stayed 12px. Inside a card
 * one of nine, the same markup is a permanent horizontal scrollbar on a desktop
 * as well, and simply letting it shrink to fit would render that 12px text at
 * about five. Neither is a chart anybody can read.
 *
 * SO THE TEXT CAME OUT OF THE DRAWING. The five axis values and the two dates
 * are HTML beside the SVG, positioned against the plot rather than drawn into
 * it, so they are set in CSS pixels and are the same size in the small card, in
 * the expanded card and on a phone. What is left inside the SVG is geometry
 * only, which scales cleanly to any width:
 *
 *   - the grid and the lines carry vector-effect="non-scaling-stroke", so a
 *     2px line is 2px whether the box is 300 or 760 wide. Without it the
 *     expanded card gets fat lines and the phone gets hairlines.
 *   - the endpoint dot is the one thing that SHOULD grow with the box, and it
 *     is in user units for exactly that reason.
 *   - the grid is five evenly spaced lines, which is what lets the HTML labels
 *     be placed at 0, 25, 50, 75 and 100 percent and land on them.
 *
 * The y axis still starts at zero, always. A truncated axis turns a rise from
 * 18 a day to 20 into a cliff, and this page's whole argument is that its
 * numbers can be checked.
 */
function tit_trend_svg(array $trend) {
    $series = $trend['series'] ?? array();
    if (!$series) return '';
    $n = count($series[0]['avg']);
    if ($n < 2) return '';

    // No padding at all: the labels are outside now, so the drawing is the plot
    // and every coordinate is the value's own position in it.
    $w = 300; $h = 116; $pad_l = 0; $pad_r = 0; $pad_t = 2; $pad_b = 2;
    $plot_w = $w - $pad_l - $pad_r;
    $plot_h = $h - $pad_t - $pad_b;

    /*
      Zero-based, and topped out at four steps of a round number.

      The step is chosen from 1, 2, 2.5 or 5 times a power of ten rather than
      computed, because an axis is read and 0/10/20/30/40 is read instantly
      while 0/9/18/27/36 is not. It has to work at both ends of this data: a
      busy view averages tens of updates a day and a narrow one averages under
      one, and the same rule gives 0..40 for the first and 0..2 in halves for
      the second.
    */
    $max = max(0.001, (float) ($trend['max'] ?? 1));
    $q = $max / 4;
    $mag = pow(10, floor(log10($q)));
    foreach (array(1, 2, 2.5, 5, 10) as $mult) {
        if ($q <= $mag * $mult + 1e-9) { $q = $mag * $mult; break; }
    }
    $max = 4 * $q;

    /*
      COORDINATES ARE WHOLE UNITS, and that is a byte decision with a measured
      cost. Ninety points times one path per drawn signal is most of what this
      chart weighs, and a decimal place on each is about a fifth of it. The
      viewBox is 300 wide and the small card renders it at roughly that, so one
      unit is about one pixel and the rounding error is under half of that, on a
      line the browser draws 2px thick whatever the box does. It is not visible
      and the bytes are.
    */
    $x = function ($i) use ($pad_l, $plot_w, $n) {
        return (int) round($pad_l + ($plot_w * $i / ($n - 1)));
    };
    $y = function ($v) use ($pad_t, $plot_h, $max) {
        return (int) round($pad_t + $plot_h - ($plot_h * min($v, $max) / $max));
    };

    $described = '';
    foreach ($series as $s) {
        $described .= sprintf('%s: %s a day on %s, %s a day on %s. ',
            $s['label'], tit_trend_rate($s['first']), $trend['start'],
            tit_trend_rate($s['last']), $trend['end']);
    }

    // The axis is formatted once for the whole scale, not per label. Running
    // each through the rate formatter gave "0.0 9.0 18 27 36", four labels in
    // two different shapes on one axis.
    $whole = (fmod($max, 4) == 0.0) && ($max / 4 >= 1);

    ob_start(); ?>
    <?php /* The four data attributes are the tap-to-filter contract with
             dashboard.js: the plot spans data-n daily points from data-start
             to data-end inclusive, smoothed over data-avg days, so a click's
             x-fraction maps to a date without a second copy of the series
             ever leaving the server. They ride every repaint because
             /aggregate rebuilds this same markup. */ ?>
    <div class="tit-tc"
         data-start="<?php echo esc_attr((string) ($trend['start'] ?? '')); ?>"
         data-end="<?php echo esc_attr((string) ($trend['end'] ?? '')); ?>"
         data-n="<?php echo (int) $n; ?>"
         data-avg="<?php echo (int) ($trend['avg'] ?? TIT_TREND_AVG); ?>">
      <?php /* Top value first, zero last: the column reads down the axis it
               labels. Placed in flow rather than at percentages so the column
               sizes itself to its widest value and pins no width at any
               viewport; the negative margin in the stylesheet is half a line,
               which is what centres the first and last labels on the lines they
               name. aria-hidden because the SVG's own label already states both
               ends of every series in words. */ ?>
      <div class="tit-tc-ys" aria-hidden="true">
        <?php for ($g = 4; $g >= 0; $g--) :
          $value = $max * $g / 4; ?>
          <span><?php echo esc_html($whole ? number_format_i18n($value)
                                           : number_format_i18n(round($value, 1), 1)); ?></span>
        <?php endfor; ?>
      </div>
      <div class="tit-tc-box">
        <svg class="tit-trend-chart" viewBox="0 0 <?php echo $w; ?> <?php echo $h; ?>"
             role="img" preserveAspectRatio="none"
             aria-label="<?php echo esc_attr('Updates a day, averaged over '
                 . (int) $trend['avg'] . ' days. ' . $described); ?>">
          <?php for ($g = 0; $g <= 4; $g++) :
            $gy = $y($max * $g / 4); ?>
            <line x1="0" x2="<?php echo $w; ?>"
                  y1="<?php echo $gy; ?>" y2="<?php echo $gy; ?>"
                  class="tit-tc-grid" vector-effect="non-scaling-stroke"/>
          <?php endfor; ?>

          <?php foreach ($series as $s) :
            $parts = array();
            foreach ($s['avg'] as $i => $v) $parts[] = ($i ? 'L' : 'M') . $x($i) . ' ' . $y($v); ?>
            <path d="<?php echo esc_attr(implode(' ', $parts)); ?>" fill="none"
                  class="tit-tc-line <?php echo esc_attr(tit_trend_series_class($s['key'], 's')); ?>"
                  vector-effect="non-scaling-stroke"/>
          <?php endforeach; ?>
        </svg>
        <?php
        /*
          THE ENDPOINT DOTS ARE THEIR OWN SVG, laid over the plot, and that is
          not fussiness. The plot is stretched to whatever box the card gives it
          (preserveAspectRatio none, a height in the stylesheet) so the ninety
          points always fill the width and the chart is never letterboxed. A
          circle inside a stretched box is an ellipse.

          This one carries NO viewBox, so its user unit IS a CSS pixel: the
          radius is 3.5px in the small card, in the expanded card and on a
          phone, exactly like the line weights beside it, and the position is a
          percentage of the same box the plot fills, so the dot sits on the end
          of its own line at every width. overflow is visible because the last
          point is at 100% and half of the dot is outside it.
        */
        ?>
        <svg class="tit-tc-dots" aria-hidden="true" focusable="false">
          <?php foreach ($series as $s) : ?>
            <circle cx="100%" cy="<?php echo esc_attr(round(100 * $y($s['avg'][$n - 1]) / $h, 1)); ?>%"
                    r="3.5" class="<?php echo esc_attr(tit_trend_series_class($s['key'], 'd')); ?>"/>
          <?php endforeach; ?>
        </svg>
      </div>
      <?php // Only the ends are dated. A label per point is a smear. ?>
      <p class="tit-tc-xs" aria-hidden="true"><span><?php
        echo esc_html((string) $trend['start']); ?></span><span><?php
        echo esc_html((string) $trend['end']); ?></span></p>
    </div>
    <?php
    return ob_get_clean();
}

/*
 * THE MARKET TREND: the dashboard's one claim about the market over time.
 *
 * It replaced "Updates Collected a Day", which plotted our own collection rate
 * and now lives on the sources page where an operations measure belongs. This
 * chart makes a MARKET claim, so it is built to not launder collection growth
 * as market movement, which is the exact confound the old chart's basis
 * sentence once falsely certified away (docs/TECHLOG.md, 2026-08-03 and
 * 2026-08-04).
 *
 * Same-store-sales logic. The honest version of "the market did X this quarter"
 * from a growing collector fleet is the panel: count only the collectors that
 * were storing rows for the ENTIRE window, so a source switched on mid-window
 * cannot appear as a rise. Liveness is read from first-seen and last-seen
 * ingest dates (DATE(captured_at)), never from publication dates, for the
 * reason tit_trend_ingest_breadth() documents: a collector that arrives late
 * and backfills old articles looks, by publication date, like it was always
 * there.
 *
 * When the panel is too thin for an honest count trend, the chart falls back
 * to COMPOSITION: each week's share of updates by stated headcount direction,
 * which survives changes in how much we collect because a share has no volume
 * axis. And when even that has too few weeks behind it, nothing is drawn and
 * the card says so. Raw all-collector counts are never drawn as a market
 * claim in any state.
 */
/** Weeks in the market window. Twelve seven-day weeks, Monday to Sunday. */
const TIT_MARKET_WEEKS = 12;
/** Fewer panel sources than this and a count trend is not honest. */
const TIT_MARKET_MIN_PANEL = 5;
/** Fewer weeks holding any updates than this and no trend is drawn at all. */
const TIT_MARKET_MIN_WEEKS = 4;

/**
 * The weekly market series, two queries.
 *
 * One finds the panel: every collector's first and last ingest day. One counts
 * the weeks, grouped by event date and split by stated headcount direction,
 * restricted to the panel when the panel is wide enough to count from.
 *
 * $where is the caller's clause (the dashboard hands its notable-default
 * clause), so the chart counts the same set of rows every other card does.
 */
function tit_market_trend($table, $where = 'is_current = 1', array $params = array()) {
    global $wpdb;

    // Whole weeks only, Monday to Sunday, and the running week is excluded: a
    // partial week drawn as a short bar reads as a fall that has not happened.
    $today = current_time('Y-m-d');
    $dow = (int) date('N', strtotime($today));
    $monday = date('Y-m-d', strtotime($today . ' -' . ($dow - 1) . ' days'));
    $end = date('Y-m-d', strtotime($monday . ' -1 day'));
    $start = date('Y-m-d', strtotime($end . ' -' . (TIT_MARKET_WEEKS * 7 - 1) . ' days'));

    /*
      The panel. Liveness is bucketed by ingest date over EVERY current row,
      not under the caller's clause: whether a collector was running is a fact
      about the collector, and a view filter that happens to exclude its rows
      must not make it look dead. "Live for the entire window" means it had
      stored something on or before the window opened and was still storing in
      the window's final week.
    */
    $lives = $wpdb->get_results(
        "SELECT collector,
                MIN(DATE(captured_at)) AS first_seen,
                MAX(DATE(captured_at)) AS last_seen
           FROM {$table} WHERE is_current = 1 GROUP BY collector", ARRAY_A) ?: array();
    $final_week = date('Y-m-d', strtotime($end . ' -6 days'));
    $panel = array();
    foreach ($lives as $row) {
        $name = trim((string) ($row['collector'] ?? ''));
        if ($name === '') continue;
        if ((string) $row['first_seen'] <= $start && (string) $row['last_seen'] >= $final_week) {
            $panel[] = $name;
        }
    }
    sort($panel);

    $variant = count($panel) >= TIT_MARKET_MIN_PANEL ? 'counts' : 'share';

    /*
      The weekly split. Grouped by event date (published, with ingest date
      standing in), because a market trend is about when things happened. In
      the counts variant the panel restriction is what keeps that honest; the
      share variant reads all collectors, because a share of a week's updates
      carries no volume claim for a new collector to inflate.
    */
    $date_expr = tit_signal_date_expr();
    $sql = "SELECT {$date_expr} AS d,
                   SUM(signal_direction = 'hiring') AS g,
                   SUM(signal_direction = 'displacement') AS s,
                   SUM(signal_direction NOT IN ('hiring', 'displacement')) AS u
              FROM {$table} WHERE {$where}
               AND {$date_expr} >= %s AND {$date_expr} <= %s";
    $args = array_merge($params, array($start, $end));
    if ($variant === 'counts') {
        $sql .= ' AND collector IN (' . implode(', ', array_fill(0, count($panel), '%s')) . ')';
        $args = array_merge($args, $panel);
    }
    $sql .= ' GROUP BY d';
    $rows = $wpdb->get_results($wpdb->prepare($sql, $args), ARRAY_A) ?: array();

    $weeks = array();
    for ($i = 0; $i < TIT_MARKET_WEEKS; $i++) {
        $lo = date('Y-m-d', strtotime($start . ' +' . ($i * 7) . ' days'));
        $weeks[$i] = array(
            'lo' => $lo,
            'hi' => date('Y-m-d', strtotime($lo . ' +6 days')),
            'g' => 0, 's' => 0, 'u' => 0, 'n' => 0,
        );
    }
    $start_ts = strtotime($start);
    foreach ($rows as $r) {
        $d = strtotime((string) $r['d']);
        if ($d === false) continue;
        $i = intdiv((int) floor(($d - $start_ts) / DAY_IN_SECONDS), 7);
        if ($i < 0 || $i >= TIT_MARKET_WEEKS) continue;
        $weeks[$i]['g'] += (int) $r['g'];
        $weeks[$i]['s'] += (int) $r['s'];
        $weeks[$i]['u'] += (int) $r['u'];
        $weeks[$i]['n'] += (int) $r['g'] + (int) $r['s'] + (int) $r['u'];
    }

    $with_data = 0;
    foreach ($weeks as $w) {
        if ($w['n'] > 0) $with_data++;
    }
    if ($with_data < TIT_MARKET_MIN_WEEKS) $variant = 'none';

    return array(
        'start'       => $start,
        'end'         => $end,
        'weeks'       => $weeks,
        'weeks_total' => TIT_MARKET_WEEKS,
        'with_data'   => $with_data,
        'panel'       => $panel,
        'panel_size'  => count($panel),
        'variant'     => $variant,
    );
}

/**
 * The sentence on the card that says what the chart is allowed to claim.
 *
 * Printed as visible prose (.tit-chart-caveat) and NEVER as note_html: the (i)
 * panels are closed by dashboard.js on load, and a basis statement nobody has
 * seen is not a basis statement. The place caveat learned this on 2026-08-03.
 */
function tit_market_caveat(array $m) {
    $fixed = ' Counted across the whole tracker, and the filters on this page do not narrow this card.';
    if ($m['variant'] === 'counts') {
        return sprintf(
            'Counted only from the %d sources that were live for all %d weeks of this window '
            . '(%s to %s). A source switched on mid window cannot appear as a market move.',
            (int) $m['panel_size'], (int) $m['weeks_total'], $m['start'], $m['end']) . $fixed;
    }
    $none = (int) $m['panel_size'] === 0;
    $lead = $none
        ? sprintf('No source has yet been live for all %d weeks of this window (%s to %s)',
                  (int) $m['weeks_total'], $m['start'], $m['end'])
        : sprintf('Only %d source%s been live for all %d weeks of this window (%s to %s)',
                  (int) $m['panel_size'], (int) $m['panel_size'] === 1 ? ' has' : 's have',
                  (int) $m['weeks_total'], $m['start'], $m['end']);
    if ($m['variant'] === 'share') {
        return $lead . ', too few for an honest count trend, so this chart shows each '
             . 'week as SHARES of its own updates instead. A share survives changes in '
             . 'how much we collect. A count from a growing set of sources would not.'
             . $fixed;
    }
    return $lead . sprintf(
        ', and only %d of the %d weeks hold any updates at all. So no trend is drawn: '
        . 'a line through that would show the shape of our collection, not the market.',
        (int) $m['with_data'], (int) $m['weeks_total']);
}

/**
 * The stacked weekly bars, as inline SVG built in PHP.
 *
 * Same decisions as every other chart here: no library, no script, y axis
 * from zero, and the drawing arrives in the initial HTML. Wrapped in the
 * shared scroll container so a narrow phone scrolls the chart inside its own
 * box rather than the page sideways.
 */
function tit_market_trend_html(array $m) {
    $weeks = $m['weeks'] ?? array();
    if (($m['variant'] ?? 'none') === 'none' || !$weeks) {
        return '<p class="tit-trend-none">Not drawn yet: too few weeks hold updates '
             . 'inside this window for a trend to mean anything.</p>';
    }
    $share = $m['variant'] === 'share';
    $n = count($weeks);

    $max = 1;
    if (!$share) {
        foreach ($weeks as $w) $max = max($max, (int) $w['n']);
        // A rounded axis: the same 1 / 2 / 2.5 / 5 rule the collection chart
        // uses, because 0/10/20/30/40 is read instantly and 0/9/18/27/36 is not.
        $q = $max / 4;
        $mag = pow(10, floor(log10(max(0.001, $q))));
        foreach (array(1, 2, 2.5, 5, 10) as $mult) {
            if ($q <= $mag * $mult + 1e-9) { $q = $mag * $mult; break; }
        }
        $max = 4 * $q;
    }

    $w_px = 720; $h_px = 230; $pad_l = 46; $pad_r = 10; $pad_t = 12; $pad_b = 30;
    $plot_w = $w_px - $pad_l - $pad_r;
    $plot_h = $h_px - $pad_t - $pad_b;
    $slot = $plot_w / $n;
    $bar = max(8, (int) round($slot * 0.62));

    $labels = array('g' => 'Adding Roles', 's' => 'Cutting Roles', 'u' => 'Headcount Not Stated');
    $described = '';
    foreach ($weeks as $w) {
        if ($w['n'] === 0) {
            $described .= sprintf('Week of %s: no updates. ', $w['lo']);
            continue;
        }
        $described .= sprintf('Week of %s: %d adding roles, %d cutting roles, %d not stated. ',
            $w['lo'], $w['g'], $w['s'], $w['u']);
    }

    ob_start(); ?>
    <div class="tit-table-scroll tit-chart-scroll">
    <svg class="tit-market-chart" viewBox="0 0 <?php echo $w_px; ?> <?php echo $h_px; ?>"
         role="img" preserveAspectRatio="xMidYMid meet"
         aria-label="<?php echo esc_attr(($share
             ? 'Each week of updates split into shares by stated headcount direction. '
             : 'Updates a week from the fixed source panel, split by stated headcount direction. ')
             . $described); ?>">
      <?php for ($g = 0; $g <= 4; $g++) :
        $value = $share ? 25 * $g : $max * $g / 4;
        $gy = round($pad_t + $plot_h - ($plot_h * $g / 4), 1); ?>
        <line x1="<?php echo $pad_l; ?>" x2="<?php echo $w_px - $pad_r; ?>"
              y1="<?php echo $gy; ?>" y2="<?php echo $gy; ?>" class="tit-mk-grid"/>
        <text x="<?php echo $pad_l - 7; ?>" y="<?php echo $gy + 4; ?>"
              class="tit-mk-axis" text-anchor="end"><?php
          echo esc_html($share ? $value . '%' : number_format_i18n($value)); ?></text>
      <?php endfor; ?>

      <?php foreach ($weeks as $i => $wk) :
        $x = (int) round($pad_l + $slot * $i + ($slot - $bar) / 2);
        if ($wk['n'] === 0) {
            // An empty week is an explicit gap, never a zero-height bar that
            // reads as a market with nothing in it.
            continue;
        }
        $scale = $share ? ($plot_h / $wk['n']) : ($plot_h / $max);
        $y_cursor = $pad_t + $plot_h;
        foreach (array('u', 's', 'g') as $key) :
            $seg = (int) $wk[$key];
            if ($seg === 0) continue;
            $seg_h = $seg * $scale;
            $y_cursor -= $seg_h; ?>
            <rect x="<?php echo $x; ?>" y="<?php echo round($y_cursor, 1); ?>"
                  width="<?php echo $bar; ?>" height="<?php echo round($seg_h, 1); ?>"
                  class="tit-mk-<?php echo $key; ?>">
              <title><?php echo esc_html(sprintf('Week of %s: %s, %s update%s',
                  $wk['lo'], $labels[$key], number_format_i18n($seg),
                  $seg === 1 ? '' : 's')); ?></title>
            </rect>
        <?php endforeach; ?>
      <?php endforeach; ?>

      <?php // Only the end weeks are dated. Twelve dated columns is a smear. ?>
      <text x="<?php echo $pad_l; ?>" y="<?php echo $h_px - 8; ?>"
            class="tit-mk-axis" text-anchor="start"><?php
        echo esc_html($weeks[0]['lo']); ?></text>
      <text x="<?php echo $w_px - $pad_r; ?>" y="<?php echo $h_px - 8; ?>"
            class="tit-mk-axis" text-anchor="end"><?php
        echo esc_html($weeks[$n - 1]['hi']); ?></text>
    </svg>
    </div>
    <p class="tit-market-legend">
      <span class="tit-mk-key"><span class="tit-mk-swatch tit-mk-g"></span>Adding Roles</span>
      <span class="tit-mk-key"><span class="tit-mk-swatch tit-mk-s"></span>Cutting Roles</span>
      <span class="tit-mk-key"><span class="tit-mk-swatch tit-mk-u"></span>Headcount Not Stated</span>
    </p>
    <?php
    return ob_get_clean();
}

/**
 * The board's date, in the header of the signal board. Derived from the same
 * clock the matrix buckets use (current_time), formatted at UTC midnight so a
 * reader in Auckland is not shown a heading a day ahead of the cells under it.
 */
function tit_board_date_label() {
    return date_i18n('M j', strtotime(current_time('Y-m-d') . ' 00:00:00 UTC'));
}

/**
 * The freshness panel's four figures: updates, employers, dollars raised,
 * official filings. dashboard.js (freshStatsHtml) mirrors this markup and
 * repaints it under the active filters, the same contract renderRow has with
 * the table, so the panel never describes a set the page is not showing.
 *
 * The dollar figure keeps the old fine-print honesty: it is a link into the
 * money section, and its title carries the exact figure plus the coverage
 * sentence, so the sum never travels without its caveat.
 */
function tit_fresh_stats_html($total, $companies, array $money, $verified, $ytd = null) {
    /*
      TWO FIGURES PER STAT WHEN THE VIEW IS NOT DATE-NARROWED. The whole-record
      totals read as this year's numbers with only the coverage ribbon saying
      otherwise; the owner misread them himself. So each stat leads with the
      current year (big) and keeps the entire record under it (small). The year
      in the labels is derived from the clock upstream, never typed. When the
      reader has set their own date window, $ytd is null and the single figure
      returns, because "all time" under a date filter would be a false label.
    */
    $pair = is_array($ytd) && !empty($ytd['year']);
    $year = $pair ? (string) (int) $ytd['year'] : '';
    $all = function ($text) {
        return '<span class="tit-fstat-all">' . $text . ' all time</span>';
    };
    if ($pair) {
        $n = (int) $ytd['total'];
        $out = '<span class="tit-fstat"><b>' . esc_html(number_format_i18n($n)) . '</b>'
             . '<span>' . esc_html(($n == 1 ? 'update' : 'updates') . ' in ' . $year) . '</span>'
             . $all(esc_html(number_format_i18n($total))) . '</span>';
        $c = (int) $ytd['companies'];
        $out .= '<span class="tit-fstat"><b>' . esc_html(number_format_i18n($c)) . '</b>'
              . '<span>' . esc_html(($c == 1 ? 'employer' : 'employers') . ' in ' . $year) . '</span>'
              . $all(esc_html(number_format_i18n($companies))) . '</span>';
    } else {
        $out  = '<span class="tit-fstat"><b>' . esc_html(number_format_i18n($total)) . '</b>'
              . '<span>' . esc_html($total == 1 ? 'update' : 'updates') . '</span></span>';
        $out .= '<span class="tit-fstat"><b>' . esc_html(number_format_i18n($companies)) . '</b>'
              . '<span>' . esc_html($companies == 1 ? 'employer' : 'employers') . '</span></span>';
    }
    if (($money['total'] ?? 0) > 0) {
        // The dollar pair only pairs when this year actually holds a stated
        // sum: "$0 raised in 2026" over a real all-time figure would read as a
        // broken parser rather than a thin January.
        $money_pair = $pair && ($ytd['money'] ?? 0) > 0;
        $lead = $money_pair ? $ytd['money'] : $money['total'];
        $title = $money_pair
            ? tit_money_full($ytd['money']) . ' in ' . $year . '; '
              . tit_money_full($money['total']) . ' all time. '
              . tit_money_coverage_sentence($money['coverage'] ?? null)
            : tit_money_full($money['total']) . '. '
              . tit_money_coverage_sentence($money['coverage'] ?? null);
        $out .= '<span class="tit-fstat tit-fstat-money"><a class="tit-fine-money" '
              . 'href="#chart-money-country" title="' . esc_attr($title) . '"><b>'
              . esc_html(tit_money_short($lead)) . '</b>'
              . '<span>' . esc_html($money_pair ? 'raised in ' . $year : 'raised') . '</span></a>'
              . ($money_pair ? $all(esc_html(tit_money_short($money['total']))) : '')
              . '</span>';
    }
    if ($pair) {
        $v = (int) $ytd['verified'];
        $out .= '<span class="tit-fstat"><b>' . esc_html(number_format_i18n($v)) . '</b>'
              . '<span>' . esc_html('official filings in ' . $year) . '</span>'
              . $all(esc_html(number_format_i18n($verified))) . '</span>';
    } else {
        $out .= '<span class="tit-fstat"><b>' . esc_html(number_format_i18n($verified)) . '</b>'
              . '<span>from official filings</span></span>';
    }
    return $out;
}

/**
 * Render the matrix. dashboard.js mirrors this markup when it repaints from
 * /aggregate, the same contract renderRow() has with the table: the classes
 * and attributes here must match what the JS emits.
 *
 * Cells are buttons: a click applies the row's filter plus the column's
 * period, a second click clears them (wired in dashboard.js). Zero renders as
 * a muted 0, never blank — a real zero is information.
 */
function tit_glance_matrix_html(array $m) {
    ob_start(); ?>
    <div class="tit-matrix-scroll">
      <table class="tit-matrix">
        <thead>
          <tr>
            <th scope="col"><span class="tit-sr">Measure</span></th>
            <?php foreach ($m['periods'] as $pi => $p) : ?>
              <th scope="col"><?php echo esc_html($p);
                // The week names its own dates; see 'week_range' above.
                if ($pi === 0 && ($m['week_range'] ?? '') !== '') : ?><span
                  class="tit-th-range"><?php echo esc_html($m['week_range']); ?></span><?php
                endif; ?></th>
            <?php endforeach; ?>
          </tr>
        </thead>
        <tbody>
          <?php foreach ($m['rows'] as $r) :
            // Each signal keeps one hue across the whole product, and the cell
            // tint is scaled to the biggest number in ITS OWN row. Rows differ
            // by orders of magnitude (leadership moves ran 1,726 against 235
            // pay changes), so a table-wide scale would wash every row but one
            // out to nothing. Colour is a second read here, never the only
            // one: the number is always printed.
            $row_max = 0;
            foreach ($r['cells'] as $n) { $row_max = max($row_max, (int) $n); }
            $money = (($r['kind'] ?? 'count') === 'money');
            ?>
            <tr class="tit-matrix-row<?php echo $r['key'] === 'total' ? ' tit-matrix-total' : ''; ?><?php
                echo $money ? ' tit-matrix-money' : ''; ?>"
                data-signal="<?php echo esc_attr($r['key']); ?>">
              <th scope="row"><?php echo esc_html($r['label']); ?><?php
                if ($money) : ?><span class="tit-matrix-unit">sum of dollars</span><?php endif;
              ?></th>
              <?php foreach ($r['cells'] as $i => $n) :
                $n = (int) $n;
                // Square-rooted so a single dominant period does not flatten
                // every smaller one to invisible, and floored at 0.14 so any
                // real activity is still tinted.
                $intensity = ($row_max > 0 && $n > 0)
                    ? max(0.14, round(sqrt($n / $row_max), 3)) : 0;
                // Money reads as money in every part of the cell: the visible
                // figure is abbreviated with a $ in front, the title carries
                // the exact number, and the screen-reader label spells out the
                // unit rather than leaving "1.2" to be read as a count.
                $text  = $money ? tit_money_short($n) : number_format_i18n($n);
                $full  = $money ? tit_money_full($n)  : number_format_i18n($n);
                $spoken = $money
                    ? sprintf('%s, %s, %s in US dollars', $r['label'], $m['periods'][$i], $full)
                    : sprintf('%s, %s', $r['label'], $m['periods'][$i]);
                ?>
                <td><button type="button"
                    class="tit-cell<?php echo $n === 0 ? ' tit-cell-zero' : ''; ?><?php
                      echo $money ? ' tit-cell-money' : ''; ?>"
                    style="--i:<?php echo esc_attr($intensity); ?>"
                    data-filter="<?php echo esc_attr($r['filter']); ?>"
                    data-since="<?php echo esc_attr($m['starts'][$i]); ?>"
                    <?php if ($money) : ?>title="<?php echo esc_attr($full); ?>"<?php endif; ?>
                    aria-pressed="false"
                    aria-label="<?php echo esc_attr($spoken); ?>"><?php
                    /* THE PERIOD, AS REAL TEXT, in every cell.
                       Below 860px the table is laid out as one card per row and
                       `display:block` drops the implicit table roles, so the
                       column header a reader was getting from the grid stops
                       being announced. A CSS ::after on a data attribute would
                       not fix that: generated content is not reliably in the
                       accessibility tree, is not selectable and is not
                       findable. So the label is markup, printed here and
                       mirrored in matrixHtml(), and hidden by the stylesheet on
                       desktop where the real <th> is doing the job. */
                    ?><span class="tit-cell-p"><?php
                      echo esc_html($m['periods'][$i]); ?></span><?php
                    echo esc_html($text);
                ?></button></td>
              <?php endforeach; ?>
            </tr>
          <?php endforeach; ?>
        </tbody>
      </table>
    </div>
    <?php
    /*
      ONE FOOTNOTE LINE, per the owner's shared design (2026-08-02). The old
      two-line lede plus a five-bullet disclosure carried three facts a reader
      acts on and four they do not; the three survive in one sentence and the
      USD caveat is a POINTER to its one home (#tit-usd-note) rather than a
      repeat of it. The heat legend is real text in the board head above.
      dashboard.js (matrixHtml) mirrors this line on every repaint.
    */
    ?>
    <p class="tit-board-note">Each cell counts updates dated inside its window;
      tap a number to filter the page. Rows overlap, so columns do not add up,
      and Total Raised sums <a href="#tit-usd-note">USD-stated dollars only</a>.</p>
    <?php
    return ob_get_clean();
}

/* =========================================================================
   Money.

   funding_amount has always been a display string ("$1.45 Million"), which
   nothing could add up or sort. funding_amount_usd is the numeric companion:
   plain US dollars, and NULL rather than a converted guess when the source
   stated a figure in another currency. Everything below reads only the numeric
   column, and everything below says how much of the data it is based on.
   ========================================================================= */

/**
 * A dollar figure at a glance: $1.2B, $450M, $3.6M, $920K, $540.
 *
 * One decimal below 100 and none above, so $3.6M keeps the precision that
 * matters at that size while $450M does not gain a false one. A figure is
 * NEVER rounded into a different order of magnitude: $999M stays $999M, and
 * the promotion step exists only so a figure that genuinely rounds to a
 * thousand of its unit prints as $1B rather than $1,000M.
 *
 * The exact number always travels with it, in a title attribute (see
 * tit_money_full), because an abbreviation is a reading aid and not the fact.
 */
function tit_money_short($n) {
    $n = (float) $n;
    if ($n <= 0) return '$0';
    if ($n < 1000) return '$' . number_format_i18n(round($n));

    $units = array('K', 'M', 'B', 'T');
    $i = 0;
    $v = $n / 1000;
    while ($v >= 1000 && $i < count($units) - 1) { $v /= 1000; $i++; }
    $v = ($v < 100) ? round($v, 1) : round($v);
    if ($v >= 1000 && $i < count($units) - 1) { $v = round($v / 1000, 1); $i++; }

    // Trailing ".0" is noise: $27M, never $27.0M.
    $s = rtrim(rtrim(number_format((float) $v, 1, '.', ''), '0'), '.');
    return '$' . $s . $units[$i];
}

/** The exact figure, for the title attribute and for screen readers. */
function tit_money_full($n) {
    return '$' . number_format_i18n(round((float) $n));
}

/**
 * Whether a stated amount names a currency at all.
 *
 * WHY THIS EXISTS, and it is a live defect rather than a hypothetical.
 * `funding_amount` is the source's own words. The company profile and the
 * place pages printed it verbatim, in bold, followed by the word "raised".
 * One row published that way on 2026-08-02:
 *
 *     OpenAI  -  "93.175 millones"  -  diarioestrategia.cl
 *
 * In Spanish that dot is a THOUSANDS separator, so the source wrote ninety
 * three thousand one hundred and seventy five million. An English reader sees
 * "93.175" and reads ninety three point one seven five. The page was showing
 * one number and asserting another, in bold, with "raised" after it.
 *
 * The parser was right to refuse it: `pipeline/vocab._USD_MARKER` requires a
 * dollar to be STATED, and that string names no currency, so
 * funding_amount_usd is correctly NULL and the row is correctly absent from
 * every total. That veto is load-bearing and is not touched here. What was
 * wrong was the rendering: a string the parser declined to read was being
 * displayed as though it had been read.
 *
 * So this asks the one question the display actually depends on, which is
 * strictly weaker than the parser's: does the string name a currency AT ALL?
 *
 *   yes  "$5 billion", "US$10.5 billion", "EUR 48 million", "R$ 50 milhoes",
 *        "GBP 585K", "250 crore" with a rupee sign, "300 Millionen Euro".
 *        A reader can see what unit it is even when we do not convert it,
 *        which is the promise the page already makes about other currencies.
 *   no   "93.175 millones", "5.300 millones", "52,5 millones". A bare number
 *        and a scale word. There is nothing here for a reader to be right
 *        about, so nothing is printed.
 *
 * Measured over the 251 live rows carrying an amount we did not parse: the
 * overwhelming majority name a symbol or a currency word and keep printing
 * exactly as before. The ones this silences are the currency-less ones, which
 * is the class the OpenAI row belongs to.
 *
 * Deliberately NOT a second copy of the parser. It answers a different, easier
 * question, it is used only for display, and its failure direction is to print
 * nothing rather than to print something wrong.
 */
function tit_amount_names_a_currency($text) {
    $text = (string) $text;
    if ($text === '') return false;

    // 1. Any currency SYMBOL, including the CJK and Hangul currency
    // characters, which are ordinary letters to a word-boundary and so can
    // never be matched by the word list below. The dollar sign is included:
    // an ambiguous dollar is the ambiguity this product already accepts
    // everywhere else, and a reader seeing "$5 billion" is not being misled
    // about the magnitude.
    if (preg_match('/[$\x{20A0}-\x{20CF}\x{00A3}\x{00A5}\x{00A2}\x{FDFC}]'
                   . '|z\x{0142}|\x{5143}|\x{5186}|\x{5713}|\x{C6D0}/u', $text)) {
        return true;
    }

    // 2. An ISO code or a code-like prefix. Matched with a LOOKAHEAD and not a
    // trailing \b, because these are written glued to the number: "EUR10
    // milioni", "Rp2,35 Triliun", "RM540mil", "Rs2-3 billion", "Tk200cr".
    // `\b...\b` fails on every one of them, and that is the exact shape of the
    // bug pipeline/vocab.py records under "a regex alternative ending in a
    // magnitude word can silently never match" - the same trap, one layer up.
    if (preg_match(
        '/\b(?:usd|eur|gbp|jpy|chf|cny|rmb|inr|krw|brl|cad|aud|sek|nok|dkk|'
        . 'pln|try|zar|mxn|sgd|hkd|twd|ils|aed|sar|qar|rub|thb|idr|myr|vnd|'
        . 'ngn|kes|pkr|bdt|egp|clp|cop|ars|uah|czk|huf|ron|isk|'
        . 'rs|rp|rm|tk|ksh|shs|nt)(?![a-z])/iu', $text)) {
        return true;
    }
    // The naira's bare capital N, which only counts immediately before a
    // digit: "N1bn". Case-sensitive on purpose, so an ordinary lowercase word
    // cannot become a currency.
    if (preg_match('/\bN\s?\d/u', $text)) return true;

    // 3. Or a currency NAME, in the languages the catalogue wires. Same
    // principle as vocab.py's dollar words: a currency written out is stated
    // just as plainly as one written with a sign. Note `meuro?` for the
    // "300 MEuro" contraction, and that "300 Millionen" is deliberately NOT
    // here - a scale word alone names no currency, which is the whole point.
    return (bool) preg_match(
        '/\b(dollars?|dolares|dolar|d\x{00F3}lar(?:es)?|dollar[oi]|'
        . 'm?euros?|eura|eury|evro|libras?|livres?|pounds?|sterlin[g]?|'
        . 'yen|yuan|renminbi|rupees?|rupiah|rupia|crore|lakh|won|reais|real|'
        . 'pesos?|francs?|franken|krona|kronor|krone|kroner|kroon|'
        . 'zloty|z\x{0142}oty|forint|lir[ae]|liras|shekel|dirhams?|riyals?|'
        . 'rand|ringgit|baht|dong|naira|shillings?|couronne|corona)\b'
        // The same names in the scripts that write them, where \b is
        // meaningless: Arabic, Hebrew, CJK and Korean.
        . '|\x{0631}\x{064A}\x{0627}\x{0644}|\x{062F}\x{0648}\x{0644}\x{0627}\x{0631}'
        . '|\x{062F}\x{0631}\x{0647}\x{0645}|\x{064A}\x{0648}\x{0631}\x{0648}'
        . '|\x{05E9}\x{05E7}\x{05DC}|\x{FF04}|\x{B2EC}\x{B7EC}|\x{30C9}\x{30EB}/iu',
        $text);
}

/**
 * The "N raised" fragment for one row, or '' when there is nothing honest to
 * print. Returns escaped HTML, ready to echo.
 *
 * ONE definition for the company profile and the place pages, because the two
 * carried the same line twice and would have been fixed once. Three cases:
 *
 *   parsed        print OUR figure, not the source's string. It is the number
 *                 the totals add up, so a profile and a chart can never
 *                 disagree, and it is unambiguous in any reader's locale.
 *   not parsed,
 *   currency named  print the source's words unchanged. "48 million euros" is
 *                 a fact a reader can use, and the page's standing promise is
 *                 that other currencies are shown and not converted.
 *   not parsed,
 *   no currency   print NOTHING. See tit_amount_names_a_currency.
 */
function tit_amount_raised_html($row) {
    $usd = isset($row['funding_amount_usd']) ? (float) $row['funding_amount_usd'] : 0.0;
    if ($usd > 0) {
        return '<strong title="' . esc_attr(tit_money_full($usd)) . '">'
            . esc_html(tit_money_short($usd)) . '</strong> raised';
    }
    $raw = isset($row['funding_amount']) ? (string) $row['funding_amount'] : '';
    if ($raw !== '' && tit_amount_names_a_currency($raw)) {
        return '<strong>' . esc_html($raw) . '</strong> raised';
    }
    return '';
}

/**
 * Summed US dollars under the caller's filters, plus the coverage figures the
 * page is required to print beside them.
 *
 * Coverage is the whole point. Only some funding updates carry a US dollar
 * figure: a round announced in euros is deliberately NULL rather than
 * converted at a rate nobody stated, and plenty of funding news names no
 * amount at all. A total presented as if it covered every round would be the
 * plausible-but-wrong number this product cannot carry, so N and M are
 * computed here, never hardcoded, and travel with every total.
 *
 * One query for the headline figures, three for the rankings. Place uses the
 * same COALESCE(location, headquarters) rule as the rest of the dashboard, so
 * the money charts and the activity charts put an employer in the same place.
 */
function tit_money_aggregate($table, $where = 'is_current = 1', array $params = array(), $limit = 40, $pillar = '') {
    global $wpdb;
    $limit = max(1, min(200, (int) $limit));

    $funding = function_exists('tit_funding_where')
        ? tit_funding_where()
        : "((funding_amount IS NOT NULL AND funding_amount <> '')"
          . " OR (funding_stage IS NOT NULL AND funding_stage <> ''))";

    // One authority for both, so a money ranking and the filter a click on it
    // writes can never select different rows. See tit_city_expr().
    $country_expr = function_exists('tit_country_expr') ? tit_country_expr() : 'COALESCE(country, hq_country)';
    $city_expr    = function_exists('tit_city_expr') ? tit_city_expr() : 'COALESCE(city, hq_city)';

    // "Placed" counts say how many of the summable rows each chart can
    // actually show, so a card can admit what it is leaving out instead of
    // quietly dropping rows off the bottom of a ranking.
    $head_sql = "SELECT COALESCE(SUM(funding_amount_usd), 0) AS total,
                        SUM(funding_amount_usd IS NOT NULL) AS with_usd,
                        SUM({$funding}) AS funding_rows,
                        SUM(funding_amount_usd IS NOT NULL
                            AND {$country_expr} IS NOT NULL AND {$country_expr} <> '') AS placed_country,
                        SUM(funding_amount_usd IS NOT NULL
                            AND {$city_expr} IS NOT NULL AND {$city_expr} <> '') AS placed_city,
                        SUM(funding_amount_usd IS NOT NULL
                            AND industry IS NOT NULL AND industry <> '') AS placed_industry
                   FROM {$table} WHERE {$where}";
    $head = $wpdb->get_row($params ? $wpdb->prepare($head_sql, $params) : $head_sql, ARRAY_A) ?: array();

    $with = (int) ($head['with_usd'] ?? 0);
    // A row can carry a dollar figure without matching the funding test if the
    // pipeline ever fills one column and not the other. The denominator must
    // never be smaller than the numerator, or the sentence reads "53 of 40".
    $all  = max($with, (int) ($head['funding_rows'] ?? 0));

    $by = function ($expr) use ($wpdb, $table, $where, $params, $limit) {
        $sql = "SELECT {$expr} AS k, SUM(funding_amount_usd) AS v, COUNT(*) AS n
                  FROM {$table}
                 WHERE {$where} AND funding_amount_usd IS NOT NULL
                   AND {$expr} IS NOT NULL AND {$expr} <> ''
                 GROUP BY k ORDER BY v DESC LIMIT {$limit}";
        $rows = $wpdb->get_results($params ? $wpdb->prepare($sql, $params) : $sql, ARRAY_A) ?: array();
        return array_map(function ($r) {
            return array('k' => $r['k'], 'v' => (float) $r['v'], 'n' => (int) $r['n']);
        }, $rows);
    };

    return array(
        'total'    => (float) ($head['total'] ?? 0),
        'coverage' => array('with' => $with, 'all' => $all),
        'placed'   => array(
            'country'  => (int) ($head['placed_country'] ?? 0),
            'city'     => (int) ($head['placed_city'] ?? 0),
            'industry' => (int) ($head['placed_industry'] ?? 0),
        ),
        // What an EMPTY money chart is allowed to say about itself. Costs a
        // query only when there is an empty chart to explain; see
        // tit_money_pillar_reach().
        'empty'    => ($with > 0)
            ? array('pillar' => '', 'pillar_placed' => null)
            : tit_money_pillar_reach($table, $pillar, $country_expr, $city_expr),
        'by_country'  => $by($country_expr),
        'by_city'     => $by($city_expr),
        'by_industry' => $by('industry'),
    );
}

/**
 * How far the SELECTED Looking For pillar could ever fill a money chart, with
 * every other filter taken off.
 *
 * WHY A SECOND QUERY EXISTS AT ALL. An empty money chart has more than one
 * cause and they need different sentences. "Switch your filters" is the wrong
 * answer when the honest answer is that this kind of update never carries a
 * funding amount, and it is also the wrong answer when we hold the money and
 * not the place. Only a measurement can tell those apart, so this takes one.
 *
 * Scoped to `is_current = 1 AND pillar = ?` and NOTHING else on purpose. Every
 * other control on the page narrows within that set, so a zero here means the
 * chart cannot fill under this pillar whatever else the reader picks, which is
 * exactly the claim the copy makes. A wider probe could not support it and a
 * narrower one would not be worth the sentence.
 *
 * It runs ONLY when the current view holds no dollar amount at all, which is
 * the only state whose explanation needs it. A page whose money charts are
 * drawing pays nothing for this.
 */
function tit_money_pillar_reach($table, $pillar, $country_expr, $city_expr) {
    global $wpdb;
    $none = array('pillar' => '', 'pillar_placed' => null);

    $pillar = (string) $pillar;
    if ($pillar === '') return $none;
    if (function_exists('tit_allowed_pillars')
        && !in_array($pillar, tit_allowed_pillars(), true)) return $none;

    // The reader-facing name, and it is the CONTROL's own word for it, taken
    // from the Looking For options a click actually sets. A sentence that named
    // the pillar something the dropdown does not say would send the reader
    // looking for a filter that is not there.
    $label = '';
    $looking = function_exists('tit_looking_options') ? tit_looking_options() : array();
    if (isset($looking['pillar=' . $pillar])) {
        $label = $looking['pillar=' . $pillar];
    } elseif (function_exists('tit_company_pillar_labels')) {
        $labels = tit_company_pillar_labels();
        $label = $labels[$pillar] ?? '';
    }
    if ($label === '') return $none;

    $sql = "SELECT SUM(funding_amount_usd IS NOT NULL
                       AND {$country_expr} IS NOT NULL AND {$country_expr} <> '') AS country,
                   SUM(funding_amount_usd IS NOT NULL
                       AND {$city_expr} IS NOT NULL AND {$city_expr} <> '') AS city,
                   SUM(funding_amount_usd IS NOT NULL
                       AND industry IS NOT NULL AND industry <> '') AS industry
              FROM {$table} WHERE is_current = 1 AND pillar = %s";
    $row = $wpdb->get_row($wpdb->prepare($sql, array($pillar)), ARRAY_A) ?: array();

    return array(
        'pillar' => $label,
        'pillar_placed' => array(
            'country'  => (int) ($row['country'] ?? 0),
            'city'     => (int) ($row['city'] ?? 0),
            'industry' => (int) ($row['industry'] ?? 0),
        ),
    );
}

/**
 * WHY THIS MONEY CHART HAS NOTHING ON IT, in the reader's terms.
 *
 * It used to say "No US dollar amounts in this view yet." for all of it. That
 * sentence is true and it is useless: it reads as data we are missing, and the
 * owner hit the case where it is not. He set Looking For to Pay and Benefits
 * and Where to United States, and all three money charts said it. Pay and
 * benefits updates do not carry funding amounts, so no money chart can ever
 * fill under that pillar; the data was fine and the filters disagreed.
 *
 * Three causes, three sentences, because telling a reader to change a filter
 * when the real answer is "we hold the money but not the place" would be a
 * confidently wrong explanation, which is worse than a vague one.
 *
 *   unplaced  the view HAS dollar amounts and this dimension cannot place any
 *             of them. A coverage gap in what the sources said, and no filter
 *             fixes it. Says how many, computed.
 *   pillar    the view has no amount at all, and the selected pillar could not
 *             fill this chart with every other filter removed. So the pillar
 *             is the cause, and it is named with the word the control uses.
 *   filters   the view has no amount and the pillar is not the reason, so it
 *             is this combination of filters. Points at the widest ones.
 *
 * No figure here is typed. The only number that appears is the view's own
 * count of dollar-stated updates, straight off the same query the totals use.
 * moneyEmptyNote() in dashboard.js mirrors this word for word.
 */
function tit_money_empty_note(array $money, $dimension = '') {
    $names = array('country' => 'a country', 'city' => 'a city', 'industry' => 'an industry');
    $name  = $names[$dimension] ?? 'a place';

    $with = (int) ($money['coverage']['with'] ?? 0);
    if ($with > 0) {
        return sprintf(
            _n('This view holds %1$s update with a US dollar amount, and it does not name %2$s.',
               'This view holds %1$s updates with a US dollar amount, and not one of them names %2$s.',
               $with, 'tit'),
            number_format_i18n($with), $name
        ) . ' That is missing detail in the sources, not a filter you can widen.';
    }

    $label  = (string) ($money['empty']['pillar'] ?? '');
    $placed = $money['empty']['pillar_placed'] ?? null;
    if ($label !== '' && is_array($placed) && (int) ($placed[$dimension] ?? 1) === 0) {
        return 'No ' . $label . ' update we hold pairs a US dollar amount with '
             . $name . ', so this chart stays empty under that setting.'
             . ' Try Looking For: Raised Money.';
    }

    return 'No update in this view states a US dollar amount.'
         . ' Try a wider country or date range.';
}

/**
 * "Totals cover the 53 of 56 funding updates that state an amount in US
 * dollars." Computed, never written down, and printed beside every money view.
 */
function tit_money_coverage_sentence($coverage) {
    if (!is_array($coverage)) return '';
    $with = (int) ($coverage['with'] ?? 0);
    $all  = (int) ($coverage['all'] ?? 0);
    if ($all === 0) {
        return 'No funding updates in this view yet, so there is nothing to add up.';
    }
    // "the 3,992 of 3,992" reads as a mistake. When coverage is complete, say
    // so plainly; keep the two numbers only when they actually differ, which is
    // the case the sentence exists for.
    $lead = ($with >= $all)
        ? sprintf(
            _n('All %s funding update states a US dollar amount',
               'All %s funding updates state a US dollar amount', $all, 'tit'),
            number_format_i18n($all))
        : sprintf(
            _n('Totals cover the %1$s of %2$s funding update that states a US dollar amount',
               'Totals cover the %1$s of %2$s funding updates that state a US dollar amount',
               $all, 'tit'),
            number_format_i18n($with), number_format_i18n($all));

    return $lead . '. We leave out amounts in other currencies rather than'
         . ' convert them at a rate nobody published.';
}

/**
 * The per-card note: the short pointer plus what THIS chart cannot place.
 *
 * The full currency sentence used to be repeated here, identically, on all
 * three money cards; it lives once now, in the About The Money Figures note
 * over the cards (#tit-usd-note). What stays per card is the one fact that
 * differs per card: how many summable updates this dimension cannot place.
 */
function tit_money_coverage_note(array $money, $dimension = '') {
    $note = 'USD-stated amounts only. The About The Money Figures note above'
          . ' says what share of funding updates that covers.';
    if (!is_array($money['coverage'] ?? null) || (int) ($money['coverage']['all'] ?? 0) === 0) {
        $note = tit_money_coverage_sentence($money['coverage'] ?? null);
    }
    $with = (int) ($money['coverage']['with'] ?? 0);
    $placed = (int) ($money['placed'][$dimension] ?? $with);
    $missing = $with - $placed;
    if ($dimension === '' || $missing <= 0) return $note;

    $names = array('country' => 'no country', 'city' => 'no city', 'industry' => 'no industry');
    return $note . ' ' . sprintf(
        _n('%1$s of those names %2$s, so it is not on this chart.',
           '%1$s of those name %2$s, so they are not on this chart.',
           $missing, 'tit'),
        number_format_i18n($missing), $names[$dimension] ?? 'no category'
    );
}

/**
 * What the detail control is showing and what it is setting aside, in numbers.
 *
 * Both counts, always, and what "routine" means, always. The sentence is
 * computed from the current view, so it stays true under every filter, and it
 * is the reason the default is allowed to hold rows back at all: a reader can
 * see exactly how many and decide for themselves in one click.
 *
 * dashboard.js mirrors this wording when a filter changes.
 */
function tit_detail_note($mode, $notable, $routine) {
    $notable = (int) $notable;
    $routine = (int) $routine;
    $total   = $notable + $routine;

    /*
      Definition FIRST, then the numbers, and all three of them.

      The old sentence used the word "routine" twice before defining it, and
      quoted two figures out of three, so a reader had to do arithmetic to find
      out what they were looking at. Now the filter explains itself in one
      sentence and prints hidden, shown and total together, which means the
      reader can check that they add up instead of taking our word for it.
    */
    /*
      COUNT FIRST, IN PLAIN WORDS, AND ONE SENTENCE.

      The owner's words: "Don't understand this or how it's placed". What they
      were looking at was three stacked headings -- "Officer and director
      filings", then "Hide the routine ones" as the select's own option text,
      then a sentence opening "Some SEC filings record only an officer or
      director change, with no headcount, no money and no location" -- before
      any number appeared, all of it sitting beside the Sort control. Three
      labels for one control is why it did not parse: a reader could not tell
      which words were the control's name, which were its current value, and
      which were prose.

      Two things changed and neither is a fact.

      The CONTROL is now named once, by what it does, and its two options say
      what they do (see the .tit-detail markup). The SENTENCE now leads with the
      number it is there to disclose and defines "routine" in a trailing clause
      rather than a leading one, so the first thing a reader gets is the size of
      what is being held back.

      Every figure is still computed from the current view and still moves with
      the filters, all three still appear so the arithmetic can be checked, and
      the sentence is still printed unconditionally. A default that sets
      thousands of rows aside has to say so where the reader is about to look at
      rows, and that has not moved.
    */
    $what = ' A routine filing records only an officer or director change, with'
          . ' no headcount, no money and no location.';

    if ($routine === 0) {
        return sprintf(
            _n('None of the %s update here is a routine filing.',
               'None of the %s updates here are routine filings.', $total, 'tit'),
            number_format_i18n($total)
        ) . $what;
    }

    if ($mode === 'all') {
        return sprintf(
            'You are seeing all %1$s updates, including the %2$s routine ones.',
            number_format_i18n($total), number_format_i18n($routine)
        ) . $what;
    }

    return sprintf(
        'You are seeing %1$s of %2$s updates. %3$s routine filings are hidden.',
        number_format_i18n($notable), number_format_i18n($total),
        number_format_i18n($routine)
    ) . $what;
}

/**
 * Amount-raised bands, keyed by the dollar floor they send to /query.
 *
 * Bands rather than a number box. Someone looking for funded employers thinks
 * "bigger than about ten million", not "at least 12,400,000", and a typed
 * threshold is the fastest way to produce a page that looks empty because of
 * one digit. Four orders of magnitude cover the whole range we hold.
 */
function tit_funding_bands() {
    return array(
        '1000000'    => 'Over $1M',
        '10000000'   => 'Over $10M',
        '100000000'  => 'Over $100M',
        '1000000000' => 'Over $1B',
    );
}

/**
 * What a record is based on, in words a reader already owns.
 *
 * "verified", "reported" and "rumored" are the stored values and they are our
 * vocabulary, not anyone else's: "verified" sounds like a badge rather than a
 * statement about the SOURCE. These say what the source actually is.
 */
function tit_confidence_labels() {
    return array(
        'verified' => 'Official Filing',
        'reported' => 'News Report',
        'rumored'  => 'Unconfirmed',
    );
}

/**
 * THE SHARED DIRECTION VOCABULARY. Four strings, and they are not only ours.
 *
 * MIRRORS direction_labels IN docs/card-contract.json AND DIRECTION_LABEL IN
 * assets/dashboard.js, AND THE SIBLING AI LAYOFF TRACKER'S OWN COPY OF BOTH.
 * Changing a word here without changing it in all four places fails
 * tests/test_card_contract.py in this repo and, once the contract file differs,
 * .github/workflows/card-contract.yml in both.
 *
 * "Adding Roles", not "Hiring up". The owner asked what "hiring up" meant, and
 * it is a fair question about a phrase nobody says: "up" is doing the work of
 * "the source told us headcount is going up", which a reader has to
 * reverse-engineer. "Adding Roles" is the thing itself. "Cutting Roles" is its
 * opposite in the same shape, where "Cutting back" could have meant costs,
 * hours or investment. Stored values (hiring, displacement, comp_shift,
 * neutral) unchanged.
 *
 * "Headcount Not Stated" replaced "Other change", which told the reader
 * nothing: it is the bucket for updates whose source says nothing about
 * headcount at all (a funding round with no hiring plan, a CEO succession).
 * Naming that plainly is both clearer and truer to the rule that we never infer
 * a direction the source did not state. The sibling reuses that same bucket for
 * a layoff whose record names no headcount, which is the identical fact.
 *
 * Title Case, deliberately, and the shared contract records why: the owner has
 * asked for Title Case three times and tests/php/render_dashboard.php enforces
 * it here. The sentence-case house rule governs every label outside these four.
 */
function tit_direction_labels() {
    return array(
        'hiring'       => 'Adding Roles',
        'displacement' => 'Cutting Roles',
        'comp_shift'   => 'Pay Change',
        'neutral'      => 'Headcount Not Stated',
    );
}

/**
 * ONE RESULT CARD, TO THE SHARED CONTRACT IN docs/card-contract.json.
 *
 * This markup and renderCard() in assets/dashboard.js MUST produce the same
 * shape, or a filtered card would lay out differently from the card it
 * replaced. It must also match the sibling AI Layoff Tracker's card: same
 * regions, same class suffixes, same badge order, same four direction words.
 * tests/test_card_contract.py pins all of that against the contract file, and
 * .github/workflows/card-contract.yml pins the contract file against the
 * sibling's copy of it, so neither product can wander alone.
 *
 * Structure, and it is the reading order a person needs:
 *   rail  who they are, what sector, where
 *   body  what kind of move (direction, evidence, amount), the fact, our read
 *   foot  when, and the document it came from
 */
/**
 * Read-throughs the board collector stamps VERBATIM on every row it emits.
 *
 * These are category explanations wearing a per-record field: every job-board
 * growth row carries the first string word for word and every posted-pay row
 * the second, so a page of board readings said the same 44 words card after
 * card. The page explains the category once (the About Job Board Readings
 * note) and the cards keep only what is theirs. EXACT match, whole string:
 * a read-through the model wrote about one record can never be caught by it.
 * BOILERPLATE_RT in dashboard.js mirrors this list character for character;
 * collectors/ats_boards.py is the source of both strings.
 */
function tit_boilerplate_readthroughs() {
    return array(
        'A board that grows week on week is the earliest public evidence '
        . 'of hiring intent an employer produces: it moves before any '
        . 'announcement and before any filing. Treat the direction as the '
        . 'reliable part and the exact count as approximate, since roles are '
        . 'reposted, split across locations and withdrawn without notice.',
        'Advertised bands move before published pay data does, because '
        . 'they are set by what an employer thinks it must offer to fill '
        . 'a role today. Read the direction across postings rather than '
        . 'any one band, and remember this is the ask, not the settlement.',
        'What an employer advertises is the offer a candidate can '
        . 'accept today. It moves before any return-to-office '
        . 'announcement, because a policy reaches the job postings '
        . 'before it reaches a press release. Read it as the shape of '
        . 'the roles being hired for, not of the whole workforce, and '
        . 'read the direction rather than the exact share.',
    );
}

function tit_card_html($r) {
    $directions  = tit_direction_labels();
    $confidences = tit_confidence_labels();
    $industries  = tit_industry_labels();

    $dir_key = isset($r['signal_direction']) ? (string) $r['signal_direction'] : 'neutral';
    $conf    = isset($r['confidence']) ? (string) $r['confidence'] : '';

    // Fall back to headquarters when the source named no place, and say so.
    $is_hq = empty($r['city']) && empty($r['country']);
    $place = $r['city'] ?: ($r['hq_city'] ?? '');
    $cc    = $r['country'] ?: ($r['hq_country'] ?? '');
    $where = trim(($place ? $place . ', ' : '') . tit_country_name($cc), ', ');

    ob_start(); ?>
<li class="tit-card">
  <div class="tit-card-rail">
    <span class="tit-card-employer"><?php
      $ck = $r['company_key'] ?? '';
      if ($ck && function_exists('tit_company_url')) {
          printf('<a href="%s">%s</a>', esc_url(tit_company_url($ck)), esc_html($r['company']));
      } else {
          echo esc_html($r['company']);
      }
    ?></span>
    <?php /* Omitted entirely when the record carries none. Never an empty line
             and never a placeholder: the contract asks for the field to be
             absent, not blank. */
    if (!empty($r['industry'])) : ?>
      <span class="tit-card-industry"><?php
        echo esc_html($industries[$r['industry']] ?? $r['industry']); ?></span>
    <?php endif; ?>
    <span class="tit-card-where"><?php
      if ($where === '') {
          // Stored anyway: geography is how we segment, not what makes the
          // record true. Saying so beats a blank line.
          echo '<span class="tit-card-nowhere">Location not stated</span>';
      } else {
          echo esc_html($where);
          if ($is_hq) echo ' <span class="tit-hq" title="Employer headquarters, not a location named in the source">HQ</span>';
      }
    ?></span>
  </div>
  <div class="tit-card-body">
    <?php /* Contract badge order: direction, evidence, amount. Colour never
             carries any of them on its own, which is why each one says its
             words and the words are the part the contract pins. */ ?>
    <div class="tit-card-badges">
      <span class="tit-card-dir tit-tag tit-<?php echo esc_attr($dir_key); ?>"><?php
        echo esc_html($directions[$dir_key] ?? $dir_key); ?></span>
      <span class="tit-card-ev tit-conf tit-c-<?php echo esc_attr($conf); ?>"><?php
        echo esc_html($confidences[$conf] ?? $conf); ?></span>
      <?php /* ONLY when there is an amount. There is no "no funding stated"
               pill: the direction badge already says what the source did and
               did not tell us, and a second badge repeating it was the
               duplicate the shared contract removed. */
      $usd = (float) ($r['funding_amount_usd'] ?? 0);
      if ($usd > 0) : ?>
        <span class="tit-card-amt"><?php echo esc_html(tit_money_short($usd)); ?><span
          class="tit-card-amt-unit"> raised</span></span>
      <?php endif; ?>
    </div>
    <span class="tit-card-h tit-h"><?php echo esc_html($r['headline']); ?></span>
    <?php /* A read-through stamped verbatim on every automated board reading
             is boilerplate, not a read of THIS record, so the page prints it
             once (the About Job Board Readings note over the cards) rather
             than card after card. The record keeps its text; the contract's
             card-rt is optional, so omitting it is an allowed state. Exact
             match only: anything the model actually wrote about this record
             renders untouched. renderCard() in dashboard.js applies the same
             rule or a repaint would resurrect the paragraph. */
    if (!empty($r['talent_readthrough'])
        && !in_array(trim($r['talent_readthrough']), tit_boilerplate_readthroughs(), true)) : ?>
      <p class="tit-card-rt tit-rt"><?php echo esc_html($r['talent_readthrough']); ?></p>
    <?php endif; ?>
    <div class="tit-card-foot">
      <?php
      $when = $r['published_date'] ?: '';
      if ($when) {
          printf('<time class="tit-card-when" datetime="%s">%s</time>',
                 esc_attr(substr($when, 0, 10)),
                 esc_html(date_i18n('j M Y', strtotime($when))));
      } else {
          echo '<span class="tit-card-when tit-card-nowhere">Date not stated</span>';
      }
      ?>
      <span class="tit-card-src"><a href="<?php echo esc_url($r['source_url']); ?>" rel="nofollow noopener" target="_blank"><?php
        echo esc_html($r['source_name']); ?></a><?php
        // The fallback, and only ever a SECOND link. Publishers unpublish,
        // rewrite their URL schemes and let domains lapse, and when that
        // happens a sourced claim silently becomes an unsourced one. A neutral
        // third-party snapshot keeps the evidence reachable. The publisher's
        // own copy is the citation and stays the citation; this never replaces
        // it.
        //
        // Three states, one renderer (tit_archive_note_html): the "Archived"
        // link where a snapshot exists; on a publisher-sourced row without one,
        // the pending sentence with a DERIVED next-check date (see
        // tit_archive_promise() — the owner asked for the state to be said, not
        // implied by absence); and nothing at all on rows whose documents a
        // government already preserves, because promising those a re-check the
        // schedule never makes would be a false sentence. Still never a dead
        // link and never a disabled control.
        //
        // The separator is not in this markup. It is a CSS ::before, because
        // this shares one wrapping line with the date and a literal middot that
        // wraps lands at the START of the new line and reads as a bullet whose
        // text went missing. See the .tit-archived rules in dashboard.css.
        echo tit_archive_note_html($r['archive_url'] ?? '', $r['collector'] ?? ''); ?></span>
    </div>
  </div>
</li>
<?php
    return ob_get_clean();
}

/** Round names as a reader would say them, matching the pipeline's vocabulary. */
function tit_funding_stage_labels() {
    return array(
        'pre_seed' => 'Pre-Seed', 'seed' => 'Seed',
        'series_a' => 'Series A', 'series_b' => 'Series B',
        'series_c' => 'Series C', 'series_d_plus' => 'Series D or Later',
        'growth' => 'Growth', 'debt' => 'Debt', 'grant' => 'Grant',
        'ipo' => 'IPO', 'other' => 'Other',
    );
}

/**
 * The primary control, phrased as what someone came here to find.
 *
 * Keys are parameter specs, not column values, because the things people
 * actually look for do not all live in one column: "Hiring" is a direction,
 * "Funding" is a funding update, and the rest are pillars. Making the reader
 * work out which is which was the whole problem with the old twelve-control bar.
 *
 * Every pillar and both headcount directions appear, so nothing reachable
 * through the old What happened and Headcount dropdowns is shut off by folding
 * them into one control.
 */
function tit_looking_options() {
    return array(
        ''                           => 'All Updates',
        'direction=hiring'           => 'Adding Roles',
        'funding=1'                  => 'Raised Money',
        'pillar=leadership_change'   => 'Leadership Moves',
        'pillar=rewards_comp'        => 'Pay and Benefits',
        'pillar=how_we_work'         => 'Ways of Working',
    );
}

/**
 * What the Headcount filter OFFERS, which is not the same list as what a row
 * can BE.
 *
 * comp_shift is a real stored value and rows wearing it still say "Pay change",
 * but it is not a headcount direction and it duplicates a filter that already
 * exists on the right axis. "Not stated" is shortened from the badge's
 * "Headcount not stated" because the control above it already says Headcount.
 */
function tit_direction_filter_options() {
    return array(
        'hiring'  => 'Adding Roles',
        'neutral' => 'Not Stated',
    );
}

/**
 * Why "Cutting back" is no longer offered.
 *
 * Measured live: hiring 4,018, comp_shift 9,217, neutral 3,361, displacement
 * SEVEN, out of 16,603. An option returning 0.04% is not a filter, it is a way
 * to make the page look broken, and this tracker's own footer says layoffs are
 * collected separately. The stored value is untouched and those rows still
 * appear in the table, because deleting accurate records to tidy a dropdown
 * would be the dishonest fix. /query still accepts direction=displacement, so
 * an existing link keeps working.
 *
 * Anyone looking for cuts is sent to the sibling tracker, which is the product
 * that actually collects them.
 */

/** Reader-facing industry names, in one place so the page and the money charts agree. */
function tit_industry_labels() {
    return array(
        'technology' => 'Technology', 'financial_services' => 'Financial Services',
        'healthcare' => 'Healthcare', 'pharma_biotech' => 'Pharma & Biotech',
        'retail_ecommerce' => 'Retail & E-commerce', 'manufacturing' => 'Manufacturing',
        'energy_utilities' => 'Energy & Utilities', 'telecom' => 'Telecom',
        'media_entertainment' => 'Media & Entertainment',
        'transport_logistics' => 'Transport & Logistics',
        'professional_services' => 'Professional Services',
        'public_sector' => 'Public Sector', 'hospitality_travel' => 'Hospitality & Travel',
        'education' => 'Education', 'food_beverage' => 'Food & Beverage',
        'automotive' => 'Automotive', 'aerospace_defence' => 'Aerospace & Defence',
        'real_estate_construction' => 'Real Estate & Construction',
    );
}

/**
 * One money card, built on the same chart-card parts as every other card:
 * tit_chart_head (heading, share, CSV download, expand), a .tit-rank list of
 * bar rows, and rows that are real buttons so a click filters the whole page.
 *
 * The only differences are the ones that have to exist: the bar carries a
 * summed dollar figure rather than a count, the figure is abbreviated with the
 * exact number on the title attribute, and the card prints what its totals are
 * based on. dashboard.js mirrors this markup when it repaints from /aggregate.
 */
function tit_money_chart($id, $title, $sub, array $rows, array $money, $dimension, callable $labeller, $labeller_returns_html = false) {
    $max = 0.0;
    foreach ($rows as $r) { $max = max($max, (float) $r['v']); }
    ?>
    <div class="tit-chart tit-chart-money" id="chart-money-<?php echo esc_attr($id); ?>">
      <?php
      /*
        THE COVERAGE SENTENCE MOVED INTO THE (i), AND IT WAS PRINTED THREE
        TIMES. It is one sentence about the currency and what share of updates
        carry a figure at all, and it was identical on all three money cards, so
        the grid carried nine lines saying one thing. It is per-card and not
        per-section because dashboard.js recomputes it for each card under the
        filters in force, and a card can place a different share of the money
        than its neighbour. dashboard.js still finds it by .tit-money-note.
      */
      ob_start(); ?>
      <p class="tit-money-note"><?php echo esc_html(tit_money_coverage_note($money, $dimension)); ?></p>
      <?php $note = ob_get_clean();
      tit_chart_head($title, $sub, 'money-' . $id, $note); ?>
      <div class="tit-rank" tabindex="0" role="group" aria-label="<?php echo esc_attr($title); ?>"
           aria-describedby="<?php echo esc_attr(tit_chart_note_id('money-' . $id)); ?>">
        <?php if (!$rows) : ?>
          <?php /* The empty state EXPLAINS ITSELF. See tit_money_empty_note():
                   three causes, three sentences, and the only number in any of
                   them is measured. */ ?>
          <p class="tit-rank-empty"><?php
            echo esc_html(tit_money_empty_note($money, $dimension)); ?></p>
        <?php else : foreach ($rows as $r) :
          $v = (float) $r['v']; ?>
          <button type="button" class="tit-rank-row" data-k="<?php echo esc_attr($r['k']); ?>"
                  <?php /* number_format, not a float cast: PHP renders a large
                           float in scientific notation, and "1.0E+9" in a CSV
                           column is worse than no column. */ ?>
                  data-v="<?php echo esc_attr(number_format($v, 0, '.', '')); ?>" aria-pressed="false">
            <span class="tit-rank-name"><?php
              // Only the country labeller returns markup, and only ever the
              // flag-plus-name helper, which escapes the name itself.
              echo $labeller_returns_html ? $labeller($r['k']) : esc_html($labeller($r['k'])); ?></span>
            <span class="tit-rank-track"><span class="tit-rank-fill"
              style="width:<?php echo esc_attr($max > 0 ? max(4, round(100 * $v / $max)) : 4); ?>%"></span></span>
            <span class="tit-rank-n" title="<?php echo esc_attr(tit_money_full($v)); ?>"><?php
              echo esc_html(tit_money_short($v)); ?></span>
          </button>
        <?php endforeach; endif; ?>
      </div>
    </div>
    <?php
}

/**
 * Absolute time in the SITE's configured timezone with its abbreviation:
 * "Jul 28, 1:51 PM EDT". The sibling tracker leads with exactly this shape.
 * wp_date() renders in wp_timezone(), so a change under Settings > General
 * moves every timestamp with it; never a hardcoded offset. Accepts a UTC
 * MySQL datetime or a Unix timestamp.
 */
function tit_local_datetime($utc) {
    $ts = is_numeric($utc) ? (int) $utc : strtotime($utc . ' UTC');
    if (!$ts) return '';
    // A site whose timezone is set to plain UTC renders 'T' as "GMT+0000",
    // which reads like a machine header, not a time a person wrote. Same
    // instant, human label. Named zones (EDT, CET) pass through untouched.
    return str_replace('GMT+0000', 'UTC', wp_date('M j, g:i A T', $ts));
}

/**
 * The collection schedule, read from data/ingest-schedule.json — generated
 * from .github/workflows/collect.yml (the cron that actually runs) by
 * generate_ingest_schedule.py, and drift-guarded by
 * tests/test_ingest_schedule.py. Missing or malformed file returns null and
 * every caller renders NOTHING there, because an absent schedule is honest
 * and a stale typed one is not: the typed twice-daily hours this replaces
 * kept promising a 6 AM run for hours after the cron moved to 16:00 UTC.
 */
function tit_ingest_schedule() {
    $path = TIT_PATH . 'data/ingest-schedule.json';
    if (!is_readable($path)) return null;
    $j = json_decode((string) file_get_contents($path), true);
    if (!is_array($j) || empty($j['utc_hours']) || !is_array($j['utc_hours'])) return null;
    $hours = array();
    foreach ($j['utc_hours'] as $h) {
        $h = (int) $h;
        if ($h >= 0 && $h <= 23) $hours[] = $h;
    }
    if (!$hours) return null;
    sort($hours);
    $minute = (int) ($j['utc_minute'] ?? 0);
    if ($minute < 0 || $minute > 59) $minute = 0;
    return array('utc_hours' => array_values(array_unique($hours)), 'utc_minute' => $minute);
}

/**
 * When the next collection run is due, as a UTC timestamp, or 0 without a
 * schedule file.
 *
 * "Resting until the next run" invites the obvious question and then does not
 * answer it. The hours come from tit_ingest_schedule(), never a typed list:
 * typed hours are how the strip promised "Next run 6:00 AM UTC" after the
 * cron moved to 16:00. Printed in the site's own timezone by the caller,
 * because a reader should not have to convert.
 */
function tit_next_run() {
    $s = tit_ingest_schedule();
    if (!$s) return 0;
    $now = time();
    foreach (array(0, 1) as $day) {
        foreach ($s['utc_hours'] as $hour) {
            $t = strtotime(gmdate('Y-m-d', $now + $day * DAY_IN_SECONDS)
                           . sprintf(' %02d:%02d:00 UTC', $hour, $s['utc_minute']));
            if ($t > $now) return $t;
        }
    }
    return 0;
}

/**
 * The run times as clock times in the site's timezone: "12 PM EDT", or
 * "6 AM and 6 PM EDT" when the cron carries two hours. Computed from today's
 * occurrences, so daylight saving moves the label with the clock (the cron is
 * UTC-fixed, and a typed Eastern label would be wrong half the year).
 * Empty without a schedule, and the caller says nothing rather than guessing.
 */
function tit_ingest_times_label() {
    $s = tit_ingest_schedule();
    if (!$s) return '';
    // On the hour reads as "12 PM EDT", not "12:00 PM EDT". Same handling of a
    // plain-UTC site's "GMT+0000" as tit_local_datetime(): same instant, a
    // label a person would actually write.
    $format = $s['utc_minute'] ? 'g:i A T' : 'g A T';
    $parts = array();
    foreach ($s['utc_hours'] as $hour) {
        $ts = strtotime(gmdate('Y-m-d') . sprintf(' %02d:%02d:00 UTC', $hour, $s['utc_minute']));
        $parts[] = str_replace('GMT+0000', 'UTC', wp_date($format, $ts));
    }
    return implode(' and ', $parts);
}

/**
 * "once a day" / "twice a day" from the schedule itself, so the cadence word
 * and the cron can never disagree. Empty without a schedule.
 */
function tit_ingest_cadence_phrase() {
    $s = tit_ingest_schedule();
    if (!$s) return '';
    $n = count($s['utc_hours']);
    if ($n === 1) return 'once a day';
    if ($n === 2) return 'twice a day';
    return sprintf('%d times a day', $n);
}

/**
 * How much history actually sits behind those tiles, in words.
 *
 * Printed under the glance block because the tiles alone cannot say it: "46
 * updates" reads like a running total from a tracker that has been going a
 * while, and right now it is eight days of collection.
 */
function tit_span_note($lo, $hi) {
    if (!$lo || !$hi) return '';

    /*
      This line said "Everything here spans 3,318 days, 28 Jun to 28 Jul 2026",
      which is nine years of days against thirty days of dates, and a page whose
      own date range does not add up undermines every number beside it.

      Both halves came from the same query and the same two values all along.
      What broke was the FORMAT: the low bound printed as 'j M' with no year.
      That read correctly while everything we held sat inside one year, and
      turned into a contradiction the moment the UK pay-gap rows reached back to
      2017 and the Form D sweep began adding 2009 onward. A format that is only
      correct for a range you happen to hold today is a bug with a fuse on it.

      Now: both bounds carry their year, and the day count is gone. It was
      there to stop "46 updates" reading like a long-running tracker, and that
      job is done by the real dates the moment they span years.
    */
    /*
      Clamp the upper bound to today. "Covering" describes COLLECTION, and a
      row whose stated effective date sits in the future (a filing effective
      next month is legitimate data) put "to 2 Sep 2026" on the live ribbon on
      2 Aug 2026 - a tracker that claims to cover a month that has not happened
      reads as broken even though every underlying row is honest. The record
      keeps its future date; only this sentence is clamped.
    */
    $alt_today = current_time('Y-m-d');
    if (substr($hi, 0, 10) > $alt_today) {
        $hi = $alt_today;
    }
    if (substr($lo, 0, 10) >= substr($hi, 0, 10)) {
        return sprintf('Covering %s', date_i18n('j M Y', strtotime($hi)));
    }
    return sprintf(
        /* translators: 1: earliest date, 2: latest date, both with the year */
        'Covering %1$s to %2$s',
        date_i18n('j M Y', strtotime($lo)),
        date_i18n('j M Y', strtotime($hi))
    );
}

/**
 * Roo, the AskTheRecruiter robot, ported from the sibling tracker.
 *
 * He reports the state of COLLECTION, not the state of the page. Wiring him to
 * the dashboard's own fetches would have been easier and would have made him
 * animate far more often, but it would also have meant a mascot that looks busy
 * because someone changed a dropdown. He works when a run has just landed and
 * sleeps the rest of the time, which is the truth and is usually "asleep".
 *
 * State is decided server-side from the newest capture, so the first paint is
 * already right; no request, and nothing to get wrong before JavaScript runs.
 */
function tit_roo($newest_run) {
    $ts     = $newest_run ? strtotime($newest_run . ' UTC') : 0;
    $ago    = $ts ? (time() - $ts) : null;
    // A run writes over a few minutes, so anything inside the last quarter hour
    // is treated as still in progress rather than finished.
    $working = ($ago !== null && $ago >= 0 && $ago < 900);
    $state   = $working ? 'tit-roo-working' : 'tit-roo-sleeping';

    $next = tit_next_run();

    // The absolute timestamp with timezone leads up in the Live pill; down
    // here the relative time is the secondary fact and the next-run note is
    // the quietest part of the line (its own span, styled smaller). Next-run
    // times go through tit_local_datetime() too: the site's timezone, never a
    // hand-converted hour.
    if ($working) {
        $say = 'Roo is pulling in new filings and news';
    } elseif ($ago !== null) {
        $say = sprintf('Roo pulled the latest data %s ago.', human_time_diff($ts, time()));
    } else {
        $say = 'Roo is resting until the next run.';
    }
    // Absolute time only. Roo's own sentence already carries the relative
    // ("pulled the latest data 1 hour ago"), and the freshness panel's job is
    // fewer words, not the same fact twice (design adoption, 2026-08-02).
    $next_note = $next ? sprintf('Next run %s.', tit_local_datetime($next)) : '';
    ?>
    <span class="tit-roo-wrap<?php echo $working ? ' is-working' : ' is-sleeping'; ?>" aria-hidden="true">
      <span class="tit-zzz"><i>z</i><i>z</i><i>z</i></span>
      <svg class="tit-roo <?php echo esc_attr($state); ?>" width="52" height="56" viewBox="0 0 140 150">
        <line x1="70" y1="14" x2="70" y2="26" stroke="var(--roo-deep)" stroke-width="3" stroke-linecap="round"/>
        <circle class="roo-bulb" cx="70" cy="10" r="5" fill="var(--roo-accent)"/>
        <rect x="26" y="24" width="88" height="40" rx="20" fill="var(--roo-surface)" stroke="var(--roo-deep)" stroke-width="3.5"/>
        <g class="roo-eyes">
          <circle cx="51" cy="44" r="12" fill="var(--roo-soft)" stroke="var(--roo-deep)" stroke-width="2.5"/>
          <g class="roo-pupil"><circle cx="53.5" cy="44" r="5.5" fill="var(--roo-deep)"/><circle cx="55.5" cy="42" r="1.8" fill="var(--roo-surface)"/></g>
          <circle cx="89" cy="44" r="12" fill="var(--roo-soft)" stroke="var(--roo-deep)" stroke-width="2.5"/>
          <g class="roo-pupil"><circle cx="86.5" cy="44" r="5.5" fill="var(--roo-deep)"/><circle cx="88.5" cy="42" r="1.8" fill="var(--roo-surface)"/></g>
        </g>
        <path d="M 59 57 Q 70 61 81 57" fill="none" stroke="var(--roo-deep)" stroke-width="2.5" stroke-linecap="round"/>
        <g class="roo-body-group">
          <rect x="64" y="64" width="12" height="8" fill="var(--roo-deep)" rx="2"/>
          <rect class="roo-arm-l" x="18" y="82" width="14" height="28" rx="7" fill="var(--roo-surface)" stroke="var(--roo-deep)" stroke-width="3"/>
          <rect class="roo-arm-r" x="108" y="82" width="14" height="28" rx="7" fill="var(--roo-surface)" stroke="var(--roo-deep)" stroke-width="3"/>
          <rect x="36" y="72" width="68" height="46" rx="10" fill="var(--roo-surface)" stroke="var(--roo-deep)" stroke-width="3.5"/>
          <rect x="48" y="80" width="44" height="30" rx="5" fill="var(--roo-tint)" stroke="var(--roo-deep)" stroke-width="2"/>
          <rect class="roo-line" x="54" y="86" height="3.5" width="26" rx="1.75" fill="var(--roo-deep)"/>
          <rect class="roo-line" x="54" y="93" height="3.5" width="18" rx="1.75" fill="var(--roo-deep)"/>
          <rect class="roo-line" x="54" y="100" height="3.5" width="22" rx="1.75" fill="var(--roo-deep)"/>
        </g>
        <rect x="30" y="122" width="80" height="14" rx="7" fill="var(--roo-soft)" stroke="var(--roo-deep)" stroke-width="2.5"/>
        <circle class="roo-tread-dot" cx="42" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
        <circle class="roo-tread-dot" cx="56" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
        <circle class="roo-tread-dot" cx="70" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
        <circle class="roo-tread-dot" cx="84" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
        <circle class="roo-tread-dot" cx="98" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
      </svg>
    </span>
    <span class="tit-roo-say"><?php echo esc_html($say); ?><?php
      if ($next_note) : ?> <span class="tit-roo-next"><?php echo esc_html($next_note); ?></span><?php endif;
    ?></span>
    <?php
}

/** The id of the note panel belonging to a chart card. One rule, two files. */
function tit_chart_note_id($id) {
    return 'tit-note-' . $id;
}

/**
 * The heading block every chart card shares: its title, its controls, and the
 * prose that used to sit under the title.
 *
 * WHY THE PROSE MOVED BEHIND AN (i), AND WHY IT IS STILL THERE.
 *
 * Nine cards in a grid each carrying two to six lines of explanation is a page
 * a reader scrolls past to reach the bars. The three money cards printed the
 * SAME currency sentence three times. So the sentence moves behind an info
 * button on the card it belongs to.
 *
 * IT IS MOVED, NEVER DROPPED, and the difference is the whole point. What sits
 * in here is what stops each chart overclaiming: how many collectors fed the
 * trend window, which signals are not drawn and why, which country one
 * collector dominates, what share of updates carry a dollar figure. This
 * project does not hide those, so:
 *
 *   1. The panel ships OPEN in the served markup and the (i) ships hidden.
 *      dashboard.js closes the panel and reveals the button, in that order, so
 *      a reader whose script never ran gets every caveat as plain prose rather
 *      than a button that opens nothing. Same rule the collapsing filter bar
 *      follows.
 *   2. The button is a real <button> carrying aria-expanded, so it is reachable
 *      by keyboard.
 *   3. The chart's own group element points at the panel with aria-describedby,
 *      so a screen reader reads the caveat as the chart's description whether
 *      the panel is open or shut. A title= attribute would have been reachable
 *      by neither a keyboard nor a screen reader, which is why it is not one.
 *
 * NO aria-label ON ANYTHING THAT HAS ITS OWN WORDS. An aria-label REPLACES the
 * text under it, and this product shipped invisible labels over visible ones
 * once already. The two icon-only buttons keep theirs because they have no text
 * at all; the expand button lost its, because it carries a visually hidden
 * label that dashboard.js rewrites between "Expand" and "Collapse" and the
 * aria-label was silently winning over both.
 */
function tit_chart_head($title, $sub, $id = '', $note_html = '', $defer_note = false) {
    $note_id = tit_chart_note_id($id);
    $esc_id  = esc_attr($id);

    /*
      BUILT AS A STRING RATHER THAN AS A TEMPLATE, and only this one function,
      because it is printed nine times. Four controls laid out as indented
      markup cost about sixty bytes of leading whitespace per button that no
      reader ever sees; nine cards times four buttons is two kilobytes of it on
      a page that is read on phones and has a measured byte budget
      (TIT_DASH_BYTE_BUDGET in tests/php/render_dashboard.php). Nothing else on
      this page is written this way and nothing else should be: the trade is
      only worth making where the markup repeats.
    */
    $btn = function ($class, $svg, $label, $attrs) {
        return '<button type="button" class="tit-ctl ' . $class . '" ' . $attrs
             . ' title="' . esc_attr($label) . '" hidden>'
             . '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' . $svg . '</svg>';
    };

    echo '<div class="tit-chart-head"><div class="tit-chart-titles"><h3>'
       . esc_html($title) . '</h3></div><div class="tit-chart-tools">'
       /*
         Its accessible name is the visually hidden text inside it and NOT an
         aria-label, which would replace that text rather than add to it.

         It is DESCRIBED BY the panel it opens, as well as controlling it, so a
         screen reader announces the caveat on reaching the button whether the
         panel is open or shut. Every card's data group carries the same
         reference, which covers a reader who lands on the bars instead; the
         trend card has no such group in one of its two states, and this is
         what makes it uniform across all nine.
       */
       . $btn('tit-chart-info',
              '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.6v.9"/>',
              'What this chart counts',
              'aria-expanded="true" aria-controls="' . esc_attr($note_id) . '"'
              . ' aria-describedby="' . esc_attr($note_id) . '"')
       . '<span class="tit-sr">What this chart counts</span></button>'
       // These two are icon only, so an aria-label is their only possible name.
       . $btn('tit-chart-share',
              '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 10.7l6.8-4M8.6 13.3l6.8 4"/>',
              'Copy a link to this view',
              'data-chart="' . $esc_id . '" aria-label="Copy a link to this view"')
       . '</button>'
       . $btn('tit-chart-dl',
              '<path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/>',
              'Download this chart as CSV',
              'data-chart="' . $esc_id . '" aria-label="Download this chart as CSV"')
       . '</button>'
       . $btn('tit-expand',
              '<path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7"/>',
              'Expand this chart', 'aria-expanded="false"')
       . '<span class="tit-sr tit-expand-t">Expand</span></button>'
       . '</div></div>';

    /*
      ONE card defers its panel: the trend's whole note moves with the filters
      (which signals could honestly be drawn changes when the view narrows), so
      it is rebuilt by the body renderer under the same id rather than printed
      here and left stale. Two panels with one id would be invalid markup and
      the (i) would open whichever one the browser found first.
    */
    if ($defer_note) return;

    echo '<div class="tit-chart-note" id="' . esc_attr($note_id) . '">'
       . '<p class="tit-sub">' . esc_html($sub) . '</p>'
       // Every caller escapes its own.
       . $note_html . '</div>';
}

/**
 * The region strip above the charts.
 *
 * Regions are groups of country codes, not a separate dimension in the data —
 * "Europe" is a shorthand for a list, and the tab sends that list to the API.
 *
 * A region with nothing in it is dropped rather than shown at zero. An empty
 * tab reads as a filter that broke, and a strip of them reads as coverage we
 * do not have. Worldwide always survives so there is always a way back.
 */
/**
 * The emoji a region pill wears. Decoration only, always aria-hidden, and the
 * name is always printed beside it, so a platform with no emoji font loses
 * nothing but colour.
 */
function tit_region_emoji($name) {
    $map = array(
        'World' => "\u{1F310}", 'Americas' => "\u{1F30E}",
        'Europe' => "\u{1F1EA}\u{1F1FA}", 'Middle East' => "\u{1F54C}",
        'Africa' => "\u{1F30D}", 'Asia' => "\u{1F30F}",
        'Oceania' => "\u{1F3DD}\u{FE0F}",
    );
    return $map[$name] ?? "\u{1F310}";
}

/**
 * A country code as its flag emoji: regional indicator symbols are the code
 * points 127462..127487, one per letter. Unknown shapes get the globe.
 */
function tit_flag($cc) {
    if (!preg_match('/^[A-Z]{2}$/', (string) $cc)) return "\u{1F310}";
    return mb_chr(127397 + ord($cc[0]), 'UTF-8') . mb_chr(127397 + ord($cc[1]), 'UTF-8');
}

function tit_regions(array $counts, $view_total) {
    /*
      ONE taxonomy, exhaustive and non-overlapping.

      The old strip read World / USA / Canada / Latin America / Europe / UK /
      Middle East / Africa / India / Asia / Australia, which mixed two
      granularities at identical visual weight and was ambiguous with it: UK sat
      beside Europe, so a reader could not tell whether Europe INCLUDED the UK,
      and clicking each produced numbers that looked like a contradiction. India
      sat beside Asia with the same problem, and USA and Canada sat beside Latin
      America while nothing covered the Americas as a whole.

      Regions now cover the world once each. A country belongs to exactly one,
      and every region CONTAINS its countries: Europe includes the United
      Kingdom, Asia includes India, Americas includes the United States. That is
      what a reader assumes and it makes the totals reconcile.

      No emoji on this row, at all. The strip carried a mosque for the Middle
      East, which puts a religious symbol on a geographic region. Reviewing the
      rest found the same class of error twice more: the EU flag stood for a
      Europe that includes the UK, Switzerland, Norway and Russia, and the
      Australian flag stood for a group that also holds New Zealand, Fiji and
      Papua New Guinea. There is no neutral geographic glyph for "Middle East",
      and a strip where some regions have a symbol and others do not looks
      broken. A text label alone is better than a bad symbol, so none of them
      carry one. Flags return on the country row below, where a flag genuinely
      denotes a country.

      Every list is the WHOLE region, never a shortlist of the big names: a
      Latvian employer once fell outside "Europe" because LV was missing, which
      is a filter quietly lying about its own name.
    */
    $defs = array(
        array('World', ''),
        array('Americas', 'US,CA,MX,BR,AR,CL,CO,PE,UY,CR,EC,BO,PY,VE,GT,HN,SV,NI,PA,DO,'
                        . 'CU,JM,TT,HT,BZ,GY,SR,BS,BB,AG,DM,GD,KN,LC,VC,PR,VI,VG,KY,BM,'
                        . 'AI,MS,TC,AW,CW,SX,BQ,MF,BL,GP,MQ,GF,PM,GL,FK,GS'),
        array('Europe',  'GB,IE,DE,FR,NL,ES,IT,SE,PL,CH,BE,DK,NO,FI,AT,PT,CZ,GR,RO,HU,'
                        . 'LV,LT,EE,SK,SI,HR,BG,RS,UA,IS,LU,MT,CY,AL,BA,ME,MK,MD,BY,MC,'
                        . 'LI,AD,SM,VA,XK,RU,FO,GG,JE,IM,AX,SJ,GI'),
        array('Middle East', 'AE,SA,IL,QA,KW,BH,OM,TR,JO,LB,IQ,IR,SY,YE,PS'),
        array('Africa',  'ZA,NG,KE,EG,MA,GH,ET,NA,TZ,UG,ZM,ZW,BW,MZ,AO,SN,CI,CM,DZ,TN,'
                        . 'LY,SD,SS,RW,MW,MU,MG,CD,CG,GA,BJ,BF,ML,NE,TD,SO,SL,LR,GM,BI,'
                        . 'CF,CV,DJ,ER,GN,GQ,GW,KM,LS,MR,SC,SH,ST,SZ,TG,EH,YT,RE'),
        array('Asia',    'IN,SG,JP,CN,HK,KR,MY,PH,ID,TH,VN,TW,PK,BD,LK,NP,MM,KH,LA,MN,'
                        . 'MO,BN,MV,KZ,UZ,GE,AM,AZ,KG,TJ,TM,AF,BT,KP,TL,IO,CC,CX'),
        array('Oceania', 'AU,NZ,FJ,PG,SB,VU,NC,PF,WS,TO,KI,FM,MH,NR,PW,TV,CK,NU,TK,WF,'
                        . 'AS,GU,MP,NF,PN,UM,AQ,BV,HM,TF'),
    );

    /*
      EVERY BADGE IS THE COUNT ITS OWN TAB RETURNS.

      World's badge was the sum of the country map, which is grouped under
      `COALESCE(country, hq_country) IS NOT NULL` and therefore skips every row
      we hold no geography for. But the World tab sends NO country parameter, so
      it returns those rows too. The badge said 23,991 and the tab returned
      25,479, on the default view, three inches under a hero printing 25,479.

      So World takes the view's own total, the same figure the hero and the
      detail control print, and it is passed in rather than summed here: this
      function cannot see the rows the map left out, and a number it cannot
      derive is a number it must be handed. Every other region is a list of
      codes, and its badge stays the sum over that list, which is exactly what
      sending the list to /query returns.
    */
    $view_total = (int) $view_total;
    $out = array();
    foreach ($defs as [$name, $codes]) {
        if ($codes === '') {
            $out[] = array('name' => $name, 'codes' => '', 'n' => $view_total);
            continue;
        }
        $n = 0;
        foreach (explode(',', $codes) as $c) {
            $n += (int) ($counts[$c] ?? 0);
        }
        // A region with nothing in it is dropped rather than shown at zero: an
        // empty tab reads as a filter that broke. World always survives, so
        // there is always a way back.
        if ($n > 0) {
            $out[] = array('name' => $name, 'codes' => $codes, 'n' => $n);
        }
    }
    return $out;
}

/**
 * The city ribbon's split, in a sentence, computed and never written down.
 *
 * The general rule above ("a place counts an update when the update states
 * that job location or when the employer is based there") is true and abstract,
 * and abstract is not what stops a reader taking "London 2,296" as 2,296 events
 * in London. The instance is what does that, and on 2026-08-13 the instance was
 * 22 stated against 2,274 registered.
 *
 * COMPUTED, for the same reason `tit_place_caveat` is: a figure typed into copy
 * is a figure that goes wrong quietly the next time the denominator moves, and
 * this repo has already paid for that once, on a coverage percentage quoted for
 * sixteen days after the number under it changed. It names whichever city
 * currently leads, and it disappears when the leader is mostly stated, which is
 * the state where the sentence would be noise.
 *
 * The two thirds threshold is `tit_place_caveat`'s own, unchanged: a majority
 * is not a distortion, a two-to-one imbalance is.
 *
 * @param array $cities Rows from $facts['cities'], each with k, n and stated.
 * @return string A leading-space sentence, or '' when there is nothing to say.
 */
function tit_city_basis_note($cities) {
    if (empty($cities) || !is_array($cities)) return '';
    $top = reset($cities);
    $n = isset($top['n']) ? (int) $top['n'] : 0;
    // `stated` is absent on a bundle cached by an older build. Absent is not
    // zero: printing "0 state it" off a missing column would be a wrong number
    // dressed as a correction, so it prints nothing until the cache turns over.
    if ($n < 1 || !isset($top['stated'])) return '';
    $stated = (int) $top['stated'];
    $based  = $n - $stated;
    if ($based < $n * (2 / 3)) return '';
    return sprintf(
        ' Most of a city total is usually the second one: %1$s reads %2$s, of'
        . ' which %3$s state %1$s as the job location and %4$s are employers'
        . ' based there.',
        esc_html($top['k']),
        esc_html(number_format_i18n($n)),
        esc_html(number_format_i18n($stated)),
        esc_html(number_format_i18n($based))
    );
}

/**
 * A one-line caveat when a single collector dominates a single country.
 *
 * Returns '' when nothing is lopsided enough to be worth saying, so the note
 * appears exactly when it is true and vanishes when it stops being true.
 *
 * The bar is deliberately high: two thirds of a country's rows from one source,
 * and at least 200 rows, so a country with nine records cannot produce a
 * warning about nothing. Only the single worst case is named, because a list of
 * caveats reads as an excuse rather than as a fact.
 */
function tit_place_caveat($table, $where = 'is_current = 1', array $params = array(),
                         ?array $country_totals = null) {
    global $wpdb;
    $expr = function_exists('tit_country_expr') ? tit_country_expr() : 'COALESCE(country, hq_country)';
    // Two plain queries rather than one with a correlated subquery: that shape
    // re-counts the whole table once per group, and this runs on every page
    // render.
    $top_sql = "SELECT {$expr} AS cc, collector, COUNT(*) AS n
                  FROM {$table}
                 WHERE {$where} AND {$expr} IS NOT NULL AND {$expr} <> ''
                 GROUP BY cc, collector
                 ORDER BY n DESC LIMIT 1";
    $row = $wpdb->get_row($params ? $wpdb->prepare($top_sql, $params) : $top_sql, ARRAY_A);
    if (!$row || (int) $row['n'] < 200) return '';

    /*
      The country's own total, from the caller's own map when it has one.

      The dashboard has already grouped every country's count under this exact
      clause to draw the place ranking, so asking the database again for one of
      those numbers is a second scan for a figure sitting in memory. /aggregate
      has no such map (its own ranking is a LIMIT 40, and the dominated country
      is not guaranteed to be in it), so it passes nothing and pays for the
      query, which is the honest cost of not already knowing.
    */
    if ($country_totals !== null && isset($country_totals[$row['cc']])) {
        $total = (int) $country_totals[$row['cc']];
    } else {
        // Goes through prepare with the filter params FIRST, because they
        // appear first in statement order.
        $total_sql = "SELECT COUNT(*) FROM {$table} WHERE {$where} AND {$expr} = %s";
        $total = (int) $wpdb->get_var(
            $wpdb->prepare($total_sql, array_merge($params, array($row['cc']))));
    }
    if ($total <= 0 || (int) $row['n'] < $total * 0.66) return '';

    $row['total'] = $total;
    $share = (int) round(100 * (int) $row['n'] / max(1, $total));
    /*
      THE VERDICT FIRST, THEN THE ARITHMETIC.

      The owner read this note beside the chart and asked "why is this here?",
      which is the right question to ask of a sentence that spends twenty words
      on a ratio before saying what the ratio means. It opened with
      "4,761 of the 4,793 rows for United Kingdom (99%) come from one source,
      the UK gender pay gap filing, so read that bar as filing volume rather
      than as how much is happening there" -- so a reader met four numbers and a
      collector name before reaching the only clause that told them what to DO
      with any of it.

      Inverted, and shortened to one sentence: the instruction is the subject,
      the evidence is the subordinate clause. Every figure is still here and
      still computed, because the whole value of the caveat is that it is
      checkable; what changed is the order, which is free.

      Placement is unchanged and deliberate. It renders inside #chart-place, in
      a tinted note directly under the ranking it qualifies, so a reader meets
      it while looking at the UK bar rather than before or after.
    */
    return sprintf(
        'Read %3$s as filing volume rather than as how much is happening there:'
        . ' %1$s of its %2$s rows (%4$s%%) come from one source, %5$s.',
        number_format_i18n((int) $row['n']),
        number_format_i18n((int) $row['total']),
        tit_country_name($row['cc']),
        number_format_i18n($share),
        tit_collector_label($row['collector'])
    );
}

/** Collector names as a reader would say them, not as the pipeline keys them. */
function tit_collector_label($key) {
    $map = array(
        'uk_paygap'    => 'the UK gender pay gap filing',
        'sec_edgar'    => 'SEC 8-K filings',
        'sec_form_d'   => 'SEC Form D filings',
        'sec_execcomp' => 'SEC executive pay filings',
        'google_news'  => 'Google News',
        'gdelt'        => 'GDELT news monitoring',
        'ats_boards'   => 'company job boards',
        'national_press' => 'national press feeds',
        // The fallback turns 'israel_registrar' into 'israel registrar', which
        // is lower case, names no country properly and does not say what was
        // read. Both of these name the registry rather than the collector.
        'israel_registrar' => 'the Israeli Registrar of Companies',
        'singapore_acra'   => 'the ACRA register of Singapore companies',
    );
    return $map[$key] ?? str_replace('_', ' ', (string) $key);
}

/**
 * The countries worth their own button, BY CURRENT ROW COUNT.
 *
 * The old strip hardcoded USA, Canada, UK, India and Australia. Measured live
 * the same week: the UK held 4,804 rows and Canada 61, while Germany, Israel
 * and Ireland had no button at all. A hardcoded list stops describing the data
 * the moment the data changes, and international feeds landing now will change
 * it weekly. This is derived, so it cannot go stale.
 *
 * Ten is where the row still scans; below the cut a country is one pick away in
 * the Where dropdown, which lists every country we hold.
 */
function tit_top_countries(array $counts, $limit = 10) {
    $counts = array_filter($counts, function ($n) { return (int) $n > 0; });
    arsort($counts);
    $out = array();
    foreach (array_slice($counts, 0, (int) $limit, true) as $code => $n) {
        if (!preg_match('/^[A-Z]{2}$/', (string) $code)) continue;
        $out[] = array('code' => $code, 'n' => (int) $n);
    }
    return $out;
}

/**
 * Enqueue the dashboard assets. Idempotent: WordPress ignores a second
 * enqueue of the same handle, so both the wp_enqueue_scripts hook and the
 * shortcode itself can call this safely.
 *
 * $with_js is false for the routes that are plain content: a country page or the
 * places directory has no filter panel, no charts and no repaint, so shipping
 * dashboard.js there is 60KB of parse work for a page with nothing to bind to.
 * They still get the stylesheet, which is the same file the dashboard already
 * served, so there is no second request for anyone who arrived from the tracker.
 */
function tit_enqueue_dashboard_assets($with_js = true) {
    // Version is TIT_VERSION plus the file's own mtime, the same shape the
    // sibling plugin uses. TIT_VERSION alone is not enough: an FTP deploy can
    // change the stylesheet without the constant moving (a CSS-only fix), and
    // this site runs Autoptimize, which caches a rewritten copy of the file
    // keyed on that version string. Without the mtime the visitor keeps the
    // old rewritten copy and the deploy appears not to have landed.
    wp_enqueue_style('tit-dashboard', TIT_URL . 'assets/dashboard.css', array(),
        tit_asset_version('assets/dashboard.css'));
    if (!$with_js) return;
    wp_enqueue_script('tit-dashboard', TIT_URL . 'assets/dashboard.js', array(),
        tit_asset_version('assets/dashboard.js'), true);
    /*
      DEFER, which our script was the only one on the page not doing.

      Measured live: every other <script src> in that document carries defer --
      jQuery, the theme's modules, the cookie banner, the host's own performance
      module -- and ours did not. In the footer it is not blocking a first paint,
      but a parser-blocking script still stops the parser where it sits until it
      has downloaded and run, which on shared hosting over a phone connection
      holds up the end of the document for no reason. There is nothing here that
      needs to run before parsing finishes: the whole file starts by looking up
      #tit-dashboard, which is already in the document above it.

      wp_script_add_data() rather than the array form of wp_enqueue_script(),
      because 'strategy' arrived in WordPress 6.3 and this way an older WordPress
      ignores one unknown data key instead of receiving an array where it expects
      a boolean.

      It is also SAFE with the Autoptimize hazard this file already guards
      against. wp_localize_script prints `var TIT` inline, immediately above, and
      an inline script is never deferred, so it still runs first. If Autoptimize
      sweeps that inline object into a bundle loading after us -- which is the
      bug that once made every control on the live page inert -- deferring only
      gives that bundle MORE time to land, and the data-attribute fallback at the
      top of dashboard.js covers it either way.
    */
    if (function_exists('wp_script_add_data')) {
        wp_script_add_data('tit-dashboard', 'strategy', 'defer');
    }
    wp_localize_script('tit-dashboard', 'TIT', array(
        'api' => esc_url_raw(rest_url('talent/v1/')),
        // The filtered rows are rendered in the browser, so it needs the same
        // country names the server used. Two copies of this list would drift.
        'countries' => tit_country_names(),
        // And the same for US postal codes, which the state filter rendered raw.
        'states' => tit_state_names(),
    ));
}

function tit_enqueue_assets() {
    /*
      Our own routed pages (company profiles, sources, the corrections log, the
      place pages) carry no shortcode and are not singular posts, so a
      shortcode-only check leaves them completely unstyled. That is how the
      sources page shipped unstyled, and the fix at the time was to name the
      three routes that existed - which then quietly stopped covering
      tit_corrections, and would have stopped covering the place pages too.

      So no route is named. Every query var this plugin registers is prefixed
      tit_, and any of them being set means the request belongs to us. A new
      route is styled the day it exists, with nothing to remember.
    */
    $is_plugin_route = false;
    foreach ((array) ($GLOBALS['wp']->query_vars ?? array()) as $name => $value) {
        if (strpos((string) $name, 'tit_') === 0 && $value) {
            $is_plugin_route = true;
            break;
        }
    }

    if (!$is_plugin_route) {
        if (!is_singular()) return;
        global $post;
        if (!$post || !has_shortcode($post->post_content, 'talent_intelligence_dashboard')) return;
    }

    /*
      Whether this route needs the script, asked rather than assumed. A route
      that is plain content answers false through this filter and gets the
      stylesheet only. It is a filter and not a list of route names here for the
      same reason the check above stopped naming routes: the file that owns a
      route is the only thing that knows what the route needs.
    */
    tit_enqueue_dashboard_assets((bool) apply_filters('tit_route_needs_js', true));
}
add_action('wp_enqueue_scripts', 'tit_enqueue_assets');

/**
 * Keep Autoptimize's hands off this plugin's assets.
 *
 * The live site runs Autoptimize with CSS aggregation on: it strips every
 * <link rel="stylesheet"> and inlines the combined result. Our dashboard.css
 * was enqueued correctly and still never reached the page - the aggregated
 * bundle simply did not contain a single .tit- rule, so the dashboard rendered
 * as raw HTML with every class inert (observed live 2026-07-28).
 *
 * The sibling plugin carries the same two filters for the same reason. Ours
 * were missing, which is why "the CSS exists and is enqueued" and "the page has
 * no styles" were both true at once.
 */
function tit_autoptimize_exclude_css($exclude) {
    return $exclude . ', talent-intelligence-tracker/assets';
}
add_filter('autoptimize_filter_css_exclude', 'tit_autoptimize_exclude_css');

function tit_autoptimize_exclude_js($exclude) {
    // Two entries, not one. The path keeps our file out of the bundle; the
    // `var TIT` string keeps the inline object wp_localize_script prints out of
    // it too. Excluding only the path split the pair apart and left the file
    // running before the object it reads.
    return $exclude . ', talent-intelligence-tracker/assets, var TIT';
}
add_filter('autoptimize_filter_js_exclude', 'tit_autoptimize_exclude_js');
