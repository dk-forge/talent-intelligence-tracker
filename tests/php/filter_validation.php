<?php
/**
 * AN UNKNOWN FILTER VALUE MUST NOT WIDEN THE ANSWER. Run for real.
 *
 * THE DEFECT, measured live on 2026-08-20 against the deployed endpoint, with
 * an unfiltered /aggregate total of 31,162:
 *
 *     ?pillar=leadership_change   ->  16,493   honoured
 *     ?pillar=leadership_chang    ->  31,162   silently dropped
 *     ?pillar=hiring_expansion    ->  31,162   silently dropped
 *     ?funding=banana             ->  31,162   silently dropped
 *     ?confidence=verifed         ->  31,162   silently dropped
 *     ?since=banana               ->  31,162   silently dropped
 *     ?sort=nonsense              ->  31,162   silently reordered
 *
 * Each of those answered HTTP 200 with the WORLDWIDE TOTAL wearing the label
 * the caller asked for. A caller that publishes "Leadership moves: 31,162"
 * off the back of one dropped character has published a wrong number, and no
 * failed-call guard anywhere can see it, because no call failed.
 *
 * WHY THIS IS A RUNNING HARNESS AND NOT A SOURCE READ.
 *
 * What has to be proven is what the ENDPOINT ANSWERS, and specifically that
 * the answer to a bad value is not the answer to no value. A source read can
 * prove the string `WP_Error` appears in the file; it cannot prove that
 * `pillar=leadership_chang` stopped returning every row in the table. So
 * $wpdb is real SQL over SQLite with the plugin's own column shape, api.php
 * and feed.php are the REAL files, the routes are entered through the REAL
 * tit_api_query / tit_api_aggregate / tit_api_feed, and every assertion is a
 * status code or a row count off a SELECT.
 *
 * THE VALID VALUES ARE NEVER WRITTEN DOWN HERE. Every vocabulary assertion
 * walks tit_filter_spec(), which reads the tit_allowed_*() functions that
 * already declare the vocabularies. Renaming a pillar therefore changes what
 * this test demands, in the same edit. And the last block below re-reads
 * tit_build_where's own source for every parameter it consults and fails when
 * one of them is missing from the registry: a filter added tomorrow without a
 * spec entry reopens this defect, and that is the shape it would arrive in.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/filter_validation.php
 *
 * To watch it fail against the pre-fix code:
 *     mkdir -p /tmp/prefix/includes
 *     git show origin/main:wordpress-plugin/talent-intelligence-tracker/includes/api.php \
 *       > /tmp/prefix/includes/api.php
 *     git show origin/main:wordpress-plugin/talent-intelligence-tracker/includes/feed.php \
 *       > /tmp/prefix/includes/feed.php
 *     TIT_PLUGIN_DIR=/tmp/prefix php tests/php/filter_validation.php
 */

define('ABSPATH', __DIR__);
$tit_plugin = getenv('TIT_PLUGIN_DIR')
    ? rtrim(getenv('TIT_PLUGIN_DIR'), '/') . '/'
    : __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/';

function plugin_dir_path($file) { return dirname($file) . '/'; }
function plugin_dir_url($file) { return 'https://example.test/plugin/'; }
define('MINUTE_IN_SECONDS', 60);
define('HOUR_IN_SECONDS', 3600);
define('DAY_IN_SECONDS', 86400);
define('ARRAY_A', 'ARRAY_A');

$GLOBALS['tit_transients'] = array();
$GLOBALS['tit_ephemeral'] = array();

function add_action($h, $f, $p = 10, $a = 1) {}
function add_filter($h, $f, $p = 10, $a = 1) {}
function apply_filters($h, $v) { return $v; }
function do_action($h) {}
function register_rest_route($ns, $route, $args) {}
function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url_raw($s) { return (string) $s; }
function esc_attr($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function rest_url($p = '') { return 'https://example.test/blog/wp-json/' . $p; }
function home_url($p = '') { return 'https://example.test/blog' . $p; }
function wp_json_encode($v, $flags = 0) { return json_encode($v, $flags); }
function sanitize_text_field($s) { return trim((string) $s); }
function current_time($t, $gmt = 0) { return $t === 'timestamp' ? time() : gmdate($t); }
function get_option($k, $d = false) { return $d; }
function update_option($k, $v, $a = null) { return true; }
function delete_transient($k) { unset($GLOBALS['tit_transients'][$k]); return true; }
function get_transient($k) { return $GLOBALS['tit_transients'][$k] ?? false; }
function set_transient($k, $v, $t = 0) { $GLOBALS['tit_transients'][$k] = $v; return true; }
function tit_ephemeral_get($k) { return $GLOBALS['tit_ephemeral'][$k] ?? 0; }
function tit_ephemeral_set($k, $v, $t = 0) { $GLOBALS['tit_ephemeral'][$k] = $v; return true; }
function rest_ensure_response($d) { return ($d instanceof WP_Error) ? $d : new WP_REST_Response($d); }
function __($s, $d = '') { return $s; }
function absint($v) { return abs((int) $v); }
function is_wp_error($t) { return $t instanceof WP_Error; }
function tit_table_name() { return 'wp_tit_signals'; }
function tit_flush_caches() {}

class WP_Error {
    public $code; public $message; public $data;
    public function __construct($c = '', $m = '', $d = null) {
        $this->code = $c; $this->message = $m; $this->data = $d;
    }
    public function get_error_message() { return $this->message; }
    public function get_error_code() { return $this->code; }
    public function get_error_data() { return $this->data; }
}
class WP_REST_Request {
    private $params;
    public function __construct($params = array()) { $this->params = $params; }
    public function get_param($n) { return $this->params[$n] ?? null; }
    public function get_json_params() { return array(); }
    public function set_query_params($p) { $this->params = $p; }
}
class WP_REST_Response {
    private $data; private $headers = array();
    public function __construct($data = null) { $this->data = $data; }
    public function header($k, $v) { $this->headers[$k] = $v; }
    public function get_data() { return $this->data; }
}

/** $wpdb over SQLite: real WHERE clauses over real rows. */
class FilterHarnessDb {
    public $pdo;
    public $prefix = 'wp_';
    public $options = 'wp_options';
    public $last_error = '';
    private $suppress = false;

    public function __construct() {
        $this->pdo = method_exists('PDO', 'connect')
            ? PDO::connect('sqlite::memory:')
            : new PDO('sqlite::memory:');
        $this->pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $register = method_exists($this->pdo, 'createFunction')
            ? array($this->pdo, 'createFunction')
            : array($this->pdo, 'sqliteCreateFunction');
        // The boundary probe in api.php runs through this; PCRE stands in for
        // the server's ICU engine exactly as tests/php/search_boundary.php.
        call_user_func($register, 'regexp', function ($pattern, $subject) {
            $hit = @preg_match('/' . $pattern . '/iu', (string) $subject);
            if ($hit === false) throw new RuntimeException("bad regex: $pattern");
            return $hit ? 1 : 0;
        }, 2);
        call_user_func($register, 'field', function () {
            $args = func_get_args();
            $needle = array_shift($args);
            $at = array_search($needle, $args, true);
            return $at === false ? 0 : $at + 1;
        }, -1);
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
    public function suppress_errors($s = true) { $was = $this->suppress; $this->suppress = $s; return $was; }
    private function run($sql) {
        try { return $this->pdo->query($sql); }
        catch (Exception $e) { if ($this->suppress) return null; throw $e; }
    }
    public function get_results($sql, $o = null) {
        $st = $this->run($sql);
        if ($st === null) return array();
        $rows = $st->fetchAll(PDO::FETCH_ASSOC);
        if ($o === ARRAY_A) return $rows;
        return array_map(function ($r) { return (object) $r; }, $rows);
    }
    public function get_var($sql) {
        $st = $this->run($sql);
        if ($st === null) return null;
        $r = $st->fetch(PDO::FETCH_NUM);
        return $r === false ? null : (string) $r[0];
    }
    public function get_row($sql, $o = null) {
        $st = $this->run($sql);
        if ($st === null) return null;
        $r = $st->fetch(PDO::FETCH_ASSOC);
        if ($r === false) return null;
        return $o === ARRAY_A ? $r : (object) $r;
    }
    public function get_col($sql) {
        $st = $this->run($sql);
        return $st === null ? array() : $st->fetchAll(PDO::FETCH_COLUMN, 0);
    }
    public function query($sql) { return 0; }
    public function esc_like($s) { return addcslashes((string) $s, '_%\\'); }
    public function insert_row(array $row) {
        $columns = implode(', ', array_keys($row));
        $marks = implode(', ', array_fill(0, count($row), '?'));
        $stmt = $this->pdo->prepare("INSERT INTO wp_tit_signals ({$columns}) VALUES ({$marks})");
        $stmt->execute(array_values($row));
    }
}

$GLOBALS['wpdb'] = new FilterHarnessDb();
require $tit_plugin . 'includes/api.php';
require $tit_plugin . 'includes/feed.php';

/* --- the corpus ----------------------------------------------------------
   Shaped like the live one in the only way that matters here: one pillar is
   the plurality and none is the whole, so "the filter was dropped" and "the
   filter was honoured" are different numbers and the test can tell them
   apart. A corpus where every row shared a pillar would pass either way. */

$corpus = array(
    array('pillar' => 'leadership_change', 'confidence' => 'verified', 'country' => 'US',
          'industry' => 'technology', 'funding_stage' => 'series_b', 'funding_amount' => '$40m',
          'signal_direction' => 'hiring', 'state' => 'CA', 'employer_type' => 'public',
          'work_mode' => 'hybrid', 'deal_type' => 'merger', 'site_event' => 'opened',
          'money_basis' => 'merger',
          'functions' => '["engineering"]', 'headcount' => 200, 'funding_amount_usd' => 40000000,
          'materiality' => 'high', 'city' => 'San Francisco', 'published_date' => '2026-08-11'),
    array('pillar' => 'leadership_change', 'confidence' => 'reported', 'country' => 'GB',
          'industry' => 'financial_services', 'signal_direction' => 'neutral',
          'materiality' => 'routine', 'published_date' => '2026-08-12'),
    array('pillar' => 'leadership_change', 'confidence' => 'reported', 'country' => 'IN',
          'industry' => 'technology', 'published_date' => '2026-08-13'),
    array('pillar' => 'rewards_comp', 'confidence' => 'verified', 'country' => 'US',
          'industry' => 'healthcare', 'signal_direction' => 'comp_shift',
          'published_date' => '2026-08-14'),
    array('pillar' => 'rewards_comp', 'confidence' => 'rumored', 'country' => 'DE',
          'published_date' => '2026-08-15'),
    array('pillar' => 'company_development', 'confidence' => 'verified', 'country' => 'US',
          'funding_stage' => 'seed', 'funding_amount' => '$3m', 'funding_amount_usd' => 3000000,
          'deal_type' => 'acquisition', 'money_basis' => 'acquisition',
          'published_date' => '2026-08-16'),
    array('pillar' => 'how_we_work', 'confidence' => 'reported', 'country' => 'FR',
          'work_mode' => 'remote', 'published_date' => '2026-08-17'),
);
foreach ($corpus as $i => $row) {
    $row += array('signal_id' => 'sig' . $i, 'is_current' => 1,
                  'company' => 'Company ' . $i, 'company_key' => 'company ' . $i,
                  'headline' => 'Signal number ' . $i,
                  'source_url' => 'https://example.test/' . $i,
                  'source_name' => 'Example');
    $GLOBALS['wpdb']->insert_row($row);
}
$UNFILTERED = count($corpus);

/* --- assertions ---------------------------------------------------------- */

$failures = array();
function fail($m) { global $failures; $failures[] = $m; }
function check($label, $got, $want) {
    if ($got !== $want) fail("$label: expected " . var_export($want, true)
                           . ", got " . var_export($got, true));
}

/** What /query answers for these params: an int total, or 'HTTP <status>'. */
function query_answer(array $params) {
    $GLOBALS['tit_transients'] = array();   // never answer from a warm cache
    $out = tit_api_query(new WP_REST_Request($params));
    if ($out instanceof WP_Error) {
        $data = $out->get_error_data();
        return 'HTTP ' . (isset($data['status']) ? $data['status'] : '?');
    }
    return (int) $out->get_data()['total'];
}

/** The same question of /aggregate. */
function aggregate_answer(array $params) {
    $GLOBALS['tit_transients'] = array();
    $params += array('include' => 'fresh');   // the cheap shape; same WHERE clause
    $out = tit_api_aggregate(new WP_REST_Request($params));
    if ($out instanceof WP_Error) {
        $data = $out->get_error_data();
        return 'HTTP ' . (isset($data['status']) ? $data['status'] : '?');
    }
    return (int) $out->get_data()['total'];
}

/** And of the RSS feed, counted in <item> elements. */
function feed_answer(array $params) {
    $GLOBALS['tit_transients'] = array();
    $GLOBALS['tit_ephemeral'] = array();
    $out = tit_api_feed(new WP_REST_Request($params));
    if ($out instanceof WP_Error) {
        $data = $out->get_error_data();
        return 'HTTP ' . (isset($data['status']) ? $data['status'] : '?');
    }
    $body = $out instanceof WP_REST_Response ? $out->get_data() : (string) $out;
    return substr_count((string) $body, '<item>');
}

/* 1. THE BASELINE. Everything below is read against these two numbers. */
check('unfiltered /query total', query_answer(array()), $UNFILTERED);
check('unfiltered /aggregate total', aggregate_answer(array()), $UNFILTERED);

/* 2. A VALID VALUE STILL FILTERS. If this ever fails, the fix has broken the
      product rather than the defect. */
$LEADERSHIP = 3;
check('pillar=leadership_change filters /query',
      query_answer(array('pillar' => 'leadership_change')), $LEADERSHIP);
check('pillar=leadership_change filters /aggregate',
      aggregate_answer(array('pillar' => 'leadership_change')), $LEADERSHIP);
check('pillar=leadership_change filters the feed',
      feed_answer(array('pillar' => 'leadership_change')), $LEADERSHIP);
check('confidence=verified filters', query_answer(array('confidence' => 'verified')), 3);
check('industry=technology,healthcare filters',
      query_answer(array('industry' => 'technology,healthcare')), 3);
check('funding=1 filters', query_answer(array('funding' => '1')), 2);
check('funding=0 is off and says so', query_answer(array('funding' => '0')), $UNFILTERED);
check('country=US,GB filters', query_answer(array('country' => 'US,GB')), 4);
check('country=us lowercases up', query_answer(array('country' => 'us')), 3);
check('since narrows', query_answer(array('since' => '2026-08-15')), 3);
check('detail=notable sets routine aside',
      query_answer(array('detail' => 'notable')), $UNFILTERED - 1);

/* 3. AN UNKNOWN VALUE IS REFUSED, AND ABOVE ALL IS NOT THE UNFILTERED TOTAL.
      This is the table measured against the live endpoint, one row per line. */
$bad = array(
    // The one-character typo that started this.
    array('pillar' => 'leadership_chang'),
    // A pillar name from a plausible other vocabulary. This is the rename
    // case: a caller still sending a name this plugin no longer uses.
    array('pillar' => 'hiring_expansion'),
    array('funding' => 'banana'),
    array('confidence' => 'verifed'),
    array('direction' => 'hirings'),
    array('industry' => 'nonsense'),
    // One good value and one bad one in the same list. The bad one used to be
    // dropped while the good one applied, so the answer was a filtered number
    // that was not the filter anyone asked for.
    array('industry' => 'technology,nonsense'),
    array('function' => 'enginering'),
    array('employer_type' => 'publik'),
    array('work_mode' => 'hybird'),
    array('deal_type' => 'aquisition'),
    array('money_basis' => 'company_rais'),
    array('site_event' => 'oppened'),
    array('funding_stage' => 'series_bb'),
    array('detail' => 'notabl'),
    array('country_basis' => 'locaton'),
    array('stated_headcount' => 'yes'),
    array('country' => 'USA'),
    array('state' => 'CAL'),
    array('since' => 'banana'),
    array('until' => '2026-13-99x'),
    array('min_headcount' => 'lots'),
    array('min_funding_usd' => '10m'),
    array('sort' => 'nonsense'),
    array('include' => 'frsh'),
    array('page' => 'two'),
    array('per_page' => 'fifty'),
);
foreach ($bad as $params) {
    $label = key($params) . '=' . current($params);
    $got = query_answer($params);
    if ($got === $UNFILTERED) {
        fail("/query?$label ANSWERED THE UNFILTERED TOTAL ($UNFILTERED). "
           . "This is the defect: the filter was dropped and the caller was "
           . "handed the whole corpus under the label it asked for.");
    } elseif ($got !== 'HTTP 400') {
        fail("/query?$label: expected 'HTTP 400', got " . var_export($got, true));
    }
    // The same value, refused the same way, on every surface built on
    // tit_build_where. A route that validates and a route that does not is
    // this defect with one fewer place to find it.
    $agg = aggregate_answer($params);
    if ($agg !== 'HTTP 400') {
        fail("/aggregate?$label: expected 'HTTP 400', got " . var_export($agg, true));
    }
    if (!isset($params['sort']) && !isset($params['include'])
        && !isset($params['page']) && !isset($params['per_page'])) {
        $rss = feed_answer($params);
        if ($rss !== 'HTTP 400') {
            fail("/feed?$label: expected 'HTTP 400', got " . var_export($rss, true));
        }
    }
}

/* 4. THE MESSAGE HAS TO BE ACTIONABLE. A 400 that does not say which
      parameter is wrong moves the guesswork rather than ending it. */
$err = tit_api_query(new WP_REST_Request(array('pillar' => 'leadership_chang')));
if (!($err instanceof WP_Error)) {
    fail('a bad pillar did not produce a WP_Error at all');
} else {
    $msg = $err->get_error_message();
    if (strpos($msg, 'pillar') === false) fail("the 400 does not name the parameter: $msg");
    if (strpos($msg, 'leadership_chang') === false) fail("the 400 does not quote the value: $msg");
    foreach (tit_allowed_pillars() as $ok) {
        if (strpos($msg, $ok) === false) fail("the 400 does not list '$ok' as valid: $msg");
    }
    $data = $err->get_error_data();
    check('the error names the parameter in its data', $data['param'], 'pillar');
    check('the error carries a 400 status', $data['status'], 400);
}

/* 5. AN EMPTY VALUE IS "NOT SUPPLIED", EXPLICITLY. Every select on the
      dashboard has an "Any" option whose value is the empty string, and a
      browser submits `pillar=` for it. This is the one case where returning
      the unfiltered total is the right answer, and it is asserted rather than
      left to fall out of the implementation. */
if (!function_exists('tit_filter_spec')) {
    fail('tit_filter_spec() does not exist, so there is no single definition '
       . 'of what each filter accepts and the registry-driven blocks below '
       . 'cannot run at all');
}
foreach (function_exists('tit_filter_spec') ? array_keys(tit_filter_spec()) : array() as $name) {
    $got = query_answer(array($name => ''));
    if ($got !== $UNFILTERED) {
        fail("?$name= (empty) should mean 'no filter' and answer $UNFILTERED, got "
           . var_export($got, true));
    }
    $spaces = query_answer(array($name => '   '));
    if ($spaces !== $UNFILTERED) {
        fail("?$name=<whitespace> should mean 'no filter', got " . var_export($spaces, true));
    }
}

/* 6. THE VALID-VALUE LIST COMES FROM THE SHARED DEFINITION.
      Not from a copy in this file and not from a copy in the route handler.
      Rename a pillar in tit_allowed_pillars() and this block demands the new
      name and refuses the old one, with no edit here. */
$vocabularies = array(
    'pillar'        => 'tit_allowed_pillars',
    'direction'     => 'tit_allowed_directions',
    'confidence'    => 'tit_allowed_confidence',
    'industry'      => 'tit_allowed_industries',
    'employer_type' => 'tit_allowed_employer_types',
    'work_mode'     => 'tit_allowed_work_modes',
    'deal_type'     => 'tit_allowed_deal_types',
    // The filter behind the "Total Raised" figure's own deep link. Every value
    // it accepts has to be a value the vocabulary declares, or the link the
    // matrix prints lands on an HTTP 400 instead of on the rows it summed.
    'money_basis'   => 'tit_allowed_money_bases',
    'site_event'    => 'tit_allowed_site_events',
    'function'      => 'tit_allowed_functions',
    'funding_stage' => 'tit_allowed_funding_stages',
);
foreach (function_exists('tit_filter_allowed') ? $vocabularies : array() as $param => $declaring) {
    check("the registry reads $param from $declaring()",
          tit_filter_allowed($param), call_user_func($declaring));
    foreach (call_user_func($declaring) as $value) {
        // Every value the vocabulary declares is accepted...
        if (query_answer(array($param => $value)) === 'HTTP 400') {
            fail("$param=$value is declared by $declaring() and was REFUSED");
        }
        // ...and the same value one character short is not. A rename that
        // reached the vocabulary but not the API would fail here.
        $typo = substr($value, 0, -1);
        if ($typo !== '' && !in_array($typo, call_user_func($declaring), true)) {
            $got = query_answer(array($param => $typo));
            if ($got !== 'HTTP 400') {
                fail("$param=$typo (one character short of a real value) "
                   . "should be refused, got " . var_export($got, true));
            }
        }
    }
}
/* 6b. A DROPDOWN MAY NOT OFFER A VALUE THE FILTER REFUSES.

      The fetched controls are data-driven -- /facets reads the DISTINCT values
      out of the column -- while the filter behind them is closed-vocabulary.
      Those two can disagree, and when they do the reader picks an option and
      gets an HTTP 400 from a control the page built for them.

      money_basis is the one where it matters most: the "Total Raised" figure
      links to `money_basis=company_raise`, so this control is not decoration,
      it is what makes a published figure's own link work. */
$facet_columns = array('money_bases' => 'money_basis', 'deal_types' => 'deal_type',
                       'employer_types' => 'employer_type', 'work_modes' => 'work_mode',
                       'site_events' => 'site_event', 'funding_stages' => 'funding_stage');
$GLOBALS['tit_transients'] = array();
$facets = function_exists('tit_api_facets') ? tit_api_facets() : null;
$facets = $facets instanceof WP_REST_Response ? $facets->get_data() : $facets;
$facets = is_array($facets) ? $facets : null;
if (!is_array($facets)) {
    fail('tit_api_facets() did not answer with an array, so the controls the '
       . 'dashboard fills from it cannot be checked against the filters');
} else {
    foreach ($facet_columns as $key => $param) {
        if (!array_key_exists($key, $facets)) {
            fail("/facets does not offer `{$key}`, so the {$param} control "
               . 'renders empty, hides itself, and any link carrying that '
               . 'parameter is dropped in the browser');
            continue;
        }
        foreach ((array) $facets[$key] as $value) {
            if (query_answer(array($param => $value)) === 'HTTP 400') {
                fail("/facets offers {$param}={$value} and /query REFUSES it: "
                   . 'the page would build a dropdown option that answers 400');
            }
        }
    }
    // And the one the money figure's link depends on is really there.
    if (!in_array('acquisition', (array) ($facets['money_bases'] ?? array()), true)) {
        fail('/facets did not list the money bases the corpus carries, so the '
           . 'Kind of Money control would render empty on a table that has them');
    }
}

if (!function_exists('tit_query_orders')) {
    fail('tit_query_orders() does not exist, so the sort names the validator '
       . 'would check are not the sort names /query answers by');
}
foreach (function_exists('tit_query_orders') ? array_keys(tit_query_orders()) : array() as $sort) {
    if (query_answer(array('sort' => $sort)) === 'HTTP 400') {
        fail("sort=$sort is a real ORDER BY in tit_query_orders() and was REFUSED");
    }
}

/* 7. EVERY PARAMETER tit_build_where READS IS IN THE REGISTRY.
      The defect's real shape is not "pillar was wrong" but "a filter was
      added and nobody validated it". This reads the where-builder's OWN
      SOURCE and fails on a parameter the registry has never heard of, so the
      next filter cannot arrive unvalidated and quiet. */
$source = file_get_contents($tit_plugin . 'includes/api.php');
$start  = strpos($source, 'function tit_build_where(');
$end    = strpos($source, 'function tit_cache_key(');
if ($start === false || $end === false || $end < $start) {
    fail('could not locate tit_build_where in the source to audit its parameters');
} else {
    $body = substr($source, $start, $end - $start);
    preg_match_all("/get_param\(\s*'([a-z_]+)'\s*\)/", $body, $m);
    preg_match_all("/tit_multi_param\(\s*\\\$req\s*,\s*'([a-z_]+)'/", $body, $m2);
    $read = array_unique(array_merge($m[1], $m2[1]));
    $spec = function_exists('tit_filter_spec') ? tit_filter_spec() : array();
    foreach ($read as $param) {
        if (!isset($spec[$param])) {
            fail("tit_build_where reads '$param' but tit_filter_spec() does not "
               . "declare it, so an unknown value for it is silently dropped");
        }
    }
    if (count($read) < 15) {
        fail('the parameter audit found only ' . count($read)
           . ' parameters, which means the scan stopped working, not that the '
           . 'endpoint got simpler');
    }
}

/* 8. THE DOWNLOADS ARE GUARDED TOO. export.php runs under admin_post and ends
      in exit(), which cannot be entered in-process without taking this harness
      with it, so this one assertion is a source read and says so. What it
      guards is real: a CSV is the surface where the mistake outlives the
      request, saved and mailed on with nothing on it to say the filter in its
      filename never reached the query. */
$export_src = @file_get_contents($tit_plugin . 'includes/export.php');
if ($export_src === false) {
    // Only the api.php/feed.php pair is required for the pre-fix comparison.
    if (!getenv('TIT_PLUGIN_DIR')) fail('includes/export.php is missing');
} else {
    $fn_start = strpos($export_src, 'function tit_export_filters(');
    $fn = $fn_start === false ? '' : substr($export_src, $fn_start, 1400);
    if (strpos($fn, 'tit_validate_filters') === false) {
        fail('tit_export_filters() does not validate its filters, so a CSV '
           . 'download silently widens to the whole corpus on a typo');
    }
    if (strpos($fn, '400') === false) {
        fail('tit_export_filters() does not answer a bad filter with a 400');
    }
}

/* --- verdict -------------------------------------------------------------- */

if ($failures) {
    fwrite(STDERR, "FAIL (" . count($failures) . ")\n");
    foreach ($failures as $f) fwrite(STDERR, "  - $f\n");
    exit(1);
}
echo "filter_validation: OK (" . $UNFILTERED . " rows, every filter checked "
   . "against tit_filter_spec())\n";
