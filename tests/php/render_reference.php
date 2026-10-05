<?php
/*
 * EVERY EMPLOYER NAME IN THIS FILE IS PREFIXED "TEST FIXTURE" ON PURPOSE: a test
 * render indistinguishable from production is a trap for the next session.
 */
/**
 * Render the reference sections (includes/reference_render.php) against
 * synthetic payloads in the shape indeed_occupations.compact() and
 * h1b_lca.compact() publish, and check the claims each one makes:
 *
 *  - HIRING DEMAND: a source line naming Indeed Hiring Lab with the licence
 *    STORED IN THE PAYLOAD (not a literal), an as-of date, at most six lines,
 *    the occupation filter, and the 3-point fallback captioned as such when the
 *    payload has no weekly points.
 *  - H-1B COMPANY BOX: an EXACT name match only (case and punctuation
 *    variants must render nothing), "filings, not hires" next to the number,
 *    the DOL source line, the as-of date and the fiscal period.
 *  - H-1B SPONSORS: the table, the role and state filters, the same note.
 *  - EVERY SECTION RENDERS NOTHING WHEN ITS DATA IS MISSING OR MALFORMED.
 *  - Employer names are escaped.
 *  - The ingest splits the company map into its own option.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/render_reference.php
 */

define('ABSPATH', __DIR__);
define('TIT_VERSION', 'test');
define('TIT_PATH', __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/');
define('TIT_URL', 'https://example.test/plugin/');
$tit_plugin = TIT_PATH;

$GLOBALS['opts'] = array();
$GLOBALS['enqueued'] = array();
function add_action($h, $f, $p = 10, $a = 1) {}
function register_rest_route($ns, $route, $args) {}
function rest_url($p = '') { return 'https://example.test/blog/wp-json/' . $p; }
function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url_raw($s) { return (string) $s; }
function sanitize_text_field($s) { return trim((string) $s); }
function get_option($k, $d = false) { return $GLOBALS['opts'][$k] ?? $d; }
function update_option($k, $v, $a = null) { $GLOBALS['opts'][$k] = $v; return true; }
function wp_enqueue_script($h, $src = '', $deps = array(), $ver = false, $footer = false) {
    $GLOBALS['enqueued'][$h] = $src;
}
function wp_script_add_data($h, $k, $v) {}
function rest_ensure_response($v) { return $v; }
class WP_Error { public $code; public function __construct($c, $m = '', $d = null) { $this->code = $c; } }
class WP_REST_Request implements ArrayAccess {
    private $p; private $body;
    public function __construct($p, $body) { $this->p = $p; $this->body = $body; }
    public function get_json_params() { return $this->body; }
    public function offsetExists($k): bool { return isset($this->p[$k]); }
    public function offsetGet($k): mixed { return $this->p[$k]; }
    public function offsetSet($k, $v): void {}
    public function offsetUnset($k): void {}
}

require $tit_plugin . 'includes/reference_data.php';
require $tit_plugin . 'includes/reference_render.php';

$failures = array();
function check($cond, $msg) { global $failures; if (!$cond) $failures[] = $msg; }

/* ---------------------------------------------------------- fixtures */
function series($idx, $c4, $c52, $weekly = null) {
    $s = array('as_of' => '2026-09-25', 'index' => $idx, 'chg_4w' => $c4, 'chg_52w' => $c52);
    if ($weekly) $s['weekly'] = $weekly;
    return $s;
}
$indeed = array(
    'source' => 'indeed_occupations', 'as_of' => '2026-09-25',
    'licence' => 'CC BY 4.0 (FIXTURE LICENCE)', 'source_url' => 'https://github.com/hiring-lab/job_postings_tracker',
    'attribution' => 'x', 'countries' => array(),
);
foreach (array('AU', 'CA', 'DE', 'EA', 'ES', 'FR', 'GB', 'IE', 'IT', 'NL', 'US') as $i => $cc) {
    $indeed['countries'][$cc] = array(
        'total' => series(100 + $i, 1.5, -3.25),
        'categories' => in_array($cc, array('US', 'GB'), true)
            ? array('Software Development' => series(80 + $i, -2, -10), 'Nursing' => series(120, 1, 2))
            : null,
    );
}

$h1b = array(
    'source' => 'h1b_lca', 'as_of' => '2026-06-30', 'file' => 'LCA_Disclosure_Data_FY2026_Q3.xlsx',
    'fiscal_year' => 2026, 'quarter' => 3, 'licence' => 'Public domain (US Government work)',
    'attribution' => 'x', 'source_url' => 'https://www.dol.gov/agencies/eta/foreign-labor/performance',
    'top_employers' => array(
        array('employer' => 'TEST FIXTURE Widgets <script>alert(1)</script> Inc', 'cases' => 1200, 'certified' => 1180,
              'positions' => 2000, 'roles' => array('Computer and Mathematical' => 900, 'Management' => 100),
              'states' => array('WA' => 700, 'CA' => 300), 'wage_median' => 150000),
        array('employer' => 'TEST FIXTURE Clinics LLC', 'cases' => 300, 'certified' => 290, 'positions' => 290,
              'roles' => array('Healthcare Practitioners and Technical' => 290), 'states' => array('TX' => 290),
              'wage_median' => 98000),
    ),
);
$h1b_companies = array(
    'as_of' => '2026-06-30',
    'companies' => array('test fixture widgets' => array(
        'employer' => 'TEST FIXTURE Widgets Inc', 'cases' => 42, 'certified' => 40, 'positions' => 55,
        'roles' => array('Computer and Mathematical' => 30, 'Management' => 6, 'Legal' => 4),
        'states' => array('WA' => 25, 'CA' => 10, 'NY' => 5), 'wage_median' => 161300, 'wage_n' => 38)),
    'names' => array('TEST FIXTURE Widgets, Inc.' => 'test fixture widgets'),
);

/* ---------------------------------------------------------- hiring demand */
check(tit_hiring_demand_panel() === '', 'hiring demand rendered with no stored payload');
$GLOBALS['opts']['tit_reference_indeed_occupations'] = array('as_of' => '2026-09-25', 'countries' => 'junk');
check(tit_hiring_demand_panel() === '', 'hiring demand rendered from a malformed payload');

$GLOBALS['opts']['tit_reference_indeed_occupations'] = $indeed;
$html = tit_hiring_demand_panel();
check($html !== '', 'hiring demand did not render from a valid payload');
check(strpos($html, 'Hiring demand') !== false, 'hiring demand lost its heading');
check(strpos($html, 'Source:') !== false && strpos($html, 'Indeed Hiring Lab') !== false,
      'hiring demand has no "Source: Indeed Hiring Lab" line');
check(strpos($html, 'CC BY 4.0 (FIXTURE LICENCE)') !== false,
      'hiring demand printed a literal licence instead of the one stored in the payload');
check(strpos($html, 'as of 2026-09-25') !== false, 'hiring demand has no as-of line');
check(strpos($html, 'not the tracker') !== false, 'hiring demand does not say it is external');
check(substr_count($html, ' checked') === 6, 'hiring demand must preselect exactly six countries');
check(substr_count($html, 'name="tit-hd-c"') === 11, 'hiring demand must offer all eleven countries');
check(substr_count($html, '<polyline') === 6, 'hiring demand must draw six lines by default');
check(strpos($html, '<option value="Software Development">') !== false, 'occupation filter lost a category');
check(strpos($html, 'Three points') !== false, 'the 3-point fallback is not captioned as such');
check(strpos($html, 'data-api="https://example.test/blog/wp-json/talent/v1/reference/indeed_occupations"') !== false,
      'hiring demand lost its data-api config');
check(isset($GLOBALS['enqueued']['tit-reference']), 'reference.js was not enqueued');
// Weekly points, when the payload carries them, are drawn and captioned.
$w = $indeed;
$w['countries']['US']['total']['weekly'] = array(array('2026-09-11', 101.0), array('2026-09-18', 102.0), array('2026-09-25', 103.0), array('2026-09-04', 100.0));
$w['countries']['GB']['total']['weekly'] = $w['countries']['US']['total']['weekly'];
$GLOBALS['opts']['tit_reference_indeed_occupations'] = $w;
check(strpos(tit_hiring_demand_panel(), 'Weekly points') !== false, 'weekly series not captioned as weekly');
// Category selection: only countries that publish it draw a line.
list($lines, $missing) = tit_hd_selection($indeed, array('US', 'GB', 'FR'), 'Nursing');
check(count($lines) === 2 && $missing === array('France'), 'a country with no category series must be named, not drawn');

/* ---------------------------------------------------------- H-1B company */
$rows = array(array('company' => 'TEST FIXTURE Widgets, Inc.'));
check(tit_h1b_company_panel($rows) === '', 'the company box rendered with no stored company map');
$GLOBALS['opts']['tit_reference_h1b_lca'] = $h1b;
$GLOBALS['opts'][TIT_H1B_COMPANIES_OPTION] = $h1b_companies;
$co = tit_h1b_company_panel($rows);
check($co !== '', 'the company box did not render on an exact name match');
foreach (array('40' => 'certified count', '$161,300' => 'median offered wage',
               'Computer and Mathematical (30)' => 'top role', 'WA (25)' => 'top work state',
               'Fiscal year 2026 through Q3' => 'fiscal period', 'as of 2026-06-30' => 'as-of',
               'U.S. Department of Labor, OFLC LCA disclosure data' => 'DOL source line',
               'not a hire' => 'filings-not-hires label') as $needle => $what) {
    check(strpos($co, $needle) !== false, "the company box lost its {$what} ({$needle})");
}
foreach (array('TEST FIXTURE Widgets Inc', 'test fixture widgets, inc.', 'TEST FIXTURE Widgets, Inc') as $near) {
    check(tit_h1b_company_panel(array(array('company' => $near))) === '',
          "a near-miss name ({$near}) rendered H-1B figures: exact match only");
}
check(tit_h1b_company_panel(array()) === '', 'the company box rendered for no rows');

/* ---------------------------------------------------------- H-1B sponsors */
$sp = tit_h1b_sponsors_panel();
check($sp !== '', 'the sponsors section did not render');
check(strpos($sp, 'H-1B sponsors') !== false, 'the sponsors section lost its heading');
check(strpos($sp, '<script>alert(1)</script>') === false && strpos($sp, '&lt;script&gt;') !== false,
      'an employer name was not escaped');
check(substr_count($sp, '<tr>') === 3, 'the sponsors table should carry a header and two rows');
check(strpos($sp, '<option value="Healthcare Practitioners and Technical">') !== false, 'role filter lost a role');
check(strpos($sp, '<option value="TX">') !== false, 'state filter lost a state');
check(strpos($sp, 'type="search"') !== false, 'the sponsors table is not searchable');
check(strpos($sp, 'not a hire') !== false, 'the sponsors section does not say LCAs are filings, not hires');
check(strpos($sp, 'U.S. Department of Labor, OFLC LCA disclosure data') !== false
      && strpos($sp, 'as of 2026-06-30') !== false, 'the sponsors section lost its source or as-of line');
$GLOBALS['opts']['tit_reference_h1b_lca'] = array('as_of' => '2026-06-30', 'top_employers' => array());
check(tit_h1b_sponsors_panel() === '', 'the sponsors section rendered with no employers');

/* ---------------------------------------------------------- ingest split */
$GLOBALS['opts'] = array();
$body = $h1b + array('companies' => $h1b_companies['companies'], 'names' => $h1b_companies['names']);
tit_api_reference_ingest(new WP_REST_Request(array('source' => 'h1b_lca'), $body));
check(isset($GLOBALS['opts']['tit_reference_h1b_lca']['top_employers'])
      && !isset($GLOBALS['opts']['tit_reference_h1b_lca']['companies']),
      'the ingest kept the company map in the public option');
check(($GLOBALS['opts'][TIT_H1B_COMPANIES_OPTION]['names'] ?? null) === $h1b_companies['names'],
      'the ingest did not store the company map in its own option');

if ($failures) {
    fwrite(STDERR, "reference FAILED:\n  - " . implode("\n  - ", $failures) . "\n");
    exit(1);
}
echo "reference ok: hiring demand, the H-1B company box and the sponsors table render "
   . "with their sources, hide without data, and match names exactly.\n";
exit(0);
