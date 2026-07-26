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

    $country = strtoupper(sanitize_text_field($req->get_param('country') ?? ''));
    if ($country !== '') {
        if (sanitize_text_field($req->get_param('country_basis') ?? 'any') === 'location') {
            $where[] = 'country = %s';
            $params[] = $country;
        } else {
            $where[] = '(country = %s OR (country IS NULL AND hq_country = %s))';
            $params[] = $country;
            $params[] = $country;
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

    return implode(' AND ', $where);
}

function tit_cache_key($prefix, WP_REST_Request $req) {
    return 'tit_' . $prefix . '_' . md5(wp_json_encode($req->get_query_params()));
}

function tit_api_query(WP_REST_Request $req) {
    global $wpdb;

    $cache_key = tit_cache_key('q', $req);
    $cached = get_transient($cache_key);
    if ($cached !== false) return rest_ensure_response($cached);

    $params = array();
    $where  = tit_build_where($req, $params);
    $table  = tit_table_name();

    $per_page = min(200, max(1, (int) ($req->get_param('per_page') ?: 50)));
    $page     = max(1, (int) ($req->get_param('page') ?: 1));
    $offset   = ($page - 1) * $per_page;

    $count_sql = "SELECT COUNT(*) FROM {$table} WHERE {$where}";
    $total = (int) $wpdb->get_var($params ? $wpdb->prepare($count_sql, $params) : $count_sql);

    $rows_sql = "SELECT signal_id, headline, summary, talent_readthrough, company,
                        pillar, signal_direction, city, region, country, hq_city, hq_country,
                        confidence, source_url, source_name, published_date, captured_at
                   FROM {$table} WHERE {$where}
                  ORDER BY COALESCE(published_date, DATE(captured_at)) DESC, row_id DESC
                  LIMIT %d OFFSET %d";
    $rows = $wpdb->get_results($wpdb->prepare($rows_sql, array_merge($params, array($per_page, $offset))), ARRAY_A);

    $out = array(
        'total'    => $total,
        'page'     => $page,
        'per_page' => $per_page,
        'rows'     => $rows ?: array(),
    );
    set_transient($cache_key, $out, TIT_CACHE_TTL);
    return rest_ensure_response($out);
}

function tit_api_aggregate(WP_REST_Request $req) {
    global $wpdb;

    $cache_key = tit_cache_key('a', $req);
    $cached = get_transient($cache_key);
    if ($cached !== false) return rest_ensure_response($cached);

    $params = array();
    $where  = tit_build_where($req, $params);
    $table  = tit_table_name();

    $group = function ($column) use ($wpdb, $table, $where, $params) {
        $sql = "SELECT {$column} AS k, COUNT(*) AS n FROM {$table}
                 WHERE {$where} AND {$column} IS NOT NULL AND {$column} != ''
                 GROUP BY {$column} ORDER BY n DESC LIMIT 40";
        return $wpdb->get_results($params ? $wpdb->prepare($sql, $params) : $sql, ARRAY_A) ?: array();
    };

    $total_sql = "SELECT COUNT(*) FROM {$table} WHERE {$where}";
    $out = array(
        'total'      => (int) $wpdb->get_var($params ? $wpdb->prepare($total_sql, $params) : $total_sql),
        'by_pillar'  => $group('pillar'),
        'by_country' => $group('country'),
        'by_city'    => $group('city'),
        'by_direction' => $group('signal_direction'),
        'by_confidence' => $group('confidence'),
        'generated'  => gmdate('c'),
    );
    set_transient($cache_key, $out, TIT_CACHE_TTL);
    return rest_ensure_response($out);
}

function tit_api_facets() {
    global $wpdb;
    $cached = get_transient('tit_facets');
    if ($cached !== false) return rest_ensure_response($cached);

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
        'pillars'   => tit_allowed_pillars(),
        'directions' => tit_allowed_directions(),
        'confidence' => tit_allowed_confidence(),
    );
    set_transient('tit_facets', $out, TIT_CACHE_TTL);
    return rest_ensure_response($out);
}

function tit_api_source_health() {
    return rest_ensure_response(array(
        'plugin_version' => TIT_VERSION,
        'collectors'     => get_option('tit_source_health', array()),
    ));
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

function tit_api_bulk(WP_REST_Request $req) {
    $body = $req->get_json_params();
    $rows = isset($body['rows']) && is_array($body['rows']) ? $body['rows'] : null;
    if ($rows === null) {
        return new WP_Error('tit_bad_body', 'Expected {"rows": [...]}', array('status' => 400));
    }

    $stored = 0; $duplicate = 0; $errors = array();
    foreach ($rows as $i => $row) {
        $result = tit_insert_signal(is_array($row) ? $row : array());
        if (is_wp_error($result)) {
            $errors[] = array('index' => $i, 'error' => $result->get_error_message());
        } elseif ($result === 'stored') {
            $stored++;
        } else {
            $duplicate++;
        }
    }

    // Fail loud: a batch with any failure returns 207 so the caller's
    // --fail-with-body sees it, rather than a cheerful 200 hiding losses.
    $payload = array('stored' => $stored, 'duplicate' => $duplicate, 'errors' => $errors);
    $response = rest_ensure_response($payload);
    if ($errors) $response->set_status(207);
    return $response;
}
