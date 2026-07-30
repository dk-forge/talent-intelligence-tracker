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
 * of them had already returned. What is left is twelve scans that each fetch
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
 *   1  the money head: the total, its coverage, and what each card can place
 *   3  money by country, by city and by industry
 *   1  the first page of rows, with a LIMIT
 */
const TIT_DASH_QUERY_BUDGET = 12;

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

    $head = $wpdb->get_row(
        "SELECT COUNT(*) total_all,
                SUM(materiality = 'routine') routine,
                SUM({$notable_sql}) notable,
                SUM(({$notable_sql}) AND confidence = 'verified') verified,
                COUNT(DISTINCT CASE WHEN {$notable_sql} THEN company_key END) companies,
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
        'counts_by_country' => array(),
        'countries' => 0,
        'by_country' => array(),
        'glance'    => array(),
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

    $facts['glance'] = tit_glance_matrix($table, $base);
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
    */
    $counts = array_column($wpdb->get_results(
        "SELECT COALESCE(country, hq_country) k, COUNT(*) n FROM {$table}
          WHERE {$base} AND COALESCE(country, hq_country) IS NOT NULL
          GROUP BY k", ARRAY_A) ?: array(), 'n', 'k');
    $counts = array_map('intval', $counts);
    $facts['counts_by_country'] = $counts;
    $facts['countries'] = count($counts);

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
        "SELECT signal_id, headline, talent_readthrough, company, company_key, pillar, signal_direction,
                city, country, hq_city, hq_country, confidence, source_url, source_name,
                archive_url, published_date
           FROM {$table} WHERE {$base}
          ORDER BY CASE materiality WHEN 'high' THEN 0 WHEN 'medium' THEN 1
                                    WHEN 'routine' THEN 3 ELSE 2 END ASC,
                   {$date_expr} DESC, row_id DESC
          LIMIT " . TIT_DASH_ROWS,
        ARRAY_A
    ) ?: array();

    /*
      TOP CITIES, counted under the clause the pill actually filters by.

      Three things were wrong with this one query, and each of them put a number
      on the page that the page itself contradicted one click later.

      1. It grouped by bare `city` while the pill writes `city=<name>`, which
         api.php resolves as `city = %s OR (city IS NULL AND hq_city = %s)`. So
         the London pill read 18 and returned 1,338: almost every London row is
         placed by its employer's head office, and this count could not see one
         of them. Manchester (108) and Edinburgh (49) were absent from a strip
         that carried Seattle (42) and Toronto (25). It groups by
         tit_city_expr() now, which is the same rule the filter uses.

      2. It was the ONE strip on this page counted under a bare `is_current = 1`
         instead of {$base}, so it silently included the routine officer filings
         every other figure in the hero sets aside. A pill counting a set the
         table is not showing is the same defect as the first, from the other
         direction.

      3. `cc` was a non-aggregated column under GROUP BY city, so the flag was
         whichever row the engine happened to reach first -- and MySQL and SQLite
         need not agree. Toronto (22 Canadian rows, 2 American) flew a US flag.
         It is now the MODAL country for that city, ties broken alphabetically,
         so it is deterministic and it is the answer a reader would give.

      Still one query. The scalar subquery runs once per pill, ten times, on a
      render that is cached for TIT_CACHE_TTL.
    */
    $city_expr    = function_exists('tit_city_expr') ? tit_city_expr() : 'COALESCE(city, hq_city)';
    $country_expr = function_exists('tit_country_expr') ? tit_country_expr() : 'COALESCE(country, hq_country)';
    $facts['cities'] = $wpdb->get_results(
        "SELECT c.k, c.n,
                (SELECT {$country_expr} FROM {$table}
                  WHERE {$base} AND {$city_expr} = c.k AND {$country_expr} IS NOT NULL
                  GROUP BY {$country_expr}
                  ORDER BY COUNT(*) DESC, {$country_expr} ASC LIMIT 1) cc
           FROM (SELECT {$city_expr} k, COUNT(*) n FROM {$table}
                  WHERE {$base} AND {$city_expr} IS NOT NULL AND {$city_expr} <> ''
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
            <h2>No signals published yet</h2>
            <p>Collection has not been switched on. Every record here has to
               link to the article that makes the claim, and that sourcing is
               still being proven out, so an empty table is the honest state
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
    $by_direction     = $facts['by_direction'];
    $counts_by_country = $facts['counts_by_country'];
    $countries        = (int) $facts['countries'];
    $by_country       = $facts['by_country'];
    $glance           = $facts['glance'];
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
    $directions = array(
        /*
          "Adding Roles", not "Hiring up". The owner asked what "hiring up"
          meant, and it is a fair question about a phrase nobody says: "up"
          is doing the work of "the source told us headcount is going up",
          which a reader has to reverse-engineer. "Adding Roles" is the thing
          itself. "Cutting Roles" is its opposite in the same shape, where
          "Cutting back" could have meant costs, hours or investment.
          Stored values (hiring, displacement, comp_shift, neutral) unchanged.
        */
        'hiring'       => 'Adding Roles',
        'displacement' => 'Cutting Roles',
        'comp_shift'   => 'Pay Change',
        // "Other change" told the reader nothing: it is the bucket for updates
        // whose source says nothing about headcount at all (a funding round
        // with no hiring plan, a CEO succession). Naming that plainly is both
        // clearer and truer to the rule that we never infer a direction the
        // source did not state.
        'neutral'      => 'Headcount Not Stated',
    );
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
         data-states="<?php echo esc_attr(wp_json_encode(tit_state_names())); ?>">

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
          <div class="tit-hero-head">
            <h2>Know who's hiring before the job ad appears</h2>
            <p class="tit-hero-sub">Every update links to the filing or report behind it</p>
          </div>
          <?php /* The sibling's freshness shape: the absolute timestamp WITH
                   its timezone is the primary fact ("Live · updated Jul 28,
                   1:51 AM EDT"); the relative time and the next-run note live
                   on Roo's quieter line below. */ ?>
          <div class="tit-live"><span class="tit-live-dot"></span>
            Live<?php if ($newest_run) : ?> · updated
            <?php echo esc_html(tit_local_datetime($newest_run)); ?>
            <?php endif; ?>
          </div>
        </div>

        <div class="tit-roo-row"><?php tit_roo($newest_run); ?></div>

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
        $tit_regions = tit_regions($counts_by_country);
        $tit_top = tit_top_countries($counts_by_country);
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
          <?php if ($tit_top) : ?>
            <div class="tit-countries" role="group" aria-label="Filter by country">
              <span class="tit-countries-label">Top Countries</span>
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
              <span class="tit-countries-label">Top Cities</span>
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
          THE DATED PANEL SITS ABOVE THE CROSS-TAB, and the two are the same
          facts at two resolutions. The panel answers "what moved, and when",
          which is the question somebody opens a tracker with; the matrix
          answers "what kind, and is it accelerating", which is the question
          they have once the first is answered. Week, month and year share their
          boundaries with the matrix rows below, so a reader who checks one
          against the other finds them equal.
        */
        ?>
        <div class="tit-dg-box" id="tit-dg-box">
          <?php echo tit_dated_glance_html($glance['dated'] ?? array(), $money['coverage'] ?? null); ?>
        </div>

        <div class="tit-glance" id="tit-glance">
          <?php echo tit_glance_matrix_html($glance); ?>
        </div>
        <?php $span_note = tit_span_note($view_lo, $view_hi); ?>
        <?php if ($span_note) : ?>
          <p class="tit-span" id="tit-span"><?php echo esc_html($span_note); ?></p>
        <?php endif; ?>

        <p class="tit-hero-fine">
          <?php
          /*
            THE WIDEST RUNG OF THE SAME LADDER, and it is labelled now.

            This line is the old undated lump. It was not deleted, because it
            answers a real question — how much is in here altogether — and the
            meta description is built from the same three figures. What was
            wrong is that it answered that question in the position where a
            reader expects "what has moved", with nothing saying which of the
            two it was. Labelled and placed under the dated rows it is the
            bottom rung: today, this week, this month, this year, everything.

            The label is OUTSIDE .tit-fine-figures on purpose. dashboard.js
            rewrites that span's innerHTML on every filter change, so a label
            inside it would survive exactly until the reader touched a control.
          */
          ?>
          <span class="tit-dg-label tit-dg-label-static">Everything We Hold</span>
          <span class="tit-fine-figures"><?php
            /* Money sits WITH the other headline figures, not trailing after
               the sentence. It is still a link, because it is a sum of dollars
               among counts and only honest beside the coverage line the money
               section prints; both read the same aggregate, so the figure and
               its caveat cannot drift apart. */
            $bits = array(
                esc_html(sprintf(_n('%s update', '%s updates', $total, 'tit'), number_format_i18n($total))),
                esc_html(sprintf(_n('%s employer', '%s employers', $companies, 'tit'), number_format_i18n($companies))),
                esc_html(sprintf(_n('%s country', '%s countries', $countries, 'tit'), number_format_i18n($countries))),
            );
            if ($money['total'] > 0) {
                $bits[] = sprintf(
                    '<a class="tit-fine-money" href="#chart-money-country" title="%s">%s raised</a>',
                    esc_attr(tit_money_full($money['total']) . '. '
                             . tit_money_coverage_sentence($money['coverage'])),
                    esc_html(tit_money_short($money['total']))
                );
            }
            $bits[] = esc_html(number_format_i18n($verified)) . ' from official filings';
            echo implode(' · ', $bits);
          ?></span>
          <?php /* Two beats, not one run-on line: the figures, then the promise.
                   They were competing, and the promise is the more important of
                   the two. The links move to a line of their own for the same
                   reason: three different jobs in one paragraph means none of
                   them lands. */ ?>
          <?php /* The linking half of this promise is now the hero sub-line
                   directly above, so saying it twice on one screen would make
                   both readings weaker. What is left is the half the hero does
                   not carry. */ ?>
          <span class="tit-fine-say">No figure appears unless its source states it.</span>
        </p>
        <p class="tit-hero-links">
          <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">Every source</a>
          · Every employer name below links to that employer's own page
          <?php /* The misses belong next to the sources, not buried in a
                   methodology footnote. A tracker that publishes what it fails
                   to catch is making a checkable claim; one that only lists its
                   sources is not. */ ?>
          · <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/recall/')); ?>">What we miss, measured</a>
          <?php /* The place pages are reachable from here and from each other,
                   and from nowhere else: these routes are in no theme menu, and
                   a set of pages a crawler can only find through a sitemap gets
                   crawled slowly and trusted less. One link, in the fine print,
                   next to the other two pages that exist to be checked. */ ?>
          · <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/places/')); ?>">Every country, city and industry we cover</a>
          · <a href="/blog/ai-layoff-tracker/">Layoffs are tracked separately</a>
        </p>
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

      <?php /* The market read comes BEFORE the filter machinery. These three
               charts are the ten-second answer a reader arrived for; the
               controls below them are how to interrogate it. The rows are
               still click-to-filter and still write the same hidden selects,
               so the order of sections changes nothing about what a click
               means. The heading is the design proposal's, kept because it
               names what the reader gets rather than what the section is
               made of. */ ?>
      <div class="tit-sec" id="tit-filter-sec">
        <h3>Narrow It Down</h3>
        <p>Everything below follows these filters, including the charts.</p>
      </div>

      <div class="tit-quick" role="group" aria-label="Quick views">
        <span class="tit-quick-label">Quick Views</span>
        <?php foreach ($quick_views as $spec => $label) : ?>
          <button type="button" class="tit-qv" data-qv="<?php echo esc_attr($spec); ?>"><?php
            echo esc_html($label);
            /* The one quick view whose set is small enough that its size is
               part of what it means. See the note beside $quick_views. */
            if ($spec === 'stated_headcount=1') : ?><span class="tit-qv-n"
              id="tit-stated-n"><?php echo esc_html('(' . number_format_i18n($n_stated) . ')');
            ?></span><?php endif; ?></button>
        <?php endforeach; ?>
        <span class="tit-quick-hint">For a time period, tap a number in the signal table above.</span>
      </div>

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
        the page.
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
        <div class="tit-filterbar" id="tit-panel" aria-labelledby="tit-panel-t">
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
                    aria-expanded="false" aria-controls="tit-panel-body" hidden>
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
                  also becomes its own chip above the table, so you can drop one
                  without clearing the rest.</p>
                <p id="tit-help-basis"><strong>Where.</strong> Places come from what a
                  source named. When a source names no place we use the employer's head
                  office instead, so a company known only by its headquarters still
                  appears. Tick "Only Countries A Source Named" to leave those out.</p>
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
      <div class="tit-active" id="tit-active" hidden>
        <span class="tit-active-label">Filtering</span>
        <span class="tit-active-chips" id="tit-active-chips"></span>
      </div>

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
                 they are already reading. Column headers below do the same job
                 for the columns they name; this covers the orderings that are
                 not a column, like "most useful" and "biggest raises". */ ?>
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
            <?php /* Sorting on money only works because funding_amount_usd is
                     a number; the display string beside it cannot be ordered. */ ?>
            <option value="raised">Biggest Raises First</option>
          </select>
        </label>
      </div>

      <?php /* With the charts above rather than below, this is where the
               machinery begins, and a section marker has to say so or the
               quick views read as a fourth chart. The id is the jump bar's
               scroll target on phones. */ ?>
      <div class="tit-table-scroll">
        <table class="tit-table">
          <?php
          /*
            Sortable columns. Each one drives the SERVER sort through /query, so
            it orders the whole filtered set and not the fifty rows that happen
            to be on screen; a header that reordered only the visible page would
            be a sort that lies about its own scope. The state rides on the same
            `sort` parameter as the select above, so it round-trips through the
            URL and the exports like everything else, and aria-sort carries it
            for anyone not looking at the arrow.
          */
          $sortable = array(
              'Employer' => 'employer',
              'Where'    => 'place',
              'Evidence' => 'evidence',
              'When'     => 'when',
          );
          $columns = array('Employer', 'What happened', 'Where',
                           'What it means', 'Evidence', 'When', 'Source');
          ?>
          <thead>
            <tr>
              <?php foreach ($columns as $col) :
                if (!isset($sortable[$col])) : ?>
                  <th scope="col"><?php echo esc_html($col); ?></th>
                <?php else : ?>
                  <th scope="col" class="tit-th-sort" aria-sort="none"
                      data-col="<?php echo esc_attr($sortable[$col]); ?>">
                    <button type="button"><?php echo esc_html($col); ?><span
                      class="tit-th-arrow" aria-hidden="true"></span></button>
                  </th>
                <?php endif;
              endforeach; ?>
            </tr>
          </thead>
          <tbody id="tit-rows">
            <?php foreach ($rows as $r) : ?>
              <tr>
                <td class="tit-eyebrow" data-label="Employer"><?php
                  $ck = $r['company_key'] ?? '';
                  if ($ck && function_exists('tit_company_url')) {
                      printf('<a href="%s">%s</a>', esc_url(tit_company_url($ck)), esc_html($r['company']));
                  } else {
                      echo esc_html($r['company']);
                  }
                ?></td>
                <td class="tit-headline" data-label="What happened">
                  <span class="tit-h"><?php echo esc_html($r['headline']); ?></span>
                  <span class="tit-rt"><?php echo esc_html($r['talent_readthrough']); ?></span>
                </td>
                <td class="tit-meta" data-label="Where">
                  <?php
                  $place = $r['city'] ?: $r['hq_city'];
                  $cc    = $r['country'] ?: $r['hq_country'];
                  $is_hq = !$r['city'] && !$r['country'];
                  $where = trim(($place ? $place . ', ' : '') . tit_country_name($cc), ', ');
                  if ($where === '') {
                      // Stored anyway: geography is how we segment, not what
                      // makes the record true. Saying so beats a blank cell.
                      echo '<span class="tit-nowhere">Location not stated</span>';
                  } else {
                      echo esc_html($where);
                      if ($is_hq) echo ' <span class="tit-hq" title="Employer headquarters, not a location named in the source">HQ</span>';
                  }
                  ?>
                </td>
                <td class="tit-meta" data-label="What it means"><span class="tit-tag tit-<?php echo esc_attr($r['signal_direction']); ?>"><?php echo esc_html($directions[$r['signal_direction']] ?? $r['signal_direction']); ?></span></td>
                <td class="tit-meta" data-label="Evidence"><span class="tit-conf tit-c-<?php echo esc_attr($r['confidence']); ?>"><?php
                  echo esc_html($confidences[$r['confidence']] ?? $r['confidence']); ?></span></td>
                <td class="tit-meta tit-when" data-label="When"><?php
                  $when = $r['published_date'] ?: '';
                  echo $when ? esc_html(date_i18n('j M Y', strtotime($when)))
                             : '<span class="tit-nowhere">Date not stated</span>';
                ?></td>
                <td class="tit-meta" data-label="Source"><a href="<?php echo esc_url($r['source_url']); ?>" rel="nofollow noopener" target="_blank"><?php echo esc_html($r['source_name']); ?></a><?php
                  // The fallback, and only ever a SECOND link. Publishers
                  // unpublish, rewrite their URL schemes and let domains lapse,
                  // and when that happens a sourced claim silently becomes an
                  // unsourced one. A neutral third-party snapshot keeps the
                  // evidence reachable. The publisher's own copy is the
                  // citation and stays the citation; this never replaces it.
                  //
                  // Printed ONLY where a snapshot exists. Never a placeholder
                  // and never a disabled control: on a page whose whole claim
                  // is that every figure still links to its document, a link
                  // offered and then not there is worse than no link.
                  //
                  // The separator is not in this markup. It is a CSS ::before,
                  // because below 860px each row is a card and this cell shares
                  // one wrapping line with the rest of the meta; a literal
                  // middot that wraps lands at the START of the new line and
                  // reads as a bullet whose text went missing. See the
                  // .tit-archived rules in dashboard.css.
                  if (!empty($r['archive_url'])): ?><span class="tit-archived"><a href="<?php echo esc_url($r['archive_url']); ?>" rel="nofollow noopener" target="_blank" title="Archived copy at the Internet Archive">Archived</a></span><?php endif; ?></td>
              </tr>
            <?php endforeach; ?>
          </tbody>
        </table>
      </div>

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
        <span class="tit-export-note">Every matching update, not just this page,
          and routine filings are included whichever way Show is set. Each row
          carries its own materiality, so you can set them aside yourself.
          Free to reuse, CC BY 4.0.</span>
      </div>

        </div><!-- /.tit-results -->
      </div><!-- /.tit-feed -->

      <?php /* The charts read the SAME filtered set as the rows above them and
               are still click-to-filter, so they belong after the feed rather
               than wedged between the controls and the table they describe.
               That is also where the design mock puts them. Nothing about what
               a click means has changed: every row still writes the same
               hidden select. */ ?>
      <div class="tit-sec">
        <h3>What The Data Says</h3>
        <p>Click any row to narrow the whole page to it.</p>
      </div>

      <div class="tit-charts">
      <div class="tit-chart" id="chart-kind">
        <?php /* Headings name what a recruiter or job seeker GETS from the chart,
                 not what the chart is made of. "What kind of update" described
                 the axis; "What is moving" answers the question they opened the
                 page with. The rows are buttons because they ARE filters:
                 dashboard.js routes a click through the same state as the
                 dropdowns, so the subtitle may promise it. Buttons hold span
                 children only (phrasing content), never divs. */ ?>
        <?php tit_chart_head('What Is Moving', 'Ranked by how much of it we are seeing.', 'kind'); ?>
      <div class="tit-pillars">
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
        <div class="tit-chart" id="chart-place">
          <?php tit_chart_head('Where the Jobs Are', "Counted where the work sits; head office stands in when no place is named.", 'place'); ?>
          <p class="tit-chart-caveat" id="tit-place-caveat"<?php
            echo $place_caveat === '' ? ' hidden' : ''; ?>><?php
            echo esc_html($place_caveat); ?></p>
          <div class="tit-rank" tabindex="0" role="group" aria-label="Activity by place">
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

        <div class="tit-chart" id="chart-direction">
          <?php tit_chart_head('Which Way Headcount Is Going', 'What the source itself says. Most updates say nothing about headcount, and those are counted as such rather than guessed.', 'direction'); ?>
          <div class="tit-rank" tabindex="0" role="group" aria-label="Activity by direction">
            <?php
            $dmax = $by_direction ? max(array_map('intval', array_column($by_direction, 'n'))) : 1;
            foreach ($by_direction as $d) : ?>
              <button type="button" class="tit-rank-row" data-k="<?php echo esc_attr($d['k']); ?>"
                      data-dir="<?php echo esc_attr($d['k']); ?>" aria-pressed="false">
                <span class="tit-rank-name"><?php echo esc_html($directions[$d['k']] ?? $d['k']); ?></span>
                <span class="tit-rank-track"><span class="tit-rank-fill"
                  style="width:<?php echo esc_attr(max(4, round(100 * $d['n'] / $dmax))); ?>%"></span></span>
                <span class="tit-rank-n"><?php echo (int) $d['n']; ?></span>
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
      <div class="tit-charts tit-charts-money">
        <?php
        tit_money_chart(
            'country', 'Money Raised by Country',
            "Funding rounds added up, in US dollars, by country.",
            $money['by_country'], $money, 'country',
            function ($k) { return tit_country_label_html($k); }, true
        );
        tit_money_chart(
            /* The mock's own wording for this card, which is the one the owner
               named. "Where the money went" is what the deleted section heading
               used to say, so the phrase survives ON the card it describes
               instead of over a section that did not need one. */
            'city', 'Where the Money Went',
            "Funding rounds added up, in US dollars, by city.",
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


      <?php echo tit_trust_panel_html($facts); ?>

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
            'Most updates say nothing about headcount. %s of %s are labelled '
            . 'as not stated rather than inferred, and no figure appears in a '
            . 'summary unless the source states it in those words.',
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
    $faqs = array(
        array('How often does this update?',
              sprintf('Collection runs twice a day, at 06:00 and 18:00 UTC. '
                    . 'The most recent capture was %s. Figures on this page are '
                    . 'computed on request and cached for five minutes, so a '
                    . 'correction or a fresh run appears immediately rather '
                    . 'than on a schedule.',
                    $newest ? tit_local_datetime($newest) : 'not recorded yet')),
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
                    . 'signals that state no direction, and guessing one from '
                    . 'them would be our claim rather than the document\'s.',
                    $n($unstated), $n($notable))),
        array('Why are some updates hidden by default?',
              sprintf('%s of the %s records we hold are routine officer and '
                    . 'director filings: accurate, verified, and in such volume '
                    . 'that they bury everything else. The default view sets '
                    . 'them aside and the control above the table turns them '
                    . 'back on, states both counts, and says what we mean by '
                    . 'routine. Nothing is deleted.',
                    $n($routine), $n($total_all))),
        array('What do you know you are missing?',
              'We measure it rather than assert it. Every week the collectors '
              . 'are graded against a fixed set of real events assembled from '
              . 'public sources without ever looking at our own database, and '
              . 'the result is published including the countries and document '
              . 'types where we come off badly. The countries scoring zero are '
              . 'the roadmap.'),
        array('How much of this is automated?',
              'About 99%. Collection, classification, validation, deduplication '
              . 'and publishing all run without a human. Repairing a scraper '
              . 'when a site changes, judging whether a novel source is worth '
              . 'reading, and assembling each new recall test set are human, '
              . 'and the last of those is human by design: a test set built '
              . 'out of what is easy to find measures memory rather than reach.'),
        array('Can I reuse the data?',
              sprintf('Yes, under CC BY 4.0, citing the Talent Intelligence '
                    . 'Tracker. The export links above take the current view '
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
      ), JSON_UNESCAPED_SLASHES);
    ?></script>
    <?php
    return ob_get_clean();
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

      Collection runs at 06:00 and 18:00 UTC and every row carries the SOURCE's
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

    // key, reader-facing label, the filter a cell click applies, SQL condition,
    // and what the cells hold: a COUNT of updates, or a SUM of dollars.
    //
    // "Money raised" is the one row that is not a count, which is exactly why
    // it is labelled, prefixed and coloured as money everywhere it appears. A
    // reader who mistakes a dollar sum for a number of updates has been misled
    // by the table, not by their own carelessness.
    $funding = function_exists('tit_funding_where')
        ? tit_funding_where()
        : "((funding_amount IS NOT NULL AND funding_amount <> '')"
          . " OR (funding_stage IS NOT NULL AND funding_stage <> ''))";

    /*
      THE ROW LABELS ARE THE PAGE'S ONE VOCABULARY. See the note beside
      $labels in tit_dashboard_html(); these five are the same words the charts
      use, which they were not before.

      Two of them earned more than a case change.

      "Total Raised" was "Money raised", sitting in a column of rows that count
      updates while it alone sums dollars. That mismatch is why the block below
      needs a paragraph to explain itself, and a label that forces an
      explanation is the wrong label. "Total" says sum, and the unit rides on
      the row as it always did.

      "Everything in This View" was "All updates", which a reader could not tell
      included the 3,143 routine filings the page hides by default. It does not:
      every figure in this table sits under the same notable clause as the rows,
      and "in this view" is the only phrase that says so without a footnote.
    */
    $defs = array(
        array('hiring',     'Adding Roles',      'direction=hiring',         "signal_direction = 'hiring'", 'count'),
        array('funded',     'Funding Rounds',    'funding=1',                $funding,                      'count'),
        array('money',      'Total Raised',      'funding=1',                '',                            'money'),
        array('leadership', 'Leadership Moves',  'pillar=leadership_change', "pillar = 'leadership_change'", 'count'),
        array('pay',        'Pay and Benefits',  'pillar=rewards_comp',      "pillar = 'rewards_comp'", 'count'),
        array('total',      'Everything in This View', '',                    '1 = 1',                      'count'),
    );

    $date_expr = 'COALESCE(published_date, DATE(captured_at))';
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
      THE DATED GLANCE PANEL RIDES ON THIS SAME SCAN, and that is a correctness
      decision before it is a budget one.

      The panel and the matrix describe the SAME four windows over the SAME
      rows. Computed separately they could disagree — a panel saying "this week,
      1,204 updates" above a matrix cell saying 1,198 for the same week is the
      exact failure the hero figures were consolidated to prevent, and it is
      invisible until a reader adds them up. Sharing one statement makes
      disagreement impossible rather than unlikely.

      It is also what keeps TIT_DASH_QUERY_BUDGET at 12. Four time buckets are
      four SETS OF COLUMNS here, never four round trips; the N+1 tripwire in
      tests/php/render_dashboard.php re-checks the count after five thousand more
      rows land, so a future session that gives one bucket its own query fails
      there rather than on the live site under a crawl.

      THE BUCKETS ARE NOT THE MATRIX'S. The matrix runs week / month / quarter /
      YTD; the panel runs today / week / month / year, which is the ladder a
      reader reads down. Week, month and year share their boundary expressions
      with the matrix, so those three rows equal the matrix's own "Everything in
      This View" cells exactly.

      TODAY IS COMPUTED AND USUALLY ABSENT, deliberately. This tracker measured
      it once already: every row carries the SOURCE's reporting date rather than
      our capture time, and collection runs twice a day, so "today" reads zero
      for most of most days. A permanent zero row does not report a quiet day, it
      teaches a reader the tracker is dead — which is why the matrix has no Today
      column at all. The panel computes it and prints the row ONLY when it holds
      something, so it is the freshest line on the page on a day with news and
      simply is not there on a day without.
    */
    $dg_year   = date('Y', strtotime($today));
    $dg_week   = date('Y-m-d', strtotime($today . ' -6 days'));
    $dg_prev   = date('Y-m-d', strtotime($today . ' -13 days'));
    $dg_periods = array(
        array('today', 'Today',                 $today),
        array('week',  'This week',             $dg_week),
        array('month', 'This month',            date('Y-m-01', strtotime($today))),
        // The year label is DERIVED, so this becomes "2027 so far" on 1 January
        // without anybody remembering to change it. corrections.php once shipped
        // a typed "$124.0bn" labelled "measured now"; a typed year is the same
        // mistake with a slower fuse.
        array('year',  $dg_year . ' so far',    date('Y-01-01', strtotime($today))),
    );

    foreach ($dg_periods as $gi => $g) {
        $select[] = "SUM({$date_expr} >= %s) AS g_n_{$gi}";
        $select_params[] = $g[2];
        $select[] = "COUNT(DISTINCT CASE WHEN {$date_expr} >= %s THEN company_key END) AS g_e_{$gi}";
        $select_params[] = $g[2];
        $select[] = "SUM(confidence = 'verified' AND {$date_expr} >= %s) AS g_v_{$gi}";
        $select_params[] = $g[2];
        $select[] = "COALESCE(SUM(CASE WHEN {$date_expr} >= %s THEN funding_amount_usd END), 0) AS g_m_{$gi}";
        $select_params[] = $g[2];
    }

    /*
      THE LARGEST RAISE IN EACH WINDOW, as two scalar subqueries per bucket.

      An aggregate can return the largest AMOUNT in one expression; it cannot
      return the employer that raised it, and SQL has no portable argmax. The
      tricks that fake one (bare columns beside MAX(), packing the amount and the
      name into a sortable string) are engine-specific: SQLite defines the first,
      MySQL does not, and the string form needs a different concat operator in
      each. This harness runs SQLite and production runs MySQL, so anything that
      behaves differently between them is a bug that ships green.

      Scalar subqueries are standard in both, they are constant with respect to
      the outer aggregate, and they stay inside ONE statement. The same shape the
      top-cities strip above already uses, for the same reason. Each one is
      narrowed to `funding_amount_usd IS NOT NULL`, which on the live table is a
      small minority of rows.

      row_id ASC breaks a tie deterministically. Two rounds of the same size
      would otherwise be resolved by whichever the engine reached first, and
      MySQL and SQLite need not agree — the same defect the city flags had.
    */
    foreach ($dg_periods as $gi => $g) {
        foreach (array('company' => "g_lc_{$gi}", 'funding_amount_usd' => "g_la_{$gi}") as $col => $alias) {
            $select[] = "(SELECT {$col} FROM {$table} WHERE {$where}"
                      . " AND funding_amount_usd IS NOT NULL AND {$date_expr} >= %s"
                      . " ORDER BY funding_amount_usd DESC, row_id ASC LIMIT 1) AS {$alias}";
            // Placeholder order inside the subquery is the WHERE clause's own
            // params first, then this bucket's start date. Getting this pair the
            // wrong way round binds a date into a country clause and the whole
            // panel silently reads zero, so it is built in emission order rather
            // than assembled at the end.
            $select_params = array_merge($select_params, $params);
            $select_params[] = $g[2];
        }
    }

    /*
      THE WEEK BEFORE, AND HOW FAR BACK THIS VIEW ACTUALLY GOES.

      Both exist to decide whether a week-over-week comparison may be printed at
      all. See tit_dated_glance_html(): the news collectors here first ran on
      27 July 2026, so a comparison drawn today would divide a populated week by
      an almost empty one and print a percentage in the thousands. g_lo is the
      earliest date IN THIS VIEW rather than in the table, so the rule holds
      under a filter that narrows to a young collector too.
    */
    $select[] = "SUM({$date_expr} >= %s AND {$date_expr} < %s) AS g_prev_n";
    $select_params[] = $dg_prev;
    $select_params[] = $dg_week;
    $select[] = "MIN({$date_expr}) AS g_lo";

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

    $dated = array('rows' => array(), 'prev_n' => (int) ($row['g_prev_n'] ?? 0),
                   'week_start' => $dg_week, 'prev_start' => $dg_prev,
                   'history_lo' => (string) ($row['g_lo'] ?? ''),
                   'today' => $today,
                   // Formatted server-side and carried, so the panel's heading
                   // says the same date whether the server or dashboard.js
                   // painted it. Formatting it again in the browser would read
                   // the READER's clock, and a reader in Auckland would be shown
                   // a heading a day ahead of the buckets underneath it.
                   'today_label' => date_i18n('M j', strtotime($today . ' 00:00:00 UTC')));
    foreach ($dg_periods as $gi => $g) {
        $dated['rows'][] = array(
            'key'    => $g[0],
            'label'  => $g[1],
            'since'  => $g[2],
            'n'      => (int) ($row["g_n_{$gi}"] ?? 0),
            'e'      => (int) ($row["g_e_{$gi}"] ?? 0),
            'v'      => (int) ($row["g_v_{$gi}"] ?? 0),
            'money'  => (float) ($row["g_m_{$gi}"] ?? 0),
            'top'    => (string) ($row["g_lc_{$gi}"] ?? ''),
            'top_usd' => (float) ($row["g_la_{$gi}"] ?? 0),
        );
    }

    return array(
        'periods' => array_column($periods, 0),
        'starts'  => array_column($periods, 1),
        'rows'    => $rows,
        'dated'   => $dated,
    );
}

/**
 * THE DATED GLANCE PANEL: what happened today, this week, this month, this year.
 *
 * The hero used to open with one undated lump — "12,566 updates · 5,542
 * employers · 51 countries · $101B raised · 7,573 from official filings" — which
 * answers "how big is this dataset" and never answers "what has moved". A reader
 * arriving at a tracker wants the second question, and every figure in that line
 * is as true in March as it is today, so nothing on the first screen told anyone
 * whether the thing was still running.
 *
 * The shape is the sibling AI Layoff Tracker's, and the FACTS ARE NOT. Layoffs
 * are not collected here — they are read from the sibling's public API at render
 * time, one source of truth per fact — so "workers" and "verified layoffs" have
 * no meaning on this page. The equivalents are what this tracker actually holds:
 * updates, the employers behind them, dollars raised, and how many came straight
 * from an official filing. "Largest" is the largest single raise, which is the
 * only superlative this dataset can support without inventing a ranking.
 *
 * NO FIGURE HERE IS TYPED. Every number comes off tit_glance_matrix()'s single
 * scan, the year label is derived from the current date, and the money row
 * carries the same coverage sentence the money charts do. corrections.php once
 * shipped a hardcoded "$124.0bn" under the caption "Measured now" while the live
 * figure was $101B; a panel of headline numbers is the worst possible place to
 * repeat that.
 */
function tit_dated_glance_html(array $dated, $coverage = null) {
    $rows = $dated['rows'] ?? array();
    if (!$rows) return '';

    /*
      THE WEEK-OVER-WEEK COMPARISON, AND WHY IT IS USUALLY ABSENT.

      The sibling prints "down 25% vs the week before" because it holds years.
      This tracker's news collectors first ran on 2026-07-27 and national_press
      on 2026-07-29, so the week before the current one is not a quiet week — it
      is a week that mostly predates the collector. Dividing by it yields
      something like "up 4,000%", which is not an exaggeration of a real change;
      it is an artefact of the corpus start date wearing a statistic's clothes,
      and it would be the single most quotable number on the page.

      The rule is therefore about HISTORY and not about size: the comparison is
      printed only when this view holds data from on or before the start of the
      period being compared against. That is measured per view, so it also holds
      when a filter narrows the page to a collector younger than the tracker, and
      it turns itself on — with no code change and no deploy — on the first day
      the corpus genuinely spans both weeks.

      When it is absent the panel SAYS SO in a few words. Silently omitting it
      would leave a reader unable to tell "flat" from "we cannot say yet", and
      the second is the honest answer.
    */
    $lo   = (string) ($dated['history_lo'] ?? '');
    $prev = (int) ($dated['prev_n'] ?? 0);
    $prev_start = (string) ($dated['prev_start'] ?? '');
    $have_history = ($lo !== '' && $prev_start !== '' && $lo <= $prev_start);

    ob_start(); ?>
    <div class="tit-dg" id="tit-dg">
      <div class="tit-dg-head">
        <?php /* The date is the panel's subject, so it leads. Computed from the
                 same clock the buckets are, or the heading could name a day the
                 rows below it do not describe. */ ?>
        <h3 class="tit-dg-title">Today, <?php
          echo esc_html((string) ($dated['today_label'] ?? ''));
        ?> <span aria-hidden="true">·</span> Sourced Talent Signals Worldwide</h3>
        <?php
        /*
          COPY AS POST, and it copies WHAT IS ON SCREEN.

          The sibling's version of this button is scoped only by the region tab
          and ignores every other filter, so a reader who had narrowed the page
          to one country could copy a worldwide total under it. That is a
          figure-out-of-context bug with our own byline on it, and it is the one
          reason this button was nearly not built at all.

          It is honest here because it reads the rendered rows out of the DOM at
          click time rather than rebuilding them from an unfiltered aggregate,
          and because this panel repaints from /aggregate under the active
          filters like every other figure on the page. It also appends the active
          filters by name and a link back, so a pasted summary carries the view
          it describes. See the handler in dashboard.js.

          Rendered with `hidden`, and dashboard.js removes that. A button whose
          whole function is navigator.clipboard is a dead control with no
          JavaScript, and a dead control is worse than an absent one.
        */
        ?>
        <button type="button" class="tit-dg-copy" id="tit-dg-copy" hidden>Copy as Post</button>
      </div>
      <?php foreach ($rows as $r) :
        // TODAY IS PRINTED ONLY WHEN IT HOLDS SOMETHING. See the note in
        // tit_glance_matrix(): a row that reads zero for most of most days
        // teaches a reader the tracker is dead rather than that the day is quiet.
        if ($r['key'] === 'today' && (int) $r['n'] === 0) continue;
        $bits = array();
        $bits[] = '<b>' . esc_html(number_format_i18n((int) $r['n'])) . '</b> '
                . esc_html($r['n'] == 1 ? 'update' : 'updates');
        if ((int) $r['e'] > 0) {
            $bits[] = '<b>' . esc_html(number_format_i18n((int) $r['e'])) . '</b> '
                    . esc_html($r['e'] == 1 ? 'employer' : 'employers');
        }
        if ((float) $r['money'] > 0) {
            $bits[] = '<b>' . esc_html(tit_money_short($r['money'])) . '</b> raised';
        }
        if ((int) $r['v'] > 0) {
            $bits[] = '<b>' . esc_html(number_format_i18n((int) $r['v'])) . '</b> from official filings';
        }
        // The largest raise names its employer, because "largest: $8.6B" with no
        // name is a number a reader cannot check and this page's whole promise
        // is that they can.
        if ($r['top'] !== '' && (float) $r['top_usd'] > 0) {
            $bits[] = 'largest: <b>' . esc_html($r['top']) . '</b> ('
                    . esc_html(tit_money_short($r['top_usd'])) . ')';
        }
        $note = '';
        if ($r['key'] === 'week') {
            if ($have_history && $prev > 0 && (int) $r['n'] > 0) {
                $delta = (int) round(100 * ((int) $r['n'] - $prev) / $prev);
                $note = ($delta >= 0 ? 'up ' : 'down ') . '<b>' . abs($delta)
                      . '%</b> vs the week before';
            } else {
                // Named plainly, and the reason is the corpus rather than the
                // week. A reader who is told "no comparison yet" learns nothing;
                // one who is told we do not hold the earlier week can judge it.
                $note = '<span class="tit-dg-nocmp">no week-on-week change yet: '
                      . 'we do not hold a full week before this one</span>';
            }
        }
        if ($note !== '') $bits[] = $note;
        ?>
        <div class="tit-dg-row" data-dg="<?php echo esc_attr($r['key']); ?>">
          <?php /* A period label is a filter this page can apply, so it is a
                   button and not a caption. data-since is the same attribute the
                   matrix cells carry, so one handler drives both. */ ?>
          <button type="button" class="tit-dg-label" data-since="<?php echo esc_attr($r['since']); ?>"
                  aria-pressed="false"><?php echo esc_html($r['label']); ?></button>
          <span class="tit-dg-body"><?php echo implode(' <span aria-hidden="true">·</span> ', $bits); ?></span>
        </div>
      <?php endforeach; ?>
      <?php
      // The money coverage sentence travels with any dollar figure on this page,
      // without exception. A total presented as though it covered every round is
      // the plausible-but-wrong number this product cannot carry.
      $cov = tit_money_coverage_sentence($coverage);
      if ($cov !== '') : ?>
        <p class="tit-dg-cov"><?php echo esc_html($cov); ?></p>
      <?php endif; ?>
    </div>
    <?php
    return ob_get_clean();
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
            <th scope="col"><span class="tit-sr">Signal</span></th>
            <?php foreach ($m['periods'] as $p) : ?>
              <th scope="col"><?php echo esc_html($p); ?></th>
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
      ONE DISCLOSURE, OPEN BY DEFAULT, AND NOT A WORD CUT.

      These three paragraphs are honesty surfaces: what the columns actually
      count, that the rows overlap so the columns do not add up, and that one row
      sums dollars while every other counts updates. On a 390px screen they were
      about fifteen lines of prose sitting between the reader and any content,
      which is how a true statement ends up unread. The owner said the phone
      experience was bad, and this was most of the reason.

      `open` is in the MARKUP, and the stylesheet is the only thing that takes it
      away, below 860px. So desktop needs no script, a crawler sees every word in
      the initial HTML, and a reader with CSS or JavaScript disabled gets three
      open paragraphs rather than a collapsed control they cannot open. Nothing
      here is fetched or injected.
    */
    ?>
    <details class="tit-matrix-note" open>
      <summary>How To Read This</summary>
      <?php
      /*
        ONE IDEA PER LINE. The owner read the old two paragraphs and said
        "this make s not sentds", and they were right: seven separate ideas were
        packed into two blocks of prose, so finding the one you needed meant
        parsing all of them. A list of facts is a list, and now looks like one.

        NOT ONE FACT IS CUT, and every figure is still computed. The page carries
        three different totals for three different questions and each was
        correct while none said what it counted; that is why the first two lines
        exist and they are unchanged in substance.

        The money line got SHORTER because the LABEL got better. It used to open
        "Money raised is the exception", which is a sentence a table needs only
        when one of its rows is lying about its unit. The row is called
        "Total Raised" now and carries "sum of dollars" on itself, so the line
        states the contrast once and hands the rest to the coverage sentence.
      */
      ?>
      <ul class="tit-matrix-points">
        <li>Each column counts updates whose source dated them inside that window.</li>
        <li>The figures above the table count everything in this view, over the
            whole period we hold, which is why they are larger.</li>
        <li>Colour shows relative activity within each row.</li>
        <li>Rows overlap, so the columns do not add up. A funded employer may
            also be hiring.</li>
        <li><strong>Tap any number to filter the page.</strong></li>
        <li class="tit-matrix-money-note">Total Raised sums dollars. Every other
            row counts updates.
            <?php echo esc_html(tit_money_coverage_sentence($m['coverage'] ?? null)); ?></li>
      </ul>
    </details>
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
function tit_money_aggregate($table, $where = 'is_current = 1', array $params = array(), $limit = 40) {
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
        'by_country'  => $by($country_expr),
        'by_city'     => $by($city_expr),
        'by_industry' => $by('industry'),
    );
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

    return $lead . '; amounts in other currencies are left out rather than'
         . ' converted at a rate nobody published.';
}

/** The same sentence, plus what this particular chart cannot place. */
function tit_money_coverage_note(array $money, $dimension = '') {
    $note = tit_money_coverage_sentence($money['coverage'] ?? null);
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
      <?php tit_chart_head($title, $sub, 'money-' . $id); ?>
      <div class="tit-rank" tabindex="0" role="group" aria-label="<?php echo esc_attr($title); ?>">
        <?php if (!$rows) : ?>
          <p class="tit-rank-empty">No US dollar amounts in this view yet.</p>
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
      <p class="tit-money-note"><?php echo esc_html(tit_money_coverage_note($money, $dimension)); ?></p>
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
 * When the next collection run is due.
 *
 * "Resting until the next run" invites the obvious question and then does not
 * answer it. The schedule is a cron in collect.yml, so the times are fixed and
 * known: 06:00 and 18:00 UTC. Printed in the site's own timezone, because a
 * reader should not have to convert.
 */
function tit_next_run() {
    $now = time();
    foreach (array(0, 1) as $day) {
        foreach (array(6, 18) as $hour) {
            $t = strtotime(gmdate('Y-m-d', $now + $day * DAY_IN_SECONDS)
                           . sprintf(' %02d:00:00 UTC', $hour));
            if ($t > $now) return $t;
        }
    }
    return 0;
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
    if (substr($lo, 0, 10) === substr($hi, 0, 10)) {
        return sprintf('Covering %s.', date_i18n('j M Y', strtotime($hi)));
    }
    return sprintf(
        /* translators: 1: earliest date, 2: latest date, both with the year */
        'Covering %1$s to %2$s.',
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
    $next_note = $next
        ? sprintf('Next run %s, in %s.', tit_local_datetime($next), human_time_diff(time(), $next))
        : '';
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

/**
 * The heading block every chart card shares, plus its expand control.
 *
 * The button ships `hidden` and dashboard.js reveals it. A control that only
 * works with JavaScript should not be visible without it, and rendering it
 * server-side (rather than injecting it) keeps its label and markup in one
 * place with the heading it belongs to.
 */
function tit_chart_head($title, $sub, $id = '') {
    ?>
    <div class="tit-chart-head">
      <div class="tit-chart-titles">
        <h3><?php echo esc_html($title); ?></h3>
        <p class="tit-sub"><?php echo esc_html($sub); ?></p>
      </div>
      <div class="tit-chart-tools">
        <button type="button" class="tit-ctl tit-chart-share" data-chart="<?php echo esc_attr($id); ?>"
                title="Copy a link to this view" aria-label="Copy a link to this view" hidden>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 10.7l6.8-4M8.6 13.3l6.8 4"/></svg>
        </button>
        <button type="button" class="tit-ctl tit-chart-dl" data-chart="<?php echo esc_attr($id); ?>"
                title="Download this chart as CSV" aria-label="Download this chart as CSV" hidden>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/></svg>
        </button>
        <button type="button" class="tit-ctl tit-expand" aria-expanded="false"
                title="Expand this chart" aria-label="Expand this chart" hidden>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7"/></svg>
          <span class="tit-sr tit-expand-t">Expand</span>
        </button>
      </div>
    </div>
    <?php
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

function tit_regions(array $counts) {
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

    $total = array_sum(array_map('intval', $counts));
    $out = array();
    foreach ($defs as [$name, $codes]) {
        if ($codes === '') {
            $out[] = array('name' => $name, 'codes' => '', 'n' => $total);
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
