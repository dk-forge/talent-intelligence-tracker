<?php
/**
 * /re-source, run rather than read.
 *
 * The door exists so that 82 published rows citing an aggregator can be
 * pointed at the outlet that reported the event WITHOUT widening /correct,
 * whose allowlist forbids headline, source_url and source_name so that a
 * correction bug can never rewrite what a document said. That reason has to
 * survive the new door, so this harness proves four things by running them:
 *
 *  - /correct still drops headline, source_url and source_name (skipped, not
 *    written), exactly as before 1.88.7;
 *  - /re-source moves a citation and takes the old masthead off the headline,
 *    and nothing else about the row changes;
 *  - /re-source refuses a headline edit that is not exactly "minus the current
 *    masthead", a citation that stays on the same host, a half citation, and a
 *    row belonging to another collector;
 *  - an empty or absent field erases nothing.
 *
 * Run: php tests/php/re_source.php
 */

define('ABSPATH', __DIR__);
$tit_plugin = __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/';

function plugin_dir_path($file) { return dirname($file) . '/'; }
function plugin_dir_url($file) { return 'https://example.test/plugin/'; }
define('MINUTE_IN_SECONDS', 60);
define('HOUR_IN_SECONDS', 3600);
define('DAY_IN_SECONDS', 86400);
define('ARRAY_A', 'ARRAY_A');

$GLOBALS['tit_transients'] = array();
$GLOBALS['tit_flushes'] = 0;

function add_action($h, $f, $p = 10, $a = 1) {}
function add_filter($h, $f, $p = 10, $a = 1) {}
function add_shortcode($t, $f) {}
function apply_filters($h, $v) { return $v; }
function has_action($h) { return false; }
function do_action($h) {}
function register_rest_route($ns, $route, $args) { $GLOBALS['tit_routes'][$route] = $args; }
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
function _n($s, $p, $c, $d = '') { return $c == 1 ? $s : $p; }
function _x($s, $c, $d = '') { return $s; }
function __($s, $d = '') { return $s; }
function wp_strip_all_tags($s) { return strip_tags((string) $s); }
function is_singular() { return false; }
function has_shortcode($c, $t) { return false; }
function wp_enqueue_style() {}
function wp_enqueue_script() {}
function wp_localize_script() {}
function wp_script_add_data() {}
function current_time($t, $gmt = 0) { return $t === 'timestamp' ? time() : gmdate($t); }
function get_option($k, $d = false) { return $d; }
function update_option($k, $v, $a = null) { return true; }
function delete_transient($k) { unset($GLOBALS['tit_transients'][$k]); return true; }
function get_transient($k) { return $GLOBALS['tit_transients'][$k] ?? false; }
function set_transient($k, $v, $t = 0) { $GLOBALS['tit_transients'][$k] = $v; return true; }
function get_query_var($v) { return ''; }
function add_query_arg($k, $v, $u) { return $u; }
function remove_accents($s) { return $s; }
function get_header() {}
function get_footer() {}
function rest_ensure_response($r) { return $r; }
function is_wp_error($t) { return $t instanceof WP_Error; }

class WP_Error {
    public $code; public $message; public $data;
    public function __construct($code = '', $message = '', $data = array()) {
        $this->code = $code; $this->message = $message; $this->data = $data;
    }
    public function get_error_message() { return $this->message; }
}
class WP_REST_Request {
    private $body; private $params;
    public function __construct($body = array(), $params = array()) {
        $this->body = $body; $this->params = $params;
    }
    public function get_json_params() { return $this->body; }
    public function get_param($n) { return $this->params[$n] ?? null; }
}

class ReSourceHarnessDb {
    public $pdo;
    public $prefix = 'wp_';
    public $options = 'wp_options';
    public $last_error = '';

    public function __construct() {
        $this->pdo = new PDO('sqlite::memory:');
        $this->pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $this->pdo->exec(
            'CREATE TABLE wp_tit_signals (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT, revision INTEGER DEFAULT 1, is_current INTEGER DEFAULT 1,
                content_hash TEXT, headline TEXT DEFAULT "", company TEXT DEFAULT "",
                company_key TEXT DEFAULT "", pillar TEXT DEFAULT "company_development",
                signal_direction TEXT DEFAULT "neutral", talent_readthrough TEXT DEFAULT "",
                city TEXT, region TEXT, country TEXT, hq_city TEXT, hq_country TEXT,
                funding_amount TEXT, funding_amount_usd INTEGER, funding_stage TEXT, money_basis TEXT,
                archive_url TEXT, collector TEXT DEFAULT "google_news",
                source_url TEXT DEFAULT "", source_name TEXT DEFAULT "",
                published_date TEXT, confidence TEXT DEFAULT "reported",
                captured_at TEXT DEFAULT "2026-07-01 00:00:00"
            )'
        );
    }

    public function prepare($sql, ...$args) {
        if (count($args) === 1 && is_array($args[0])) $args = $args[0];
        $out = ''; $i = 0; $len = strlen($sql);
        for ($p = 0; $p < $len; $p++) {
            if ($sql[$p] === '%' && $p + 1 < $len && ($sql[$p + 1] === 's' || $sql[$p + 1] === 'd')) {
                $v = $args[$i++] ?? '';
                $out .= $sql[$p + 1] === 'd' ? (string) (int) $v : $this->pdo->quote((string) $v);
                $p++;
                continue;
            }
            $out .= $sql[$p];
        }
        return $out;
    }

    public function get_results($sql, $o = null) { return $this->pdo->query($sql)->fetchAll(PDO::FETCH_ASSOC); }
    public function get_row($sql, $o = null) {
        $r = $this->pdo->query($sql)->fetch(PDO::FETCH_ASSOC);
        return $r === false ? null : $r;
    }
    public function get_var($sql) {
        $r = $this->pdo->query($sql)->fetch(PDO::FETCH_NUM);
        return $r === false ? null : $r[0];
    }
    public function get_col($sql) { return $this->pdo->query($sql)->fetchAll(PDO::FETCH_COLUMN, 0); }
    public function query($sql) { $GLOBALS['tit_flushes']++; return 0; }
    public function esc_like($s) { return addcslashes((string) $s, '_%\\'); }

    public function update($table, $data, $where) {
        $sets = array(); $values = array();
        foreach ($data as $col => $value) {
            if ($value === null) { $sets[] = "`{$col}` = NULL"; continue; }
            $sets[] = "`{$col}` = ?";
            $values[] = $value;
        }
        $conds = array();
        foreach ($where as $col => $value) {
            $conds[] = "`{$col}` = ?";
            $values[] = $value;
        }
        $stmt = $this->pdo->prepare("UPDATE {$table} SET " . implode(', ', $sets)
                                    . ' WHERE ' . implode(' AND ', $conds));
        $stmt->execute($values);
        return $stmt->rowCount();
    }

    public function insert_row(array $row) {
        $columns = implode(', ', array_keys($row));
        $marks = implode(', ', array_fill(0, count($row), '?'));
        $stmt = $this->pdo->prepare("INSERT INTO wp_tit_signals ({$columns}) VALUES ({$marks})");
        $stmt->execute(array_values($row));
    }

    public function fetch($hash) {
        $stmt = $this->pdo->prepare('SELECT * FROM wp_tit_signals WHERE content_hash = ?');
        $stmt->execute(array($hash));
        return $stmt->fetch(PDO::FETCH_ASSOC);
    }
}

$GLOBALS['wpdb'] = new ReSourceHarnessDb();
global $wpdb;
require $tit_plugin . 'talent-intelligence-tracker.php';

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

/* --- the rows, shaped like the 82 the audit found ------------------------ */

// A provider "news note" surfaced through Google News: the masthead is a
// trailing " - Provider" and source_name is that masthead.
$wpdb->insert_row(array(
    'signal_id' => 'acme', 'content_hash' => 'h-acme',
    'company' => 'Acme Robotics', 'company_key' => 'acme robotics',
    'headline' => 'Acme Robotics raises $20M Series A to automate warehouses - Provider',
    'source_url' => 'https://app.provider.example/news/note/acme-robotics-raises-20m',
    'source_name' => 'Provider', 'collector' => 'google_news',
    'city' => 'Austin', 'country' => 'US', 'signal_direction' => 'hiring',
    'funding_amount' => '$20M', 'published_date' => '2026-08-12',
));
// A finance mirror: no masthead suffix, source_name is the mirror.
$wpdb->insert_row(array(
    'signal_id' => 'beta', 'content_hash' => 'h-beta',
    'company' => 'Beta Foods', 'company_key' => 'beta foods',
    'headline' => 'Beta Foods names new CEO',
    'source_url' => 'https://finance.mirror.example/news/beta-foods-names-new-ceo.html',
    'source_name' => 'Mirror Finance', 'collector' => 'google_news',
));
// The same hash under ANOTHER collector: a batch built badly must not reach it.
$wpdb->insert_row(array(
    'signal_id' => 'acme-press', 'content_hash' => 'h-acme',
    'company' => 'Acme Robotics', 'company_key' => 'acme robotics',
    'headline' => 'Acme Robotics raises $20M Series A to automate warehouses - Provider',
    'source_url' => 'https://app.provider.example/news/note/acme-robotics-raises-20m',
    'source_name' => 'Provider', 'collector' => 'national_press',
));

/* --- the route is registered and keyed ----------------------------------- */

// The plugin hooks its registrar onto rest_api_init; the stub add_action above
// runs nothing, so the registrar is called the way WordPress would.
tit_register_routes();
check(isset($GLOBALS['tit_routes']['/re-source']), '/re-source is registered');
check(($GLOBALS['tit_routes']['/re-source']['callback'] ?? '') === 'tit_api_resource',
      '/re-source calls tit_api_resource');
check(($GLOBALS['tit_routes']['/re-source']['permission_callback'] ?? '') !== '__return_true',
      '/re-source is not public');

/* --- the general door still drops the citation fields -------------------- */

$out = tit_api_correct(new WP_REST_Request(array(
    'collector' => 'google_news',
    'rows' => array(array(
        'content_hash' => 'h-acme',
        'headline' => 'Acme Robotics raises $20M Series A to automate warehouses',
        'source_url' => 'https://publisher.example/acme-20m',
        'source_name' => 'Publisher',
    )),
)));
$row = $wpdb->fetch('h-acme');
check($row['source_url'] === 'https://app.provider.example/news/note/acme-robotics-raises-20m'
      && $row['source_name'] === 'Provider'
      && $row['headline'] === 'Acme Robotics raises $20M Series A to automate warehouses - Provider',
      '/correct wrote none of headline, source_url or source_name');
check(($out['skipped_no_fields'] ?? 0) === 1 && ($out['corrected'] ?? 0) === 0,
      '/correct reported the citation fields as dropped: skipped_no_fields='
      . ($out['skipped_no_fields'] ?? 'null'));

/* --- /re-source refuses everything short of the whole rule --------------- */

// A headline edit that is not "minus the current masthead".
$out = tit_api_resource(new WP_REST_Request(array(
    'collector' => 'google_news',
    'rows' => array(array(
        'content_hash' => 'h-acme',
        'headline' => 'Acme Robotics raises $25M Series A to automate warehouses',
        'source_url' => 'https://publisher.example/acme-20m', 'source_name' => 'Publisher',
    )),
)));
$row = $wpdb->fetch('h-acme');
check($row['headline'] === 'Acme Robotics raises $20M Series A to automate warehouses - Provider'
      && $row['source_url'] === 'https://app.provider.example/news/note/acme-robotics-raises-20m',
      'a headline that changes a word is refused and NOTHING on the row moves');
check(!empty($out['errors']) && ($out['resourced'] ?? 1) === 0, 'and the refusal is reported');

// Dropping a masthead that is NOT the current source's name.
$out = tit_api_resource(new WP_REST_Request(array(
    'collector' => 'google_news',
    'rows' => array(array(
        'content_hash' => 'h-acme',
        'headline' => 'Acme Robotics raises $20M Series A',
        'source_url' => 'https://publisher.example/acme-20m', 'source_name' => 'Publisher',
    )),
)));
check($wpdb->fetch('h-acme')['source_url'] === 'https://app.provider.example/news/note/acme-robotics-raises-20m',
      'a headline shortened past the masthead is refused');
check(!empty($out['errors']), 'and reported');

// A citation that stays on the same host.
$out = tit_api_resource(new WP_REST_Request(array(
    'collector' => 'google_news',
    'rows' => array(array(
        'content_hash' => 'h-acme',
        'source_url' => 'https://app.provider.example/news/note/acme-robotics-other',
        'source_name' => 'Provider Newsroom',
    )),
)));
check($wpdb->fetch('h-acme')['source_url'] === 'https://app.provider.example/news/note/acme-robotics-raises-20m',
      'a re-source that stays on the same host is refused');
check(!empty($out['errors']), 'and reported');

// Half a citation, and an http URL.
$out = tit_api_resource(new WP_REST_Request(array(
    'collector' => 'google_news',
    'rows' => array(
        array('content_hash' => 'h-acme', 'source_url' => 'https://publisher.example/acme-20m'),
        array('content_hash' => 'h-acme', 'source_url' => 'https://publisher.example/acme-20m',
              'source_name' => ''),
        array('content_hash' => 'h-acme', 'source_url' => 'http://publisher.example/acme-20m',
              'source_name' => 'Publisher'),
    ),
)));
check($wpdb->fetch('h-acme')['source_url'] === 'https://app.provider.example/news/note/acme-robotics-raises-20m',
      'a URL without a name, a blank name, and an http URL are all refused');
check(count($out['errors'] ?? array()) === 3, 'and each is reported: ' . count($out['errors'] ?? array()));

// A request that names no collector is a 400, not a sweep.
$out = tit_api_resource(new WP_REST_Request(array(
    'rows' => array(array('content_hash' => 'h-acme',
                          'source_url' => 'https://publisher.example/acme-20m',
                          'source_name' => 'Publisher')),
)));
check($out instanceof WP_Error, 'a re-source that names no collector is refused outright');

/* --- and does the one thing it is for ----------------------------------- */

$flushes_before = $GLOBALS['tit_flushes'];
$out = tit_api_resource(new WP_REST_Request(array(
    'collector' => 'google_news',
    'rows' => array(
        array(
            'content_hash' => 'h-acme',
            'headline' => 'Acme Robotics raises $20M Series A to automate warehouses',
            'source_url' => 'https://publisher.example/acme-20m',
            'source_name' => 'Publisher',
        ),
        // No headline sent: the live one stays, the citation moves.
        array(
            'content_hash' => 'h-beta',
            'source_url' => 'https://trade.example/news/beta-foods-names-new-ceo/',
            'source_name' => 'Trade Title',
        ),
    ),
)));
$acme = $wpdb->pdo->query("SELECT * FROM wp_tit_signals WHERE content_hash = 'h-acme' AND collector = 'google_news'")->fetch(PDO::FETCH_ASSOC);
check($acme['source_url'] === 'https://publisher.example/acme-20m'
      && $acme['source_name'] === 'Publisher'
      && $acme['headline'] === 'Acme Robotics raises $20M Series A to automate warehouses',
      'the citation moved and the masthead came off the headline');
check($acme['city'] === 'Austin' && $acme['country'] === 'US'
      && $acme['signal_direction'] === 'hiring' && $acme['funding_amount'] === '$20M'
      && $acme['published_date'] === '2026-08-12' && $acme['company'] === 'Acme Robotics',
      'and nothing else on the row changed');
$beta = $wpdb->fetch('h-beta');
check($beta['source_url'] === 'https://trade.example/news/beta-foods-names-new-ceo/'
      && $beta['source_name'] === 'Trade Title'
      && $beta['headline'] === 'Beta Foods names new CEO',
      'a row sent without a headline keeps its headline and moves its citation');
check(($out['resourced'] ?? 0) === 2 && empty($out['errors']),
      'and the response says so: resourced=' . ($out['resourced'] ?? 'null'));
check($GLOBALS['tit_flushes'] > $flushes_before, 'a successful re-source flushes the caches');

// The same hash under another collector was never touched.
$press = $wpdb->pdo->query("SELECT * FROM wp_tit_signals WHERE content_hash = 'h-acme' AND collector = 'national_press'")->fetch(PDO::FETCH_ASSOC);
check($press['source_url'] === 'https://app.provider.example/news/note/acme-robotics-raises-20m'
      && $press['headline'] === 'Acme Robotics raises $20M Series A to automate warehouses - Provider',
      'a row belonging to ANOTHER collector is not touched, however the batch was built');

// A second, identical run is idempotent: the host no longer differs, so it is
// refused as "must move", and the row is exactly as the first run left it.
$out = tit_api_resource(new WP_REST_Request(array(
    'collector' => 'google_news',
    'rows' => array(array(
        'content_hash' => 'h-acme',
        'headline' => 'Acme Robotics raises $20M Series A to automate warehouses',
        'source_url' => 'https://publisher.example/acme-20m', 'source_name' => 'Publisher',
    )),
)));
$again = $wpdb->pdo->query("SELECT * FROM wp_tit_signals WHERE content_hash = 'h-acme' AND collector = 'google_news'")->fetch(PDO::FETCH_ASSOC);
check($again == $acme, 'a repeated re-source changes nothing');

// A row the collector does not hold is counted, not created.
$out = tit_api_resource(new WP_REST_Request(array(
    'collector' => 'google_news',
    'rows' => array(array('content_hash' => 'h-nobody',
                          'source_url' => 'https://publisher.example/x', 'source_name' => 'P')),
)));
check(($out['unchanged_or_missing'] ?? 0) === 1 && $wpdb->fetch('h-nobody') === false,
      'an unknown hash is reported missing and no row is created');

if ($failures) {
    fwrite(STDERR, "re-source FAILED:\n  - " . implode("\n  - ", $failures) . "\n");
    exit(1);
}
echo "re-source ok: /correct still drops the citation fields, /re-source moves a citation "
   . "and may only take the masthead off the headline, refuses everything short of that, "
   . "and cannot cross collectors.\n";
