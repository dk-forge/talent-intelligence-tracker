<?php
/**
 * "Who's hiring" role pages, from the `functions` field (owner-approved SEO
 * work, 2026-10-13):
 *
 *   /talent-intelligence-tracker/role/{role}/           one per function
 *   /talent-intelligence-tracker/role/{role}/{city}/    role x city
 *   /talent-intelligence-tracker/roles/                 the crawlable directory
 *   /talent-intelligence-tracker/roles-sitemap.xml
 *
 * THE GATE IS THE PLACE GATE, called and not copied: tit_place_meets_threshold()
 * over distinct documents, employers and sources. A role, or a role in a city,
 * below it gets NO PAGE: a 302 to the dashboard filtered to the same cell, for
 * the reasons tit_place_route() records (temporary, because coverage grows). So
 * nothing thin is ever indexable and nothing thin is in the sitemap.
 *
 * Membership is INSTR(functions, '"<token>"'): functions is a JSON array, and
 * matching the quoted token is the dashboard filter's own rule (api.php), so
 * "finance" never matches a longer value that contains it. INSTR rather than
 * LIKE because "_" in a token is a LIKE wildcard and INSTR has none.
 *
 * Computed on render off {prefix}tit_signals, cached under the API's TTL and
 * dropped by tit_flush_caches() like every tit_ transient. Ships no JavaScript.
 */

if (!defined('ABSPATH')) exit;

const TIT_ROLES_PATH = 'talent-intelligence-tracker/role';
const TIT_ROLES_DIR_PATH = 'talent-intelligence-tracker/roles';
const TIT_ROLES_SITEMAP_PATH = 'talent-intelligence-tracker/roles-sitemap.xml';
const TIT_ROLE_RECENT = 12;
const TIT_ROLE_TOP_EMPLOYERS = 8;
const TIT_ROLE_CTA_URL = 'https://asktherecruiter.com/?utm_source=tit&utm_medium=seo&utm_campaign=whos_hiring';

/** Reader-facing role names, keyed by the stored vocabulary (api.php). */
function tit_role_labels() {
    $labels = array(
        'engineering' => 'Engineering', 'data_ai' => 'Data & AI',
        'it_infrastructure' => 'IT & Infrastructure', 'product' => 'Product',
        'design' => 'Design', 'finance' => 'Finance', 'hr_people' => 'HR & People',
        'sales' => 'Sales', 'marketing' => 'Marketing',
        'customer_support' => 'Customer Support', 'operations' => 'Operations',
        'supply_chain' => 'Supply Chain', 'manufacturing' => 'Production & Manufacturing',
        'legal_compliance' => 'Legal & Compliance', 'research' => 'Research',
        'clinical_healthcare' => 'Clinical & Healthcare', 'executive' => 'Executive',
    );
    // Only tokens the API accepts as a filter, so every redirect lands on a
    // dashboard that honours it.
    $allowed = function_exists('tit_allowed_functions') ? tit_allowed_functions() : array_keys($labels);
    return array_intersect_key($labels, array_flip($allowed));
}

function tit_role_slug($token) {
    $labels = tit_role_labels();
    if (!isset($labels[$token]) || !function_exists('tit_company_slug')) return '';
    $slug = tit_company_slug($labels[$token]);
    return preg_match('/^[a-z0-9-]+$/', (string) $slug) ? $slug : '';
}

function tit_role_url($token, $city = '') {
    $slug = tit_role_slug($token);
    if ($slug === '') return '';
    $path = '/' . TIT_ROLES_PATH . '/' . $slug . '/';
    if ($city !== '') {
        $cs = function_exists('tit_place_slug') ? tit_place_slug('city', $city) : '';
        if ($cs === '') return '';
        $path .= $cs . '/';
    }
    return home_url($path);
}

function tit_roles_url() { return home_url('/' . TIT_ROLES_DIR_PATH . '/'); }
function tit_roles_sitemap_url() { return home_url('/' . TIT_ROLES_SITEMAP_PATH); }

function tit_role_dashboard_url($token, $city = '') {
    $url = add_query_arg('function', (string) $token, home_url('/talent-intelligence-tracker/'));
    return $city !== '' ? add_query_arg('city', (string) $city, $url) : $url;
}

function &tit_role_memo() {
    if (!isset($GLOBALS['tit_role_memo'])) {
        $GLOBALS['tit_role_memo'] = array('current' => null, 'index' => null, 'facts' => array());
    }
    return $GLOBALS['tit_role_memo'];
}

function tit_role_forget() {
    $GLOBALS['tit_role_memo'] = array('current' => null, 'index' => null, 'facts' => array());
}

/** The membership clause for one token, as SQL with no user input in it. */
function tit_role_match_sql($token) {
    // Tokens are a closed [a-z_] vocabulary; refuse anything else outright.
    if (!preg_match('/^[a-z_]+$/', (string) $token)) return '0';
    return "INSTR(functions, '\"" . $token . "\"') > 0";
}

/** One cell's counts, from a row of the conditional-aggregate query. */
function tit_role_cell($row, $i, $key, $name) {
    $d = (int) ($row["d{$i}"] ?? 0);
    $e = (int) ($row["e{$i}"] ?? 0);
    $s = (int) ($row["s{$i}"] ?? 0);
    return array('key' => (string) $key, 'name' => (string) $name, 'documents' => $d,
                 'employers' => $e, 'sources' => $s, 'lastmod' => (string) ($row["l{$i}"] ?? ''),
                 'gated' => function_exists('tit_place_meets_threshold')
                            && tit_place_meets_threshold($d, $e, $s));
}

/**
 * Every role, and every role x city, with its counts and gate. TWO queries,
 * whatever the vocabulary size: one conditional aggregate over the table, and
 * the same grouped by city. Keyed by canonical slug; a city slug two stored
 * names claim is refused, as places.php refuses it.
 */
function tit_role_index() {
    $memo = &tit_role_memo();
    if ($memo['index'] !== null) return $memo['index'];
    $cache_key = 'tit_role_index_' . md5(TIT_VERSION);
    $cached = get_transient($cache_key);
    if (is_array($cached) && isset($cached['roles'])) return $memo['index'] = $cached;

    global $wpdb;
    $table = tit_table_name();
    $tokens = array_keys(tit_role_labels());
    $cols = array();
    foreach ($tokens as $i => $t) {
        $m = tit_role_match_sql($t);
        $cols[] = "COUNT(DISTINCT CASE WHEN {$m} THEN source_url END) AS d{$i}";
        $cols[] = "COUNT(DISTINCT CASE WHEN {$m} THEN company_key END) AS e{$i}";
        $cols[] = "COUNT(DISTINCT CASE WHEN {$m} THEN source_name END) AS s{$i}";
        $cols[] = "MAX(CASE WHEN {$m} THEN COALESCE(published_date, DATE(captured_at)) END) AS l{$i}";
    }
    $select = implode(",\n ", $cols);
    $base = "FROM {$table} WHERE is_current = 1 AND functions IS NOT NULL AND functions <> ''";

    $total = $wpdb->get_row("SELECT {$select} {$base}", ARRAY_A) ?: array();
    $by_city = $wpdb->get_results(
        "SELECT COALESCE(city, hq_city) AS k, {$select} {$base}
            AND COALESCE(city, hq_city) IS NOT NULL AND COALESCE(city, hq_city) <> ''
          GROUP BY k", ARRAY_A) ?: array();

    $labels = tit_role_labels();
    $roles = array();
    $cities = array();
    foreach ($tokens as $i => $t) {
        $slug = tit_role_slug($t);
        if ($slug === '' || (int) ($total["d{$i}"] ?? 0) === 0) continue;
        $roles[$slug] = tit_role_cell($total, $i, $t, $labels[$t]);
        $claims = array();
        foreach ($by_city as $row) {
            if ((int) ($row["d{$i}"] ?? 0) === 0) continue;
            $cs = function_exists('tit_place_slug') ? tit_place_slug('city', $row['k']) : '';
            if ($cs !== '') $claims[$cs][] = $row;
        }
        foreach ($claims as $cs => $owners) {
            if (count($owners) > 1) continue;   // a collision is refused, not resolved
            $cities[$slug][$cs] = tit_role_cell($owners[0], $i, $owners[0]['k'], $owners[0]['k']);
        }
    }
    $index = array('roles' => $roles, 'cities' => $cities);
    if (function_exists('tit_company_slug')) {
        set_transient($cache_key, $index, defined('TIT_CACHE_TTL') ? TIT_CACHE_TTL : 300);
    }
    return $memo['index'] = $index;
}

/* --- routing ------------------------------------------------------------- */

function tit_roles_rewrites() {
    $p = TIT_ROLES_PATH;
    return array(
        '^' . $p . '/([^/]+)/([^/]+)/?$' => 'index.php?tit_role=$matches[1]&tit_role_city=$matches[2]',
        '^' . $p . '/([^/]+)/?$' => 'index.php?tit_role=$matches[1]',
        '^' . TIT_ROLES_DIR_PATH . '/?$' => 'index.php?tit_roles=1',
        '^' . str_replace('.', '\.', TIT_ROLES_SITEMAP_PATH) . '$' => 'index.php?tit_roles_sitemap=1',
    );
}

function tit_roles_rewrite() {
    foreach (tit_roles_rewrites() as $pattern => $target) add_rewrite_rule($pattern, $target, 'top');
}
add_action('init', 'tit_roles_rewrite');

function tit_roles_query_vars($vars) {
    return array_merge($vars, array('tit_role', 'tit_role_city', 'tit_roles', 'tit_roles_sitemap'));
}
add_filter('query_vars', 'tit_roles_query_vars');

function tit_roles_needs_no_js($needs) {
    return (get_query_var('tit_role') || get_query_var('tit_roles')) ? false : $needs;
}
add_filter('tit_route_needs_js', 'tit_roles_needs_no_js');

/** The cell being rendered, resolved once, or null. */
function tit_role_current() {
    $memo = &tit_role_memo();
    if ($memo['current'] !== null) return $memo['current'] ?: null;
    $req_role = sanitize_text_field(rawurldecode((string) get_query_var('tit_role')));
    $req_city = sanitize_text_field(rawurldecode((string) get_query_var('tit_role_city')));
    $index = tit_role_index();
    $rs = strtolower($req_role);
    $cs = strtolower($req_city);
    if ($rs === '' || !isset($index['roles'][$rs])) { $memo['current'] = false; return null; }
    $role = $index['roles'][$rs];
    $city = null;
    if ($cs !== '') {
        if (!isset($index['cities'][$rs][$cs])) { $memo['current'] = false; return null; }
        $city = $index['cities'][$rs][$cs];
    }
    return $memo['current'] = array('role' => $role, 'city' => $city,
        'requested' => $req_role . ($req_city !== '' ? '/' . $req_city : ''),
        'canonical' => $rs . ($cs !== '' ? '/' . $cs : ''));
}

/** 404 / 301 / 302 / render, as a decision with no side effects. */
function tit_role_route($current) {
    if (!$current) return array('action' => '404');
    $role = $current['role'];
    $city = $current['city'];
    $cell = $city ?: $role;
    $ckey = $city ? $city['key'] : '';
    if ($current['requested'] !== $current['canonical']) {
        return array('action' => 'redirect', 'code' => 301, 'url' => tit_role_url($role['key'], $ckey));
    }
    if (!$cell['gated'] || !$role['gated']) {
        return array('action' => 'redirect', 'code' => 302,
                     'url' => tit_role_dashboard_url($role['key'], $ckey));
    }
    return array('action' => 'render');
}

function tit_role_template() {
    if (!get_query_var('tit_role')) return;
    $current = tit_role_current();
    $route = tit_role_route($current);
    if ($route['action'] === '404') {
        status_header(404);
        nocache_headers();
        include get_404_template();
        exit;
    }
    if ($route['action'] === 'redirect') {
        wp_safe_redirect($route['url'], $route['code']);
        exit;
    }
    tit_role_render($current);
    exit;
}
add_action('template_redirect', 'tit_role_template');

/* --- the page ------------------------------------------------------------ */

/** Three queries per cold render: the scalars, the employers, recent updates. */
function tit_role_facts($token, $city) {
    $memo = &tit_role_memo();
    $mk = $token . '|' . $city;
    if (isset($memo['facts'][$mk])) return $memo['facts'][$mk];
    $cache_key = 'tit_role_facts_' . md5($mk . '|' . TIT_VERSION);
    $cached = get_transient($cache_key);
    if (is_array($cached)) return $memo['facts'][$mk] = $cached;

    global $wpdb;
    $table = tit_table_name();
    $where = 'is_current = 1 AND ' . tit_role_match_sql($token);
    $args = array();
    if ($city !== '') {
        $where .= ' AND (city = %s OR (city IS NULL AND hq_city = %s))';
        $args = array($city, $city);
    }
    $q = function ($sql) use ($wpdb, $args) { return $args ? $wpdb->prepare($sql, $args) : $sql; };

    $s = $wpdb->get_row($q("SELECT COUNT(*) AS records, COUNT(DISTINCT company_key) AS employers,
                COUNT(DISTINCT source_name) AS sources,
                MAX(COALESCE(published_date, DATE(captured_at))) AS hi
           FROM {$table} WHERE {$where}"), ARRAY_A) ?: array();
    $employers = $wpdb->get_results($q("SELECT company_key, MAX(company) AS company, COUNT(*) AS n
           FROM {$table} WHERE {$where} GROUP BY company_key
          ORDER BY n DESC, company_key ASC LIMIT " . (int) TIT_ROLE_TOP_EMPLOYERS), ARRAY_A) ?: array();
    $recent = $wpdb->get_results($q("SELECT headline, company, company_key, city, hq_city,
                source_url, source_name, published_date, captured_at
           FROM {$table} WHERE {$where}
          ORDER BY COALESCE(published_date, DATE(captured_at)) DESC, row_id DESC
          LIMIT " . (int) TIT_ROLE_RECENT), ARRAY_A) ?: array();

    $facts = array(
        'records'   => (int) ($s['records'] ?? 0),
        'employers' => (int) ($s['employers'] ?? 0),
        'sources'   => (int) ($s['sources'] ?? 0),
        'hi'        => (string) ($s['hi'] ?? ''),
        'top_employers' => function_exists('tit_place_employer_links')
                           ? tit_place_employer_links($employers) : array(),
        'recent'    => $recent,
    );
    set_transient($cache_key, $facts, defined('TIT_CACHE_TTL') ? TIT_CACHE_TTL : 300);
    return $memo['facts'][$mk] = $facts;
}

function tit_role_heading($current) {
    $role = $current['role']['name'];
    return $current['city']
        ? sprintf("Who's Hiring In %s: %s Roles", $current['city']['name'], $role)
        : sprintf("Who's Hiring For %s Roles", $role);
}

function tit_role_trail($current = null) {
    $current = $current ?: tit_role_current();
    if (!$current || !function_exists('tit_breadcrumbs_hub')) return null;
    $role = $current['role'];
    $trail = array(tit_breadcrumbs_hub(),
        array('name' => "Who's Hiring", 'url' => tit_roles_url()),
        array('name' => $role['name'], 'url' => tit_role_url($role['key'])));
    if ($current['city']) {
        $trail[] = array('name' => $current['city']['name'],
                         'url' => tit_role_url($role['key'], $current['city']['key']));
    }
    return $trail;
}

function tit_role_cta_html($role_name) {
    return '<div class="tit-callout tit-role-cta"><a class="tit-btn" href="' . esc_url(TIT_ROLE_CTA_URL)
         . '">' . esc_html(sprintf('Check your fit for %s jobs', $role_name)) . '</a></div>';
}

function tit_role_render($current) {
    $role = $current['role'];
    $city = $current['city'];
    $ckey = $city ? $city['key'] : '';
    $facts = tit_role_facts($role['key'], $ckey);
    $heading = tit_role_heading($current);
    $url = tit_role_url($role['key'], $ckey);
    $index = tit_role_index();

    if (function_exists('tit_render_header')) tit_render_header(); else get_header();
    ?>
    <div class="tit-wrap tit-place tit-role">
      <?php if (function_exists('tit_breadcrumbs_html')) echo tit_breadcrumbs_html(tit_role_trail($current)); ?>
      <h1><?php echo esc_html($heading); ?></h1>
      <?php if (function_exists('tit_freshness_line')) echo tit_freshness_line($facts['hi'], $facts['sources']); ?>
      <p class="tit-note"><?php echo esc_html(sprintf(
          'Employers we track with %1$s activity%2$s: %3$s from %4$s across %5$s. Each update links to the filing or report behind it, so these are counts of documents we can cite, not of every opening.',
          $role['name'], $city ? ' in ' . $city['name'] : '',
          sprintf(_n('%s update', '%s updates', $facts['records'], 'tit'), number_format_i18n($facts['records'])),
          sprintf(_n('%s source', '%s sources', $facts['sources'], 'tit'), number_format_i18n($facts['sources'])),
          sprintf(_n('%s employer', '%s employers', $facts['employers'], 'tit'), number_format_i18n($facts['employers'])))); ?></p>

      <?php echo tit_role_cta_html($role['name']); // escaped inside ?>

      <?php if ($facts['top_employers']) : ?>
        <h2 class="tit-place-h2">Employers We Hold The Most On</h2>
        <ul class="tit-place-list">
          <?php foreach ($facts['top_employers'] as $e) : ?>
            <li><?php if ($e['url']) : ?><a href="<?php echo esc_url($e['url']); ?>"><?php echo esc_html($e['name']); ?></a><?php
                else : echo esc_html($e['name']); endif; ?>
              <span class="tit-place-n"><?php echo esc_html(sprintf(_n('%s update', '%s updates', $e['n'], 'tit'),
                  number_format_i18n($e['n']))); ?></span></li>
          <?php endforeach; ?>
        </ul>
      <?php endif; ?>

      <?php
      $slug = tit_role_slug($role['key']);
      $gated_cities = array_filter($index['cities'][$slug] ?? array(), function ($c) { return $c['gated']; });
      if (!$city && $gated_cities) :
        uasort($gated_cities, function ($a, $b) { return $b['documents'] <=> $a['documents']; }); ?>
        <h2 class="tit-place-h2"><?php echo esc_html(sprintf('%s Roles By City', $role['name'])); ?></h2>
        <ul class="tit-place-list">
          <?php foreach ($gated_cities as $c) : ?>
            <li><a href="<?php echo esc_url(tit_role_url($role['key'], $c['key'])); ?>"><?php echo esc_html($c['name']); ?></a></li>
          <?php endforeach; ?>
        </ul>
      <?php elseif ($city) : ?>
        <p class="tit-note"><a href="<?php echo esc_url(tit_role_url($role['key'])); ?>"><?php
          echo esc_html(sprintf("Who's hiring for %s roles everywhere we track", $role['name'])); ?></a></p>
      <?php endif; ?>

      <h2 class="tit-place-h2">Recent Updates</h2>
      <ol class="tit-timeline">
        <?php foreach ($facts['recent'] as $r) : ?>
          <li class="tit-event"><div class="tit-event-when"><?php
            echo esc_html($r['published_date'] ?: substr((string) $r['captured_at'], 0, 10)); ?></div>
            <div class="tit-event-body"><h3 class="tit-h"><?php echo esc_html($r['headline']); ?></h3>
              <p class="tit-event-meta"><?php echo esc_html($r['company']); ?> ·
                <a href="<?php echo esc_url($r['source_url']); ?>" rel="nofollow noopener" target="_blank"><?php
                  echo esc_html($r['source_name']); ?></a></p></div></li>
        <?php endforeach; ?>
      </ol>
      <p class="tit-cite">
        <a href="<?php echo esc_url(tit_role_dashboard_url($role['key'], $ckey)); ?>">See every update in the tracker</a>
        &middot; <a href="<?php echo esc_url(tit_roles_url()); ?>">Every role we cover</a>
        &middot; Data licensed CC BY 4.0.
      </p>
    </div>
    <script type="application/ld+json"><?php echo wp_json_encode(array(
        '@context' => 'https://schema.org', '@type' => 'CollectionPage',
        'name' => $heading, 'url' => $url,
        'isPartOf' => array('@type' => 'WebSite', 'name' => 'Talent Intelligence Tracker',
                            'url' => home_url('/talent-intelligence-tracker/')),
    ) + ($facts['hi'] && $facts['hi'] <= gmdate('Y-m-d') ? array('dateModified' => $facts['hi']) : array()),
      JSON_UNESCAPED_SLASHES | JSON_HEX_TAG | JSON_HEX_AMP); ?></script>
    <?php
    if (function_exists('tit_render_footer')) tit_render_footer(); else get_footer();
}

/* --- directory, head, sitemap ------------------------------------------- */

function tit_roles_directory_template() {
    if (!get_query_var('tit_roles')) return;
    $index = tit_role_index();
    if (function_exists('tit_render_header')) tit_render_header(); else get_header();
    ?>
    <div class="tit-wrap tit-place tit-directory">
      <?php if (function_exists('tit_breadcrumbs_html')) echo tit_breadcrumbs_html(array(
          tit_breadcrumbs_hub(), array('name' => "Who's Hiring", 'url' => tit_roles_url()))); ?>
      <h1>Who's Hiring, By Role</h1>
      <?php if (function_exists('tit_page_faq_intro')) echo tit_page_faq_intro('roles'); ?>
      <p class="tit-note">A role, or a role in a city, gets a page once we hold enough source documents, employers and sources on it, the same bar a city page has to clear.</p>
      <ul class="tit-place-list">
        <?php foreach ($index['roles'] as $r) : if (!$r['gated']) continue; ?>
          <li><a href="<?php echo esc_url(tit_role_url($r['key'])); ?>"><?php echo esc_html($r['name']); ?></a></li>
        <?php endforeach; ?>
      </ul>
    </div>
    <?php
    if (function_exists('tit_page_faq_section')) echo tit_page_faq_section('roles');
    if (function_exists('tit_render_footer')) tit_render_footer(); else get_footer();
    exit;
}
add_action('template_redirect', 'tit_roles_directory_template');

function tit_role_title($title) {
    if (get_query_var('tit_roles')) return "Who's Hiring, By Role · Talent Intelligence Tracker";
    $current = tit_role_current();
    return ($current && tit_role_route($current)['action'] === 'render')
        ? tit_role_heading($current) . ' · Talent Intelligence Tracker' : $title;
}
add_filter('pre_get_document_title', 'tit_role_title');

function tit_role_head() {
    if (get_query_var('tit_roles')) {
        echo '<link rel="canonical" href="' . esc_url(tit_roles_url()) . '" />' . "\n";
        return;
    }
    $current = tit_role_current();
    if (!$current || tit_role_route($current)['action'] !== 'render') return;
    $ckey = $current['city'] ? $current['city']['key'] : '';
    $facts = tit_role_facts($current['role']['key'], $ckey);
    $desc = sprintf('%s: %s employers we track, %s updates, each linked to the filing or report behind it.',
        tit_role_heading($current), number_format_i18n($facts['employers']), number_format_i18n($facts['records']));
    if (function_exists('tit_seo_cap_description')) $desc = tit_seo_cap_description($desc);
    echo "\n" . '<meta name="description" content="' . esc_attr($desc) . '" />' . "\n";
    echo '<link rel="canonical" href="' . esc_url(tit_role_url($current['role']['key'], $ckey)) . '" />' . "\n";
}
add_action('wp_head', 'tit_role_head', 1);

function tit_roles_sitemap_xml() {
    $today = gmdate('Y-m-d');
    $index = tit_role_index();
    $entries = array();
    $newest = '';
    foreach ($index['roles'] as $slug => $r) {
        if (!$r['gated']) continue;
        $entries[] = array(tit_role_url($r['key']), $r['lastmod']);
        if ($r['lastmod'] > $newest) $newest = $r['lastmod'];
        foreach ($index['cities'][$slug] ?? array() as $c) {
            if ($c['gated']) $entries[] = array(tit_role_url($r['key'], $c['key']), $c['lastmod']);
        }
    }
    array_unshift($entries, array(tit_roles_url(), $newest));
    $xml = '<?xml version="1.0" encoding="UTF-8"?>' . "\n"
         . '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' . "\n";
    foreach ($entries as $e) {
        if ($e[0] === '') continue;
        // A future-dated row never becomes a future lastmod (places.php rule).
        $lastmod = ($e[1] && $e[1] <= $today) ? $e[1] : $today;
        $xml .= '  <url><loc>' . esc_url($e[0]) . '</loc><lastmod>' . esc_html($lastmod) . '</lastmod></url>' . "\n";
    }
    return $xml . '</urlset>' . "\n";
}

function tit_roles_sitemap_no_canonical_redirect($redirect) {
    return get_query_var('tit_roles_sitemap') ? false : $redirect;
}
add_filter('redirect_canonical', 'tit_roles_sitemap_no_canonical_redirect');

function tit_roles_sitemap_template() {
    if (!get_query_var('tit_roles_sitemap')) return;
    status_header(200);
    header('Content-Type: application/xml; charset=UTF-8', true);
    header('X-Robots-Tag: noindex', true);
    echo tit_roles_sitemap_xml();
    exit;
}
add_action('template_redirect', 'tit_roles_sitemap_template');
