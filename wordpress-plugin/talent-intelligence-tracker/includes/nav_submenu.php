<?php
/**
 * THE SECONDARY ROUTES ARE IN THE SITE NAVIGATION, UNDER THE TRACKER.
 *
 * WHY THIS FILE EXISTS. /sources/, /places/, /recall/ and /press/ have been
 * live for months and the site header carried "Talent Intelligence Tracker"
 * as one flat item with nothing under it. A reader who wants to know where the
 * data comes from, or what we do not cover, has to already be on the dashboard
 * and find a link in a footer. The sibling tracker measured the same defect
 * one level down: its press route sat 13,252px into the page on a desktop and
 * 31,707px on a phone.
 *
 * WHAT THE HEADER MENU ACTUALLY IS. Twenty Twenty-Five is a block theme, so
 * the header menu is a core/navigation block whose items live as block markup
 * in the post_content of a `wp_navigation` post ("ATR Main Menu"), NOT in the
 * classic nav_menu taxonomy. Read live 2026-08-13 its top level is Pricing,
 * Blog (a submenu of six), AI Layoff Tracker, Talent Intelligence Tracker,
 * with both trackers as flat `core/navigation-link` blocks. This converts ours
 * to a `core/navigation-submenu` with the four routes inside it, through
 * parse_blocks()/serialize_blocks() so the shape is core's own. If no
 * wp_navigation post carries our URL, nothing happens and it retries: a menu
 * we are not in is not a menu we may create.
 *
 * THE LABELS HAVE ONE AUTHOR AND IT IS THE ROUTE. Each label is
 * tit_route_heading() off the file that renders the destination, taken from
 * the <h1> that carries data-tit-route-heading. A heading that cannot be read
 * verbatim yields '', and '' aborts the whole sync rather than naming a menu
 * item from a guess. Rename a route's <h1> and the menu follows on the next
 * deploy. The attribute exists because two of these files render more than one
 * <h1> -- recall.php has an empty-state "Measured recall" above the real "How
 * much do we miss?", and places.php has the per-cell heading above the
 * directory's -- so "the first h1" would have quietly labelled the menu with
 * the wrong one.
 *
 * NOTHING IS ORPHANED. The child set is rebuilt from TIT_NAV_ROUTES, and a
 * route only earns an item if its include actually loaded (function_exists on
 * its own url helper) AND its heading reads. Any existing child under the
 * tracker path that is not in the rebuilt set is DROPPED, so a retired or
 * renamed route's item goes with it. Children pointing anywhere else are left
 * as the owner left them.
 *
 * RETRY UNTIL VERIFIED, never one-shot. FTP deploys bypass every WordPress
 * hook and land files one at a time, which is the same reason
 * tit_ensure_dashboard_page() is shaped this way.
 *
 * IT SHARES THE MENU WITH THE SIBLING TRACKER, WHICH IS WHY THE LOCK IS NOT
 * NAMED AFTER THIS PLUGIN. "AI Layoff Tracker" is the item next to ours in the
 * same wp_navigation post and its plugin does exactly this on the same `init`.
 * Two plugins that each read the post, edit their own subtree and write the
 * whole post back will, on an unlucky interleave, drop the other's children --
 * and each would have verified its own write and set its own done-flag, so
 * neither would ever retry. TIT_NAV_LOCK_OPTION is therefore a deliberate
 * cross-plugin convention: the same literal option name is used by the layoff
 * tracker's ALT_NAV_LOCK_OPTION. add_option() is the lock because
 * wp_options.option_name is UNIQUE, so exactly one of two concurrent callers
 * gets true out of it; a transient is a cache read and races.
 *
 * The two repos share no code by design, so this file and the sibling's
 * includes/nav-submenu.php are deliberate parallel implementations. The ONE
 * thing that must not diverge is the lock name.
 */

if (!defined('ABSPATH')) exit;

/** Must match the sibling plugin's ALT_NAV_LOCK_OPTION exactly. */
const TIT_NAV_LOCK_OPTION = 'atr_nav_children_lock';
const TIT_NAV_LOCK_TTL    = 60;
const TIT_NAV_RETRY_EVERY = 300;

/**
 * THE FOUR, IN THE ORDER A READER NEEDS THEM.
 *
 * Where did this come from, what does it cover, how much does it miss, what
 * may I quote. Each entry is the file that renders the route, the function
 * that returns its URL, and the constant naming its path, so this list holds
 * no copy of a heading and no copy of a URL.
 *
 * ONE READER-FACING ROUTE IS DELIBERATELY OUT.
 *
 *   /corrections/   a report-an-error form. It is reached at the moment a
 *                   reader spots something wrong, and every data surface
 *                   already links to it from where that happens. A permanent
 *                   header slot for it would push one of the four below out.
 *
 * The company pages and the individual place cells are out for a different
 * reason: there are thousands of them and they are the data, not the
 * explanation of it. /places/ is the directory that reaches them.
 *
 * A submenu of five is a list to be read; a submenu of four is a route. A
 * fifth earns its place by displacing one of these.
 */
function tit_nav_routes() {
    return array(
        array('file' => 'includes/sources.php',     'url_fn' => 'tit_sources_url'),
        array('file' => 'includes/places.php',      'url_fn' => 'tit_places_url'),
        array('file' => 'includes/recall.php',      'url_fn' => 'tit_recall_url'),
        array('file' => 'includes/press.php',       'url_fn' => 'tit_press_url'),
    );
}

/**
 * The route's own name, read from the <h1> that renders it.
 *
 * REFUSES TO GUESS, for the same reasons the sibling's alt_template_heading()
 * does. It takes the h1 carrying data-tit-route-heading and returns '' unless
 * the text inside is plain: no nested markup, no PHP. A route that grows a
 * dynamic heading, or a file half uploaded when a hook fires mid-deploy,
 * yields nothing, and every caller treats nothing as "not yet" rather than as
 * a name.
 */
function tit_route_heading($file) {
    $path = TIT_PATH . $file;
    if (!is_readable($path)) return '';
    $src = file_get_contents($path);
    if ($src === false || $src === '') return '';
    if (!preg_match('#<h1[^>]*\bdata-tit-route-heading\b[^>]*>(.*?)</h1>#si', $src, $m)) return '';
    $inner = $m[1];
    /* A '<' catches both nested markup and an opening PHP tag; a closing PHP
       tag catches a heading that steps back out to HTML. Either means the
       rendered text is not this string. (A block comment on purpose: a closing
       PHP tag inside a // comment ends PHP mode.) */
    if (strpos($inner, '<') !== false || strpos($inner, '?' . '>') !== false) return '';
    $text = html_entity_decode($inner, ENT_QUOTES, 'UTF-8');
    return trim(preg_replace('/\s+/u', ' ', $text));
}

/** One spelling for a URL, so "is this the same destination" is answerable. */
function tit_nav_normalize_url($url) {
    $url = trim((string) $url);
    if ($url === '') return '';
    $url = preg_replace('#^https?://#i', '', $url);
    $url = preg_replace('#^www\.#i', '', $url);
    return strtolower(rtrim($url, '/'));
}

/** The dashboard's own URL, normalised. */
function tit_nav_parent_url() {
    return tit_nav_normalize_url(home_url('/' . TIT_PAGE_SLUG . '/'));
}

/**
 * The children we intend the menu to carry, in order, each
 * array('url' => the route's own URL, 'label' => the <h1> it renders).
 *
 * Returns an EMPTY array for "not ready", never a partial set: a half-built
 * submenu on the live menu is worse than none, and the caller treats empty as
 * "try again on the next request". Not-ready means an include that has not
 * landed yet (its url helper is undefined) or a heading that cannot be read.
 */
function tit_nav_desired_children() {
    $out = array();
    foreach (tit_nav_routes() as $route) {
        if (!function_exists($route['url_fn'])) return array();   // FTP race
        $label = tit_route_heading($route['file']);
        if ($label === '') return array();                        // never a guess
        $url = call_user_func($route['url_fn']);
        if (!is_string($url) || $url === '') return array();
        $out[] = array('url' => $url, 'label' => $label);
    }
    return $out;
}

/** The desired set as normalised-url => label, which is what verification compares. */
function tit_nav_desired_map($desired) {
    $map = array();
    foreach ($desired as $child) {
        $map[tit_nav_normalize_url($child['url'])] = $child['label'];
    }
    return $map;
}

/**
 * Is this block a menu entry pointing at $url?
 *
 * Both block names are accepted because the item may already have been
 * converted by an earlier run (navigation-submenu) or may still be flat
 * (navigation-link). That is what makes the second run a no-op.
 */
function tit_nav_block_points_at($block, $url) {
    $name = isset($block['blockName']) ? $block['blockName'] : '';
    if ($name !== 'core/navigation-link' && $name !== 'core/navigation-submenu') return false;
    $attrs = isset($block['attrs']) && is_array($block['attrs']) ? $block['attrs'] : array();
    if (!isset($attrs['url'])) return false;
    return tit_nav_normalize_url($attrs['url']) === $url;
}

/**
 * The child blocks our item should end up with, given the ones it has.
 *
 * REBUILD OURS, KEEP THEIRS. Every existing child whose URL sits under the
 * tracker path is discarded and re-derived, which is what makes a retired
 * route's item disappear rather than linger on a 404. Every other child is
 * carried through untouched and in order: the owner may have added one and
 * this plugin has no standing to decide about it.
 */
function tit_nav_rebuild_children($existing, $desired, $parent_url) {
    $kept = array();
    foreach ((array) $existing as $child) {
        $attrs = isset($child['attrs']) && is_array($child['attrs']) ? $child['attrs'] : array();
        $url = isset($attrs['url']) ? tit_nav_normalize_url($attrs['url']) : '';
        // Ours to manage: anything below the dashboard. Note the '/' so the
        // parent's own URL is not read as one of its own children.
        if ($url !== '' && $parent_url !== '' && strpos($url, $parent_url . '/') === 0) continue;
        $kept[] = $child;
    }

    $mine = array();
    foreach ($desired as $child) {
        $mine[] = array(
            'blockName'    => 'core/navigation-link',
            'attrs'        => array(
                'label' => $child['label'],
                'type'  => 'custom',
                'kind'  => 'custom',
                'url'   => $child['url'],
            ),
            'innerBlocks'  => array(),
            'innerHTML'    => '',
            'innerContent' => array(),
        );
    }
    return array_merge($mine, $kept);
}

/**
 * The item, converted to a submenu carrying $children.
 *
 * innerContent IS NOT DECORATION. serialize_block() walks innerContent and
 * substitutes the next innerBlock for each null it finds; it never reads
 * innerBlocks directly. A submenu handed innerContent => array() therefore
 * serialises with its children silently dropped, which is a menu item that
 * gained a toggle and lost everything behind it. One null per child.
 */
function tit_nav_as_submenu($block, $children) {
    $attrs = isset($block['attrs']) && is_array($block['attrs']) ? $block['attrs'] : array();
    unset($attrs['isTopLevelLink']);
    $attrs['isTopLevelItem'] = true;
    return array(
        'blockName'    => 'core/navigation-submenu',
        'attrs'        => $attrs,
        'innerBlocks'  => array_values($children),
        'innerHTML'    => '',
        'innerContent' => $children ? array_fill(0, count($children), null) : array(),
    );
}

/**
 * Walk $blocks, convert our item, and report whether anything changed.
 *
 * $changed is the ONLY signal the caller writes on. A run that finds the item
 * already correct returns the blocks untouched and $changed false, which is
 * the whole of the idempotence guarantee: the second call performs no write.
 */
function tit_nav_apply(&$blocks, $parent_url, $desired, &$changed, &$found) {
    foreach ($blocks as $i => $block) {
        if (tit_nav_block_points_at($block, $parent_url)) {
            $found = true;
            $existing = isset($block['innerBlocks']) ? $block['innerBlocks'] : array();
            $next = tit_nav_as_submenu($block, tit_nav_rebuild_children($existing, $desired, $parent_url));
            if (serialize_blocks(array($next)) !== serialize_blocks(array($block))) {
                $blocks[$i] = $next;
                $changed = true;
            }
            return true;
        }
        if (!empty($block['innerBlocks'])) {
            $inner = $block['innerBlocks'];
            if (tit_nav_apply($inner, $parent_url, $desired, $changed, $found)) {
                $blocks[$i]['innerBlocks'] = $inner;
                return true;
            }
        }
    }
    return false;
}

/** The block for $url, or null. */
function tit_nav_find($blocks, $url) {
    foreach ($blocks as $block) {
        if (tit_nav_block_points_at($block, $url)) return $block;
        if (!empty($block['innerBlocks'])) {
            $hit = tit_nav_find($block['innerBlocks'], $url);
            if ($hit !== null) return $hit;
        }
    }
    return null;
}

/**
 * Take the cross-plugin lock, or report that the sibling holds it.
 *
 * add_option() is an INSERT against a UNIQUE column, so of two concurrent
 * callers exactly one gets true. A lock older than TIT_NAV_LOCK_TTL is a
 * crashed run and is cleared, so this can never wedge the sibling permanently.
 */
function tit_nav_lock() {
    if (add_option(TIT_NAV_LOCK_OPTION, (string) time(), '', 'no')) return true;
    $held = (int) get_option(TIT_NAV_LOCK_OPTION, 0);
    if ($held && (time() - $held) > TIT_NAV_LOCK_TTL) delete_option(TIT_NAV_LOCK_OPTION);
    return false;
}

function tit_nav_unlock() {
    delete_option(TIT_NAV_LOCK_OPTION);
}

/** Re-read every menu and confirm our item carries exactly the intended set. */
function tit_nav_verify($parent_url, $desired) {
    $menus = get_posts(array(
        'post_type'        => 'wp_navigation',
        'post_status'      => array('publish', 'draft'),
        'numberposts'      => 20,
        'suppress_filters' => false,
    ));
    $seen = false;
    foreach ($menus as $menu) {
        $item = tit_nav_find(parse_blocks((string) $menu->post_content), $parent_url);
        if ($item === null) continue;
        $seen = true;
        if ($item['blockName'] !== 'core/navigation-submenu') return false;
        $got = array();
        foreach ((array) $item['innerBlocks'] as $child) {
            $attrs = isset($child['attrs']) && is_array($child['attrs']) ? $child['attrs'] : array();
            $url = isset($attrs['url']) ? tit_nav_normalize_url($attrs['url']) : '';
            if ($url !== '' && strpos($url, $parent_url . '/') === 0) {
                $got[$url] = isset($attrs['label']) ? $attrs['label'] : '';
            }
        }
        if ($got !== tit_nav_desired_map($desired)) return false;
    }
    return $seen;
}

/**
 * Put the four routes under the tracker's menu item, and keep them there.
 *
 * Verified means re-read from the database and re-derived, not "wp_update_post
 * did not return an error". The done-flag stores TIT_VERSION, so every deploy
 * re-checks, which is what carries a renamed heading into the menu.
 */
function tit_nav_submenu_sync() {
    if (get_option('tit_nav_submenu_synced') === TIT_VERSION) return;

    $last = (int) get_option('tit_nav_submenu_last_try', 0);
    if ($last && (time() - $last) < TIT_NAV_RETRY_EVERY) return;
    update_option('tit_nav_submenu_last_try', time(), false);

    $parent_url = tit_nav_parent_url();
    if ($parent_url === '') return;
    $desired = tit_nav_desired_children();
    if (!$desired) return;                       // not ready; never a partial menu

    if (!tit_nav_lock()) return;                 // the sibling is writing; retry
    try {
        $menus = get_posts(array(
            'post_type'        => 'wp_navigation',
            'post_status'      => array('publish', 'draft'),
            'numberposts'      => 20,
            'suppress_filters' => false,
        ));
        $found_anywhere = false;

        foreach ($menus as $menu) {
            $blocks  = parse_blocks((string) $menu->post_content);
            $changed = false;
            $found   = false;
            tit_nav_apply($blocks, $parent_url, $desired, $changed, $found);
            if (!$found) continue;
            $found_anywhere = true;
            if (!$changed) continue;             // already right: no write at all
            wp_update_post(array(
                'ID'           => (int) $menu->ID,
                'post_content' => serialize_blocks($blocks),
            ));
        }

        // The dashboard is in no menu on this site. Nothing to do and nothing
        // to create. Leave the flag unset so a later menu edit is picked up.
        if (!$found_anywhere) return;

        if (tit_nav_verify($parent_url, $desired)) {
            update_option('tit_nav_submenu_synced', TIT_VERSION, false);
        }
    } finally {
        tit_nav_unlock();
    }
}
add_action('init', 'tit_nav_submenu_sync', 24);
