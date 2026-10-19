<?php
/*
 * EVERY EMPLOYER NAME IN THIS FILE IS PREFIXED "TEST FIXTURE" ON PURPOSE.
 */
/**
 * By location + Top rounds (includes/by_location.php), through the real
 * /aggregate?include=geo route, on SQLite.
 *
 * Proves: region rows fold the strip's own country lists; country and city
 * rows count with tit_signal_defs() predicates; $ Raised sums company_raise
 * ONLY (a divestiture with a bigger figure is excluded from both the table
 * and the top rounds); the period window applies; the caller's filters apply;
 * top rounds are capped at 10, largest first, each with its source.
 *
 * Run: php tests/php/by_location.php
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
function rest_ensure_response($r) { return $r instanceof WP_REST_Response ? $r : new WP_REST_Response($r); }
function is_wp_error($t) { return $t instanceof WP_Error; }
function status_header($c) {}
function wp_unslash($v) { return $v; }
function nocache_headers() {}
class WP_Error {
    public $code; public $message; public $data;
    public function __construct($code = '', $message = '', $data = array()) {
        $this->code = $code; $this->message = $message; $this->data = $data;
    }
    public function get_error_message() { return $this->message; }
}
class WP_REST_Request {
    private $params;
    public function __construct($params = array()) { $this->params = $params; }
    public function get_param($n) { return $this->params[$n] ?? null; }
    public function get_params() { return $this->params; }
}
class WP_REST_Response {
    private $data; private $headers = array(); private $status = 200;
    public function __construct($data = null) { $this->data = $data; }
    public function header($k, $v) { $this->headers[$k] = $v; }
    public function get_headers() { return $this->headers; }
    public function get_data() { return $this->data; }
    public function set_status($s) { $this->status = $s; }
}
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
class GeoHarnessDb {
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

$GLOBALS['wpdb'] = new GeoHarnessDb();
global $wpdb;


require $tit_plugin . 'talent-intelligence-tracker.php';

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

$today = $GLOBALS['tit_clock_ymd'];
$day = function ($n) use ($today) { return gmdate('Y-m-d', strtotime($today . " -{$n} days")); };
$year_start = substr($today, 0, 4) . '-01-01';

// Berlin, DE: two company raises today + one divestiture with a larger figure.
$wpdb->insert_row(array('company' => 'TEST FIXTURE Alpha', 'country' => 'DE', 'city' => 'Berlin',
    'funding_amount' => '50M', 'funding_amount_usd' => 50000000, 'money_basis' => 'company_raise',
    'published_date' => $today, 'source_url' => 'https://example.test/alpha'));
$wpdb->insert_row(array('company' => 'TEST FIXTURE Beta', 'country' => 'DE', 'city' => 'Berlin',
    'funding_amount' => '5M', 'funding_amount_usd' => 5000000, 'money_basis' => 'company_raise',
    'published_date' => $today));
$wpdb->insert_row(array('company' => 'TEST FIXTURE Divest', 'country' => 'DE', 'city' => 'Berlin',
    'funding_amount' => '900M', 'funding_amount_usd' => 900000000, 'money_basis' => 'divestiture',
    'published_date' => $today));
// Paris, FR (Europe): hiring + leadership.
$wpdb->insert_row(array('company' => 'TEST FIXTURE Gamma', 'country' => 'FR', 'city' => 'Paris',
    'signal_direction' => 'hiring', 'published_date' => $today));
$wpdb->insert_row(array('company' => 'TEST FIXTURE Delta', 'country' => 'FR', 'city' => 'Paris',
    'pillar' => 'leadership_change', 'published_date' => $today));
// Austin, US (Americas): HQ-only geography, pay, plus a big raise last year.
$wpdb->insert_row(array('company' => 'TEST FIXTURE Eps', 'hq_country' => 'US', 'hq_city' => 'Austin',
    'pillar' => 'rewards_comp', 'published_date' => $today));
$wpdb->insert_row(array('company' => 'TEST FIXTURE Old', 'country' => 'US', 'city' => 'Austin',
    'funding_amount' => '2B', 'funding_amount_usd' => 2000000000, 'money_basis' => 'company_raise',
    'published_date' => (substr($today, 0, 4) - 1) . '-06-01'));
// Twelve small raises in Tokyo, to prove the cap of ten.
for ($i = 1; $i <= 12; $i++) {
    $wpdb->insert_row(array('company' => 'TEST FIXTURE Tok ' . $i, 'country' => 'JP', 'city' => 'Tokyo',
        'funding_amount' => $i . 'M', 'funding_amount_usd' => $i * 1000000, 'money_basis' => 'company_raise',
        'published_date' => $today));
}

$agg = function (array $p) {
    $GLOBALS['tit_transients'] = array();
    $r = tit_api_aggregate(new WP_REST_Request($p));
    if (is_wp_error($r)) return $r;
    return $r instanceof WP_REST_Response ? $r->get_data() : $r;
};

// --- Region view, YTD -----------------------------------------------------
$d = $agg(array('include' => 'geo', 'geo' => 'region', 'geo_period' => 'ytd'));
check(is_array($d) && isset($d['by_location']['rows']), 'include=geo returns by_location rows');
$by = array();
foreach ($d['by_location']['rows'] ?? array() as $r) $by[$r['key']] = $r;
check(isset($by['Europe'], $by['Americas'], $by['Asia']), 'regions present: ' . implode(',', array_keys($by)));
check(($by['Europe']['money'] ?? -1) === 55000000, 'Europe $ raised is company_raise only (55M), got ' . ($by['Europe']['money'] ?? 'none'));
check(($by['Europe']['rounds'] ?? -1) === 3, 'Europe funding rounds counts every round incl. divestiture row (tit_funding_where), got ' . ($by['Europe']['rounds'] ?? 'none'));
check(($by['Europe']['hiring'] ?? -1) === 1 && ($by['Europe']['leadership'] ?? -1) === 1, 'Europe hiring/leadership');
check(($by['Americas']['pay'] ?? -1) === 1, 'HQ-only row is placed by hq_country');
check(($by['Americas']['money'] ?? -1) === 0, 'last year\'s raise is outside YTD');
check(strpos($by['Europe']['codes'] ?? '', 'DE') !== false, 'region row carries the strip codes');
check(!isset($by['World']), 'World is not a region row');

// --- Country view under a filter --------------------------------------------
$d = $agg(array('include' => 'geo', 'geo' => 'country', 'geo_period' => 'ytd', 'country' => 'DE,FR'));
$keys = array_map(function ($r) { return $r['key']; }, $d['by_location']['rows']);
sort($keys);
check($keys === array('DE', 'FR'), 'country filter applies: ' . implode(',', $keys));

// --- City view ---------------------------------------------------------------
$d = $agg(array('include' => 'geo', 'geo' => 'city', 'geo_period' => 'week'));
$by = array();
foreach ($d['by_location']['rows'] as $r) $by[$r['key']] = $r;
check(isset($by['Berlin'], $by['Paris'], $by['Austin'], $by['Tokyo']), 'cities present');
check(($by['Austin']['country'] ?? '') === 'US', 'city row names its country');
check($d['by_location']['period'] === 'This week', 'period label echoed');

// --- Top rounds ----------------------------------------------------------------
$d = $agg(array('include' => 'geo', 'geo_period' => 'ytd'));
check($d['by_location'] === null, 'Time view skips the per-place scan');
$top = $d['top_rounds'];
check(count($top) === 10, 'top rounds capped at 10, got ' . count($top));
check(($top[0]['company'] ?? '') === 'TEST FIXTURE Alpha', 'largest company raise first');
check(($top[0]['source_url'] ?? '') === 'https://example.test/alpha', 'source link carried');
check(($top[0]['city'] ?? '') === 'Berlin' && ($top[0]['country'] ?? '') === 'DE', 'place carried');
foreach ($top as $t) {
    check($t['company'] !== 'TEST FIXTURE Divest', 'divestiture never in top rounds');
    check($t['company'] !== 'TEST FIXTURE Old', 'out-of-period raise never in top rounds');
}
$amts = array_column($top, 'amount');
$sorted = $amts; rsort($sorted);
check($amts === $sorted, 'top rounds sorted largest first');
$d = $agg(array('include' => 'geo', 'geo_period' => 'ytd', 'country' => 'JP'));
check(($d['top_rounds'][0]['company'] ?? '') === 'TEST FIXTURE Tok 12', 'top rounds respect the filter');

// --- Validation -----------------------------------------------------------------
$e = $agg(array('include' => 'geo', 'geo' => 'planet'));
check(is_wp_error($e), 'unknown geo level is refused');
$e = $agg(array('include' => 'geo', 'geo_period' => 'decade'));
check(is_wp_error($e), 'unknown geo_period is refused');

if ($failures) {
    fwrite(STDERR, "FAIL\n - " . implode("\n - ", $failures) . "\n");
    exit(1);
}
echo "by_location: all checks passed\n";
