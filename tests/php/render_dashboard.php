<?php
/*
 * EVERY EMPLOYER NAME IN THIS FILE IS PREFIXED "TEST FIXTURE" ON PURPOSE.
 *
 * This harness renders the REAL dashboard against a synthetic corpus, so its
 * output is byte-for-byte the shape of the live page with different numbers in
 * it. The owner twice read a screenshot of this render as the live site and
 * concluded the data had broken -- its UK count outranks its US count, which is
 * inverted from production, and that was the only clue. A test render
 * indistinguishable from production is a trap for a human and for the next
 * session, so the fixtures announce themselves in the one field a reader looks
 * at first. Do not "tidy" the prefix away.
 */
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
 *    query count on every single request. The warm render is asserted at ZERO.
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
            'headline' => 'TEST FIXTURE NOT REAL DATA: synthetic update ' . $n . ', padded to about the length of a real headline',
            'talent_readthrough' => 'What this means for anyone hiring into that team right now.',
            'company' => 'TEST FIXTURE Employer ' . $n,
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
const TIT_DASH_BYTE_BUDGET = 168000;

/*
 * RAISED 156,000 -> 168,000 on 2026-07-30 (second design pass), and here is the
 * itemised bill. Measured on this fixture with the "TEST FIXTURE " prefix
 * stripped, so these are bytes production actually ships.
 *
 *   2,824  the dated glance panel: today / this week / this month / this year,
 *          each with updates, employers, dollars raised, official filings and
 *          the largest single raise. It replaces nothing — the all-time
 *          figures line stays as the panel's bottom rung — so it is new markup
 *          in full. It is also the owner's most-wanted item and the first
 *          thing on the page that carries a date, which is what a reader opens
 *          a tracker to find out.
 *
 *   6,673  the "Why you can trust this" panel and the FAQ, both rendered
 *          server-side in full. This is the expensive half and it is
 *          deliberate: the constraint on the FAQ is that every answer is in
 *          the INITIAL HTML rather than fetched on click, because an FAQ
 *          behind a click is an FAQ no crawler and no answer engine ever
 *          reads, and it is among the most valuable blocks on the page. Paying
 *          for it in markup is the whole point rather than an oversight.
 *
 *   3,450  the FAQPage structured data, which is that same FAQ a second time.
 *          Kept, and it is the one line here worth arguing about. It is a
 *          straight duplicate of visible prose, and it earns its bytes only
 *          because the answers ARE visible: company.php and places.php both
 *          record that the sibling earned a manual-action risk emitting
 *          identical FAQPage markup across ~1,830 URLs where the answers were
 *          nowhere in the document. If a future session ever moves an answer
 *          behind a fetch, this block has to go with it.
 *  ------
 *  12,947  measured 153,670 -> 166,688 on this fixture.
 *
 * The headroom is 1,312 bytes and that is on purpose. The budget is not a
 * target and it is not a ceiling to grow into: it is here so the next session
 * that adds a card has to come to this line and write down what it cost.
 *
 * 2026-07-30, NOT raised, and 461 bytes of that headroom is now spent: 167,299
 * -> 167,760. It bought the archived-copy assertions, and almost all of it is
 * FIXTURE rather than page. Six rows were added so the first page contains a row
 * with a saved copy and a row without (before them the render carried none of
 * either, because the only 1,800 rows holding an archive_url are routine officer
 * filings the default view sets aside). Production pays about 110 bytes per row
 * that actually has a copy, and 72 of 12,970 cited documents do. The remaining
 * headroom is 240 bytes, which is not room for anything: the next addition
 * raises this number and writes down why.
 */

/*
 * RAISED 152,000 -> 156,000 on 2026-07-30, and here is what bought it.
 *
 * 2,096 bytes of it is the `data-states` attribute on #tit-dashboard: the US
 * state filter rendered 51 bare postal codes as its option labels ("AK", "AL",
 * "AZ") while every other control on the page spells its values out, so the
 * name map has to reach the browser. It rides on a data- attribute AND on
 * wp_localize_script for the reason gotcha 10 exists: Autoptimize sweeps the
 * inline object into a bundle that loads after the script, and the attribute is
 * the copy that cannot be moved away from the element it describes. Paying it
 * once in markup is the price of the control working at all.
 *
 * The rest is the two sentences the place pages and this page needed to stop
 * contradicting each other, and the cross-tracker section's markup, which
 * renders nothing while that feature is disabled.
 *
 * The budget is not a target. It is here so the next session that adds a card
 * has to come to this line and write down what it cost.
 */

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
        'company' => 'TEST FIXTURE UK Employer ' . ($i % 900), 'company_key' => 'uk employer ' . ($i % 900),
        'pillar' => 'rewards_comp', 'signal_direction' => 'comp_shift',
        'collector' => 'uk_paygap', 'source_name' => 'GOV.UK gender pay gap service',
        'source_url' => 'https://example.test/paygap/' . $i,
    ));
}
for ($i = 0; $i < 60; $i++) {
    $wpdb->insert_row(array(
        'country' => 'GB', 'city' => 'London', 'industry' => 'technology',
        'company' => 'TEST FIXTURE UK News Employer ' . ($i % 30), 'company_key' => 'uk news employer ' . ($i % 30),
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
        'company' => 'TEST FIXTURE Filer ' . ($i % 700), 'company_key' => 'filer ' . ($i % 700),
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
            'company' => 'TEST FIXTURE ' . $cc . ' Employer ' . ($i % max(1, intdiv($n, 2))),
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
    'industry' => 'technology', 'company' => 'TEST FIXTURE HQ Only Employer',
    'company_key' => 'hq only employer', 'published_date' => gmdate('Y-m-d'),
    'source_url' => 'https://example.test/hqonly/1'));
// A row with no place at all, and no date: both print their own words.
$wpdb->insert_row(array('industry' => 'technology', 'company' => 'TEST FIXTURE Placeless Employer',
    'company_key' => 'placeless employer', 'published_date' => null,
    'source_url' => 'https://example.test/placeless/1'));
// A withdrawn row must not count towards any figure.
$wpdb->insert_row(array('country' => 'IL', 'industry' => 'technology', 'is_current' => 0,
    'company' => 'TEST FIXTURE Retracted Employer', 'company_key' => 'retracted employer',
    'source_url' => 'https://example.test/retracted/1'));

/*
 * THE TWO SHAPES THAT BROKE THE TOP CITIES STRIP, seeded so the assertions
 * further down have something to catch.
 *
 * Edinburgh is placed ONLY by its employers' head offices, which is the live
 * shape for most of the United Kingdom: the strip counted bare `city`, so
 * Edinburgh (49 rows live) was missing from a list that carried Toronto (25),
 * and London read 18 against the 1,338 its own pill returned. The counts here
 * are sized to reach the strip against this fixture's UK volume, not to match
 * the live ones.
 *
 * Ottawa is held by two countries at once. `cc` was a non-aggregated column
 * under GROUP BY city, so the flag was whichever row the engine reached first
 * and MySQL and SQLite need not agree -- live, Toronto flew a US flag on 22
 * Canadian rows against 2 American ones.
 */
for ($i = 0; $i < 120; $i++) {
    $wpdb->insert_row(array(
        'hq_country' => 'GB', 'hq_city' => 'Edinburgh', 'industry' => 'technology',
        'company' => 'TEST FIXTURE Edinburgh Employer ' . ($i % 20),
        'company_key' => 'edinburgh employer ' . ($i % 20),
        'collector' => 'national_press', 'source_name' => 'The Scotsman',
        'source_url' => 'https://example.test/edinburgh/' . $i,
    ));
}
for ($i = 0; $i < 100; $i++) {
    $wpdb->insert_row(array(
        'country' => $i < 92 ? 'CA' : 'US', 'city' => 'Ottawa',
        'industry' => 'public_sector',
        'company' => 'TEST FIXTURE Ottawa Employer ' . ($i % 12),
        'company_key' => 'ottawa employer ' . ($i % 12),
        'collector' => 'national_press', 'source_name' => 'Ottawa Citizen',
        'source_url' => 'https://example.test/ottawa/' . $i,
    ));
}

/*
 * THE ARCHIVED-COPY PAIR, and why they are the last rows inserted.
 *
 * The first page is ordered materiality bucket, then date, then row_id DESC, so
 * an unjudged row dated today and inserted last is deterministically at the top.
 * That matters because the property under test is a CONDITIONAL: the "Archived"
 * link prints where a snapshot exists and prints nothing where one does not, and
 * a fixture where no archived row reaches page one asserts neither half. Before
 * this pair existed the whole dashboard render carried zero of these spans while
 * 1,800 rows in the table held an archive_url, because every one of them is
 * materiality=routine and set aside from the default view.
 *
 * Both halves are the assertion. A placeholder or a dead link on the rows
 * WITHOUT a copy is the failure that matters here: this page's one claim is that
 * every figure still reaches its document, and a link offered and not there
 * breaks that claim more thoroughly than an absent link ever could.
 */
for ($i = 0; $i < 3; $i++) {
    $wpdb->insert_row(array(
        'country' => 'US', 'city' => 'Austin', 'industry' => 'technology',
        'company' => 'TEST FIXTURE Archived Employer ' . $i,
        'company_key' => 'archived employer ' . $i,
        'collector' => 'national_press', 'source_name' => 'TEST FIXTURE Archived Outlet',
        'confidence' => 'reported', 'published_date' => gmdate('Y-m-d'),
        'source_url' => 'https://example.test/archived/' . $i,
        'archive_url' => 'https://web.archive.test/save/' . $i,
    ));
    $wpdb->insert_row(array(
        'country' => 'US', 'city' => 'Austin', 'industry' => 'technology',
        'company' => 'TEST FIXTURE Unarchived Employer ' . $i,
        'company_key' => 'unarchived employer ' . $i,
        'collector' => 'national_press', 'source_name' => 'TEST FIXTURE Unarchived Outlet',
        'confidence' => 'reported', 'published_date' => gmdate('Y-m-d'),
        'source_url' => 'https://example.test/unarchived/' . $i,
    ));
}

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
check(substr_count($html, 'class="tit-qv"') === 4,
      'the quick views strip has to carry its four buttons and carried '
      . substr_count($html, 'class="tit-qv"'));
check(strpos($html, 'data-qv="confidence=verified"') !== false,
      'and one of them has to be the official-filings view');

/*
 * THE HEADCOUNT CONTROL, WHICH IS A QUICK VIEW NOW AND NOT A PANEL FILTER.
 *
 * It shipped in the primary filter row reading "Only Updates That Move
 * Headcount", and the owner asked what that meant. It filters
 * signal_direction IN ('hiring','displacement') and reads nothing at all from
 * the `headcount` column: measured 2026-07-29 over 15,711 current rows,
 * headcount is non-null on 11 while that direction test is true on 53. So the
 * old label promised a column it does not touch, and the set it does return is
 * a third of one percent of the page.
 *
 * Both halves of the fix are pinned here, because either one alone regresses.
 * The BUTTON has to exist and has to carry its count, or the control is gone and
 * an existing share link has nothing to drive it. The CHECKBOX has to still
 * exist somewhere in the markup, or applyUrlState(), the chips bar and both
 * exports lose the parameter without anything visible breaking.
 */
check(strpos($html, 'data-qv="stated_headcount=1"') !== false,
      'the headcount cut is a quick view, beside the other two narrow cuts');
check(strpos($html, 'id="tit-stated-n"') !== false,
      'and it prints its own count, so a reader sees how small the set is '
      . 'before clicking rather than after');
check(strpos($html, 'id="tit-f-stated_headcount"') !== false,
      'while the checkbox it drives survives as state, or the querystring, the '
      . 'chips bar and the exports quietly lose the parameter');
check(strpos($html, 'Only Updates That Move Headcount') === false,
      'and the label that promised a headcount column is gone from the page');

/*
 * THE CONTROLS ARE A BAR ABOVE THE ROWS.
 *
 * This REPLACES the assertion that they were a column beside the rows, which
 * shipped in 1.54.0 and which the owner has since seen and rejected: "move
 * those to above the stuff and compact and have it frozen on top when you
 * scroll down". The column cost the table 282px of a 1340px content width and
 * that is what wrapped the What Happened cell to one word per line.
 *
 * The structure is what can be checked from markup. `position:sticky` is a
 * stylesheet rule and cannot be, which is exactly why it is verified in a real
 * DOM instead and the measurement recorded in the TECHLOG.
 */
foreach (array('tit-feed', 'tit-filterbar', 'tit-panel-head', 'tit-panel-body',
               'tit-results') as $cls) {
    check(strpos($html, 'class="' . $cls) !== false
          || strpos($html, ' ' . $cls . '"') !== false
          || strpos($html, '"' . $cls . '"') !== false,
          "the feed layout needs .{$cls}, or the bar is not above the rows");
}
/*
 * THE BAR IS BEFORE THE ROWS IN THE DOCUMENT, not merely present.
 *
 * A bar that renders after the table would still satisfy every class check
 * above and would still be sticky; it would simply pin the wrong thing. Source
 * order is also what a reader with no CSS and a reader on a screen reader get,
 * and both should meet the controls before the rows they control.
 */
check(strpos($html, 'class="tit-filterbar"') < strpos($html, 'class="tit-results"'),
      'the filter bar has to come BEFORE the results in the document, or the '
      . 'controls are below the table for anyone without the stylesheet');

/*
 * THE PHONE AFFORDANCE SHIPS INERT.
 *
 * The bar collapses to one button below 900px, and that button is revealed by
 * script. Shipped visible it would be a control that opens nothing for a reader
 * whose JavaScript never ran, and the bar they get is already open.
 */
check(strpos($html, 'id="tit-bar-toggle"') !== false,
      'the phone bar needs its Filters toggle in the markup');
check(preg_match('/id="tit-bar-toggle"[^>]*\shidden/', $html) === 1,
      'and it must ship `hidden`, because script is what makes it do anything. '
      . 'A no-JS reader gets the whole bar open instead');
check(preg_match('/id="tit-bar-toggle"[^>]*aria-expanded="false"/', $html) === 1,
      'with aria-expanded set at construction rather than on first use: a '
      . 'trigger that reports no state until somebody has already pressed it '
      . 'tells a screen reader nothing at the moment it matters');

/*
 * THE DATE RANGE IS ADDRESSABLE.
 *
 * It is the one dropdown whose panel holds inputs rather than a checkbox group,
 * and dashboard.js finds that cell by id. Without the id the two date inputs
 * stay flat on the bar, which is 260px of a control that says "no dates chosen"
 * on almost every page view.
 */
check(strpos($html, 'id="tit-field-daterange"') !== false,
      'the date range cell needs its id, or script cannot make it a dropdown');
// Reset moved to the top of the panel and kept its id, so the same handler
// binds it. Two elements with that id would bind only the first.
check(substr_count($html, 'id="tit-reset"') === 1,
      'exactly one reset control, at the top of the panel where somebody '
      . 'starting over will look for it');

/*
 * NO WORD MAY NAME TWO DIFFERENT GROUPS IN ONE PANEL.
 *
 * Three did. "Manufacturing" was both a Team or Function and an Industry,
 * "Education" was both an Industry and an Employer Type, and "IPO" was both a
 * Funding Stage and a Deal Type. Each pair is a genuinely different question, so
 * the fix is wording and never the vocabulary: pipeline/vocab.py is fixed and a
 * value that will not normalise is a rejected record, not a new category. Only
 * the two server-rendered groups can be checked here; the other three are filled
 * from /facets and their labels live in dashboard.js.
 */
$fn_block = substr($html, strpos($html, 'id="tit-f-function"'));
$fn_block = substr($fn_block, 0, strpos($fn_block, '</select>'));
$ind_block = substr($html, strpos($html, 'id="tit-f-industry"'));
$ind_block = substr($ind_block, 0, strpos($ind_block, '</select>'));
check(strpos($fn_block, '>Manufacturing<') === false,
      'the Team or Function group may not offer the bare word "Manufacturing": '
      . 'the Industry group beside it already means something else by it');
check(strpos($ind_block, '>Manufacturing<') !== false,
      'while Industry keeps it, because that is the sector reading a reader '
      . 'expects');

check(substr_count($html, 'class="tit-region') >= 6,
      'the region strip drops a region with nothing in it and keeps the rest');
check(substr_count($html, 'class="tit-cbtn"') === 10,
      'ten country buttons, by live row count, and not a hardcoded list');
check(strpos($html, 'tit-citybtn') !== false, 'and the top cities row below them');

/* --- every city pill has to return what it promises ----------------------- */

/*
 * A PILL THAT CONTRADICTS THE PAGE IT LINKS TO IS WORSE THAN NO PILL.
 *
 * Clicking one writes city=<name>, which api.php resolves with the clause
 * below. So the number printed on the pill must equal the number of rows that
 * clause selects under the same base clause the rest of the hero uses. Three
 * separate defects lived in the one query that builds this strip, and each of
 * them shows up here as a pill whose count is not the count you get:
 *
 *   grouping by bare `city`       -- London printed 18 and returned 1,338
 *   WHERE is_current = 1 only     -- San Francisco printed 1,800 routine
 *                                    filings the table was not showing
 *   a non-aggregated country      -- the flag was whichever row came first
 *
 * The clause is read from tit_place_kinds() rather than written here, and
 * test_place_pages.py already asserts that string is identical to the API's.
 */
$city_kind   = tit_place_kinds()['city'];
$city_clause = $city_kind['where'];
// The clause names the same value once per geography column, so it takes as
// many arguments as tit_place_kinds() says it does. Hardcoding one silently
// bound the second %s to an empty string and every count came back 0.
$city_args   = (int) $city_kind['args'];
$base_where = 'is_current = 1 AND ' . tit_notable_where();

preg_match_all(
    '/data-city="([^"]*)".*?class="tit-cbtn-n">([\d,]+)</s',
    $html, $pills, PREG_SET_ORDER
);
check(count($pills) > 0, 'the top cities strip has to carry pills to check');
foreach ($pills as $pill) {
    $name = html_entity_decode($pill[1], ENT_QUOTES, 'UTF-8');
    $printed = (int) str_replace(',', '', $pill[2]);
    $actual = (int) $wpdb->get_var(
        "SELECT COUNT(*) FROM wp_tit_signals WHERE {$base_where} AND "
        . $wpdb->prepare($city_clause, array_fill(0, $city_args, $name))
    );
    check($printed === $actual,
          "the {$name} pill prints " . number_format($printed) . ' and clicking it '
          . 'returns ' . number_format($actual)
          . '. The strip has to be counted under the clause it filters by.');
}

$city_names = array_map(fn($p) => html_entity_decode($p[1], ENT_QUOTES, 'UTF-8'), $pills);
check(in_array('Edinburgh', $city_names, true),
      'Edinburgh is placed only by its employers\' head offices and has 120 rows '
      . 'here, so it belongs in a strip that carries cities with fewer: '
      . implode(', ', $city_names));
check(!in_array('San Francisco', $city_names, true),
      'San Francisco holds 1,800 routine officer filings and nothing else, and '
      . 'the default view sets those aside, so it cannot lead this strip');

// The flag is the MODAL country for the city, ties broken alphabetically, which
// is deterministic and is also the answer a reader would give.
$ottawa = null;
foreach ($pills as $pill) {
    if (html_entity_decode($pill[1], ENT_QUOTES, 'UTF-8') === 'Ottawa') $ottawa = $pill[0];
}
check($ottawa !== null, 'Ottawa (100 rows) should be in the strip: '
      . implode(', ', $city_names));
if ($ottawa !== null) {
    check(strpos($ottawa, tit_flag('CA')) !== false,
          'Ottawa holds 92 Canadian rows and 8 American ones, so it wears the '
          . 'Canadian flag rather than whichever row the engine reached first');
}

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

/* --- the archived copy, where one exists and only there ------------------ */

/*
 * A source link that dies turns a sourced claim into an unsourced one while the
 * page looks unchanged, which is the one failure this product cannot absorb. The
 * fallback is a snapshot link beside the publisher's own, and the whole value of
 * it is that it is TRUE: printed where a copy is on file, absent where one is
 * not, never a placeholder and never a link to a page that is not there.
 *
 * The row-by-row walk below is the only version of that claim a string check can
 * hold. Counting spans would pass on a render that printed the link on every row
 * and on a render that printed it on none.
 */
preg_match_all('/<tr>.*?<\/tr>/s', $tbody, $tr_matches);
$rows_seen = array('archived' => 0, 'plain' => 0);
foreach ($tr_matches[0] as $tr) {
    $has_span = strpos($tr, 'class="tit-archived"') !== false;
    if (strpos($tr, 'TEST FIXTURE Archived Outlet') !== false) {
        $rows_seen['archived']++;
        check($has_span, 'a row whose document has a saved copy has to offer it');
    } else {
        if (strpos($tr, 'TEST FIXTURE Unarchived Outlet') !== false) $rows_seen['plain']++;
        check(!$has_span,
              'and a row with no saved copy must print no link at all: a dead or '
              . 'placeholder "Archived" is worse here than an absent one');
    }
}
check($rows_seen['archived'] > 0 && $rows_seen['plain'] > 0,
      'the first page has to contain both kinds of row or this asserts nothing: '
      . $rows_seen['archived'] . ' archived, ' . $rows_seen['plain'] . ' not');

// Title Case, and the word by itself. "Wayback" is a brand a recruiter does not
// have to know, and the card footer is already dense.
check(substr_count($tbody, '>Archived</a>') === $rows_seen['archived'],
      'the visible text is the single word "Archived", in Title Case');
check(strpos($tbody, 'title="Archived copy at the Internet Archive"') !== false,
      'with the long form on the title attribute, for anyone who hovers');

// The separator is a CSS ::before. A literal middot in the markup wraps to the
// START of the next line inside the 390px card layout and reads as a bullet
// whose text went missing, which is the bug the meta line already carries a
// comment about.
check(strpos($tbody, '<span class="tit-archived"> ') === false
      && strpos($tbody, "\u{00B7}") === false,
      'and the separator before it is not a text node in the markup');

/* --- the dated glance panel ---------------------------------------------- */

/*
 * THE PANEL THE OWNER ASKED FOR, AND THE ONE RULE IT MUST NOT BREAK.
 *
 * The hero used to open with an undated lump of totals, which answers "how big
 * is this dataset" in the position where a reader is asking "what has moved".
 * The panel answers the second question on four rungs, and every figure on it is
 * computed on the matrix's existing scan.
 */
check(strpos($html, 'id="tit-dg"') !== false,
      'the dated glance panel has to render, and it is the first thing on the '
      . 'page that carries a date');
foreach (array('week', 'month', 'year') as $bucket) {
    check(strpos($html, 'data-dg="' . $bucket . '"') !== false,
          "the {$bucket} rung of the dated panel is missing");
}
// The year label is DERIVED from the clock. A typed "2026 so far" is a line that
// becomes wrong at midnight on 31 December and stays wrong until somebody reads
// it carefully, which is the same failure as corrections.php's hardcoded
// "$124.0bn" under a caption reading "Measured now".
check(strpos($html, '>' . date('Y') . ' so far<') !== false,
      'the year rung has to name the CURRENT year, derived rather than typed, '
      . 'so it becomes "' . (date('Y') + 1) . ' so far" by itself');

/*
 * EVERY FIGURE ON THE PANEL IS THE ONE THE DATABASE HOLDS.
 *
 * Read back out of the rendered markup and recomputed here from the same clause
 * the render used. A panel of headline numbers is the worst place on the site
 * for a figure that drifted from its source, and "computed, never typed" is only
 * a claim until something checks the arithmetic.
 */
$dg_date = 'COALESCE(published_date, DATE(captured_at))';
foreach (array('week'  => gmdate('Y-m-d', strtotime(gmdate('Y-m-d') . ' -6 days')),
               'month' => gmdate('Y-m-01'),
               'year'  => gmdate('Y-01-01')) as $bucket => $since) {
    $expect = (int) $wpdb->get_var(
        "SELECT COUNT(*) FROM wp_tit_signals WHERE {$base_where} AND {$dg_date} >= '{$since}'");
    if (preg_match('/data-dg="' . $bucket . '".*?<b>([\d,]+)<\/b> updates/s', $html, $m)) {
        check((int) str_replace(',', '', $m[1]) === $expect,
              "the {$bucket} rung prints {$m[1]} updates and the database holds "
              . number_format($expect) . '. Every figure on this panel is computed.');
    } else {
        check(false, "the {$bucket} rung has to print an update count");
    }
    // And it has to agree with the matrix cell for the same window, which is the
    // reason the two share one query rather than running two.
    check(strpos($html, 'data-since="' . $since . '"') !== false,
          "the {$bucket} rung and the matrix column for the same window have to "
          . 'carry the same data-since, or one handler cannot drive both');
}

/*
 * THE COMPARISON THAT MUST NOT BE INVENTED.
 *
 * The sibling can print "down 25% vs the week before" because it holds years.
 * This tracker's news collectors first ran on 2026-07-27 and national_press on
 * 2026-07-29, so a week-over-week figure drawn today divides a populated week by
 * one that mostly predates the collector, and prints something in the thousands
 * of percent. That is not an exaggerated trend, it is an artefact of the corpus
 * start date wearing a statistic's clothes, and it would be the most quotable
 * number on the page.
 *
 * Both directions are pinned, because a rule that only ever suppresses is
 * indistinguishable from a feature that never worked:
 *  - this fixture spans forty days, so the comparison IS printed;
 *  - with every older row deleted it must NOT be, and must say why.
 */
check(preg_match('/vs the week before/', $html) === 1,
      'this fixture holds forty days, so the week-over-week comparison should '
      . 'be printed: the rule has to switch itself ON once real history exists, '
      . 'or it is not a rule, it is a permanent suppression');

/* --- "Why you can trust this", and the FAQ tucked into it ---------------- */

/*
 * THE CONTRACT THIS PANEL LIVES OR DIES BY: EVERY WORD IS IN THE INITIAL HTML.
 *
 * A tab that fetches its content on click hides that content from a crawler,
 * and an FAQ is among the most SEO-valuable blocks on a page. So both panels
 * are rendered server-side, always, in full, and JavaScript's whole job is to
 * add a class that lets the stylesheet hide one of them.
 *
 * The assertions below are made against markup produced with NO JavaScript
 * running at all, which is exactly the state a crawler sees. If a future
 * session ever moves a panel behind a fetch, every one of these fails.
 */
check(strpos($html, 'id="tit-trust"') !== false,
      'the "Why you can trust this" panel has to render; it existed nowhere in '
      . 'this product before, so its absence is not a regression, it is a '
      . 'deletion');
foreach (array('Sourced', 'Unconverted', 'Unguessed', 'Correctable') as $item) {
    check(strpos($html, $item) !== false,
          "the {$item} item is missing from the trust panel");
}
check(substr_count($html, 'class="tit-trust-k"') === 4,
      'four numbered items, and the stylesheet lays them out 1 / 2 / 4 across '
      . 'so there is no width at which the fourth is stranded alone on a second '
      . 'row, which is what the mock\'s auto-fit grid does');

// Real tab semantics, not two divs and a click handler.
check(strpos($html, 'role="tablist"') !== false
      && substr_count($html, 'role="tab"') === 2
      && substr_count($html, 'role="tabpanel"') === 2,
      'the tabs have to be real tabs: a tablist, two tabs and two panels');
/*
 * Counted PER TAB, not across the page. This asserted `substr_count($html,
 * 'aria-controls=') === 2` and so quietly meant "no other element on this page
 * may ever control another": adding the filter bar's own phone toggle, which
 * legitimately points at the panel it opens, failed a test about tab semantics.
 * A test should fail for the thing it names.
 */
preg_match_all('/<button[^>]*\brole="tab"[^>]*>/', $html, $tab_tags);
check(count($tab_tags[0]) === 2, 'two tab buttons');
foreach ($tab_tags[0] as $tag) {
    check(strpos($tag, 'aria-selected=') !== false
          && strpos($tag, 'aria-controls=') !== false,
          'each tab states whether it is selected and which panel it controls');
}

/*
 * NEITHER PANEL MAY BE HIDDEN IN THE MARKUP.
 *
 * `hidden` is applied by dashboard.js, never by the server. If the server ever
 * ships one panel hidden, a reader with no JavaScript loses it completely and a
 * crawler reads a page with half its content marked away — which is the failure
 * this whole design exists to avoid.
 */
$trust_block = substr($html, strpos($html, 'id="tit-trust"'));
$trust_block = substr($trust_block, 0, strpos($trust_block, '</script>') + 9);
check(strpos($trust_block, 'role="tabpanel" hidden') === false
      && strpos($trust_block, 'hidden role="tabpanel"') === false,
      'no panel may be server-rendered hidden. With JavaScript off both stack, '
      . 'and that is the state a crawler reads');
// And each panel keeps a heading of its own, which is what labels it when the
// tab strip is not there to.
check(substr_count($html, 'class="tit-tabpanel-h"') === 2,
      'each panel carries its own heading, so with no JavaScript the two '
      . 'degrade to stacked headings and answers rather than to unlabelled prose');

/*
 * EVERY NUMBER IN THE COPY IS COMPUTED.
 *
 * A panel whose subject is trustworthiness is the last place on this site that
 * can carry a stale figure. corrections.php once shipped a typed "$124.0bn"
 * captioned "Measured now" against a live figure of $101B; the sibling's press
 * page still carries a hardcoded "51 ... we currently carry every one of them"
 * with no query behind it. This asserts the panel's figures move with the data
 * by checking them against the database rather than against a string.
 */
$trust_verified = (int) $wpdb->get_var(
    "SELECT COUNT(*) FROM wp_tit_signals WHERE {$base_where} AND confidence = 'verified'");
check(strpos($html, number_format_i18n($trust_verified) . ' of the ') !== false,
      'the Sourced item states the official-filings count from the database ('
      . number_format_i18n($trust_verified) . ')');
$trust_routine = (int) $wpdb->get_var(
    "SELECT COUNT(*) FROM wp_tit_signals WHERE is_current = 1 AND materiality = 'routine'");
check(strpos($html, number_format_i18n($trust_routine) . ' of the ') !== false,
      'and the FAQ states the hidden-rows count from the database ('
      . number_format_i18n($trust_routine) . ')');

/*
 * THE FAQ ITSELF, AND ITS STRUCTURED DATA.
 *
 * The FAQPage block is a straight duplicate of visible prose and is only
 * defensible because the prose IS visible: company.php and places.php both
 * record the sibling's manual-action risk from emitting identical FAQPage
 * markup across ~1,830 URLs where the answers appeared nowhere in the document.
 * So this checks the two together — the schema may exist only while every
 * question it names is also rendered as text.
 */
check(substr_count($html, 'class="tit-faq-q"') >= 6,
      'the FAQ has to carry its questions as real headings in the markup');
check(strpos($html, '"@type":"FAQPage"') !== false,
      'and the FAQPage structured data, which is worth its bytes only because '
      . 'the answers are on the page');
if (preg_match('/"@type":"FAQPage".*?<\/script>/s', $html, $ld)) {
    $decoded = json_decode(substr($ld[0], 0, strrpos($ld[0], '}') + 1), true);
    foreach (($decoded['mainEntity'] ?? array()) as $q) {
        check(strpos($html, esc_html($q['name'])) !== false,
              'the schema names a question that is not rendered on the page: "'
              . $q['name'] . '". Structured data may only describe what a reader '
              . 'can read, or this is the sibling\'s manual action again');
    }
}
// The project cannot support these, so its own FAQ may not claim them.
foreach (array('100% automated', 'real time', 'comprehensive', 'most advanced') as $overclaim) {
    check(stripos($html, $overclaim) === false,
          'the page claims "' . $overclaim . '", which this project cannot '
          . 'support. The automation figure is ~99% and names the human sliver');
}

$wpdb->pdo->exec("DELETE FROM wp_tit_signals WHERE {$dg_date} < '"
                 . gmdate('Y-m-d', strtotime(gmdate('Y-m-d') . ' -9 days')) . "'");
$young = cold_render();
check(strpos($young, 'vs the week before') === false,
      'a corpus whose history starts INSIDE the comparison window must not emit '
      . 'a percentage. This is the "up 4,000%" case and it is a fabrication, not '
      . 'a large number.');
check(preg_match('/\bup <b>\d+%|\bdown <b>\d+%/', $young) === 0,
      'and no percentage of any kind reaches the week rung while the prior week '
      . 'is outside what we hold');
check(strpos($young, 'we do not hold a full week before this one') !== false,
      'the absence has to be STATED. A reader who sees nothing cannot tell '
      . '"flat" from "we cannot say yet", and the second is the honest answer');

/* --- the assets ---------------------------------------------------------- */

check(isset($GLOBALS['tit_enqueued']['style']['tit-dashboard']),
      'the stylesheet is enqueued from inside the shortcode, because the '
      . 'wp_enqueue_scripts guard is false whenever the shortcode arrives through a block');
check(count($GLOBALS['tit_enqueued']['style']) === 1,
      'and it stays ONE file: a second stylesheet is a second blocking request in the head');
check(($GLOBALS['tit_enqueued']['script']['tit-dashboard']['footer'] ?? false) === true,
      'dashboard.js loads in the footer, never in the head');
check(($GLOBALS['tit_enqueued']['script_data']['tit-dashboard']['strategy'] ?? '') === 'defer',
      'and it is deferred. Every other script on the live page carries defer; a '
      . 'parser-blocking one in the footer holds up the end of the document for '
      . 'no reason, because nothing in that file needs to run before parsing ends');
/* --- the byte budget ---------------------------------------------------- */

/*
 * MEASURED WITH THE FIXTURE PREFIX STRIPPED, and that is not a way of gaming the
 * budget. "TEST FIXTURE " exists so no human mistakes this render for the live
 * page, it appears once per row plus once per employer link, and it is about
 * 2.1KB that PRODUCTION NEVER SHIPS. Counting it would spend real headroom on a
 * test artefact and would eventually fail a legitimate change for a reason
 * nobody could find. The budget has to measure the page.
 */
$bytes = strlen(str_replace('TEST FIXTURE ', '', $html));
$GLOBALS['tit_bytes'] = $bytes;
// The full-corpus render, kept for the optional browser dump in finish(). Not
// the trimmed one below it, which exists only to prove a suppression.
$GLOBALS['tit_dump_html'] = $html;
check($bytes <= TIT_DASH_BYTE_BUDGET,
      'the markup must stay inside ' . number_format(TIT_DASH_BYTE_BUDGET)
      . ' bytes and was ' . number_format($bytes)
      . ' (fixture prefixes excluded). This page is read on phones; a new card '
      . 'is not free.');

/* --- Title Case on control labels, as an assertion ----------------------- */

/*
 * THE OWNER HAS ASKED FOR THIS THREE TIMES, so it stops being a habit and
 * becomes a test. It regressed twice because Title Case was a convention nobody
 * could check: a new label written in sentence case looked exactly as correct as
 * a right one.
 *
 * CONVENTIONAL Title Case, not every-word-capitalised. Short conjunctions,
 * articles and prepositions stay lowercase inside a label, because "Pay And
 * Benefits" and "Ways Of Working" are not how a person writes and the owner's
 * other complaint about these strings was that they did not read as if a person
 * had. First word always capitalises.
 *
 * SCOPE, deliberately narrow: the matrix row labels, the chart card headings and
 * the section headings. NOT the option lists inside a control -- eighteen
 * industries and seventeen functions are a vocabulary being listed, not labels,
 * and shouting them would make the panel louder rather than clearer. Where that
 * line falls is a judgement; what matters is that it is written down here rather
 * than rediscovered.
 */
$tit_small = array('a','an','and','as','at','but','by','for','in','of','on','or',
                   'the','to','vs','with');
$title_case_ok = function ($label) use ($tit_small) {
    $words = preg_split('/\s+/', trim($label));
    foreach ($words as $i => $w) {
        // Strip surrounding punctuation; leave the word itself alone.
        $bare = preg_replace('/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/u', '', $w);
        if ($bare === '') continue;
        if ($i > 0 && in_array(mb_strtolower($bare), $tit_small, true)) continue;
        // An all-caps acronym (IPO, HR, IT, CSV) is already fine.
        if ($bare === mb_strtoupper($bare)) continue;
        if (mb_substr($bare, 0, 1) !== mb_strtoupper(mb_substr($bare, 0, 1))) {
            return false;
        }
    }
    return true;
};

// The matrix row labels, read out of the markup rather than out of the source,
// so this checks what a reader actually gets.
preg_match_all('/<th scope="row">([^<]+)/', $html, $mrows);
check(count($mrows[1]) >= 5,
      'the matrix has to render its signal rows for this to mean anything, and '
      . 'rendered ' . count($mrows[1]));
foreach ($mrows[1] as $label) {
    check($title_case_ok($label),
          'matrix row label "' . $label . '" is not Title Case. The owner has '
          . 'asked for Title Case three times; see $labels in shortcodes.php');
}

// Chart card headings and section headings.
preg_match_all('/<h3>([^<]+)<\/h3>/', $html, $heads);
check(count($heads[1]) >= 4, 'the chart cards have to render their headings');
foreach ($heads[1] as $label) {
    check($title_case_ok($label),
          'heading "' . $label . '" is not Title Case');
}

/* --- ONE vocabulary, not two -------------------------------------------- */

/*
 * The charts said "Pay and benefits" while the matrix beside them said "Pay
 * news" for the same rows, and "Growing and expanding" against "Funding raised".
 * A reader had to work out that two phrases meant one thing. These assert the
 * words that were retired, so a future edit cannot quietly bring a second
 * vocabulary back.
 */
foreach (array('Hiring up', 'Pay news', 'All updates', 'Funding raised',
               'Money raised is the exception', 'Cutting back') as $retired) {
    check(strpos($html, $retired) === false,
          'the retired phrase "' . $retired . '" is back on the page. The page '
          . 'has ONE vocabulary; see the note beside $labels in shortcodes.php');
}
check(strpos($html, 'Adding Roles') !== false,
      'and the replacement for "Hiring up" is on the page, so this is a rename '
      . 'and not a deletion');

/* --- the stacked matrix needs its period labels as REAL TEXT ------------- */

/*
 * Below 860px the matrix is laid out one card per row, which drops the implicit
 * table roles and with them the column header. A CSS ::after on a data attribute
 * cannot replace it: generated content is not reliably in the accessibility
 * tree, is not selectable and is not findable. So the period is markup, and
 * there has to be one per cell.
 */
// Matched on the word boundary, not on the prefix: 'class="tit-cell' also
// matches the period label's own class and would count every cell twice.
$cells = preg_match_all('/class="tit-cell[ "]/', $html);
$periods = substr_count($html, 'class="tit-cell-p"');
check($cells > 0 && $periods === $cells,
      'every matrix cell needs its period printed as real text for the stacked '
      . "phone layout: {$cells} cells, {$periods} period labels");

/* --- the pill groups have to swap into a box the same size -------------- */

/*
 * dashboard.js replaces each multiple select with a row of pills AFTER the page
 * has painted, because it loads in the footer. If the two boxes are not the same
 * height the reader watches the filter panel resize and everything below it
 * move, which is the definition of a layout shift. The stylesheet fixes both to
 * one height; this asserts the markup still hands it two boxes to fix, since a
 * select that lost its `multiple` would never be pillified at all.
 */
check(substr_count($html, 'multiple size="5"') === 7,
      'seven multiple selects become pill groups, and the stylesheet reserves the '
      . 'height of each: found ' . substr_count($html, 'multiple size="5"'));

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

    // THE WARM PATH, which is what a reader actually gets. Every aggregate on
    // this page is filter-independent -- the render reads no request state at
    // all -- so the whole thing is one transient and a second render touches the
    // database not at all. A key written one way and read another is invisible
    // in a diff and costs the full count on every single request, which is
    // exactly the mistake this zero exists to catch.
    $wpdb->reset_reads();
    render();
    check($wpdb->reads === 0,
          'a warm render must cost no queries at all, and cost ' . $wpdb->reads
          . trace($wpdb->log));

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
            'company' => 'TEST FIXTURE Grown Employer ' . ($i % 2000),
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
        printf("  budget ok: %d queries cold, none warm.\n", TIT_DASH_QUERY_BUDGET);
        exit(0);
    }
    /*
     * OPTIONAL: write the rendered markup out, for measuring in a real browser.
     *
     * Three of this page's properties cannot be asserted from a string and have
     * to be measured in a layout engine: whether `position:sticky` actually
     * pins, whether anything overflows a 390px viewport, and how many pixels
     * wide a given table column ends up. Every one of those has shipped broken
     * here while the markup was correct, and the sticky one fails SILENTLY.
     *
     * Off by default and gated on an environment variable, so the harness stays
     * a test rather than a build step, and it writes nowhere near the repo
     * unless asked. Nothing reads the file back: it is for a human or an agent
     * driving a browser.
     */
    $dump = getenv('TIT_DUMP_HTML');
    if ($dump) {
        file_put_contents($dump, $GLOBALS['tit_dump_html']);
        fwrite(STDERR, "wrote markup to {$dump}\n");
    }

    // The budget needs a process where nothing has rendered yet.
    $command = escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' budget';
    passthru($command, $status);
    if ($status !== 0) exit(1);
    printf("dashboard ok: markup, controls, %s bytes of it, and a %d-query cold render.\n",
           number_format($GLOBALS['tit_bytes']), TIT_DASH_QUERY_BUDGET);
    exit(0);
}
