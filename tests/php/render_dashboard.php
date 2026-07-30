<?php
/**
 * Render the dashboard shortcode and PUT A NUMBER ON WHAT IT COSTS.
 *
 * WHY THIS IS A RUNNING HARNESS AND NOT A TEXT ASSERTION.
 *
 * The dashboard is the page every reader lands on, and three of the things that
 * decide whether it is fast cannot be read out of the source.
 *
 *  - HOW MANY QUERIES ONE RENDER COSTS. The shortcode computes the hero
 *    figures, the at-a-glance matrix, four rankings, three money rankings, a
 *    concentration caveat and the first page of rows. Every one of those is an
 *    aggregate over the whole table, and "no N+1" is the kind of claim that is
 *    true on the day it is written and false three commits later, because what
 *    breaks it is one innocent call inside a foreach. So $wpdb counts, and the
 *    count is asserted against TIT_DASH_QUERY_BUDGET.
 *  - WHETHER THE CACHE ACTUALLY CATCHES. A transient that is written under one
 *    key and read under another is invisible in a diff and costs the full
 *    query count on every single request, so the warm render is counted too.
 *  - HOW MANY BYTES THE MARKUP IS. The owner asked for this page to be fast on
 *    a phone. A byte budget is the only version of that claim a test can hold,
 *    and it is what stops the next session adding a fourth ranking card without
 *    noticing what it costs.
 *
 * So WordPress is stubbed and the SQL is real: $wpdb runs against in-memory
 * SQLite with the plugin's own column shape, and api.php and shortcodes.php are
 * the REAL files loaded through the plugin's own bootstrap, so the notable
 * clause, the funding predicate, the industry vocabulary and the money
 * formatting are the ones that ship rather than copies that can drift.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/render_dashboard.php
 */

define('ABSPATH', __DIR__);
// TIT_PATH, TIT_URL, TIT_VERSION and TIT_TABLE_SUFFIX are NOT defined here: the
// plugin's own bootstrap defines them, and the version this harness runs against
// has to be the version that ships, because it is part of the cache key.
$tit_plugin = __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/';
function plugin_dir_path($file) { return dirname($file) . '/'; }
function plugin_dir_url($file) { return 'https://example.test/plugin/'; }
define('MINUTE_IN_SECONDS', 60);
define('HOUR_IN_SECONDS', 3600);
define('DAY_IN_SECONDS', 86400);
define('ARRAY_A', 'ARRAY_A');

/* --- the WordPress surface these files touch ----------------------------- */

$GLOBALS['tit_query_vars'] = array();
$GLOBALS['tit_transients'] = array();
$GLOBALS['tit_enqueued'] = array();
$GLOBALS['tit_localized'] = array();

function add_action($h, $f, $p = 10, $a = 1) {}
function add_filter($h, $f, $p = 10, $a = 1) {}
function add_shortcode($t, $f) { $GLOBALS['tit_shortcodes'][$t] = $f; }
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
function has_shortcode($content, $tag) { return false; }
function wp_enqueue_style($h, $src = '', $deps = array(), $ver = false, $media = 'all') {
    $GLOBALS['tit_enqueued']['style'][$h] = array('src' => $src, 'ver' => $ver, 'media' => $media);
}
function wp_enqueue_script($h, $src = '', $deps = array(), $ver = false, $footer = false) {
    $GLOBALS['tit_enqueued']['script'][$h] = array('src' => $src, 'ver' => $ver, 'footer' => $footer);
}
function wp_localize_script($h, $name, $data) { $GLOBALS['tit_localized'][$name] = $data; }
function wp_script_add_data($h, $key, $value) { $GLOBALS['tit_enqueued']['script_data'][$h][$key] = $value; }
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

/** WordPress core, and company.php's slug depends on it running first. */
function remove_accents($string) {
    $folded = @iconv('UTF-8', 'ASCII//TRANSLIT//IGNORE', $string);
    return $folded === false ? $string : $folded;
}

function get_header() { echo '<!--header-->'; }
function get_footer() { echo '<!--footer-->'; }

/**
 * $wpdb, backed by SQLite, COUNTING EVERY READ. Same class the place-page
 * harness uses, for the same reason: the count is as much the point as the SQL.
 */
class DashHarnessDb {
    public $pdo;
    public $prefix = 'wp_';
    public $options = 'wp_options';
    public $last_error = '';
    public $reads = 0;
    public $log = array();

    public function __construct() {
        $this->pdo = new PDO('sqlite::memory:');
        $this->pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $this->pdo->exec(
            'CREATE TABLE wp_tit_signals (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                is_current INTEGER NOT NULL DEFAULT 1,
                headline TEXT NOT NULL DEFAULT "",
                summary TEXT NOT NULL DEFAULT "",
                talent_readthrough TEXT NOT NULL DEFAULT "",
                company TEXT NOT NULL DEFAULT "",
                company_key TEXT NOT NULL DEFAULT "",
                pillar TEXT NOT NULL DEFAULT "rewards_comp",
                signal_direction TEXT NOT NULL DEFAULT "neutral",
                city TEXT, region TEXT, country TEXT,
                hq_city TEXT, hq_country TEXT, state TEXT,
                functions TEXT, industry TEXT,
                headcount INTEGER, funding_amount TEXT,
                funding_amount_usd INTEGER, funding_stage TEXT,
                materiality TEXT,
                confidence TEXT NOT NULL DEFAULT "verified",
                source_url TEXT NOT NULL DEFAULT "",
                source_name TEXT NOT NULL DEFAULT "",
                archive_url TEXT,
                published_date TEXT,
                captured_at TEXT NOT NULL DEFAULT "2026-01-01 00:00:00",
                collector TEXT NOT NULL DEFAULT "uk_paygap"
            )'
        );
    }

    public function prepare($sql, ...$args) {
        if (count($args) === 1 && is_array($args[0])) $args = $args[0];
        $out = '';
        $i = 0;
        $len = strlen($sql);
        for ($p = 0; $p < $len; $p++) {
            if ($sql[$p] === '%' && $p + 1 < $len && ($sql[$p + 1] === 's' || $sql[$p + 1] === 'd')) {
                $value = $args[$i++] ?? '';
                $out .= $sql[$p + 1] === 'd'
                    ? (string) (int) $value
                    : $this->pdo->quote((string) $value);
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
    public function get_results($sql, $output = null) { return $this->run($sql)->fetchAll(PDO::FETCH_ASSOC); }
    public function get_row($sql, $output = null) {
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
            'signal_id' => 'sig' . $n,
            'revision' => 1, 'is_current' => 1,
            'headline' => 'An update numbered ' . $n . ' with a headline about as long as a real one',
            'talent_readthrough' => 'What this means for anyone hiring into that team right now.',
            'company' => 'Employer ' . $n,
            'company_key' => 'employer ' . $n,
            'pillar' => 'company_development',
            'signal_direction' => 'hiring',
            'confidence' => 'verified',
            'source_url' => 'https://example.test/doc/' . $n,
            'source_name' => 'SEC EDGAR',
            'published_date' => gmdate('Y-m-d', time() - ($n % 40) * DAY_IN_SECONDS),
            'captured_at' => gmdate('Y-m-d H:i:s', time() - 3600),
        ), $opts);
        $columns = implode(', ', array_keys($row));
        $marks = implode(', ', array_fill(0, count($row), '?'));
        $stmt = $this->pdo->prepare("INSERT INTO wp_tit_signals ({$columns}) VALUES ({$marks})");
        $stmt->execute(array_values($row));
    }
}

$GLOBALS['wpdb'] = new DashHarnessDb();
global $wpdb;

// THE WHOLE PLUGIN, through its own bootstrap. tit_notable_where(),
// tit_funding_where(), the industry vocabulary and company.php's employer URLs
// all have to be the shipping ones or this harness proves something about
// itself.
require $tit_plugin . 'talent-intelligence-tracker.php';
if (!function_exists('tit_dashboard_html')) {
    fwrite(STDERR, "tit_dashboard_html() is missing: the shortcode body has to be "
                 . "callable more than once per process for cold and warm to be "
                 . "two measurements rather than one.\n");
    exit(1);
}

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

/**
 * The markup budget, in bytes, for the dataset seeded below.
 *
 * It lives here and not in the plugin because it is a property of this fixture:
 * 50 rows of these headlines, 40 countries in the place ranking and 40 rows in
 * each of the three money cards. The point is not the absolute figure, it is
 * that a session which adds a fourth ranking card has to come here and write
 * down what it cost.
 */
const TIT_DASH_BYTE_BUDGET = 152000;

/* ------------------------------------------------------------------------
   THE DATASET, shaped like the live distribution rather than like a fixture
   that happens to pass. The live table is dominated by one UK filing, holds
   thousands of routine officer changes, and carries dollar amounts on a small
   minority of rows. Every one of those shapes decides a branch below.
   ------------------------------------------------------------------------ */

$UK_CITIES = array('London', 'Manchester', 'Birmingham', 'Leeds', 'Glasgow');
$INDUSTRIES = array('technology', 'financial_services', 'healthcare',
                    'manufacturing', 'retail', 'education');

// The United Kingdom, dominated by ONE collector: 2,400 of its 2,460 rows, which
// is the live shape (4,761 of 4,808 from the gender pay gap filing) and is what
// must produce the concentration note.
for ($i = 0; $i < 2400; $i++) {
    $wpdb->insert_row(array(
        'country' => 'GB', 'city' => $UK_CITIES[$i % count($UK_CITIES)],
        'industry' => $INDUSTRIES[$i % count($INDUSTRIES)],
        'company' => 'UK Employer ' . ($i % 900), 'company_key' => 'uk employer ' . ($i % 900),
        'pillar' => 'rewards_comp', 'signal_direction' => 'comp_shift',
        'collector' => 'uk_paygap', 'source_name' => 'GOV.UK gender pay gap service',
        'source_url' => 'https://example.test/paygap/' . $i,
    ));
}
for ($i = 0; $i < 60; $i++) {
    $wpdb->insert_row(array(
        'country' => 'GB', 'city' => 'London', 'industry' => 'technology',
        'company' => 'UK News Employer ' . ($i % 30), 'company_key' => 'uk news employer ' . ($i % 30),
        'collector' => 'national_press', 'source_name' => 'Reuters', 'confidence' => 'reported',
        'source_url' => 'https://example.test/uknews/' . $i,
    ));
}

// The routine officer filings the default view sets aside: thousands of them,
// which is exactly the shape that made the detail control necessary.
for ($i = 0; $i < 1800; $i++) {
    $wpdb->insert_row(array(
        'country' => 'US', 'state' => 'CA', 'city' => 'San Francisco',
        'industry' => 'financial_services',
        'company' => 'Filer ' . ($i % 700), 'company_key' => 'filer ' . ($i % 700),
        'pillar' => 'leadership_change', 'signal_direction' => 'neutral',
        'materiality' => 'routine', 'collector' => 'sec_edgar',
        'source_name' => 'SEC EDGAR', 'source_url' => 'https://example.test/8k/' . $i,
        'archive_url' => 'https://web.archive.test/8k/' . $i,
    ));
}

// The rest of the world, so the region strip, the country ranking and the top
// city row all have more than one row to sort.
$WORLD = array('US' => 420, 'CA' => 90, 'IN' => 140, 'DE' => 70, 'FR' => 40,
               'IE' => 30, 'IL' => 25, 'AU' => 35, 'SG' => 28, 'JP' => 22,
               'BR' => 18, 'ZA' => 12, 'AE' => 10, 'NZ' => 8);
$CITY_OF = array('US' => 'Austin', 'CA' => 'Toronto', 'IN' => 'Bengaluru',
                 'DE' => 'Berlin', 'FR' => 'Paris', 'IE' => 'Dublin',
                 'IL' => 'Tel Aviv', 'AU' => 'Sydney', 'SG' => 'Singapore',
                 'JP' => 'Tokyo', 'BR' => 'Sao Paulo', 'ZA' => 'Cape Town',
                 'AE' => 'Dubai', 'NZ' => 'Auckland');
$DIRECTIONS = array('hiring', 'displacement', 'comp_shift', 'neutral');
$PILLARS = array('company_development', 'leadership_change', 'rewards_comp', 'how_we_work');
$k = 0;
foreach ($WORLD as $cc => $n) {
    for ($i = 0; $i < $n; $i++, $k++) {
        $row = array(
            'country' => $cc, 'city' => $CITY_OF[$cc],
            'industry' => $INDUSTRIES[$k % count($INDUSTRIES)],
            'company' => $cc . ' Employer ' . ($i % max(1, intdiv($n, 2))),
            'company_key' => strtolower($cc) . ' employer ' . ($i % max(1, intdiv($n, 2))),
            'pillar' => $PILLARS[$k % 4], 'signal_direction' => $DIRECTIONS[$k % 4],
            'collector' => $k % 3 ? 'sec_form_d' : 'gdelt',
            'source_name' => $k % 3 ? 'SEC EDGAR (Form D)' : 'GDELT',
            'confidence' => $k % 5 ? 'verified' : 'reported',
            'source_url' => 'https://example.test/world/' . $k,
        );
        // A dollar figure on a minority of rows, which is why every money card
        // prints a coverage sentence rather than a bare total.
        if ($i % 4 === 0) {
            $row['funding_amount'] = '$' . (5 + $i) . ' Million';
            $row['funding_amount_usd'] = (5 + $i) * 1000000;
            $row['funding_stage'] = array('seed', 'series_a', 'series_b', 'growth')[$i % 4];
        }
        $wpdb->insert_row($row);
    }
}

// A row placed only by its employer's head office, so the HQ badge renders.
$wpdb->insert_row(array('hq_country' => 'US', 'hq_city' => 'Seattle',
    'industry' => 'technology', 'company' => 'HQ Only Employer',
    'company_key' => 'hq only employer', 'published_date' => gmdate('Y-m-d'),
    'source_url' => 'https://example.test/hqonly/1'));
// A row with no place at all, and no date: both print their own words.
$wpdb->insert_row(array('industry' => 'technology', 'company' => 'Placeless Employer',
    'company_key' => 'placeless employer', 'published_date' => null,
    'source_url' => 'https://example.test/placeless/1'));
// A withdrawn row must not count towards any figure.
$wpdb->insert_row(array('country' => 'IL', 'industry' => 'technology', 'is_current' => 0,
    'company' => 'Retracted Employer', 'company_key' => 'retracted employer',
    'source_url' => 'https://example.test/retracted/1'));

/*
 * THE BUDGET PHASE RUNS HERE, and only in its own process.
 *
 * It has to be the first thing that touches the database, because company.php
 * memoises its employer slug index in a static and every assertion below would
 * warm it. Same reason render_place_pages.php gives its budget its own process.
 */
if (($argv[1] ?? '') === 'budget') {
    budget_phase();
    finish('budget');
}

/* ------------------------------------------------------------------------
   THE RENDER. Everything below asserts against ONE cold render, because the
   markup is what a reader gets and the render is what it costs.
   ------------------------------------------------------------------------ */

$html = cold_render();

/* --- the plumbing the whole page hangs off ------------------------------- */

check(strpos($html, 'id="tit-dashboard"') !== false,
      'the root element carries id="tit-dashboard", which is the only thing dashboard.js looks for');
check(strpos($html, 'data-api=') !== false && strpos($html, 'data-countries=') !== false,
      'the config rides on the element as well as on wp_localize_script, because '
      . 'Autoptimize can sweep the inline global into a bundle that loads after the script');

// The hidden selects the querystring, the chips bar, the exports, the matrix and
// every click-to-filter chart read and write. If one of these disappears, a
// filter stops round-tripping through the URL and nothing visible breaks until
// someone shares a link.
foreach (array('tit-f-pillar', 'tit-f-direction', 'tit-f-country', 'tit-f-state',
               'tit-f-city', 'tit-f-country_basis', 'tit-f-function', 'tit-f-industry',
               'tit-f-confidence', 'tit-f-min_funding_usd', 'tit-f-q', 'tit-f-since',
               'tit-f-until', 'tit-f-detail', 'tit-f-sort', 'tit-f-company',
               'tit-f-looking', 'tit-f-place', 'tit-f-employer_type', 'tit-f-work_mode',
               'tit-f-funding_stage', 'tit-f-deal_type', 'tit-f-site_event') as $id) {
    check(strpos($html, 'id="' . $id . '"') !== false,
          "the {$id} control has to exist: the querystring, the chips bar and the "
          . 'exports all read it by id');
}

/* --- the controls that are only worth having if they are populated -------- */

// Quick views used $quick_views three hundred lines BEFORE it was assigned, so
// the strip shipped as a label and a hint with no buttons between them and the
// live page emitted an undefined-variable notice on every render. A control
// group with nothing in it is the one thing a byte budget cannot see.
check(substr_count($html, 'class="tit-qv"') === 3,
      'the quick views strip has to carry its three buttons and carried '
      . substr_count($html, 'class="tit-qv"'));
check(strpos($html, 'data-qv="confidence=verified"') !== false,
      'and one of them has to be the official-filings view');

check(substr_count($html, 'class="tit-region') >= 6,
      'the region strip drops a region with nothing in it and keeps the rest');
check(substr_count($html, 'class="tit-cbtn"') === 10,
      'ten country buttons, by live row count, and not a hardcoded list');
check(strpos($html, 'tit-citybtn') !== false, 'and the top cities row below them');

/* --- the figures, under the same clause as the rows ---------------------- */

$notable = (int) $wpdb->get_var(
    "SELECT COUNT(*) FROM wp_tit_signals WHERE is_current = 1 AND " . tit_notable_where());
check(strpos($html, number_format_i18n($notable) . ' updates') !== false,
      'the hero counts the set the table is showing (' . number_format_i18n($notable)
      . ' notable updates), never the whole table');
check(strpos($html, 'come from one source') !== false,
      'one collector holds 2,400 of the United Kingdom\'s 2,460 rows, so the '
      . 'concentration caveat has to name it');
check(strpos($html, 'raised</a>') !== false, 'and the money total sits with the other figures');

/* --- the table ---------------------------------------------------------- */

$tbody = substr($html, strpos($html, '<tbody id="tit-rows">'));
$tbody = substr($tbody, 0, strpos($tbody, '</tbody>'));
check(substr_count($tbody, '<tr>') === TIT_DASH_ROWS,
      'the first page is ' . TIT_DASH_ROWS . ' server-rendered rows and was '
      . substr_count($tbody, '<tr>'));
check(strpos($tbody, 'materiality') === false && strpos($tbody, 'routine') === false,
      'and none of them is a routine officer filing, because the default view sets those aside');
check(strpos($html, '>HQ<') !== false,
      'a row placed only by its employer\'s head office says so');
check(strpos($html, 'Location not stated') !== false && strpos($html, 'Date not stated') !== false,
      'and a row with no place or no date says that too, rather than showing a blank cell');
check(strpos($html, '/company/') !== false, 'every employer name links to that employer\'s page');

/* --- the assets ---------------------------------------------------------- */

check(isset($GLOBALS['tit_enqueued']['style']['tit-dashboard']),
      'the stylesheet is enqueued from inside the shortcode, because the '
      . 'wp_enqueue_scripts guard is false whenever the shortcode arrives through a block');
check(count($GLOBALS['tit_enqueued']['style']) === 1,
      'and it stays ONE file: a second stylesheet is a second blocking request in the head');
check(($GLOBALS['tit_enqueued']['script']['tit-dashboard']['footer'] ?? false) === true,
      'dashboard.js loads in the footer, never in the head');
/* --- the byte budget ---------------------------------------------------- */

$bytes = strlen($html);
$GLOBALS['tit_bytes'] = $bytes;
check($bytes <= TIT_DASH_BYTE_BUDGET,
      'the markup must stay inside ' . number_format(TIT_DASH_BYTE_BUDGET)
      . ' bytes and was ' . number_format($bytes)
      . '. This page is read on phones; a new card is not free.');

/* --- nothing that scrolls the body sideways ----------------------------- */

// Every wide thing on this page has to live inside its own scroller, or the
// body scrolls sideways on a phone and the whole layout reads as broken.
check(strpos($html, 'class="tit-table-scroll"') !== false,
      'the table sits inside its own horizontal scroller');

/* --- the SVG that has to be sized in markup ----------------------------- */

// Roo is the only image on the page. An SVG with no width and height gets its
// default 300x150 until the stylesheet lands, which moves the entire hero.
check(preg_match('/<svg class="tit-roo [^>]*width="\d+" height="\d+"/', $html) === 1,
      'Roo carries width and height in the markup, so he reserves his own space '
      . 'before the stylesheet arrives');

finish('');

/* ------------------------------------------------------------------------ */

function render() {
    ob_start();
    echo tit_dashboard_html();
    return ob_get_clean();
}

function cold_render() {
    global $wpdb;
    $GLOBALS['tit_transients'] = array();
    $wpdb->reset_reads();
    return render();
}

function trace(array $log) {
    return ":\n      " . implode("\n      ", array_map(fn($q) => substr($q, 0, 120), $log));
}

function budget_phase() {
    global $wpdb;

    // Nothing is memoised in a static across renders here, unlike the place
    // pages: this page links employer profiles by transforming the key, and
    // never asks company.php for its slug index. So the first render in a
    // process costs exactly what every later cold one does, and if that stops
    // being true the number below is where it shows up.
    $GLOBALS['tit_transients'] = array();
    $wpdb->reset_reads();
    render();
    check($wpdb->reads === TIT_DASH_QUERY_BUDGET,
          'the first cold render in a process must cost exactly '
          . TIT_DASH_QUERY_BUDGET . ' queries and cost ' . $wpdb->reads
          . trace($wpdb->log));

    $GLOBALS['tit_transients'] = array();
    $wpdb->reset_reads();
    render();
    check($wpdb->reads === TIT_DASH_QUERY_BUDGET,
          'and so must every later cold one: cost ' . $wpdb->reads . trace($wpdb->log));

    // THE WARM PATH is not yet a path: nothing on this page is cached, so a
    // second render costs the same as the first. That is the measurement this
    // harness exists to make actionable, and the next commit is where the
    // number below becomes zero.
    $wpdb->reset_reads();
    render();
    check($wpdb->reads === TIT_DASH_QUERY_BUDGET,
          'nothing is cached yet, so a warm render costs what a cold one does: '
          . $wpdb->reads);

    // THE PROOF THAT THERE IS NO N+1. The same page over three times the rows
    // must cost the same. A per-row query shows up here as a number that moved,
    // which is the only way that mistake is caught before a reader waits for it.
    $GLOBALS['tit_transients'] = array();
    $wpdb->reset_reads();
    render();
    $before = $wpdb->reads;
    for ($i = 0; $i < 5000; $i++) {
        $wpdb->insert_row(array(
            'country' => 'GB', 'city' => 'London', 'industry' => 'technology',
            'company' => 'Grown Employer ' . ($i % 2000),
            'company_key' => 'grown employer ' . ($i % 2000),
            'collector' => 'uk_paygap', 'source_name' => 'GOV.UK gender pay gap service',
            'source_url' => 'https://example.test/grown/' . $i,
        ));
    }
    $GLOBALS['tit_transients'] = array();
    $wpdb->reset_reads();
    render();
    check($wpdb->reads === $before,
          'the query count must not depend on how many rows the table holds: '
          . $before . ' before adding five thousand, ' . $wpdb->reads . ' after');

    // And the row count must not either. The first page is a LIMIT, so a table
    // that doubles cannot double the bytes a phone downloads.
    $html = render();
    $tbody = substr($html, strpos($html, '<tbody id="tit-rows">'));
    $tbody = substr($tbody, 0, strpos($tbody, '</tbody>'));
    check(substr_count($tbody, '<tr>') === TIT_DASH_ROWS,
          'and neither can the number of rows it prints: ' . substr_count($tbody, '<tr>'));
}

function finish($phase) {
    global $failures;
    if ($failures) {
        fwrite(STDERR, 'dashboard FAILED' . ($phase ? " in phase '{$phase}'" : '')
                       . ":\n  - " . implode("\n  - ", $failures) . "\n");
        exit(1);
    }
    if ($phase === 'budget') {
        printf("  budget ok: %d queries per render, cold or warm.\n", TIT_DASH_QUERY_BUDGET);
        exit(0);
    }
    // The budget needs a process where nothing has rendered yet.
    $command = escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' budget';
    passthru($command, $status);
    if ($status !== 0) exit(1);
    printf("dashboard ok: markup, controls, %s bytes of it, and a %d-query cold render.\n",
           number_format($GLOBALS['tit_bytes']), TIT_DASH_QUERY_BUDGET);
    exit(0);
}
