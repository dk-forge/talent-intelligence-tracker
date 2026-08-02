<?php
/**
 * Run the country, city and industry pages: the gate, the routing, the sitemap,
 * and the number of database queries a render costs.
 *
 * WHY THIS IS A RUNNING HARNESS AND NOT A TEXT ASSERTION.
 *
 * Three of the things that matter here cannot be read out of the source.
 *
 *  - THE GATE IS A DECISION ABOUT DATA. Whether Belfast gets a page depends on
 *    30 documents sitting behind one employer, and the only way to know that the
 *    employer bar is the thing stopping it is to build that shape and ask.
 *  - WHETHER A URL 404s, 301s OR 302s is a behaviour across three states of the
 *    same cell. company.php shipped 22 broken sitemap URLs on 1.45.4 with source
 *    that read correctly.
 *  - QUERY COUNT IS NOT VISIBLE IN A DIFF. "No N+1" is the kind of claim that is
 *    true on the day it is written and false three commits later, because what
 *    breaks it is one innocent call inside a foreach. So $wpdb counts, and the
 *    count is asserted against TIT_PLACE_QUERY_BUDGET, including against the
 *    same cell after its rows are tripled.
 *
 * So WordPress is stubbed and the SQL is real: $wpdb runs against in-memory
 * SQLite with the plugin's own column shape, and api.php, shortcodes.php and
 * company.php are the REAL files, so the industry vocabulary, the funding
 * predicate, the money formatting and the employer slug refusal are the ones
 * that ship rather than copies that can drift.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/render_place_pages.php
 */

define('ABSPATH', __DIR__);
// TIT_PATH, TIT_URL, TIT_VERSION and TIT_TABLE_SUFFIX are NOT defined here: the
// plugin's own bootstrap defines them, and the version this harness runs against
// has to be the version that ships, because it is part of every cache key.
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
function rest_url($p = '') { return 'https://example.test/blog/wp-json/' . $p; }
function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url_raw($s) { return (string) $s; }
function esc_js($s) { return (string) $s; }
function wp_json_encode($v, $flags = 0) { return json_encode($v, $flags); }
function number_format_i18n($n, $d = 0) { return number_format((float) $n, (int) $d); }
function date_i18n($f, $t = null) { return gmdate($f, $t === null ? time() : $t); }
function human_time_diff($a, $b = null) { return '1 hour'; }
function sanitize_text_field($s) { return trim((string) $s); }
function _n($single, $plural, $count, $domain = '') { return $count == 1 ? $single : $plural; }
function _x($s, $c, $d = '') { return $s; }
function __($s, $d = '') { return $s; }
function wp_strip_all_tags($s) { return strip_tags((string) $s); }
function is_singular() { return false; }
function wp_enqueue_style() {}
function wp_enqueue_script() {}
function wp_localize_script() {}
function current_time($t, $gmt = 0) {
    // Honour the requested format: tit_archive_pending_note() asks for
    // 'Y-m-d' and feeds it to strtotime, where a full datetime is a parse
    // failure rather than a date.
    return $t === 'timestamp' ? time() : gmdate($t);
}
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

/*
 * The page shell. tit_render_header() is the REAL one, and with
 * wp_is_block_theme() absent it takes its own classic fallback, so these two
 * stubs are where the markers the assertions look for come from. Stubbing
 * tit_render_header() itself would skip the branch the live site does not take
 * and prove nothing about the one it does.
 */
function get_header() { echo '<!--header-->'; }
function get_footer() { echo '<!--footer-->'; }

/**
 * $wpdb, backed by SQLite, COUNTING EVERY READ.
 *
 * The count is as much the point of this class as the SQL is. prepare() is the
 * real thing's contract and nothing more: %s becomes a quoted literal, %d an
 * integer, which is all this plugin uses.
 */
class PlaceHarnessDb {
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
            'headline' => 'An update numbered ' . $n,
            'talent_readthrough' => 'What it means for hiring.',
            'company' => 'Employer ' . $n,
            'company_key' => 'employer ' . $n,
            'pillar' => 'company_development',
            'signal_direction' => 'hiring',
            'confidence' => 'verified',
            'source_url' => 'https://example.test/doc/' . $n,
            'source_name' => 'SEC EDGAR',
            'published_date' => '2026-07-0' . (1 + ($n % 9)),
            'captured_at' => '2026-07-20 00:00:00',
        ), $opts);
        $columns = implode(', ', array_keys($row));
        $marks = implode(', ', array_fill(0, count($row), '?'));
        $stmt = $this->pdo->prepare("INSERT INTO wp_tit_signals ({$columns}) VALUES ({$marks})");
        $stmt->execute(array_values($row));
    }
}

$GLOBALS['wpdb'] = new PlaceHarnessDb();
global $wpdb;

// THE WHOLE PLUGIN, through its own bootstrap, rather than a chosen subset. The
// industry vocabulary, tit_funding_where(), the money formatting, the country
// names and company.php's slug refusal all have to be the shipping ones or this
// harness proves something about itself, and loading the bootstrap also proves
// that places.php is actually wired into it.
require $tit_plugin . 'talent-intelligence-tracker.php';
if (!function_exists('tit_place_index')) {
    fwrite(STDERR, "places.php is not loaded by the plugin bootstrap\n");
    exit(1);
}

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

/* ------------------------------------------------------------------------
   THE DATASET, shaped like the live distribution rather than like a fixture
   that happens to pass. Each cell is kept in its own country so that one
   cell's shape cannot quietly change another's counts.
   ------------------------------------------------------------------------ */

/**
 * @param array $place     the columns that place every row (country, city, industry)
 * @param int   $docs      distinct source documents, one per row
 * @param int   $employers distinct employers they are spread over
 * @param array $sources   the source names, cycled
 */
function seed_cell(array $place, $docs, $employers, array $sources, $prefix) {
    global $wpdb;
    for ($i = 0; $i < $docs; $i++) {
        $wpdb->insert_row(array_merge($place, array(
            'company'     => ucfirst($prefix) . ' Employer ' . ($i % $employers),
            'company_key' => $prefix . ' employer ' . ($i % $employers),
            'source_name' => $sources[$i % count($sources)],
            'source_url'  => 'https://example.test/' . $prefix . '/' . $i,
        )));
    }
}

// GB: a gated country and a gated city inside it, dominated by ONE source. 34 of
// its 44 documents, which is the United Kingdom's real shape (4,761 of 4,808
// from the gender pay gap filing) and must produce the concentration note.
seed_cell(array('country' => 'GB', 'city' => 'London', 'industry' => 'technology'),
          34, 12, array('GOV.UK gender pay gap service'), 'london');
seed_cell(array('country' => 'GB', 'city' => 'London', 'industry' => 'financial_services'),
          6, 6, array('SEC EDGAR', 'SEC EDGAR (Form D)'), 'londonb');
// A city inside GB that is NOT gated, so the country's cross-links have to carry
// both cases: a link to a page, and a link to the filtered dashboard.
seed_cell(array('country' => 'GB', 'city' => 'Leeds', 'industry' => 'education'),
          4, 3, array('SEC EDGAR'), 'leeds');
const GB_DOCS = 44;
const GB_TOP_SOURCE = 34;

// A country below the document bar, which is where Israel sits today.
seed_cell(array('country' => 'IL', 'industry' => 'technology'),
          10, 8, array('SEC EDGAR', 'Reuters'), 'israel');

// THE TWO BARS THAT ARE NOT THE DOCUMENT BAR, each the only thing stopping a
// cell that would otherwise qualify.
// CA and Belfast: 30 documents, ONE employer. A page of one employer's annual
// filings titled "signals in Belfast" is a company profile wearing a city's name.
seed_cell(array('country' => 'CA', 'city' => 'Belfast', 'industry' => 'public_sector'),
          30, 1, array('GOV.UK gender pay gap service', 'SEC EDGAR'), 'belfast');
// IE and Cork: 30 documents, 6 employers, ONE source. One lens is not a market.
seed_cell(array('country' => 'IE', 'city' => 'Cork', 'industry' => 'pharma_biotech'),
          30, 6, array('SEC EDGAR'), 'cork');

// A slug two cells claim. Both would qualify alone, and neither is served,
// because serving either under the shared URL shows half a city.
seed_cell(array('country' => 'US', 'city' => 'St. Louis', 'industry' => 'manufacturing'),
          30, 6, array('SEC EDGAR', 'SEC EDGAR (Form D)'), 'stlouisa');
seed_cell(array('country' => 'US', 'city' => 'St Louis', 'industry' => 'manufacturing'),
          30, 6, array('SEC EDGAR', 'SEC EDGAR (Form D)'), 'stlouisb');

// Money, so the total and its coverage sentence are exercised: two rows state US
// dollars and one names a round with no amount, which is exactly the gap
// tit_money_coverage_sentence() exists to state.
$wpdb->insert_row(array('country' => 'US', 'city' => 'Austin', 'industry' => 'technology',
    'company' => 'Austin Employer 0', 'company_key' => 'austin employer 0',
    'funding_amount' => '$40 Million', 'funding_amount_usd' => 40000000,
    'funding_stage' => 'series_b', 'source_url' => 'https://example.test/austin/f1',
    'source_name' => 'SEC EDGAR (Form D)'));
$wpdb->insert_row(array('country' => 'US', 'city' => 'Austin', 'industry' => 'technology',
    'company' => 'Austin Employer 1', 'company_key' => 'austin employer 1',
    'funding_amount' => '$10 Million', 'funding_amount_usd' => 10000000,
    'funding_stage' => 'seed', 'source_url' => 'https://example.test/austin/f2',
    'source_name' => 'SEC EDGAR (Form D)'));
$wpdb->insert_row(array('country' => 'US', 'city' => 'Austin', 'industry' => 'technology',
    'company' => 'Austin Employer 2', 'company_key' => 'austin employer 2',
    'funding_stage' => 'series_a', 'source_url' => 'https://example.test/austin/f3',
    'source_name' => 'Reuters'));
seed_cell(array('country' => 'US', 'city' => 'Austin', 'industry' => 'technology'),
          27, 9, array('SEC EDGAR', 'SEC EDGAR (Form D)', 'Reuters'), 'austin');

// The archive pending state on a place page's recent-updates list. One
// publisher-sourced row with no snapshot, in a cell (Austin) otherwise made of
// registry rows: the sentence must appear once, with its derived date, and the
// registry rows must promise nothing — nothing will ever re-check an EDGAR
// filing. Austin rather than London because GB's document count is a named
// constant with a concentration caveat hanging off it.
$wpdb->insert_row(array('country' => 'US', 'city' => 'Austin', 'industry' => 'technology',
    'company' => 'Austin Press Employer', 'company_key' => 'austin press employer',
    'collector' => 'national_press', 'source_name' => 'TEST FIXTURE Press Outlet',
    'source_url' => 'https://example.test/austin/press-1'));

// A withdrawn row must not count towards any gate or any figure.
$wpdb->insert_row(array('country' => 'IL', 'industry' => 'technology', 'is_current' => 0,
    'company' => 'Retracted Employer', 'company_key' => 'retracted employer',
    'source_url' => 'https://example.test/retracted/1'));

/*
 * THE BUDGET PHASE RUNS HERE, and only in its own process.
 *
 * It has to be the first thing that touches the database, because company.php
 * memoises its slug index in a static and every assertion below would warm it.
 * Same reason route_company_slugs.php gives each of its phases a process: a
 * static that is correct in a request is wrong in a test that needs to see the
 * state before it was built.
 */
if (($argv[1] ?? '') === 'budget') {
    budget_phase();
    finish('budget');
}

/* ------------------------------------------------------------------------
   THE GATE
   ------------------------------------------------------------------------ */

$countries  = tit_place_index('country');
$cities     = tit_place_index('city');
$industries = tit_place_index('industry');

check(isset($countries['cells']['united-kingdom']),
      'a country is keyed by the slug of its NAME, not of its ISO code');
check(!isset($countries['cells']['gb']),
      '/country/gb/ is not a URL anybody searches for and must not be one');
check(($countries['cells']['united-kingdom']['documents'] ?? 0) === GB_DOCS,
      'the United Kingdom holds ' . GB_DOCS . ' documents: got '
      . ($countries['cells']['united-kingdom']['documents'] ?? 'nothing'));
check(($countries['cells']['united-kingdom']['gated'] ?? null) === true,
      'and clears every bar');
check(($countries['cells']['israel']['gated'] ?? null) === false,
      'Israel holds 10 documents and must not get a page');
check(($countries['cells']['israel']['records'] ?? 0) === 10,
      'a withdrawn row must not be counted: got '
      . ($countries['cells']['israel']['records'] ?? 'nothing'));

check(($cities['cells']['belfast']['documents'] ?? 0) === 30,
      'Belfast clears the document bar');
check(($cities['cells']['belfast']['employers'] ?? 0) === 1, 'on one employer');
check(($cities['cells']['belfast']['gated'] ?? null) === false,
      'so the EMPLOYER bar is what stops it, and it has to be doing that work');
check(($countries['cells']['canada']['gated'] ?? null) === false,
      'and the same rows must not give Canada a page either');

check(($cities['cells']['cork']['documents'] ?? 0) === 30, 'Cork clears the document bar');
check(($cities['cells']['cork']['sources'] ?? 0) === 1, 'from one source');
check(($cities['cells']['cork']['gated'] ?? null) === false,
      'so the SOURCE bar is what stops it');

check(($cities['cells']['london']['gated'] ?? null) === true, 'London has a page');
check(($cities['cells']['austin']['gated'] ?? null) === true, 'Austin has a page');
check(($cities['cells']['leeds']['gated'] ?? null) === false, 'Leeds does not');

// The predicate at its exact boundaries, so an off-by-one in either direction
// fails here rather than as a page that quietly appears or vanishes.
check(tit_place_meets_threshold(25, 3, 2) === true, 'the bar itself passes');
check(tit_place_meets_threshold(24, 99, 99) === false, 'one document short fails');
check(tit_place_meets_threshold(999, 2, 99) === false, 'one employer short fails');
check(tit_place_meets_threshold(999, 99, 1) === false, 'one source short fails');

/* --- collisions are refused, never resolved ----------------------------- */

check(isset($cities['collisions']['st-louis']),
      'two spellings of one city claiming one slug is a collision');
check(!isset($cities['cells']['st-louis']),
      'and neither of them is served under the shared URL');
check(!isset(tit_place_published('city')['st-louis']),
      'a refused slug is absent from the directory as well as from the page');

/* --- the closed vocabularies -------------------------------------------- */

check(isset($industries['cells']['technology']), 'technology has a page');
check(tit_place_slug('industry', 'pharma_biotech') === 'pharma-and-biotech',
      'the "&" in "Pharma & biotech" transliterates to "and", because no '
      . 'encoding of it survives both the rewrite and a sitemap: got '
      . tit_place_slug('industry', 'pharma_biotech'));
check(tit_place_slug('industry', 'not_a_real_industry') === '',
      'a stored value absent from the vocabulary has no name and so no page');
check(tit_place_slug('country', 'ZZ') === '',
      'an unmapped country code has no page rather than a page titled "ZZ"');
check(tit_place_slug('country', 'GB') === 'united-kingdom',
      'got ' . tit_place_slug('country', 'GB'));

/* ------------------------------------------------------------------------
   ROUTING: 404 for nothing, 301 for a non-canonical form, 302 for below the
   bar, a render for a page. Asked of tit_place_route(), which is the decision
   with the exit() taken out of it.
   ------------------------------------------------------------------------ */

/** What a request for one URL decides, with the memos cleared first. */
function route($kind, $slug) {
    $GLOBALS['tit_query_vars'] = array('tit_place_kind' => $kind, 'tit_place' => $slug);
    tit_place_forget();
    return tit_place_route(tit_place_current());
}

/** And what it renders, for the cells that render. */
function render($kind, $slug) {
    $GLOBALS['tit_query_vars'] = array('tit_place_kind' => $kind, 'tit_place' => $slug);
    tit_place_forget();
    $current = tit_place_current();
    $route = tit_place_route($current);
    if ($route['action'] !== 'render') return '';
    ob_start();
    tit_place_render($current['kind'], $current['cell'],
                     tit_place_facts($current['kind'], $current['cell']['key']));
    return ob_get_clean();
}

check(route('country', 'united-kingdom')['action'] === 'render', 'a gated cell renders');

$thin = route('country', 'israel');
check($thin['action'] === 'redirect', 'a below-threshold cell does not render');
check(($thin['code'] ?? 0) === 302,
      'and it redirects TEMPORARILY: a cell below the bar today crosses it as '
      . 'coverage grows, and a 301 tells every crawler never to ask again. Got '
      . ($thin['code'] ?? 'nothing'));
check(strpos($thin['url'] ?? '', 'country=IL') !== false,
      'to the dashboard filtered to the same cell, by its STORED key, which is '
      . 'what the dashboard filter reads: got ' . ($thin['url'] ?? ''));
check(strpos(route('city', 'belfast')['url'] ?? '', 'city=Belfast') !== false,
      'a city redirects by its stored name: got ' . (route('city', 'belfast')['url'] ?? ''));

check(route('country', 'atlantis')['action'] === '404',
      'a cell we hold nothing for is a 404: an empty page for every possible '
      . 'slug is the doorway-page pattern');
check(route('city', 'st-louis')['action'] === '404',
      'a refused slug 404s rather than serving half a city');
check(route('nonsense', 'united-kingdom')['action'] === '404',
      'and an unknown kind is a 404 rather than a notice');

$capitals = route('country', 'United-Kingdom');
check($capitals['action'] === 'redirect' && ($capitals['code'] ?? 0) === 301,
      'a slug that resolves but is not canonical 301s, so a cell is never '
      . 'indexable at two addresses');
check(($capitals['url'] ?? '') === 'https://example.test/blog/talent-intelligence-tracker/country/united-kingdom/',
      'to the canonical URL: got ' . ($capitals['url'] ?? ''));

/* ------------------------------------------------------------------------
   WHAT THE PAGE CARRIES
   ------------------------------------------------------------------------ */

$html = render('country', 'united-kingdom');

check(strpos($html, '<!--header-->') === 0,
      'the block-theme shell, never get_header(): these pages are the SEO surface');
check(substr_count($html, '<!--footer-->') === 1, 'and its footer, once');
check(strpos($html, '<h1>United Kingdom: Hiring, Funding And Leadership Signals</h1>') !== false,
      'the h1 leads with the name, so it needs no article and puts the word a '
      . 'reader searched for first');
check(strpos($html, 'placed in the United Kingdom') !== false,
      'and the prose takes the article English wants: "in United Kingdom" is not '
      . 'a sentence');
// The rule itself, at both ends, because only one gated country in this dataset
// is on either side of it.
check(tit_place_the('country', 'Ireland') === '',
      'a simple proper noun takes no article');
check(tit_place_the('country', 'United Kingdom') === 'the ', 'a compound does');
check(tit_place_the('country', 'Netherlands') === 'the ', 'and so does a plural');
check(tit_place_the('city', 'Netherlands') === '',
      'and the rule is about country names only: no city or industry takes one');
check(strpos(render('city', 'london'), 'placed in London') !== false,
      'so a city page reads plainly');
check(strpos($html, 'counts of documents we have read') !== false,
      'and the lede says what the numbers mean, which is the part a reader '
      . 'cannot infer');

// Live counts, all four of them.
check(strpos($html, '>' . GB_DOCS . '<') !== false, 'the record count is on the page');
check(strpos($html, 'updates tracked') !== false, 'labelled');
check(strpos($html, '>21<') !== false && strpos($html, 'employers<') !== false,
      'employers counted');
check(strpos($html, '>3<') !== false && strpos($html, 'sources<') !== false,
      'sources counted');

// Employers link to the pages that already exist.
check(strpos($html, '/talent-intelligence-tracker/company/london-employer-0/') !== false,
      'top employers link to their own profile pages');

// Every recent update links to its primary document.
check(strpos($html, 'https://example.test/london/') !== false,
      'recent updates link to the source document');

// Cross-links, both cases, and the dashboard.
check(strpos($html, '/talent-intelligence-tracker/city/london/') !== false,
      'a country links to the cities that have pages');
check(strpos($html, 'city=Leeds') !== false,
      'and to the filtered dashboard for the ones that do not');
check(strpos($html, '?country=GB') !== false,
      'and to the dashboard filtered to itself');

// The honest note, computed rather than written down.
$share = (int) round(100 * GB_TOP_SOURCE / GB_DOCS);
check(strpos($html, 'One source dominates this count.') !== false,
      GB_TOP_SOURCE . ' of ' . GB_DOCS . ' documents from one filing must produce the caveat');
check(strpos($html, 'GOV.UK gender pay gap service') !== false, 'naming the source');
check(strpos($html, '(' . $share . '%)') !== false,
      'and its share, computed: expected ' . $share . '%');

// A page NOT dominated by one source must not carry the note.
check(strpos(render('city', 'austin'), 'One source dominates') === false,
      'and the note vanishes when it stops being true');

// Money, never without its coverage sentence.
$austin = render('city', 'austin');
check(strpos($austin, 'disclosed across') !== false, 'the money figure is printed');
check(strpos($austin, 'funding update') !== false, 'with the number of rows behind it');
check(strpos($austin, 'of 3 funding updates that state a US dollar amount') !== false,
      'and never without the coverage sentence: 2 of 3 rows state an amount');

/*
 * The archive pending state, on this surface too. One Austin row is
 * publisher-sourced with no snapshot: its meta line says the sentence with the
 * date DERIVED from data/archive_promise.json. Every other Austin row is a
 * registry filing, so the sentence appears exactly once — a "we re-check
 * weekly" on documents the schedule never touches would be a false promise
 * printed thirty times.
 */
$place_promise = json_decode((string) file_get_contents(TIT_PATH . 'data/archive_promise.json'), true);
check(is_array($place_promise), 'data/archive_promise.json ships with the plugin');
$place_note = 'No archive snapshot yet. We re-check '
    . ((int) $place_promise['recheck_days'] === 7 ? 'weekly'
       : 'every ' . (int) $place_promise['recheck_days'] . ' days')
    . '; next check by '
    . gmdate('M j', strtotime(gmdate('Y-m-d') . ' 00:00:00 UTC')
             + (int) $place_promise['recheck_days'] * DAY_IN_SECONDS)
    . '.';
check(substr_count($austin, 'class="tit-archive-wait"') === 1
      && strpos($austin, esc_html($place_note)) !== false,
      'the publisher-sourced row without a snapshot says the pending sentence '
      . 'with its derived date, exactly once on this page: ' . $place_note);

// SEO furniture.
$GLOBALS['tit_query_vars'] = array('tit_place_kind' => 'country', 'tit_place' => 'united-kingdom');
tit_place_forget();
ob_start(); tit_place_head(); $head = ob_get_clean();
check(strpos($head, 'name="description"') !== false, 'a meta description');
check(strpos($head, 'rel="canonical"') !== false, 'a canonical');
check(strpos($head, '/country/united-kingdom/') !== false, 'pointing at the canonical URL');
check(strpos($head, 'name="robots"') === false,
      'and NO robots tag: every place page that exists is indexable, so a tag '
      . 'saying so could only duplicate or contradict the site default');
$title = tit_place_title('fallback');
check(strpos($title, 'United Kingdom') !== false && strpos($title, (string) GB_DOCS) !== false,
      'the title carries a live figure: got ' . $title);

// A below-threshold cell must not get head tags either: it has no page for them
// to describe.
$GLOBALS['tit_query_vars'] = array('tit_place_kind' => 'country', 'tit_place' => 'israel');
tit_place_forget();
ob_start(); tit_place_head(); $thin_head = ob_get_clean();
check(trim($thin_head) === '',
      'a cell that redirects prints no canonical and no description');

// Structured data describes only what is printed.
preg_match('#<script type="application/ld\+json">(.*?)</script>#s', $html, $ld);
check(!empty($ld[1]), 'the page emits JSON-LD');
$data = json_decode($ld[1] ?? '{}', true);
check(($data['@type'] ?? '') === 'CollectionPage', 'as a CollectionPage');
check(($data['about']['@type'] ?? '') === 'Place', 'about a Place');
check(strpos($ld[1] ?? '', 'FAQPage') === false, 'never FAQPage');
$listed = $data['mainEntity']['itemListElement'] ?? array();
check(count($listed) === TIT_PLACE_RECENT, 'listing the updates the page printed');
check(($data['mainEntity']['numberOfItems'] ?? -1) === count($listed),
      'numberOfItems counts the items LISTED, not the records we hold');
foreach ($listed as $item) {
    check(strpos($html, esc_html($item['headline'])) !== false,
          'every headline in the markup is visible on the page: ' . $item['headline']);
    check(strpos($html, esc_url($item['url'])) !== false,
          'and so is every URL: ' . $item['url']);
}

// Prose rules, on what actually reaches a reader.
check(strpos($html, '—') === false, 'no em-dash in UI copy');
foreach (array('best', 'biggest', 'largest', 'leading', 'most comprehensive',
               'definitive', 'fastest', 'world-class', '#1') as $word) {
    check(stripos($html, $word) === false, "superlative in UI copy: {$word}");
}

/* --- the caveat a CITY page needs and the other two do not -------------- */

$city_page = render('city', 'london');
check(strpos($city_page, 'grouped by the city name a source printed') !== false,
      'a city page says so: a city name is not unique in the world, and we store '
      . 'the name a source printed with no country of its own');
check(strpos($city_page, '/talent-intelligence-tracker/country/united-kingdom/') !== false,
      'and links to its country');

$industry_page = render('industry', 'technology');
check($industry_page !== '', 'an industry page renders');
preg_match('#<script type="application/ld\+json">(.*?)</script>#s', $industry_page, $ild);
check((json_decode($ild[1] ?? '{}', true)['about']['@type'] ?? '') === 'Thing',
      'an industry is not a Place, so the markup does not claim it is');
check(strpos($industry_page, 'Countries In This Industry') !== false,
      'an industry links to its countries');
check(strpos($industry_page, 'filed under Technology') !== false,
      'and an industry is something we FILED a row under, never somewhere a '
      . 'source placed it');
check(strpos($industry_page, 'placed in Technology') === false, 'so it does not say so');
check(strpos($industry_page, 'grouped by the city name') === false,
      'and carries no city caveat');

/* ------------------------------------------------------------------------
   THE DIRECTORY AND THE SITEMAP
   ------------------------------------------------------------------------ */

$GLOBALS['tit_query_vars'] = array('tit_places' => 1);
tit_place_forget();
ob_start(); tit_places_render(); $directory = ob_get_clean();

check(strpos($directory, '<h1>Countries, Cities And Industries We Cover</h1>') !== false,
      'the directory has an h1');
check(strpos($directory, '/country/united-kingdom/') !== false, 'and lists the gated cells');
check(strpos($directory, '/city/london/') !== false, 'across all three kinds');
check(strpos($directory, '/industry/technology/') !== false, 'including industries');
check(strpos($directory, '/country/israel/') === false,
      'and NOT the ones below the bar, which have no page to link to');
check(strpos($directory, '/city/belfast/') === false, 'nor Belfast');
check(strpos($directory, '/city/st-louis/') === false, 'nor a refused slug');
check(strpos($directory, 'Pages appear by themselves.') !== false,
      'and it says how a cell earns a page, in numbers');
check(strpos($directory, '—') === false, 'no em-dash in the directory either');

$sitemap = tit_places_sitemap_xml();
check(strpos($sitemap, '<?xml version="1.0" encoding="UTF-8"?>') === 0, 'the sitemap is XML');
check(strpos($sitemap, 'http://www.sitemaps.org/schemas/sitemap/0.9') !== false,
      'with the sitemap namespace');
check(strpos($sitemap, '/talent-intelligence-tracker/places/') !== false,
      'listing the directory itself');
check(strpos($sitemap, '/country/israel/') === false,
      'A URL IN A SITEMAP IS A PROMISE: a cell that redirects must not be in it');
check(strpos($sitemap, '/city/belfast/') === false, 'nor Belfast');
check(strpos($sitemap, '/city/cork/') === false, 'nor Cork');
check(strpos($sitemap, '/city/st-louis/') === false, 'nor a refused slug');

/*
 * THE ASSERTION THIS WHOLE HARNESS EXISTS FOR: the sitemap and the pages cannot
 * disagree, because every URL in it is requested and has to answer 200.
 * company.php's own sitemap shipped 22 URLs that did not, and a 20-URL hand
 * sample said it was fine.
 */
preg_match_all('#<loc>([^<]+)</loc>#', $sitemap, $locs);
$checked = 0;
foreach ($locs[1] as $loc) {
    if (strpos($loc, '/places/') !== false) continue;
    if (!preg_match('#/(country|city|industry)/([^/]+)/#', $loc, $m)) {
        $failures[] = "unrecognised sitemap URL {$loc}";
        continue;
    }
    $checked++;
    $decision = route($m[1], $m[2]);
    check($decision['action'] === 'render',
          "the sitemap lists {$loc}, so it must render and not answer "
          . ($decision['action'] === 'redirect' ? $decision['code'] : $decision['action']));
}
check($checked >= 5, 'and there has to be something in it to check: got ' . $checked);

// No future lastmod: a pay-versus-performance table is filed for a fiscal year
// that has not ended, and a crawler reads a future date as a broken one.
preg_match_all('#<lastmod>([^<]+)</lastmod>#', $sitemap, $mods);
$today = gmdate('Y-m-d');
foreach ($mods[1] as $mod) {
    check($mod <= $today, "lastmod {$mod} is in the future");
}

/* ------------------------------------------------------------------------
   THE QUERY BUDGET
   ------------------------------------------------------------------------
   The owner asked for these pages to be fast on desktop and on mobile, and the
   only version of that claim a test can hold is a bound on the work a render
   costs. Not a rough target: the exact number, so a query added inside a loop
   fails here instead of on the live site under a crawl.

   Measured in its own process, because company.php memoises its slug index in a
   static this harness cannot reach, and the first render in a process therefore
   costs two more queries than every render after it. Both figures are asserted,
   which also proves that the memoisation is doing its job.
   ------------------------------------------------------------------------ */

/** A full render with every transient dropped and every place memo cleared. */
function cold_render($kind, $slug) {
    global $wpdb;
    $GLOBALS['tit_transients'] = array();
    tit_place_forget();
    $wpdb->reset_reads();
    render($kind, $slug);
    return array('reads' => $wpdb->reads, 'log' => $wpdb->log);
}

function trace($cold) {
    return ":\n      " . implode("\n      ",
        array_map(fn($q) => substr($q, 0, 110), $cold['log']));
}

function budget_phase() {
    global $wpdb;

    // company.php's slug index is built once per process and shared with the
    // dashboard, so the first render pays for it and no later one does.
    $first = cold_render('country', 'united-kingdom');
    check($first['reads'] === TIT_PLACE_QUERY_BUDGET + 2,
          'the first cold render in a process pays company.php\'s slug index too, '
          . 'so it must cost ' . (TIT_PLACE_QUERY_BUDGET + 2) . ' and cost '
          . $first['reads'] . trace($first));

    foreach (array('country' => 'united-kingdom', 'city' => 'london',
                   'industry' => 'technology') as $kind => $slug) {
        $cold = cold_render($kind, $slug);
        check($cold['reads'] === TIT_PLACE_QUERY_BUDGET,
              "a cold {$kind} page must cost exactly " . TIT_PLACE_QUERY_BUDGET
              . ' queries and cost ' . $cold['reads'] . trace($cold));
    }

    // THE PROOF THAT THERE IS NO N+1. The same page against a cell with three
    // times the rows must cost the same. A per-row query shows up here as a
    // number that moved, which is the only way that mistake is ever caught
    // before a crawler finds it.
    $before = cold_render('country', 'united-kingdom');
    seed_cell(array('country' => 'GB', 'city' => 'London', 'industry' => 'technology'),
              90, 45, array('GOV.UK gender pay gap service', 'SEC EDGAR'), 'grown');
    $after = cold_render('country', 'united-kingdom');
    check($after['reads'] === $before['reads'],
          'the query count must not depend on how many rows a cell holds: '
          . $before['reads'] . ' before tripling them, ' . $after['reads'] . ' after');

    // And the warm path, which is what a reader actually gets: the five page
    // queries are one cached bundle and each index is one cached entry, so a
    // second render touches the database not at all.
    $wpdb->reset_reads();
    render('country', 'united-kingdom');
    check($wpdb->reads === 0,
          'a warm render must cost no queries at all, and cost ' . $wpdb->reads);

    $GLOBALS['tit_query_vars'] = array('tit_places' => 1);
    ob_start(); tit_places_render(); ob_end_clean();
    $wpdb->reset_reads();
    ob_start(); tit_places_render(); ob_end_clean();
    check($wpdb->reads === 0,
          'and so must the directory, which is three cached indexes and no more: '
          . 'cost ' . $wpdb->reads);

    $wpdb->reset_reads();
    tit_places_sitemap_xml();
    check($wpdb->reads === 0,
          'and so must the sitemap, off the same three: cost ' . $wpdb->reads);
}

/**
 * Report and stop. Declared as a function so the budget phase can stop where it
 * finishes rather than falling into assertions that would build the very state
 * it exists to measure.
 */
function finish($phase) {
    global $failures;
    if ($failures) {
        fwrite(STDERR, 'place pages FAILED'
                       . ($phase ? " in phase '{$phase}'" : '') . ":\n  - "
                       . implode("\n  - ", $failures) . "\n");
        exit(1);
    }
    if ($phase === 'budget') {
        printf("  budget ok: %d queries cold, none warm.\n", TIT_PLACE_QUERY_BUDGET);
        exit(0);
    }
    // The budget needs a process where nothing has rendered yet.
    $command = escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' budget';
    passthru($command, $status);
    if ($status !== 0) exit(1);
    printf("place pages ok: gate, routing, sitemap and a %d-query cold render.\n",
           TIT_PLACE_QUERY_BUDGET);
    exit(0);
}

finish('');
