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

/*
 * THE ONE CLOCK. Every date in this file, in the fixture and in the assertions
 * and inside the render, is read from here and from nowhere else.
 *
 * IT IS HERE BECAUSE THIS HARNESS WENT RED FOR AN HOUR A DAY AND NOBODY COULD
 * SEE WHY. The fixture used to read the clock in two different resolutions: rows
 * were dated `gmdate('Y-m-d', time() - n days)`, whole UTC days, while
 * `captured_at` was `time() - 3600`, an instant. The first page is ordered by
 * COALESCE(published_date, DATE(captured_at)) DESC, and the row that carries the
 * whole "Location not stated" / "Date not stated" assertion is the ONLY row with
 * no published_date, so its sort key is DATE(captured_at) alone. Between 00:00
 * and 01:00 UTC that subtraction lands on YESTERDAY while ninety-odd sibling rows
 * are dated today, the row is pushed off a fifty-row first page, and an assertion
 * about a blank cell fails for a reason that has nothing to do with blank cells.
 * Runs before 23:25Z were green, runs after 00:11Z were red, and the same commit
 * was both.
 *
 * A test whose answer depends on what time it is run is a defect on its own
 * terms, worse than the one it is meant to catch, because it teaches whoever
 * reads CI to discount a red. So the clock is read ONCE, snapped to midday UTC,
 * and handed to the fixture, to the assertions, and to the WordPress stubs the
 * render itself reads its "today" through (`current_time()` is what shortcodes.php
 * asks). Midday is the point: every offset from it is whole days, so no
 * subtraction of hours can cross a date boundary, and 23:59 and 00:01 build the
 * same corpus, render the same page and reach the same verdict.
 *
 * It still tracks the real date rather than freezing on one, because two
 * assertions below are about the page moving WITH the calendar: the year rung has
 * to say the current year because it derived it, and the week-over-week
 * comparison has to appear once forty days of history exist. A frozen date would
 * let a typed year pass.
 *
 * ONE READ OF THE REAL CLOCK SURVIVES INSIDE THE RENDER, and it is named here
 * rather than left for somebody to find. tit_roo() asks PHP's own time() whether
 * the newest capture is less than fifteen minutes old, and draws a working mascot
 * if it is. Pinning that means changing the plugin, not this harness. Its whole
 * effect is two bytes of Roo's status line for the quarter hour after the
 * fixture's capture time each day; measured both ways, every assertion here
 * passes in both, and the byte budget has two hundred bytes of room. It is a
 * difference in the markup, never in the verdict.
 *
 * AND IT CAN BE MOVED, which is the other half of the lesson.
 *
 * The reason this defect survived four commits is that nobody could run the
 * suite at the hour it broke. A red that reproduces only between 00:00 and 01:00
 * UTC is a red that gets re-run, comes back green, and is filed as flaky.
 *
 *   TIT_FIXTURE_CLOCK='2026-12-31 23:59:59 UTC' php tests/php/render_dashboard.php
 *
 * runs the harness exactly as it would run at that instant: this is now the only
 * place any part of the fixture, the assertions or the render reads the wall
 * clock, so overriding it here is not a simulation of a late-night run, it IS
 * one. It is how the fix below was checked on both sides of a date boundary, and
 * anything that reintroduces a second clock read makes that stop being true.
 *
 * It announces itself on STDERR, because a knob that silently changes what a
 * test means is worse than no knob.
 */
$tit_clock = getenv('TIT_FIXTURE_CLOCK');
if ($tit_clock !== false && $tit_clock !== '') {
    $tit_at = strtotime($tit_clock);
    if ($tit_at === false) {
        fwrite(STDERR, "TIT_FIXTURE_CLOCK is not a date this can read: {$tit_clock}\n");
        exit(1);
    }
    fwrite(STDERR, 'clock overridden: running as if ' . gmdate('Y-m-d H:i:s', $tit_at) . " UTC\n");
} else {
    $tit_at = time();
}
define('TIT_FIXTURE_NOW', strtotime(gmdate('Y-m-d', $tit_at) . ' 12:00:00 UTC'));

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
function date_i18n($f, $t = null) { return gmdate($f, $t === null ? TIT_FIXTURE_NOW : $t); }
function wp_date($f, $t = null) { return gmdate($f, $t === null ? TIT_FIXTURE_NOW : $t); }
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
// The render's own "today". shortcodes.php builds every window on the dated
// glance panel and the matrix from this call, so pinning it here is what makes
// the PAGE agree with the fixture rather than merely agreeing with itself.
function current_time($t, $gmt = 0) {
    return $t === 'timestamp' ? TIT_FIXTURE_NOW : gmdate($t, TIT_FIXTURE_NOW);
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

function get_header() { echo '<!--header-->'; }
function get_footer() { echo '<!--footer-->'; }

/*
 * ENOUGH OF THE REST SURFACE TO CALL /aggregate, and nothing more.
 *
 * The place ribbon's whole contract is that its counts equal what the endpoint
 * a reader's browser calls reports for the same country and the same city. A
 * test that re-wrote the endpoint's GROUP BY by hand would prove the two agree
 * with each other's copies. So the endpoint itself is invoked, out of the same
 * api.php the render loaded, against the same fixture, and the answer is
 * written out for tests/test_place_ribbon_counts_updates_held.py to read.
 */
class WP_REST_Request {
    private $params;
    public function __construct(array $params = array()) { $this->params = $params; }
    public function get_param($key) { return $this->params[$key] ?? null; }
}
class WP_REST_Response {
    public $data;
    public function __construct($data) { $this->data = $data; }
    public function header($key, $value) {}
}
function rest_ensure_response($data) {
    return $data instanceof WP_REST_Response ? $data : new WP_REST_Response($data);
}

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
            'headline' => 'TEST FIXTURE NOT REAL DATA: synthetic update ' . $n . ', padded to about the length of a real headline',
            'talent_readthrough' => 'What this means for anyone hiring into that team right now.',
            'company' => 'TEST FIXTURE Employer ' . $n,
            'company_key' => 'employer ' . $n,
            'pillar' => 'company_development',
            'signal_direction' => 'hiring',
            'confidence' => 'verified',
            'source_url' => 'https://example.test/doc/' . $n,
            'source_name' => 'SEC EDGAR',
            'published_date' => gmdate('Y-m-d', TIT_FIXTURE_NOW - ($n % 40) * DAY_IN_SECONDS),
            // An hour before the fixture's midday, so DATE(captured_at) is the
            // SAME UTC day as a row dated "today" however late the suite runs.
            // This one subtraction, taken from the real clock, is what put the
            // placeless row on yesterday between 00:00 and 01:00 UTC.
            'captured_at' => gmdate('Y-m-d H:i:s', TIT_FIXTURE_NOW - 3600),
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
 *
 * RAISED 177,000 -> 178,000 on 2026-08-02 for the audience-spec pass, which
 * measured 177,466 against this fixture: four chart-group question headings,
 * the two shared caveat disclosures (About The Money Figures, About Job Board
 * Readings), the matrix lede, the trend's tap-to-filter data attributes and
 * hint sentence, and the two zone wrappers. Partly paid for by the five
 * copies of the currency caveat and the per-card job-board boilerplate this
 * same pass deleted. Headroom is ~500 bytes: the next addition raises the
 * budget again, in writing.
 *
 * NOT RAISED on 2026-08-03, and 2,348 bytes of the room the interim passes had
 * opened is now spent: 175,187 -> 177,535. It bought the owner's two asks: the
 * freshness panel's this-year/all-time pairing (four small "all time" lines
 * plus the year in each label, ~400 bytes) and the Year / Quarter / Month
 * period selects beside the date boxes (~1,900 bytes, most of it the derived
 * year options and the three labelled parts). Headroom is 465 bytes, which is
 * not room for anything: the next addition raises this number and writes down
 * why.
 */
/*
 * RAISED 178,000 -> 180,000 on 2026-08-03 for the owner-approved batch of
 * four, which measured 178,134 against this fixture: the watchlist chip and
 * its (i) paragraph, the hidden card/table view toggle and its empty table
 * container, and three more links in the export strip (HubSpot CSV,
 * Salesforce CSV, RSS) with two sentences added to the export note. The
 * table itself costs nothing here: it is built client-side from rows already
 * fetched, which is why its container ships empty.
 */
/*
 * RAISED 180,000 -> 181,000 on 2026-08-03 for the this-year/all-time stat
 * pairing (eight figures where four were) and the Year/Quarter/Month selects
 * inside the Date Range panel. Measured 180,482; the 482 overage is entirely
 * those two owner-requested controls, itemised here per the convention. This
 * page is read on phones; the next raise needs its own itemised bill.
 */
/*
 * NOT RAISED on 2026-08-03 for the four-defect UX pass, and the spend is written
 * down anyway because the convention is that every session says what it cost:
 * 180,482 -> 180,678, so 196 bytes, and the headroom is now 322.
 *
 *   ~130  THE PLACE CARD'S SUBTITLE gained "Every bar is a count of updates we
 *         hold, never a count of jobs", after the card shipped titled "Where the
 *         Jobs Are" over a ranking of record counts.
 *   ~110  THE KIND CARD'S SUBTITLE, rewritten from "Ranked by how much of it we
 *         are seeing" to a sentence that names the unit.
 *   ~-44  net of the six title changes, which are mostly the same length as what
 *         they replaced.
 *
 * The place caveat MOVED out of the (i) panel and did not change size, so the
 * one fix a reader is most likely to notice cost nothing.
 *
 * THIS FIXTURE STILL CANNOT PRICE THE TREND PANEL, for the reason the 2026-07-30
 * entry below already names: its rows sit within days of the render date, so the
 * continuity gate refuses every signal and the panel collapses to one sentence.
 * The same pass lengthened the drawn panel's basis sentence by roughly 120 bytes,
 * which is not in the 196 above and is not measured anywhere. Budget ~5,400 for
 * that panel in production rather than the ~5,300 recorded below.
 */
/*
 * RAISED 181,000 -> 181,600 on 2026-08-03, for the watchlist explaining itself.
 * Measured on this fixture: 180,678 -> 181,146, so 468 bytes, and the headroom
 * afterwards is 454.
 *
 * THE DEFECT it buys out: the owner asked three times what the star does and
 * could not tell from the page. The answer existed, in a comment in
 * dashboard.js and in #tit-help-watch inside <details id="tit-help">, which
 * ships CLOSED. Both are places a first-time reader never reaches, so the page
 * had no answer on it. These bytes are the answer, printed.
 *
 *   ~367  #tit-watch-hint, one sentence in the controls band beside the chip it
 *         explains: what the star does, where the stars live, what turning the
 *         Watchlist on then does. It carries the empty #tit-watch-short span,
 *         which applyWatchFilter() fills only when the watchlist is on and some
 *         starred employer has nothing in the loaded window. Deliberately NOT
 *         inside the (i) panel and NOT passed as note_html, for the reason the
 *         2026-08-03 entry below records about the place caveat.
 *
 *    ~71  #tit-watch-toast, the empty aria-live region the star confirmation is
 *         written into. It ships empty and permanent because a live region
 *         created at the moment of the message is one several screen readers
 *         never announce, and :empty gives it no padding, border or background,
 *         so an unstarred page pays these 71 bytes and no pixels.
 *
 *    ~30  indentation and line breaks around the two elements.
 *
 * Nothing else on the page moved. The star's own tooltip and accessible name
 * are written by dashboard.js onto buttons the server never renders, so the
 * plainer wording cost this budget nothing at all.
 */
/*
 * RAISED 181,600 -> 184,600 on 2026-08-05, for the market trend replacing the
 * collection-rate chart, and the direction card folding into it. Measured on
 * this fixture: 181,146 -> 184,118, so a net 2,972, and the bill splits:
 *
 *   ~3,600  THE MARKET CHART, drawn (this fixture's dated rows reach six of
 *           its twelve weeks, so the share variant renders): the stacked-bar
 *           SVG whose every segment carries a <title> naming its week, its
 *           direction and its count, the axis, the legend, and the visible
 *           caveat sentence that names the panel and the variant. The titles
 *           are most of it and they are the reason a reader on any pointer
 *           gets the numbers without a script.
 *
 *   ~1,700  net of the two cards it replaced: the collection-rate card cost
 *           about 700 here (its rows sit within days of the render date, so
 *           it rendered its collapsed one-sentence form; in production it
 *           drew, and budget ~5,400 there, so the LIVE page gets lighter even
 *           though this fixture gets heavier), and the direction ranking's
 *           four rows about 1,000.
 *
 *   ~1,070  the section comments' share and the market card's (i) panel.
 *
 * Production note, honestly: live, this swap REMOVES more than it adds (the
 * drawn collection chart was ~5,400 and leaves entirely), so the raise here is
 * a property of this fixture pricing the drawn market chart against the
 * collapsed old one. Headroom ~480 bytes: the next addition raises this
 * number and writes down why.
 */
/*
 * RAISED 184,600 -> 185,600 on 2026-08-17, AND MOVED ONTO A PINNED CLOCK, which
 * is the more important half. Read both halves before touching this number.
 *
 * FIRST, WHAT THE NUMBER WAS MEASURING. It was measuring the calendar. This
 * fixture's rows are dated relative to TIT_FIXTURE_NOW, which tracks the real
 * date, and the market chart buckets forty days of that history into a sliding
 * twelve-week window. Which WEEKDAY the harness runs on decides whether those
 * forty days land across six week buckets or seven, and a drawn week is a
 * stacked bar whose every segment carries a <title>. Measured at 1.83.2 over
 * seventy consecutive dates:
 *
 *   Mon 185,082-185,151   Tue 185,055-185,147   Wed 185,075-185,162
 *   Thu 184,850-184,939   Fri 184,384-184,466   Sat 184,384-184,464
 *   Sun 184,380-184,460
 *
 * A 782-byte swing on a ceiling whose stated headroom was 480. So the assertion
 * failed Monday to Thursday and passed Friday to Sunday, on identical code.
 * That is exactly the "re-run it and it goes green, file it as flaky" defect the clock
 * comment at the top of this file was written about, arriving again through the
 * one measurement the clock fix did not cover.
 *
 * It is why nobody saw this coming: the ceiling was first crossed at 1.77.1
 * (436549e, 184,767 on a Wednesday basis) and EVERY commit since has been over
 * it. Each landed on a day the render happened to be light, and CI agreed.
 *
 * So the byte budget now runs in the budget subprocess with TIT_FIXTURE_CLOCK
 * pinned to TIT_DASH_BYTE_CLOCK. The number below is a constant of the code
 * rather than of the day, and a diff that does not touch the markup cannot move
 * it. The main process keeps the real clock, because the assertions there ARE
 * about the page moving with the calendar.
 *
 * SECOND, THE RAISE, on that pinned basis and therefore like for like with the
 * 184,118 the 2026-08-05 entry above measured. 1.72.0 rendered 184,167; 1.83.2
 * renders 185,150. That is 983 bytes, spent by six commits and never once
 * written down, and every byte of it is shipped copy:
 *
 *   ~348  1.74.6, the place ribbon's basis sentence: these counts are updates
 *         we hold, not a ranking of the market, and they count the routine
 *         filings the table sets aside.
 *   ~176  1.77.1, "a row is an entry": the board's definition line, which
 *         says what one entry is and which row counts dollars.
 *   ~176  1.81.1, the city ribbon carrying city totals and its own label.
 *   ~167  1.82.2, the email-digest call to action in the hero.
 *    ~47  1.80.0, the ribbon counting the updates it says it holds.
 *    ~69  net of 1.73.0 / 1.74.1 / the loading states / the mobile pass.
 *
 * NOT ONE OF THOSE IS FAT. They are five sentences and one link that a reader
 * asked for, four of them answering "what does this number actually count",
 * which is the question this whole tracker is built to answer honestly. The
 * alternative to raising the ceiling is deleting published copy to satisfy a
 * test, and that is the wrong way round: the budget exists to make the cost of
 * a card VISIBLE, not to make the page silent.
 *
 * THE PRECEDENT THIS HAS TO ANSWER is 1.74.6, where the same ceiling was 79
 * bytes short and was NOT raised (TECHLOG): the paragraph was printed on one
 * source line, two aria-labels that had grown were put back, and the page
 * landed at 184,579. That was the right call and it does not scale to 550
 * bytes. There is no 550 bytes of wrapping and grown labels left to reclaim,
 * and what sits behind them is the copy itself. Squeezing the markup was the
 * cheaper answer once; treating it as the answer every time is how a page loses
 * the sentences that make its numbers mean anything.
 *
 * 185,600 is the measured 185,150 plus 450 of headroom, and the headroom is
 * real now rather than being eaten by whatever day it is. The next addition
 * raises this number and writes down why.
 */
/*
 * RAISED 185,600 -> 186,350 on 2026-08-21, for 420 measured bytes, on the same
 * pinned clock as the entry above and therefore like for like with its 185,150.
 * This render is 185,879.
 *
 * Both halves bought the SAME defect, which is why they are one entry:
 *
 *   ~280  the Kind Of Money control. An empty hidden <label> and <select>, one
 *         more of the seven that were already there, plus its source comment's
 *         indentation. Its options are not in this number: like every other
 *         facet control it is filled from /facets in the browser and it hides
 *         itself while the column is empty.
 *
 *   ~140  35 bytes x 4 cells of the at-a-glance matrix's Total Raised row,
 *         whose data-filter went from `funding=1` to
 *         `funding=1&money_basis=company_raise`.
 *
 * THE 140 IS THE POINT AND THE 280 IS WHAT MAKES IT TRUE. That figure sums
 * company raises only (tit_money_where(), shipped 1.85.0) while its link sent
 * the reader to the wider funding view, so clicking a number landed them on
 * rows that included divestiture prices, fund closes, outbound spends, state
 * subsidies and pledges -- rows that demonstrably do not add up to the number
 * they clicked. The link alone would not have fixed it: dashboard.js forwards
 * only the parameters it has a control for, so the new param would have been
 * dropped in the browser and the link would have looked precise while behaving
 * exactly as it did before. The control is the half that makes the link real.
 *
 * The alternative was leaving a published figure whose own link contradicts it,
 * which is the one thing this tracker cannot carry. 750 of headroom rather than
 * the usual 450: the money control's own options arrive from /facets and never
 * touch this render, so the fixture cannot price the copy this control will
 * grow, and the next addition still raises this number and writes down why.
 */
/*
 * RAISED 186,350 -> 189,350 on 2026-08-24, for 2,543 measured bytes, on the same
 * pinned clock and therefore like for like with the 185,879 above. This render
 * is 188,893.
 *
 * A WHOLE NEW PANEL, not a sentence: the US job-postings macro backdrop from
 * Indeed Hiring Lab (1.87.0, includes/indeed_index.php), rendered once after the
 * feed. It is the largest single addition this budget has priced, and it earns
 * it -- it is a card, not a line -- so here is every byte:
 *
 *   ~1,050  the CC BY 4.0 methodology / attribution note. The licence REQUIRES
 *           the credit and the "computed by us" statement, and the panel's whole
 *           reason to exist is that a reader can tell this external number from
 *           the tracker's own. That sentence is the feature, not packaging.
 *   ~700   four .tit-stat tiles: the index, its change vs a month, the AI share
 *           and its change, each with its label and its own "as of" date so
 *           staleness is visible.
 *   ~600   the section head plus the "external context, not counted in the
 *           numbers above" sub-line -- the disclaimer that keeps this from being
 *           read as one of our figures.
 *   ~450   the source line (linked, CC BY 4.0, both series' dates) and the
 *           trimmed sparkline. The sparkline is DELIBERATELY light: it downsamples
 *           ~180 daily readings to at most 52 integer-rounded points precisely
 *           because of this budget (see tit_indeed_sparkline). Undownsampled it
 *           was ~1,500 bytes heavier and this raise would have been that much
 *           larger.
 *
 * The alternative to raising is deleting the attribution or the disclaimer, and
 * both are load-bearing: one is a licence obligation, the other is the promise
 * that this context is never confused with the tracker's own counts. 457 of
 * headroom on the usual basis. The next addition raises this number and writes
 * down why.
 */
const TIT_DASH_BYTE_BUDGET = 189350;

/*
 * THE INSTANT THE BYTE BUDGET IS MEASURED AT, and it is a Wednesday on purpose.
 *
 * Any fixed date would make the measurement deterministic, which is the whole
 * point. This one is the HEAVIEST alignment: Monday to Wednesday put the
 * fixture's forty days of history across the most week buckets the market chart
 * can draw, so the budget bounds the worst page rather than a lucky one. A pin
 * chosen for a low number would be gaming the ceiling, and would leave the
 * heaviest render unbounded.
 *
 * Changing this date changes what every byte figure in the comments above
 * means. Re-measure and re-derive the ceiling if you ever do.
 */
const TIT_DASH_BYTE_CLOCK = '2026-08-19';

/*
 * RAISED 174,000 -> 177,000 on 2026-08-02, for the archive pending state and
 * the strip disambiguators. Measured on this fixture: 175,749 against the old
 * 174,000 ceiling, and the bill splits three ways:
 *
 *   ~1,100  THE PENDING SENTENCE on every page-one publisher card without a
 *           snapshot ("No archive snapshot yet. We re-check weekly; next check
 *           by <date>.", ~110 bytes x the in-scope unarchived rows the default
 *           view holds). This is the owner's ask: the absence of an archive
 *           link now says what happens next instead of implying nothing does.
 *
 *   ~600    THREE MORE FIXTURE CARDS (the Registry pair rows), which exist so
 *           the out-of-scope state — no link AND no promise — is asserted
 *           rather than assumed. Fixture cost, not page cost.
 *
 *   ~300    data-archive-note on the root (the server-composed sentence the JS
 *           reprints, so the repaint cannot derive a second date), the week
 *           rung's derived "(Jul 27-Aug 2)" span, and the largest raise's own
 *           country inside its parens.
 *
 * Headroom ~1,250 bytes. Same rule as every raise below: the next addition
 * raises this number and writes down why.
 */

/*
 * RAISED 169,000 -> 174,000 on 2026-07-31, when the chart grid went to nine
 * cards and every card's prose moved behind an (i). Measured on this fixture
 * with the prefix stripped, 163,511 -> 173,637, and here is the itemised bill.
 *
 *   4,394  WHICH INDUSTRIES ARE MOVING, a sixth ranking card. Counted by
 *          updates rather than by dollars, which is the thing the money card
 *          of nearly the same name cannot show: that one only sees rows
 *          carrying a figure, so a sector hiring hard and raising nothing is
 *          invisible in it. SEVEN rows on this fixture and eighteen in
 *          production, so budget roughly 2,200 more there.
 *
 *   3,459  THE (i) BUTTON, nine times, carrying aria-controls AND
 *          aria-describedby. This is the honest cost of not deleting the
 *          caveats: the panels ship OPEN and this button ships hidden, so a
 *          reader whose script never ran gets the prose rather than a control
 *          that opens nothing, and that means both are in the markup. A title=
 *          attribute would have been free and would have been reachable by
 *          neither a keyboard nor a screen reader.
 *
 *   2,562  HOW SOLID THE EVIDENCE IS, the ninth card, and the only one that
 *          counts how we know rather than what happened. Two bars, because
 *          nothing here is stored as rumored and a bucket drawn at zero is a
 *          category invented to fill a box. It costs NO query: three CASE
 *          expressions joined the head scan that was already counting the
 *          verified rows for the hero.
 *
 *   1,385  THE TREND, as a card in the grid rather than a panel above it. It
 *          gained a card wrapper and the same four controls every other card
 *          has; it lost its own heading, its own border and its five SVG axis
 *          labels, whose values are now HTML beside the plot. READ THIS ONE
 *          WITH THE SAME CAVEAT THE 2026-07-30 ENTRY BELOW ATTACHES: this
 *          fixture's rows all sit within days of the render date, so the
 *          chart's continuity gate refuses every signal and 1,385 is the price
 *          of the card SAYING NOTHING, not the price of the chart.
 *
 *     339  net of everything else: nine note panels, which are mostly the
 *          subtitles MOVED out of the card heads rather than added, less about
 *          2,600 saved by building tit_chart_head() as a string. Four indented
 *          buttons printed nine times was two kilobytes of leading whitespace
 *          no reader ever sees.
 *
 * The headroom is 363 bytes, which is not room for anything. Same rule as every
 * raise below: the next addition raises this number and writes down why.
 */

/*
 * RAISED 168,000 -> 169,000 on 2026-07-30 for the trend chart, and the bill has
 * a hole in it that this note exists to name rather than to paper over.
 *
 *   738  measured here, 167,713 -> 168,451.
 *
 * READ THAT NUMBER WITH THE CAVEAT ATTACHED. This fixture's rows all sit within
 * a few days of the render date, so every signal fails the chart's continuity
 * gate and the panel collapses to its one-sentence "not drawn for this view yet"
 * form. 738 bytes is the price of the panel SAYING NOTHING. It is not the price
 * of the chart.
 *
 * The drawn case was measured separately, by calling tit_signal_trend_html()
 * with two ninety-point series and three refusals, which is what the live
 * default view produces: 5,321 bytes. Roughly a third of that is the two SVG
 * paths, a third the prose and the legend, and a third indentation. So a session
 * changing this page should budget about 5,300 for this panel in production and
 * about 1,300 more for each further line the data earns.
 *
 * THE GAP IS REAL AND IT IS THE FIXTURE'S. A harness whose data cannot reach a
 * branch cannot cost it, and the honest fix is a fixture seeded across a
 * ninety-day window rather than a number typed here as though it had been
 * measured. That is a bigger change to this file than a chart deserved on the
 * day it shipped, and it is written down as the next thing rather than done
 * quietly or forgotten.
 *
 * WHY THE PANEL IS WORTH ITS BYTES EITHER WAY. Every other figure on this page
 * is a snapshot. The matrix hands a reader four columns and leaves them to do
 * arithmetic across the four to guess at a direction, which is the question a
 * talent tracker is opened with. Nothing else on the page can show the shape
 * between those columns.
 *
 * WHY IT IS NOT MORE. No library and no script: the SVG is drawn in PHP and
 * arrives in the initial HTML, so the whole feature costs its markup and not one
 * request. A charting dependency would have cost many times this over the wire
 * before it drew anything, and this page's sibling had a render-blocking CDN
 * stylesheet taken off it last week.
 *
 * The headroom is 549 bytes, which is not room for anything. Same rule as every
 * raise above: the next addition raises this number and writes down why.
 */

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
            // A figure only reaches a total when something has judged it a
            // company raise. The pipeline writes this on every stored figure,
            // so a fixture that omitted it would be a corpus the site cannot
            // add up -- and it would pass by accident if the sums ever stopped
            // asking.
            $row['money_basis'] = 'company_raise';
        }
        $wpdb->insert_row($row);
    }
}

// A row placed only by its employer's head office, so the HQ badge renders.
$wpdb->insert_row(array('hq_country' => 'US', 'hq_city' => 'Seattle',
    'industry' => 'technology', 'company' => 'TEST FIXTURE HQ Only Employer',
    'company_key' => 'hq only employer', 'published_date' => gmdate('Y-m-d', TIT_FIXTURE_NOW),
    'source_url' => 'https://example.test/hqonly/1'));
// A row with no place at all, and no date: both print their own words. It is the
// ONLY row in the corpus with a null published_date, so it is the only one whose
// place on the first page is decided by DATE(captured_at) — see TIT_FIXTURE_NOW.
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
        'confidence' => 'reported', 'published_date' => gmdate('Y-m-d', TIT_FIXTURE_NOW),
        'source_url' => 'https://example.test/archived/' . $i,
        'archive_url' => 'https://web.archive.test/save/' . $i,
    ));
    $wpdb->insert_row(array(
        'country' => 'US', 'city' => 'Austin', 'industry' => 'technology',
        'company' => 'TEST FIXTURE Unarchived Employer ' . $i,
        'company_key' => 'unarchived employer ' . $i,
        'collector' => 'national_press', 'source_name' => 'TEST FIXTURE Unarchived Outlet',
        'confidence' => 'reported', 'published_date' => gmdate('Y-m-d', TIT_FIXTURE_NOW),
        'source_url' => 'https://example.test/unarchived/' . $i,
    ));
    // The THIRD archive state: a collector the archive schedule deliberately
    // does not cover (EDGAR keeps its own filings indefinitely). Such a row
    // must render neither the link nor the pending promise — a "we re-check
    // weekly" on a document nothing will ever re-check is a false sentence.
    $wpdb->insert_row(array(
        'country' => 'US', 'city' => 'Austin', 'industry' => 'technology',
        'company' => 'TEST FIXTURE Registry Employer ' . $i,
        'company_key' => 'registry employer ' . $i,
        'collector' => 'sec_edgar', 'source_name' => 'TEST FIXTURE Registry Outlet',
        'confidence' => 'verified', 'published_date' => gmdate('Y-m-d', TIT_FIXTURE_NOW),
        'source_url' => 'https://example.test/registry/' . $i,
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
               'tit-f-funding_stage', 'tit-f-deal_type', 'tit-f-money_basis',
               'tit-f-site_event') as $id) {
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
check(preg_match('/id="tit-bar-toggle"[^>]*aria-expanded="true"/', $html) === 1,
      'with aria-expanded set at construction rather than on first use: a '
      . 'trigger that reports no state until somebody has already pressed it '
      . 'tells a screen reader nothing at the moment it matters. It ships '
      . '"true" because the panel ships OPEN -- collapsing is a class the '
      . 'reader adds, so the served markup and the no-JS render agree');

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
 * A PILL THAT CONTRADICTS ITS OWN CAPTION IS WORSE THAN NO PILL.
 *
 * Clicking one writes city=<name>, which api.php resolves with the clause
 * below. Two defects lived in the one query that builds this strip, and each
 * showed up here as a pill whose count is not the count of the thing it names:
 *
 *   grouping by bare `city`       -- London printed 18 and returned 1,338
 *   a non-aggregated country      -- the flag was whichever row came first
 *
 * A THIRD FIX WAS REVERSED, and this is where that is written down. The strip
 * was moved onto the notable clause so its number matched the table below it.
 * The caption over it says "Cities by Updates Held", and under that clause it
 * could not see a routine officer filing, which is an update we hold. So the
 * count is every current row again, which is also what /aggregate's by_city
 * returns, and the assertion below is against that. What the strip must NOT go
 * back to is the OTHER two defects, which are what the London and Ottawa checks
 * pin. The reader-facing consequence, that a pill can now read higher than the
 * rows a click returns while the detail control sits on notable, is stated in
 * the basis line above the strip.
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
// TWO CLAUSES, and which one a figure is checked against is the whole point of
// this pass. $base_where is the DEFAULT VIEW, which the hero, the board and the
// rows all print. $held_where is EVERY CURRENT ROW, which is what a surface
// captioned "Updates Held" counts. Later assertions in this file use both.
$base_where = 'is_current = 1 AND ' . tit_notable_where();
$held_where = 'is_current = 1';

preg_match_all(
    '/data-city="([^"]*)".*?class="tit-cbtn-n">([\d,]+)</s',
    $html, $pills, PREG_SET_ORDER
);
check(count($pills) > 0, 'the cities strip has to carry pills to check');
foreach ($pills as $pill) {
    $name = html_entity_decode($pill[1], ENT_QUOTES, 'UTF-8');
    $printed = (int) str_replace(',', '', $pill[2]);
    $actual = (int) $wpdb->get_var(
        "SELECT COUNT(*) FROM wp_tit_signals WHERE {$held_where} AND "
        . $wpdb->prepare($city_clause, array_fill(0, $city_args, $name))
    );
    check($printed === $actual,
          "the {$name} pill prints " . number_format($printed) . ' and we hold '
          . number_format($actual)
          . ' updates for it. The strip is captioned by updates held, so it has '
          . 'to be counted over every current row.');
}

$city_names = array_map(fn($p) => html_entity_decode($p[1], ENT_QUOTES, 'UTF-8'), $pills);
check(in_array('Edinburgh', $city_names, true),
      'Edinburgh is placed only by its employers\' head offices and has 120 rows '
      . 'here, so it belongs in a strip that carries cities with fewer: '
      . implode(', ', $city_names));
check(in_array('San Francisco', $city_names, true),
      'San Francisco holds 1,800 routine officer filings and nothing else, and '
      . 'they are updates we hold, so a strip captioned by updates held has to '
      . 'carry it: ' . implode(', ', $city_names));

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
/*
 * THE FRESHNESS PANEL PAIRS EVERY FIGURE: this year big, all time small. The
 * whole-record totals alone read as this year's numbers (the owner misread
 * them himself), so each stat leads with the current-year slice and keeps the
 * entire record beneath it. The year in the labels is DERIVED from the same
 * clock the fixture runs on, so this assertion says 2027 in January without an
 * edit, exactly like the year rung above.
 */
$fx_year = gmdate('Y', TIT_FIXTURE_NOW);
check(strpos($html, 'class="tit-fstat tit-fstat-money"') !== false
      && strpos($html, '<span>raised in ' . $fx_year . '</span></a>') !== false,
      'and the money total sits in the freshness panel with the other figures, '
      . 'still a link so the sum never travels without its caveat, led by the '
      . 'current year');
check(strpos($html, 'updates in ' . $fx_year . '</span>') !== false
      && strpos($html, 'employers in ' . $fx_year . '</span>') !== false
      && strpos($html, 'official filings in ' . $fx_year . '</span>') !== false,
      'each freshness stat leads with the current year, derived from the clock');
check(substr_count($html, ' all time</span>') >= 4,
      'and every one of the four keeps its all-time figure beneath the pair');
$ytd_notable = (int) $wpdb->get_var(
    "SELECT COUNT(*) FROM wp_tit_signals WHERE is_current = 1 AND " . tit_notable_where()
    . " AND COALESCE(published_date, DATE(captured_at)) >= '{$fx_year}-01-01'");
check(strpos($html, '<b>' . number_format_i18n($ytd_notable) . '</b>'
             . '<span>updates in ' . $fx_year . '</span>') !== false,
      'the big number is the current-year slice of the same clause the table '
      . 'uses (' . number_format_i18n($ytd_notable) . '), never a typed figure');

/*
 * THE PERIOD SHORTHAND. Year, quarter and month selects that write into the
 * since/until inputs. The year list is DERIVED from the data's own bounds:
 * this fixture's rows span two calendar years at most (seeded relative to the
 * clock), so the current year must be an option and a year we hold nothing in
 * must not be.
 */
foreach (array('tit-f-yearsel', 'tit-f-quartersel', 'tit-f-monthsel') as $id) {
    check(strpos($html, 'id="' . $id . '"') !== false,
          "the {$id} period select has to exist beside the date boxes");
}
check(strpos($html, '<option value="' . $fx_year . '">') !== false,
      'the year select offers the year the data actually holds');
check(strpos($html, '<option value="' . ($fx_year + 1) . '">') === false,
      'and never a year the data does not (' . ($fx_year + 1) . ')');

/* --- the results list ---------------------------------------------------- */

/*
 * These are CARDS now, not table rows, to the contract in docs/card-contract.json
 * that this repo shares byte-for-byte with the sibling AI Layoff Tracker. The id
 * is unchanged (`tit-rows`) because the JavaScript replaces the same element it
 * always did; what changed is that each result is an <li class="tit-card">.
 * tests/test_card_contract.py checks the card against the contract; this file
 * goes on checking the things only a real render can show — the right number of
 * them, the right rows, nothing blank, and the archived link telling the truth.
 */
$tbody = substr($html, strpos($html, '<ul class="tit-cards" id="tit-rows">'));
$tbody = substr($tbody, 0, strpos($tbody, '</ul>'));
check(substr_count($tbody, '<li class="tit-card">') === TIT_DASH_ROWS,
      'the first page is ' . TIT_DASH_ROWS . ' server-rendered cards and was '
      . substr_count($tbody, '<li class="tit-card">'));
check(strpos($tbody, 'materiality') === false && strpos($tbody, 'routine') === false,
      'and none of them is a routine officer filing, because the default view sets those aside');
check(strpos($html, '>HQ<') !== false,
      'a row placed only by its employer\'s head office says so');
/*
 * A ROW WITH NOTHING TO SAY HAS TO SAY SO, and the check is in two halves
 * because for four days it failed on the wrong one.
 *
 * The property is the second half: no cell on this table is ever blank. The
 * first half is the fixture keeping its promise — exactly one row in the corpus
 * has neither a place nor a date, and it only demonstrates anything while it is
 * among the fifty rows the server renders. When it slid off page one the suite
 * reported "a row with no place or no date says that too" as broken, which sent
 * a reader to the renderer, where nothing was wrong. So the precondition gets
 * its own line and its own message, and it names the fix.
 */
check(strpos($tbody, 'TEST FIXTURE Placeless Employer') !== false,
      'the placeless row has to be ON the first page for the next check to mean '
      . 'anything. It is not, so this is a FIXTURE problem and not a rendering '
      . 'one: the row sorts by DATE(captured_at) because it has no '
      . 'published_date, and something above it now outranks it. Give it a later '
      . 'row_id by moving its insert further down, or thin out the rows dated '
      . 'TIT_FIXTURE_NOW — do not relax the check below.');
check(strpos($tbody, 'Location not stated') !== false
      && strpos($tbody, 'Date not stated') !== false,
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
preg_match_all('/<li class="tit-card">.*?<\/li>/s', $tbody, $tr_matches);
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

/*
 * THE PENDING STATE SAYS WHEN, AND ONLY WHERE THE PROMISE IS REAL.
 *
 * The owner's ask (2026-08-02): a row without a snapshot must not just be
 * silent — it says "No archive snapshot yet. We re-check weekly; next check by
 * <date>", with the cadence and the date DERIVED from data/archive_promise.json
 * (itself generated from the real workflow schedule; see
 * build_archive_promise.py). Three states, all asserted:
 *   archived      the link, and no pending sentence beside it;
 *   in scope      the sentence, with the exact derived date — a typed date here
 *                 is the corrections-page "$124.0bn" mistake on every card;
 *   out of scope  NOTHING. sec_edgar is not in the schedule's collector list,
 *                 and promising a re-check nothing will make is a false
 *                 sentence on a page whose one claim is that it does not lie.
 */
$promise = json_decode((string) file_get_contents(TIT_PATH . 'data/archive_promise.json'), true);
check(is_array($promise) && (int) $promise['recheck_days'] > 0,
      'data/archive_promise.json has to ship with the plugin; the pending state '
      . 'renders from it (run build_archive_promise.py)');
$expected_note = 'No archive snapshot yet. We re-check '
    . ((int) $promise['recheck_days'] === 7 ? 'weekly' : 'every ' . (int) $promise['recheck_days'] . ' days')
    . '; next check by '
    . gmdate('M j', strtotime(gmdate('Y-m-d', TIT_FIXTURE_NOW) . ' 00:00:00 UTC')
             + (int) $promise['recheck_days'] * DAY_IN_SECONDS)
    . '.';
$rows_seen['pending'] = 0;
$rows_seen['registry'] = 0;
foreach ($tr_matches[0] as $tr) {
    $has_wait = strpos($tr, 'class="tit-archive-wait"') !== false;
    if (strpos($tr, 'TEST FIXTURE Unarchived Outlet') !== false) {
        $rows_seen['pending']++;
        check($has_wait && strpos($tr, esc_html($expected_note)) !== false,
              'a publisher-sourced row with no snapshot has to say the pending '
              . 'sentence with the DERIVED next-check date: ' . $expected_note);
    } elseif (strpos($tr, 'TEST FIXTURE Registry Outlet') !== false) {
        $rows_seen['registry']++;
        check(!$has_wait,
              'a row whose collector the archive schedule does not cover must '
              . 'promise nothing: nothing will re-check it');
    } elseif (strpos($tr, 'TEST FIXTURE Archived Outlet') !== false) {
        check(!$has_wait, 'the pending sentence never appears beside the link');
    }
}
check($rows_seen['pending'] > 0 && $rows_seen['registry'] > 0,
      'both pending-state kinds have to reach page one or this asserts nothing: '
      . $rows_seen['pending'] . ' pending, ' . $rows_seen['registry'] . ' out of scope');

// The repaint contract: dashboard.js prints the SERVER's sentence, carried on
// the root element, so the first paint and every repaint say the same date.
check(strpos($html, 'data-archive-note=') !== false
      && strpos($html, esc_attr(wp_json_encode(array(
             'collectors' => array_values($promise['collectors']),
             'text'       => $expected_note,
         )))) !== false,
      'the composed pending note has to ride the root element for dashboard.js, '
      . 'or the repaint would derive a second date');

/* --- the signal board ----------------------------------------------------- */

/*
 * THE SIGNAL BOARD (the owner\'s shared design, adopted 2026-08-02). It
 * REPLACED the dated strip\'s four text lines: one head (the date, the heat
 * legend, Copy as Post), the signal-by-period matrix, ONE footnote line. The
 * strip\'s markup must be GONE — half a replacement is two summaries that can
 * disagree — and the board\'s numbers must be the database\'s.
 */
check(strpos($html, 'id="tit-board"') !== false,
      'the signal board has to render');
check(strpos($html, 'Today, ' . gmdate('M j', TIT_FIXTURE_NOW)) !== false,
      'the board title carries the derived date, never a typed one');
check(strpos($html, 'id="tit-dg-copy"') !== false && strpos($html, 'Copy as Post') !== false,
      'Copy as Post survives the strip it came from, in the board head');
check(substr_count($html, 'class="tit-lg"') === 4
      && preg_match('/tit-board-legend[^>]*>less</', $html) === 1,
      'the heat legend is real text ("less ... more") with four swatches');
foreach (array('tit-dg-row', 'data-dg=', 'id="tit-dg"', 'tit-dg-cov') as $gone) {
    check(strpos($html, $gone) === false,
          'the dated strip\'s markup (' . $gone . ') must be gone: the board '
          . 'replaces it, and half a replacement is two summaries that can disagree');
}
check(strpos($html, 'vs the week before') === false,
      'the week-over-week sentence went with the strip that carried it');

// ONE footnote line, and only one: the lede-plus-details pair is gone.
check(substr_count($html, 'class="tit-board-note"') === 1,
      'the board carries exactly ONE footnote line');
check(strpos($html, 'tit-matrix-lede') === false
      && strpos($html, 'tit-matrix-note') === false,
      'and the old two-line lede plus Full notes disclosure is gone');
check(preg_match('/class="tit-board-note">.*?href="#tit-usd-note".*?<\/p>/s', $html) === 1,
      'the footnote points the USD caveat at its one home (#tit-usd-note) '
      . 'rather than repeating the sentence');

/*
 * EVERY CELL IS THE DATABASE\'S NUMBER. The board\'s "Everything in This View"
 * row is recomputed here from the same clause the render used, per window,
 * and each window\'s data-since must be carried on the cell so one handler
 * drives the whole click contract.
 */
$dg_date = 'COALESCE(published_date, DATE(captured_at))';
$q_month = (intdiv((int) gmdate('n', TIT_FIXTURE_NOW) - 1, 3) * 3) + 1;
$board_windows = array(
    'week'    => gmdate('Y-m-d', TIT_FIXTURE_NOW - 6 * DAY_IN_SECONDS),
    'month'   => gmdate('Y-m-01', TIT_FIXTURE_NOW),
    'quarter' => sprintf('%s-%02d-01', gmdate('Y', TIT_FIXTURE_NOW), $q_month),
    'ytd'     => gmdate('Y-01-01', TIT_FIXTURE_NOW),
);
foreach ($board_windows as $bucket => $since) {
    $expect = (int) $wpdb->get_var(
        "SELECT COUNT(*) FROM wp_tit_signals WHERE {$base_where} AND {$dg_date} >= '{$since}'");
    if (preg_match_all('/data-filter=""\s+data-since="' . preg_quote($since, '/')
                       . '".*?<\/span>([\d,]+)<\/button>/s', $html, $m)) {
        check((int) str_replace(',', '', $m[1][0]) === $expect,
              "the {$bucket} column\'s total cell prints {$m[1][0]} and the database holds "
              . number_format($expect) . '. Every cell on this board is computed.');
    } else {
        check(false, "the {$bucket} column has to render a total cell carrying data-since");
    }
}
// The YTD column names the CURRENT year, derived rather than typed, so it
// becomes next year\'s by itself at midnight on 31 December.
check(strpos($html, '>' . gmdate('Y', TIT_FIXTURE_NOW) . ' YTD<') !== false,
      'the YTD column has to name the current year, derived from the clock');

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

/* The week-over-week comparison and its young-corpus suppression went with
   the dated strip (2026-08-02): the board prints counts per window and no
   derived percentage, so there is no fabricatable comparison left to guard.
   The "vs the week before" absence is asserted with the board above. */

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
 * THE ASSERTION LIVES IN budget_phase(), on a pinned clock. See
 * TIT_DASH_BYTE_CLOCK: measured here, on whatever day CI happened to run, the
 * same markup weighed anywhere from 184,380 to 185,162 bytes, so the number was
 * as much about the calendar as about the page.
 *
 * The render is still measured here, because the figure this process prints at
 * the end is the one a session reading its output is looking at, and because a
 * gap between the two would be a real defect: the budget subprocess renders the
 * same fixture and must reach the same page. It is reported, never asserted.
 */
$GLOBALS['tit_bytes'] = measure_bytes($html);
// The full-corpus render, kept for the optional browser dump in finish(). Not
// the trimmed one below it, which exists only to prove a suppression.
$GLOBALS['tit_dump_html'] = $html;

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

/* --- the market trend, and the two cards it replaced --------------------- */

/*
 * 2026-08-05. The owner judged the collection-rate chart an ops metric readers
 * do not need on the dashboard, so its slot carries a MARKET trend now, on
 * same-store-sales logic, and the standalone direction ranking folded into it
 * as the split. Three properties are load-bearing and each shipped broken once
 * in some form on this page:
 *
 *  - the basis sentence is VISIBLE prose on the card, never note_html: the
 *    (i) panels are closed by dashboard.js on load, and a basis nobody sees
 *    is the place-caveat defect again;
 *  - raw all-collector counts are never drawn as a market claim: this fixture
 *    has no collector live for the whole window, so the card MUST be in its
 *    share (or not-drawn) state and must say which and why;
 *  - the replaced cards are actually gone from this page, and the moved one
 *    is gone by its title, not merely renamed.
 */
check(strpos($html, 'id="chart-market"') !== false,
      'the market trend card renders');
check(strpos($html, 'id="chart-direction"') === false,
      'the standalone direction card is gone: its numbers are the split inside '
      . 'the market trend now, and a second card of them was a duplicate');
check(strpos($html, 'Updates Collected a Day') === false,
      'the collection-rate chart has left the dashboard: it is an operations '
      . 'measure and it renders on the sources page now');
check(strpos($html, 'id="tit-trend-box"') === false,
      'and its repaint box went with it, or dashboard.js would inject the '
      . 'aggregate trend_html into a card that no longer explains it');

$mk = strpos($html, 'id="chart-market"');
$mk_seg = substr($html, $mk, strpos($html, 'What Kind Of Moves') - $mk);
check(strpos($mk_seg, 'id="tit-market-caveat"') !== false,
      'the market card carries its visible caveat');
$mk_note = strpos($mk_seg, 'tit-chart-note');
$mk_cav  = strpos($mk_seg, 'id="tit-market-caveat"');
check($mk_note !== false && $mk_cav !== false && $mk_cav > $mk_note,
      'the caveat is its own element AFTER the head block, not note_html '
      . 'inside the (i) panel that dashboard.js closes on load');
// This fixture ingests everything within days of the render, so no collector
// was live for the whole window and a count trend would be dishonest here.
check(strpos($mk_seg, 'live for all 12 weeks') !== false,
      'the caveat names the panel state and the window');
check(strpos($mk_seg, 'SHARES of its own updates') !== false
      || strpos($mk_seg, 'no trend is drawn') !== false,
      'with no full-window collector the card must be the share variant or '
      . 'refuse to draw, never a raw count drawn as a market claim');
check(strpos($mk_seg, 'the filters on this page do not narrow this card') !== false,
      'a static card on a filterable page says so in visible prose');
foreach (array('Adding Roles', 'Cutting Roles', 'Headcount Not Stated') as $mk_label) {
    check(strpos($mk_seg, $mk_label) !== false,
          'the market legend carries "' . $mk_label . '", the shared direction vocabulary');
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

/* --- a matrix cell may only ask for filters the browser forwards --------- */

/*
 * THE SAME CHECK render_press.php RUNS ON ITS HREFS, ON THE MATRIX'S CELLS.
 *
 * A cell is a deep link that happens to be a button: tit_signal_defs() writes
 * the spec, the server prints it as data-filter, and dashboard.js routes it
 * through the same inputs the querystring uses. So it fails the same way a
 * press link does, and it failed that way silently until 2026-08-21: the Total
 * Raised figure sums company raises only and its cell asked for `funding=1`,
 * the wider view, which includes divestiture prices, fund closes, outbound
 * spends, state subsidies and pledges. Every one of those is a row the figure
 * left out, sitting under the figure as though it were part of it.
 *
 * The parameter list is parsed out of dashboard.js rather than written here,
 * for render_press.php's reason: a control that is renamed or removed breaks
 * this on the next run instead of turning a cell into a button that quietly
 * does nothing. `funding` has no control and is read by name in
 * applyUrlState(), so it is admitted the same way and proved present.
 *
 * The VALUE is checked too. A wrong name over-reports and a wrong value
 * under-reports, and a matrix cell that lands on zero rows reads as "nothing
 * happened this week" rather than as a broken link.
 */
$js = file_get_contents($tit_plugin . 'assets/dashboard.js');
check($js !== false && $js !== '',
      'dashboard.js has to be readable to check the matrix cells against it');
preg_match('/var inputs = \{(.*?)\n  \};/s', $js, $im);
check(!empty($im[1]),
      'the `inputs` map could not be parsed out of dashboard.js, and it is the '
      . 'only thing standing between a matrix cell and a filter the browser '
      . 'drops on the floor, so a parse failure is a failure and never a skip');
preg_match_all("/^\s*'?([a-z_]+)'?\s*:/m", $im[1] ?? '', $keys);
$js_reads = array_flip($keys[1] ?? array());
check(strpos($js, "q.get('funding')") !== false,
      'a cell may ask for `funding` only while applyUrlState() still reads it '
      . 'by name, and that call is gone from dashboard.js');
$js_reads['funding'] = true;

// A deep-linked view forces the filter panel open, and a parameter missing from
// that regex leaves someone arriving on a narrowed page with the controls shut
// and nothing on screen explaining why the numbers are not the front page's.
preg_match('/var deepLinked = \/(.*?)\/\s*\n/s', $js, $dm);
check(!empty($dm[1]), 'the deepLinked regex could not be parsed out of dashboard.js');

preg_match_all('/data-filter="([^"]*)"/', $html, $specs);
check(count($specs[1]) > 0, 'the matrix has to print its cell filters');
$seen_multi = false;
foreach (array_unique($specs[1]) as $spec) {
    $spec = html_entity_decode($spec, ENT_QUOTES, 'UTF-8');
    if ($spec === '') continue;             // the total row narrows nothing
    if (strpos($spec, '&') !== false) $seen_multi = true;
    parse_str($spec, $args);
    foreach ($args as $name => $value) {
        check(isset($js_reads[$name]),
              "the matrix cell `{$spec}` asks for `{$name}`, which dashboard.js "
              . 'does not read. The cell would advertise a narrowing and serve '
              . 'the wider view, and nothing would error');
        check($name === 'funding' || strpos($dm[1] ?? '', '|' . $name . '|') !== false
              || strpos($dm[1] ?? '', '(' . $name . '|') !== false,
              "the matrix cell `{$spec}` asks for `{$name}`, which is missing "
              . 'from the deepLinked regex, so sharing the link leaves the '
              . 'filter panel collapsed over a page that is already narrowed');
        $allowed = array('funding' => null, 'direction' => 'tit_allowed_directions',
                         'pillar' => 'tit_allowed_pillars',
                         'money_basis' => 'tit_allowed_money_bases');
        check(array_key_exists($name, $allowed),
              "the matrix cell `{$spec}` uses `{$name}`, which this check has no "
              . 'vocabulary for. Add it here rather than leaving the value '
              . 'unchecked: a value nothing carries returns zero rows and reads '
              . 'as "nothing happened", which is the quieter half of this bug');
        if (!empty($allowed[$name]) && function_exists($allowed[$name])) {
            check(in_array($value, call_user_func($allowed[$name]), true),
                  "the matrix cell `{$spec}` filters {$name}={$value}, which "
                  . $allowed[$name] . '() does not declare, so it would return '
                  . 'zero rows and read as a quiet week');
        }
    }
}

/*
 * AND ONE CELL HAS TO CARRY TWO OF THEM. The Total Raised row is the only spec
 * with an `&` in it, and it is the reason glancePairs() exists: split on '='
 * alone, `funding=1&money_basis=company_raise` yields a key of `funding` and a
 * value of `1&money_basis`, no input answers to it, and the cell becomes a
 * button that does nothing. A future session that simplifies that row back to
 * one pair fails here instead of on the live page.
 */
check($seen_multi,
      'the Total Raised cell has to carry BOTH its narrowings: the funding view '
      . 'and the basis its figure actually sums. Either half alone names a '
      . 'different set of rows from the number printed on the cell');
check(strpos($js, 'function glancePairs(') !== false,
      'and dashboard.js has to still split a cell spec on & before =, or that '
      . 'two-part spec is routed to an input that does not exist');

/* --- the pill groups have to swap into a box the same size -------------- */

/*
 * dashboard.js replaces each multiple select with a row of pills AFTER the page
 * has painted, because it loads in the footer. If the two boxes are not the same
 * height the reader watches the filter panel resize and everything below it
 * move, which is the definition of a layout shift. The stylesheet fixes both to
 * one height; this asserts the markup still hands it two boxes to fix, since a
 * select that lost its `multiple` would never be pillified at all.
 */
check(substr_count($html, 'multiple size="5"') === 8,
      'eight multiple selects become pill groups, and the stylesheet reserves the '
      . 'height of each: found ' . substr_count($html, 'multiple size="5"'));

/* --- nothing that scrolls the body sideways ----------------------------- */

/*
 * Every wide thing on this page has to live inside its own scroller, or the body
 * scrolls sideways on a phone and the whole layout reads as broken.
 *
 * The results used to be the widest thing here and this line asserted its
 * scroller. They are cards now (docs/card-contract.json), and a card has nothing
 * to scroll: it pins no width at all, which is a better answer than a scroller
 * and is why the assertion changed rather than being deleted. So the property is
 * stated the way it is actually true now — any TABLE still rendered is inside a
 * scroller, and the results list is not wrapped in one, because wrapping a thing
 * that already fits produces a scrollbar with nowhere to go.
 *
 * NOT asserted anywhere, and deliberately: scrollWidth === innerWidth. It passes
 * on a CLIPPED page, and an overflow-x rule on a narrow ancestor already
 * guillotined this page's hero headline once, in 1.37.0.
 */
// Any of our own *-scroll wrappers counts: the glance matrix has its own
// (.tit-matrix-scroll), because it and a data table need different rules.
$tables   = preg_match_all('/<table[ >]/', $html);
$scrolers = preg_match_all('/class="tit-[a-z]+-scroll"/', $html);
if ($tables > 0) {
    check($scrolers >= $tables,
          'every table left on this page sits inside its own horizontal scroller: '
          . $tables . ' table(s), ' . $scrolers . ' scroller(s)');
}
check(strpos($html, '<ul class="tit-cards" id="tit-rows">') !== false,
      'the results are a card list, which needs no scroller because it pins no width');

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

/*
 * MEASURED WITH THE FIXTURE PREFIX STRIPPED, and that is not a way of gaming the
 * budget. "TEST FIXTURE " exists so no human mistakes this render for the live
 * page, it appears once per row plus once per employer link, and it is about
 * 2.1KB that PRODUCTION NEVER SHIPS. Counting it would spend real headroom on a
 * test artefact and would eventually fail a legitimate change for a reason
 * nobody could find. The budget has to measure the page.
 */
function measure_bytes($html) {
    return strlen(str_replace('TEST FIXTURE ', '', $html));
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
    $html = render();
    check($wpdb->reads === TIT_DASH_QUERY_BUDGET,
          'the first cold render in a process must cost exactly '
          . TIT_DASH_QUERY_BUDGET . ' queries and cost ' . $wpdb->reads
          . trace($wpdb->log));

    /*
     * THE BYTE BUDGET, measured on the first cold render of a pinned clock and
     * nowhere else. TIT_DASH_BYTE_CLOCK says why the clock is pinned; this is
     * the process it is pinned in, because this one already exists, already
     * starts from a database nothing has touched, and is already spawned with
     * an environment of our own choosing.
     */
    $bytes = measure_bytes($html);
    $GLOBALS['tit_bytes'] = $bytes;
    check($bytes <= TIT_DASH_BYTE_BUDGET,
          'the markup must stay inside ' . number_format(TIT_DASH_BYTE_BUDGET)
          . ' bytes and was ' . number_format($bytes)
          . ' (fixture prefixes excluded, measured at ' . TIT_DASH_BYTE_CLOCK
          . '). This page is read on phones; a new card is not free.');

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
    $tbody = substr($html, strpos($html, '<ul class="tit-cards" id="tit-rows">'));
    $tbody = substr($tbody, 0, strpos($tbody, '</ul>'));
    check(substr_count($tbody, '<li class="tit-card">') === TIT_DASH_ROWS,
          'and neither can the number of cards it prints: '
          . substr_count($tbody, '<li class="tit-card">'));
}

function finish($phase) {
    global $failures;
    if ($failures) {
        fwrite(STDERR, 'dashboard FAILED' . ($phase ? " in phase '{$phase}'" : '')
                       . ":\n  - " . implode("\n  - ", $failures) . "\n");
        exit(1);
    }
    if ($phase === 'budget') {
        printf("  budget ok: %d queries cold, none warm, %s bytes of markup at %s.\n",
               TIT_DASH_QUERY_BUDGET, number_format($GLOBALS['tit_bytes']),
               TIT_DASH_BYTE_CLOCK);
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

    /*
     * OPTIONAL: the SAME fixture through /aggregate, for the browser test that
     * checks the place ribbon against the endpoint rather than against a
     * re-implementation of it. No parameters at all, which is the request the
     * ribbon's counts have to match: /aggregate applies the detail filter only
     * when asked for it, and the ribbon counts every update we hold.
     *
     * After the byte and query assertions on purpose. This is one more read of
     * the table and the budget is measured in a separate process anyway, but a
     * diagnostic that can move a budget is a diagnostic that gets deleted.
     */
    $agg = getenv('TIT_DUMP_AGGREGATE');
    if ($agg) {
        $response = tit_api_aggregate(new WP_REST_Request(array()));
        file_put_contents($agg, json_encode($response->data));
        fwrite(STDERR, "wrote /aggregate to {$agg}\n");
    }

    /*
     * The budget needs a process where nothing has rendered yet, AND a clock
     * that is the same every day.
     *
     * TIT_FIXTURE_CLOCK is set here rather than inherited, and it overrides an
     * explicit one from the caller on purpose. The override exists to run the
     * assertions in THIS process at a chosen instant, which is what the
     * 00:00-01:00 UTC ordering defect needed; the byte and query budgets are
     * not date-shaped claims and must not answer differently on a Tuesday. The
     * child announces the pin on STDERR either way, so nothing about it is
     * silent.
     */
    $command = 'TIT_FIXTURE_CLOCK=' . escapeshellarg(TIT_DASH_BYTE_CLOCK) . ' '
               . escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' budget';
    passthru($command, $status);
    if ($status !== 0) exit(1);
    printf("dashboard ok: markup, controls, %s bytes of it today, and a %d-query "
           . "cold render.\n",
           number_format($GLOBALS['tit_bytes']), TIT_DASH_QUERY_BUDGET);
    exit(0);
}
