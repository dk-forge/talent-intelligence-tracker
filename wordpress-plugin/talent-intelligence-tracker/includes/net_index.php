<?php
/**
 * The monthly Net Talent Index (owner ruling 2026-10-15, gap 5 of the
 * 2026-10-12 scored review): one quotable number a month, with its own
 * indexable URL per month and schema.org Dataset markup.
 *
 * THE DEFINITION, printed on the page in the same words:
 *
 *   index = 100 x (employers adding roles - employers cutting roles)
 *                 / employers with any tracked update that month
 *
 * Each employer counts once per month per side. The denominator is what keeps
 * months comparable while coverage grows: a raw count of hiring employers
 * rises because we read more sources, a share of the employers we read does
 * not. A month with fewer than TIT_NET_INDEX_MIN_ACTIVE employers prints no
 * value at all (a metro needs TIT_NET_INDEX_MIN_METRO), because a ratio on a
 * thin month is noise wearing a decimal point. The month in progress is never
 * published: a half month is a different population.
 *
 * WHAT IT IS NOT. "Cutting roles" here is this tracker's own displacement
 * direction, which is rare by design: layoffs are the sibling tracker's
 * subject and are not collected here (CLAUDE.md). So the index reads as the
 * share of active employers that announced hiring, net of the few cuts we
 * hold, and the page says so and links the AI Layoff Tracker for cuts. It is
 * not a jobs count and not an estimate of the labour market.
 *
 * Guard: tests/test_net_index.py.
 */
if (!defined('ABSPATH')) exit;

const TIT_NET_INDEX_PATH       = 'talent-intelligence-tracker/net-talent-index';
const TIT_NET_INDEX_SITEMAP    = 'talent-intelligence-tracker/net-index-sitemap.xml';
const TIT_NET_INDEX_MIN_ACTIVE = 200;
const TIT_NET_INDEX_MIN_METRO  = 30;
const TIT_NET_INDEX_FIRST      = '2026-01'; // first month with a hiring direction in volume

/** PURE. The index, one decimal, or null below the floor. */
function tit_net_index_value($adding, $cutting, $active, $floor = TIT_NET_INDEX_MIN_ACTIVE) {
    $active = (int) $active;
    if ($active < (int) $floor || $active <= 0) return null;
    return round(100 * ((int) $adding - (int) $cutting) / $active, 1);
}

/** PURE. A valid YYYY-MM, or ''. */
function tit_net_index_clean_month($ym) {
    $ym = (string) $ym;
    if (!preg_match('/^(\d{4})-(\d{2})$/', $ym, $m)) return '';
    return ((int) $m[2] >= 1 && (int) $m[2] <= 12) ? $ym : '';
}

/** PURE. "September 2026" for "2026-09". */
function tit_net_index_month_label($ym) {
    $ts = strtotime($ym . '-01 00:00:00 UTC');
    return $ts ? gmdate('F Y', $ts) : $ym;
}

/**
 * PURE. Rows of {m, active, adding, cutting} into published months, oldest
 * first: complete months only (before $current_month), from the first month,
 * each with its value (null below the floor) and the change on the month
 * before when both have one.
 */
function tit_net_index_months(array $rows, $current_month, $first = TIT_NET_INDEX_FIRST) {
    $out = array();
    usort($rows, function ($a, $b) { return strcmp((string) $a['m'], (string) $b['m']); });
    $prev = null;
    foreach ($rows as $r) {
        $m = tit_net_index_clean_month($r['m'] ?? '');
        if ($m === '' || $m < $first || $m >= $current_month) continue;
        $v = tit_net_index_value($r['adding'] ?? 0, $r['cutting'] ?? 0, $r['active'] ?? 0);
        $out[$m] = array('m' => $m, 'active' => (int) $r['active'], 'adding' => (int) $r['adding'],
                         'cutting' => (int) $r['cutting'], 'value' => $v,
                         'change' => ($v !== null && $prev !== null) ? round($v - $prev, 1) : null);
        $prev = $v;
    }
    return $out;
}

/** PURE. Metro rows {city, active, adding, cutting} into a ranked list above the metro floor. */
function tit_net_index_metros(array $rows, $limit = 15) {
    $out = array();
    foreach ($rows as $r) {
        $v = tit_net_index_value($r['adding'] ?? 0, $r['cutting'] ?? 0, $r['active'] ?? 0, TIT_NET_INDEX_MIN_METRO);
        if ($v === null) continue;
        $out[] = array('city' => (string) $r['city'], 'active' => (int) $r['active'],
                       'adding' => (int) $r['adding'], 'cutting' => (int) $r['cutting'], 'value' => $v);
    }
    usort($out, function ($a, $b) { return $b['value'] <=> $a['value'] ?: $b['active'] <=> $a['active']; });
    return array_slice($out, 0, $limit);
}

/** PURE. The schema.org Dataset node for one month's page (or the hub, $month = ''). */
function tit_net_index_dataset_node(array $months, $month, $url) {
    $keys = array_keys($months);
    $node = array(
        '@context' => 'https://schema.org',
        '@type'    => 'Dataset',
        'name'     => $month === '' ? 'Net Talent Index, monthly'
                                    : 'Net Talent Index, ' . tit_net_index_month_label($month),
        'description' => 'The share of employers with a tracked update in a month that announced '
                       . 'adding roles, net of those announcing cuts, times 100. Each figure is '
                       . 'counted from source-linked records in the Talent Intelligence Tracker.',
        'url'      => $url,
        'isAccessibleForFree' => true,
        'license'  => 'https://creativecommons.org/licenses/by/4.0/',
        'creator'  => array('@type' => 'Organization', 'name' => 'Ask The Recruiter',
                            'url' => 'https://asktherecruiter.com/'),
        'isPartOf' => array('@type' => 'Dataset', 'name' => 'Talent Intelligence Tracker',
                            'url' => home_url('/talent-intelligence-tracker/')),
        'variableMeasured' => array(
            array('@type' => 'PropertyValue', 'name' => 'Net Talent Index', 'unitText' => 'index points'),
            array('@type' => 'PropertyValue', 'name' => 'Employers adding roles'),
            array('@type' => 'PropertyValue', 'name' => 'Employers cutting roles'),
            array('@type' => 'PropertyValue', 'name' => 'Employers with any tracked update'),
        ),
        'measurementTechnique' => 'Count of distinct employers per month by announced direction, from source-linked records',
        'keywords' => array('net hiring', 'talent market', 'hiring index', 'who is hiring'),
    );
    if ($month !== '') {
        $node['temporalCoverage'] = $month;
    } elseif ($keys) {
        $node['temporalCoverage'] = reset($keys) . '/' . end($keys);
    }
    return $node;
}

function tit_net_index_url($month = '') {
    return home_url('/' . TIT_NET_INDEX_PATH . '/' . ($month !== '' ? $month . '/' : ''));
}

function tit_net_index_sitemap_url() {
    return home_url('/' . TIT_NET_INDEX_SITEMAP);
}

/* ------------------------------------------------------------------ runtime */

function tit_net_index_rewrite() {
    add_rewrite_rule('^' . TIT_NET_INDEX_PATH . '/?$', 'index.php?tit_net_index=latest', 'top');
    add_rewrite_rule('^' . TIT_NET_INDEX_PATH . '/(\d{4}-\d{2})/?$', 'index.php?tit_net_index=$matches[1]', 'top');
    add_rewrite_rule('^' . TIT_NET_INDEX_SITEMAP . '$', 'index.php?tit_net_index_sitemap=1', 'top');
}
add_action('init', 'tit_net_index_rewrite');

function tit_net_index_query_var($vars) {
    $vars[] = 'tit_net_index';
    $vars[] = 'tit_net_index_sitemap';
    return $vars;
}
add_filter('query_vars', 'tit_net_index_query_var');

function tit_net_index_needs_no_js($needs) {
    return get_query_var('tit_net_index') ? false : $needs;
}
add_filter('tit_route_needs_js', 'tit_net_index_needs_no_js');

/** One grouped query for every month; cached until the next write flushes tit_ transients. */
function tit_net_index_data() {
    $cached = get_transient('tit_net_index_months');
    if (is_array($cached)) return $cached;
    global $wpdb;
    $t = tit_table_name();
    $rows = $wpdb->get_results(
        "SELECT DATE_FORMAT(COALESCE(published_date, DATE(captured_at)), '%Y-%m') AS m,
                COUNT(DISTINCT company_key) AS active,
                COUNT(DISTINCT CASE WHEN signal_direction = 'hiring' THEN company_key END) AS adding,
                COUNT(DISTINCT CASE WHEN signal_direction = 'displacement' THEN company_key END) AS cutting
           FROM {$t}
          WHERE is_current = 1 AND company_key <> ''
          GROUP BY m", ARRAY_A) ?: array();
    $months = tit_net_index_months($rows, gmdate('Y-m'));
    set_transient('tit_net_index_months', $months, 6 * HOUR_IN_SECONDS);
    return $months;
}

/** Metro breakdown for one month (one query, cached per month). */
function tit_net_index_metro_data($month) {
    $key = 'tit_net_index_metro_' . $month;
    $cached = get_transient($key);
    if (is_array($cached)) return $cached;
    global $wpdb;
    $t = tit_table_name();
    $from = $month . '-01';
    $to = gmdate('Y-m-d', strtotime($from . ' +1 month'));
    $rows = $wpdb->get_results($wpdb->prepare(
        "SELECT COALESCE(city, hq_city) AS city,
                COUNT(DISTINCT company_key) AS active,
                COUNT(DISTINCT CASE WHEN signal_direction = 'hiring' THEN company_key END) AS adding,
                COUNT(DISTINCT CASE WHEN signal_direction = 'displacement' THEN company_key END) AS cutting
           FROM {$t}
          WHERE is_current = 1 AND company_key <> ''
            AND COALESCE(city, hq_city) IS NOT NULL AND COALESCE(city, hq_city) <> ''
            AND COALESCE(published_date, DATE(captured_at)) >= %s
            AND COALESCE(published_date, DATE(captured_at)) < %s
          GROUP BY city", $from, $to), ARRAY_A) ?: array();
    $metros = tit_net_index_metros($rows);
    set_transient($key, $metros, 6 * HOUR_IN_SECONDS);
    return $metros;
}

/** The month a request asks for, '' when it names none we publish. */
function tit_net_index_current_month() {
    $q = (string) get_query_var('tit_net_index');
    if ($q === '') return '';
    $months = tit_net_index_data();
    if ($q === 'latest') {
        foreach (array_reverse($months) as $m => $row) if ($row['value'] !== null) return $m;
        return '';
    }
    $q = tit_net_index_clean_month($q);
    return ($q !== '' && isset($months[$q]) && $months[$q]['value'] !== null) ? $q : '';
}

function tit_net_index_template() {
    if (get_query_var('tit_net_index_sitemap')) { tit_net_index_sitemap(); exit; }
    if (!get_query_var('tit_net_index')) return;
    $month = tit_net_index_current_month();
    if ($month === '') {
        global $wp_query;
        $wp_query->set_404();
        status_header(404);
        return;
    }
    tit_net_index_render($month, get_query_var('tit_net_index') === 'latest');
    exit;
}
add_action('template_redirect', 'tit_net_index_template');

function tit_net_index_sitemap() {
    $months = tit_net_index_data();
    status_header(200);
    header('Content-Type: application/xml; charset=UTF-8', true);
    header('X-Robots-Tag: noindex', true);
    echo '<?xml version="1.0" encoding="UTF-8"?>' . "\n";
    echo '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' . "\n";
    echo '  <url><loc>' . esc_url(tit_net_index_url()) . '</loc><lastmod>' . gmdate('Y-m-d') . '</lastmod></url>' . "\n";
    foreach ($months as $m => $row) {
        if ($row['value'] === null) continue;
        $last = gmdate('Y-m-d', strtotime($m . '-01 +1 month -1 day'));
        echo '  <url><loc>' . esc_url(tit_net_index_url($m)) . '</loc><lastmod>' . esc_html($last) . '</lastmod></url>' . "\n";
    }
    echo '</urlset>' . "\n";
}
function tit_net_index_no_canonical_redirect($redirect) {
    return get_query_var('tit_net_index_sitemap') ? false : $redirect;
}
add_filter('redirect_canonical', 'tit_net_index_no_canonical_redirect');

/** PURE. The one sentence a journalist quotes. */
function tit_net_index_headline_sentence(array $row) {
    $s = sprintf('The Net Talent Index for %s is %s: %s of %s employers with a tracked update announced adding roles and %s announced cutting roles.',
        tit_net_index_month_label($row['m']), number_format((float) $row['value'], 1),
        number_format($row['adding']), number_format($row['active']), number_format($row['cutting']));
    if ($row['change'] !== null) {
        $s .= sprintf(' That is %s %s points on the month before.',
            $row['change'] >= 0 ? 'up' : 'down', number_format(abs((float) $row['change']), 1));
    }
    return $s;
}

function tit_net_index_render($month, $is_hub) {
    $months = tit_net_index_data();
    $row = $months[$month];
    $metros = tit_net_index_metro_data($month);
    $url = $is_hub ? tit_net_index_url() : tit_net_index_url($month);
    if (function_exists('tit_render_header')) tit_render_header(); else get_header();
    ?>
    <div class="tit-wrap tit-net-index" id="tit-net-index">
      <?php if (function_exists('tit_breadcrumbs_html') && function_exists('tit_breadcrumbs_section_trail')) {
          echo tit_breadcrumbs_html(tit_breadcrumbs_section_trail('Net Talent Index', tit_net_index_url()));
      } ?>
      <h1 data-tit-route-heading>Net Talent Index: <?php echo esc_html(tit_net_index_month_label($month)); ?></h1>
      <p class="tit-page-intro"><?php echo esc_html(tit_net_index_headline_sentence($row)); ?></p>

      <div class="tit-stats">
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format_i18n((float) $row['value'], 1)); ?></span><span class="tit-l">index</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format_i18n($row['adding'])); ?></span><span class="tit-l">employers adding roles</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format_i18n($row['cutting'])); ?></span><span class="tit-l">employers cutting roles</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format_i18n($row['active'])); ?></span><span class="tit-l">employers with an update</span></div>
      </div>

      <h2>How it is calculated</h2>
      <p>The index is 100 times the number of employers that announced adding roles, minus the
        number that announced cutting roles, divided by every employer with a tracked update
        that month. Each employer counts once per month. Dividing by the employers we read keeps
        months comparable as coverage grows. A month with fewer than
        <?php echo (int) TIT_NET_INDEX_MIN_ACTIVE; ?> employers, and a city with fewer than
        <?php echo (int) TIT_NET_INDEX_MIN_METRO; ?>, gets no figure. The month in progress is
        not published.</p>
      <p>Layoffs are not collected in this tracker, so cuts here are only the few that arrive
        as hiring news. For announced layoffs, see the
        <a href="<?php echo esc_url(home_url('/ai-layoff-tracker/')); ?>">AI Layoff Tracker</a>.
        This is a count of employers, not of jobs, and not an estimate of the labour market.</p>

      <?php if ($metros) : ?>
      <h2>By city, <?php echo esc_html(tit_net_index_month_label($month)); ?></h2>
      <div class="tit-table-scroll tit-press-scroll"><table class="tit-press-table">
        <thead><tr><th scope="col">City</th><th scope="col">Index</th><th scope="col">Adding roles</th><th scope="col">Cutting roles</th><th scope="col">Employers with an update</th></tr></thead>
        <tbody>
        <?php foreach ($metros as $mt) :
            $purl = function_exists('tit_place_url') ? tit_place_url('city', $mt['city']) : ''; ?>
          <tr><td><?php echo $purl ? '<a href="' . esc_url($purl) . '">' . esc_html($mt['city']) . '</a>' : esc_html($mt['city']); ?></td>
            <td><?php echo esc_html(number_format_i18n($mt['value'], 1)); ?></td>
            <td><?php echo esc_html(number_format_i18n($mt['adding'])); ?></td>
            <td><?php echo esc_html(number_format_i18n($mt['cutting'])); ?></td>
            <td><?php echo esc_html(number_format_i18n($mt['active'])); ?></td></tr>
        <?php endforeach; ?>
        </tbody></table></div>
      <?php endif; ?>

      <h2>Every month</h2>
      <div class="tit-table-scroll tit-press-scroll"><table class="tit-press-table">
        <thead><tr><th scope="col">Month</th><th scope="col">Index</th><th scope="col">Change</th><th scope="col">Adding roles</th><th scope="col">Employers with an update</th></tr></thead>
        <tbody>
        <?php foreach (array_reverse($months) as $m => $r) : if ($r['value'] === null) continue; ?>
          <tr><td><a href="<?php echo esc_url(tit_net_index_url($m)); ?>"><?php echo esc_html(tit_net_index_month_label($m)); ?></a></td>
            <td><?php echo esc_html(number_format_i18n($r['value'], 1)); ?></td>
            <td><?php echo $r['change'] === null ? '' : esc_html(($r['change'] >= 0 ? '+' : '-') . number_format_i18n(abs($r['change']), 1)); ?></td>
            <td><?php echo esc_html(number_format_i18n($r['adding'])); ?></td>
            <td><?php echo esc_html(number_format_i18n($r['active'])); ?></td></tr>
        <?php endforeach; ?>
        </tbody></table></div>

      <p class="tit-cite">Data licensed CC BY 4.0. Cite as: Talent Intelligence Tracker, Net Talent Index,
        <?php echo esc_html(tit_net_index_month_label($month)); ?>, asktherecruiter.com,
        <?php echo esc_html($url); ?></p>
      <script type="application/ld+json"><?php
        echo wp_json_encode(tit_net_index_dataset_node($months, $is_hub ? '' : $month, $url),
                            JSON_UNESCAPED_SLASHES | JSON_HEX_TAG | JSON_HEX_AMP);
      ?></script>
    </div>
    <?php
    if (function_exists('tit_render_footer')) tit_render_footer('net-index'); else get_footer();
}

function tit_net_index_title($title) {
    $month = get_query_var('tit_net_index') ? tit_net_index_current_month() : '';
    return $month !== '' ? 'Net Talent Index, ' . tit_net_index_month_label($month) . ' · Talent Intelligence Tracker' : $title;
}
add_filter('pre_get_document_title', 'tit_net_index_title');

function tit_net_index_head() {
    if (!get_query_var('tit_net_index')) return;
    $month = tit_net_index_current_month();
    if ($month === '') return;
    $months = tit_net_index_data();
    $desc = tit_net_index_headline_sentence($months[$month]);
    if (function_exists('tit_seo_cap_description')) $desc = tit_seo_cap_description($desc);
    $url = get_query_var('tit_net_index') === 'latest' ? tit_net_index_url() : tit_net_index_url($month);
    echo "\n" . '<meta name="description" content="' . esc_attr($desc) . '" />' . "\n";
    echo '<link rel="canonical" href="' . esc_url($url) . '" />' . "\n";
    if (function_exists('tit_seo_og_echo')) tit_seo_og_echo($desc, $url);
}
add_action('wp_head', 'tit_net_index_head', 1);
