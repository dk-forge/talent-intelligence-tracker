<?php
/**
 * Run the "Who's hiring" role pages (includes/roles.php): the gate, the
 * routing, the sitemap and the CTA, against real SQL.
 *
 * Same harness shape as render_place_pages.php (WordPress stubbed, $wpdb on
 * in-memory SQLite, the WHOLE plugin loaded through its bootstrap), because the
 * role gate IS the place gate: tit_place_meets_threshold(), called rather than
 * copied. What a URL below it answers is a status code across a change of
 * state, which reading the source cannot settle.
 *
 * Run: php tests/php/render_role_pages.php
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
// wp_date() is what tit_cite_line() reaches for to date a citation. Every
// other plugin-loading harness here already stubs it; this one did not, so
// 1.83.5 fataled on `undefined function wp_date()` in CI while the live site
// (where it is WordPress core) rendered the same line fine.
function wp_date($f, $t = null) { return gmdate($f, $t === null ? time() : $t); }
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
                funding_amount_usd INTEGER, funding_stage TEXT, money_basis TEXT,
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
if (!function_exists('tit_role_index')) {
    fwrite(STDERR, "roles.php is not loaded by the plugin bootstrap\n");
    exit(1);
}

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

/** $docs rows for one role in one city, over $employers employers and $sources. */
function seed_role($functions, $city, $country, $docs, $employers, array $sources, $prefix) {
    global $wpdb;
    for ($i = 0; $i < $docs; $i++) {
        $wpdb->insert_row(array(
            'functions'   => json_encode($functions),
            'city' => $city, 'country' => $country,
            'company'     => ucfirst($prefix) . ' Employer ' . ($i % $employers),
            'company_key' => $prefix . ' employer ' . ($i % $employers),
            'source_name' => $sources[$i % count($sources)],
            'source_url'  => 'https://example.test/' . $prefix . '/' . $i,
        ));
    }
}

// Engineering: thick in London (30 docs) and thin in Leeds (5). Role total 35.
seed_role(array('engineering', 'product'), 'London', 'GB', 30, 6, array('SEC EDGAR', 'Reuters'), 'lon');
seed_role(array('engineering'), 'Leeds', 'GB', 5, 3, array('SEC EDGAR'), 'leeds');
// Data & AI: 30 docs from ONE employer. Below the employer bar, so no page.
seed_role(array('data_ai'), 'Austin', 'US', 30, 1, array('SEC EDGAR', 'Reuters'), 'solo');
// "finance" must not be matched by a longer token that merely contains it.
seed_role(array('finance_ops_fake'), 'Austin', 'US', 30, 6, array('SEC EDGAR', 'Reuters'), 'fake');
// A withdrawn row counts towards nothing.
$wpdb->insert_row(array('functions' => '["sales"]', 'city' => 'London', 'country' => 'GB',
    'is_current' => 0, 'source_url' => 'https://example.test/retracted/1'));

$index = tit_role_index();
$roles = $index['roles'];
check(isset($roles['engineering']) && $roles['engineering']['documents'] === 35,
      'engineering counts 35 distinct documents across both cities: got '
      . ($roles['engineering']['documents'] ?? 'nothing'));
check(!empty($roles['engineering']['gated']), 'engineering clears the place gate');
check(isset($roles['data-and-ai']) && empty($roles['data-and-ai']['gated']),
      'one employer is not a market: Data & AI is below the bar');
check(!isset($roles['finance']), 'a longer token never matches a shorter one');
check(!isset($roles['sales']), 'a withdrawn row creates no role');
check(!empty($index['cities']['engineering']['london']['gated']),
      'engineering x London clears the gate on its own documents');
check(isset($index['cities']['engineering']['leeds'])
      && empty($index['cities']['engineering']['leeds']['gated']),
      'engineering x Leeds (5 documents) does not');

function role_route($role, $city = '') {
    $GLOBALS['tit_query_vars'] = array('tit_role' => $role, 'tit_role_city' => $city);
    tit_role_forget();
    return tit_role_route(tit_role_current());
}
check(role_route('engineering')['action'] === 'render', 'a gated role renders');
check(role_route('engineering', 'london')['action'] === 'render', 'a gated role x city renders');
$thin = role_route('engineering', 'leeds');
check(($thin['code'] ?? 0) === 302 && strpos($thin['url'], 'function=engineering') !== false
      && strpos($thin['url'], 'city=Leeds') !== false,
      'below the bar is a TEMPORARY redirect to the dashboard filtered to the same cell: got '
      . json_encode($thin));
check((role_route('data-and-ai')['code'] ?? 0) === 302, 'a thin role redirects too');
check(role_route('nonsense')['action'] === '404', 'an unknown role is a 404');
check(role_route('engineering', 'atlantis')['action'] === '404', 'an unknown city is a 404');
$caps = role_route('Engineering', 'London');
check(($caps['code'] ?? 0) === 301
      && $caps['url'] === 'https://example.test/blog/talent-intelligence-tracker/role/engineering/london/',
      'a non-canonical form 301s to the canonical URL: got ' . json_encode($caps));

function role_render($role, $city = '') {
    $GLOBALS['tit_query_vars'] = array('tit_role' => $role, 'tit_role_city' => $city);
    tit_role_forget();
    ob_start();
    tit_role_render(tit_role_current());
    return ob_get_clean();
}
$page = role_render('engineering', 'london');
check(strpos($page, 'Check your fit for Engineering jobs') !== false, 'the CTA names the role');
check(strpos($page, 'https://asktherecruiter.com/?utm_source=tit&amp;utm_medium=seo&amp;utm_campaign=whos_hiring') !== false,
      'the CTA carries the agreed UTM link');
check(strpos($page, 'aria-label="Breadcrumb"') !== false, 'the page carries the visible breadcrumb trail');
check(strpos($page, '<h1') !== false && strpos($page, 'London') !== false, 'the heading names the city');
check(strpos($page, 'https://example.test/lon/') !== false, 'recent updates link their source documents');
$role_page = role_render('engineering');
check(strpos($role_page, '/role/engineering/london/') !== false,
      'a role page links its gated city pages');
check(strpos($role_page, '/role/engineering/leeds/') === false,
      'and never a city page that would redirect');

$xml = tit_roles_sitemap_xml();
check(strpos($xml, '/role/engineering/</loc><lastmod>') !== false, 'gated role in the sitemap with lastmod');
check(strpos($xml, '/role/engineering/london/</loc><lastmod>') !== false, 'gated role x city in the sitemap');
check(strpos($xml, 'leeds') === false && strpos($xml, 'data-and-ai') === false,
      'nothing below the bar is in the sitemap');
check(preg_match_all('/<lastmod>/', $xml) === preg_match_all('/<loc>/', $xml),
      'every sitemap URL carries a lastmod');

if ($failures) {
    fwrite(STDERR, "role pages FAILED:\n  - " . implode("\n  - ", $failures) . "\n");
    exit(1);
}
echo "role pages ok: gate, routing, CTA and sitemap.\n";
