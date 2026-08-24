<?php
/**
 * US job-postings macro backdrop, from Indeed Hiring Lab.
 *
 * This is CONTEXT, not the tracker's own data. Indeed Hiring Lab publishes the
 * US Job Postings Index (level of postings on Indeed, 100 = Feb 1 2020) and the
 * share of US postings that mention AI, as free CC BY 4.0 CSVs. The panel shows
 * that macro backdrop next to the tracker's per-employer signals so a reader can
 * frame them -- and it is built to be impossible to confuse with our own counts:
 *
 * 1. **It is never our number.** The section is separately headed, separately
 *    sourced, and says in words that it is external and not counted in the
 *    tracker's figures. Nothing here is summed into a signal total, a YTD figure
 *    or a coverage number.
 * 2. **The value is Indeed's; only the comparisons are ours.** The index and the
 *    AI share are shown exactly as Indeed published them. The change vs the 2020
 *    baseline and vs a month earlier are computed by us from the same series,
 *    which the licence asks us to state, so the footer does.
 * 3. **Staleness is visible.** Each series carries its own real "as of" date, so
 *    a reader always sees how current the backdrop is -- the AI series lags the
 *    index by a few weeks and says so.
 *
 * Data arrives the same way the recall and board-series pages' does: a shipped
 * JSON seed with a keyed endpoint that overrides it, because the plugin deploy is
 * deliberately not armed on push and a file-only page would freeze at
 * shipping-day values. build_indeed_index.py --publish refreshes it.
 */

if (!defined('ABSPATH')) exit;

/**
 * The backdrop, option first and shipped file as the seed.
 */
function tit_indeed_index_data() {
    $stored = get_option('tit_indeed_index');
    if (is_array($stored) && !empty($stored['national'])) return $stored;

    $file = TIT_PATH . 'data/indeed_index.json';
    if (!is_readable($file)) return array();
    $data = json_decode(file_get_contents($file), true);
    return is_array($data) ? $data : array();
}

/** A signed, fixed-decimal change like "+1.8" or "-0.3". */
function tit_indeed_signed($value, $decimals = 1) {
    $n = (float) $value;
    // A real minus sign (U+2212) rather than a hyphen for a negative change.
    $sign = $n > 0 ? '+' : ($n < 0 ? "\xE2\x88\x92" : '');
    return $sign . number_format_i18n(abs($n), $decimals);
}

/**
 * A sparkline as inline SVG. No library, no request, no script: it renders
 * inside a cached page on shared hosting, exactly like the board-series one.
 */
function tit_indeed_sparkline($series, $label, $width = 320, $height = 48) {
    $values = array();
    foreach ((array) $series as $point) {
        if (is_array($point) && isset($point[1]) && is_numeric($point[1])) {
            $values[] = (float) $point[1];
        }
    }
    $count = count($values);
    if ($count < 2) return '';

    $min = min($values);
    $max = max($values);
    $span = max(0.0001, $max - $min);
    $points = array();
    foreach ($values as $i => $value) {
        $x = round($i * ($width - 2) / ($count - 1) + 1, 1);
        $y = round($height - 3 - (($value - $min) / $span) * ($height - 6), 1);
        $points[] = $x . ',' . $y;
    }

    return '<svg class="tit-spark" viewBox="0 0 ' . (int) $width . ' ' . (int) $height . '" '
         . 'width="100%" height="' . (int) $height . '" role="img" preserveAspectRatio="none" '
         . 'aria-label="' . esc_attr($label) . '">'
         . '<polyline fill="none" stroke="currentColor" stroke-width="2" '
         . 'stroke-linejoin="round" stroke-linecap="round" points="'
         . esc_attr(implode(' ', $points)) . '" /></svg>';
}

/**
 * The panel. Returns HTML, or '' when we hold no backdrop -- an empty shell
 * would imply a market with no postings, which is a claim we have not made.
 */
function tit_indeed_index_panel() {
    $data = tit_indeed_index_data();
    $national = is_array($data['national'] ?? null) ? $data['national'] : array();
    if (empty($national['index']) && $national['index'] !== 0) return '';

    $ai = is_array($data['ai'] ?? null) ? $data['ai'] : array();

    $index      = (float) $national['index'];
    $vs_base    = $national['vs_baseline'] ?? null;
    $n_asof     = $national['as_of'] ?? '';
    $n_month    = is_array($national['month_ago'] ?? null) ? $national['month_ago'] : array();
    $n_series   = is_array($national['series'] ?? null) ? $national['series'] : array();
    $n_src      = $national['source_url'] ?? '';
    $n_src_name = $national['source_name'] ?: 'Indeed Hiring Lab';

    $share      = isset($ai['share_pct']) ? (float) $ai['share_pct'] : null;
    $ai_asof    = $ai['as_of'] ?? '';
    $ai_month   = is_array($ai['month_ago'] ?? null) ? $ai['month_ago'] : array();
    $ai_series  = is_array($ai['series'] ?? null) ? $ai['series'] : array();
    $ai_src     = $ai['source_url'] ?? '';

    ob_start(); ?>
    <section class="tit-macro" id="tit-macro" aria-labelledby="tit-macro-h">
      <div class="tit-macro-head">
        <h2 class="tit-h2" id="tit-macro-h">Hiring demand across the US market</h2>
        <p class="tit-macro-sub">
          External context from Indeed Hiring Lab, not the tracker's own records.
          These figures describe the whole US labour market and are not counted in
          the numbers above.
        </p>
      </div>

      <div class="tit-stats tit-macro-stats">
        <div class="tit-stat">
          <span class="tit-n"><?php echo esc_html(number_format_i18n($index, 1)); ?></span>
          <span class="tit-l">Indeed Job Postings Index<?php
            if ($vs_base !== null) : ?><br><?php
              echo esc_html(tit_indeed_signed($vs_base) . ' vs Feb 2020 (=100)'); ?><?php
            endif; ?></span>
        </div>
        <div class="tit-stat">
          <span class="tit-n"><?php
            echo esc_html(!empty($n_month) ? tit_indeed_signed($n_month['delta']) : 'n/a'); ?></span>
          <span class="tit-l">Index change vs a month earlier<?php
            if (!empty($n_month['date'])) : ?><br><?php
              echo esc_html('from ' . $n_month['date']); ?><?php endif; ?></span>
        </div>
        <div class="tit-stat">
          <span class="tit-n"><?php
            echo esc_html($share !== null ? number_format_i18n($share, 1) . '%' : 'n/a'); ?></span>
          <span class="tit-l">Share of US postings mentioning AI<?php
            if ($ai_asof) : ?><br><?php echo esc_html('as of ' . $ai_asof); ?><?php endif; ?></span>
        </div>
        <div class="tit-stat">
          <span class="tit-n"><?php
            echo esc_html(!empty($ai_month) ? tit_indeed_signed($ai_month['delta']) . ' pt' : 'n/a'); ?></span>
          <span class="tit-l">AI share change vs a month earlier<?php
            if (!empty($ai_month['date'])) : ?><br><?php
              echo esc_html('from ' . $ai_month['date']); ?><?php endif; ?></span>
        </div>
      </div>

      <?php $spark = tit_indeed_sparkline($n_series,
              sprintf('US Job Postings Index, last %d readings', count($n_series)));
            if ($spark) : ?>
        <div class="tit-macro-spark">
          <?php echo $spark; // built above, already escaped ?>
          <span class="tit-macro-spark-l">Job Postings Index, trailing <?php
            echo esc_html((string) count($n_series)); ?> days to <?php
            echo esc_html($n_asof); ?></span>
        </div>
      <?php endif; ?>

      <p class="tit-event-meta">
        US job postings &middot; Source:
        <a href="<?php echo esc_url($n_src ?: $ai_src); ?>" rel="nofollow noopener" target="_blank"><?php
          echo esc_html($n_src_name); ?></a>
        (CC&nbsp;BY&nbsp;4.0) &middot; index as of <?php echo esc_html($n_asof); ?><?php
          if ($ai_asof) : ?>, AI share as of <?php echo esc_html($ai_asof); ?><?php endif; ?>.
      </p>
      <p class="tit-note"><?php echo esc_html($data['rule'] ?? ''); ?></p>
    </section>
    <?php
    return ob_get_clean();
}

/**
 * POST /talent/v1/indeed-index - keyed. How a scheduled build_indeed_index.py
 * --publish refreshes the backdrop without a plugin deploy.
 *
 * Registered in this file rather than api.php so the whole feature is one file
 * and a mistake in it cannot reach any other route, exactly as recall.php and
 * board_series.php do.
 *
 * It stores a sourced measurement, never a bare claim: the national block must
 * carry a numeric index, its "as of" date and the source URL that backs it. A
 * payload with no source or no date is rejected.
 */
function tit_indeed_index_register_route() {
    register_rest_route('talent/v1', '/indeed-index', array(
        'methods'  => 'POST',
        'callback' => 'tit_api_indeed_index',
        'permission_callback' => function_exists('tit_api_permission')
            ? 'tit_api_permission' : '__return_false',
    ));
}
add_action('rest_api_init', 'tit_indeed_index_register_route');

function tit_api_indeed_index(WP_REST_Request $req) {
    $body = $req->get_json_params();
    $national = is_array($body['national'] ?? null) ? $body['national'] : null;
    if (!is_array($body) || $national === null || empty($body['as_of'])
        || empty($body['rule'])) {
        return new WP_Error('tit_indeed_bad_body',
            'The backdrop needs as_of, the rule it was read by, and a national block.',
            array('status' => 400));
    }
    if (!isset($national['index']) || !is_numeric($national['index'])
        || empty($national['as_of']) || empty($national['source_url'])) {
        return new WP_Error('tit_indeed_unsourced',
            'The national index needs a numeric value, its as_of date and the '
            . 'source URL it was counted from: a number with no source is not a measurement.',
            array('status' => 400));
    }

    update_option('tit_indeed_index', $body, false);
    if (function_exists('tit_flush_caches')) tit_flush_caches();

    return rest_ensure_response(array(
        'stored' => true,
        'as_of'  => sanitize_text_field((string) $body['as_of']),
        'index'  => (float) $national['index'],
    ));
}
