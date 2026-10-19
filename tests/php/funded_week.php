<?php
/*
 * EVERY EMPLOYER NAME IN THIS FILE IS PREFIXED "TEST FIXTURE" ON PURPOSE.
 */
/**
 * Funded this week, now hiring (includes/funded_week.php), on SQLite, with the
 * clock PINNED to Wednesday 2026-10-21 so the week arithmetic is exact.
 *
 * Proves: the stable URL resolves to the last COMPLETED Monday-Sunday week;
 * archive URLs accept only a past Monday on or after the first week; rows are
 * company_raise only (a bigger divestiture in the same week is absent from the
 * list AND the totals), inside the week, largest first, each with source, jobs
 * link and tailor button; the intro is computed counts and totals; the
 * Article/ItemList JSON-LD parses, carries no JobPosting, and its dateModified
 * is the newest capture of the rows it lists.
 *
 * Run: php tests/php/funded_week.php
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
$GLOBALS['tit_clock_ymd'] = '2026-10-21';

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
function get_query_var($v) { return $GLOBALS['tit_qv'][$v] ?? ''; }
$GLOBALS['tit_qv'] = array();
function add_query_arg($key, $value, $url) { return $url; }
function remove_accents($string) { return $string; }
function get_header() {}
function get_footer() {}

$GLOBALS['tit_transients'] = array();

/** $wpdb on SQLite, same shape as the other harnesses. */
class FweekHarnessDb {
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

$GLOBALS['wpdb'] = new FweekHarnessDb();
global $wpdb;


require $tit_plugin . 'talent-intelligence-tracker.php';

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}


// Week under test: Monday 12 to Sunday 18 October 2026.
$row = function ($opts) use ($wpdb) {
    $wpdb->insert_row(array_merge(array('funding_amount' => '1M', 'money_basis' => 'company_raise',
        'country' => 'DE', 'city' => 'Berlin', 'captured_at' => '2026-10-13 09:00:00'), $opts));
};
$row(array('company' => 'TEST FIXTURE Big', 'funding_amount_usd' => 80000000, 'published_date' => '2026-10-12',
    'source_url' => 'https://example.test/big', 'captured_at' => '2026-10-17 08:30:00'));
$row(array('company' => 'TEST FIXTURE Small', 'funding_amount_usd' => 2000000, 'published_date' => '2026-10-18',
    'country' => 'FR', 'city' => 'Paris'));
$row(array('company' => 'TEST FIXTURE Mid', 'funding_amount_usd' => 20000000, 'published_date' => '2026-10-15'));
// Same week, bigger figure, NOT a company raise: absent from list and totals.
$row(array('company' => 'TEST FIXTURE Divest', 'funding_amount_usd' => 900000000, 'money_basis' => 'divestiture',
    'published_date' => '2026-10-14'));
// Never judged (NULL basis) is not a quiet yes.
$row(array('company' => 'TEST FIXTURE Unjudged', 'funding_amount_usd' => 700000000, 'money_basis' => null,
    'published_date' => '2026-10-14'));
// Just outside on both sides, and one in the current, unfinished week.
$row(array('company' => 'TEST FIXTURE Before', 'funding_amount_usd' => 500000000, 'published_date' => '2026-10-11'));
$row(array('company' => 'TEST FIXTURE After', 'funding_amount_usd' => 500000000, 'published_date' => '2026-10-19'));
// A superseded revision of a raise in the week.
$row(array('company' => 'TEST FIXTURE Stale', 'funding_amount_usd' => 600000000, 'published_date' => '2026-10-13',
    'is_current' => 0));

// --- week arithmetic ---------------------------------------------------------
check(tit_funded_week_latest('2026-10-21') === '2026-10-12', 'Wednesday -> previous Monday');
check(tit_funded_week_latest('2026-10-19') === '2026-10-12', 'on Monday the roundup is the week just ended');
check(tit_funded_week_latest('2026-10-18') === '2026-10-05', 'Sunday is still inside the current week');
check(tit_funded_week_resolve('latest', '2026-10-21') === '2026-10-12', 'latest resolves');
check(tit_funded_week_resolve('2026-10-05', '2026-10-21') === '2026-10-05', 'a past Monday is an archive page');
check(tit_funded_week_resolve('2026-10-19', '2026-10-21') === '', 'the unfinished week has no page yet');
check(tit_funded_week_resolve('2026-10-13', '2026-10-21') === '', 'a Tuesday is not a week');
check(tit_funded_week_resolve('2026-02-30', '2026-10-21') === '', 'an impossible date is refused');
check(tit_funded_week_resolve('2026-06-29', '2026-10-21') === '', 'before the first week is refused');
check(tit_funded_week_resolve('../etc', '2026-10-21') === '', 'garbage is refused');
$ms = tit_funded_week_mondays('2026-10-21');
check($ms[0] === '2026-10-12' && count($ms) === 12, 'archive lists twelve completed weeks, newest first');
check(tit_funded_week_span('2026-09-28', '2026-10-04') === '28 September to 4 October 2026', 'span across months');
check(array_key_exists('^talent-intelligence-tracker/funded-this-week/?$', tit_funded_week_rewrites()), 'stable route');
check(in_array('tit_funded_week_rewrites', tit_route_modules(), true), 'route is verified and repaired like the others');

// --- facts -------------------------------------------------------------------
$f = tit_funded_week_facts('wp_tit_signals', '2026-10-12');
$names = array_column($f['rows'], 'company');
check($names === array('TEST FIXTURE Big', 'TEST FIXTURE Mid', 'TEST FIXTURE Small'),
    'company raises in the week, largest first: ' . implode(', ', $names));
check($f['n'] === 3 && $f['c'] === 3 && $f['countries'] === 2, 'counts: ' . json_encode(array($f['n'], $f['c'], $f['countries'])));
check((int) $f['m'] === 102000000, 'total is company_raise only: ' . $f['m']);
check($f['newest'] === '2026-10-17 08:30:00', 'newest capture: ' . $f['newest']);
$big = $f['rows'][0];
check($big['source_url'] === 'https://example.test/big', 'source carried');
check($big['jobs_kind'] === 'search' && strpos($big['jobs_url'], 'linkedin.com/jobs/search/') !== false,
    'jobs link comes from tit_funded_links()');
$sql = implode("\n", $wpdb->log);
check(strpos($sql, "money_basis = 'company_raise'") !== false || strpos($sql, 'company_raise') !== false,
    'money asks for company_raise by name');

$intro = tit_funded_week_intro($f);
check($intro === 'From 12 to 18 October 2026, we recorded 3 company raises with a stated US dollar amount, by 3 employers in 2 countries, totalling ' . tit_money_short(102000000) . '. The largest was TEST FIXTURE Big at ' . tit_money_short(80000000) . '.',
    'intro is computed: ' . $intro);
check(strpos($intro, '—') === false, 'no em-dash in reader copy');
$empty = tit_funded_week_facts('wp_tit_signals', '2026-09-28');
check($empty['n'] === 0 && strpos(tit_funded_week_intro($empty), 'no company raise') !== false, 'empty week says so');

// --- JSON-LD -------------------------------------------------------------------
$ld = tit_funded_week_jsonld($f, tit_funded_week_url('2026-10-12'));
check(preg_match('#<script type="application/ld\+json">(.*)</script>#s', $ld, $m) === 1, 'json-ld block');
$j = json_decode($m[1] ?? '', true);
check(($j['@type'] ?? '') === 'Article' && ($j['mainEntity']['@type'] ?? '') === 'ItemList', 'Article + ItemList');
check(count($j['mainEntity']['itemListElement'] ?? array()) === 3, 'one ListItem per row');
check(strpos($m[1] ?? '', 'JobPosting') === false, 'no JobPosting');
check(($j['dateModified'] ?? '') === '2026-10-18T23:59:59+00:00' || ($j['dateModified'] ?? '') === '2026-10-18T23:59:59Z',
    'dateModified never precedes the week end: ' . ($j['dateModified'] ?? ''));
check(($j['datePublished'] ?? '') === '2026-10-18T23:59:59Z', 'datePublished is the week end');
// A late capture after the week moves dateModified.
$late = $f; $late['newest'] = '2026-10-20 07:00:00';
$jl = json_decode(preg_replace('#^<script[^>]*>|</script>\s*$#', '', tit_funded_week_jsonld($late, 'u')), true);
check(($jl['dateModified'] ?? '') === '2026-10-20T07:00:00+00:00', 'dateModified follows the newest capture: ' . ($jl['dateModified'] ?? ''));

// --- render ----------------------------------------------------------------------
ob_start();
tit_funded_week_render($f);
$html = ob_get_clean();
check(substr_count($html, 'class="tit-tr-item"') === 3, 'three rows rendered');
check(substr_count($html, 'Tailor your resume for this company') === 3, 'tailor button per row');
check(substr_count($html, '>Source</a>') === 3, 'source link per row');
check(substr_count($html, '>Search jobs</a>') === 3, 'jobs link per row');
check(strpos($html, 'Divest') === false && strpos($html, 'Unjudged') === false, 'non-raises never render');
check(strpos($html, tit_funded_week_url('2026-10-05')) !== false, 'links an earlier week');
check(strpos($html, tit_funded_week_url('2026-10-12') . '"') === false, 'does not link to itself in the archive');
check(strpos($html, '—') === false, 'no em-dash in the page');

if ($failures) {
    fwrite(STDERR, "FAIL\n - " . implode("\n - ", $failures) . "\n");
    exit(1);
}
echo "funded_week: all checks passed\n";
