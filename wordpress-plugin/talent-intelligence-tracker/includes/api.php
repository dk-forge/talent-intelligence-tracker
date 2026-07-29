<?php
/**
 * REST API. Namespace talent/v1, which is distinct from the sibling
 * tracker's own namespace. The two never share a route.
 *
 * Public (cached):   GET /query, /aggregate, /facets, /source-health
 * Keyed (X-Talent-API-Key): POST /add, /bulk
 */

if (!defined('ABSPATH')) exit;

const TIT_NS = 'talent/v1';
/*
  Five minutes, not thirty.

  These endpoints already send Cache-Control max-age=300, so the CDN edge holds
  a copy for five minutes whatever we do here. A thirty-minute transient behind
  that meant a figure could be up to half an hour out of date, and coverage is
  about to move hard in both directions at once: international feeds landing,
  and roughly a thousand Form D rows being retracted, which takes total money
  raised down by tens of billions. Every write route calls tit_flush_caches(),
  so the normal path is already immediate; this bounds the damage when a route
  we do not own forgets to. A number that stopped being true this morning is
  the one thing this product cannot print.
*/
const TIT_CACHE_TTL = 300; // 5 min

function tit_register_routes() {
    $keyed = function_exists('tit_api_permission') ? 'tit_api_permission' : '__return_false';

    register_rest_route(TIT_NS, '/query', array(
        'methods' => 'GET', 'callback' => 'tit_api_query', 'permission_callback' => '__return_true',
    ));
    register_rest_route(TIT_NS, '/aggregate', array(
        'methods' => 'GET', 'callback' => 'tit_api_aggregate', 'permission_callback' => '__return_true',
    ));
    register_rest_route(TIT_NS, '/facets', array(
        'methods' => 'GET', 'callback' => 'tit_api_facets', 'permission_callback' => '__return_true',
    ));
    register_rest_route(TIT_NS, '/source-health', array(
        'methods' => 'GET', 'callback' => 'tit_api_source_health', 'permission_callback' => '__return_true',
    ));
    register_rest_route(TIT_NS, '/add', array(
        'methods' => 'POST', 'callback' => 'tit_api_add', 'permission_callback' => $keyed,
    ));
    register_rest_route(TIT_NS, '/bulk', array(
        'methods' => 'POST', 'callback' => 'tit_api_bulk', 'permission_callback' => $keyed,
    ));
    // Derived-field updates for rows already published. Keyed like every other
    // write, and restricted to the allowlist in tit_enrichable_columns().
    register_rest_route(TIT_NS, '/enrich', array(
        'methods' => 'POST', 'callback' => 'tit_api_enrich', 'permission_callback' => $keyed,
    ));
    // Corrections to what we SAID about a source, on rows already published.
    // Deliberately NOT part of /enrich: that route is for derived values, and
    // these two are closer to facts. See tit_api_correct().
    register_rest_route(TIT_NS, '/correct', array(
        'methods' => 'POST', 'callback' => 'tit_api_correct', 'permission_callback' => $keyed,
    ));
    register_rest_route(TIT_NS, '/health', array(
        'methods' => 'POST', 'callback' => 'tit_api_report_health', 'permission_callback' => $keyed,
    ));
    register_rest_route(TIT_NS, '/retract', array(
        'methods' => 'POST', 'callback' => 'tit_api_retract', 'permission_callback' => $keyed,
    ));
    // Operational alert. health_digest.py posts here when a collector has died
    // or the pipeline has stopped, and this mails the owner. Keyed like every
    // write route, so only the pipeline can send mail through the site.
    register_rest_route(TIT_NS, '/alert', array(
        'methods' => 'POST', 'callback' => 'tit_api_alert', 'permission_callback' => $keyed,
    ));
}
add_action('rest_api_init', 'tit_register_routes');

/** Allowed values, mirrored from the pipeline's vocabularies. */
function tit_allowed_pillars() {
    return array('company_development', 'leadership_change', 'rewards_comp', 'how_we_work');
}
function tit_allowed_directions() {
    return array('hiring', 'displacement', 'neutral', 'comp_shift');
}
function tit_allowed_confidence() {
    return array('verified', 'reported', 'rumored');
}
function tit_allowed_functions() {
    return array('engineering','data_ai','it_infrastructure','product','design',
                 'finance','hr_people','sales','marketing','customer_support',
                 'operations','supply_chain','manufacturing','legal_compliance',
                 'research','clinical_healthcare','executive');
}
function tit_allowed_industries() {
    return array('technology','financial_services','healthcare','pharma_biotech',
                 'retail_ecommerce','manufacturing','energy_utilities','telecom',
                 'media_entertainment','transport_logistics','professional_services',
                 'public_sector','hospitality_travel','education','food_beverage',
                 'automotive','aerospace_defence','real_estate_construction');
}

/**
 * Round names, mirrored from the pipeline's normalize_funding_stage. Series D
 * through Z collapse into one bucket there, so they do here too.
 */
function tit_allowed_funding_stages() {
    return array('pre_seed','seed','series_a','series_b','series_c',
                 'series_d_plus','growth','debt','grant','ipo','other');
}

/**
 * What counts as a funding update, in one place.
 *
 * An employer that announced a round without saying how much still raised
 * money, so a stated amount cannot be the test for whether the update is about
 * funding. This used to read `funding_amount IS NOT NULL`, which quietly
 * dropped every amount-free round from the "Raised money" view and would have
 * made the money coverage figure flatter itself (it would have compared the
 * rows with a dollar amount against a set defined as "rows with an amount").
 *
 * Used by the funding=1 filter, the at-a-glance matrix and the money views, so
 * all three count the same population.
 */
function tit_funding_where() {
    return "((funding_amount IS NOT NULL AND funding_amount <> '')"
         . " OR (funding_stage IS NOT NULL AND funding_stage <> ''))";
}

/**
 * Split a comma-separated filter value into a validated list.
 *
 * A recruiter wants "Technology OR Healthcare", not one at a time, so the
 * list-like parameters accept several values. Every value is checked against
 * the closed vocabulary it belongs to before it reaches SQL, so the IN clause
 * can only ever be built from strings we shipped ourselves.
 */
function tit_multi_param(WP_REST_Request $req, $name, array $allowed) {
    $raw = sanitize_text_field($req->get_param($name) ?? '');
    if ($raw === '') return array();
    $out = array();
    foreach (explode(',', $raw) as $value) {
        $value = trim($value);
        if ($value !== '' && in_array($value, $allowed, true) && !in_array($value, $out, true)) {
            $out[] = $value;
        }
    }
    return $out;
}

/** Employer kinds, mirrored from the pipeline's vocabulary. */
function tit_allowed_employer_types() {
    return array('public', 'private', 'startup', 'government', 'nonprofit', 'education');
}

/** Where the work happens, when a source says so. */
function tit_allowed_work_modes() {
    return array('remote', 'hybrid', 'onsite', 'rto_mandate', 'flexible');
}

/** Corporate events, when a source names one. */
function tit_allowed_deal_types() {
    return array('acquisition', 'acquired', 'merger', 'divestiture', 'joint_venture', 'ipo');
}

/**
 * Rows that are not a bare routine filing.
 *
 * NULL counts as notable on purpose. materiality is filled by the pipeline, so
 * every row predating it is NULL, and a predicate written as
 * `materiality IN ('high','medium')` would empty the entire dashboard the
 * moment this shipped. Only a row we have positively judged routine is held
 * back, which is also the honest reading: "not judged" is not "unimportant".
 */
function tit_notable_where() {
    return "(materiality IS NULL OR materiality <> 'routine')";
}

/**
 * Build the WHERE clause shared by /query and /aggregate.
 *
 * country_basis=any (the default) unions job location with employer HQ, so a
 * London-headquartered company's leadership change shows under a UK filter even
 * when the article named no city. country_basis=location is strict.
 *
 * $ignore lets a caller build the SAME clause minus one filter. /aggregate
 * needs the routine count across the set the reader would see if they switched
 * the detail control, and computing it under a clause that already excludes
 * routine rows would report zero every time.
 */
function tit_build_where(WP_REST_Request $req, array &$params, array $ignore = array()) {
    global $wpdb;
    $where = array('is_current = 1');
    $skip = array_flip($ignore);

    // A comma-separated list is accepted so one request can cover a region
    // ("Europe" is a set of countries, not a country). Codes are filtered to
    // exactly two letters, which is both the ISO shape and the whole of the
    // sanitising needed before they reach a prepared IN clause.
    $country = strtoupper(sanitize_text_field($req->get_param('country') ?? ''));
    $codes = array_values(array_unique(array_filter(
        array_map('trim', explode(',', $country)),
        fn($c) => (bool) preg_match('/^[A-Z]{2}$/', $c)
    )));
    if ($codes) {
        $slots = implode(', ', array_fill(0, count($codes), '%s'));
        if (sanitize_text_field($req->get_param('country_basis') ?? 'any') === 'location') {
            $where[] = "country IN ($slots)";
            $params = array_merge($params, $codes);
        } else {
            $where[] = "(country IN ($slots) OR (country IS NULL AND hq_country IN ($slots)))";
            $params = array_merge($params, $codes, $codes);
        }
    }

    $city = sanitize_text_field($req->get_param('city') ?? '');
    if ($city !== '') {
        $where[] = '(city = %s OR (city IS NULL AND hq_city = %s))';
        $params[] = $city;
        $params[] = $city;
    }

    $pillar = sanitize_text_field($req->get_param('pillar') ?? '');
    if ($pillar !== '' && in_array($pillar, tit_allowed_pillars(), true)) {
        $where[] = 'pillar = %s';
        $params[] = $pillar;
    }

    $direction = sanitize_text_field($req->get_param('direction') ?? '');
    if ($direction !== '' && in_array($direction, tit_allowed_directions(), true)) {
        $where[] = 'signal_direction = %s';
        $params[] = $direction;
    }

    $confidence = sanitize_text_field($req->get_param('confidence') ?? '');
    if ($confidence !== '' && in_array($confidence, tit_allowed_confidence(), true)) {
        $where[] = 'confidence = %s';
        $params[] = $confidence;
    }

    $company = sanitize_text_field($req->get_param('company') ?? '');
    if ($company !== '') {
        $where[] = 'company_key LIKE %s';
        $params[] = '%' . $wpdb->esc_like(strtolower($company)) . '%';
    }

    $industries = tit_multi_param($req, 'industry', tit_allowed_industries());
    if ($industries) {
        $where[] = 'industry IN (' . implode(', ', array_fill(0, count($industries), '%s')) . ')';
        $params = array_merge($params, $industries);
    }

    // Kind of employer, which is a real question for a job seeker choosing
    // where to apply and was stored all along with nowhere to ask it.
    $employer_types = tit_multi_param($req, 'employer_type', tit_allowed_employer_types());
    if ($employer_types) {
        $where[] = 'employer_type IN (' . implode(', ', array_fill(0, count($employer_types), '%s')) . ')';
        $params = array_merge($params, $employer_types);
    }

    $work_modes = tit_multi_param($req, 'work_mode', tit_allowed_work_modes());
    if ($work_modes) {
        $where[] = 'work_mode IN (' . implode(', ', array_fill(0, count($work_modes), '%s')) . ')';
        $params = array_merge($params, $work_modes);
    }

    $deal_types = tit_multi_param($req, 'deal_type', tit_allowed_deal_types());
    if ($deal_types) {
        $where[] = 'deal_type IN (' . implode(', ', array_fill(0, count($deal_types), '%s')) . ')';
        $params = array_merge($params, $deal_types);
    }

    $state = strtoupper(sanitize_text_field($req->get_param('state') ?? ''));
    if (preg_match('/^[A-Z]{2}$/', $state)) {
        $where[] = 'state = %s';
        $params[] = $state;
    }

    // functions is a JSON array; match the quoted token so 'finance' never
    // matches a longer value that merely contains it.
    $function_list = tit_multi_param($req, 'function', tit_allowed_functions());
    if ($function_list) {
        // OR, not AND: a row naming engineering satisfies "engineering or
        // design". Requiring both would answer a question nobody asked.
        $likes = array();
        foreach ($function_list as $fn) {
            $likes[] = 'functions LIKE %s';
            $params[] = '%"' . $wpdb->esc_like($fn) . '"%';
        }
        $where[] = '(' . implode(' OR ', $likes) . ')';
    }

    if ($req->get_param('funding') === '1') {
        $where[] = tit_funding_where();
    }

    // Amount raised, as a floor in plain US dollars. Only rows we could read as
    // US dollars can answer this at all, which is why the page offers bands
    // rather than a number box, and why the money views print their coverage.
    $min_funding = (int) ($req->get_param('min_funding_usd') ?: 0);
    if ($min_funding > 0) {
        $where[] = 'funding_amount_usd >= %d';
        $params[] = $min_funding;
    }

    $stages = tit_multi_param($req, 'funding_stage', tit_allowed_funding_stages());
    if ($stages) {
        $where[] = 'funding_stage IN (' . implode(', ', array_fill(0, count($stages), '%s')) . ')';
        $params = array_merge($params, $stages);
    }

    // "Only updates that state a headcount." About 87% of what we hold says
    // nothing about headcount, so filtering TO that is asking for the least
    // informative rows; the useful control is its inverse, and nothing could
    // express it before. hiring and displacement are exactly the directions the
    // SOURCE stated, so this narrows on a fact, never on an inference.
    if (!isset($skip['stated_headcount'])
        && $req->get_param('stated_headcount') === '1') {
        $where[] = "signal_direction IN ('hiring', 'displacement')";
    }

    // How much to show. The API's own default is EVERYTHING: an endpoint that
    // quietly withheld two thirds of its rows unless you knew to ask would be
    // a worse lie than a cluttered page. The dashboard asks for detail=notable
    // explicitly, and says so on screen with the counts.
    if (!isset($skip['detail'])
        && sanitize_text_field($req->get_param('detail') ?? '') === 'notable') {
        $where[] = tit_notable_where();
    }

    // Date window on the source's own published_date, falling back to when we
    // captured it. Filtering on capture date would move a story between
    // periods depending on when a collector happened to run.
    foreach (array('since' => '>=', 'until' => '<=') as $param => $op) {
        $value = sanitize_text_field($req->get_param($param) ?? '');
        if (preg_match('/^\d{4}-\d{2}-\d{2}$/', $value)) {
            $where[] = "COALESCE(published_date, DATE(captured_at)) {$op} %s";
            $params[] = $value;
        }
    }

    $min_headcount = (int) ($req->get_param('min_headcount') ?: 0);
    if ($min_headcount > 0) {
        $where[] = 'headcount >= %d';
        $params[] = $min_headcount;
    }

    // Free-text search across what the source said and what we concluded.
    $search = sanitize_text_field($req->get_param('q') ?? '');
    if ($search !== '') {
        $like = '%' . $wpdb->esc_like($search) . '%';
        $where[] = '(headline LIKE %s OR summary LIKE %s OR talent_readthrough LIKE %s OR company LIKE %s)';
        array_push($params, $like, $like, $like, $like);
    }

    return implode(' AND ', $where);
}

/**
 * Transient key from the WHITELISTED filter params only, never from the whole
 * querystring. Keying on md5 of all query params meant any request with a
 * random extra param (?utm_source=..., cache busters, crawler junk) minted a
 * fresh 30-minute transient row in wp_options, unbounded. Params the endpoint
 * never reads cannot change the response, so they must not change the key.
 */
function tit_cache_key($prefix, WP_REST_Request $req) {
    $whitelist = array(
        'country', 'country_basis', 'city', 'pillar', 'direction', 'confidence',
        'company', 'industry', 'state', 'function', 'funding', 'since', 'until',
        'min_headcount', 'q', 'sort', 'per_page', 'page',
        // A param the endpoint reads MUST be listed here. One that is read but
        // not keyed on means two different responses share a cache entry, and
        // whichever request arrives first decides what everyone else sees.
        'min_funding_usd', 'funding_stage', 'detail', 'stated_headcount',
        'employer_type', 'work_mode', 'deal_type',
    );
    $parts = array();
    foreach ($whitelist as $key) {
        $value = $req->get_param($key);
        if ($value !== null && $value !== '') {
            $parts[$key] = (string) $value;
        }
    }
    return 'tit_' . $prefix . '_' . md5(wp_json_encode($parts));
}

/**
 * Public GET responses carry Cache-Control so the CDN edge can shield the
 * origin; 5 minutes is enough to absorb a crawl or a traffic spike while the
 * transient layer (30 min) stays the origin's own shield.
 */
function tit_public_response($data) {
    $response = rest_ensure_response($data);
    $response->header('Cache-Control', 'public, max-age=300');
    return $response;
}

function tit_api_query(WP_REST_Request $req) {
    global $wpdb;

    $cache_key = tit_cache_key('q', $req);
    $cached = get_transient($cache_key);
    if ($cached !== false) return tit_public_response($cached);

    $params = array();
    $where  = tit_build_where($req, $params);
    $table  = tit_table_name();

    // A closed list, never interpolated from the request: this string goes
    // straight into the SQL, where $wpdb->prepare cannot help.
    // Materiality first, recency inside it. This is its own sort rather than a
    // silent tweak to "newest", because a control labelled "Newest first" that
    // does not put the newest row first is a control that lies. A reader who
    // wants pure recency can still ask for it.
    //
    // Unjudged rows (NULL) rank between medium and routine: we have not called
    // them routine, so they are not treated as routine.
    $material_rank = "CASE materiality WHEN 'high' THEN 0 WHEN 'medium' THEN 1"
                   . " WHEN 'routine' THEN 3 ELSE 2 END ASC";

    $orders = array(
        'notable'  => "{$material_rank}, COALESCE(published_date, DATE(captured_at)) DESC, row_id DESC",
        'newest'   => 'COALESCE(published_date, DATE(captured_at)) DESC, row_id DESC',
        'oldest'   => 'COALESCE(published_date, DATE(captured_at)) ASC, row_id ASC',
        'largest'  => 'headcount DESC, COALESCE(published_date, DATE(captured_at)) DESC',
        'employer' => 'company_key ASC, COALESCE(published_date, DATE(captured_at)) DESC',
        // Only possible now that a numeric column exists: funding_amount was a
        // display string ("$1.45 Million"), and sorting on it put $9M above
        // $10B. MySQL sorts NULLs last on DESC, so the rows whose amount we
        // could not read as US dollars fall to the bottom instead of claiming
        // the top of a list about size.
        'raised'   => 'funding_amount_usd DESC, COALESCE(published_date, DATE(captured_at)) DESC',
        // Column sorts, paired so a header click can toggle direction. A
        // clicked header orders the WHOLE filtered set through this endpoint,
        // never the fifty rows already on screen, which would be a sort quietly
        // lying about its own scope.
        //
        // Rows we cannot place or date sort to the BOTTOM in both directions:
        // reversing a list should not promote the unknowns to the top, because
        // "we do not know" is not an extreme value of anything.
        'employer_desc' => 'company_key DESC, COALESCE(published_date, DATE(captured_at)) DESC',
        'place'      => 'COALESCE(country, hq_country) IS NULL, COALESCE(country, hq_country) ASC,'
                      . ' COALESCE(city, hq_city) IS NULL, COALESCE(city, hq_city) ASC,'
                      . ' COALESCE(published_date, DATE(captured_at)) DESC',
        'place_desc' => 'COALESCE(country, hq_country) IS NULL, COALESCE(country, hq_country) DESC,'
                      . ' COALESCE(city, hq_city) IS NULL, COALESCE(city, hq_city) DESC,'
                      . ' COALESCE(published_date, DATE(captured_at)) DESC',
        // Strongest evidence first, in the order the vocabulary itself ranks.
        'evidence'      => "FIELD(confidence, 'verified', 'reported', 'rumored') ASC,"
                         . ' COALESCE(published_date, DATE(captured_at)) DESC',
        'evidence_desc' => "FIELD(confidence, 'verified', 'reported', 'rumored') DESC,"
                         . ' COALESCE(published_date, DATE(captured_at)) DESC',
    );
    $order = $orders[sanitize_text_field($req->get_param('sort') ?? '')] ?? $orders['notable'];

    $per_page = min(200, max(1, (int) ($req->get_param('per_page') ?: 50)));
    $page     = max(1, (int) ($req->get_param('page') ?: 1));
    $offset   = ($page - 1) * $per_page;

    $count_sql = "SELECT COUNT(*) FROM {$table} WHERE {$where}";
    $total = (int) $wpdb->get_var($params ? $wpdb->prepare($count_sql, $params) : $count_sql);

    // The accepted-column list for /query. A column missing here is invisible
    // to every consumer of the API however well it is populated, so a new
    // field lands in this list in the same change that creates it.
    $rows_sql = "SELECT signal_id, headline, summary, talent_readthrough, company,
                        ticker, cik, employer_type,
                        pillar, signal_direction, city, region, country, hq_city, hq_country,
                        state, functions, industry, headcount, headcount_scope,
                        funding_amount, funding_amount_usd, funding_stage, work_mode,
                        predicted_outcome, check_after_date, outcome_observed, archive_url,
                        materiality, confidence, source_url, source_name,
                        published_date, effective_date, captured_at
                   FROM {$table} WHERE {$where}
                  ORDER BY {$order}
                  LIMIT %d OFFSET %d";
    $rows = $wpdb->get_results($wpdb->prepare($rows_sql, array_merge($params, array($per_page, $offset))), ARRAY_A);

    $out = array(
        'total'    => $total,
        'page'     => $page,
        'per_page' => $per_page,
        'rows'     => $rows ?: array(),
    );
    set_transient($cache_key, $out, TIT_CACHE_TTL);
    return tit_public_response($out);
}

/**
 * The at-a-glance matrix under the caller's own filters.
 *
 * Delegates to tit_glance_matrix() in shortcodes.php — one implementation,
 * so the matrix the server renders and the one JavaScript repaints cannot
 * describe the world differently. Guarded because an FTP deploy can load this
 * file for a few seconds before shortcodes.php lands.
 */
function tit_aggregate_glance($table, $where, array $params) {
    if (!function_exists('tit_glance_matrix')) {
        return null;
    }
    return tit_glance_matrix($table, $where, $params);
}

/**
 * The money views under the caller's own filters. Same guard, same reason.
 */
function tit_aggregate_money($table, $where, array $params) {
    if (!function_exists('tit_money_aggregate')) {
        return null;
    }
    return tit_money_aggregate($table, $where, $params);
}

function tit_api_aggregate(WP_REST_Request $req) {
    global $wpdb;

    $cache_key = tit_cache_key('a', $req);
    $cached = get_transient($cache_key);
    if ($cached !== false) return tit_public_response($cached);

    $params = array();
    $where  = tit_build_where($req, $params);
    $table  = tit_table_name();

    $group = function ($column) use ($wpdb, $table, $where, $params) {
        $sql = "SELECT {$column} AS k, COUNT(*) AS n FROM {$table}
                 WHERE {$where} AND {$column} IS NOT NULL AND {$column} != ''
                 GROUP BY {$column} ORDER BY n DESC LIMIT 40";
        return $wpdb->get_results($params ? $wpdb->prepare($sql, $params) : $sql, ARRAY_A) ?: array();
    };

    // Same expression the page's own chart uses. Grouping on `country` alone
    // here while the server-rendered chart coalesced with hq_country meant the
    // bars jumped the moment a filter was applied, for no reason a reader
    // could see.
    $coalesced = function ($expr) use ($wpdb, $table, $where, $params) {
        $sql = "SELECT {$expr} AS k, COUNT(*) AS n FROM {$table}
                 WHERE {$where} AND {$expr} IS NOT NULL AND {$expr} != ''
                 GROUP BY k ORDER BY n DESC LIMIT 40";
        return $wpdb->get_results($params ? $wpdb->prepare($sql, $params) : $sql, ARRAY_A) ?: array();
    };
    $scalar = function ($expr) use ($wpdb, $table, $where, $params) {
        $sql = "SELECT {$expr} FROM {$table} WHERE {$where}";
        return (int) $wpdb->get_var($params ? $wpdb->prepare($sql, $params) : $sql);
    };

    // Summed dollars, and the coverage figures that say what the sums are
    // based on. Computed once and handed to BOTH the money cards and the
    // at-a-glance matrix, so a dollar total can never appear next to a
    // coverage sentence describing a different set of rows.
    $money = tit_aggregate_money($table, $where, $params);
    $glance = tit_aggregate_glance($table, $where, $params);
    if (is_array($glance) && is_array($money)) {
        $glance['coverage'] = $money['coverage'];
    }

    // How many updates the detail control is setting aside, counted under
    // every OTHER filter but not under the detail filter itself. Counting them
    // under the same clause the rows use would report zero routine filings
    // whenever routine filings were being held back, which is precisely the
    // moment the reader needs the number.
    $md_params = array();
    $md_where  = tit_build_where($req, $md_params, array('detail'));
    $md_sql = "SELECT SUM(materiality = 'routine') AS routine,
                      SUM(materiality IS NULL OR materiality <> 'routine') AS notable
                 FROM {$table} WHERE {$md_where}";
    $md = $wpdb->get_row($md_params ? $wpdb->prepare($md_sql, $md_params) : $md_sql, ARRAY_A) ?: array();

    // What the stated-headcount toggle would leave, counted WITHOUT the toggle
    // applied, so the figure printed beside it says what it would DO rather
    // than reporting itself back.
    $sh_params = array();
    $sh_where  = tit_build_where($req, $sh_params, array('stated_headcount'));
    $sh_sql = "SELECT COUNT(*) FROM {$table} WHERE {$sh_where}
                 AND signal_direction IN ('hiring', 'displacement')";
    $stated = (int) $wpdb->get_var($sh_params ? $wpdb->prepare($sh_sql, $sh_params) : $sh_sql);

    // The date range the page actually covers, from ONE query so the day count
    // and the labels can never disagree. Under the caller's own filters, like
    // every other figure in the hero.
    $span_sql = "SELECT MIN(COALESCE(published_date, DATE(captured_at))) lo,
                        MAX(COALESCE(published_date, DATE(captured_at))) hi
                   FROM {$table} WHERE {$where}";
    $span = $wpdb->get_row($params ? $wpdb->prepare($span_sql, $params) : $span_sql, ARRAY_A) ?: array();

    $total_sql = "SELECT COUNT(*) FROM {$table} WHERE {$where}";
    $out = array(
        'total'      => (int) $wpdb->get_var($params ? $wpdb->prepare($total_sql, $params) : $total_sql),
        // The hero's own figures, so a filtered page can restate them instead
        // of leaving four numbers describing a set the reader is no longer
        // looking at.
        'companies'  => $scalar('COUNT(DISTINCT company_key)'),
        'countries'  => $scalar('COUNT(DISTINCT COALESCE(country, hq_country))'),
        'verified'   => $scalar("SUM(confidence = 'verified')"),
        'by_pillar'  => $group('pillar'),
        'by_country' => $coalesced('COALESCE(country, hq_country)'),
        'by_city'    => $group('city'),
        'by_direction' => $group('signal_direction'),
        'by_industry' => $group('industry'),
        'by_state'   => $group('state'),
        'by_confidence' => $group('confidence'),
        // The at-a-glance matrix, under the SAME where clause as everything
        // else on this response. The old tiles were server-rendered once and
        // never moved, so filtering to one region left the hero contradicting
        // its own summary. A dashboard that disagrees with itself is worse
        // than one that shows less.
        'glance'     => $glance,
        // Summed US dollars by place and by industry, plus the coverage the
        // page must print beside them. Never a bare total: only some rows
        // carry a dollar figure, and a total shown as if it covered
        // everything would be the plausible-but-wrong number this product
        // cannot carry.
        'money'      => $money,
        // What the detail control is holding back, so the page can state it in
        // numbers instead of asking to be trusted.
        'materiality' => array(
            'notable' => (int) ($md['notable'] ?? 0),
            'routine' => (int) ($md['routine'] ?? 0),
        ),
        'stated_headcount' => $stated,
        'span' => array('lo' => $span['lo'] ?? '', 'hi' => $span['hi'] ?? ''),
        'generated'  => gmdate('c'),
    );
    set_transient($cache_key, $out, TIT_CACHE_TTL);
    return tit_public_response($out);
}

function tit_api_facets() {
    global $wpdb;
    $cached = get_transient('tit_facets');
    if ($cached !== false) return tit_public_response($cached);

    $table = tit_table_name();
    $col = function ($column) use ($wpdb, $table) {
        return $wpdb->get_col(
            "SELECT DISTINCT {$column} FROM {$table}
              WHERE is_current = 1 AND {$column} IS NOT NULL AND {$column} != ''
              ORDER BY {$column} ASC LIMIT 300"
        ) ?: array();
    };

    /*
      Geography needs BOTH columns, not just the job location.

      The charts place a record with COALESCE(country, hq_country), so a company
      known only by its head office appears as a bar, and /query accepts it as a
      filter. The dropdown was built from the location column alone, so that
      country was visible in a chart and unselectable in the control beside it.
      Clicking the bar patched an option in on the fly, which hid the gap
      without closing it: anyone who went to the dropdown first simply could not
      find the country.

      Whole table, every row, no page sample. Union in SQL rather than two
      round trips, and DISTINCT over both so a country present in each is
      listed once.
    */
    $geo = function ($column, $hq_column) use ($wpdb, $table) {
        return $wpdb->get_col(
            "SELECT DISTINCT v FROM (
                 SELECT {$column} AS v FROM {$table}
                  WHERE is_current = 1 AND {$column} IS NOT NULL AND {$column} != ''
                 UNION
                 SELECT {$hq_column} AS v FROM {$table}
                  WHERE is_current = 1 AND {$hq_column} IS NOT NULL AND {$hq_column} != ''
             ) u ORDER BY v ASC LIMIT 500"
        ) ?: array();
    };

    $out = array(
        'countries' => $geo('country', 'hq_country'),
        'cities'    => $geo('city', 'hq_city'),
        'states'    => $col('state'),
        // Data-driven on purpose. The vocabulary has eleven rounds and the
        // Form D backfill has filled a handful of them so far; offering all
        // eleven would put ten dead options in front of a reader, and a filter
        // that returns nothing reads as broken rather than as thin coverage.
        'funding_stages' => $col('funding_stage'),
        // Data-driven for the same reason as the stages: a control whose column
        // is still empty is HIDDEN by the page rather than shown returning
        // nothing, and it appears by itself the day the pipeline fills it.
        // Nothing to remember, nothing to go stale.
        'employer_types' => $col('employer_type'),
        'work_modes'     => $col('work_mode'),
        'deal_types'     => $col('deal_type'),
        'industries' => tit_allowed_industries(),
        'functions' => tit_allowed_functions(),
        'pillars'   => tit_allowed_pillars(),
        'directions' => tit_allowed_directions(),
        'confidence' => tit_allowed_confidence(),
    );
    set_transient('tit_facets', $out, TIT_CACHE_TTL);
    return tit_public_response($out);
}

/**
 * Receive each collector's last run.
 *
 * The pipeline has always recorded this locally and never sent it, so
 * /source-health returned an empty list and the sources page could not say when
 * anything last ran. "Running now" was a status with no evidence behind it.
 */
function tit_api_report_health(WP_REST_Request $req) {
    $body = $req->get_json_params();
    $rows = is_array($body['collectors'] ?? null) ? $body['collectors'] : array();

    $clean = array();
    foreach ($rows as $row) {
        $name = sanitize_text_field($row['collector'] ?? '');
        if ($name === '') continue;
        $clean[$name] = array(
            'collector'   => $name,
            'run_at'      => sanitize_text_field($row['run_at'] ?? ''),
            'status'      => in_array(($row['status'] ?? ''), array('ok', 'degraded', 'error'), true)
                             ? $row['status'] : 'degraded',
            'items_found' => (int) ($row['items_found'] ?? 0),
            'items_stored' => (int) ($row['items_stored'] ?? 0),
            'detail'      => sanitize_text_field($row['detail'] ?? ''),
        );
    }

    update_option('tit_source_health', $clean, false);
    delete_transient('tit_facets');
    return rest_ensure_response(array('received' => count($clean)));
}

function tit_api_source_health() {
    return rest_ensure_response(array(
        'plugin_version' => TIT_VERSION,
        'collectors'     => get_option('tit_source_health', array()),
    ));
}

/**
 * Where an operational alert is mailed.
 *
 * Server-side on purpose: the recipient is not something the caller may set, so
 * a leaked pipeline key cannot be used to mail arbitrary addresses. Override
 * with a TIT_ALERT_TO constant in wp-config.php, or the tit_alert_to option.
 */
function tit_alert_recipient() {
    if (defined('TIT_ALERT_TO') && TIT_ALERT_TO) {
        return (string) TIT_ALERT_TO;
    }
    $stored = (string) get_option('tit_alert_to', '');
    if ($stored !== '' && is_email($stored)) {
        return $stored;
    }
    return 'info@asktherecruiter.com';
}

/**
 * Mail the owner that something needs a human.
 *
 * Health has always been recorded and never announced: a dead collector was a
 * red run and a badge on a page nobody opens, and a stopped workflow was
 * nothing at all. This is the one route that reaches a person.
 */
function tit_api_alert(WP_REST_Request $req) {
    $body    = $req->get_json_params();
    $subject = sanitize_text_field(is_array($body) ? ($body['subject'] ?? '') : '');
    $message = is_array($body) ? (string) ($body['body'] ?? '') : '';

    if ($subject === '' || trim($message) === '') {
        return new WP_Error('tit_bad_body', 'subject and body are both required',
                            array('status' => 400));
    }

    // A breakage that persists would otherwise mail on every run until it is
    // fixed, and an alert that arrives weekly forever gets filtered. Repeat the
    // same subject at most once every three days.
    $seen = 'tit_alert_' . md5($subject);
    if (get_transient($seen)) {
        return rest_ensure_response(array(
            'ok' => true, 'sent' => false,
            'reason' => 'suppressed, the same alert went out within three days',
        ));
    }

    $sent = wp_mail(
        tit_alert_recipient(),
        '[Talent Intelligence Tracker] ' . $subject,
        wp_strip_all_tags($message)
    );
    if ($sent) {
        set_transient($seen, 1, 3 * DAY_IN_SECONDS);
    }
    return rest_ensure_response(array('ok' => (bool) $sent, 'sent' => (bool) $sent));
}

function tit_api_add(WP_REST_Request $req) {
    $row = $req->get_json_params();
    if (!is_array($row)) {
        return new WP_Error('tit_bad_body', 'Expected a JSON object', array('status' => 400));
    }
    $result = tit_insert_signal($row);
    if (is_wp_error($result)) return $result;
    return rest_ensure_response(array('result' => $result));
}

/**
 * Withdraw a record from public view without destroying it.
 *
 * Sets is_current = 0 and records why. The row survives, so "what did we
 * publish, and when did we withdraw it" stays answerable — which is the whole
 * point of an event-sourced table, and what a corrections log needs.
 */
function tit_api_retract(WP_REST_Request $req) {
    global $wpdb;
    $body = $req->get_json_params();
    $signal_id = isset($body['signal_id']) ? sanitize_text_field($body['signal_id']) : '';
    $reason    = isset($body['reason']) ? sanitize_text_field($body['reason']) : '';

    if ($signal_id === '' || $reason === '') {
        return new WP_Error('tit_bad_body', 'signal_id and reason are both required',
                            array('status' => 400));
    }

    $table = tit_table_name();
    $updated = $wpdb->query($wpdb->prepare(
        "UPDATE {$table} SET is_current = 0, notes = %s WHERE signal_id = %s AND is_current = 1",
        'retracted: ' . $reason,
        $signal_id
    ));

    if ($updated === false) {
        return new WP_Error('tit_retract_failed', $wpdb->last_error, array('status' => 500));
    }

    tit_flush_caches();
    return rest_ensure_response(array('retracted' => (int) $updated, 'signal_id' => $signal_id));
}

/**
 * Correct what we SAID about a source, on rows that are already published.
 *
 * Two columns only, and they are not in tit_enrichable_columns() on purpose.
 * /enrich writes DERIVED values — a re-parsed figure, a looked-up ticker — and
 * is safe to be liberal with because a bug there adds a wrong label. These two
 * are the opposite: signal_direction is the badge a reader sees, and
 * talent_readthrough is the sentence under it. Both are assertions about a
 * source, so a bug here does not mislabel a row, it misquotes one. They get a
 * narrower door rather than a wider allowlist on the existing one.
 *
 * Why an in-place update is safe: content_hash is md5 of
 * company_key|pillar|published_date|normalised_headline (pipeline/validate.py,
 * content_hash()). Neither of these columns is an input, so correcting them
 * cannot move a row's hash and cannot orphan the dedup. That is what makes
 * this preferable to purge-and-reimport, which would churn thousands of rows.
 *
 * The request must NAME the collector it is correcting, and every UPDATE is
 * scoped to it. A correction pass is written against one source's logic; if
 * the caller builds a bad batch it can then only damage the source it claimed,
 * never sweep rows belonging to another.
 *
 * What this route still cannot do: create a row, revive a retracted one
 * (is_current = 1 is required), or blank a value — an absent or empty field is
 * "no correction for this one", never an erasure.
 */
function tit_correctable_columns() {
    return array('signal_direction', 'talent_readthrough');
}

function tit_api_correct(WP_REST_Request $req) {
    global $wpdb;
    $body = $req->get_json_params();
    $rows = isset($body['rows']) && is_array($body['rows']) ? $body['rows'] : null;
    $collector = isset($body['collector']) ? sanitize_text_field($body['collector']) : '';

    if ($rows === null || $collector === '') {
        return new WP_Error('tit_bad_body',
            'Expected {"collector": "...", "rows": [...]}: a correction must name '
            . 'the source it is correcting.', array('status' => 400));
    }

    $table = tit_table_name();
    $allowed = tit_correctable_columns();
    $directions = tit_allowed_directions();
    $updated = 0; $missing = 0; $skipped = 0; $errors = array();

    foreach ($rows as $i => $row) {
        if (!is_array($row) || empty($row['content_hash'])) {
            $errors[] = array('index' => $i, 'error' => 'content_hash is required');
            continue;
        }
        $data = array();
        foreach ($allowed as $col) {
            if (!array_key_exists($col, $row) || $row[$col] === null || $row[$col] === '') {
                continue;
            }
            $value = (string) $row[$col];
            // The badge is a closed vocabulary. A typo here would render as a
            // label nothing filters on, which is worse than the wrong badge.
            if ($col === 'signal_direction' && !in_array($value, $directions, true)) {
                $errors[] = array('index' => $i,
                                  'error' => 'signal_direction not in vocabulary: ' . $value);
                continue 2;
            }
            $data[$col] = $value;
        }
        if (!$data) { $skipped++; continue; }

        $ok = $wpdb->update($table, $data, array(
            'content_hash' => (string) $row['content_hash'],
            'collector'    => $collector,
            'is_current'   => 1,
        ));
        if ($ok === false) {
            $errors[] = array('index' => $i, 'error' => 'update failed');
        } elseif ($ok === 0) {
            // No live row with that hash for that collector, or it already
            // holds the corrected values. Re-running a correction is expected.
            $missing++;
        } else {
            $updated += (int) $ok;
        }
    }

    if ($updated > 0) {
        tit_flush_caches();
    }
    return rest_ensure_response(array(
        'collector' => $collector, 'corrected' => $updated,
        'unchanged_or_missing' => $missing, 'skipped_no_fields' => $skipped,
        'errors' => $errors,
    ));
}

/**
 * Columns /enrich may write. DERIVED values only.
 *
 * Nothing here is a fact a source stated: these are figures we computed
 * (funding_amount_usd is a deterministic re-parse of the funding string we
 * already hold) or identity we looked up (ticker, cik). The headline, company,
 * counts, source URL and dates can NEVER be written through this path, so a bug
 * in enrichment can add a wrong label but can never rewrite what a filing said.
 * That is why this is a separate endpoint with its own allowlist rather than an
 * update flag on /bulk.
 */
function tit_enrichable_columns() {
    return array(
        'funding_amount_usd', 'funding_stage', 'effective_date',
        'ticker', 'cik', 'work_mode', 'employer_type', 'headcount_scope',
        'materiality',
    );
}

/**
 * Update derived fields on rows that are ALREADY published.
 *
 * publish() only sends rows with published_at IS NULL, and the server treats a
 * re-sent content_hash as a duplicate, so a newly added derived column had no
 * way to reach the live table. Measured 2026-07-28: the local database held
 * $20.79bn of parsed funding across 53 rows while the site's money charts
 * showed one row and $3.2M, because every one of those rows had been published
 * before the column existed.
 */
function tit_api_enrich(WP_REST_Request $req) {
    global $wpdb;
    $body = $req->get_json_params();
    $rows = isset($body['rows']) && is_array($body['rows']) ? $body['rows'] : null;
    if ($rows === null) {
        return new WP_Error('tit_bad_body', 'Expected {"rows": [...]}', array('status' => 400));
    }

    $table = tit_table_name();
    $allowed = tit_enrichable_columns();
    $updated = 0; $missing = 0; $skipped = 0; $errors = array();

    foreach ($rows as $i => $row) {
        if (!is_array($row) || empty($row['content_hash'])) {
            $errors[] = array('index' => $i, 'error' => 'content_hash is required');
            continue;
        }
        $data = array();
        foreach ($allowed as $col) {
            // Only keys actually present, and never an empty one: absent means
            // "we still do not know", which must not erase a value already
            // there.
            if (array_key_exists($col, $row) && $row[$col] !== null && $row[$col] !== '') {
                $data[$col] = $row[$col];
            }
        }
        if (!$data) { $skipped++; continue; }

        $ok = $wpdb->update(
            $table, $data,
            array('content_hash' => (string) $row['content_hash'], 'is_current' => 1)
        );
        if ($ok === false) {
            $errors[] = array('index' => $i, 'error' => 'update failed');
        } elseif ($ok === 0) {
            // No row with that hash, or the values already matched. Neither is
            // an error: the pipeline re-sends the same enrichment happily.
            $missing++;
        } else {
            $updated += (int) $ok;
        }
    }

    if ($updated > 0) {
        tit_flush_caches();
    }
    return rest_ensure_response(array(
        'updated' => $updated, 'unchanged_or_missing' => $missing,
        'skipped_no_fields' => $skipped, 'errors' => $errors,
    ));
}

function tit_api_bulk(WP_REST_Request $req) {
    $body = $req->get_json_params();
    $rows = isset($body['rows']) && is_array($body['rows']) ? $body['rows'] : null;
    if ($rows === null) {
        return new WP_Error('tit_bad_body', 'Expected {"rows": [...]}', array('status' => 400));
    }

    $stored = 0; $duplicate = 0; $retracted = 0; $errors = array();
    foreach ($rows as $i => $row) {
        // $flush = false: one flush after the loop, not one per inserted row.
        $result = tit_insert_signal(is_array($row) ? $row : array(), false);
        if (is_wp_error($result)) {
            $errors[] = array('index' => $i, 'error' => $result->get_error_message());
        } elseif ($result === 'stored') {
            $stored++;
        } elseif ($result === 'retracted') {
            // Counted with duplicates for the caller's totals (additive key
            // below says how many), because a retracted story re-arriving is
            // expected every run, not a failure.
            $duplicate++;
            $retracted++;
        } else {
            $duplicate++;
        }
    }

    // One flush for the whole batch, and only when something actually changed.
    // Flushing inside the loop purged the page cache once per stored row.
    if ($stored > 0) {
        tit_flush_caches();
    }

    // Fail loud: a batch with any failure returns 207 so the caller's
    // --fail-with-body sees it, rather than a cheerful 200 hiding losses.
    $payload = array(
        'stored' => $stored, 'duplicate' => $duplicate,
        'retracted' => $retracted, 'errors' => $errors,
    );
    $response = rest_ensure_response($payload);
    if ($errors) $response->set_status(207);
    return $response;
}
