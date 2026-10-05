<?php
/**
 * Reference data, RENDERED: Indeed Hiring Lab by country and occupation, and
 * DOL OFLC H-1B Labor Condition Applications.
 *
 * reference_data.php stores these and serves them as stored; this file is the
 * only place they are drawn. Three sections:
 *
 *   tit_hiring_demand_panel()      dashboard: postings index over time, up to
 *                                  six countries, one occupation category
 *   tit_h1b_sponsors_panel()       dashboard: top employers by H-1B filings,
 *                                  searchable, role and state filters
 *   tit_h1b_company_panel($rows)   company page: that employer's filings, on an
 *                                  EXACT name match only
 *
 * The same honesty rules as indeed_index.php:
 *
 * 1. Never our number. Each section is separately headed, separately sourced
 *    and says in words that it is external and not counted in the tracker.
 * 2. An LCA is a FILING, not a hire and not an approved visa. Every H-1B
 *    surface says so next to the number.
 * 3. No data, no section. Every panel returns '' when its option is missing or
 *    malformed: an empty chart would assert a market with no postings, and an
 *    empty H-1B box would assert an employer that files nothing.
 * 4. Exact match only. A company page shows H-1B figures when one of its
 *    stored company names is a key in the published `names` map (built by
 *    h1b_join.match() from the same normaliser on both sides). No fuzzy join:
 *    a near miss would put a stranger's visa filings on an employer's page.
 *
 * Server-rendered first (inline SVG, a table), so a cached page with no
 * script shows the default view. assets/reference.js only redraws when a
 * reader changes a control, reading the public /reference/<source> route; its
 * config is a data- attribute because Autoptimize sweeps inline objects (see
 * CLAUDE.md, Bluehost gotcha 10).
 */

if (!defined('ABSPATH')) exit;

/** Option name holding the h1b company detail, split off at ingest. */
const TIT_H1B_COMPANIES_OPTION = 'tit_reference_h1b_lca_companies';

/** Countries Indeed Hiring Lab publishes, in the reader's words. */
function tit_hd_country_names() {
    return array(
        'US' => 'United States', 'GB' => 'United Kingdom', 'CA' => 'Canada',
        'DE' => 'Germany', 'FR' => 'France', 'AU' => 'Australia',
        'IE' => 'Ireland', 'NL' => 'Netherlands', 'ES' => 'Spain',
        'IT' => 'Italy', 'EA' => 'Euro area',
    );
}

/** The six countries drawn before a reader picks. */
function tit_hd_default_countries() {
    return array('US', 'GB', 'CA', 'DE', 'FR', 'AU');
}

function tit_reference_stored($source) {
    $v = get_option('tit_reference_' . $source);
    return is_array($v) ? $v : array();
}

/**
 * A series as [[date, value], ...]. The 26 weekly points when the payload
 * carries them; otherwise the three points every payload does carry: a year
 * earlier, four weeks earlier and the latest, each reconstructed from the
 * published index minus the change we computed. Never a guess between them.
 */
function tit_hd_points($s) {
    if (!is_array($s) || !isset($s['index']) || !is_numeric($s['index']) || empty($s['as_of'])) {
        return array();
    }
    if (!empty($s['weekly']) && is_array($s['weekly'])) {
        $out = array();
        foreach ($s['weekly'] as $p) {
            if (is_array($p) && isset($p[0], $p[1]) && is_numeric($p[1])) {
                $out[] = array((string) $p[0], (float) $p[1]);
            }
        }
        if (count($out) >= 2) return $out;
    }
    $t = strtotime($s['as_of'] . ' 00:00:00 UTC');
    if ($t === false) return array();
    $idx = (float) $s['index'];
    $out = array();
    foreach (array('chg_52w' => 364, 'chg_4w' => 28) as $k => $days) {
        if (isset($s[$k]) && is_numeric($s[$k])) {
            $out[] = array(gmdate('Y-m-d', $t - $days * 86400), round($idx - (float) $s[$k], 2));
        }
    }
    $out[] = array((string) $s['as_of'], $idx);
    return $out;
}

/** The series for one country and one category ('' = all postings). */
function tit_hd_series($data, $cc, $category) {
    $block = $data['countries'][$cc] ?? null;
    if (!is_array($block)) return array();
    if ($category === '') return tit_hd_points($block['total'] ?? null);
    $cats = is_array($block['categories'] ?? null) ? $block['categories'] : array();
    return tit_hd_points($cats[$category] ?? null);
}

/**
 * A multi-line chart as inline SVG. One polyline per series, coloured by a
 * class (tokens in dashboard.css, so it follows the theme), with a min/max
 * axis label and the first and last date. Escaped here.
 */
function tit_hd_chart_svg(array $lines, $label) {
    $w = 640; $h = 240; $l = 44; $r = 10; $t = 12; $b = 26;
    $vals = array(); $dates = array();
    foreach ($lines as $line) {
        foreach ($line['points'] as $p) { $vals[] = $p[1]; $dates[$p[0]] = true; }
    }
    if (count($vals) < 2) return '';
    ksort($dates);
    $dates = array_keys($dates);
    $t0 = strtotime($dates[0] . ' UTC');
    $t1 = strtotime(end($dates) . ' UTC');
    $tspan = max(1, $t1 - $t0);
    $min = floor(min($vals) / 5) * 5;
    $max = ceil(max($vals) / 5) * 5;
    if ($max <= $min) $max = $min + 5;
    $x = function ($d) use ($t0, $tspan, $w, $l, $r) {
        return round($l + (strtotime($d . ' UTC') - $t0) / $tspan * ($w - $l - $r), 1);
    };
    $y = function ($v) use ($min, $max, $h, $t, $b) {
        return round($t + (1 - ($v - $min) / ($max - $min)) * ($h - $t - $b), 1);
    };
    $svg = '<svg class="tit-hd-svg" viewBox="0 0 ' . $w . ' ' . $h . '" width="100%" role="img" '
         . 'preserveAspectRatio="xMidYMid meet" aria-label="' . esc_attr($label) . '">';
    foreach (array($min, ($min + $max) / 2, $max) as $g) {
        $gy = $y($g);
        $svg .= '<line class="tit-hd-grid" x1="' . $l . '" x2="' . ($w - $r) . '" y1="' . $gy . '" y2="' . $gy . '"/>'
              . '<text class="tit-hd-ax" x="' . ($l - 6) . '" y="' . ($gy + 4) . '" text-anchor="end">'
              . esc_html(number_format((float) $g, 0)) . '</text>';
    }
    if ($min <= 100 && $max >= 100) {
        $by = $y(100);
        $svg .= '<line class="tit-hd-base" x1="' . $l . '" x2="' . ($w - $r) . '" y1="' . $by . '" y2="' . $by . '"/>';
    }
    $svg .= '<text class="tit-hd-ax" x="' . $l . '" y="' . ($h - 6) . '">' . esc_html($dates[0]) . '</text>'
          . '<text class="tit-hd-ax" x="' . ($w - $r) . '" y="' . ($h - 6) . '" text-anchor="end">'
          . esc_html(end($dates)) . '</text>';
    foreach ($lines as $i => $line) {
        $pts = array();
        foreach ($line['points'] as $p) $pts[] = $x($p[0]) . ',' . $y($p[1]);
        if (count($pts) < 2) continue;
        $svg .= '<polyline class="tit-hd-line tit-hd-s' . (int) $i . '" points="' . esc_attr(implode(' ', $pts)) . '"/>';
    }
    return $svg . '</svg>';
}

function tit_hd_signed($v) {
    if (!is_numeric($v)) return 'n/a';
    $n = (float) $v;
    return ($n > 0 ? '+' : ($n < 0 ? "\xE2\x88\x92" : '')) . number_format(abs($n), 1);
}

/** Lines + legend rows for a selection. */
function tit_hd_selection($data, array $countries, $category) {
    $names = tit_hd_country_names();
    $lines = array(); $missing = array();
    foreach ($countries as $cc) {
        $pts = tit_hd_series($data, $cc, $category);
        if (count($pts) < 2) { $missing[] = $names[$cc] ?? $cc; continue; }
        $block = $data['countries'][$cc];
        $s = $category === '' ? $block['total'] : $block['categories'][$category];
        $lines[] = array('cc' => $cc, 'name' => $names[$cc] ?? $cc, 'points' => $pts, 's' => $s);
    }
    return array($lines, $missing);
}

/** The "Hiring demand" section. '' when nothing is stored. */
function tit_hiring_demand_panel() {
    $data = tit_reference_stored('indeed_occupations');
    if (empty($data['countries']) || !is_array($data['countries'])) return '';
    $names = tit_hd_country_names();
    $available = array_values(array_filter(array_keys($names), function ($cc) use ($data) {
        return count(tit_hd_series($data, $cc, '')) >= 2;
    }));
    if (!$available) return '';
    $default = array_values(array_intersect(tit_hd_default_countries(), $available));
    if (!$default) $default = array_slice($available, 0, 6);

    $cats = array();
    foreach ($data['countries'] as $block) {
        if (is_array($block['categories'] ?? null)) {
            foreach ($block['categories'] as $n => $_) $cats[(string) $n] = true;
        }
    }
    ksort($cats, SORT_STRING | SORT_FLAG_CASE);

    list($lines, $missing) = tit_hd_selection($data, $default, '');
    $chart = tit_hd_chart_svg($lines, 'Indeed job postings index, all postings, '
        . implode(', ', array_column($lines, 'name')));
    if ($chart === '') return '';

    $licence = (string) ($data['licence'] ?? '');
    $src_url = (string) ($data['source_url'] ?? 'https://github.com/hiring-lab/job_postings_tracker');
    $as_of = (string) ($data['as_of'] ?? '');
    $weekly = !empty($lines[0]['points']) && count($lines[0]['points']) > 3;
    if (function_exists('tit_reference_enqueue_js')) tit_reference_enqueue_js();

    ob_start(); ?>
    <section class="tit-macro tit-hd" id="tit-hiring-demand" aria-labelledby="tit-hd-h"
             data-api="<?php echo esc_attr(esc_url_raw(rest_url('talent/v1/reference/indeed_occupations'))); ?>">
      <div class="tit-macro-head">
        <h2 class="tit-h2" id="tit-hd-h">Hiring demand by country and occupation</h2>
        <p class="tit-macro-sub">
          External context from Indeed Hiring Lab, not the tracker's own records.
          It measures job postings on Indeed, not hires, and is never counted in
          the numbers above.
        </p>
      </div>
      <form class="tit-hd-controls">
        <fieldset class="tit-hd-countries">
          <legend>Countries <span class="tit-hd-hint">(up to 6)</span></legend>
          <?php foreach ($available as $cc) : ?>
            <label class="tit-hd-chip"><input type="checkbox" name="tit-hd-c" value="<?php echo esc_attr($cc); ?>"<?php
              echo in_array($cc, $default, true) ? ' checked' : ''; ?>> <?php echo esc_html($names[$cc]); ?></label>
          <?php endforeach; ?>
        </fieldset>
        <?php if ($cats) : ?>
        <label class="tit-hd-occ-l" for="tit-hd-occ">Occupation category</label>
        <select id="tit-hd-occ" class="tit-hd-occ">
          <option value="">All postings</option>
          <?php foreach (array_keys($cats) as $c) : ?>
            <option value="<?php echo esc_attr($c); ?>"><?php echo esc_html($c); ?></option>
          <?php endforeach; ?>
        </select>
        <?php endif; ?>
      </form>
      <figure class="tit-hd-fig">
        <div class="tit-hd-chart"><?php echo $chart; // escaped in tit_hd_chart_svg ?></div>
        <ul class="tit-hd-legend">
          <?php foreach ($lines as $i => $ln) : ?>
            <li><span class="tit-hd-key tit-hd-k<?php echo (int) $i; ?>" aria-hidden="true"></span>
              <strong><?php echo esc_html($ln['name']); ?></strong>
              <?php echo esc_html(number_format((float) $ln['s']['index'], 1)); ?>
              <span class="tit-hd-chg"><?php echo esc_html(tit_hd_signed($ln['s']['chg_4w'] ?? null) . ' in 4 weeks, '
                . tit_hd_signed($ln['s']['chg_52w'] ?? null) . ' in a year'); ?></span></li>
          <?php endforeach; ?>
        </ul>
        <p class="tit-hd-missing"<?php echo $missing ? '' : ' hidden'; ?>><?php
          echo $missing ? esc_html('No published series for: ' . implode(', ', $missing) . '.') : ''; ?></p>
        <figcaption>
          <p class="tit-note">Index of job postings on Indeed, 100 = February 1, 2020,
          as Indeed Hiring Lab published it. Country totals are seasonally adjusted;
          occupation categories are not, and Indeed publishes them for only some
          countries. <?php echo $weekly
            ? 'Weekly points over the last six months.'
            : 'Three points: a year earlier, four weeks earlier and the latest reading.'; ?>
          The 4-week and 1-year changes are computed by us.</p>
          <p class="tit-event-meta">Source:
            <a href="<?php echo esc_url($src_url); ?>" rel="nofollow noopener" target="_blank">Indeed Hiring Lab</a>
            (<?php echo esc_html($licence !== '' ? $licence : 'CC BY 4.0'); ?>)<?php
            if ($as_of !== '') : ?> &middot; as of <?php echo esc_html($as_of); ?><?php endif; ?>.</p>
        </figcaption>
      </figure>
      <p class="tit-hd-status" role="status" aria-live="polite"></p>
    </section>
    <?php
    return ob_get_clean();
}

/* ------------------------------------------------------------------ H-1B */

/** "FY2026 through Q3 (Oct 1, 2025 to Jun 30, 2026)", or '' if unknown. */
function tit_h1b_period($data) {
    $fy = (int) ($data['fiscal_year'] ?? 0);
    $q  = (int) ($data['quarter'] ?? 0);
    if ($fy < 2000) {
        if (preg_match('/FY(\d{4})_Q(\d)/', (string) ($data['file'] ?? ''), $m)) {
            $fy = (int) $m[1]; $q = (int) $m[2];
        }
    }
    if ($fy < 2000 || $q < 1 || $q > 4) return '';
    $end = array(1 => 'Dec 31, ' . ($fy - 1), 2 => 'Mar 31, ' . $fy, 3 => 'Jun 30, ' . $fy, 4 => 'Sep 30, ' . $fy);
    return 'Fiscal year ' . $fy . ($q < 4 ? ' through Q' . $q : '')
         . ' (Oct 1, ' . ($fy - 1) . ' to ' . $end[$q] . ')';
}

function tit_h1b_money($v) {
    return is_numeric($v) && $v > 0 ? '$' . number_format((float) $v) : 'n/a';
}

/** The DOL source line every H-1B surface carries. */
function tit_h1b_source_html($data) {
    $url = (string) ($data['source_url'] ?? 'https://www.dol.gov/agencies/eta/foreign-labor/performance');
    $as_of = (string) ($data['as_of'] ?? '');
    return '<p class="tit-event-meta">Source: <a href="' . esc_url($url)
         . '" rel="nofollow noopener" target="_blank">U.S. Department of Labor, OFLC LCA disclosure data</a>'
         . ' (public domain)' . ($as_of !== '' ? ' &middot; as of ' . esc_html($as_of) : '') . '.</p>';
}

const TIT_H1B_FILINGS_NOTE = 'A Labor Condition Application is a filing an employer makes before '
    . 'it can sponsor an H-1B worker. It is not a hire and not an approved visa, and one filing '
    . 'can cover several positions.';

/** Top-N "name (n)" text from a {name: count} map. */
function tit_h1b_top_text($map, $n = 3) {
    if (!is_array($map)) return '';
    arsort($map);
    $out = array();
    foreach (array_slice($map, 0, $n, true) as $k => $v) {
        $out[] = $k . ' (' . number_format((int) $v) . ')';
    }
    return implode(', ', $out);
}

/** The company detail for these rows, or null. Exact name match only. */
function tit_h1b_company_detail(array $rows) {
    $store = get_option(TIT_H1B_COMPANIES_OPTION);
    if (!is_array($store) || !is_array($store['names'] ?? null) || !is_array($store['companies'] ?? null)) {
        return null;
    }
    foreach ($rows as $r) {
        $name = (string) ($r['company'] ?? '');
        if ($name === '' || !isset($store['names'][$name])) continue;
        $key = (string) $store['names'][$name];
        $d = $store['companies'][$key] ?? null;
        if (is_array($d) && !empty($d['cases'])) return $d;
    }
    return null;
}

/** The H-1B box on a company page. '' when there is no exact match. */
function tit_h1b_company_panel(array $rows) {
    $d = tit_h1b_company_detail($rows);
    if ($d === null) return '';
    $data = tit_reference_stored('h1b_lca');
    $period = tit_h1b_period($data);
    ob_start(); ?>
    <section class="tit-macro tit-h1b-co" id="tit-h1b" aria-labelledby="tit-h1b-h">
      <div class="tit-macro-head">
        <h2 class="tit-h2" id="tit-h1b-h">H-1B filings (Labor Condition Applications)</h2>
        <p class="tit-macro-sub"><?php echo esc_html(TIT_H1B_FILINGS_NOTE); ?>
          External data, not counted in the tracker's records.</p>
      </div>
      <div class="tit-stats tit-macro-stats">
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format((int) $d['certified'])); ?></span>
          <span class="tit-l">Certified H-1B LCAs<br><?php echo esc_html(number_format((int) $d['cases']) . ' filed in all'); ?></span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(tit_h1b_money($d['wage_median'] ?? null)); ?></span>
          <span class="tit-l">Median offered wage, a year<br>combined from per-role, per-worksite medians</span></div>
      </div>
      <dl class="tit-h1b-dl">
        <?php if (!empty($d['roles'])) : ?>
          <dt>Top roles (certified filings)</dt><dd><?php echo esc_html(tit_h1b_top_text($d['roles'])); ?></dd>
        <?php endif; ?>
        <?php if (!empty($d['states'])) : ?>
          <dt>Top work states</dt><dd><?php echo esc_html(tit_h1b_top_text($d['states'])); ?></dd>
        <?php endif; ?>
        <?php if ($period !== '') : ?>
          <dt>Period</dt><dd><?php echo esc_html($period); ?></dd>
        <?php endif; ?>
        <dt>Matched to</dt><dd><?php echo esc_html((string) ($d['employer'] ?? '')); ?>
          (exact employer-name match)</dd>
      </dl>
      <?php echo tit_h1b_source_html($data); // escaped inside ?>
    </section>
    <?php
    return ob_get_clean();
}

/** Rows shown before a reader filters; the full list arrives from the route. */
const TIT_H1B_SPONSOR_ROWS = 25;

/** The "H-1B sponsors" section. '' when nothing is stored. */
function tit_h1b_sponsors_panel() {
    $data = tit_reference_stored('h1b_lca');
    $top = is_array($data['top_employers'] ?? null) ? $data['top_employers'] : array();
    $top = array_values(array_filter($top, function ($e) {
        return is_array($e) && !empty($e['employer']) && isset($e['cases']);
    }));
    if (!$top) return '';
    $roles = array(); $states = array();
    foreach ($top as $e) {
        foreach ((array) ($e['roles'] ?? array()) as $k => $_) if ($k !== '') $roles[$k] = true;
        foreach ((array) ($e['states'] ?? array()) as $k => $_) if ($k !== '') $states[$k] = true;
    }
    ksort($roles); ksort($states);
    $period = tit_h1b_period($data);
    if (function_exists('tit_reference_enqueue_js')) tit_reference_enqueue_js();

    ob_start(); ?>
    <section class="tit-macro tit-h1b" id="tit-h1b-sponsors" aria-labelledby="tit-h1b-s-h"
             data-api="<?php echo esc_attr(esc_url_raw(rest_url('talent/v1/reference/h1b_lca'))); ?>"
             data-rows="<?php echo (int) TIT_H1B_SPONSOR_ROWS; ?>">
      <div class="tit-macro-head">
        <h2 class="tit-h2" id="tit-h1b-s-h">H-1B sponsors</h2>
        <p class="tit-macro-sub">Employers with the most H-1B Labor Condition Applications
          <?php echo $period !== '' ? esc_html('in ' . $period) : ''; ?>.
          <?php echo esc_html(TIT_H1B_FILINGS_NOTE); ?> External data, not counted in the
          tracker's records.</p>
      </div>
      <form class="tit-h1b-controls" role="search">
        <label for="tit-h1b-q">Search employers</label>
        <input id="tit-h1b-q" type="search" class="tit-h1b-q" autocomplete="off">
        <?php if ($roles) : ?>
          <label for="tit-h1b-role">Role</label>
          <select id="tit-h1b-role" class="tit-h1b-role"><option value="">All roles</option>
            <?php foreach (array_keys($roles) as $k) : ?><option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($k); ?></option><?php endforeach; ?>
          </select>
        <?php endif; ?>
        <?php if ($states) : ?>
          <label for="tit-h1b-state">Work state</label>
          <select id="tit-h1b-state" class="tit-h1b-state"><option value="">All states</option>
            <?php foreach (array_keys($states) as $k) : ?><option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($k); ?></option><?php endforeach; ?>
          </select>
        <?php endif; ?>
      </form>
      <table class="tit-h1b-table">
        <thead><tr><th scope="col">Employer</th><th scope="col" class="tit-num">LCAs filed</th>
          <th scope="col" class="tit-num">Certified</th><th scope="col" class="tit-h1b-opt">Top role</th>
          <th scope="col" class="tit-h1b-opt">Top state</th><th scope="col" class="tit-num tit-h1b-opt">Median wage</th></tr></thead>
        <tbody>
        <?php foreach (array_slice($top, 0, TIT_H1B_SPONSOR_ROWS) as $e) :
            $r = (array) ($e['roles'] ?? array()); arsort($r);
            $s = (array) ($e['states'] ?? array()); arsort($s); ?>
          <tr><td><?php echo esc_html($e['employer']); ?></td>
            <td class="tit-num"><?php echo esc_html(number_format((int) $e['cases'])); ?></td>
            <td class="tit-num"><?php echo esc_html(number_format((int) ($e['certified'] ?? 0))); ?></td>
            <td class="tit-h1b-opt"><?php echo esc_html((string) (key($r) ?? '')); ?></td>
            <td class="tit-h1b-opt"><?php echo esc_html((string) (key($s) ?? '')); ?></td>
            <td class="tit-num tit-h1b-opt"><?php echo esc_html(tit_h1b_money($e['wage_median'] ?? null)); ?></td></tr>
        <?php endforeach; ?>
        </tbody>
      </table>
      <p class="tit-hd-status" role="status" aria-live="polite"></p>
      <p class="tit-note">Ranked by H-1B applications filed. Role and state filters use each
        employer's top five roles and work states by certified filings. Employer names are
        grouped by a normalised spelling; the wage is an annual median combined from
        per-role, per-worksite medians.</p>
      <?php echo tit_h1b_source_html($data); // escaped inside ?>
    </section>
    <?php
    return ob_get_clean();
}

/** One small deferred script for both dashboard sections. */
function tit_reference_enqueue_js() {
    if (!function_exists('wp_enqueue_script')) return;
    $v = function_exists('tit_asset_version') ? tit_asset_version('assets/reference.js') : TIT_VERSION;
    wp_enqueue_script('tit-reference', TIT_URL . 'assets/reference.js', array(), $v, true);
    if (function_exists('wp_script_add_data')) wp_script_add_data('tit-reference', 'strategy', 'defer');
}
