<?php
/**
 * THE PUBLIC SEARCH BOX, RUN AS REAL SQL OVER REAL ROWS.
 *
 * MEASURED 2026-08-18 against the live endpoint:
 *
 *     /talent/v1/query?q=EY  ->  13,934 of 30,986 rows
 *
 * Two letters, matched as a bare substring, so "money", "survey", "Monterrey",
 * "key" and "attorney" all answered and the top hits for a reader searching a
 * Big Four firm were a Brazilian space startup and a Bolivian statistics
 * agency. The sibling layoff tracker returned 1,968 of 65,441 for the same
 * term, and its INGEST gate had already been bitten by the identical class
 * when `layoff` matched `playoff`.
 *
 * WHY THIS IS A RUNNING HARNESS AND NOT A SOURCE READ.
 *
 * What is being asserted is which ROWS come back, and that is a property of
 * the SQL, the collation and the corpus together. A source read can prove that
 * the word `REGEXP` appears; it cannot prove that `q=EY` stopped returning the
 * Bolivian census. So $wpdb is real SQL over SQLite with the plugin's own
 * column shape, api.php is the REAL file, the clause is built by the REAL
 * tit_build_where, and every assertion below is a row count off a SELECT.
 *
 * SQLite has no REGEXP of its own, so one is registered here as PCRE with /iu.
 * That is a deliberate stand-in for the server's ICU engine and the two agree
 * on `\b` over Latin text, which is the only thing tit_boundary_pattern ever
 * emits a boundary for. WHICH DIALECT THE REAL SERVER SPEAKS IS NOT ASSUMED
 * ANYWHERE: tit_regexp_boundary_syntax() proves it at run time with a positive
 * AND a negative probe, and that probe runs here too, through this same SQL.
 *
 * THE CJK ROWS ARE REAL. Four of the rows below are the actual stored
 * headlines of Korean and Japanese signals in the live corpus (145 Korean and
 * 434 Japanese rows on the day of the fix). A word boundary needs a non-word
 * character on the far side of the term and Japanese is written without one,
 * so a boundary applied there would return NOTHING while looking like an
 * honest empty result. Those searches must return exactly what they returned
 * before the fix, and that is asserted, not hoped for.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/search_boundary.php
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

function add_action($h, $f, $p = 10, $a = 1) {}
function add_filter($h, $f, $p = 10, $a = 1) {}
function apply_filters($h, $v) { return $v; }
function do_action($h) {}
function register_rest_route($ns, $route, $args) {}
function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url_raw($s) { return (string) $s; }
function wp_json_encode($v, $flags = 0) { return json_encode($v, $flags); }
function sanitize_text_field($s) { return trim((string) $s); }
function current_time($t, $gmt = 0) { return $t === 'timestamp' ? time() : gmdate($t); }
function get_option($k, $d = false) { return $d; }
function update_option($k, $v, $a = null) { return true; }
function delete_transient($k) { unset($GLOBALS['tit_transients'][$k]); return true; }
function get_transient($k) { return $GLOBALS['tit_transients'][$k] ?? false; }
function set_transient($k, $v, $t = 0) { $GLOBALS['tit_transients'][$k] = $v; return true; }
function rest_ensure_response($d) { return new WP_REST_Response($d); }
function __($s, $d = '') { return $s; }
function absint($v) { return abs((int) $v); }

class WP_Error {
    public $code; public $message;
    public function __construct($c = '', $m = '') { $this->code = $c; $this->message = $m; }
    public function get_error_message() { return $this->message; }
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

/** $wpdb over SQLite: real WHERE clauses, real rows, a real REGEXP operator. */
class SearchHarnessDb {
    public $pdo;
    public $prefix = 'wp_';
    public $options = 'wp_options';
    public $last_error = '';
    private $suppress = false;
    /** Every pattern the code under test actually sent to the engine. */
    public $patterns_seen = array();

    public function __construct() {
        // PHP 8.4 moved the sqlite extras onto Pdo\Sqlite and deprecated the
        // PDO ones. A deprecation notice on STDERR is how a green harness
        // starts looking red, so take the subclass where it exists.
        $this->pdo = method_exists('PDO', 'connect')
            ? PDO::connect('sqlite::memory:')
            : new PDO('sqlite::memory:');
        $this->pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        // MySQL 8 runs ICU; PCRE with /iu is the stand-in. An UNPARSEABLE
        // pattern must behave the way MySQL does with a foreign dialect -- an
        // error, never a silent 0 -- so that tit_regexp_boundary_syntax()'s
        // probe cannot "pass" a syntax this engine does not speak.
        $self = $this;
        $register = method_exists($this->pdo, 'createFunction')
            ? array($this->pdo, 'createFunction')
            : array($this->pdo, 'sqliteCreateFunction');
        call_user_func($register, 'regexp', function ($pattern, $subject) use ($self) {
            $self->patterns_seen[] = $pattern;
            $hit = @preg_match('/' . $pattern . '/iu', (string) $subject);
            if ($hit === false) throw new RuntimeException("bad regex: $pattern");
            return $hit ? 1 : 0;
        }, 2);
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
                funding_amount TEXT, funding_amount_usd INTEGER, funding_stage TEXT,
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
        try {
            return $this->pdo->query($sql);
        } catch (Exception $e) {
            if ($this->suppress) return null;
            throw $e;
        }
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

$GLOBALS['wpdb'] = new SearchHarnessDb();
require $tit_plugin . 'includes/api.php';

/* --- the corpus ---------------------------------------------------------- */

$rows = array(
    // What a reader searching EY is looking for.
    array('company' => 'EY', 'company_key' => 'ey',
          'headline' => 'EY LLP names new UK managing partner'),
    array('company' => 'EY Parthenon', 'company_key' => 'ey parthenon',
          'headline' => "EY's strategy arm opens 40 roles in Dublin"),
    // What it was returning instead, in the words that did it. These are the
    // shapes of the real top hits from the live probe.
    array('company' => 'Neospace', 'company_key' => 'neospace',
          'headline' => 'Neospace negotiates a round of up to US$ 250 million',
          'summary'  => 'The startup is negotiating new money for AI development.'),
    array('company' => 'Instituto Nacional de Estadistica', 'company_key' => 'instituto nacional de estadistica ine',
          'headline' => 'INE recruits 6,419 surveyors for the agricultural census'),
    array('company' => 'Monterrey Logistics', 'company_key' => 'monterrey logistics',
          'headline' => 'Monterrey Logistics adds a distribution key account team'),
    array('company' => 'Attorney General of Texas', 'company_key' => 'attorney general of texas',
          'headline' => 'The attorney general hires 30 investigators'),
    // The searches that already worked and must keep working.
    array('company' => 'Workday', 'company_key' => 'workday',
          'headline' => 'Workday opens 120 roles in Pleasanton'),
    array('company' => 'Stripe', 'company_key' => 'stripe',
          'headline' => 'Stripe expands its Dublin engineering office'),
    // Other short all-caps names from the same family.
    array('company' => 'GE Aerospace', 'company_key' => 'ge aerospace',
          'headline' => 'GE Aerospace hires 300 in Cincinnati'),
    array('company' => 'Germany Rail', 'company_key' => 'germany rail',
          'headline' => 'Germany cuts rail engineering headcount'),
    array('company' => 'BT Group', 'company_key' => 'bt group',
          'headline' => 'BT Group restructures its network division'),
    array('company' => 'Doubtfire Ltd', 'company_key' => 'doubtfire ltd',
          'headline' => 'There is no doubt the hiring market has turned'),
    array('company' => 'SAP', 'company_key' => 'sap',
          'headline' => 'SAP SE reorganises its cloud unit'),
    array('company' => 'Sapphire Foods', 'company_key' => 'sapphire foods',
          'headline' => 'Sapphire Foods opens 900 restaurant roles'),
    array('company' => 'AT&T', 'company_key' => 'at&t',
          'headline' => 'AT&T moves 200 network roles to Dallas'),
    // The CJK rows, as stored. Their behaviour must not change at all.
    array('company' => 'NHK', 'company_key' => 'nhk', 'country' => 'JP',
          'headline' => 'NHK、理事7人が異例の同時退任へ 井上会長のもと体制一新'),
    array('company' => 'サンコーマーケティングフーズ', 'company_key' => 'sanko', 'country' => 'JP',
          'headline' => 'SANKO MARKETING FOODS: 代表取締役の退任を届け出'),
    array('company' => '트레이드 데스크(TTD)', 'company_key' => 'trade desk', 'country' => 'KR',
          'headline' => '트레이드 데스크(TTD), 이사 사임 및 감사'),
    array('company' => '삼성전자', 'company_key' => 'samsung electronics', 'country' => 'KR',
          'headline' => '삼성전자가 반도체 설계 인력을 늘린다'),
);
foreach ($rows as $i => $row) {
    $row += array('signal_id' => 'sig' . $i, 'is_current' => 1,
                  'source_url' => 'https://example.test/' . $i,
                  'published_date' => '2026-08-01');
    $GLOBALS['wpdb']->insert_row($row);
}

/* --- assertions ---------------------------------------------------------- */

$failures = array();
function check($label, $got, $want) {
    global $failures;
    if ($got !== $want) $failures[] = "$label: expected " . var_export($want, true)
                                    . ", got " . var_export($got, true);
}

/** Company names returned by /query's own WHERE clause for these params. */
function companies_for(array $params) {
    global $wpdb;
    $bind = array();
    $where = tit_build_where(new WP_REST_Request($params), $bind);
    $sql = $wpdb->prepare("SELECT company FROM wp_tit_signals WHERE $where ORDER BY row_id", $bind);
    return $wpdb->get_col($sql);
}

// The dialect probe resolves through real SQL, and resolves to the one this
// engine speaks rather than to the first one tried.
check('dialect probe', tit_regexp_boundary_syntax(), 'icu');

// THE DEFECT. Before the fix this returned every row whose text contains the
// letters e-y in sequence: money, survey, Monterrey, key, attorney.
check('q=EY', companies_for(array('q' => 'EY')), array('EY', 'EY Parthenon'));
check('q=ey lowercase still finds it', companies_for(array('q' => 'ey')),
      array('EY', 'EY Parthenon'));

// The rest of the family the defect bites.
check('q=GE', companies_for(array('q' => 'GE')), array('GE Aerospace'));
check('q=BT', companies_for(array('q' => 'BT')), array('BT Group'));
check('q=SAP', companies_for(array('q' => 'SAP')), array('SAP'));
check('q=AT&T', companies_for(array('q' => 'AT&T')), array('AT&T'));

// WHAT ALREADY WORKED MUST STILL WORK, in both cases of the same letters.
check('q=Workday', companies_for(array('q' => 'Workday')), array('Workday'));
check('q=workday', companies_for(array('q' => 'workday')), array('Workday'));
check('q=Stripe', companies_for(array('q' => 'Stripe')), array('Stripe'));

// A two-letter name is not answered with an empty result. The wrong fix is a
// minimum query length, and it would make each of the four above return zero.
foreach (array('EY', 'GE', 'BT') as $short) {
    if (count(companies_for(array('q' => $short))) === 0) {
        $failures[] = "q=$short returned nothing: a length floor is not a fix";
    }
}

// THE CJK ROWS. Substring, exactly as before, and no regex is applied at all.
check('q=退任 (Japanese, substring)', companies_for(array('q' => '退任')),
      array('NHK', 'サンコーマーケティングフーズ'));
check('q=사임 (Korean, substring)', companies_for(array('q' => '사임')),
      array('트레이드 데스크(TTD)'));
check('q=삼성 inside 삼성전자 (Korean, no boundary)', companies_for(array('q' => '삼성')),
      array('삼성전자'));
check('no boundary pattern is built for a CJK term', tit_boundary_pattern('退任'), '');
check('no boundary pattern is built for a Hangul term', tit_boundary_pattern('삼성'), '');

// The company filter is the same path and had the same defect.
check('company=EY', companies_for(array('company' => 'EY')), array('EY', 'EY Parthenon'));
check('company=workday matches the lowercased key', companies_for(array('company' => 'Workday')),
      array('Workday'));

// An unsupported engine degrades to substring rather than to a wrong answer.
check('no dialect means no pattern', tit_boundary_pattern('EY', ''), '');

if ($failures) {
    fwrite(STDERR, implode("\n", $failures) . "\n");
    exit(1);
}
echo "search_boundary: all checks passed\n";
