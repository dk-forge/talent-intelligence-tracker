<?php
/**
 * Prove that correcting an employer's company_key does not 404 its old URL.
 *
 * WHY THIS IS A RUNNING HARNESS AND NOT A TEXT ASSERTION.
 *
 * The Python suite reads company.php as source, which is enough for "does the
 * constant appear twice". It is not enough here. What has to hold is a
 * behaviour across a state change — a slug that resolved yesterday still
 * resolving today, through a different code path, after the row it pointed at
 * was withdrawn and replaced. Three URLs that are in the live sitemap change
 * when correct_company_key.py runs, and "the redirect looks right" is exactly
 * the confidence that shipped 22 broken sitemap URLs on 1.45.4.
 *
 * So WordPress is stubbed and the SQL is real: $wpdb runs against an in-memory
 * SQLite table with the same columns and the same rows the correction produces,
 * so the JOIN in tit_company_moved_slugs() is executed rather than read. A
 * query the harness does not recognise is a failure, not a silent empty result.
 *
 * Each phase runs in its own process. tit_company_slug_index() memoises in a
 * static, which is correct in a request and wrong in a test that needs to see
 * the index before and after a correction.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/route_company_slugs.php
 */

define('ABSPATH', __DIR__);
define('TIT_PATH', __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/');
define('TIT_VERSION', 'test');
define('HOUR_IN_SECONDS', 3600);
define('ARRAY_A', 'ARRAY_A');   // $wpdb's "give me associative rows" flag

function add_action($h, $f, $p = 10, $a = 1) {}
function add_filter($h, $f, $p = 10, $a = 1) {}
function add_rewrite_rule($r, $q, $w = 'bottom') {}
function home_url($path = '') { return 'https://example.test' . $path; }
function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function number_format_i18n($n) { return number_format((float) $n); }
function date_i18n($f, $t) { return gmdate($f, $t); }
function human_time_diff($a, $b) { return '1 hour'; }
function get_query_var($v) { return ''; }
function sanitize_text_field($s) { return trim((string) $s); }
function status_header($c) {}
function nocache_headers() {}
function wp_safe_redirect($u, $c = 302) {}
function get_option($k, $d = false) { return $d; }
function update_option($k, $v, $a = null) { return true; }
function flush_rewrite_rules($hard = true) {}

// No caching in the harness. The transient is a performance detail; every
// assertion here is about what the index CONTAINS.
function get_transient($k) { return false; }
function set_transient($k, $v, $t = 0) { return true; }

/**
 * WordPress ships this and company.php depends on it: the slug is only ASCII
 * because remove_accents runs first.
 *
 * TWO THINGS THIS STUB MUST GET RIGHT, and the plain
 * `iconv('UTF-8', 'ASCII//TRANSLIT//IGNORE', $string)` it used to be got both
 * wrong.
 *
 * 1. IT MUST ONLY TOUCH THE LATIN RANGES. WordPress folds Latin-1, Latin
 *    Extended-A/B and Latin Extended Additional and leaves every other script
 *    alone. iconv//IGNORE DELETES what it cannot transliterate, so the stub ate
 *    Hangul before tit_company_slug() could romanise it -- and the romanised
 *    phase below failed with 'lg전자' still slugging to 'lg', which is the very
 *    bug it was written to catch. A stub that is wrong in the same direction as
 *    the defect makes the test agree with the bug.
 *
 * 2. IT MUST NOT DEPEND ON THE PLATFORM. GNU iconv renders 'é' as "e"; the
 *    BSD build macOS ships renders it "'e". So this harness asserted different
 *    slugs on a laptop and on the Linux runner -- 'the estée lauder companies'
 *    was `the-estee-lauder-companies` in CI and `the-est-ee-lauder-companies`
 *    locally. Keeping only the letters iconv returns makes both builds agree.
 */
function remove_accents($string) {
    return preg_replace_callback(
        '/[\x{00C0}-\x{024F}\x{1E00}-\x{1EFF}]/u',
        function ($m) {
            $folded = @iconv('UTF-8', 'ASCII//TRANSLIT//IGNORE', $m[0]);
            if ($folded === false) return $m[0];
            $folded = preg_replace('/[^A-Za-z]/', '', $folded);
            return $folded === '' ? $m[0] : $folded;
        },
        (string) $string);
}

function tit_table_name() { return 'wp_tit_signals'; }
function tit_country_name($cc) { return (string) $cc; }
function tit_money_short($n) { return (string) $n; }
function tit_render_header() {}
function tit_board_series_panel($k) { return ''; }
function tit_funding_stage_labels() { return array(); }

/**
 * $wpdb, backed by SQLite, so the plugin's SQL is executed rather than matched.
 *
 * prepare() is the real thing's contract and nothing more: %s becomes a quoted
 * literal, %d an integer. That is what company.php uses.
 */
class HarnessDb {
    public $pdo;
    public $last_error = '';

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
                confidence TEXT NOT NULL DEFAULT "verified",
                source_url TEXT NOT NULL DEFAULT "",
                source_name TEXT NOT NULL DEFAULT "",
                archive_url TEXT,
                published_date TEXT, captured_at TEXT NOT NULL DEFAULT "2026-01-01 00:00:00",
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

    public function get_col($sql) {
        return $this->pdo->query($sql)->fetchAll(PDO::FETCH_COLUMN, 0);
    }

    public function get_results($sql, $output = null) {
        return $this->pdo->query($sql)->fetchAll(PDO::FETCH_ASSOC);
    }

    public function insert_signal($signal_id, $company, $company_key, $opts = array()) {
        $row = array_merge(array(
            'revision' => 1, 'is_current' => 1, 'pillar' => 'rewards_comp',
            'signal_direction' => 'neutral', 'source_url' => 'https://example.test/' . $signal_id,
            'source_name' => 'GOV.UK gender pay gap service',
            'published_date' => '2024-04-05', 'captured_at' => '2026-01-01 00:00:00',
            'headline' => $company . ' published a pay gap report',
        ), $opts);
        $row['signal_id'] = $signal_id;
        $row['company'] = $company;
        $row['company_key'] = $company_key;
        $columns = implode(', ', array_keys($row));
        $marks = implode(', ', array_fill(0, count($row), '?'));
        $stmt = $this->pdo->prepare("INSERT INTO wp_tit_signals ({$columns}) VALUES ({$marks})");
        $stmt->execute(array_values($row));
    }

    /** What correct_company_key.py does: withdraw, then append the revision. */
    public function reissue_under($signal_id, $new_key) {
        $row = $this->pdo->query(
            "SELECT * FROM wp_tit_signals WHERE signal_id = " . $this->pdo->quote($signal_id)
            . " AND is_current = 1")->fetch(PDO::FETCH_ASSOC);
        if (!$row) { fwrite(STDERR, "no live row for {$signal_id}\n"); exit(1); }
        $this->pdo->exec("UPDATE wp_tit_signals SET is_current = 0 WHERE row_id = " . (int) $row['row_id']);
        unset($row['row_id']);
        $row['company_key'] = $new_key;
        $row['is_current'] = 1;
        $columns = implode(', ', array_keys($row));
        $marks = implode(', ', array_fill(0, count($row), '?'));
        $stmt = $this->pdo->prepare("INSERT INTO wp_tit_signals ({$columns}) VALUES ({$marks})");
        $stmt->execute(array_values($row));
    }
}

$GLOBALS['wpdb'] = new HarnessDb();
global $wpdb;

require TIT_PATH . 'includes/company.php';

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

/** The canonical URL a request for $slug would end at, or '' for a 404. */
function resolves_to($slug) {
    $rows = tit_company_rows($slug);
    return $rows ? tit_company_slug($rows[0]['company_key']) : '';
}

$phase = $argv[1] ?? '';

// --------------------------------------------------------------------------
// Every phase runs in its own process: the slug index memoises in a static.
// --------------------------------------------------------------------------
if ($phase === '') {
    $rc = 0;
    foreach (array('before', 'after', 'ambiguous', 'romanised', 'romanised-live-key',
                      'romanised-refused-collision', 'disambiguated-script-drop') as $each) {
        $command = escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' ' . escapeshellarg($each);
        passthru($command, $status);
        if ($status !== 0) $rc = 1;
    }
    if ($rc === 0) echo "company slug routing ok: a corrected key redirects, a live key wins.\n";
    exit($rc);
}

// The three employers whose URL actually moves, plus the merge that must NOT
// move one. Real keys and real row counts, from data/talent_intel.db.
$COOPS = array(
    array('sig' => 'coop', 'name' => 'CO-OPERATIVE GROUP LIMITED',
          'old' => '-operative group', 'new' => 'co-operative group', 'rows' => 9),
    array('sig' => 'midc', 'name' => 'THE MIDCOUNTIES CO-OPERATIVE LIMITED',
          'old' => 'the midcounties -operative', 'new' => 'the midcounties co-operative', 'rows' => 9),
    array('sig' => 'cent', 'name' => 'CENTRAL ENGLAND CO-OPERATIVE LIMITED',
          'old' => 'central england -operative', 'new' => 'central england co-operative', 'rows' => 8),
);

if ($phase === 'before' || $phase === 'after') {
    foreach ($COOPS as $coop) {
        for ($i = 0; $i < $coop['rows']; $i++) {
            $wpdb->insert_signal($coop['sig'] . $i, $coop['name'], $coop['old'],
                                 array('published_date' => (2018 + $i) . '-04-05'));
        }
    }
    // The merge: one employer, two spellings, one URL. Both halves stay live;
    // only the key changes on one of them.
    $wpdb->insert_signal('perma-8k', 'Perma-Fix Environmental Services, Inc.',
        'perma-fix environmental services',
        array('pillar' => 'leadership_change', 'collector' => 'sec_edgar',
              'published_date' => '2026-01-28'));
    for ($i = 0; $i < 3; $i++) {
        $wpdb->insert_signal('perma-pvp' . $i, 'PERMA FIX ENVIRONMENTAL SERVICES INC',
            'perma fix environmental services',
            array('collector' => 'sec_execcomp', 'published_date' => (2023 + $i) . '-12-31'));
    }
}

// --------------------------------------------------------------------------
if ($phase === 'before') {
// --------------------------------------------------------------------------
    // What the live sitemap contains today. The leading hyphen is trimmed by
    // canonicalisation, so the published URL is not the legacy form.
    check(resolves_to('operative-group') === 'operative-group',
          'the mangled key must serve its own URL before the correction');
    check(tit_company_rows('co-operative-group') === array(),
          'the corrected URL cannot exist before the correction');

    // The collision, refused rather than resolved.
    check(tit_company_servable_slug('perma-fix environmental services') === false,
          'a slug two live keys claim must not be servable');
    check(tit_company_servable_slug('perma fix environmental services') === false,
          'neither side of a collision is servable');
    check(tit_company_profile(tit_company_rows('perma-fix-environmental-services'))['indexable'] === false,
          'and neither side is indexable');

    $index = tit_company_slug_index();
    check($index['moved'] === array(),
          'nothing has been corrected yet, so nothing is a moved slug');
}

// --------------------------------------------------------------------------
if ($phase === 'after') {
// --------------------------------------------------------------------------
    foreach ($COOPS as $coop) {
        for ($i = 0; $i < $coop['rows']; $i++) {
            $wpdb->reissue_under($coop['sig'] . $i, $coop['new']);
        }
    }
    $wpdb->reissue_under('perma-8k', 'perma fix environmental services');

    foreach ($COOPS as $coop) {
        $canonical = tit_company_slug($coop['new']);
        $was = tit_company_slug($coop['old']);

        // The new URL serves the whole employer, by the fast path.
        check(count(tit_company_rows($canonical)) === $coop['rows'],
              "{$canonical} should serve all {$coop['rows']} rows");

        // The old URL still resolves, and to the new one. tit_company_template()
        // 301s on exactly this comparison.
        check(resolves_to($was) === $canonical,
              "the pre-correction URL /company/{$was}/ must redirect to "
              . "/company/{$canonical}/, not 404");

        // And the pre-1.46 form of the old key, which was also a live URL.
        check(resolves_to(tit_company_legacy_slug($coop['old'])) === $canonical,
              "the legacy form of {$was} must redirect too");

        check(tit_company_servable_slug($coop['new']) === true,
              "{$coop['new']} must be publishable after the correction");
    }

    // THE MERGE. One key now, so the collision is gone and the employer is
    // whole: the 8-K and the three pay tables on one page.
    $slug = 'perma-fix-environmental-services';
    check(count(tit_company_rows($slug)) === 4,
          'a merged employer serves both halves of its history');
    check(tit_company_servable_slug('perma fix environmental services') === true,
          'the merged key is publishable, because nothing else claims its slug');

    // A LIVE KEY WINS. The superseded 'perma-fix environmental services' claims
    // this slug too, and must not be allowed to redirect the employer that
    // currently holds it away from its own URL.
    check(resolves_to($slug) === $slug,
          'a slug a live key holds must be served, never redirected');
    $index = tit_company_slug_index();
    check(!isset($index['moved'][$slug]),
          'a slug a live key holds must not be in the moved map at all');

    // The old co-op URLs are the ONLY thing that moved.
    check(count($index['moved']) === 6,
          'two forms each of three moved keys, and nothing else: got '
          . count($index['moved']));
}

// --------------------------------------------------------------------------
if ($phase === 'romanised') {
// --------------------------------------------------------------------------
    // 1.88.0 ROMANISES HANGUL, AND THAT MOVES TEN LIVE URLS.
    //
    // 'lg전자' is LG Electronics. It used to slug to 'lg', because the
    // collapse deleted the Hangul and left the Latin fragment standing alone --
    // so LG Electronics was published on LG's URL, and because no OTHER key
    // produced 'lg' there was no collision for the refusal to catch. All ten
    // such URLs answered HTTP 200 on 2026-09-01.
    //
    // Executed rather than reasoned about, because "the redirect looks right"
    // is the confidence that shipped 22 broken sitemap URLs on 1.45.4.
    $wpdb->insert_signal('lg1', 'LG Electronics Inc.', 'lg전자',
                         array('published_date' => '2026-02-01'));
    $wpdb->insert_signal('cj1', 'CJ CheilJedang Corp.', 'cj제일제당',
                         array('published_date' => '2026-02-02'));
    // Two DIFFERENT employers whose old form collided on 'ai'. Neither was
    // ever served there and neither may be redirected there now.
    $wpdb->insert_signal('oa1', 'OpenAI', '오픈ai',
                         array('published_date' => '2026-02-03'));
    $wpdb->insert_signal('pa1', 'Persona AI', '페르소나ai',
                         array('published_date' => '2026-02-04'));

    check(tit_company_slug('lg전자') === 'lg-jeonja',
        "LG Electronics must not canonicalise to 'lg'; got '"
        . tit_company_slug('lg전자') . "'");

    // THE REGRESSION THIS PHASE EXISTS FOR.
    check(resolves_to('lg') === 'lg-jeonja',
        "/company/lg/ was a live, indexed URL and now 404s instead of "
        . "redirecting. Romanising moved it; tit_company_slug_preromanisation "
        . "is what keeps it reachable.");
    check(resolves_to('cj') === 'cj-jeiljedang',
        "/company/cj/ no longer redirects to CJ CheilJedang");

    // The new URL is of course itself reachable.
    check(resolves_to('lg-jeonja') === 'lg-jeonja', 'the new URL 404s');

    // AN AMBIGUOUS OLD FORM IS REFUSED, NOT GUESSED. '오픈ai' and '페르소나ai'
    // both used to produce 'ai'. Redirecting it would send readers looking for
    // one company to the other.
    check(resolves_to('ai') === '',
        "/company/ai/ was claimed by two different employers and must stay a "
        . "404 rather than silently picking one");
    check(resolves_to('opeun-ai') === 'opeun-ai', 'OpenAI has no URL');
    check(resolves_to('pereusona-ai') === 'pereusona-ai', 'Persona AI has no URL');
}

// --------------------------------------------------------------------------
if ($phase === 'romanised-live-key') {
// --------------------------------------------------------------------------
    // A LIVE KEY STILL WINS. Its own process, because the index memoises in a
    // static and this needs a different index from the phase above -- the same
    // reason every other phase here is its own process.
    //
    // This is the refusal that makes the historical map safe. If LG Corp. is
    // itself an employer we hold, /company/lg/ is ITS url and a claim from
    // 1.87.4 must not take it away.
    $wpdb->insert_signal('lg1', 'LG Electronics Inc.', 'lg전자',
                         array('published_date' => '2026-02-01'));
    $wpdb->insert_signal('lgc1', 'LG Corp.', 'lg',
                         array('published_date' => '2026-02-05'));

    check(resolves_to('lg') === 'lg',
        "an employer that currently holds /company/lg/ was redirected away "
        . "from its own URL by a historical claim");
    check(resolves_to('lg-jeonja') === 'lg-jeonja',
        'LG Electronics lost its own new URL');
}

// --------------------------------------------------------------------------
if ($phase === 'romanised-refused-collision') {
// --------------------------------------------------------------------------
    // THE CASE THAT ISOLATES THE LIVE-KEY REFUSAL, and it took a failed
    // mutation to find. Dropping `isset($claims[$old])` from the historical
    // loop did not fail the phase above, because a live key normally
    // contributes its OWN spelling to the historical map too and the ambiguity
    // refusal catches it. There is one shape where it does not:
    //
    //   'indigo'  -> canonical 'indigo', historical 'indigo'
    //   '인디고'   -> canonical 'indigo', historical ''  (all Hangul, so the
    //                 old rule deleted the whole name and there is no history)
    //
    // The canonical slug 'indigo' is claimed by TWO keys, so the collision
    // refusal REFUSES it and neither employer is published there -- which is
    // the entire point of that refusal. But the historical map sees only one
    // claimant, because the Hangul-only key contributed nothing, so without
    // the live-key check it would happily map 'indigo' -> 'indigo' and serve
    // one of two colliding employers under a URL the plugin had just decided
    // was unsafe. A refusal undone by a redirect is not a refusal.
    //
    // This pair is real: ops_status [1c] reports /company/indigo/ as a new
    // collision the moment Hangul is romanised.
    $wpdb->insert_signal('ind1', 'Indigo Books & Music', 'indigo',
                         array('published_date' => '2026-03-01'));
    $wpdb->insert_signal('ind2', 'Indigo (Korea)', '인디고',
                         array('published_date' => '2026-03-02'));

    check(tit_company_slug('인디고') === 'indigo',
        'the two keys must actually collide for this phase to mean anything');

    $index = tit_company_slug_index();
    check(isset($index['collisions']['indigo']),
        "'indigo' is claimed by two current keys and must be REFUSED, which is "
        . "what keeps it out of the sitemap until someone merges them");
    check(!isset($index['map']['indigo']),
        "the historical map added a claim for a slug the collision refusal had "
        . "just refused. A refusal undone by a redirect is not a refusal: the "
        . "live-key check in the historical loop is what stops it.");

    // What is NOT claimed here, deliberately. /company/indigo/ still RESOLVES,
    // because tit_company_rows() step 1 matches the ASCII key 'indigo' by
    // direct SQL and always has. That is unchanged by romanisation and is not
    // this phase's business; the page simply stops being INDEXABLE while the
    // collision stands, which is the refusal doing its job. Asserting a 404
    // here would be inventing a stricter rule than the plugin has ever had.
}

// --------------------------------------------------------------------------
if ($phase === 'ambiguous') {
// --------------------------------------------------------------------------
    // Two employers whose OLD keys canonicalise to one slug, corrected to two
    // different new keys. There is no right answer, so there must be no answer.
    $wpdb->insert_signal('amb-a', 'Northwind & Co', 'northwind and co');
    $wpdb->insert_signal('amb-b', 'Northwind and Co', 'northwind & co');
    $wpdb->reissue_under('amb-a', 'northwind alpha');
    $wpdb->reissue_under('amb-b', 'northwind beta');

    $index = tit_company_slug_index();
    check(!isset($index['moved']['northwind-and-co']),
          'a moved slug two corrections claim must be dropped, not guessed');
    check(tit_company_rows('northwind-and-co') === array(),
          'and it must 404 rather than serve one of the two employers');

    // The unambiguous halves still work.
    check(resolves_to('northwind-alpha') === 'northwind-alpha', 'northwind alpha serves itself');
    check(resolves_to('northwind-beta') === 'northwind-beta', 'northwind beta serves itself');
}

// --------------------------------------------------------------------------
if ($phase === 'disambiguated-script-drop') {
// --------------------------------------------------------------------------
    // THE LIVE DEFECT (ops_status [1c], /company/ibm/): 'ibm' is already
    // ASCII, so tit_company_slug() folds it to itself untouched. '日本ibm'
    // folds to the SAME 'ibm', because Han is a script this function does not
    // romanise or transliterate the way it now does Hangul -- it is simply
    // deleted by the final collapse. Before this phase's fix, that made
    // /company/ibm/ a two-key collision like any other and, worse,
    // tit_company_url('日本ibm') silently returned IBM'S OWN URL: a citation
    // for the Japanese subsidiary's news landed a reader on the parent's page.
    $wpdb->insert_signal('ibm1', 'IBM', 'ibm',
                         array('published_date' => '2026-04-01'));
    $wpdb->insert_signal('ibmjp1', '日本IBM', '日本ibm',
                         array('published_date' => '2026-04-02'));

    check(tit_company_slug('ibm') === 'ibm', 'sanity: IBM folds to itself');
    check(tit_company_slug('日本ibm') === 'ibm',
        "sanity: the PLAIN fold of both keys must still collide -- if this "
        . "fails, tit_company_slug() itself changed and the rest of this "
        . "phase is not testing what it says it is testing");

    $index = tit_company_slug_index();
    check(!isset($index['collisions']['ibm']),
        "'ibm' is a clean ASCII key -- nothing of ITS OWN was dropped -- so it "
        . "must not be refused just because a different key's script landed "
        . "on the same slug");
    check(isset($index['disambiguated']['日本ibm']),
        "'日本ibm' lost its Han prefix to the plain fold and must be given its "
        . "own URL instead of being blocked alongside 'ibm'");

    // IBM keeps its own clean URL, unmoved by the disambiguation.
    check(tit_company_url('ibm') === home_url('/' . TIT_COMPANY_BASE . '/ibm/'),
        'IBM must not lose its own URL because a different key collided on it');
    check(resolves_to('ibm') === 'ibm', '/company/ibm/ must still serve IBM');
    check(tit_company_servable_slug('ibm') === true, 'IBM must stay servable');

    // IBM Japan gets a DIFFERENT, reachable URL: not IBM's, not empty, not a
    // guessed English or Japanese reading of '日本' -- the disambiguated form
    // is the key's own bytes, percent-encoded.
    $ibm_url = home_url('/' . TIT_COMPANY_BASE . '/ibm/');
    $jp_url  = tit_company_url('日本ibm');
    check($jp_url !== $ibm_url,
        "IBM Japan's own citation must not point at IBM's page: got {$jp_url}");
    check(tit_company_servable_slug('日本ibm') === true,
        'IBM Japan must be servable, not silently blocked');

    // THE ROUND TRIP. What tit_company_url() just published has to be exactly
    // what a browser would request (decoding the href) and what
    // tit_company_rows() then resolves -- back to IBM Japan's own key, never
    // to IBM's and never to nothing.
    $prefix = home_url('/' . TIT_COMPANY_BASE . '/');
    check(strpos($jp_url, $prefix) === 0, "unexpected URL shape: {$jp_url}");
    $requested_path = rawurldecode(rtrim(substr($jp_url, strlen($prefix)), '/'));
    $rows = tit_company_rows($requested_path);
    check(count($rows) === 1 && $rows[0]['company_key'] === '日本ibm',
        'the URL tit_company_url() publishes for IBM Japan must resolve back '
        . 'to IBM Japan. Got ' . count($rows) . ' row(s)'
        . (count($rows) ? " for key '{$rows[0]['company_key']}'" : ''));

    // NO SELF-REDIRECT LOOP: the canonical-redirect comparison in
    // tit_company_template() decodes the published slug before comparing it
    // to the requested one, so visiting IBM Japan's own canonical URL must
    // compare equal to itself rather than bouncing forever.
    check(rawurldecode(tit_company_canonical_slug('日本ibm')) === $requested_path,
        'visiting the published URL for IBM Japan must not trigger a further '
        . 'redirect');
}

if ($failures) {
    fwrite(STDERR, "company slug routing FAILED in phase '{$phase}':\n  - "
                   . implode("\n  - ", $failures) . "\n");
    exit(1);
}
echo "  phase '{$phase}' ok\n";
