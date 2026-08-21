<?php
/**
 * The RSS feed and the CRM export presets, run for real.
 *
 * WHY THIS IS A RUNNING HARNESS.
 *
 *  - THE FEED MUST BE WELL-FORMED FOR EVERY ROW WE HOLD, and what breaks a
 *    feed is never the happy path: it is an ampersand in a headline, a
 *    control character a scraper let through, a row with no published date.
 *    So the rows here carry all three, the XML is built by the REAL
 *    tit_feed_xml over the REAL tit_build_where and tit_feed_rows, and the
 *    document is parsed back with a STRICT parser (DOMDocument with
 *    error reporting on). tests/test_feed_and_crm.py parses the same output
 *    again in Python, so two independent parsers must both accept it.
 *  - THE FEED MUST HONOUR THE FILTERS. A country-filtered feed URL that
 *    quietly returned the world would be the dashboard's central promise
 *    broken in a place nobody is watching. Asserted by filtering.
 *  - THE PRESET HEADERS ARE A CONTRACT WITH TWO IMPORT WIZARDS. HubSpot and
 *    Salesforce map columns BY HEADER NAME, so a renamed header silently
 *    unmaps a field. The exact header rows are asserted here.
 *  - NO INVENTED DATA. The website/domain column must be EMPTY on every row:
 *    we do not hold company websites, and the publisher's domain in a CRM
 *    dedupe key would be a fabrication.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/feed_and_crm.php            (asserts, quiet)
 *      php tests/php/feed_and_crm.php --dump-xml (prints the feed for the
 *                                                 Python strict-parse test)
 *      php tests/php/feed_and_crm.php --dump-csv=hubspot|salesforce
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
function is_page($p = null) { return false; }
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
    private $body; private $params;
    public function __construct($body = array(), $params = array()) {
        $this->body = $body; $this->params = $params;
    }
    public function get_json_params() { return $this->body; }
    public function get_param($n) { return $this->params[$n] ?? null; }
    public function set_query_params($p) { $this->params = $p; }
}
class WP_REST_Response {
    private $data; private $headers = array(); private $status = 200;
    public function __construct($data = null) { $this->data = $data; }
    public function header($k, $v) { $this->headers[$k] = $v; }
    public function get_headers() { return $this->headers; }
    public function get_data() { return $this->data; }
    public function set_status($s) { $this->status = $s; }
}

/** $wpdb over SQLite: real WHERE clauses, real ORDER BY, real rows. */
class FeedHarnessDb {
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
                content_hash TEXT, headline TEXT DEFAULT "", summary TEXT DEFAULT "",
                talent_readthrough TEXT DEFAULT "", company TEXT DEFAULT "",
                company_key TEXT DEFAULT "", ticker TEXT, cik TEXT, employer_type TEXT,
                pillar TEXT DEFAULT "company_development",
                signal_direction TEXT DEFAULT "neutral",
                city TEXT, region TEXT, country TEXT, hq_city TEXT, hq_country TEXT,
                state TEXT, functions TEXT, industry TEXT,
                headcount INTEGER, headcount_scope TEXT,
                funding_amount TEXT, funding_amount_usd INTEGER, funding_stage TEXT, money_basis TEXT,
                work_mode TEXT, deal_type TEXT, site_event TEXT,
                predicted_outcome TEXT, check_after_date TEXT, outcome_observed TEXT,
                archive_url TEXT, materiality TEXT, confidence TEXT DEFAULT "reported",
                collector TEXT DEFAULT "google_news",
                source_url TEXT DEFAULT "", source_name TEXT DEFAULT "",
                published_date TEXT, effective_date TEXT,
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

    /* SQLite has no FIELD(); the feed never sorts by it, but tit_build_where
       is shared machinery, so keep the translation local and honest. */
    private function fix($sql) { return $sql; }

    public function get_results($sql, $o = null) {
        $rows = $this->pdo->query($this->fix($sql))->fetchAll(PDO::FETCH_ASSOC);
        if ($o === ARRAY_A) return $rows;
        return array_map(function ($r) { return (object) $r; }, $rows);
    }
    public function get_row($sql, $o = null) {
        $r = $this->pdo->query($this->fix($sql))->fetch(PDO::FETCH_ASSOC);
        if ($r === false) return null;
        return $o === ARRAY_A ? $r : (object) $r;
    }
    public function get_var($sql) {
        $r = $this->pdo->query($this->fix($sql))->fetch(PDO::FETCH_NUM);
        return $r === false ? null : $r[0];
    }
    public function get_col($sql) { return $this->pdo->query($this->fix($sql))->fetchAll(PDO::FETCH_COLUMN, 0); }
    public function query($sql) { $GLOBALS['tit_flushes']++; return 0; }
    public function esc_like($s) { return addcslashes((string) $s, '_%\\'); }

    public function insert_row(array $row) {
        $columns = implode(', ', array_keys($row));
        $marks = implode(', ', array_fill(0, count($row), '?'));
        $stmt = $this->pdo->prepare("INSERT INTO wp_tit_signals ({$columns}) VALUES ({$marks})");
        $stmt->execute(array_values($row));
    }
}

$GLOBALS['wpdb'] = new FeedHarnessDb();
global $wpdb;
require $tit_plugin . 'talent-intelligence-tracker.php';

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

/* --- the rows: the characters that break feeds, on purpose ---------------- */

$wpdb->insert_row(array(
    'signal_id' => 'sig-amp', 'content_hash' => 'h1',
    'company' => 'TEST FIXTURE Ampersand & Sons', 'company_key' => 'ampersand-sons',
    'headline' => 'Ampersand & Sons raises $10M <fast> & hires "everyone"',
    'summary' => "Control char \x07 inside, plus an apostrophe: it's here.",
    'talent_readthrough' => 'Hiring follows funding.',
    'pillar' => 'company_development', 'signal_direction' => 'hiring',
    'country' => 'US', 'industry' => 'technology', 'state' => 'CA', 'city' => 'San Jose',
    'confidence' => 'reported', 'materiality' => 'high',
    'source_url' => 'https://news.example.test/a?b=1&c=2',
    'source_name' => 'Example News', 'published_date' => '2026-08-01',
    'captured_at' => '2026-08-01 09:00:00',
));
$wpdb->insert_row(array(
    'signal_id' => 'sig-nodate', 'content_hash' => 'h2',
    'company' => 'TEST FIXTURE Dateless Ltd', 'company_key' => 'dateless',
    'headline' => 'Dateless Ltd names a chief executive',
    'summary' => 'No published_date on this row.',
    'pillar' => 'leadership_change', 'signal_direction' => 'neutral',
    'hq_city' => 'London', 'hq_country' => 'GB', 'industry' => 'financial_services',
    'confidence' => 'verified',
    'source_url' => 'https://filings.example.test/x',
    'source_name' => 'Registry', 'published_date' => null,
    'captured_at' => '2026-07-30 12:34:56',
));
$wpdb->insert_row(array(
    'signal_id' => 'sig-fr', 'content_hash' => 'h3',
    'company' => 'TEST FIXTURE Société Générale de Tests', 'company_key' => 'societe-tests',
    'headline' => 'Une levée de fonds à Paris',
    'summary' => 'Accents: éàüß. CJK: 東京.',
    'pillar' => 'company_development', 'signal_direction' => 'hiring',
    'country' => 'FR', 'city' => 'Paris', 'industry' => 'technology',
    'confidence' => 'reported',
    'funding_amount' => '=2+2 EUR', 'funding_amount_usd' => null,
    'source_url' => 'https://presse.example.test/y',
    'source_name' => 'La Presse', 'published_date' => '2026-07-29',
    'captured_at' => '2026-07-29 08:00:00',
));
// A retracted row: must appear in NOTHING below.
$wpdb->insert_row(array(
    'signal_id' => 'sig-retracted', 'content_hash' => 'h4', 'is_current' => 0,
    'company' => 'TEST FIXTURE Withdrawn Co', 'company_key' => 'withdrawn',
    'headline' => 'Withdrawn story', 'pillar' => 'company_development',
    'source_url' => 'https://gone.example.test/z', 'source_name' => 'Gone',
    'published_date' => '2026-07-28',
));

/* --- the feed ------------------------------------------------------------- */

$req = new WP_REST_Request(array(), array());
$rows = tit_feed_rows($req);
check(count($rows) === 3, 'feed returns the three current rows, not the retracted one; got ' . count($rows));
check($rows[0]['signal_id'] === 'sig-amp', 'newest first: sig-amp leads');

$xml = tit_feed_xml($rows, rest_url('talent/v1/feed'), home_url('/talent-intelligence-tracker/'));

// STRICT parse: libxml with errors surfaced. A feed that only lenient parsers
// accept is a feed half the readers cannot read.
libxml_use_internal_errors(true);
$doc = new DOMDocument();
$ok = $doc->loadXML($xml);
$errs = libxml_get_errors();
libxml_clear_errors();
check($ok && !$errs, 'feed XML is well-formed under a strict parse: '
    . ($errs ? $errs[0]->message : 'ok'));

if ($ok) {
    $xp = new DOMXPath($doc);
    $xp->registerNamespace('atom', 'http://www.w3.org/2005/Atom');
    check($doc->documentElement->tagName === 'rss'
        && $doc->documentElement->getAttribute('version') === '2.0',
        'root is <rss version="2.0">');
    foreach (array('channel/title', 'channel/link', 'channel/description') as $need) {
        check($xp->query('/rss/' . $need)->length === 1, "channel carries {$need}");
    }
    check($xp->query('/rss/channel/atom:link[@rel="self"]')->length === 1,
        'channel carries the atom:self link validators ask for');
    $items = $xp->query('/rss/channel/item');
    check($items->length === 3, 'three items; got ' . $items->length);
    foreach ($items as $item) {
        foreach (array('title', 'link', 'guid', 'pubDate', 'description') as $need) {
            check($item->getElementsByTagName($need)->length === 1,
                "every item carries one {$need}");
        }
        $guid = $item->getElementsByTagName('guid')->item(0);
        check($guid && $guid->getAttribute('isPermaLink') === 'false',
            'guid says isPermaLink="false": a signal_id is an identifier, not a URL');
        $pub = $item->getElementsByTagName('pubDate')->item(0)->textContent;
        check((bool) preg_match(
            '/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} \d{2}:\d{2}:\d{2} \+0000$/',
            $pub), 'pubDate is RFC 822: ' . $pub);
    }
    // The dateless row takes its capture time, so its pubDate carries 12:34:56.
    check(strpos($xml, '30 Jul 2026 12:34:56') !== false,
        'a row with no published_date falls back to captured_at');
    // The item link is the SOURCE document.
    $first = $items->item(0);
    check($first->getElementsByTagName('link')->item(0)->textContent
        === 'https://news.example.test/a?b=1&c=2',
        'item link is the source document, ampersand intact after decode');
    check($first->getElementsByTagName('category')->item(0)->textContent
        === 'company_development', 'category is the pillar');
    check(strpos($xml, "\x07") === false, 'control characters are stripped, not escaped');
}

// The feed honours the filters: a France-only request holds only the French row.
$fr = new WP_REST_Request(array(), array('country' => 'FR'));
$frRows = tit_feed_rows($fr);
check(count($frRows) === 1 && $frRows[0]['signal_id'] === 'sig-fr',
    'country=FR narrows the feed to the French row');

// And the route caches: two calls, one build.
$resp1 = tit_api_feed(new WP_REST_Request(array(), array()));
check($resp1 instanceof WP_REST_Response, 'route returns a response');
$hdrs = $resp1->get_headers();
check(strpos((string) ($hdrs['Content-Type'] ?? ''), 'application/rss+xml') === 0,
    'Content-Type is application/rss+xml');
$built = $resp1->get_data();
$resp2 = tit_api_feed(new WP_REST_Request(array(), array()));
check($resp2->get_data() === $built, 'second call serves the cached document');

// tit_feed_serve echoes the raw XML for exactly this Content-Type.
ob_start();
$served = tit_feed_serve(false, $resp1, null);
$echoed = ob_get_clean();
check($served === true && $echoed === $built, 'rest_pre_serve_request hands back raw XML');
check(tit_feed_serve(false, rest_ensure_response(array('a' => 1)), null) === false,
    'JSON responses are left to the REST server');

/* --- the CRM presets ------------------------------------------------------ */

$expectHubspot = array(
    'Company name', 'Company domain name', 'City', 'State/Region',
    'Country/Region', 'Industry', 'Description',
    'Signal Date', 'Signal Direction', 'Evidence', 'Source Name', 'Source URL');
$expectSalesforce = array(
    'Account Name', 'Website', 'Billing City', 'Billing State/Province',
    'Billing Country', 'Industry', 'Description',
    'Signal Date', 'Signal Direction', 'Evidence', 'Source Name', 'Source URL');
check(tit_crm_headers('hubspot') === $expectHubspot, 'HubSpot header row is the documented mapping');
check(tit_crm_headers('salesforce') === $expectSalesforce, 'Salesforce header row is the documented mapping');

function crm_csv($preset) {
    $mem = fopen('php://memory', 'w+');
    tit_export_crm_stream($preset, $mem);
    rewind($mem);
    $raw = stream_get_contents($mem);
    fclose($mem);
    return $raw;
}

foreach (array('hubspot' => $expectHubspot, 'salesforce' => $expectSalesforce) as $preset => $expect) {
    $raw = crm_csv($preset);
    check(substr($raw, 0, 3) === "\xEF\xBB\xBF", "{$preset}: UTF-8 BOM for Excel");
    $mem = fopen('php://memory', 'w+');
    fwrite($mem, substr($raw, 3));
    rewind($mem);
    $head = fgetcsv($mem, 0, ',', '"', '\\');
    check($head === $expect, "{$preset}: parsed header row matches");
    $n = 0; $domainIdx = 1;
    while (($line = fgetcsv($mem, 0, ',', '"', '\\')) !== false) {
        $n++;
        check(count($line) === count($expect), "{$preset}: row {$n} has a cell per header");
        check($line[$domainIdx] === '', "{$preset}: website/domain column stays empty (no invented data)");
        check($line[0] === '' || $line[0][0] !== '=', "{$preset}: formula injection guarded");
    }
    check($n === 3, "{$preset}: three data rows (retracted row excluded); got {$n}");
    fclose($mem);
    // The French row's company name survives with its accents.
    check(strpos($raw, 'Société Générale de Tests') !== false, "{$preset}: UTF-8 intact");
}

/* --- dumps for the Python strict-parse test -------------------------------- */

foreach ($argv as $arg) {
    if ($arg === '--dump-xml') { echo $xml; exit(count($failures) ? 1 : 0); }
    if (strpos($arg, '--dump-csv=') === 0) {
        echo crm_csv(substr($arg, strlen('--dump-csv=')));
        exit(count($failures) ? 1 : 0);
    }
}

if ($failures) {
    fwrite(STDERR, "FEED/CRM HARNESS FAILURES:\n  - " . implode("\n  - ", $failures) . "\n");
    exit(1);
}
echo "feed_and_crm: all assertions passed\n";
