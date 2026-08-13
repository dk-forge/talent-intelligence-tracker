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

/**
 * What an employer did with a place of work, when a source says so.
 *
 * The earliest geographic hiring signal there is: a site decision is public
 * months before the job adverts are. It carries no headcount claim of its own,
 * which is why it is a filter and not a direction.
 */
function tit_allowed_site_events() {
    return array('opened', 'closed', 'expanded', 'relocated', 'announced');
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
 * WHERE A ROW IS, FOR GROUPING. One authority, because a count and the filter
 * it drives have to agree.
 *
 * Every place FILTER in this file unions the job location with the employer's
 * head office: `city = %s OR (city IS NULL AND hq_city = %s)`, written in that
 * shape rather than as a COALESCE comparison so it can use idx_geo / idx_hq
 * (see the note on tit_place_kinds()). Every place COUNT therefore has to group
 * by the expression that selects the same rows, which is the COALESCE form.
 * COALESCE(a, b) = x is true precisely when a = x OR (a IS NULL AND b = x).
 *
 * The dashboard's "Top cities" strip grouped by bare `city` instead, and the
 * discrepancy was not small: the London pill read 18 while clicking it returned
 * 1,338, because nearly every London row is placed by its employer's head
 * office. Manchester (108) and Edinburgh (49) were missing from the strip
 * altogether while Seattle (42) and Toronto (25) sat in it. A pill that
 * contradicts the page it links to is worse than no pill.
 *
 * Grouping is a full scan either way, so the COALESCE form costs nothing here.
 */
function tit_city_expr() {
    return 'COALESCE(city, hq_city)';
}

/** The same rule for countries. See tit_city_expr(). */
function tit_country_expr() {
    return 'COALESCE(country, hq_country)';
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

    $site_events = tit_multi_param($req, 'site_event', tit_allowed_site_events());
    if ($site_events) {
        $where[] = 'site_event IN (' . implode(', ', array_fill(0, count($site_events), '%s')) . ')';
        $params = array_merge($params, $site_events);
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

    /*
      "Only updates that move headcount", and READ THE NAME CAREFULLY, because
      the parameter's name is the misleading part of this control.

      It selects rows where the SOURCE stated a DIRECTION of headcount movement.
      It does not select rows carrying a headcount NUMBER, and the difference is
      not academic: measured 2026-07-30 over 15,711 current rows, `headcount` is
      non-null on 11 of them (0.07%) while signal_direction is hiring or
      displacement on 53 (0.34%). Exposing the number column as a filter would
      cut the page to eleven rows, which is why this control is the direction and
      why the label a reader sees says "move headcount" rather than "state a
      headcount". A comment here previously said "about 87%" of rows say nothing
      about headcount; the real figure is 99.93%, and being an order of magnitude
      out in the comment is how the chips bar came to describe this control as
      "Only with a stated headcount", which is a claim about a column it does not
      read.

      hiring and displacement are exactly the directions a source stated, so this
      narrows on a fact and never on an inference.
    */
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
        'employer_type', 'work_mode', 'deal_type', 'site_event',
        // /aggregate's response-shaping param. Read but not keyed on would let
        // a slimmed include=fresh response be served to the full dashboard
        // fetch, which then paints from keys that are not there.
        'include',
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
    // company_key rides along for the watchlist: the star keys on the same
    // canonical identity the company pages and the employer filter use, so
    // "Alphabet" and "Alphabet Inc." star as one employer rather than two.
    $rows_sql = "SELECT signal_id, headline, summary, talent_readthrough, company,
                        company_key, ticker, cik, employer_type,
                        pillar, signal_direction, city, region, country, hq_city, hq_country,
                        state, functions, industry, headcount, headcount_scope,
                        funding_amount, funding_amount_usd, funding_stage, work_mode,
                        deal_type, site_event,
                        predicted_outcome, check_after_date, outcome_observed, archive_url,
                        collector,
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
 * The trend chart under the caller's own filters, ALREADY RENDERED.
 *
 * This is the one place this endpoint returns markup, and the reason is the
 * same one that made the matrix a single implementation. A chart is geometry:
 * paths, a zero-based scale, a continuity gate that decides which lines may be
 * drawn at all. Shipping the series as data means writing that geometry a
 * second time in JavaScript, and two implementations of a gate that decides
 * whether a line is honest is two answers to the question this page exists to
 * answer once. So the server draws it, here and on first paint, from the same
 * function, and the browser swaps the element's contents.
 *
 * Everything inside is escaped where it is built; see tit_trend_svg().
 */
function tit_aggregate_trend($table, $where, array $params) {
    if (!function_exists('tit_signal_trend') || !function_exists('tit_signal_trend_html')) {
        return '';
    }
    return tit_signal_trend_html(tit_signal_trend($table, $where, $params));
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

    /*
      include=fresh RETURNS ONLY THE FRESHNESS PANEL'S FIGURES. The panel pairs
      each stat with the current year's slice, which is one more /aggregate
      call under the same filters plus since=Jan-1; making that call carry the
      groups, the matrix and the trend would double the whole endpoint's cost
      to fetch four scalars. A closed vocabulary of one value: anything else is
      ignored and the full response returns, so nothing that already consumes
      this endpoint can change shape by accident. The cache key carries the
      param (see tit_cache_key) so slim and full responses never share an entry.
    */
    if (trim((string) $req->get_param('include')) === 'fresh') {
        $one = function ($expr) use ($wpdb, $table, $where, $params) {
            $sql = "SELECT {$expr} FROM {$table} WHERE {$where}";
            return (int) $wpdb->get_var($params ? $wpdb->prepare($sql, $params) : $sql);
        };
        $out = array(
            'total'     => $one('COUNT(*)'),
            'companies' => $one('COUNT(DISTINCT company_key)'),
            'countries' => $one('COUNT(DISTINCT ' . tit_country_expr() . ')'),
            'verified'  => $one("SUM(confidence = 'verified')"),
            'money'     => tit_aggregate_money($table, $where, $params),
            'generated' => gmdate('c'),
        );
        set_transient($cache_key, $out, TIT_CACHE_TTL);
        return tit_public_response($out);
    }

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
        'countries'  => $scalar('COUNT(DISTINCT ' . tit_country_expr() . ')'),
        'verified'   => $scalar("SUM(confidence = 'verified')"),
        'by_pillar'  => $group('pillar'),
        'by_country' => $coalesced(tit_country_expr()),
        // by_city was $group('city'), i.e. the job location alone, while the
        // `city` filter on this same endpoint unions it with the employer's
        // head office. So this endpoint reported a city count that its own
        // filter contradicted. Same fix, same reason as by_country above.
        'by_city'    => $coalesced(tit_city_expr()),
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
        // The trajectory behind those columns, rendered server-side under the
        // same clause. See tit_aggregate_trend() for why this one is markup.
        'trend_html' => tit_aggregate_trend($table, $where, $params),
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
        // The one-collector caveat under the place chart, computed under the
        // caller's own filters so it names whichever country is dominated in
        // THIS view and vanishes when none is.
        'place_caveat' => function_exists('tit_place_caveat')
            ? tit_place_caveat($table, $where, $params) : '',
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
        'site_events'    => $col('site_event'),
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
 * Open CI alerts, keyed on CAUSE.
 *
 * Deliberately an option and not a transient: a transient can be evicted by an
 * object cache at any moment, and an evicted "we already told them" record
 * re-sends an alert the owner has already read, while an evicted "this is open"
 * record silently swallows the RECOVERED notice. Neither failure announces
 * itself, which is the property this whole feature exists to stop paying for.
 * Stored with autoload = false, so it costs nothing on a normal page request.
 */
function tit_alert_state() {
    $state = get_option('tit_ci_alert_state', array());
    return is_array($state) ? $state : array();
}

function tit_alert_state_save($state) {
    // A caller looping on a mutating cause key could otherwise grow wp_options
    // without bound. Keep the newest 200 and drop the rest.
    if (count($state) > 200) {
        uasort($state, function ($a, $b) {
            return ((int) ($a['first'] ?? 0)) <=> ((int) ($b['first'] ?? 0));
        });
        $state = array_slice($state, -200, null, true);
    }
    update_option('tit_ci_alert_state', $state, false);
}

/**
 * Mail the owner that something needs a human.
 *
 * Health has always been recorded and never announced: a dead collector was a
 * red run and a badge on a page nobody opens, and a stopped workflow was
 * nothing at all. This is the one route that reaches a person.
 *
 * THREE CALLING SHAPES, and the difference is the whole point:
 *
 *   {subject, body}                — legacy. Suppressed by SUBJECT for 3 days.
 *                                    health_digest.py and link_check.py.
 *   {subject, body, dedupe_key}    — an alarm is RAISED for that cause key. The
 *                                    same key stays quiet until it is resolved.
 *   {subject, body, resolve_scope} — an alarm is CLEARED. Mails once if anything
 *                                    was open under that scope, silent if not.
 *
 * WHY DEDUPE BY CAUSE RATHER THAN BY RUN. The sibling repo had one assertion
 * redden CI eight consecutive times in an afternoon. Eight identical emails
 * would teach the owner to filter this sender, which recreates the original
 * problem — an alarm nobody reads — in a new form. `ci_alert.py` normalises
 * run-to-run numbers out of the message before hashing it, so a count drifting
 * while the same thing stays broken is ONE cause and mails once, and a
 * genuinely different assertion mails immediately.
 *
 * AND IT CLEARS. `resolve_scope` is posted on every green run, so a fixed
 * breakage says so exactly once. That is what lets the owner stop worrying
 * without going and checking, which is the actual ask.
 */
function tit_api_alert(WP_REST_Request $req) {
    $body    = $req->get_json_params();
    $subject = sanitize_text_field(is_array($body) ? ($body['subject'] ?? '') : '');
    $message = is_array($body) ? (string) ($body['body'] ?? '') : '';

    if ($subject === '' || trim($message) === '') {
        return new WP_Error('tit_bad_body', 'subject and body are both required',
                            array('status' => 400));
    }

    $to      = tit_alert_recipient();
    $dedupe  = sanitize_text_field(is_array($body) ? ($body['dedupe_key'] ?? '') : '');
    $resolve = sanitize_text_field(is_array($body) ? ($body['resolve_scope'] ?? '') : '');
    $safe    = '/^[a-z0-9][a-z0-9:._-]{0,159}$/';

    // ---- RECOVERY -------------------------------------------------------
    if ($resolve !== '') {
        if (!preg_match($safe, $resolve)) {
            return new WP_Error('tit_bad_body', 'bad resolve_scope', array('status' => 400));
        }
        $state = tit_alert_state();
        $open  = array();
        foreach ($state as $k => $v) {
            if (strpos($k, $resolve . ':') === 0) { $open[] = $k; }
        }
        if (!$open) {
            // The overwhelmingly common case: a green run of something already
            // green. Silence here is what makes it safe to post a resolve on
            // EVERY success without the clear becoming noise in its own right.
            return rest_ensure_response(array(
                'ok' => true, 'sent' => false,
                'reason' => 'nothing was open for this scope',
            ));
        }
        $extra = "\n\nThis clears " . count($open) . " open alert(s):\n";
        foreach ($open as $k) {
            $extra .= '  - ' . (string) ($state[$k]['subject'] ?? $k) . "\n";
        }
        $sent = wp_mail($to, '[Talent Intelligence Tracker] ' . $subject,
                        wp_strip_all_tags($message . $extra));
        // Cleared whether or not the mail landed. The flag answers "is there an
        // unresolved failure", and the answer is now no; leaving it open would
        // suppress the NEXT genuine alert for this cause, which is the more
        // expensive of the two mistakes.
        foreach ($open as $k) { unset($state[$k]); }
        tit_alert_state_save($state);
        return rest_ensure_response(array(
            'ok' => (bool) $sent, 'sent' => (bool) $sent, 'cleared' => count($open),
        ));
    }

    // ---- CAUSE-KEYED ALARM ----------------------------------------------
    if ($dedupe !== '') {
        if (!preg_match($safe, $dedupe)) {
            return new WP_Error('tit_bad_body', 'bad dedupe_key', array('status' => 400));
        }
        $state = tit_alert_state();
        $now   = time();
        $first = $now;
        if (isset($state[$dedupe])) {
            $first = (int) ($state[$dedupe]['first'] ?? $now);
            $last  = (int) ($state[$dedupe]['last'] ?? $first);
            if (($now - $last) < 14 * DAY_IN_SECONDS) {
                return rest_ensure_response(array(
                    'ok' => true, 'sent' => false,
                    'reason' => 'suppressed, this exact cause is already open (raised '
                                . human_time_diff($first, $now) . ' ago)',
                ));
            }
            // One reminder a fortnight, no more. Total silence until a green run
            // would mean a breakage the owner missed once is never mentioned
            // again; twice a month is a reminder, not alarm fatigue.
            $subject = 'STILL FAILING: ' . $subject;
        }
        $sent = wp_mail($to, '[Talent Intelligence Tracker] ' . $subject,
                        wp_strip_all_tags($message));
        if ($sent) {
            // Only recorded on a successful send. An alarm that was never
            // delivered is not "already reported" — the next failure must retry.
            $state[$dedupe] = array('first' => $first, 'last' => $now, 'subject' => $subject);
            tit_alert_state_save($state);
        }
        return rest_ensure_response(array('ok' => (bool) $sent, 'sent' => (bool) $sent));
    }

    // ---- LEGACY: suppress by subject for three days ----------------------
    // A breakage that persists would otherwise mail on every run until it is
    // fixed, and an alert that arrives weekly forever gets filtered.
    // An OPTION, via tit_ephemeral_* in db.php, for the same reason
    // `tit_ci_alert_state` above is one. As a transient this key matched
    // `_transient_tit_%`, so tit_flush_caches() wiped it on every write route:
    // the three-day window collapsed to "until the next collector run", and a
    // persistent breakage mailed the owner several times a day. An alarm that
    // mails several times a day is one you learn to filter, and a filtered
    // alarm is the original silence in a new hat - which is the whole reason
    // this endpoint's keyed path dedupes by cause. The legacy path was quietly
    // undoing that for every caller still on it (health_digest.py,
    // link_check.py, process_tips.py).
    // FTP race: fail OPEN, so the alert goes out. A duplicate email costs the
    // owner a second read; a swallowed one costs the thing this route exists for.
    $seen = 'alert_' . md5($subject);
    if (function_exists('tit_ephemeral_get') && tit_ephemeral_get($seen)) {
        return rest_ensure_response(array(
            'ok' => true, 'sent' => false,
            'reason' => 'suppressed, the same alert went out within three days',
        ));
    }

    $sent = wp_mail($to, '[Talent Intelligence Tracker] ' . $subject,
                    wp_strip_all_tags($message));
    if ($sent && function_exists('tit_ephemeral_set')) {
        tit_ephemeral_set($seen, 1, 3 * DAY_IN_SECONDS);
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
/*
 * WHERE THE SOURCE PLACED IT: city, region and country, added 1.53.0.
 *
 * The reasoning matters, because these are the one class of column that is
 * neither derived nor quite a quotation, and one of them is explicitly refused
 * by the OTHER write route.
 *
 * `country` is deliberately not ENRICHABLE (see tit_enrichable_columns())
 * because it is the job location as a source stated it, and writing a looked-up
 * head office into it would turn "where the source says this happened" into
 * "where the company is from" with no way back. That argument says the column
 * must not be filled from a lookup. It says nothing against fixing a value we
 * read wrong, which is what a correction is, and which had no route at all: two
 * current rows read city Toronto with country US, and five more the same at the
 * head office. They are Canadian issuers filing with the SEC, they are the only
 * such contradiction in the corpus, and they are why the dashboard's Toronto
 * pill flew an American flag.
 *
 * Same in-place safety as the two columns above. content_hash is md5 of
 * company_key, pillar, published_date and the normalised headline
 * (pipeline/validate.py), so none of these three is an input to it, and
 * correcting one cannot move a row's fingerprint or orphan the dedup. That is
 * what makes this preferable to a withdraw-and-republish, which for a revision
 * carrying the SAME hash would remove both rows rather than replacing one.
 *
 * Still scoped to the collector the caller names, still unable to blank a value,
 * and still unable to create or revive a row. Proved by running it:
 * tests/php/enrich_and_correct.php.
 *
 * NOTE FOR THE NEXT EDITOR: tests/test_form_d_correction.py reads the BODY of
 * the function below as text and fails if a forbidden column name appears in
 * it, so keep prose out of it. That is why this block is up here.
 */
function tit_correctable_columns() {
    return array(
        'signal_direction', 'talent_readthrough',
        'city', 'region', 'country',
    );
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
        // The employer's headquarters: looked up, never claimed by a source,
        // so it belongs in exactly the same class as ticker and cik. It was
        // missing here by oversight, and the cost was specific. The identity
        // backfill fills these locally and they had no route to the live
        // table, so published rows stayed invisible to every geographic filter
        // while we already held the answer. The recall measurement found it.
        //
        // `country` is deliberately NOT enrichable. That column is the JOB
        // location and comes only from the source text; writing a looked-up
        // value into it would turn "where the source says this happened" into
        // "where the company is from", and there would be no way back. The
        // country filter already unions the two at query time, which is where
        // that belongs.
        'hq_city', 'hq_country',
        // A Wayback permalink for the source_url this row already carries,
        // recorded by archive_sources.py. Looked up, never claimed by a
        // source, so it sits in the same class as ticker and cik. It can only
        // ADD a fallback: it is not source_url, it can never overwrite
        // source_url, and a row whose archiving failed is exactly as sourced as
        // it was. That is why the snapshot lives in its own column rather than
        // being written over a link that died.
        'archive_url',
    );
}

/**
 * Columns /enrich may set back to NULL, when the caller says so EXPLICITLY.
 *
 * /enrich ignores an absent or empty field on purpose: absent means "we still do
 * not know", and letting a blank erase a known value is how an enrichment pass
 * with one missing lookup wipes a column. That guarantee is kept exactly as it
 * was. This is the other case, and it is narrow: a value we DID compute and have
 * since established is wrong, where leaving it there publishes a false figure.
 *
 * It exists because there was no route at all. Five live rows carried a
 * funding_amount_usd off by a factor of a million (a hyphenated multiplier, and
 * Danish kroner that the currency denylist did not recognise), and three of the
 * five have no correct dollar value to send: the round was in kroner or euros,
 * and this page promises those are left out rather than converted at a rate
 * nobody published. So the only true value is no value, and neither /enrich nor
 * /correct could write it.
 *
 * Deliberately NOT the whole enrichable list. `archive_url` is the fallback that
 * outlives a dead publisher, and clearing it loses work rather than removing a
 * wrong claim. Add a column here only when its wrong value would be a published
 * falsehood and no right value exists.
 *
 * `hq_city` and `hq_country` were on the other side of that line until
 * 2026-08-12, on the reasoning that looked-up identity is work and clearing it
 * throws work away. That reasoning held only while every stored value was
 * actually looked up. `hq_country` is read from P17 of the entity's
 * HEADQUARTERS and falls back to P17 of the entity itself, and one cancelled
 * placement run committed 37 rows off that weaker fallback with no headquarters
 * city behind them. Synthesia, the UK company, went live filed under Czechia:
 * the Czech chemical works of the same name. There is no right value to send
 * instead, because the right value is that we do not know, and this page would
 * rather show a blank than a country it made up. So the only correction is a
 * clear, and without these two entries there was no route for it at all --
 * `/correct` does not carry `hq_country` and `/enrich` ignores an empty field by
 * design. The narrowness is kept where it matters: a clear still has to be named
 * explicitly in `clear`, so an absent or empty field can no more erase a
 * headquarters than it could erase a funding figure.
 */
function tit_clearable_columns() {
    return array('funding_amount_usd', 'funding_stage', 'hq_city', 'hq_country');
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

        // An EXPLICIT clear. Named in its own array, so it can never be the
        // result of a field being absent or empty, which is the property the
        // loop above exists to protect. Restricted to tit_clearable_columns().
        if (isset($row['clear']) && is_array($row['clear'])) {
            $clearable = tit_clearable_columns();
            foreach ($row['clear'] as $col) {
                $col = (string) $col;
                if (!in_array($col, $clearable, true)) {
                    $errors[] = array('index' => $i,
                                      'error' => 'not clearable: ' . $col);
                    continue;
                }
                // A column cannot be set and cleared in one row: that is a
                // caller bug, and picking a winner would hide it.
                if (array_key_exists($col, $data)) {
                    $errors[] = array('index' => $i,
                                      'error' => 'both set and cleared: ' . $col);
                    unset($data[$col]);
                    continue;
                }
                $data[$col] = null;
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
