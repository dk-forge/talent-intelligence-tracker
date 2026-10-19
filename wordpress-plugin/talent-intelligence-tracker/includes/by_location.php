<?php
/**
 * BY LOCATION AND TOP ROUNDS (owner-approved 2026-10-19).
 *
 * The time board answers "how much, when". This answers "how much, WHERE":
 * places down the side, the same measures across the top, for one of the
 * board's own four windows (tit_glance_periods). And beside it, the ten
 * largest company raises in that window and place, each with its source.
 *
 * READ ONLY. Nothing here writes. It is reached through /aggregate with
 * include=geo, under the caller's own filters (tit_build_where), so the table
 * and the page above it cannot describe different sets of rows.
 *
 * ONE DEFINITION OF EVERY MEASURE. The count predicates come from
 * tit_signal_defs() and the money from tit_money_where(), exactly as the time
 * board's do. A location column that counted "Funding Rounds" its own way
 * would make a row of this table disagree with the board's cell for the same
 * window, and nobody could tell which was right.
 */

if (!defined('ABSPATH')) exit;

/** The levels the table groups by. Closed vocabulary, validated in the API. */
function tit_geo_levels() {
    return array('region', 'country', 'city');
}

/** Period tokens, in tit_glance_periods() order. Used in URLs and the API. */
function tit_geo_period_keys() {
    return array('week', 'month', 'quarter', 'ytd');
}

/** [label, start date] for a period token, or the YTD window when unknown. */
function tit_geo_period($key) {
    $periods = tit_glance_periods();
    $i = array_search((string) $key, tit_geo_period_keys(), true);
    if ($i === false) $i = 3;
    return $periods[$i];
}

/**
 * The per-place table. Returns rows of
 *   key, label, codes (regions only), country (cities only),
 *   rounds, money, hiring, leadership, pay, total
 * sorted by total, largest first. The client re-sorts; the server's order is
 * only the default.
 */
function tit_by_location($table, $where, array $params, $level, $period_key) {
    global $wpdb;
    if (!in_array($level, tit_geo_levels(), true)) return array();
    $period = tit_geo_period($period_key);
    $date_expr = tit_signal_date_expr();

    $pred = array();
    foreach (tit_signal_defs() as $d) {
        $pred[$d[0]] = $d[3];
    }
    $money = tit_money_where();

    $key_expr = $level === 'city' ? tit_city_expr() : tit_country_expr();
    $extra = $level === 'city' ? ', MAX(' . tit_country_expr() . ') AS cc' : '';
    $sql = "SELECT {$key_expr} AS k{$extra},
                   SUM(({$pred['funded']})) AS rounds,
                   COALESCE(SUM(CASE WHEN {$money} THEN funding_amount_usd END), 0) AS money,
                   SUM(({$pred['hiring']})) AS hiring,
                   SUM(({$pred['leadership']})) AS leadership,
                   SUM(({$pred['pay']})) AS pay,
                   COUNT(*) AS total
              FROM {$table}
             WHERE {$where} AND {$date_expr} >= %s
               AND {$key_expr} IS NOT NULL AND {$key_expr} <> ''
             GROUP BY k ORDER BY total DESC LIMIT 1000";
    $res = $wpdb->get_results($wpdb->prepare($sql, array_merge($params, array($period[1]))), ARRAY_A) ?: array();

    $num = function (array $r) {
        return array(
            'rounds'     => (int) $r['rounds'],
            'money'      => (int) $r['money'],
            'hiring'     => (int) $r['hiring'],
            'leadership' => (int) $r['leadership'],
            'pay'        => (int) $r['pay'],
            'total'      => (int) $r['total'],
        );
    };

    $rows = array();
    if ($level === 'region') {
        // Fold countries into the strip's own regions, so tapping a region row
        // applies exactly the tab the reader would have pressed.
        $by_code = array();
        foreach ($res as $r) $by_code[strtoupper((string) $r['k'])] = $num($r);
        foreach (tit_region_defs() as [$name, $codes]) {
            if ($codes === '') continue;
            $sum = array('rounds' => 0, 'money' => 0, 'hiring' => 0,
                         'leadership' => 0, 'pay' => 0, 'total' => 0);
            foreach (explode(',', $codes) as $c) {
                if (!isset($by_code[$c])) continue;
                foreach ($sum as $f => $v) $sum[$f] += $by_code[$c][$f];
            }
            if ($sum['total'] > 0) {
                $rows[] = array('key' => $name, 'label' => $name, 'codes' => $codes) + $sum;
            }
        }
        usort($rows, function ($a, $b) { return $b['total'] <=> $a['total']; });
    } else {
        foreach ($res as $r) {
            $row = array('key' => (string) $r['k'], 'label' => (string) $r['k']);
            if ($level === 'country') {
                $row['key'] = strtoupper($row['key']);
                $row['label'] = function_exists('tit_country_name')
                    ? tit_country_name($row['key']) : $row['key'];
            } else {
                $row['country'] = strtoupper((string) ($r['cc'] ?? ''));
            }
            $rows[] = $row + $num($r);
        }
    }

    return array('level' => $level, 'period' => $period[0], 'since' => $period[1],
                 'rows' => $rows);
}

/**
 * The ten largest company raises in the window, under the caller's filters.
 * company_raise only, by name (tit_money_where), so a divestiture price or a
 * fund close can never top this list. Every row carries its source link.
 */
function tit_top_rounds($table, $where, array $params, $period_key, $limit = 10) {
    global $wpdb;
    $period = tit_geo_period($period_key);
    $date_expr = tit_signal_date_expr();
    $money = tit_money_where();
    $sql = "SELECT company, company_key, funding_amount_usd AS amount,
                   " . tit_city_expr() . " AS city, " . tit_country_expr() . " AS country,
                   {$date_expr} AS d, source_url
              FROM {$table}
             WHERE {$where} AND {$money} AND funding_amount_usd IS NOT NULL
               AND funding_amount_usd > 0 AND {$date_expr} >= %s
             ORDER BY funding_amount_usd DESC, d DESC LIMIT %d";
    $res = $wpdb->get_results($wpdb->prepare($sql, array_merge($params, array($period[1], (int) $limit))), ARRAY_A) ?: array();
    $out = array();
    foreach ($res as $r) {
        $out[] = array(
            'company' => (string) $r['company'],
            'amount'  => (int) $r['amount'],
            'city'    => (string) ($r['city'] ?? ''),
            'country' => strtoupper((string) ($r['country'] ?? '')),
            'date'    => (string) $r['d'],
            'source_url' => (string) $r['source_url'],
        ) + tit_funded_links((string) ($r['company_key'] ?? ''), (string) $r['company']);
    }
    return $out;
}

/**
 * Where a reader goes next from a funded company: its tracker page and its jobs.
 *
 * NO GUESSED DOMAINS. jobs_kind 'board' is the employer's OWN job board, a URL
 * the ats_boards collector actually read (board_series.json / its option).
 * Anything else is jobs_kind 'search', a LinkedIn jobs SEARCH for the name,
 * which the card labels "Search jobs" rather than "See jobs" so it never reads
 * as the company's careers page. company_url is set only when the company page
 * is servable, so the card never links a 404.
 */
function tit_funded_links($company_key, $company) {
    $out = array('company_url' => '', 'jobs_url' => '', 'jobs_kind' => '');
    if ($company_key !== '' && function_exists('tit_company_url')
            && function_exists('tit_company_servable_slug') && tit_company_servable_slug($company_key)) {
        $out['company_url'] = tit_company_url($company_key);
    }
    if ($company_key !== '' && function_exists('tit_board_series_data')) {
        $data = tit_board_series_data();
        $boards = isset($data['boards'][$company_key]) && is_array($data['boards'][$company_key])
            ? $data['boards'][$company_key] : array();
        foreach ($boards as $b) {
            $u = isset($b['source_url']) ? (string) $b['source_url'] : '';
            if (preg_match('#^https://[^\s"<>]+$#', $u)) {
                $out['jobs_url'] = $u;
                $out['jobs_kind'] = 'board';
                return $out;
            }
        }
    }
    $name = trim($company);
    if ($name !== '') {
        $out['jobs_url'] = 'https://www.linkedin.com/jobs/search/?keywords=' . rawurlencode($name);
        $out['jobs_kind'] = 'search';
    }
    return $out;
}
