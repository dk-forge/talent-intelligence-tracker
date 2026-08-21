<?php
/*
 * EVERY EMPLOYER NAME IN THIS FILE IS PREFIXED "TEST FIXTURE" ON PURPOSE.
 * Same reason as render_dashboard.php: a test render indistinguishable from
 * production is a trap for a human and for the next session.
 */
/**
 * The market trend's three states, proven against corpora that reach them.
 *
 * WHY THIS FILE EXISTS WHEN render_dashboard.php ALREADY RENDERS THE CARD.
 *
 * That fixture ingests everything within days of the render date, so it can
 * only ever reach the share variant (no collector is live for the whole
 * window there). The property this chart exists for is the OTHER one: with a
 * wide enough panel it counts ONLY the collectors that were live for the
 * entire window, so a source switched on mid-window cannot appear as a market
 * rise. That is the exact confound the old collection-rate chart's basis note
 * once falsely certified away (docs/TECHLOG.md 2026-08-03, defect 3), and a
 * guard that has never seen the counts variant drawn cannot hold it.
 *
 * So this harness seeds three corpora with controlled ingest dates:
 *
 *   A  six collectors live the whole window, plus one switched on mid-window
 *      with a flood of hiring rows. The counts variant must draw, and the
 *      flood must be ABSENT from every weekly count.
 *   B  too few full-window collectors. The share variant must draw, must say
 *      which variant and why in the visible caveat, and a share axis reads in
 *      percent.
 *   C  too few weeks holding anything. Nothing may be drawn, and the card
 *      says so rather than drawing a line through a gap.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/market_trend.php
 */

define('ABSPATH', __DIR__);
$tit_plugin = __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/';
function plugin_dir_path($file) { return dirname($file) . '/'; }
function plugin_dir_url($file) { return 'https://example.test/plugin/'; }
define('MINUTE_IN_SECONDS', 60);
define('HOUR_IN_SECONDS', 3600);
define('DAY_IN_SECONDS', 86400);
define('ARRAY_A', 'ARRAY_A');

/*
 * ONE CLOCK, read once, snapped to a date string. tit_market_trend() reads
 * current_time('Y-m-d') and the fixture below seeds rows relative to the same
 * string, so a run that crosses midnight cannot seed one corpus and measure
 * another. render_dashboard.php documents the hour-a-day red that lesson cost.
 */
$GLOBALS['tit_clock_ymd'] = gmdate('Y-m-d');

function add_action($h, $f, $p = 10, $a = 1) {}
function add_filter($h, $f, $p = 10, $a = 1) {}
function add_shortcode($t, $f) {}
function apply_filters($h, $v) { return $v; }
function has_action($h) { return false; }
function do_action($h) {}
function register_rest_route($ns, $route, $args) {}
function add_rewrite_rule($r, $q, $w = 'bottom') {}
function flush_rewrite_rules($hard = true) {}
function home_url($path = '') { return 'https://example.test/blog' . $path; }
function admin_url($path = '') { return 'https://example.test/blog/wp-admin/' . $path; }
function rest_url($p = '') { return 'https://example.test/blog/wp-json/' . $p; }
function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url_raw($s) { return (string) $s; }
function esc_js($s) { return (string) $s; }
function wp_json_encode($v, $flags = 0) { return json_encode($v, $flags); }
function number_format_i18n($n, $d = 0) { return number_format((float) $n, (int) $d); }
function date_i18n($f, $t = null) { return gmdate($f, $t === null ? time() : $t); }
function wp_date($f, $t = null) { return gmdate($f, $t === null ? time() : $t); }
function human_time_diff($a, $b = null) { return '1 hour'; }
function sanitize_text_field($s) { return trim((string) $s); }
function _n($single, $plural, $count, $domain = '') { return $count == 1 ? $single : $plural; }
function _x($s, $c, $d = '') { return $s; }
function __($s, $d = '') { return $s; }
function wp_strip_all_tags($s) { return strip_tags((string) $s); }
function is_singular() { return false; }
function is_page($p = '') { return false; }
function has_shortcode($content, $tag) { return false; }
function wp_enqueue_style($h, $src = '', $deps = array(), $ver = false, $media = 'all') {}
function wp_enqueue_script($h, $src = '', $deps = array(), $ver = false, $footer = false) {}
function wp_localize_script($h, $name, $data) {}
function wp_script_add_data($h, $key, $value) {}
function current_time($t, $gmt = 0) {
    if ($t === 'timestamp') return strtotime($GLOBALS['tit_clock_ymd'] . ' 12:00:00 UTC');
    if ($t === 'Y-m-d') return $GLOBALS['tit_clock_ymd'];
    if ($t === 'Y') return substr($GLOBALS['tit_clock_ymd'], 0, 4);
    return gmdate($t, strtotime($GLOBALS['tit_clock_ymd'] . ' 12:00:00 UTC'));
}
function get_option($k, $d = false) { return $d; }
function update_option($k, $v, $a = null) { return true; }
function delete_transient($k) { unset($GLOBALS['tit_transients'][$k]); return true; }
function get_transient($k) { return $GLOBALS['tit_transients'][$k] ?? false; }
function set_transient($k, $v, $t = 0) { $GLOBALS['tit_transients'][$k] = $v; return true; }
function get_query_var($v) { return ''; }
function add_query_arg($key, $value, $url) { return $url; }
function remove_accents($string) { return $string; }
function get_header() {}
function get_footer() {}

$GLOBALS['tit_transients'] = array();

/** $wpdb on SQLite, same shape as the other harnesses. */
class MarketHarnessDb {
    public $pdo;
    public $prefix = 'wp_';
    public $last_error = '';
    public $reads = 0;
    public $log = array();

    public function __construct() {
        $this->pdo = new PDO('sqlite::memory:');
        $this->pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $this->pdo->exec(
            'CREATE TABLE wp_tit_signals (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                is_current INTEGER NOT NULL DEFAULT 1,
                headline TEXT NOT NULL DEFAULT "", summary TEXT NOT NULL DEFAULT "",
                talent_readthrough TEXT NOT NULL DEFAULT "",
                company TEXT NOT NULL DEFAULT "", company_key TEXT NOT NULL DEFAULT "",
                pillar TEXT NOT NULL DEFAULT "company_development",
                signal_direction TEXT NOT NULL DEFAULT "neutral",
                city TEXT, region TEXT, country TEXT, hq_city TEXT, hq_country TEXT,
                state TEXT, functions TEXT, industry TEXT, headcount INTEGER,
                funding_amount TEXT, funding_amount_usd INTEGER, funding_stage TEXT, money_basis TEXT,
                materiality TEXT, confidence TEXT NOT NULL DEFAULT "verified",
                source_url TEXT NOT NULL DEFAULT "", source_name TEXT NOT NULL DEFAULT "",
                archive_url TEXT, published_date TEXT,
                captured_at TEXT NOT NULL DEFAULT "2026-01-01 00:00:00",
                collector TEXT NOT NULL DEFAULT "uk_paygap"
            )'
        );
    }

    public function prepare($sql, ...$args) {
        if (count($args) === 1 && is_array($args[0])) $args = $args[0];
        $out = ''; $i = 0; $len = strlen($sql);
        for ($p = 0; $p < $len; $p++) {
            if ($sql[$p] === '%' && $p + 1 < $len && ($sql[$p + 1] === 's' || $sql[$p + 1] === 'd')) {
                $value = $args[$i++] ?? '';
                $out .= $sql[$p + 1] === 'd' ? (string) (int) $value : $this->pdo->quote((string) $value);
                $p++;
                continue;
            }
            $out .= $sql[$p];
        }
        return $out;
    }

    private function run($sql) {
        $this->reads++;
        $this->log[] = preg_replace('/\s+/', ' ', trim($sql));
        return $this->pdo->query($sql);
    }
    public function get_col($sql) { return $this->run($sql)->fetchAll(PDO::FETCH_COLUMN, 0); }
    public function get_results($sql, $o = null) { return $this->run($sql)->fetchAll(PDO::FETCH_ASSOC); }
    public function get_row($sql, $o = null) {
        $row = $this->run($sql)->fetch(PDO::FETCH_ASSOC);
        return $row === false ? null : $row;
    }
    public function get_var($sql) {
        $row = $this->run($sql)->fetch(PDO::FETCH_NUM);
        return $row === false ? null : $row[0];
    }
    public function reset_reads() { $this->reads = 0; $this->log = array(); }

    public function insert_row(array $opts) {
        static $n = 0;
        $n++;
        $row = array_merge(array(
            'signal_id' => 'mkt' . $n,
            'headline' => 'TEST FIXTURE NOT REAL DATA: synthetic update ' . $n,
            'company' => 'TEST FIXTURE Employer ' . $n,
            'company_key' => 'employer ' . $n,
            'source_url' => 'https://example.test/doc/' . $n,
            'source_name' => 'TEST FIXTURE Source',
        ), $opts);
        $cols = implode(', ', array_keys($row));
        $marks = implode(', ', array_fill(0, count($row), '?'));
        $stmt = $this->pdo->prepare("INSERT INTO wp_tit_signals ({$cols}) VALUES ({$marks})");
        $stmt->execute(array_values($row));
    }
}

$GLOBALS['wpdb'] = new MarketHarnessDb();
global $wpdb;

require $tit_plugin . 'talent-intelligence-tracker.php';

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

/*
 * The window, computed EXACTLY as tit_market_trend() computes it, off the one
 * clock: whole Monday-to-Sunday weeks, running week excluded.
 */
$today  = $GLOBALS['tit_clock_ymd'];
$dow    = (int) date('N', strtotime($today));
$monday = date('Y-m-d', strtotime($today . ' -' . ($dow - 1) . ' days'));
$w_end  = date('Y-m-d', strtotime($monday . ' -1 day'));
$w_start = date('Y-m-d', strtotime($w_end . ' -' . (TIT_MARKET_WEEKS * 7 - 1) . ' days'));

/** A day inside week $i (0-based) of the window, offset $d days into it. */
function week_day($start, $i, $d = 2) {
    return date('Y-m-d', strtotime($start . ' +' . ($i * 7 + $d) . ' days'));
}

/** Seed one row: event date, ingest date, direction, collector. */
function seed($event, $ingest, $direction, $collector) {
    global $wpdb;
    $wpdb->insert_row(array(
        'published_date' => $event,
        'captured_at' => $ingest . ' 10:00:00',
        'signal_direction' => $direction,
        'collector' => $collector,
    ));
}

/* ==========================================================================
   A. THE COUNTS VARIANT, and the flood it must not launder.
   ========================================================================== */

$before = date('Y-m-d', strtotime($w_start . ' -3 days'));
$panel_names = array('panel_a', 'panel_b', 'panel_c', 'panel_d', 'panel_e', 'panel_f');
foreach ($panel_names as $c) {
    // First seen before the window opens, still storing in its final week.
    seed($before, $before, 'neutral', $c);
    seed(week_day($w_start, TIT_MARKET_WEEKS - 1, 1), week_day($w_start, TIT_MARKET_WEEKS - 1, 1), 'neutral', $c);
}
// A known weekly mix from the panel: week 1 and week 10, distinct shapes.
seed(week_day($w_start, 1), week_day($w_start, 1), 'hiring', 'panel_a');
seed(week_day($w_start, 1), week_day($w_start, 1), 'hiring', 'panel_b');
seed(week_day($w_start, 1), week_day($w_start, 1), 'displacement', 'panel_c');
seed(week_day($w_start, 1), week_day($w_start, 1), 'neutral', 'panel_d');
for ($i = 0; $i < 5; $i++) {
    seed(week_day($w_start, 10), week_day($w_start, 10), 'hiring', 'panel_' . chr(97 + $i));
}
// Enough weeks with data to clear the min-weeks gate.
for ($w = 2; $w <= 9; $w++) {
    seed(week_day($w_start, $w), week_day($w_start, $w), 'comp_shift', 'panel_f');
}

/*
 * THE FLOOD. A collector switched on in week 8 that ingests a hundred hiring
 * rows dated across the window. By event date it looks like a market boom
 * that was always there; by ingest date it arrived last month. The fixed
 * panel exists so none of this reaches the drawn counts.
 */
for ($i = 0; $i < 100; $i++) {
    seed(week_day($w_start, $i % TIT_MARKET_WEEKS),
         week_day($w_start, 8, $i % 7), 'hiring', 'latecomer');
}

$m = tit_market_trend('wp_tit_signals');
check($m['variant'] === 'counts',
      'A: six full-window collectors must produce the counts variant, got ' . $m['variant']);
check($m['panel'] === $panel_names,
      'A: the panel is exactly the six full-window collectors, sorted: '
      . implode(',', $m['panel']));
check(!in_array('latecomer', $m['panel'], true),
      'A: a collector switched on mid-window must never enter the panel');
check($m['start'] === $w_start && $m['end'] === $w_end,
      "A: the window is whole weeks, {$m['start']}..{$m['end']} vs {$w_start}..{$w_end}");

// Week 1 as seeded: 2 hiring, 1 displacement, 1 neutral, and NOTHING from the
// flood, although the flood put rows in every week by event date.
check($m['weeks'][1]['g'] === 2 && $m['weeks'][1]['s'] === 1 && $m['weeks'][1]['u'] === 1,
      sprintf('A: week 1 must count only the panel (2/1/1), got %d/%d/%d',
              $m['weeks'][1]['g'], $m['weeks'][1]['s'], $m['weeks'][1]['u']));
check($m['weeks'][10]['g'] === 5,
      'A: week 10 must hold the five panel hiring rows and none of the flood, got '
      . $m['weeks'][10]['g']);
$flood_total = 0;
foreach ($m['weeks'] as $w) $flood_total += $w['n'];
check($flood_total < 100,
      'A: the weekly totals must exclude the hundred-row flood entirely');

$html = tit_market_trend_html($m);
$cav  = tit_market_caveat($m);
check(strpos($html, '<rect') !== false, 'A: the counts variant draws bars');
check(strpos($cav, 'Counted only from the 6 sources') !== false,
      'A: the caveat names the panel size: ' . $cav);
check(strpos($cav, 'cannot appear as a market move') !== false,
      'A: and says what the panel is for');
check(strpos($html, "\u{2014}") === false && strpos($cav, "\u{2014}") === false
      && strpos($html, "\u{2013}") === false && strpos($cav, "\u{2013}") === false,
      'A: no em or en dashes reach the page');

/* ==========================================================================
   B. THE SHARE VARIANT, when the panel is too thin to count from.
   ========================================================================== */

$wpdb->pdo->exec('DELETE FROM wp_tit_signals');
// Two full-window collectors: under TIT_MARKET_MIN_PANEL, so counts would be
// a claim about the market made from a sliver of what we read.
foreach (array('thin_a', 'thin_b') as $c) {
    seed($before, $before, 'neutral', $c);
    seed(week_day($w_start, TIT_MARKET_WEEKS - 1, 1), week_day($w_start, TIT_MARKET_WEEKS - 1, 1), 'neutral', $c);
}
// A late collector's rows DO count here: a share of a week's updates makes no
// volume claim for a new source to inflate.
for ($w = 1; $w <= 8; $w++) {
    seed(week_day($w_start, $w), week_day($w_start, 9), $w % 2 ? 'hiring' : 'neutral', 'late_share');
}

$m = tit_market_trend('wp_tit_signals');
check($m['variant'] === 'share',
      'B: two full-window collectors must fall back to shares, got ' . $m['variant']);
$cav = tit_market_caveat($m);
check(strpos($cav, 'Only 2 sources have been live') !== false,
      'B: the caveat names the thin panel: ' . $cav);
check(strpos($cav, 'SHARES of its own updates') !== false
      && strpos($cav, 'too few for an honest count trend') !== false,
      'B: and says which variant is shown and why');
$html = tit_market_trend_html($m);
check(strpos($html, '<rect') !== false, 'B: the share variant draws bars');
check(strpos($html, '>100%</text>') !== false || strpos($html, '>100%') !== false,
      'B: a share axis reads in percent');

/* ==========================================================================
   C. NOTHING DRAWN, said out loud.
   ========================================================================== */

$wpdb->pdo->exec('DELETE FROM wp_tit_signals');
seed(week_day($w_start, 3), week_day($w_start, 3), 'hiring', 'sparse');
seed(week_day($w_start, 7), week_day($w_start, 7), 'neutral', 'sparse');

$m = tit_market_trend('wp_tit_signals');
check($m['variant'] === 'none',
      'C: two weeks of data is not a trend, got ' . $m['variant']);
$html = tit_market_trend_html($m);
check(strpos($html, '<rect') === false && strpos($html, '<svg') === false,
      'C: nothing may be drawn');
check(strpos($html, 'Not drawn yet') !== false,
      'C: and the card says so rather than leaving a hole');
check(strpos(tit_market_caveat($m), 'no trend is drawn') !== false,
      'C: the caveat explains the refusal');

/* ==========================================================================
   The query bill: two per computation, whatever the variant.
   ========================================================================== */

$wpdb->reset_reads();
tit_market_trend('wp_tit_signals');
check($wpdb->reads === 2,
      'the market trend costs exactly two queries (panel, weekly split) and cost '
      . $wpdb->reads . ': ' . implode(' | ', $wpdb->log));

if ($failures) {
    fwrite(STDERR, "market trend FAILED:\n  - " . implode("\n  - ", $failures) . "\n");
    exit(1);
}
echo "market trend ok: the panel excludes the mid-window flood, the thin panel "
   . "falls back to shares and says why, and nothing is drawn from two weeks.\n";
