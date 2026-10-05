<?php
/**
 * Reference data: external context series, stored apart from the tracker's own.
 *
 * Mirrors the sibling tracker's /reference/<source> pair, on this plugin's own
 * conventions (one option per source, keyed write, public read, never
 * autoloaded, nothing summed into a signal):
 *
 *   POST /talent/v1/reference-ingest/<source>  keyed; the collector's compact copy
 *   GET  /talent/v1/reference/<source>          public; what was stored, as stored
 *
 * The full tables live in the repo under data/reference/<source>/ (the
 * collectors commit them); the site holds only the compact copy a page needs.
 * Every stored payload must carry its licence, attribution line, source URL and
 * as_of date: a number with no source is not a measurement.
 *
 * tit_reference_attributions() is the single list the Sources page prints, so
 * a source added here cannot ship without its attribution line.
 */

if (!defined('ABSPATH')) exit;

/** Allowlist: source => attribution row for the Sources page. */
function tit_reference_sources() {
    return array(
        'indeed_occupations' => array(
            'name'    => 'Indeed Hiring Lab: job postings by occupation and country',
            'url'     => 'https://github.com/hiring-lab/job_postings_tracker',
            'licence' => 'CC BY 4.0',
            'cadence' => 'weekly',
            'line'    => 'Job postings indices for 11 countries and, where published, '
                       . 'by occupational category. Values as Indeed Hiring Lab published '
                       . 'them (100 = February 1, 2020); the 4- and 52-week changes are ours. '
                       . 'Licensed CC BY 4.0.',
        ),
        'h1b_lca' => array(
            'name'    => 'U.S. Department of Labor, OFLC: H-1B Labor Condition Application disclosure data',
            'url'     => 'https://www.dol.gov/agencies/eta/foreign-labor/performance',
            'licence' => 'Public domain (U.S. Government work)',
            'cadence' => 'quarterly',
            'line'    => 'H-1B applications aggregated by employer, NAICS industry, worksite '
                       . 'state and county, and occupation family, with wage quartiles. '
                       . 'Aggregation is ours; no personal data is kept. Source: U.S. '
                       . 'Department of Labor, Office of Foreign Labor Certification.',
        ),
    );
}

function tit_reference_option($source) {
    return 'tit_reference_' . $source;
}

function tit_reference_register_routes() {
    $pattern = '/(?P<source>[a-z0-9_]{1,40})';
    register_rest_route('talent/v1', '/reference-ingest' . $pattern, array(
        'methods'  => 'POST',
        'callback' => 'tit_api_reference_ingest',
        'permission_callback' => function_exists('tit_api_permission')
            ? 'tit_api_permission' : '__return_false',
    ));
    register_rest_route('talent/v1', '/reference' . $pattern, array(
        'methods'  => 'GET',
        'callback' => 'tit_api_reference_get',
        'permission_callback' => '__return_true',
    ));
}
add_action('rest_api_init', 'tit_reference_register_routes');

function tit_api_reference_ingest(WP_REST_Request $req) {
    $source = (string) $req['source'];
    if (!array_key_exists($source, tit_reference_sources())) {
        return new WP_Error('tit_reference_unknown', 'Unknown reference source.', array('status' => 404));
    }
    $body = $req->get_json_params();
    if (!is_array($body) || empty($body['as_of']) || empty($body['licence'])
        || empty($body['attribution']) || empty($body['source_url'])) {
        return new WP_Error('tit_reference_unsourced',
            'A reference payload needs as_of, licence, attribution and source_url.',
            array('status' => 400));
    }
    $body['stored_at'] = gmdate('c');
    // Not autoloaded: these are read by one route, never on every page.
    update_option(tit_reference_option($source), $body, false);
    return rest_ensure_response(array(
        'stored' => true,
        'source' => $source,
        'as_of'  => sanitize_text_field((string) $body['as_of']),
    ));
}

function tit_api_reference_get(WP_REST_Request $req) {
    $source = (string) $req['source'];
    if (!array_key_exists($source, tit_reference_sources())) {
        return new WP_Error('tit_reference_unknown', 'Unknown reference source.', array('status' => 404));
    }
    $stored = get_option(tit_reference_option($source));
    if (!is_array($stored)) {
        return new WP_Error('tit_reference_empty', 'Nothing stored yet.', array('status' => 404));
    }
    return rest_ensure_response($stored);
}

/** The attribution block for the Sources page. Escaped here. */
function tit_reference_attribution_html() {
    $out = '<h2>Reference Data</h2><p class="tit-note">External context series. '
         . 'They are shown beside the tracker\'s records and never counted into them.</p><ul>';
    foreach (tit_reference_sources() as $s) {
        $out .= '<li><a href="' . esc_url($s['url']) . '" rel="nofollow noopener" target="_blank">'
              . esc_html($s['name']) . '</a> (' . esc_html($s['cadence']) . ', '
              . esc_html($s['licence']) . '). ' . esc_html($s['line']) . '</li>';
    }
    return $out . '</ul>';
}
