<?php
/**
 * Plugin Name: Talent Intelligence Tracker
 * Description: Hiring, leadership, compensation and location signals, sourced to primary documents.
 * Version: 1.0.0
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

define('TIT_VERSION', '1.0.0');
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
            'API key is not configured. Set TALENT_API_KEY in wp-config.php.',
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

    global $wpdb;
    $table = tit_table_name();
    $total = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$table} WHERE is_current = 1");
    $has_key = tit_get_api_key() !== '';
    ?>
    <div class="wrap">
        <h1>Talent Intelligence Tracker</h1>
        <p><strong><?php echo esc_html(number_format_i18n($total)); ?></strong> current signals.</p>
        <p>Plugin version <?php echo esc_html(TIT_VERSION); ?>, table <code><?php echo esc_html($table); ?></code>.</p>

        <h2>Pipeline API key</h2>
        <?php if ($has_key) : ?>
            <p>A key is configured. Requests must send it in the
               <code>X-Talent-API-Key</code> header.</p>
        <?php else : ?>
            <p><strong>No key configured.</strong> All writes are rejected with 503.
               Add <code>define('TALENT_API_KEY', '...');</code> to
               <code>wp-config.php</code>, using the same value as the
               <code>WP_API_KEY</code> secret in the GitHub repository.</p>
        <?php endif; ?>

        <h2>Page</h2>
        <p>Put <code>[talent_intelligence_dashboard]</code> on the page at
           <code>/blog/talent-intelligence-tracker/</code>.</p>
    </div>
    <?php
}
