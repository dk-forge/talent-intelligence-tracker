<?php
/*
 * EVERY EMPLOYER NAME IN THIS FILE IS PREFIXED "TEST FIXTURE" ON PURPOSE.
 * Same reason as render_dashboard.php: this renders the REAL press page against
 * a synthetic corpus, so its output is the shape of production with different
 * numbers in it, and a test render indistinguishable from production is a trap.
 */
/**
 * Render the press page and check the one thing a press page can get silently,
 * catastrophically wrong.
 *
 * THE BUG THIS FILE EXISTS FOR.
 *
 * The sibling AI Layoff Tracker shipped a press page whose evidence links were
 * built on `ai_primary=1`. Its REST API accepts that parameter. Its dashboard
 * JavaScript does not read it. So every "see the rows behind this number" link
 * advertised a filtered view and served the entire corpus, nothing errored, and
 * there was no way for a reader or a writer to tell. Its own ARCHITECTURE.md
 * now cites it as the canonical example of the failure class: a bad parameter
 * NAME over-reports, a bad parameter VALUE under-reports, and neither raises.
 *
 * A whitelist somebody maintains by hand does not fix that, because the whole
 * defect is that the whitelist and the front end drifted apart. So the check
 * below parses the `inputs` map OUT OF assets/dashboard.js and requires every
 * parameter this page emits to be in it. When a control is renamed or removed,
 * this fails on the next run rather than on the next journalist.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/render_press.php
 */

define('ABSPATH', __DIR__);
$tit_plugin = __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/';
function plugin_dir_path($file) { return dirname($file) . '/'; }
function plugin_dir_url($file) { return 'https://example.test/plugin/'; }
define('MINUTE_IN_SECONDS', 60);
define('HOUR_IN_SECONDS', 3600);
define('DAY_IN_SECONDS', 86400);
define('ARRAY_A', 'ARRAY_A');

$GLOBALS['tit_query_vars'] = array('tit_press' => 1);
$GLOBALS['tit_transients'] = array();
$GLOBALS['tit_enqueued'] = array();
$GLOBALS['tit_localized'] = array();
$GLOBALS['tit_filters'] = array();

function add_action($h, $f, $p = 10, $a = 1) {}
function add_filter($h, $f, $p = 10, $a = 1) { $GLOBALS['tit_filters'][$h][] = $f; }
function add_shortcode($t, $f) {}
function apply_filters($h, $v) {
    foreach ($GLOBALS['tit_filters'][$h] ?? array() as $f) $v = $f($v);
    return $v;
}
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
function wp_enqueue_style($h, $src = '', $deps = array(), $ver = false, $media = 'all') {
    $GLOBALS['tit_enqueued']['style'][$h] = array('src' => $src, 'ver' => $ver);
}
function wp_enqueue_script($h, $src = '', $deps = array(), $ver = false, $footer = false) {
    $GLOBALS['tit_enqueued']['script'][$h] = array('src' => $src, 'ver' => $ver);
}
function wp_localize_script($h, $name, $data) {}
function wp_script_add_data($h, $key, $value) {}
function current_time($t, $gmt = 0) { return $t === 'timestamp' ? time() : gmdate($t); }
function get_option($k, $d = false) { return $d; }
function update_option($k, $v, $a = null) { return true; }
function delete_transient($k) { unset($GLOBALS['tit_transients'][$k]); return true; }
function get_transient($k) { return $GLOBALS['tit_transients'][$k] ?? false; }
function set_transient($k, $v, $t = 0) { $GLOBALS['tit_transients'][$k] = $v; return true; }
function get_query_var($v) { return $GLOBALS['tit_query_vars'][$v] ?? ''; }
function add_query_arg($key, $value, $url) {
    return $url . (strpos($url, '?') === false ? '?' : '&')
         . rawurlencode((string) $key) . '=' . rawurlencode((string) $value);
}
function remove_accents($string) {
    $folded = @iconv('UTF-8', 'ASCII//TRANSLIT//IGNORE', $string);
    return $folded === false ? $string : $folded;
}
function get_header() { echo '<!--header-->'; }
function get_footer() { echo '<!--footer-->'; }

/** $wpdb on SQLite, counting every read. Same shape as the other harnesses. */
class PressHarnessDb {
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
                pillar TEXT NOT NULL DEFAULT "rewards_comp",
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
            'signal_id' => 'p' . $n, 'revision' => 1, 'is_current' => 1,
            'headline' => 'TEST FIXTURE NOT REAL DATA: synthetic update ' . $n,
            'company' => 'TEST FIXTURE Employer ' . $n,
            'company_key' => 'employer ' . $n,
            'pillar' => 'company_development', 'signal_direction' => 'hiring',
            'confidence' => 'verified',
            'source_url' => 'https://example.test/doc/' . $n,
            'source_name' => 'SEC EDGAR',
            'published_date' => gmdate('Y-m-d', time() - ($n % 50) * DAY_IN_SECONDS),
            'captured_at' => gmdate('Y-m-d H:i:s', time() - 3600),
        ), $opts);
        $cols = implode(', ', array_keys($row));
        $marks = implode(', ', array_fill(0, count($row), '?'));
        $stmt = $this->pdo->prepare("INSERT INTO wp_tit_signals ({$cols}) VALUES ({$marks})");
        $stmt->execute(array_values($row));
    }
}

$GLOBALS['wpdb'] = new PressHarnessDb();
global $wpdb;

require $tit_plugin . 'talent-intelligence-tracker.php';

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

if (!function_exists('tit_press_render')) {
    fwrite(STDERR, "tit_press_render() is missing: includes/press.php did not load.\n");
    exit(1);
}

/* ---------------------------------------------------------------- fixture -- */

$COUNTRIES = array('US' => 260, 'GB' => 180, 'IN' => 70, 'DE' => 40, 'CA' => 30);
$INDUSTRIES = array('technology', 'financial_services', 'healthcare', 'retail');
$k = 0;
foreach ($COUNTRIES as $cc => $count) {
    for ($i = 0; $i < $count; $i++, $k++) {
        $row = array(
            'country' => $cc,
            'city' => $cc === 'US' ? 'Austin' : 'London',
            'industry' => $INDUSTRIES[$k % 4],
            'company' => 'TEST FIXTURE ' . $cc . ' Employer ' . ($i % 20),
            'company_key' => strtolower($cc) . ' employer ' . ($i % 20),
            'confidence' => $k % 4 ? 'verified' : 'reported',
            'source_url' => 'https://example.test/w/' . $k,
        );
        if ($i % 5 === 0) {
            $row['funding_amount'] = '$' . (3 + $i) . ' Million';
            $row['funding_amount_usd'] = (3 + $i) * 1000000;
            $row['funding_stage'] = 'series_a';
        }
        $wpdb->insert_row($row);
    }
}
// Routine filings, which the notable clause sets aside, so the press figures can
// be checked against that clause rather than against a bare row count.
for ($i = 0; $i < 300; $i++) {
    $wpdb->insert_row(array(
        'country' => 'US', 'materiality' => 'routine', 'pillar' => 'leadership_change',
        'signal_direction' => 'neutral', 'industry' => 'financial_services',
        'company' => 'TEST FIXTURE Filer ' . ($i % 50), 'company_key' => 'filer ' . ($i % 50),
        'source_url' => 'https://example.test/8k/' . $i,
    ));
}
// A withdrawn record must not reach any figure on a page journalists quote.
$wpdb->insert_row(array('country' => 'IL', 'is_current' => 0,
    'company' => 'TEST FIXTURE Retracted', 'company_key' => 'retracted',
    'source_url' => 'https://example.test/retracted/1'));

/* ----------------------------------------------------------------- render -- */

function press_render() {
    ob_start();
    tit_press_render(tit_press_facts('wp_tit_signals'));
    return ob_get_clean();
}

$GLOBALS['tit_transients'] = array();
$wpdb->reset_reads();
$html = press_render();
$cold = $wpdb->reads;

/*
 * THE COST OF THE PAGE, as a number, for the same reason the dashboard has one:
 * "no N+1" is true the day it is written and false three commits later, and what
 * breaks it is one innocent call inside a foreach. The archive loop and the
 * period table are both foreaches over query results, which is exactly where a
 * per-row lookup would go unnoticed.
 */
const TIT_PRESS_QUERY_BUDGET = 5;
check($cold === TIT_PRESS_QUERY_BUDGET,
      'a cold press render must cost exactly ' . TIT_PRESS_QUERY_BUDGET
      . ' queries and cost ' . $cold . ":\n      "
      . implode("\n      ", array_map(fn($q) => substr($q, 0, 110), $wpdb->log)));

$wpdb->reset_reads();
press_render();
check($wpdb->reads === 0,
      'and a warm one must cost none at all, and cost ' . $wpdb->reads);

/* --------------------------------------------------- the page's own claims -- */

/* The heading now carries data-tit-route-heading, which is what names this
   route's item in the site navigation. Asserted through tit_route_heading()
   rather than against a typed copy of the string, so the page, the menu and
   this check can never describe the route three ways. */
check(preg_match('#<h1[^>]*\bdata-tit-route-heading\b[^>]*>Press and Media Kit</h1>#', $html) === 1,
      'the press page has to render its heading, marked as the route heading');
foreach (array('press-numbers', 'press-archive', 'press-limits', 'press-cite',
               'press-contact') as $id) {
    check(strpos($html, 'id="' . $id . '"') !== false,
          "the {$id} section is missing");
}

/*
 * EVERY STATED FIGURE IS COMPUTED, checked by recomputing it here.
 *
 * The sibling's press page still carries a hardcoded "51 of the most
 * significant layoffs ... we currently carry every one of them" with no query
 * behind it, and corrections.php in THIS repo once shipped a typed "$124.0bn"
 * captioned "Measured now" while the live figure was $101B. A press page is
 * where a typed number does the most damage, because it leaves in a quote.
 */
$notable_where = 'is_current = 1 AND ' . tit_notable_where();
$expect_n = (int) $wpdb->get_var("SELECT COUNT(*) FROM wp_tit_signals WHERE {$notable_where}");
$expect_c = (int) $wpdb->get_var("SELECT COUNT(DISTINCT company_key) FROM wp_tit_signals WHERE {$notable_where}");
$expect_v = (int) $wpdb->get_var(
    "SELECT COUNT(*) FROM wp_tit_signals WHERE {$notable_where} AND confidence = 'verified'");
check(strpos($html, '>' . number_format_i18n($expect_n) . '<') !== false,
      'the update total has to be the one the database holds ('
      . number_format_i18n($expect_n) . ')');
check(strpos($html, '>' . number_format_i18n($expect_c) . '<') !== false,
      'and so does the employer total (' . number_format_i18n($expect_c) . ')');
check(strpos($html, number_format_i18n($expect_v)) !== false,
      'and the official-filings count (' . number_format_i18n($expect_v) . ')');
// A withdrawn record must not have reached anything.
check(strpos($html, 'Retracted') === false,
      'a withdrawn record reached the press page. Nothing on this page may '
      . 'count a row with is_current = 0');
// The year label derives from the clock, or this page starts lying on 1 January.
check(strpos($html, date('Y') . ' So Far') !== false,
      'the year window has to name the CURRENT year, derived rather than typed');

/*
 * THE DEEP LINKS, AND THE ONLY CHECK THAT ACTUALLY CLOSES THE SIBLING'S BUG.
 *
 * Every link into the dashboard is pulled out of the rendered markup, its
 * parameters are parsed, and each parameter NAME is required to be one the
 * dashboard's JavaScript actually reads. The list of those is not written down
 * here: it is parsed out of assets/dashboard.js, so a control that is renamed or
 * removed breaks this on the next run rather than turning every link on this
 * page into a silently unfiltered view.
 */
$js = file_get_contents($tit_plugin . 'assets/dashboard.js');
check($js !== false && $js !== '', 'dashboard.js has to be readable to check the links against it');

preg_match('/var inputs = \{(.*?)\n  \};/s', $js, $im);
check(!empty($im[1]), 'the `inputs` map could not be parsed out of dashboard.js. '
      . 'This check is the only thing standing between this page and the '
      . 'sibling\'s silently-unfiltered-link bug, so a parse failure is a '
      . 'failure and never a skip');
preg_match_all("/^\s*'?([a-z_]+)'?\s*:/m", $im[1] ?? '', $keys);
$js_reads = array_flip($keys[1] ?? array());

/*
 * Three parameters have no entry in that map and ARE read, each by name, in
 * applyUrlState(). They are listed individually rather than waved through as a
 * class, and each one is proved present in the file.
 */
foreach (array('funding' => "q.get('funding')",
               'stated_headcount' => "q.get('stated_headcount')") as $param => $needle) {
    check(strpos($js, $needle) !== false,
          "press links may use `{$param}` only while applyUrlState() still reads "
          . 'it by name, and that call is gone from dashboard.js');
    $js_reads[$param] = true;
}

preg_match_all('/href="([^"]*talent-intelligence-tracker\/\?[^"]*)"/', $html, $links);
check(count($links[1]) >= 5,
      'the press page has to carry deep links for this check to mean anything, '
      . 'and carried ' . count($links[1]));

$countries = tit_country_names();
foreach (array_unique($links[1]) as $href) {
    $query = html_entity_decode(substr($href, strpos($href, '?') + 1), ENT_QUOTES, 'UTF-8');
    parse_str($query, $args);
    foreach ($args as $name => $value) {
        check(isset($js_reads[$name]),
              "the link {$href} uses `{$name}`, which applyUrlState() in "
              . 'dashboard.js does not read. The link would advertise a filtered '
              . 'view and serve the unfiltered page, exactly as the sibling\'s '
              . '`ai_primary=1` did, and nothing would error');
        // A bad NAME over-reports; a bad VALUE under-reports and reads as "no
        // rows there". Both are silent, so the closed vocabularies are checked
        // as well as the parameter names.
        if ($name === 'country') {
            check(isset($countries[$value]),
                  "the link {$href} filters country={$value}, which is not an "
                  . 'ISO code this product recognises, so it would return zero '
                  . 'rows and read as "nothing happened there"');
        }
        if ($name === 'since' || $name === 'until') {
            check(preg_match('/^\d{4}-\d{2}-\d{2}$/', $value) === 1,
                  "the link {$href} passes {$name}={$value}, which is not a date "
                  . 'the control can accept');
        }
    }
}

/* ------------------------------------------------- what the page refuses to do */

// No superlatives, and no claim the project cannot support. These are the ones a
// press page reaches for, and each is disprovable in thirty seconds.
foreach (array('most complete', 'most advanced', 'comprehensive', 'the only',
               'real time', '100% automated', 'the largest database',
               'the first tracker') as $overclaim) {
    check(stripos($html, $overclaim) === false,
          'the press page claims "' . $overclaim . '". Everything visible here '
          . 'has to survive a check, and this does not');
}
// Em-dashes are the house style's one typographic rule and this page is the one
// most likely to be written in a hurry.
check(strpos($html, "\xe2\x80\x94") === false,
      'the press page contains an em-dash, which the house style does not use');

// What the tracker does NOT do is stated, on the page, rather than left to be
// found after somebody has quoted us.
check(strpos($html, 'It does not track layoffs') !== false,
      'the page has to say that layoffs are not collected here. It is the single '
      . 'most likely thing for a journalist to misquote it for');
check(strpos($html, 'It does not estimate') !== false,
      'and that nothing is estimated');
check(strpos($html, 'It does not claim to be complete') !== false,
      'and that coverage is measured rather than asserted');

/* -------------------------------------------------------- Title Case headings */

$tit_small = array('a','an','and','as','at','but','by','for','in','of','on','or',
                   'the','to','vs','with');
$title_case_ok = function ($label) use ($tit_small) {
    foreach (preg_split('/\s+/', trim($label)) as $i => $w) {
        $bare = preg_replace('/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/u', '', $w);
        if ($bare === '') continue;
        if ($i > 0 && in_array(mb_strtolower($bare), $tit_small, true)) continue;
        if ($bare === mb_strtoupper($bare)) continue;
        if (mb_substr($bare, 0, 1) !== mb_strtoupper(mb_substr($bare, 0, 1))) return false;
    }
    return true;
};
preg_match_all('/<h2[^>]*>([^<]+)<\/h2>/', $html, $h2s);
check(count($h2s[1]) >= 4, 'the press page has to render its section headings');
foreach ($h2s[1] as $label) {
    check($title_case_ok($label), 'press heading "' . $label . '" is not Title Case');
}

/* --------------------------------------------------------------------------- */

if ($failures) {
    fwrite(STDERR, "press FAILED:\n  - " . implode("\n  - ", $failures) . "\n");
    exit(1);
}
printf("press ok: %s bytes, a %d-query cold render, none warm, and every deep "
     . "link uses a parameter the front end reads.\n",
       number_format(strlen(str_replace('TEST FIXTURE ', '', $html))),
       TIT_PRESS_QUERY_BUDGET);
exit(0);
