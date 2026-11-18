<?php
/**
 * The update log: /talent-intelligence-tracker/update-log/
 *
 * What changed in the tracker, newest first, and the release each change
 * shipped in. Corrections to individual figures live on the corrections page;
 * this is the product's own change history.
 *
 * The entries are read from data/update-log.json, which is GENERATED from git
 * by generate_update_log.py (one entry per plugin release, from the commit that
 * bumped TIT_VERSION). The deployed plugin has no repository, so this file
 * never reads git. tests/test_update_log.py fails when the newest entry is not
 * the shipped version, so a release cannot go out without its line here.
 *
 * Routed like the sources and corrections pages (rewrite rule, query var,
 * template redirect), and styled by the same classes, so it needs no CSS of
 * its own.
 */

if (!defined('ABSPATH')) exit;

const TIT_UPDATE_LOG_PATH = 'talent-intelligence-tracker/update-log';

function tit_update_log_rewrite() {
    add_rewrite_rule('^' . TIT_UPDATE_LOG_PATH . '/?$', 'index.php?tit_update_log=1', 'top');
}
add_action('init', 'tit_update_log_rewrite');

function tit_update_log_query_var($vars) {
    $vars[] = 'tit_update_log';
    return $vars;
}
add_filter('query_vars', 'tit_update_log_query_var');

function tit_update_log_url() {
    return home_url('/' . TIT_UPDATE_LOG_PATH . '/');
}

/** The page ships no script: it is a list. */
function tit_update_log_needs_no_js($needs) {
    return get_query_var('tit_update_log') ? false : $needs;
}
add_filter('tit_route_needs_js', 'tit_update_log_needs_no_js');

/**
 * Pure: validate and normalise decoded JSON into entries, newest first as
 * stored. A malformed row is dropped rather than rendered half-empty; a file
 * that yields nothing returns array() and the page says so.
 */
function tit_update_log_normalise($decoded) {
    $rows = is_array($decoded) ? ($decoded['entries'] ?? array()) : array();
    $out = array();
    foreach ((array) $rows as $r) {
        if (!is_array($r)) continue;
        $version = (string) ($r['version'] ?? '');
        $date    = (string) ($r['date'] ?? '');
        $text    = trim((string) ($r['text'] ?? ''));
        if (!preg_match('/^\d+\.\d+\.\d+$/', $version)) continue;
        if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $date)) continue;
        if ($text === '') continue;
        $out[] = array(
            'version' => $version,
            'date'    => $date,
            'tag'     => (($r['tag'] ?? '') === 'Fix') ? 'Fix' : 'Change',
            'text'    => $text,
        );
    }
    return $out;
}

function tit_update_log_entries() {
    $path = TIT_PATH . 'data/update-log.json';
    if (!is_readable($path)) return array();
    $decoded = json_decode((string) file_get_contents($path), true);
    return tit_update_log_normalise($decoded);
}

function tit_update_log_template() {
    if (!get_query_var('tit_update_log')) return;
    tit_update_log_render(tit_update_log_entries());
    exit;
}
add_action('template_redirect', 'tit_update_log_template');

function tit_update_log_render($entries) {
    // Block theme: never get_header() directly. See tit_render_header().
    if (function_exists('tit_render_header')) tit_render_header(); else get_header();
    ?>
    <div class="tit-wrap tit-corrections tit-update-log" id="tit-update-log">
      <?php if (function_exists('tit_breadcrumbs_html')) echo tit_breadcrumbs_html(tit_breadcrumbs_section_trail('Update log', tit_update_log_url())); ?>

      <h1>Update log</h1>
      <p class="tit-page-intro">
        What changed in the Talent Intelligence Tracker, newest first, and the
        release each change shipped in. Corrections to individual figures are
        in the
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/corrections/')); ?>">corrections log</a>;
        how a record is made is on the
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/methodology/')); ?>">methodology page</a>.
      </p>

      <?php if (!$entries) : ?>
        <p class="tit-note">The release list could not be read just now. Try again shortly.</p>
      <?php else : ?>
        <div class="tit-stats">
          <div class="tit-stat">
            <span class="tit-n"><?php echo esc_html(number_format_i18n(count($entries))); ?></span>
            <span class="tit-l">releases</span>
          </div>
          <div class="tit-stat">
            <span class="tit-n"><?php echo esc_html($entries[0]['version']); ?></span>
            <span class="tit-l">current version</span>
          </div>
        </div>

        <?php foreach ($entries as $e) : ?>
          <div class="tit-correction" id="v-<?php echo esc_attr(str_replace('.', '-', $e['version'])); ?>">
            <p class="tit-meta">
              <time datetime="<?php echo esc_attr($e['date']); ?>"><?php
                echo esc_html(date_i18n('j F Y', strtotime($e['date'])));
              ?></time>
              <span aria-hidden="true">&middot;</span>
              version <?php echo esc_html($e['version']); ?>
              <span aria-hidden="true">&middot;</span>
              <span class="tit-conf <?php echo $e['tag'] === 'Fix' ? 'tit-c-degraded' : 'tit-c-verified'; ?>"><?php
                echo esc_html($e['tag']);
              ?></span>
            </p>
            <p><?php echo esc_html($e['text']); ?></p>
          </div>
        <?php endforeach; ?>
      <?php endif; ?>

      <p class="tit-cite">
        This list is generated from the release history of the tracker's code.
        Data changes that are not a new release, such as a new source starting
        to be read, appear on the
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">sources page</a>.
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Back to the tracker</a>
      </p>
    </div>
    <?php
    if (function_exists('tit_render_footer')) tit_render_footer('update_log'); else get_footer();
}

function tit_update_log_title($title) {
    return get_query_var('tit_update_log')
        ? 'Update log · Talent Intelligence Tracker'
        : $title;
}
add_filter('pre_get_document_title', 'tit_update_log_title');

function tit_update_log_head() {
    if (!get_query_var('tit_update_log')) return;
    if (!function_exists('tit_head_description')) return;
    tit_head_description(
        'What changed in the Talent Intelligence Tracker, newest first, with the '
        . 'release each change shipped in.'
    );
    echo '<link rel="canonical" href="' . esc_url(tit_update_log_url()) . '" />' . "\n";
}
add_action('wp_head', 'tit_update_log_head', 1);
