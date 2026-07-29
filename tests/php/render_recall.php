<?php
/**
 * Render the recall page's chart and history table outside WordPress.
 *
 * The trend only draws once there are two measurements, so on the day it ships
 * there is exactly one point and the chart cannot be checked on the live page.
 * The alternative was to publish a fabricated second point to look at it, which
 * is not something this project gets to do. So it is exercised here instead,
 * with synthetic points, in CI, on every push.
 *
 * WordPress is not loaded. recall.php registers hooks and escapes output at
 * load time, so the few functions it needs are stubbed with honest equivalents:
 * the escapers really escape, so a missing esc_attr would still show up as
 * broken markup here.
 *
 * Exits non-zero with a message on any failure. Run: php tests/php/render_recall.php
 */

define('ABSPATH', __DIR__);
define('TIT_PATH', __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/');
define('TIT_VERSION', 'test');

function add_action($h, $f, $p = 10, $a = 1) {}
function add_filter($h, $f, $p = 10, $a = 1) {}
function add_rewrite_rule($r, $q, $w = 'bottom') {}
function home_url($path = '') { return 'https://example.test' . $path; }
function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function number_format_i18n($n) { return number_format((float) $n); }
function human_time_diff($a, $b) { return '1 hour'; }
function get_query_var($v) { return ''; }
function get_option($k, $d = false) { return $d; }

require TIT_PATH . 'includes/recall.php';

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

// --- two measurements: the chart must draw -------------------------------

$series = array(
    array(
        'measured_on' => '2026-07-28', 'goldset_version' => '2026-07-v1',
        'overall' => array('total' => 89, 'found' => 5, 'found_partial' => 3,
                           'missed' => 81, 'held' => 8, 'held_pct' => 9.0, 'clean_pct' => 5.6),
        'by_segment' => array(
            'US funding' => array('total' => 22, 'found' => 1, 'found_partial' => 1,
                                  'missed' => 20, 'held' => 2, 'held_pct' => 9.1, 'clean_pct' => 4.5),
        ),
        'by_source_type' => array(), 'by_country' => array(),
    ),
    array(
        'measured_on' => '2026-08-03', 'goldset_version' => '2026-07-v1',
        'overall' => array('total' => 89, 'found' => 9, 'found_partial' => 4,
                           'missed' => 76, 'held' => 13, 'held_pct' => 14.6, 'clean_pct' => 10.1),
        'by_segment' => array(
            'US funding' => array('total' => 22, 'found' => 3, 'found_partial' => 1,
                                  'missed' => 18, 'held' => 4, 'held_pct' => 18.2, 'clean_pct' => 13.6),
            'non-US funding' => array('total' => 44, 'found' => 2, 'found_partial' => 0,
                                      'missed' => 42, 'held' => 2, 'held_pct' => 4.5, 'clean_pct' => 4.5),
        ),
        'by_source_type' => array(), 'by_country' => array(),
    ),
);

$points = tit_recall_points($series);
check(count($points) === 2, 'both measurements should become points');

$svg = tit_recall_sparkline($points);
check(strpos($svg, '<svg') !== false, 'the chart should render an svg');
check(substr_count($svg, '<path') === 2, 'both lines should be drawn, held and clean');
check(substr_count($svg, '<circle') === 4, 'every point on both lines should get a dot');
check(strpos($svg, 'tit-rc-held') !== false, 'the held line should carry its class');
check(strpos($svg, 'tit-rc-clean') !== false, 'the clean line should carry its class');
check(strpos($svg, '2026-07-28') !== false && strpos($svg, '2026-08-03') !== false,
      'the ends of the series should be labelled with their dates');
check(strpos($svg, 'aria-label') !== false, 'the chart needs a text description');
// The y axis must start at zero. A truncated axis turns a five point move into
// a cliff, which is the flattery this page exists to avoid.
check(preg_match('/>0%<\/text>/', $svg) === 1, 'the axis should start at zero');

// --- one measurement: the chart must NOT draw ----------------------------

check(tit_recall_sparkline(array($points[0])) === '',
      'a single measurement is not a trend and must not be drawn as one');

// --- the history table ---------------------------------------------------

ob_start();
tit_recall_history_table('By category, over time', 'by_segment', $points);
$table = ob_get_clean();

check(strpos($table, '2 of 22') !== false && strpos($table, '4 of 22') !== false,
      'history should show raw counts per measurement');
check(strpos($table, 'not tested') !== false,
      'a category absent from an earlier set must read as not tested, never as zero');
// Counted with a pattern, not substr_count('<th'): `<thead` contains `<th`.
check(preg_match_all('/<th[\s>]/', $table) === 3,
      'a column per measurement, plus the label column');

// --- labels --------------------------------------------------------------

check(tit_recall_label('non-US funding') === 'Funding rounds outside the US',
      'cell keys should render as English');
check(tit_recall_label('country_missing') === 'No country recorded',
      'defect keys should render as English');

if ($failures) {
    fwrite(STDERR, "recall page render FAILED:\n  - " . implode("\n  - ", $failures) . "\n");
    exit(1);
}
echo "recall page render ok: chart, history table and labels all render.\n";
