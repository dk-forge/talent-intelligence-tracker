<?php
/**
 * The two write routes that touch rows the site ALREADY shows, run for real.
 *
 * WHY THIS IS A RUNNING HARNESS.
 *
 * /enrich and /correct are the only endpoints that can change a row a reader is
 * looking at, and the whole safety of both is a property of one loop each:
 *
 *  - AN ABSENT OR EMPTY FIELD MUST NEVER ERASE A KNOWN VALUE. An enrichment pass
 *    with one failed lookup would otherwise blank a column across thousands of
 *    rows, and it would look exactly like a successful run.
 *  - AN EXPLICIT CLEAR MUST WORK, AND ONLY FOR THE COLUMNS THAT ALLOW IT. Three
 *    live rows carried a US dollar figure for a round raised in kroner or euros,
 *    and until 1.53.0 no route could remove it. A wrong figure in public is the
 *    one thing this product cannot leave alone.
 *  - A CORRECTION MUST NOT SWEEP ANOTHER SOURCE'S ROWS. /correct is scoped to the
 *    collector the caller names, so a bad batch can only damage what it claimed.
 *
 * None of those can be read out of the source with any confidence, because what
 * breaks them is which branch a value takes. So $wpdb is real SQL over SQLite
 * with the plugin's own column shape, api.php is the REAL file, and the
 * assertions read the rows back afterwards.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/enrich_and_correct.php
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

/**
 * $wpdb over SQLite, with a real update() — including WordPress core's null
 * handling, which is the branch the explicit clear depends on. Core emits
 * "`col` = NULL" for a null value rather than binding it, so a stub that bound
 * it as an empty string would prove the opposite of what this harness is for.
 */
class WriteHarnessDb {
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
                funding_amount TEXT, funding_amount_usd INTEGER, funding_stage TEXT,
                archive_url TEXT, collector TEXT DEFAULT "google_news",
                source_url TEXT DEFAULT "", confidence TEXT DEFAULT "reported",
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

    /** tit_flush_caches() purges transients with a LIKE over wp_options. */
    public function query($sql) { $GLOBALS['tit_flushes']++; return 0; }
    public function esc_like($s) { return addcslashes((string) $s, '_%\\'); }

    /** WordPress core's semantics, null branch included. */
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
        $sql = "UPDATE {$table} SET " . implode(', ', $sets)
             . ' WHERE ' . implode(' AND ', $conds);
        $stmt = $this->pdo->prepare($sql);
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

$GLOBALS['wpdb'] = new WriteHarnessDb();
global $wpdb;
require $tit_plugin . 'talent-intelligence-tracker.php';

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

/* --- the rows, shaped like the five the audit found ---------------------- */

// Terminal: the hyphenated multiplier. A real dollar figure exists, so this one
// is an ordinary enrichment.
$wpdb->insert_row(array(
    'signal_id' => 'terminal', 'content_hash' => 'h-terminal',
    'company' => 'Terminal', 'company_key' => 'terminal',
    'funding_amount' => '$20-million USD', 'funding_amount_usd' => 20,
    'funding_stage' => 'series_a', 'collector' => 'google_news',
    'hq_country' => 'CA', 'archive_url' => 'https://web.archive.test/terminal',
));
// Visibuilt: Danish kroner. There IS no dollar figure, so the only true value
// is no value.
$wpdb->insert_row(array(
    'signal_id' => 'visibuilt', 'content_hash' => 'h-visibuilt',
    'company' => 'Visibuilt', 'company_key' => 'visibuilt',
    'funding_amount' => '25 millioner kroner', 'funding_amount_usd' => 25,
    'funding_stage' => 'seed', 'collector' => 'national_press',
    'hq_country' => 'DK', 'archive_url' => 'https://web.archive.test/visibuilt',
));

/* --- an absent or empty field never erases ------------------------------- */

tit_api_enrich(new WP_REST_Request(array('rows' => array(
    array('content_hash' => 'h-terminal', 'funding_amount_usd' => 20000000),
    // Every other enrichable column absent, and two of them sent empty. Neither
    // may touch what is already there.
    array('content_hash' => 'h-visibuilt', 'hq_country' => '', 'archive_url' => null,
          'funding_stage' => ''),
))));

$terminal = $wpdb->fetch('h-terminal');
check((int) $terminal['funding_amount_usd'] === 20000000,
      'an ordinary enrichment writes the corrected figure and wrote '
      . var_export($terminal['funding_amount_usd'], true));

$visibuilt = $wpdb->fetch('h-visibuilt');
check($visibuilt['hq_country'] === 'DK' && $visibuilt['funding_stage'] === 'seed'
      && $visibuilt['archive_url'] === 'https://web.archive.test/visibuilt',
      'an absent or empty field must not erase a known value: hq_country='
      . var_export($visibuilt['hq_country'], true) . ' funding_stage='
      . var_export($visibuilt['funding_stage'], true) . ' archive_url='
      . var_export($visibuilt['archive_url'], true));
check((int) $visibuilt['funding_amount_usd'] === 25,
      'and it must not have touched the wrong figure either, since nothing asked it to');

/* --- an explicit clear works, and only where it is allowed --------------- */

$out = tit_api_enrich(new WP_REST_Request(array('rows' => array(
    array('content_hash' => 'h-visibuilt', 'clear' => array('funding_amount_usd')),
))));
$visibuilt = $wpdb->fetch('h-visibuilt');
check($visibuilt['funding_amount_usd'] === null,
      'an explicitly named clear removes a figure that has no true value, and left '
      . var_export($visibuilt['funding_amount_usd'], true)
      . '. Without this there is no route at all, and a round raised in kroner '
      . 'stays on the page as US dollars.');
check(($out['updated'] ?? 0) === 1, 'and it reports the row it changed');
check($visibuilt['funding_amount'] === '25 millioner kroner',
      'the verbatim string a publisher wrote is untouched: it is the quotable form, '
      . 'and it is what makes the absent dollar figure legible rather than a gap');
check($visibuilt['hq_country'] === 'DK',
      'and clearing one column does not disturb another');

// hq_city and hq_country joined the allowlist in 1.77.0. A looked-up country
// with no headquarters city behind it is a guess, and 37 of them reached the
// live page; the only true value is no value, and this is the route for it.
$out = tit_api_enrich(new WP_REST_Request(array('rows' => array(
    array('content_hash' => 'h-terminal', 'clear' => array('hq_city', 'hq_country')),
))));
$after = $wpdb->fetch('h-terminal');
check($after['hq_country'] === null,
      'a wrong headquarters country can be taken back rather than left on the page, '
      . 'and hq_country is ' . var_export($after['hq_country'], true));
check(($out['updated'] ?? 0) === 1, 'and it reports the row it changed');
check((int) $after['funding_amount_usd'] === 20000000,
      'and clearing identity does not disturb the figure');

// The allowlist still is one. archive_url is enrichable and NOT clearable: it
// is the fallback that outlives a dead publisher, so clearing it loses work
// rather than removing a wrong claim.
$before = $wpdb->fetch('h-visibuilt');
$out = tit_api_enrich(new WP_REST_Request(array('rows' => array(
    array('content_hash' => 'h-visibuilt', 'clear' => array('archive_url')),
))));
$after = $wpdb->fetch('h-visibuilt');
check($after['archive_url'] === $before['archive_url'],
      'a column outside tit_clearable_columns() is refused, and archive_url went from '
      . var_export($before['archive_url'], true) . ' to ' . var_export($after['archive_url'], true));
check(!empty($out['errors']),
      'and the refusal is reported rather than silently skipped');

// Set and clear in one row is a caller bug, and must not resolve to either.
$out = tit_api_enrich(new WP_REST_Request(array('rows' => array(
    array('content_hash' => 'h-terminal', 'funding_amount_usd' => 999,
          'clear' => array('funding_amount_usd')),
))));
$after = $wpdb->fetch('h-terminal');
check((int) $after['funding_amount_usd'] === 20000000,
      'a column both set and cleared changes nothing and was '
      . var_export($after['funding_amount_usd'], true));
check(!empty($out['errors']), 'and that contradiction is reported');

/* --- /correct is scoped to the collector it names ------------------------ */

// The Toronto rows: Canadian issuers filing with the SEC, stored as country US.
// This is the correction those columns were added for.
$wpdb->insert_row(array(
    'signal_id' => 'toronto-a', 'content_hash' => 'h-toronto-a',
    'company' => 'A Toronto Issuer', 'company_key' => 'a toronto issuer',
    'city' => 'Toronto', 'country' => 'US', 'collector' => 'sec_edgar',
    'signal_direction' => 'neutral',
));
$wpdb->insert_row(array(
    'signal_id' => 'other', 'content_hash' => 'h-other',
    'company' => 'Somebody Else', 'company_key' => 'somebody else',
    'city' => 'Toronto', 'country' => 'US', 'collector' => 'google_news',
));

check(in_array('country', tit_correctable_columns(), true)
      && in_array('city', tit_correctable_columns(), true)
      && in_array('region', tit_correctable_columns(), true),
      'city, region and country have to be correctable or the city/region '
      . 'correction cannot run at all');

$out = tit_api_correct(new WP_REST_Request(array(
    'collector' => 'sec_edgar',
    'rows' => array(
        array('content_hash' => 'h-toronto-a', 'country' => 'CA'),
        // Same hash-less claim against a row belonging to a different collector.
        array('content_hash' => 'h-other', 'country' => 'CA'),
    ),
)));
check($wpdb->fetch('h-toronto-a')['country'] === 'CA',
      'the row belonging to the named collector is corrected');
check($wpdb->fetch('h-other')['country'] === 'US',
      'and a row belonging to ANOTHER collector is not touched, however the batch '
      . 'was built. A correction pass is written against one source\'s logic.');
check(($out['corrected'] ?? 0) === 1 && ($out['unchanged_or_missing'] ?? 0) === 1,
      'and the response says so: corrected=' . ($out['corrected'] ?? 'null')
      . ' unchanged_or_missing=' . ($out['unchanged_or_missing'] ?? 'null'));

// A correction still cannot blank a value. That route stays closed: these are
// assertions about a source, and an empty one is "no correction", never an
// erasure. Clearing a derived figure goes through /enrich's clear, above.
tit_api_correct(new WP_REST_Request(array(
    'collector' => 'sec_edgar',
    'rows' => array(array('content_hash' => 'h-toronto-a', 'city' => '', 'country' => null)),
)));
$row = $wpdb->fetch('h-toronto-a');
check($row['city'] === 'Toronto' && $row['country'] === 'CA',
      'an empty correction erases nothing: city=' . var_export($row['city'], true)
      . ' country=' . var_export($row['country'], true));

// And a value outside a closed vocabulary is refused rather than rendered as a
// label nothing filters on.
$out = tit_api_correct(new WP_REST_Request(array(
    'collector' => 'sec_edgar',
    'rows' => array(array('content_hash' => 'h-toronto-a',
                          'signal_direction' => 'vibes')),
)));
check($wpdb->fetch('h-toronto-a')['signal_direction'] === 'neutral',
      'a signal_direction outside the vocabulary is refused');
check(!empty($out['errors']), 'and the refusal is reported');

if ($failures) {
    fwrite(STDERR, "enrich/correct FAILED:\n  - " . implode("\n  - ", $failures) . "\n");
    exit(1);
}
echo "enrich/correct ok: an empty field erases nothing, an explicit clear works "
   . "only where allowed, and a correction cannot cross collectors.\n";
exit(0);
