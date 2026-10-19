<?php
/**
 * FUNDED THIS WEEK, NOW HIRING (owner approved "go roundup", 2026-10-19).
 *
 *   /talent-intelligence-tracker/funded-this-week/             the latest week
 *   /talent-intelligence-tracker/funded-this-week/YYYY-MM-DD/  one week, by its Monday
 *
 * A weekly roundup of the company raises the dashboard's "Recently funded, now
 * hiring" card shows, as a page that can be linked, shared and indexed.
 *
 * RENDERED LIVE, SO NOTHING WRITES AND NOTHING IS SCHEDULED. A week is Monday
 * to Sunday UTC, and the stable URL always shows the last COMPLETED week, so
 * the roundup "generates" at 00:00 UTC every Monday by the calendar alone. A
 * scheduled job would have to be a writer (a stored snapshot) to add anything,
 * and a stored snapshot would preserve figures this tracker has since
 * corrected: corrections append a revision, and the current rows are what we
 * now believe. Same argument as the press page's archive.
 *
 * REUSE, NOT A SECOND COPY. The rows come from tit_raises_between(), the query
 * the dashboard's top-rounds card runs, and each row's company page and jobs
 * link come from tit_funded_links() (by_location.php). Money is company_raise
 * only, through tit_money_where(), by name.
 *
 * NO MODEL CALL. The intro is counts and totals computed below. No reader
 * email is sent from here: there is no subscriber relay in this repo, and
 * reader mail must never ride the ops channel.
 */

if (!defined('ABSPATH')) exit;

const TIT_FUNDED_WEEK_PATH = 'talent-intelligence-tracker/funded-this-week';
/** The Monday of the first week the collectors ran a whole week (July 2026). */
const TIT_FUNDED_WEEK_FIRST = '2026-07-06';
/** Rows listed per week. The intro's counts and totals cover every row. */
const TIT_FUNDED_WEEK_LIMIT = 50;

function tit_funded_week_rewrites() {
    $p = TIT_FUNDED_WEEK_PATH;
    return array(
        '^' . $p . '/(\d{4}-\d{2}-\d{2})/?$' => 'index.php?tit_funded_week=$matches[1]',
        '^' . $p . '/?$' => 'index.php?tit_funded_week=latest',
    );
}

function tit_funded_week_rewrite() {
    foreach (tit_funded_week_rewrites() as $pattern => $target) add_rewrite_rule($pattern, $target, 'top');
}
add_action('init', 'tit_funded_week_rewrite');

function tit_funded_week_query_var($vars) {
    $vars[] = 'tit_funded_week';
    return $vars;
}
add_filter('query_vars', 'tit_funded_week_query_var');

function tit_funded_week_needs_no_js($needs) {
    return get_query_var('tit_funded_week') ? false : $needs;
}
add_filter('tit_route_needs_js', 'tit_funded_week_needs_no_js');

/** The stable URL, or one week's archive URL when given its Monday. */
function tit_funded_week_url($monday = '') {
    return home_url('/' . TIT_FUNDED_WEEK_PATH . '/' . ($monday !== '' ? $monday . '/' : ''));
}

/** Monday of the last completed Monday-to-Sunday week, for a Y-m-d "today". */
function tit_funded_week_latest($today) {
    $t = strtotime($today . ' 00:00:00 UTC');
    $dow = (int) gmdate('N', $t);                       // 1 = Monday
    return gmdate('Y-m-d', $t - ($dow - 1 + 7) * DAY_IN_SECONDS);
}

/**
 * Resolve a query value to a week's Monday, or '' when there is no such page:
 * not a date, not a Monday, before the collectors ran, or a week not yet over.
 */
function tit_funded_week_resolve($value, $today) {
    $latest = tit_funded_week_latest($today);
    if ($value === 'latest') return $latest;
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', (string) $value)) return '';
    $t = strtotime($value . ' 00:00:00 UTC');
    if ($t === false || gmdate('Y-m-d', $t) !== $value || gmdate('N', $t) !== '1') return '';
    if ($value < TIT_FUNDED_WEEK_FIRST || $value > $latest) return '';
    return $value;
}

/** Every completed week's Monday, newest first, back to the first. */
function tit_funded_week_mondays($today, $max = 12) {
    $out = array();
    $m = tit_funded_week_latest($today);
    while ($m >= TIT_FUNDED_WEEK_FIRST && count($out) < $max) {
        $out[] = $m;
        $m = gmdate('Y-m-d', strtotime($m . ' 00:00:00 UTC') - 7 * DAY_IN_SECONDS);
    }
    return $out;
}

/**
 * One week's roundup: the rows, largest first, and the figures the intro
 * states. Cached per week and version; the latest week also per day, because
 * a late correction can still land on it.
 */
function tit_funded_week_facts($table, $monday) {
    $sunday = gmdate('Y-m-d', strtotime($monday . ' 00:00:00 UTC') + 6 * DAY_IN_SECONDS);
    $key = 'tit_fweek_' . md5(TIT_VERSION . '|' . $monday . '|' . current_time('Y-m-d'));
    $cached = get_transient($key);
    if (is_array($cached) && isset($cached['rows'])) return $cached;

    global $wpdb;
    // The dashboard's own default view: the same clause /aggregate builds for
    // a request with no filters, so this page and the card count the same rows.
    $params = array();
    // WP_REST_Request's first argument is the HTTP METHOD, a string. 1.111.0
    // passed array() here and real WordPress fataled (strtoupper on an array);
    // tests/php/funded_week.php now stubs the real signature.
    $where = tit_build_where(new WP_REST_Request('GET'), $params);
    $money = tit_money_where();
    $date_expr = tit_signal_date_expr();

    $rows = tit_raises_between($table, $where, $params, $monday, $sunday, TIT_FUNDED_WEEK_LIMIT);

    $head = $wpdb->get_row($wpdb->prepare(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT company_key) AS c,
                COALESCE(SUM(funding_amount_usd), 0) AS m,
                COUNT(DISTINCT " . tit_country_expr() . ") AS countries,
                MAX(captured_at) AS newest
           FROM {$table}
          WHERE {$where} AND {$money} AND funding_amount_usd IS NOT NULL
            AND funding_amount_usd > 0 AND {$date_expr} >= %s AND {$date_expr} <= %s",
        array_merge($params, array($monday, $sunday))), ARRAY_A) ?: array();

    $facts = array(
        'monday' => $monday, 'sunday' => $sunday, 'rows' => $rows,
        'n' => (int) ($head['n'] ?? 0), 'c' => (int) ($head['c'] ?? 0),
        'm' => (float) ($head['m'] ?? 0), 'countries' => (int) ($head['countries'] ?? 0),
        'newest' => (string) ($head['newest'] ?? ''),
    );
    set_transient($key, $facts, function_exists('tit_dash_ttl') ? tit_dash_ttl() : 300);
    return $facts;
}

/** "5 to 11 October 2026", or across a month or year boundary in full. */
function tit_funded_week_span($monday, $sunday) {
    $a = strtotime($monday . ' 00:00:00 UTC');
    $b = strtotime($sunday . ' 00:00:00 UTC');
    if (gmdate('Y-m', $a) === gmdate('Y-m', $b)) return gmdate('j', $a) . ' to ' . gmdate('j F Y', $b);
    if (gmdate('Y', $a) === gmdate('Y', $b)) return gmdate('j F', $a) . ' to ' . gmdate('j F Y', $b);
    return gmdate('j F Y', $a) . ' to ' . gmdate('j F Y', $b);
}

/** The intro: counts and totals only, every one computed. No model, no typing. */
function tit_funded_week_intro(array $f) {
    $span = tit_funded_week_span($f['monday'], $f['sunday']);
    $money = function ($v) {
        return function_exists('tit_money_short') ? tit_money_short($v) : '$' . number_format((float) $v);
    };
    if ($f['n'] === 0) {
        return 'We recorded no company raise with a stated US dollar amount dated ' . $span . '.';
    }
    $s = 'From ' . $span . ', we recorded ' . number_format_i18n($f['n'])
        . ($f['n'] === 1 ? ' company raise' : ' company raises')
        . ' with a stated US dollar amount, by ' . number_format_i18n($f['c'])
        . ($f['c'] === 1 ? ' employer' : ' employers');
    if ($f['countries'] > 0) {
        $s .= ' in ' . number_format_i18n($f['countries']) . ($f['countries'] === 1 ? ' country' : ' countries');
    }
    $s .= ', totalling ' . $money($f['m']) . '.';
    if (!empty($f['rows'][0]['company'])) {
        $s .= ' The largest was ' . $f['rows'][0]['company'] . ' at ' . $money($f['rows'][0]['amount']) . '.';
    }
    return $s;
}

/** Article + ItemList JSON-LD. No JobPosting: we list no job, only where to look. */
function tit_funded_week_jsonld(array $f, $url) {
    $items = array();
    foreach ($f['rows'] as $i => $r) {
        $items[] = array(
            '@type' => 'ListItem', 'position' => $i + 1,
            'name' => $r['company'],
            'url' => $r['company_url'] !== '' ? $r['company_url'] : $r['source_url'],
        );
    }
    $published = $f['sunday'] . 'T23:59:59Z';
    $modified = $f['newest'] !== '' ? gmdate('c', strtotime($f['newest'] . ' UTC')) : $published;
    if ($modified < $published) $modified = $published;
    return '<script type="application/ld+json">' . wp_json_encode(array(
        '@context' => 'https://schema.org',
        '@type' => 'Article',
        'headline' => 'Funded This Week, Now Hiring: ' . tit_funded_week_span($f['monday'], $f['sunday']),
        'description' => tit_funded_week_intro($f),
        'url' => $url,
        'mainEntityOfPage' => $url,
        'datePublished' => $published,
        'dateModified' => $modified,
        'author' => array('@type' => 'Organization', 'name' => 'Talent Intelligence Tracker'),
        'publisher' => array('@type' => 'Organization', 'name' => 'Ask The Recruiter', 'url' => 'https://asktherecruiter.com/'),
        'mainEntity' => array('@type' => 'ItemList', 'itemListOrder' => 'https://schema.org/ItemListOrderDescending',
                              'numberOfItems' => count($items), 'itemListElement' => $items),
    ), JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_HEX_TAG) . "</script>\n";
}

/** Where the "Tailor your resume" button goes. Attribution only, as on the card. */
function tit_funded_tailor_url() {
    return 'https://asktherecruiter.com/quick-draft?utm_source=talent-tracker&utm_medium=funded-week&utm_campaign=tailor';
}

/** The request's week, resolved once: the Monday, or '' for no such page. */
function tit_funded_week_current() {
    $v = get_query_var('tit_funded_week');
    if (!$v) return null;
    return tit_funded_week_resolve((string) $v, current_time('Y-m-d'));
}

function tit_funded_week_template() {
    $monday = tit_funded_week_current();
    if ($monday === null) return;
    if ($monday === '') {
        global $wp_query;
        if ($wp_query) $wp_query->set_404();
        status_header(404);
        return;
    }
    tit_funded_week_render(tit_funded_week_facts(tit_table_name(), $monday));
    exit;
}
add_action('template_redirect', 'tit_funded_week_template');

function tit_funded_week_render(array $f) {
    if (function_exists('tit_render_header')) tit_render_header(); else get_header();
    $money = function ($v) {
        return function_exists('tit_money_short') ? tit_money_short($v) : '$' . number_format((float) $v);
    };
    $span = tit_funded_week_span($f['monday'], $f['sunday']);
    ?>
    <div class="tit-wrap tit-fweek" id="tit-fweek">
      <?php if (function_exists('tit_breadcrumbs_html')) echo tit_breadcrumbs_html(tit_breadcrumbs_section_trail('Funded This Week', tit_funded_week_url())); ?>

      <h1 data-tit-route-heading>Funded This Week, Now Hiring</h1>
      <p class="tit-note"><strong><?php echo esc_html($span); ?>.</strong>
        <?php echo esc_html(tit_funded_week_intro($f)); ?></p>
      <p class="tit-note">
        Company raises only: a sale price, a fund close or a government award
        is real news and is not money the employer raised, so it is not here.
        Every row links to the document behind it. A new list starts every
        Monday.
      </p>

      <?php if ($f['rows']) : ?>
      <ol class="tit-tr-list">
        <?php foreach ($f['rows'] as $r) :
          $place = array_filter(array($r['city'], $r['country'] !== ''
              ? (function_exists('tit_country_name') ? tit_country_name($r['country']) : $r['country']) : ''));
          $date = $r['date'] !== '' ? gmdate('j M', strtotime($r['date'] . ' 00:00:00 UTC')) : ''; ?>
        <li class="tit-tr-item">
          <span class="tit-tr-amt"><?php echo esc_html($money($r['amount'])); ?></span>
          <?php if ($r['company_url'] !== '') : ?>
            <a class="tit-tr-co" href="<?php echo esc_url($r['company_url']); ?>"><?php echo esc_html($r['company']); ?></a>
          <?php else : ?>
            <span class="tit-tr-co"><?php echo esc_html($r['company']); ?></span>
          <?php endif; ?>
          <span class="tit-tr-meta"><?php echo esc_html(implode(' · ', array_filter(array(implode(', ', $place), $date)))); ?></span>
          <span class="tit-tr-links">
            <?php if ($r['jobs_url'] !== '') : ?>
              <a class="tit-tr-jobs" href="<?php echo esc_url($r['jobs_url']); ?>" target="_blank" rel="noopener nofollow"><?php
                echo $r['jobs_kind'] === 'board' ? 'See jobs' : 'Search jobs'; ?></a>
            <?php endif; ?>
            <?php if ($r['source_url'] !== '') : ?>
              <a class="tit-tr-src" href="<?php echo esc_url($r['source_url']); ?>" target="_blank" rel="noopener nofollow">Source</a>
            <?php endif; ?>
            <a class="tit-tr-tailor" href="<?php echo esc_url(tit_funded_tailor_url()); ?>" target="_blank" rel="noopener">Tailor your resume for this company</a>
          </span>
        </li>
        <?php endforeach; ?>
      </ol>
      <?php if ($f['n'] > count($f['rows'])) : ?>
        <p class="tit-note">The <?php echo esc_html(number_format_i18n(count($f['rows']))); ?> largest are listed;
          the figures above count all <?php echo esc_html(number_format_i18n($f['n'])); ?>.</p>
      <?php endif; ?>
      <?php endif; ?>

      <p class="tit-note">
        "See jobs" opens the employer's own job board where we read one.
        "Search jobs" is a job search for the name, not the company's careers
        page. Dollar totals cover raises stated in US dollars; we do not convert
        other currencies.
      </p>

      <h2 id="fweek-archive">Earlier Weeks</h2>
      <ul class="tit-fweek-archive">
        <?php foreach (tit_funded_week_mondays(current_time('Y-m-d')) as $m) :
          if ($m === $f['monday']) continue;
          $s = gmdate('Y-m-d', strtotime($m . ' 00:00:00 UTC') + 6 * DAY_IN_SECONDS); ?>
          <li><a href="<?php echo esc_url(tit_funded_week_url($m)); ?>"><?php echo esc_html(tit_funded_week_span($m, $s)); ?></a></li>
        <?php endforeach; ?>
      </ul>

      <p class="tit-cite">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Back to the tracker</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/corrections/')); ?>">Corrections</a>
      </p>
    </div>
    <?php
    if (function_exists('tit_render_footer')) tit_render_footer('funded-week'); else get_footer();
}

function tit_funded_week_title($title) {
    $monday = tit_funded_week_current();
    if (!$monday) return $title;
    $sunday = gmdate('Y-m-d', strtotime($monday . ' 00:00:00 UTC') + 6 * DAY_IN_SECONDS);
    return 'Funded This Week, Now Hiring: ' . tit_funded_week_span($monday, $sunday)
        . ' &middot; Talent Intelligence Tracker';
}
add_filter('pre_get_document_title', 'tit_funded_week_title');

function tit_funded_week_head() {
    $monday = tit_funded_week_current();
    if (!$monday) return;
    $f = tit_funded_week_facts(tit_table_name(), $monday);
    // The latest week lives at two URLs; the dated one is canonical, so a link
    // shared on Monday still means the same list next month.
    $canonical = tit_funded_week_url($monday);
    if (function_exists('tit_head_description')) tit_head_description(tit_funded_week_intro($f));
    echo '<link rel="canonical" href="' . esc_url($canonical) . '" />' . "\n";
    if ($f['n'] === 0) echo '<meta name="robots" content="noindex, follow" />' . "\n";
    echo tit_funded_week_jsonld($f, $canonical);
}
add_action('wp_head', 'tit_funded_week_head', 1);
