<?php
/*
 * EVERY EMPLOYER NAME IN THIS FILE IS PREFIXED "TEST FIXTURE" ON PURPOSE.
 * Same reason as render_dashboard.php: this renders the REAL sources page
 * against a synthetic corpus, and a test render indistinguishable from
 * production is a trap for a human and for the next session.
 */
/**
 * Render the sources page, and check the sentence that explains the archive.
 *
 * WHY THIS FILE EXISTS.
 *
 * The dashboard prints an "Archived" link on the records whose source document
 * has a saved copy. Today that is 72 of 12,970 cited documents, and a link that
 * sparse, shown without explanation, reads as a hole: 99% of the page apparently
 * missing something the other 1% has. It is not a hole. Most of what this
 * tracker cites is filings held by regulators and government registers, which
 * their own publishers keep, and a second copy of one of those preserves
 * nothing. The sources page has to say so, with counts.
 *
 * A sentence with numbers in it is exactly the kind of thing that is true the
 * day it is written. Three ways it goes wrong, and all three are silent:
 *
 *  - THE SPLIT DRIFTS. "Filings" versus "publishers who unpublish" is derived
 *    from the category each collector carries in data/sources.json, not typed
 *    here. A collector added tomorrow with no catalogue entry must count as
 *    perishable, because that direction overstates what needs preserving and
 *    never claims somebody else is keeping a document on our behalf. The
 *    alternative failure is the one the collector map already shipped: a
 *    hand-typed list with five of nine entries.
 *  - THE ARITHMETIC STOPS ADDING UP. Filings plus perishable is every cited
 *    document, or one of the two shares is wrong and the page is asserting a
 *    corpus it does not have.
 *  - THE PAGE STARTS CLAIMING AN ABSENCE. The archive ledger knows three states
 *    (archived, still pending, confirmed to have no copy) and only the first
 *    reaches WordPress. So the page may say what it holds and may not say the
 *    rest is unavailable, which is a distinction ops_status [2c] draws and this
 *    page must not flatten.
 *
 * And the figure MOVES: the archiver is working through the perishable tail, so
 * the share climbs on its own. The render is checked twice against two different
 * corpora, one sparse and one nearly complete, because a sentence that only
 * reads correctly at half a percent is a sentence that has to be rewritten later
 * by somebody who will not know it needed rewriting.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/render_sources.php
 */

define('ABSPATH', __DIR__);
$tit_plugin = __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/';
function plugin_dir_path($file) { return dirname($file) . '/'; }
function plugin_dir_url($file) { return 'https://example.test/plugin/'; }
define('MINUTE_IN_SECONDS', 60);
define('HOUR_IN_SECONDS', 3600);
define('DAY_IN_SECONDS', 86400);
define('ARRAY_A', 'ARRAY_A');

$GLOBALS['tit_query_vars'] = array('tit_sources' => 1);
$GLOBALS['tit_transients'] = array();
$GLOBALS['tit_enqueued'] = array();

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
class SourcesHarnessDb {
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
                funding_amount TEXT, funding_amount_usd INTEGER, funding_stage TEXT,
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
            'signal_id' => 's' . $n, 'revision' => 1, 'is_current' => 1,
            'headline' => 'TEST FIXTURE NOT REAL DATA: synthetic update ' . $n,
            'company' => 'TEST FIXTURE Employer ' . $n,
            'company_key' => 'employer ' . $n,
            'confidence' => 'verified',
            'source_url' => 'https://example.test/doc/' . $n,
            'source_name' => 'SEC EDGAR',
            'published_date' => gmdate('Y-m-d'),
            'captured_at' => gmdate('Y-m-d H:i:s', time() - 3600),
        ), $opts);
        $cols = implode(', ', array_keys($row));
        $marks = implode(', ', array_fill(0, count($row), '?'));
        $stmt = $this->pdo->prepare("INSERT INTO wp_tit_signals ({$cols}) VALUES ({$marks})");
        $stmt->execute(array_values($row));
    }
}

$GLOBALS['wpdb'] = new SourcesHarnessDb();
global $wpdb;

require $tit_plugin . 'talent-intelligence-tracker.php';

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

/*
 * THE CATALOGUE, in the shape the real one has: a filing system and a news
 * publisher, each carrying the category build_sources_json.py writes. The rows
 * a reader sees do not matter here; the collector-to-category join does, and it
 * is the only thing standing between this page and a typed list.
 */
$SOURCES = array(
    array('name' => 'TEST FIXTURE SEC EDGAR', 'status' => 'live',
          'category' => 'Regulatory filings', 'collector' => 'sec_edgar',
          'country' => 'US', 'signals' => array('Leadership change'), 'url' => 'https://example.test/sec'),
    array('name' => 'TEST FIXTURE GOV.UK pay gap', 'status' => 'live',
          'category' => 'Government filings', 'collector' => 'uk_paygap',
          'country' => 'GB', 'signals' => array('Pay gap'), 'url' => 'https://example.test/paygap'),
    array('name' => 'TEST FIXTURE national press', 'status' => 'live',
          'category' => 'News publishers', 'collector' => 'national_press',
          'country' => 'Worldwide', 'signals' => array('Hiring'), 'url' => 'https://example.test/press'),
    array('name' => 'TEST FIXTURE researched outlet', 'status' => 'candidate',
          'category' => 'News publishers', 'collector' => '',
          'country' => 'IE', 'signals' => array('Hiring'), 'url' => ''),
);

/** Seed a corpus and render, returning the markup. */
function render_with($filings, $perishable, $archived) {
    global $wpdb, $SOURCES;
    $wpdb->pdo->exec('DELETE FROM wp_tit_signals');
    $GLOBALS['tit_transients'] = array();

    for ($i = 0; $i < $filings; $i++) {
        // Two rows per document, because the ledger is keyed on the URL and not
        // on the row: thousands of SEC rows live behind a handful of index
        // pages, and a count of rows would report a corpus we do not have.
        $wpdb->insert_row(array('collector' => 'sec_edgar',
            'source_url' => 'https://example.test/filing/' . $i));
        $wpdb->insert_row(array('collector' => 'sec_edgar',
            'source_url' => 'https://example.test/filing/' . $i));
    }
    for ($i = 0; $i < $perishable; $i++) {
        $wpdb->insert_row(array('collector' => 'national_press', 'confidence' => 'reported',
            'source_url' => 'https://example.test/story/' . $i,
            'archive_url' => $i < $archived ? 'https://web.archive.test/story/' . $i : null));
    }
    // A collector with no catalogue entry at all. It must land on the
    // perishable side rather than being quietly counted as somebody else's
    // problem to preserve.
    $wpdb->insert_row(array('collector' => 'brand_new_collector', 'confidence' => 'reported',
        'source_url' => 'https://example.test/unknown/1'));

    ob_start();
    tit_sources_render($SOURCES);
    return ob_get_clean();
}

/** The prose, tags stripped and whitespace flattened, for reading assertions. */
function prose($html) {
    return preg_replace('/\s+/', ' ', html_entity_decode(strip_tags($html), ENT_QUOTES, 'UTF-8'));
}

/* --- the sparse corpus, which is today ----------------------------------- */

$html = render_with(1200, 60, 4);
$text = prose($html);

check(strpos($html, 'id="tit-sources"') !== false, 'the page renders at all');
check(strpos($text, 'We save a copy of the citations that can disappear') !== false,
      'the archive paragraph is on the page');

// 1,200 filings + 60 stories + 1 unclassified = 1,261 documents, of which 4
// carry a copy. Every one of those is a count, and the point of asserting the
// literal strings is that a typed number would pass a test written around it.
check(strpos($text, '1,261 documents cited') !== false,
      'the corpus size is counted, not written down: ' . substr($text, strpos($text, 'Of the'), 200));
check(strpos($text, '1,200 are filings') !== false,
      'and so is the share their own publishers keep');
check(strpos($text, 'other 61 come from') !== false,
      'and so is the perishable share, which includes the collector this page '
      . 'has no category for: an unknown source is one we assume nobody else is keeping');
check(strpos($text, "4 of all cited documents (0.3%)") !== false,
      'and the archived count carries its share to one decimal, matching ops_status [2c]');

// The arithmetic, checked rather than trusted. Two shares that do not sum to the
// corpus mean the page is describing a table it does not have.
preg_match('/Of the ([\d,]+) documents cited on this tracker, ([\d,]+) are filings/', $text, $m);
preg_match('/other ([\d,]+) come from/', $text, $m2);
$total = (int) str_replace(',', '', $m[1] ?? '0');
$filed = (int) str_replace(',', '', $m[2] ?? '0');
$peri  = (int) str_replace(',', '', $m2[1] ?? '0');
check($total > 0 && $filed + $peri === $total,
      "filings plus perishable has to be the whole corpus: {$filed} + {$peri} != {$total}");

// The distinction ops_status [2c] draws, which this page may not flatten. Only
// "we hold a copy" reaches WordPress; "we asked and there is none" does not, so
// the page says what it holds and stops.
check(strpos($text, 'record a copy we hold, never an absence we have checked for') !== false,
      'the page must not read a missing link as a document that has gone');

/* --- the same sentences, once the archiver has caught up ----------------- */

/*
 * The figure climbs. This is the whole reason the wording is asserted at two
 * points rather than one: a sentence built around "only a few" would still pass
 * every check above and would be wrong within a month, and nobody would be
 * looking at it when it turned.
 */
$later = prose(render_with(1200, 60, 55));
check(strpos($later, '55 of all cited documents (4.4%)') !== false,
      'the same sentence carries the larger figure: ' . substr($later, strpos($later, 'Of the'), 260));
check(strpos($later, 'We save a copy of the citations that can disappear') !== false
      && strpos($later, 'record a copy we hold, never an absence we have checked for') !== false,
      'and neither the claim nor the caveat needed rewriting to stay true');

/* --- nothing captured yet, which is where this actually starts ----------- */

/*
 * MEASURED, NOT HYPOTHETICAL. On 2026-07-30 the pipeline held 72 snapshots and
 * the live table held none of them: archive_url travels to WordPress as a later
 * enrichment rather than with the row, so there is a real window in which the
 * corpus is fully classified and the coverage figure is zero.
 *
 * The split still has to be said, because "most of what we cite needs no copy"
 * is true of this corpus whether or not a capture has landed. The coverage
 * figure must not be, because "0 of 12,970 (0.0%)" is a paragraph explaining a
 * link that is nowhere on the site.
 */
$none = prose(render_with(1200, 60, 0));
check(strpos($none, '1,200 are filings') !== false
      && strpos($none, 'other 61 come from') !== false,
      'with nothing captured the split is still on the page, because it is true either way');
check(strpos($none, '(0.0%)') === false && strpos($none, '0 of all cited documents') === false,
      'but the coverage figure is not, because there is no link on the site to explain');
check(strpos($none, 'None of them carries a saved copy on this site yet') !== false,
      'and the page says so plainly rather than going quiet');

/* --- an empty table says nothing rather than nonsense -------------------- */

global $wpdb;
$wpdb->pdo->exec('DELETE FROM wp_tit_signals');
$GLOBALS['tit_transients'] = array();
ob_start();
tit_sources_render($SOURCES);
$empty = ob_get_clean();
check(strpos($empty, 'We save a copy') === false,
      'a table with nothing in it prints no archive paragraph at all, rather than '
      . '"0 of 0 documents (0.0%)"');

/* --- what it costs ------------------------------------------------------- */

/*
 * One query, and it is cached. This page cost nothing before: it reads a JSON
 * file the pipeline writes and one option. COUNT(DISTINCT source_url) over the
 * whole table is not free on 15,711 rows, so a second render inside the TTL has
 * to touch the database not at all, for the same reason the dashboard's warm
 * render is asserted at zero.
 */
$wpdb->pdo->exec('DELETE FROM wp_tit_signals');
for ($i = 0; $i < 40; $i++) {
    $wpdb->insert_row(array('collector' => 'sec_edgar',
        'source_url' => 'https://example.test/cost/' . $i));
}
$GLOBALS['tit_transients'] = array();
$wpdb->reset_reads();
ob_start(); tit_sources_render($SOURCES); ob_end_clean();
$cold = $wpdb->reads;
check($cold === 1, "the sources page costs one query cold and cost {$cold}: "
                   . implode(' | ', $wpdb->log));

$wpdb->reset_reads();
ob_start(); tit_sources_render($SOURCES); ob_end_clean();
check($wpdb->reads === 0,
      'and none warm, or the count is paid on every request: ' . $wpdb->reads);

if ($failures) {
    fwrite(STDERR, "sources FAILED:\n  - " . implode("\n  - ", $failures) . "\n");
    exit(1);
}
printf("sources ok: the archive paragraph counts its own numbers, reads the same "
     . "at 0.3%% and 4.4%%, and costs %d query cold and none warm.\n", $cold);
exit(0);
