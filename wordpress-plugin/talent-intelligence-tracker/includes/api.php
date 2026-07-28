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
const TIT_CACHE_TTL = 1800; // 30 min

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
 * Build the WHERE clause shared by /query and /aggregate.
 *
 * country_basis=any (the default) unions job location with employer HQ, so a
 * London-headquartered company's leadership change shows under a UK filter even
 * when the article named no city. country_basis=location is strict.
 */
function tit_build_where(WP_REST_Request $req, array &$params) {
    global $wpdb;
    $where = array('is_current = 1');

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

    $industry = sanitize_text_field($req->get_param('industry') ?? '');
    if ($industry !== '' && in_array($industry, tit_allowed_industries(), true)) {
        $where[] = 'industry = %s';
        $params[] = $industry;
    }

    $state = strtoupper(sanitize_text_field($req->get_param('state') ?? ''));
    if (preg_match('/^[A-Z]{2}$/', $state)) {
        $where[] = 'state = %s';
        $params[] = $state;
    }

    // functions is a JSON array; match the quoted token so 'finance' never
    // matches a longer value that merely contains it.
    $function = sanitize_text_field($req->get_param('function') ?? '');
    if ($function !== '' && in_array($function, tit_allowed_functions(), true)) {
        $where[] = 'functions LIKE %s';
        $params[] = '%"' . $wpdb->esc_like($function) . '"%';
    }

    if ($req->get_param('funding') === '1') {
        $where[] = "funding_amount IS NOT NULL AND funding_amount != ''";
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
    $orders = array(
        'newest'   => 'COALESCE(published_date, DATE(captured_at)) DESC, row_id DESC',
        'oldest'   => 'COALESCE(published_date, DATE(captured_at)) ASC, row_id ASC',
        'largest'  => 'headcount DESC, COALESCE(published_date, DATE(captured_at)) DESC',
        'employer' => 'company_key ASC, COALESCE(published_date, DATE(captured_at)) DESC',
    );
    $order = $orders[sanitize_text_field($req->get_param('sort') ?? '')] ?? $orders['newest'];

    $per_page = min(200, max(1, (int) ($req->get_param('per_page') ?: 50)));
    $page     = max(1, (int) ($req->get_param('page') ?: 1));
    $offset   = ($page - 1) * $per_page;

    $count_sql = "SELECT COUNT(*) FROM {$table} WHERE {$where}";
    $total = (int) $wpdb->get_var($params ? $wpdb->prepare($count_sql, $params) : $count_sql);

    $rows_sql = "SELECT signal_id, headline, summary, talent_readthrough, company,
                        pillar, signal_direction, city, region, country, hq_city, hq_country,
                        state, functions, industry, headcount, funding_amount,
                        predicted_outcome, check_after_date, outcome_observed, archive_url,
                        confidence, source_url, source_name, published_date, captured_at
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
        'glance'     => tit_aggregate_glance($table, $where, $params),
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

    $out = array(
        'countries' => $col('country'),
        'cities'    => $col('city'),
        'states'    => $col('state'),
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
