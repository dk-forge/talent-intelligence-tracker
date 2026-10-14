<?php
/**
 * Server-rendered weekly totals under the market chart (1.104.0,
 * ai-layoff-tracker#446 item 1, SEO route B).
 *
 * The chart is an SVG whose figures sit in an aria-label and <title>s, which a
 * crawler does not read as page text. This prints the SAME tit_market_trend()
 * array the chart draws as a plain table, so the two cannot disagree: same
 * twelve whole weeks, same panel restriction in the counts variant, same
 * all-collector basis in the share variant (where the chart draws shares of
 * these counts). When the chart refuses to draw ('none'), so does the table.
 */
if (!defined('ABSPATH')) exit;

/** Pure: the table markup, newest week first. '' when the chart draws nothing. */
function tit_market_totals_table_html(array $m) {
    $weeks = $m['weeks'] ?? array();
    $variant = $m['variant'] ?? 'none';
    if ($variant === 'none' || !$weeks) return '';
    $caption = $variant === 'counts'
        ? sprintf('Updates a week by stated headcount direction, counted from the fixed panel of %d sources live for the whole window: the figures the chart above draws.', (int) ($m['panel_size'] ?? 0))
        : 'Updates a week by stated headcount direction, across all sources: the counts behind the chart above, which draws each week as shares of its own updates.';
    $h = '<div class="tit-table-scroll tit-market-totals"><table class="tit-market-totals-table">'
       . '<caption>' . esc_html($caption) . '</caption>'
       . '<thead><tr><th scope="col">Week</th><th scope="col">Adding roles</th>'
       . '<th scope="col">Cutting roles</th><th scope="col">Headcount not stated</th>'
       . '<th scope="col">Total</th></tr></thead><tbody>';
    foreach (array_reverse(array_values($weeks)) as $w) {
        $h .= '<tr><th scope="row">' . esc_html('Week of ' . $w['lo']) . '</th>';
        foreach (array('g', 's', 'u', 'n') as $k) {
            $h .= '<td>' . esc_html(number_format_i18n((int) ($w[$k] ?? 0))) . '</td>';
        }
        $h .= '</tr>';
    }
    return $h . '</tbody></table></div>';
}
