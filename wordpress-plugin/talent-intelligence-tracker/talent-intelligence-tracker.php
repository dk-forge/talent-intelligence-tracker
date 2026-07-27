<?php
/**
 * Plugin Name: Talent Intelligence Tracker
 * Description: Hiring, leadership, compensation and location signals, sourced to primary documents.
 * Version: 1.4.1
 * Author: dk-forge
 * License: MIT
 *
 * SEPARATE PLUGIN, DELIBERATELY. This shares no code, no database table and no
 * REST namespace with the AI Layoff Tracker. WordPress fatals an entire plugin
 * on one bad require, and the layoff tracker is live — a shared file would mean
 * one mistake takes down both products.
 *
 * Everything here is prefixed tit_ / TIT_ and writes only to {prefix}tit_signals.
 * If you are editing this file and see the sibling's prefix anywhere, you are
 * in the wrong plugin.
 */

if (!defined('ABSPATH')) exit;

define('TIT_VERSION', '1.4.1');
define('TIT_PATH', plugin_dir_path(__FILE__));
define('TIT_URL', plugin_dir_url(__FILE__));
define('TIT_TABLE_SUFFIX', 'tit_signals');

/**
 * FTP deploys land files one at a time, so an include can be missing for a few
 * seconds mid-upload. A hard require of a not-yet-uploaded file fatals the
 * whole plugin on every request until the upload finishes. Guard each one and
 * carry on without it.
 */
function tit_require($relative) {
    $path = TIT_PATH . $relative;
    if (is_readable($path)) {
        require_once $path;
        return true;
    }
    return false;
}

tit_require('includes/db.php');
tit_require('includes/api.php');
tit_require('includes/shortcodes.php');
tit_require('includes/page.php');
tit_require('includes/company.php');
tit_require('includes/sources.php');

// Stub fallbacks so a partial upload degrades instead of fatalling.
if (!function_exists('tit_table_name')) {
    function tit_table_name() {
        global $wpdb;
        return $wpdb->prefix . TIT_TABLE_SUFFIX;
    }
}

/**
 * FTP deploys bypass activation hooks entirely, so nothing runs on "activate".
 * A version change is the only reliable signal that new code has landed: use it
 * to run migrations and flush caches on the next request.
 */
function tit_maybe_upgrade() {
    if (get_option('tit_installed_version') === TIT_VERSION) {
        return;
    }
    if (function_exists('tit_create_or_update_table')) {
        tit_create_or_update_table();
    }
    if (function_exists('tit_flush_caches')) {
        tit_flush_caches();
    }
    update_option('tit_installed_version', TIT_VERSION, false);
}
add_action('init', 'tit_maybe_upgrade', 1);

/**
 * The pipeline's write key. A wp-config.php constant wins over the stored
 * option, so the value can be set before this plugin has ever run.
 */
function tit_get_api_key() {
    if (defined('TALENT_API_KEY') && TALENT_API_KEY) {
        return (string) TALENT_API_KEY;
    }
    return (string) get_option('tit_api_key', '');
}

/**
 * Fails CLOSED. If no key is configured server-side, every keyed request is
 * rejected — an empty stored key must never match an empty header.
 */
function tit_api_permission($request) {
    $stored = tit_get_api_key();
    if ($stored === '') {
        return new WP_Error(
            'tit_key_missing',
            'API key is not configured. Set it under Talent Intel in wp-admin, '
            . 'or define TALENT_API_KEY in wp-config.php.',
            array('status' => 503)
        );
    }
    $provided = (string) $request->get_header('X-Talent-API-Key');
    if ($provided === '' || !hash_equals($stored, $provided)) {
        return new WP_Error('tit_forbidden', 'Invalid or missing API key.', array('status' => 403));
    }
    return true;
}

function tit_register_admin_page() {
    add_menu_page(
        'Talent Intelligence',
        'Talent Intel',
        'manage_options',
        'tit-settings',
        'tit_render_admin_page',
        'dashicons-chart-line',
        31
    );
}
add_action('admin_menu', 'tit_register_admin_page');

function tit_render_admin_page() {
    if (!current_user_can('manage_options')) return;

    $notice = '';

    // Setting the key here means wp-config.php is never touched. Editing a
    // core file over FTP risks 500-ing the whole blog; a stored option cannot.
    // This form is in wp-admin, which is never page-cached, so a nonce is safe
    // here (it would not be on a cached front-end page).
    if (isset($_POST['tit_set_key']) && check_admin_referer('tit_set_key')) {
        $submitted = trim((string) wp_unslash($_POST['tit_api_key_value']));
        if ($submitted === '') {
            $notice = '<div class="notice notice-error"><p>Key was empty. Nothing changed.</p></div>';
        } elseif (strlen($submitted) < 32) {
            $notice = '<div class="notice notice-error"><p>That looks too short to be the full key. Nothing changed.</p></div>';
        } else {
            update_option('tit_api_key', $submitted, false);
            $notice = '<div class="notice notice-success"><p>Key saved. It must match the WP_API_KEY secret in the GitHub repository.</p></div>';
        }
    }

    // Shown exactly once, here, immediately after generating. The stored value
    // is never displayed again, so a key that is not copied now has to be
    // regenerated. Telling someone to "copy it" without showing it, which is
    // what the first version of this did, is a trap.
    if (isset($_POST['tit_generate_key']) && check_admin_referer('tit_set_key')) {
        $generated = bin2hex(random_bytes(32));
        update_option('tit_api_key', $generated, false);
        $notice = '<div class="notice notice-success"><p><strong>New key generated.</strong> '
                . 'Copy it now, it is not shown again. Paste it into the '
                . '<code>WP_API_KEY</code> secret in the GitHub repository, or every '
                . 'write stays rejected.</p>'
                . '<p><input type="text" readonly onclick="this.select()" '
                . 'style="width:100%;max-width:640px;font-family:monospace;padding:6px;" '
                . 'value="' . esc_attr($generated) . '"></p></div>';
    }

    global $wpdb;
    $table = tit_table_name();
    $total = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$table} WHERE is_current = 1");
    $has_key = tit_get_api_key() !== '';
    $from_config = defined('TALENT_API_KEY') && TALENT_API_KEY;
    echo $notice;
    ?>
    <div class="wrap">
        <h1>Talent Intelligence Tracker</h1>
        <p><strong><?php echo esc_html(number_format_i18n($total)); ?></strong> current signals.</p>
        <p>Plugin version <?php echo esc_html(TIT_VERSION); ?>, table <code><?php echo esc_html($table); ?></code>.</p>

        <h2>Pipeline API key</h2>
        <?php if ($from_config) : ?>
            <p>A key is set in <code>wp-config.php</code>, which takes precedence
               over anything stored here.</p>
        <?php elseif ($has_key) : ?>
            <p>A key is stored. The pipeline must send the same value in the
               <code>X-Talent-API-Key</code> header, so it has to match the
               <code>WP_API_KEY</code> secret in the GitHub repository.</p>
        <?php else : ?>
            <p><strong>No key configured, so every write is rejected with 503.</strong>
               Paste the value of your <code>WP_API_KEY</code> GitHub secret below.
               Editing <code>wp-config.php</code> is not required.</p>
        <?php endif; ?>

        <form method="post" style="margin:14px 0 24px;">
            <?php wp_nonce_field('tit_set_key'); ?>
            <p>
                <label for="tit_api_key_value"><strong>Set the key</strong></label><br>
                <input type="password" id="tit_api_key_value" name="tit_api_key_value"
                       class="regular-text" autocomplete="off"
                       placeholder="paste the WP_API_KEY value">
                <button type="submit" name="tit_set_key" class="button button-primary">Save key</button>
            </p>
            <p class="description">
                Stored as an option, never written to a core file. The value is
                write-only here: it is not displayed back.
            </p>
            <p>
                <button type="submit" name="tit_generate_key" class="button"
                        onclick="return confirm('Generate a new key? The pipeline will be locked out until you copy it into the WP_API_KEY GitHub secret.');">
                    Generate a new key instead
                </button>
            </p>
        </form>

        <h2>Page</h2>
        <p>Put <code>[talent_intelligence_dashboard]</code> on the page at
           <code>/blog/talent-intelligence-tracker/</code>.</p>
    </div>
    <?php
}
