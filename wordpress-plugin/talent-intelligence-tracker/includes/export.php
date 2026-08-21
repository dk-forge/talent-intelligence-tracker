<?php
/**
 * CSV + JSON downloads via admin-post.php (the nopriv hooks make them work for
 * logged-out visitors, which is everyone).
 *
 * Exports honor the SAME filter params as /query by reusing tit_build_where,
 * so "download what I am looking at" is exactly the table's own WHERE clause
 * (is_current = 1 included) and can never drift from it. Rows stream in
 * row_id-keyed chunks so a large table never sits in memory.
 */

if (!defined('ABSPATH')) exit;

add_action('admin_post_tit_export_csv', 'tit_export_csv');
add_action('admin_post_nopriv_tit_export_csv', 'tit_export_csv');
add_action('admin_post_tit_export_json', 'tit_export_json');
add_action('admin_post_nopriv_tit_export_json', 'tit_export_json');

/**
 * Neutralize spreadsheet formula injection: a cell starting with = + - @
 * would execute as a formula when the CSV is opened in Excel or Sheets.
 */
function tit_csv_guard($value) {
    $value = (string) $value;
    if ($value === '') return $value;
    // TAB and CR belong in this list because Excel and LibreOffice STRIP
    // leading whitespace before deciding what a cell IS. So "\t=cmd|..." is
    // read as "=cmd|...", and the guard that only inspected $value[0] saw a
    // tab, judged it harmless, and passed the formula through unchanged. A
    // leading CR additionally lets a value forge a row break inside a quoted
    // field. Defence in depth: every value reaching here is normalised through
    // a fixed vocabulary or is a number. Depth is the point.
    $lead = ltrim($value, " \t\r\n");
    if ($lead !== '' && in_array($lead[0], array('=', '+', '-', '@'), true)) {
        return "'" . $value;
    }
    if (in_array($value[0], array('=', '+', '-', '@', "\t", "\r"), true)) {
        return "'" . $value;
    }
    return $value;
}

/**
 * FTP deploys land files one at a time, so api.php can be missing for the few
 * seconds this file already exists. Refuse politely instead of fatalling.
 */
function tit_export_ready() {
    if (function_exists('tit_build_where') && function_exists('tit_table_name')) {
        return;
    }
    status_header(503);
    nocache_headers();
    header('Content-Type: text/plain; charset=utf-8');
    echo 'Export is briefly unavailable while an update lands. Try again in a minute.';
    exit;
}

/** The request's filters, as the exact WHERE clause /query would use. */
function tit_export_filters() {
    $req = new WP_REST_Request('GET');
    $params = wp_unslash($_GET);
    unset($params['action']);
    $req->set_query_params($params);
    // An unknown filter value refuses the DOWNLOAD too. A CSV is the one
    // surface where the mistake outlives the request: the file is saved,
    // mailed on and analysed, with nothing on it to say the filter in its
    // filename never reached the query. Plain text and a 400, because this
    // route is admin_post and not REST, and because a browser is what asked.
    if (function_exists('tit_validate_filters')) {
        $invalid = tit_validate_filters($req);
        if (is_wp_error($invalid)) {
            status_header(400);
            nocache_headers();
            header('Content-Type: text/plain; charset=utf-8');
            echo $invalid->get_error_message() . "\n";
            exit;
        }
    }

    $out = array();
    // Every filter EXCEPT the detail control. A download is the complete
    // matching set: the page sets routine filings aside so a reader is not
    // buried, but a file someone is going to analyse should not arrive with
    // rows quietly missing. The materiality column ships in the export, so
    // anyone who wants the page's own view can reproduce it in one filter.
    $where = tit_build_where($req, $out, array('detail'));
    return array($where, $out);
}

function tit_export_is_filtered() {
    if (isset($_GET['country_basis']) && $_GET['country_basis'] === 'location') {
        return true;
    }
    $keys = array('country', 'city', 'pillar', 'direction', 'confidence', 'company',
        'industry', 'state', 'function', 'funding', 'since', 'until',
        'min_headcount', 'q', 'min_funding_usd', 'funding_stage',
        'stated_headcount');
    foreach ($keys as $k) {
        if (!empty($_GET[$k])) return true;
    }
    return false;
}

/** Iterate matching rows in row_id-keyed chunks, calling $cb($row) per row. */
function tit_export_walk($cb) {
    global $wpdb;
    $table = tit_table_name();
    list($where, $params) = tit_export_filters();
    $last = 0;
    while (true) {
        $sql = "SELECT * FROM {$table} WHERE ({$where}) AND row_id > %d ORDER BY row_id ASC LIMIT 2000";
        $rows = $wpdb->get_results($wpdb->prepare($sql, array_merge($params, array($last))));
        if (!$rows) break;
        foreach ($rows as $row) {
            $last = (int) $row->row_id;
            $cb($row);
        }
        if (count($rows) < 2000) break;
    }
}

/**
 * Lightweight per-IP throttle, mirroring the sibling tracker: reuse is welcome
 * (the data is CC BY 4.0), but repeated concurrent full-table downloads should
 * not be able to hammer the origin. 20 exports / 10 min per IP.
 */
function tit_export_throttle() {
    $ip = isset($_SERVER['REMOTE_ADDR']) ? preg_replace('/[^0-9a-f:.]/i', '', (string) $_SERVER['REMOTE_ADDR']) : '0';
    // An OPTION, via tit_ephemeral_* in db.php, not a transient. As a transient
    // this key matched `_transient_tit_%`, so tit_flush_caches() deleted it on
    // every write route - four-plus times a day - and the counter a caller had
    // to stay under was reset for them, on a schedule, by our own collectors.
    // A throttle that resets four times a day is not a throttle.
    // Same FTP-race degradation as every other cross-file call here: db.php can
    // be missing for a few seconds mid-upload, and an unthrottled export for
    // those seconds is a far better outcome than a fatal on the route.
    if (!function_exists('tit_ephemeral_get')) return;
    $key = 'export_rl_' . md5($ip);
    $n = (int) tit_ephemeral_get($key);
    if ($n >= 20) {
        status_header(429);
        nocache_headers();
        header('Retry-After: 600');
        header('Content-Type: text/plain; charset=utf-8');
        echo 'Export rate limit reached. The data is free to reuse (CC BY 4.0). Please wait a few minutes, filter your export, or use the public API.';
        exit;
    }
    tit_ephemeral_set($key, $n + 1, 10 * MINUTE_IN_SECONDS);
}

function tit_export_filename($ext) {
    return 'talent-intelligence-tracker-' . (tit_export_is_filtered() ? 'filtered-' : '')
        . gmdate('Y-m-d') . '.' . $ext;
}

/** functions is stored as a JSON array; export it as a readable list. */
function tit_export_functions_list($raw) {
    $arr = json_decode((string) $raw, true);
    return is_array($arr) ? array_values(array_map('strval', $arr)) : array();
}

function tit_export_csv() {
    tit_export_ready();
    tit_export_throttle();
    nocache_headers();
    header('Content-Type: text/csv; charset=utf-8');
    header('Content-Disposition: attachment; filename="' . tit_export_filename('csv') . '"');

    $out = fopen('php://output', 'w');

    // UTF-8 BOM so Excel renders accents and CJK correctly instead of mojibake.
    fwrite($out, "\xEF\xBB\xBF");

    fputcsv($out, array(
        'signal_id', 'company', 'headline', 'summary', 'talent_readthrough',
        'pillar', 'signal_direction', 'roles_affected', 'industry',
        'city', 'region', 'country', 'hq_city', 'hq_country', 'us_state',
        // funding_amount is the source's own wording; funding_amount_usd is the
        // number. A download that only carried the string would leave the
        // person who took it doing the parsing we already did.
        // money_basis says WHY a figure is or is not in the published total.
        // A download that carried the amount without it would let somebody sum
        // the column and get the number this change exists to correct.
        'headcount', 'funding_amount', 'funding_amount_usd', 'funding_stage',
        'money_basis',
        'materiality', 'confidence',
        'source_name', 'source_url', 'archive_url',
        'published_date', 'captured_at',
        'predicted_outcome', 'check_after_date', 'outcome_observed',
        'source_attribution',
    ));

    tit_export_walk(function ($row) use ($out) {
        fputcsv($out, array(
            tit_csv_guard($row->signal_id),
            tit_csv_guard($row->company),
            tit_csv_guard($row->headline),
            tit_csv_guard($row->summary),
            tit_csv_guard($row->talent_readthrough),
            tit_csv_guard($row->pillar),
            tit_csv_guard($row->signal_direction),
            tit_csv_guard(implode('|', tit_export_functions_list($row->functions ?? ''))),
            tit_csv_guard((string) $row->industry),
            tit_csv_guard((string) $row->city),
            tit_csv_guard((string) $row->region),
            tit_csv_guard((string) $row->country),
            tit_csv_guard((string) $row->hq_city),
            tit_csv_guard((string) $row->hq_country),
            tit_csv_guard((string) $row->state),
            $row->headcount === null ? '' : (int) $row->headcount,
            tit_csv_guard((string) $row->funding_amount),
            ($row->funding_amount_usd === null || $row->funding_amount_usd === '')
                ? '' : (int) $row->funding_amount_usd,
            tit_csv_guard((string) $row->funding_stage),
            tit_csv_guard((string) $row->money_basis),
            tit_csv_guard((string) $row->materiality),
            tit_csv_guard($row->confidence),
            tit_csv_guard($row->source_name),
            tit_csv_guard((string) $row->source_url),
            tit_csv_guard((string) $row->archive_url),
            $row->published_date ?: '',
            (string) $row->captured_at,
            tit_csv_guard((string) $row->predicted_outcome),
            $row->check_after_date ?: '',
            tit_csv_guard((string) $row->outcome_observed),
            'Talent Intelligence Tracker - asktherecruiter.com - CC BY 4.0',
        ));
    });

    fclose($out);
    exit;
}

function tit_export_json() {
    tit_export_ready();
    tit_export_throttle();
    nocache_headers();
    header('Content-Type: application/json; charset=utf-8');
    header('Content-Disposition: attachment; filename="' . tit_export_filename('json') . '"');

    echo '{';
    echo '"source":"Talent Intelligence Tracker - asktherecruiter.com",';
    echo '"license":"CC BY 4.0. Free to use with attribution to asktherecruiter.com.",';
    echo '"source_url":' . wp_json_encode(home_url('/talent-intelligence-tracker/')) . ',';
    echo '"generated":"' . gmdate('Y-m-d\TH:i:s\Z') . '",';
    echo '"data":[';

    // Count while streaming (rather than a COUNT(*) up front) so total_records
    // always equals the rows actually emitted, even if writes land mid-export.
    $count = 0;
    tit_export_walk(function ($row) use (&$count) {
        // The same public fields /query returns, so the download and the API
        // describe every record identically.
        $entry = array(
            'signal_id'          => $row->signal_id,
            'headline'           => $row->headline,
            'summary'            => $row->summary,
            'talent_readthrough' => $row->talent_readthrough,
            'company'            => $row->company,
            'pillar'             => $row->pillar,
            'signal_direction'   => $row->signal_direction,
            'city'               => $row->city,
            'region'             => $row->region,
            'country'            => $row->country,
            'hq_city'            => $row->hq_city,
            'hq_country'         => $row->hq_country,
            'state'              => $row->state,
            'functions'          => tit_export_functions_list($row->functions ?? ''),
            'industry'           => $row->industry,
            'headcount'          => $row->headcount === null ? null : (int) $row->headcount,
            'funding_amount'     => $row->funding_amount,
            // The numeric companion, and null (never 0) when the stated figure
            // was not in US dollars. A zero would read as a round of nothing.
            'funding_amount_usd' => ($row->funding_amount_usd === null || $row->funding_amount_usd === '')
                                    ? null : (int) $row->funding_amount_usd,
            'funding_stage'      => $row->funding_stage,
            // Why this amount is or is not in the site's money total. NULL
            // means never judged, which is a third state and not a yes.
            'money_basis'        => $row->money_basis,
            'predicted_outcome'  => $row->predicted_outcome,
            'check_after_date'   => $row->check_after_date,
            'outcome_observed'   => $row->outcome_observed,
            'archive_url'        => $row->archive_url,
            'materiality'        => $row->materiality,
            'confidence'         => $row->confidence,
            'source_url'         => $row->source_url,
            'source_name'        => $row->source_name,
            'published_date'     => $row->published_date,
            'captured_at'        => $row->captured_at,
        );
        echo ($count ? ',' : '') . wp_json_encode($entry, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        $count++;
    });

    echo '],"total_records":' . $count . '}';
    exit;
}
