<?php
/**
 * Render the recall page's chart and history table outside WordPress.
 *
 * The trend only draws once there are two measurements, so on the day it ships
 * there is exactly one point and the chart cannot be checked on the live page.
 * The alternative was to publish a fabricated second point to look at it, which
 * is not something this project gets to do. So it is exercised here instead,
 * with synthetic points, in CI, on every push.
 *
 * WordPress is not loaded. recall.php registers hooks and escapes output at
 * load time, so the few functions it needs are stubbed with honest equivalents:
 * the escapers really escape, so a missing esc_attr would still show up as
 * broken markup here.
 *
 * Exits non-zero with a message on any failure. Run: php tests/php/render_recall.php
 */

define('ABSPATH', __DIR__);
define('TIT_PATH', __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/');
define('TIT_VERSION', 'test');

function add_action($h, $f, $p = 10, $a = 1) {}
function add_filter($h, $f, $p = 10, $a = 1) {}
function add_rewrite_rule($r, $q, $w = 'bottom') {}
function home_url($path = '') { return 'https://example.test' . $path; }
function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function number_format_i18n($n) { return number_format((float) $n); }
function human_time_diff($a, $b) { return '1 hour'; }
function get_query_var($v) { return ''; }

$GLOBALS['tit_options'] = array();
function get_option($k, $d = false) { return $GLOBALS['tit_options'][$k] ?? $d; }
function update_option($k, $v, $a = null) { $GLOBALS['tit_options'][$k] = $v; return true; }
function rest_ensure_response($r) { return $r; }
function register_rest_route($ns, $route, $args) {}
function sanitize_text_field($s) { return trim((string) $s); }

class WP_Error {
    public $code; public $message; public $data;
    public function __construct($code = '', $message = '', $data = array()) {
        $this->code = $code; $this->message = $message; $this->data = $data;
    }
}
class WP_REST_Request {
    private $body;
    public function __construct($body) { $this->body = $body; }
    public function get_json_params() { return $this->body; }
}

require TIT_PATH . 'includes/recall.php';

$failures = array();
function check($condition, $message) {
    global $failures;
    if (!$condition) $failures[] = $message;
}

// --- two measurements: the chart must draw -------------------------------

$series = array(
    array(
        'measured_on' => '2026-07-28', 'goldset_version' => '2026-07-v1',
        'overall' => array('total' => 89, 'found' => 5, 'found_partial' => 3,
                           'missed' => 81, 'held' => 8, 'held_pct' => 9.0, 'clean_pct' => 5.6),
        'by_segment' => array(
            'US funding' => array('total' => 22, 'found' => 1, 'found_partial' => 1,
                                  'missed' => 20, 'held' => 2, 'held_pct' => 9.1, 'clean_pct' => 4.5),
        ),
        'by_source_type' => array(), 'by_country' => array(),
    ),
    array(
        'measured_on' => '2026-08-03', 'goldset_version' => '2026-07-v1',
        'overall' => array('total' => 89, 'found' => 9, 'found_partial' => 4,
                           'missed' => 76, 'held' => 13, 'held_pct' => 14.6, 'clean_pct' => 10.1),
        'by_segment' => array(
            'US funding' => array('total' => 22, 'found' => 3, 'found_partial' => 1,
                                  'missed' => 18, 'held' => 4, 'held_pct' => 18.2, 'clean_pct' => 13.6),
            'non-US funding' => array('total' => 44, 'found' => 2, 'found_partial' => 0,
                                      'missed' => 42, 'held' => 2, 'held_pct' => 4.5, 'clean_pct' => 4.5),
        ),
        'by_source_type' => array(), 'by_country' => array(),
    ),
);

$points = tit_recall_points($series);
check(count($points) === 2, 'both measurements should become points');

$svg = tit_recall_sparkline($points);
check(strpos($svg, '<svg') !== false, 'the chart should render an svg');
check(substr_count($svg, '<path') === 2, 'both lines should be drawn, held and clean');
check(substr_count($svg, '<circle') === 4, 'every point on both lines should get a dot');
check(strpos($svg, 'tit-rc-held') !== false, 'the held line should carry its class');
check(strpos($svg, 'tit-rc-clean') !== false, 'the clean line should carry its class');
check(strpos($svg, '2026-07-28') !== false && strpos($svg, '2026-08-03') !== false,
      'the ends of the series should be labelled with their dates');
check(strpos($svg, 'aria-label') !== false, 'the chart needs a text description');
// The y axis must start at zero. A truncated axis turns a five point move into
// a cliff, which is the flattery this page exists to avoid.
check(preg_match('/>0%<\/text>/', $svg) === 1, 'the axis should start at zero');

// --- one measurement: the chart must NOT draw ----------------------------

check(tit_recall_sparkline(array($points[0])) === '',
      'a single measurement is not a trend and must not be drawn as one');

// --- the history table ---------------------------------------------------

ob_start();
tit_recall_history_table('By category, over time', 'by_segment', $points);
$table = ob_get_clean();

check(strpos($table, '2 of 22') !== false && strpos($table, '4 of 22') !== false,
      'history should show raw counts per measurement');
check(strpos($table, 'not tested') !== false,
      'a category absent from an earlier set must read as not tested, never as zero');
// Counted with a pattern, not substr_count('<th'): `<thead` contains `<th`.
check(preg_match_all('/<th[\s>]/', $table) === 3,
      'a column per measurement, plus the label column');

// --- labels --------------------------------------------------------------

check(tit_recall_label('non-US funding') === 'Funding rounds outside the US',
      'cell keys should render as English');
check(tit_recall_label('country_missing') === 'No country recorded',
      'defect keys should render as English');

// --- the keyed endpoint that keeps the page fresh ------------------------
//
// This is the difference between automated and nearly automated: the plugin
// deploy is not armed on push, so without this route the page would show its
// shipping-day figure forever while the repository filled with newer ones.

$measurement = array(
    'measured_on' => '2026-08-03',
    'goldset' => array('digest' => 'abc123', 'version' => '2026-07-v1'),
    'summary' => array('overall' => array('total' => 2, 'held' => 1, 'missed' => 1,
                                          'found' => 1, 'found_partial' => 0,
                                          'held_pct' => 50.0, 'clean_pct' => 50.0)),
    'items' => array(array('verdict' => 'FOUND'), array('verdict' => 'MISSED')),
    'series' => $series,
);

$ok = tit_api_recall(new WP_REST_Request($measurement));
check(!($ok instanceof WP_Error), 'a well formed measurement should be accepted');
check(isset($ok['stored']) && $ok['stored'] === true, 'and should report that it stored');

$stored = tit_recall_data();
check(($stored['measured_on'] ?? '') === '2026-08-03',
      'the page must prefer the pushed measurement over the file it shipped with');

// A figure with no events behind it is the one thing this route must refuse.
$typed = $measurement;
$typed['items'] = array();
check(tit_api_recall(new WP_REST_Request($typed)) instanceof WP_Error,
      'a summary with no items must be refused');

$inflated = $measurement;
$inflated['summary']['overall']['held'] = 2;
check(tit_api_recall(new WP_REST_Request($inflated)) instanceof WP_Error,
      'held plus missed must equal total, or the counts were typed not measured');

check(tit_api_recall(new WP_REST_Request(array('measured_on' => '2026-08-03'))) instanceof WP_Error,
      'a body with no summary must be refused');

// --- the whole page, and the sentence the owner misread -------------------
//
// "Held has gone from 9% to 19.5%, a change of +10.5 points" made the owner
// read his own improvement as decline: on a page about what we MISS, a bare
// metric called "Held" rising reads like more of something bad. The metric is
// coverage of the independent gold set, a rise is good, and the sentence now
// says both. These assertions render the full page and hold that wording, so
// the misreading cannot ship again under a rewrite.

function get_header() { echo '<!--header-->'; }
function get_footer() { echo '<!--footer-->'; }
// The real map lives in the plugin bootstrap, which this harness does not
// load; the property under test is that the PAGE asks the map, so a small
// honest stub is enough and asserting its output proves the call happens.
function tit_country_name($code) {
    $map = array('SG' => 'Singapore', 'AZ' => 'Azerbaijan', 'US' => 'United States',
                 'KR' => 'South Korea', 'DE' => 'Germany', 'IT' => 'Italy',
                 'ES' => 'Spain');
    return $map[strtoupper(trim((string) $code))] ?? '';
}

$page_data = array(
    'measured_on' => '2026-08-03',
    'goldset' => array(
        'digest' => 'abc123', 'version' => '2026-07-v1',
        'assembled_on' => '2026-07-27',
        'window' => array('start' => '2026-06-01', 'end' => '2026-07-25'),
        'counts' => array('country' => array('US' => 40, 'GB' => 20, 'IN' => 29)),
        'caveats' => array('TEST FIXTURE caveat.'),
        'url' => 'https://example.test/goldset',
    ),
    'summary' => array(
        'overall' => array('total' => 89, 'found' => 9, 'found_partial' => 4,
                           'missed' => 76, 'held' => 13,
                           'held_pct' => 14.6, 'clean_pct' => 10.1),
        'by_segment' => array(
            'US funding' => array('total' => 22, 'found' => 3, 'found_partial' => 1,
                                  'missed' => 18, 'held' => 4,
                                  'held_pct' => 18.2, 'clean_pct' => 13.6),
        ),
        'defects' => array('country_missing' => 2),
        // One country with a live source in the generated coverage file and
        // one with nothing catalogued at all, so both halves of the source
        // line render: the named sources and the honest zero.
        'by_country' => array(
            'SG' => array('total' => 4, 'found' => 1, 'found_partial' => 0,
                          'missed' => 3, 'held' => 1,
                          'held_pct' => 25.0, 'clean_pct' => 25.0),
            'AZ' => array('total' => 1, 'found' => 0, 'found_partial' => 0,
                          'missed' => 1, 'held' => 0,
                          'held_pct' => 0.0, 'clean_pct' => 0.0),
        ),
    ),
    'items' => array(
        array('verdict' => 'FOUND', 'company' => 'TEST FIXTURE Employer A',
              'detail' => 'raised money', 'country' => 'US', 'event_date' => '2026-06-10',
              'source_name' => 'TEST FIXTURE Outlet', 'source_url' => 'https://example.test/a',
              'defects' => array()),
        array('verdict' => 'MISSED', 'company' => 'TEST FIXTURE Employer B',
              'detail' => 'changed CFO', 'country' => 'GB', 'event_date' => '2026-06-12',
              'source_name' => 'TEST FIXTURE Outlet', 'source_url' => 'https://example.test/b',
              'defects' => array()),
    ),
    'series' => $series,
);

ob_start();
tit_recall_render($page_data);
$page = ob_get_clean();
$flat = preg_replace('/\s+/', ' ', html_entity_decode(strip_tags($page), ENT_QUOTES, 'UTF-8'));

check(strpos($flat, 'Held has gone from') === false,
      'the bare-metric sentence is back. "Held has gone from 9% to 19.5%" made '
      . 'the owner read a doubling of coverage as decline; the metric must be '
      . 'named as coverage and the direction spelled out.');
check(strpos($flat, 'Coverage of the independent gold set has gone from 9% to 14.6% '
      . 'across 2 weekly measurements between 2026-07-28 and 2026-08-03, '
      . 'a gain of 5.6 points') !== false,
      'the direction sentence names coverage, the movement and its size, and '
      . 'got: ' . substr($flat, max(0, (int) strpos($flat, 'Coverage')), 260));
check(strpos($flat, 'Higher is better') !== false,
      'and it says in words which way is good, because on a page about misses '
      . 'a rising number reads as bad by default');
check(strpos($page, 'data-label="Held"') === false
      && strpos($page, 'data-label="Held with every field right"') === false,
      'the mobile column labels must match the "In the tracker" headers, never '
      . 'the bare metric name');
check(strpos($page, "\u{2014}") === false && strpos($page, "\u{2013}") === false,
      'no em or en dashes reach the page');

// --- the country table: names, plain headers, and the why underneath ------

check(strpos($page, '>Singapore<') !== false && strpos($page, '>Azerbaijan<') !== false,
      'country rows print full names, never ISO codes: AE, AR, AT is not a '
      . 'table a human reads');
check(preg_match('/data-label="Category">(SG|AZ)</', $page) === 0,
      'and no country cell falls through to its bare code');
check(strpos($page, '<th class="tit-num">Event captured</th>') !== false
      && strpos($page, '<th class="tit-num">Captured with every detail correct</th>') !== false,
      'the two score headers are the plain ones. The owner had to ask what '
      . '"And every field right" meant, which means it failed as a header');
check(strpos($page, 'In the tracker</th>') === false
      && strpos($page, 'And every field right</th>') === false,
      'and the old headers are gone');
check(strpos($flat, 'The second score is stricter') !== false
      && strpos($flat, 'passes the first score and fails the second') !== false,
      'one sentence above the tables says how the two scores relate');

// The source line under each country: always-visible prose, never inside a
// panel any script closes. SG has a live registry source in the generated
// coverage file; AZ has nothing catalogued, which must render as the honest
// sentence and not as silence.
check(strpos($page, 'class="tit-recall-src"') !== false,
      'each country row carries its source line');
check(strpos($flat, 'Read today:') !== false,
      'a country with live sources names them');
check(strpos($flat, 'No dedicated source yet. Events here can only arrive via '
      . 'worldwide discovery.') !== false,
      'a country with no dedicated source says so: that sentence is the honest '
      . 'answer to why it sits at zero');
check(strpos($page, 'tit-chart-note') === false,
      'nothing on this page uses .tit-chart-note, the panel dashboard.js '
      . 'closes on load; three separate caveats have shipped invisible that way');

// --- the whole-market table: external denominators, kept apart ------------

check(strpos($flat, 'Against the whole market, by country') !== false,
      'the whole-market table renders');
check(strpos($page, '>South Korea<') !== false && strpos($flat, '8,542') !== false,
      'with the external denominator that makes 0 of 1 stop standing in for '
      . 'coverage');
check(strpos($flat, 'Not comparable') !== false
      && strpos($flat, 'counts individual fund investments; ours counts funding rounds') !== false,
      'South Korea carries the Not comparable read: a ratio between two '
      . 'differently defined counts must never print as a coverage score');
check(strpos($flat, 'indicative and not a parity claim') !== false,
      'and the table says external counts use their own definitions and dates');
check(strpos($flat, 'Real gap') !== false && strpos($flat, 'Thin coverage') !== false,
      'the read column carries an honest verdict per market');
// Kept apart from the gold set: the market table must not print gold-set
// score headers, and the gold-set country table must not print shares.
// Sliced to END at the next population's heading. The United States section
// renders after the market table and has its own score headers, which are
// correct there and would be a false positive here.
$mkt = substr($page, strpos($page, 'Against the whole market'));
if (($stop = strpos($mkt, 'id="united-states"')) !== false) {
    $mkt = substr($mkt, 0, $stop);
}
check(strpos($mkt, 'Event captured</th>') === false,
      'the two measures answer different questions and are never blended '
      . 'into one table');

// --- a second measured population ----------------------------------------
//
// The United States set is 51 events against the worldwide set's 169, which is
// the whole reason it renders differently: at 51 events the interval IS the
// finding and a bare percentage misleads. These checks are that the page cannot
// lose the interval, cannot merge the two populations into one table, and
// cannot quietly drop a population that has never been measured.

$us = array(
    'measured_on' => '2026-08-11',
    'family' => 'us',
    'goldset' => array(
        'digest' => 'usdigest1', 'version' => '2026-06-us-v1',
        'window' => array('start' => '2026-06-01', 'end' => '2026-07-31'),
        'assembled_on' => '2026-08-12',
        'signal_types' => array('funding'),
        'caveats' => array('This set covers FUNDING ONLY.'),
    ),
    'summary' => array(
        'overall' => array('total' => 51, 'found' => 4, 'found_partial' => 17,
                           'missed' => 30, 'held' => 21, 'held_pct' => 41.2,
                           'clean_pct' => 7.8,
                           'held_interval' => array('pct' => 41.2, 'low_pct' => 28.8,
                                                    'high_pct' => 54.8,
                                                    'width_pct' => 26.1,
                                                    'successes' => 21, 'total' => 51)),
        'by_metro' => array(
            'Austin' => array('total' => 8, 'found' => 1, 'found_partial' => 4,
                              'missed' => 3, 'held' => 5, 'held_pct' => 62.5,
                              'clean_pct' => 12.5,
                              'held_interval' => array('pct' => 62.5, 'low_pct' => 30.6,
                                                       'high_pct' => 86.3, 'width_pct' => 55.7,
                                                       'successes' => 5, 'total' => 8)),
            'San Francisco' => array('total' => 13, 'found' => 0, 'found_partial' => 3,
                                     'missed' => 10, 'held' => 3, 'held_pct' => 23.1,
                                     'clean_pct' => 0.0,
                                     'held_interval' => array('pct' => 23.1, 'low_pct' => 8.2,
                                                              'high_pct' => 50.3, 'width_pct' => 42.1,
                                                              'successes' => 3, 'total' => 13)),
        ),
        'by_source_type' => array(), 'by_size_band' => array(),
    ),
    'items' => array(),
);

$GLOBALS['tit_options']['tit_recall_us'] = $us;

ob_start();
tit_recall_family_section('us');
$block = ob_get_clean();
$flat_us = preg_replace('/\s+/', ' ',
    html_entity_decode(strip_tags($block), ENT_QUOTES, 'UTF-8'));

check(strpos($block, 'id="united-states"') !== false,
      'the second population gets its own anchored heading');
check(strpos($flat_us, '21 of 51') !== false,
      'the headline carries its counts, as every figure on this page does');
check(strpos($flat_us, '28.8% to 54.8%') !== false,
      'the headline carries its interval. At 51 events a bare 41% invites a '
      . 'reader to compare it against a number it has not been shown to '
      . 'differ from');
check(strpos($flat_us, '30.6% to 86.3%') !== false
      && strpos($flat_us, '8.2% to 50.3%') !== false,
      'and so does every metro cell, whose ranges overlap almost entirely');
check(strpos($flat_us, 'Read the range, not just the percentage') !== false,
      'the page says in words what the range is for');
check(strpos($flat_us, 'Funding rounds only') !== false,
      'a set that tests one of four signal types must say so where the '
      . 'number is, not only in a method note further down');
check(strpos($flat_us, 'This set covers FUNDING ONLY') !== false,
      'the set\'s own caveats reach the page rather than staying in the repo');

// The two populations are never blended. Different reference sets, different
// windows, different denominators: one table holding both would be a number
// that is true of nothing.
check(strpos($block, 'Recall by category') === false,
      'the second population must not reuse the worldwide tables');

// A population with no measurement says so rather than vanishing.
unset($GLOBALS['tit_options']['tit_recall_us']);
ob_start();
tit_recall_family_section('us', array());
$empty = preg_replace('/\s+/', ' ',
    html_entity_decode(strip_tags(ob_get_clean()), ENT_QUOTES, 'UTF-8'));
check(strpos($empty, 'No united states measurement has been published') !== false,
      'an unmeasured population reads as unmeasured, never as absent');
check(strpos($empty, '%') === false,
      'and it invents no encouraging percentage while it waits');

// The keyed endpoint routes by the family named in the BODY, and refuses one
// the page cannot render: a result stored under a name nothing reads is a
// result nobody will ever see.
$stored = tit_api_recall(new WP_REST_Request(array(
    'measured_on' => '2026-08-11', 'family' => 'us',
    'goldset' => array('digest' => 'usdigest1'),
    'summary' => array('overall' => array('total' => 2, 'held' => 1, 'missed' => 1,
                                          'found' => 1, 'found_partial' => 0,
                                          'held_pct' => 50.0, 'clean_pct' => 50.0)),
    'items' => array(array('id' => 'a'), array('id' => 'b')),
)));
check(is_array($stored) && ($stored['family'] ?? '') === 'us',
      'a measurement stores under the family it declares');
check(isset($GLOBALS['tit_options']['tit_recall_us']),
      'and lands in that family\'s option rather than over the worldwide one');

$bad = tit_api_recall(new WP_REST_Request(array(
    'measured_on' => '2026-08-11', 'family' => 'martian',
    'goldset' => array('digest' => 'x'),
    'summary' => array('overall' => array('total' => 1, 'held' => 1, 'missed' => 0,
                                          'found' => 1, 'found_partial' => 0,
                                          'held_pct' => 100.0, 'clean_pct' => 100.0)),
    'items' => array(array('id' => 'a')),
)));
check($bad instanceof WP_Error && $bad->code === 'tit_recall_bad_family',
      'an unknown family is refused rather than stored where nothing renders it');

if ($failures) {
    fwrite(STDERR, "recall page render FAILED:\n  - " . implode("\n  - ", $failures) . "\n");
    exit(1);
}
echo "recall page render ok: chart, history table, labels, the coverage "
   . "direction sentence and the second measured population all render.\n";
