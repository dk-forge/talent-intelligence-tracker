<?php
/**
 * The header submenu under "Talent Intelligence Tracker", executed.
 *
 * WHY THIS FILE EXISTS. includes/nav_submenu.php rewrites the site's
 * `wp_navigation` post, which is the one artefact on this site both trackers
 * write. Reading it as text would prove nothing about the two properties that
 * matter -- that a second run changes nothing, and that a route which stops
 * being offered loses its menu item -- because both are about what the code
 * DOES to a document, twice.
 *
 * So this loads the real include against a WordPress shim and drives it. The
 * four route files are loaded too, unmodified, so the labels come from the
 * <h1> each route actually renders and nothing here holds a copy of one.
 *
 * parse_blocks() and serialize_blocks() are core's, reimplemented here because
 * the shim has no WordPress. serialize_block()'s real behaviour is reproduced
 * exactly, in particular that it drives its output off innerContent and
 * substitutes an innerBlock for each null it finds -- a container handed an
 * empty innerContent serialises with every child dropped. --round-trip proves
 * this parser agrees with core's grammar on core-shaped markup, so the rest is
 * not measuring a fiction.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/nav_submenu.php
 */

define('ABSPATH', __DIR__);
$tit_plugin = __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/';

function plugin_dir_path($file) { return dirname($file) . '/'; }
function plugin_dir_url($file) { return 'https://example.test/plugin/'; }
define('MINUTE_IN_SECONDS', 60);
define('HOUR_IN_SECONDS', 3600);
define('DAY_IN_SECONDS', 86400);
define('ARRAY_A', 'ARRAY_A');
define('OBJECT', 'OBJECT');

$GLOBALS['tit_options'] = array();
$GLOBALS['tit_writes'] = array();

function add_action($h, $f = null, $p = 10, $a = 1) {}
function add_filter($h, $f = null, $p = 10, $a = 1) {}
function remove_filter($h, $f = null, $p = 10) {}
function add_shortcode($t, $f) {}
function apply_filters($h, $v) { return $v; }
function do_action($h) {}
function has_action($h) { return false; }
function register_rest_route($ns, $route, $args) {}
function add_rewrite_rule($r, $q, $w = 'bottom') {}
function add_rewrite_tag($t, $r) {}
function flush_rewrite_rules($hard = true) {}
function register_activation_hook($f, $c) {}
function wp_next_scheduled($h) { return false; }
function wp_schedule_event($t, $r, $h) {}
function is_admin() { return false; }
function get_query_var($k, $d = '') { return $d; }
function home_url($path = '') { return 'https://example.test/blog' . $path; }
function admin_url($path = '') { return 'https://example.test/blog/wp-admin/' . $path; }
function rest_url($p = '') { return 'https://example.test/blog/wp-json/' . $p; }
function site_url($p = '') { return 'https://example.test/blog' . $p; }
function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url_raw($s) { return (string) $s; }
function esc_js($s) { return (string) $s; }
function esc_html__($s, $d = null) { return $s; }
function __($s, $d = null) { return $s; }
function wp_json_encode($v, $flags = 0) { return json_encode($v, $flags); }
function sanitize_title($s) { return strtolower(preg_replace('/[^a-z0-9]+/i', '-', (string) $s)); }
function sanitize_text_field($s) { return trim(strip_tags((string) $s)); }
function wp_kses_post($s) { return $s; }
function number_format_i18n($n, $d = 0) { return number_format((float) $n, $d); }
function get_option($k, $d = false) { return array_key_exists($k, $GLOBALS['tit_options']) ? $GLOBALS['tit_options'][$k] : $d; }
function update_option($k, $v, $a = null) { $GLOBALS['tit_options'][$k] = $v; return true; }
function add_option($k, $v, $x = '', $a = null) {
    if (array_key_exists($k, $GLOBALS['tit_options'])) return false;
    $GLOBALS['tit_options'][$k] = $v; return true;
}
function delete_option($k) { unset($GLOBALS['tit_options'][$k]); return true; }
function get_transient($k) { return false; }
function set_transient($k, $v, $t = 0) { return true; }
function delete_transient($k) { return true; }
function get_posts($args) { return $GLOBALS['tit_menus']; }
function wp_slash($v) { return is_string($v) ? addslashes($v) : $v; }
function wp_unslash($v) { return is_string($v) ? stripslashes($v) : $v; }
/*
 * wp_update_post() EXPECTS SLASHED DATA, and this stub used not to say so.
 * Core hands $postarr to wp_insert_post(), whose first statement is
 * `$postarr = wp_unslash( $postarr );`. Modelling that is the whole point:
 * without it the stub accepted the raw serializer output that production
 * silently stripped, and every ampersand in the live header nav read
 * "u0026" for weeks with this harness green. A stub gentler than production
 * is not a test.
 */
function wp_update_post($args) {
    $content = wp_unslash((string) $args['post_content']);
    foreach ($GLOBALS['tit_menus'] as $m) {
        if ((int) $m->ID === (int) $args['ID']) $m->post_content = $content;
    }
    $GLOBALS['tit_writes'][] = (int) $args['ID'];
    return $args['ID'];
}
function wp_enqueue_style() {}
function wp_enqueue_script() {}
function wp_register_style() {}
function wp_register_script() {}
function get_page_by_path($p, $o = null, $t = 'page') { return null; }
function current_user_can($c) { return false; }
function wp_doing_ajax() { return false; }
function wp_upload_dir() { return array('basedir' => sys_get_temp_dir(), 'baseurl' => 'https://example.test/u'); }

/* ---- core's block grammar, both directions ---- */
function parse_blocks($content) {
    $tok = '/<!--\s+(\/)?wp:([a-z][a-z0-9_-]*\/?[a-z0-9_-]*)\s*(\{.*?\})?\s*(\/)?-->/s';
    preg_match_all($tok, $content, $m, PREG_OFFSET_CAPTURE | PREG_SET_ORDER);
    $stack = array(array('blockName' => null, 'attrs' => array(), 'innerBlocks' => array(),
                         'innerHTML' => '', 'innerContent' => array()));
    $pos = 0;
    foreach ($m as $t) {
        $at = $t[0][1];
        $text = substr($content, $pos, $at - $pos);
        if (trim($text) !== '') $stack[count($stack) - 1]['innerContent'][] = $text;
        $pos = $at + strlen($t[0][0]);
        $name = strpos($t[2][0], '/') === false ? 'core/' . $t[2][0] : $t[2][0];
        $attrs = (isset($t[3]) && $t[3][0] !== '') ? json_decode($t[3][0], true) : array();
        if ($t[1][0] === '/') {
            $done = array_pop($stack);
            $top = count($stack) - 1;
            $stack[$top]['innerBlocks'][] = $done;
            $stack[$top]['innerContent'][] = null;
        } elseif (isset($t[4]) && $t[4][0] === '/') {
            $top = count($stack) - 1;
            $stack[$top]['innerBlocks'][] = array('blockName' => $name, 'attrs' => $attrs,
                'innerBlocks' => array(), 'innerHTML' => '', 'innerContent' => array());
            $stack[$top]['innerContent'][] = null;
        } else {
            $stack[] = array('blockName' => $name, 'attrs' => $attrs, 'innerBlocks' => array(),
                             'innerHTML' => '', 'innerContent' => array());
        }
    }
    return $stack[0]['innerBlocks'];
}

/*
 * core's serialize_block_attributes(). The escaping is not cosmetic: an
 * attribute lives inside an HTML comment, so a raw `&`, `<`, `>` or `--` in a
 * label could close or corrupt the delimiter. Core writes each as a JSON
 * \uXXXX escape, and it is that BACKSLASH which wp_unslash() used to eat.
 * The old stub encoded plain and so could not produce the bytes that break.
 */
function serialize_block_attributes($attrs) {
    $json = json_encode($attrs, JSON_HEX_TAG | JSON_HEX_AMP
                              | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    return preg_replace('/--/', '\\\\u002d\\\\u002d', $json);
}

function get_comment_delimited_block_content($name, $attrs, $content) {
    $short = strpos($name, 'core/') === 0 ? substr($name, 5) : $name;
    $json = $attrs ? serialize_block_attributes($attrs) . ' ' : '';
    if ($content === '') return '<!-- wp:' . $short . ' ' . $json . '/-->';
    return '<!-- wp:' . $short . ' ' . $json . '-->' . $content . '<!-- /wp:' . $short . ' -->';
}

function serialize_block($block) {
    $content = '';
    $i = 0;
    foreach ($block['innerContent'] as $chunk) {
        $content .= is_string($chunk) ? $chunk : serialize_block($block['innerBlocks'][$i++]);
    }
    if (empty($block['blockName'])) return $content;
    return get_comment_delimited_block_content($block['blockName'], $block['attrs'], $content);
}

function serialize_blocks($blocks) {
    $out = '';
    foreach ($blocks as $b) $out .= serialize_block($b);
    return $out;
}

/* ---- the plugin, unmodified ---- */
define('TIT_VERSION', 'test');
define('TIT_PATH', $tit_plugin);
define('TIT_URL', 'https://example.test/plugin/');
require $tit_plugin . 'includes/page.php';
require $tit_plugin . 'includes/sources.php';
require $tit_plugin . 'includes/places.php';
require $tit_plugin . 'includes/recall.php';
require $tit_plugin . 'includes/press.php';
require $tit_plugin . 'includes/nav_submenu.php';

$PARENT = 'https://example.test/blog/talent-intelligence-tracker/';

/*
 * The menu, as block markup. Reconstructed from the LIVE header nav captured
 * on 2026-08-13: same items, same order, "Blog" as the one existing submenu,
 * both trackers flat. --round-trip holds this file's parser to it.
 */
function tit_test_menu($parent) {
    return '<!-- wp:navigation-link {"label":"Pricing","type":"custom","url":"/pricing","kind":"custom","isTopLevelLink":true} /-->'
        . '<!-- wp:navigation-submenu {"label":"Blog","type":"custom","url":"/blog/","kind":"custom","isTopLevelItem":true} -->'
        . '<!-- wp:navigation-link {"label":"Resume Writing","type":"custom","url":"/blog/category/resume-writing/","kind":"custom"} /-->'
        . '<!-- wp:navigation-link {"label":"Cover Letters","type":"custom","url":"/blog/category/cover-letters/","kind":"custom"} /-->'
        /* THE AMPERSAND IS THE POINT, and it is on the live menu. This is a
           child the plugin does not own and promises to carry through
           untouched, so it is exactly the label the unslashed write corrupted
           on production. Without one such item in this fixture the round trip
           never emits a \uXXXX escape and the defect is unreachable. */
        . '<!-- wp:navigation-link {"label":"Salary \u0026 Negotiation","type":"custom","url":"/blog/category/salary-negotiation/","kind":"custom"} /-->'
        . '<!-- /wp:navigation-submenu -->'
        . '<!-- wp:navigation-link {"label":"AI Layoff Tracker","type":"custom","url":"https://example.test/blog/ai-layoff-tracker/","kind":"custom","isTopLevelLink":true} /-->'
        . '<!-- wp:navigation-link {"label":"Talent Intelligence Tracker","type":"custom","url":"' . $parent . '","kind":"custom","isTopLevelLink":true} /-->';
}

function tit_test_reset($content) {
    $GLOBALS['tit_options'] = array();
    $GLOBALS['tit_writes'] = array();
    $GLOBALS['tit_menus'] = array((object) array('ID' => 72, 'post_content' => $content));
}

function tit_test_sync() {
    unset($GLOBALS['tit_options']['tit_nav_submenu_synced']);
    $GLOBALS['tit_options']['tit_nav_submenu_last_try'] = 0;
    tit_nav_submenu_sync();
}

function tit_test_children() {
    $item = tit_nav_find(parse_blocks($GLOBALS['tit_menus'][0]->post_content),
                         tit_nav_parent_url());
    if (!$item) return null;
    $out = array('block' => $item['blockName'], 'kids' => array());
    foreach ($item['innerBlocks'] as $c) {
        $out['kids'][] = array('url' => $c['attrs']['url'], 'label' => $c['attrs']['label']);
    }
    return $out;
}

$arg = isset($argv[1]) ? $argv[1] : '';

if ($arg === '--desired') {
    echo json_encode(tit_nav_desired_children());
    exit(0);
}

if ($arg === '--round-trip') {
    echo json_encode(serialize_blocks(parse_blocks(tit_test_menu($PARENT))) === tit_test_menu($PARENT));
    exit(0);
}

if ($arg === '--twice') {
    tit_test_reset(tit_test_menu($PARENT));
    tit_test_sync();
    $one = $GLOBALS['tit_menus'][0]->post_content;
    $writes_one = count($GLOBALS['tit_writes']);
    tit_test_sync();                       // a second request, nothing else cleared
    echo json_encode(array(
        'one' => $one, 'two' => $GLOBALS['tit_menus'][0]->post_content,
        'writes_one' => $writes_one, 'writes_total' => count($GLOBALS['tit_writes']),
        'item' => tit_test_children(),
        'synced' => get_option('tit_nav_submenu_synced'),
    ));
    exit(0);
}

if ($arg === '--retired') {
    // A route that is no longer offered, already in the menu.
    $stale = $PARENT . 'corrections/';
    $menu = str_replace(
        '"url":"' . $PARENT . '","kind":"custom","isTopLevelLink":true} /-->',
        '"url":"' . $PARENT . '","kind":"custom","isTopLevelItem":true} -->'
        . '<!-- wp:navigation-link {"label":"Corrections","type":"custom","url":"' . $stale . '","kind":"custom"} /-->'
        . '<!-- wp:navigation-link {"label":"Contact","type":"custom","url":"https://example.test/blog/contact/","kind":"custom"} /-->'
        . '<!-- /wp:navigation-submenu -->',
        tit_test_menu($PARENT));
    tit_test_reset($menu);
    tit_test_sync();
    echo json_encode(array('item' => tit_test_children(),
                           'content' => $GLOBALS['tit_menus'][0]->post_content));
    exit(0);
}

if ($arg === '--not-in-any-menu') {
    tit_test_reset('<!-- wp:navigation-link {"label":"Pricing","type":"custom","url":"/pricing","kind":"custom"} /-->');
    tit_test_sync();
    echo json_encode(array('writes' => count($GLOBALS['tit_writes']),
                           'content' => $GLOBALS['tit_menus'][0]->post_content));
    exit(0);
}

/*
 * Every label in the menu after a sync, ours and the owner's alike.
 * The owner's are the ones the unslashed write corrupted, so they are the
 * ones worth reading back.
 */
if ($arg === '--labels') {
    $seed = $argc > 2 && $argv[2] === 'mangled'
        /* A menu ALREADY corrupted on production: the backslash is gone and
           the label is the literal text "u0026". Nothing re-derives this, so
           only a repair can bring it back. */
        ? str_replace('Salary \\u0026 Negotiation', 'Salary u0026 Negotiation',
                      tit_test_menu($PARENT))
        : tit_test_menu($PARENT);
    tit_test_reset($seed);
    tit_test_sync();
    $labels = array();
    $walk = function ($blocks) use (&$walk, &$labels) {
        foreach ($blocks as $b) {
            if (isset($b['attrs']['label'])) $labels[] = $b['attrs']['label'];
            if (!empty($b['innerBlocks'])) $walk($b['innerBlocks']);
        }
    };
    $walk(parse_blocks($GLOBALS['tit_menus'][0]->post_content));
    echo json_encode($labels);
    exit(0);
}

if ($arg === '--serialised') {
    tit_test_reset(tit_test_menu($PARENT));
    tit_test_sync();
    echo $GLOBALS['tit_menus'][0]->post_content;
    exit(0);
}

/* ---- default: self-check, non-zero on failure ---- */
$fail = array();
function tit_check($ok, $why) { if (!$ok) $GLOBALS['fail'][] = $why; }

tit_check(serialize_blocks(parse_blocks(tit_test_menu($PARENT))) === tit_test_menu($PARENT),
          "this harness's block parser does not agree with core's grammar, so "
          . "everything below is measuring a fiction");

$desired = tit_nav_desired_children();
tit_check(count($desired) === 4,
          'tit_nav_desired_children() offers ' . count($desired) . ' routes, not the four');
foreach ($desired as $c) {
    tit_check($c['label'] !== '', 'a menu label is empty');
    tit_check(strpos($c['label'], "\xe2\x80\x94") === false && strpos($c['label'], "\xe2\x80\x93") === false,
              'the menu label "' . $c['label'] . '" carries a dash the UI copy rule forbids');
}

tit_test_reset(tit_test_menu($PARENT));
tit_test_sync();
$one = $GLOBALS['tit_menus'][0]->post_content;
$writes_one = count($GLOBALS['tit_writes']);
tit_test_sync();
tit_check($writes_one === 1, 'the first registration wrote ' . $writes_one . ' times, not once');
tit_check(count($GLOBALS['tit_writes']) === 1,
          'registering twice wrote the menu ' . count($GLOBALS['tit_writes'])
          . ' times; the second run must find the item correct and write nothing');
tit_check($one === $GLOBALS['tit_menus'][0]->post_content,
          'the second registration changed the stored menu');

tit_check(strpos($GLOBALS['tit_menus'][0]->post_content, 'Salary \\u0026 Negotiation') !== false,
          "the ampersand in a menu label this plugin does not own did not "
          . "survive the write: wp_update_post() unslashes what it is given, "
          . "so serialize_blocks() output must be wp_slash()ed first or every "
          . "&, <, > and -- in the whole menu loses its backslash and renders "
          . "as the literal text u0026");

$item = tit_test_children();
tit_check($item['block'] === 'core/navigation-submenu',
          'the tracker item is still a ' . $item['block'] . ', so it has no children');
$urls = array();
foreach ($item['kids'] as $k) $urls[] = $k['url'];
tit_check(count($urls) === count(array_unique($urls)),
          'a destination appears twice in the submenu after two registrations');
foreach ($desired as $c) {
    tit_check(strpos($GLOBALS['tit_menus'][0]->post_content, $c['url']) !== false,
              $c['url'] . ' is missing from the SERIALISED menu. The block array may '
              . 'hold it while innerContent does not, and innerContent is what core writes out');
}
tit_check(strpos($GLOBALS['tit_menus'][0]->post_content, '"label":"AI Layoff Tracker"') !== false,
          "the sibling tracker's menu item was lost; both plugins write this one post");

if ($fail) {
    fwrite(STDERR, "FAIL\n  " . implode("\n  ", $fail) . "\n");
    exit(1);
}
echo "ok: the four routes are under the tracker, twice over\n";
