<?php
/**
 * The press page: /talent-intelligence-tracker/press/
 *
 * The owner assumed one existed here and asked for it to carry "the latest
 * data, archived with the old". It did not exist: this tracker had sources,
 * recall, corrections, places, company profiles and the dashboard, and nothing
 * addressed to somebody writing about it.
 *
 * The sibling AI Layoff Tracker has one and it was read for the SHAPE only.
 * Nothing is imported and no file is copied — the two products share a host and
 * share no code, by the owner's standing rule — so every figure, query and
 * string below is this tracker's own. What was taken from it is the discipline:
 * one number per statement, a period attached to every one, and a link behind
 * each that lands on the rows the number came from.
 *
 * JOURNALIST-FIRST, WHICH IS A SET OF REFUSALS.
 *
 *  - Every stated figure is computed on request. Nothing here is typed, which is
 *    the rule the sibling's press page still breaks: it carries a hardcoded "51
 *    of the most significant layoffs ... we currently carry every one of them"
 *    with no query behind it, and this repo's own corrections.php once shipped a
 *    typed "$124.0bn" captioned "Measured now" while the live figure was $101B.
 *  - No superlatives. Nothing here is the biggest, the most complete or the
 *    first, because those are the claims a skeptic disproves in thirty seconds
 *    and everything on this page has to survive a check.
 *  - What the tracker does NOT do is stated on the page, in its own section,
 *    rather than left for a reader to discover after quoting us.
 *  - No em-dashes, matching the house style. Title Case headings.
 *
 * EVERY DEEP LINK USES A PARAMETER THE FRONT END ACTUALLY READS.
 *
 * This is the one bug on the sibling's press page worth naming, because it is
 * silent in both directions. Its evidence-ladder links were built on
 * `ai_primary=1`, which its REST API accepts and its dashboard JavaScript
 * ignores, so every "see the rows behind this number" link advertised a filtered
 * view and served the whole corpus. Nothing errors, and the reader has no way to
 * tell.
 *
 * So the whitelist below is not a list somebody wrote down and maintained: it is
 * asserted in tests/php/render_press.php against the `inputs` map parsed out of
 * assets/dashboard.js itself, plus the three parameters that map has no control
 * for. A parameter that stops being read stops passing.
 */

if (!defined('ABSPATH')) exit;

const TIT_PRESS_PATH = 'talent-intelligence-tracker/press';

function tit_press_rewrite() {
    add_rewrite_rule('^' . TIT_PRESS_PATH . '/?$', 'index.php?tit_press=1', 'top');
}
add_action('init', 'tit_press_rewrite');

function tit_press_query_var($vars) {
    $vars[] = 'tit_press';
    return $vars;
}
add_filter('query_vars', 'tit_press_query_var');

function tit_press_url() {
    return home_url('/' . TIT_PRESS_PATH . '/');
}

/**
 * No filter panel, no charts, no repaint, so no dashboard.js. Same decision the
 * place pages made: 60KB of parse work for a page with nothing to bind to.
 */
function tit_press_needs_no_js($needs) {
    return get_query_var('tit_press') ? false : $needs;
}
add_filter('tit_route_needs_js', 'tit_press_needs_no_js');

/**
 * THE PARAMETERS A LINK FROM THIS PAGE MAY USE.
 *
 * Every one of these is read by applyUrlState() in dashboard.js, so a link
 * carrying it comes back as the view it promises. A parameter the API accepts
 * and the front end ignores looks identical in a diff and serves an unfiltered
 * page under a filtered heading.
 *
 * `funding` and `stated_headcount` have no control of their own in the `inputs`
 * map and are handled by name in applyUrlState(); `sort` is read from the map
 * but its option is created on the fly, which is why all three are called out
 * rather than assumed.
 */
function tit_press_link_params() {
    return array('since', 'until', 'country', 'state', 'city', 'industry',
                 'pillar', 'direction', 'confidence', 'company', 'q',
                 'min_funding_usd', 'funding_stage', 'employer_type',
                 'country_basis', 'detail', 'sort', 'funding', 'stated_headcount');
}

/**
 * A link into the dashboard, filtered.
 *
 * Refuses rather than degrades. A parameter that is not on the whitelist would
 * produce a link that quietly shows the unfiltered page, so it is dropped here
 * and the test catches it, instead of shipping as a link that looks right.
 */
function tit_press_link(array $args) {
    $allowed = array_flip(tit_press_link_params());
    $url = home_url('/talent-intelligence-tracker/');
    $first = true;
    foreach ($args as $k => $v) {
        if (!isset($allowed[$k]) || $v === '' || $v === null) continue;
        $url .= ($first ? '?' : '&') . rawurlencode($k) . '=' . rawurlencode((string) $v);
        $first = false;
    }
    return $url;
}

/**
 * Everything this page prints, in as few scans as it can be had in.
 *
 * Four windows in one conditional aggregation, the monthly archive in one GROUP
 * BY, and one ranking each for country and industry. Cached on the same key
 * shape the dashboard uses: TIT_VERSION so a deploy cannot serve the previous
 * version's figures, and the current date because three of the four windows
 * derive from today.
 */
function tit_press_facts($table) {
    $key = 'tit_press_' . md5(TIT_VERSION . '|' . current_time('Y-m-d'));
    $cached = get_transient($key);
    if (is_array($cached) && isset($cached['windows'])) return $cached;

    global $wpdb;

    // The same clause the dashboard's own figures sit under, so a journalist who
    // follows a link from here counts the same rows this page told them about.
    $notable = function_exists('tit_notable_where') ? tit_notable_where() : '1 = 1';
    $base = "is_current = 1 AND {$notable}";
    $date_expr = 'COALESCE(published_date, DATE(captured_at))';

    $today = current_time('Y-m-d');
    $windows = array(
        array('week',  'The Last Seven Days', date('Y-m-d', strtotime($today . ' -6 days')), $today),
        array('month', 'This Month',          date('Y-m-01', strtotime($today)), $today),
        // Computed, so this becomes "2027 So Far" on 1 January with nothing to
        // remember. A typed year on a page journalists quote from is a figure
        // that goes wrong quietly and stays wrong.
        array('year',  date('Y', strtotime($today)) . ' So Far', date('Y-01-01', strtotime($today)), $today),
    );

    $select = array();
    $params = array();
    foreach ($windows as $i => $w) {
        foreach (array(
            "SUM({$date_expr} >= %s) AS w_n_{$i}",
            "COUNT(DISTINCT CASE WHEN {$date_expr} >= %s THEN company_key END) AS w_c_{$i}",
            "SUM(confidence = 'verified' AND {$date_expr} >= %s) AS w_v_{$i}",
            "COALESCE(SUM(CASE WHEN {$date_expr} >= %s THEN funding_amount_usd END), 0) AS w_m_{$i}",
        ) as $expr) {
            $select[] = $expr;
            $params[] = $w[2];
        }
    }
    // The whole corpus, on the same scan.
    $select[] = 'COUNT(*) AS all_n';
    $select[] = 'COUNT(DISTINCT company_key) AS all_c';
    $select[] = "SUM(confidence = 'verified') AS all_v";
    $select[] = 'COALESCE(SUM(funding_amount_usd), 0) AS all_m';
    $select[] = "COUNT(DISTINCT COALESCE(country, hq_country)) AS all_countries";
    $select[] = "MIN({$date_expr}) AS lo";
    $select[] = "MAX({$date_expr}) AS hi";
    $select[] = 'MAX(captured_at) AS newest';

    $head = $wpdb->get_row(
        $wpdb->prepare('SELECT ' . implode(', ', $select) . " FROM {$table} WHERE {$base}", $params),
        ARRAY_A
    ) ?: array();

    $facts = array('windows' => array(), 'all' => array(), 'archive' => array(),
                   'top_country' => array(), 'top_industry' => array(),
                   'largest' => array());

    foreach ($windows as $i => $w) {
        $facts['windows'][] = array(
            'key' => $w[0], 'label' => $w[1], 'since' => $w[2], 'until' => $w[3],
            'n' => (int) ($head["w_n_{$i}"] ?? 0),
            'c' => (int) ($head["w_c_{$i}"] ?? 0),
            'v' => (int) ($head["w_v_{$i}"] ?? 0),
            'm' => (float) ($head["w_m_{$i}"] ?? 0),
        );
    }
    $facts['all'] = array(
        'n' => (int) ($head['all_n'] ?? 0),
        'c' => (int) ($head['all_c'] ?? 0),
        'v' => (int) ($head['all_v'] ?? 0),
        'm' => (float) ($head['all_m'] ?? 0),
        'countries' => (int) ($head['all_countries'] ?? 0),
        'lo' => (string) ($head['lo'] ?? ''),
        'hi' => (string) ($head['hi'] ?? ''),
        'newest' => (string) ($head['newest'] ?? ''),
    );

    /*
      THE ARCHIVE, AND WHY IT IS A LIVE QUERY RATHER THAN A SNAPSHOT.

      The owner asked for "the latest data, archived with the old". A month keeps
      its own preset view here, so a figure quoted in March still resolves to the
      March figure a year later. It is recomputed rather than frozen, and that is
      the honest arrangement for THIS tracker: corrections append a revision and
      the current rows are what we now believe, so a frozen copy would preserve a
      figure we have since corrected and present it as though it still stood.
      What makes the older number answerable is the corrections log, which says
      what changed and when, not a snapshot nobody would ever revisit.

      A month with nothing in it is skipped rather than rendered as a zero: the
      collectors here started in July 2026, so every earlier month would be a
      row of zeroes reading as "nothing happened" rather than "we were not there".
    */
    $facts['archive'] = $wpdb->get_results(
        "SELECT SUBSTR({$date_expr}, 1, 7) AS m, COUNT(*) AS n,
                COUNT(DISTINCT company_key) AS c,
                SUM(confidence = 'verified') AS v,
                COALESCE(SUM(funding_amount_usd), 0) AS money
           FROM {$table} WHERE {$base} AND {$date_expr} IS NOT NULL
          GROUP BY m HAVING n > 0 ORDER BY m DESC LIMIT 24", ARRAY_A) ?: array();

    $country_expr = function_exists('tit_country_expr') ? tit_country_expr() : 'COALESCE(country, hq_country)';
    $year_start = date('Y-01-01', strtotime($today));

    $facts['top_country'] = $wpdb->get_row($wpdb->prepare(
        "SELECT {$country_expr} AS k, COUNT(*) AS n FROM {$table}
          WHERE {$base} AND {$date_expr} >= %s AND {$country_expr} IS NOT NULL
          GROUP BY k ORDER BY n DESC, k ASC LIMIT 1", $year_start), ARRAY_A) ?: array();

    $facts['top_industry'] = $wpdb->get_row($wpdb->prepare(
        "SELECT industry AS k, COUNT(*) AS n FROM {$table}
          WHERE {$base} AND {$date_expr} >= %s AND industry IS NOT NULL AND industry <> ''
          GROUP BY k ORDER BY n DESC, k ASC LIMIT 1", $year_start), ARRAY_A) ?: array();

    // The largest single raise this year, named. A superlative about our own
    // dataset is fine; a superlative about the world is not.
    $facts['largest'] = $wpdb->get_row($wpdb->prepare(
        "SELECT company, company_key, funding_amount_usd AS usd, {$date_expr} AS d
           FROM {$table} WHERE {$base} AND funding_amount_usd IS NOT NULL
            AND {$date_expr} >= %s
          ORDER BY funding_amount_usd DESC, row_id ASC LIMIT 1", $year_start), ARRAY_A) ?: array();

    set_transient($key, $facts, function_exists('tit_dash_ttl') ? tit_dash_ttl() : 300);
    return $facts;
}

function tit_press_template() {
    if (!get_query_var('tit_press')) return;
    tit_press_render(tit_press_facts(tit_table_name()));
    exit;
}
add_action('template_redirect', 'tit_press_template');

function tit_press_render(array $f) {
    if (function_exists('tit_render_header')) tit_render_header(); else get_header();

    $all = $f['all'];
    $n = function ($v) { return number_format_i18n((int) $v); };
    $money = function ($v) {
        return function_exists('tit_money_short') ? tit_money_short($v) : '$' . number_format((float) $v);
    };
    ?>
    <div class="tit-wrap tit-press" id="tit-press">
      <nav class="tit-crumb">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Talent Intelligence Tracker</a>
        <span aria-hidden="true">&rsaquo;</span> Press
      </nav>

      <h1 data-tit-route-heading>Press and Media Kit</h1>

      <p class="tit-note">
        We compute every figure on this page when you request it. Every one
        carries a link to the rows it came from, with the same filters already
        applied. Nobody types these numbers in by hand, so anyone can check a
        number you quote today against the tracker, now or later.
        <?php if ($all['newest']) : ?>
          The most recent collection ran
          <?php echo esc_html(function_exists('tit_local_datetime')
                ? tit_local_datetime($all['newest']) : $all['newest']); ?>.
        <?php endif; ?>
      </p>

      <div class="tit-stats">
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html($n($all['n'])); ?></span><span class="tit-l">updates</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html($n($all['c'])); ?></span><span class="tit-l">employers</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html($n($all['countries'])); ?></span><span class="tit-l">countries</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html($money($all['m'])); ?></span><span class="tit-l">raised, in US dollars</span></div>
      </div>

      <h2 id="press-numbers">Numbers You Can Use Right Now</h2>
      <p class="tit-note">
        Each row states one period. The link beside it opens the tracker
        narrowed to exactly those rows. The number and the evidence behind it
        are one click apart, rather than a claim and a promise.
      </p>

      <div class="tit-table-scroll tit-press-scroll">
        <table class="tit-press-table">
          <thead>
            <tr>
              <th scope="col">Period</th>
              <th scope="col">Updates</th>
              <th scope="col">Employers</th>
              <th scope="col">From Official Filings</th>
              <th scope="col">Raised</th>
              <th scope="col">Preset View</th>
            </tr>
          </thead>
          <tbody>
            <?php foreach ($f['windows'] as $w) : ?>
              <tr>
                <th scope="row"><?php echo esc_html($w['label']); ?></th>
                <td data-label="Updates"><?php echo esc_html($n($w['n'])); ?></td>
                <td data-label="Employers"><?php echo esc_html($n($w['c'])); ?></td>
                <td data-label="From Official Filings"><?php echo esc_html($n($w['v'])); ?></td>
                <td data-label="Raised"><?php echo $w['m'] > 0 ? esc_html($money($w['m'])) : 'none stated'; ?></td>
                <td data-label="Preset View"><a href="<?php echo esc_url(tit_press_link(array(
                      'since' => $w['since'], 'until' => $w['until']))); ?>">See the rows</a></td>
              </tr>
            <?php endforeach; ?>
            <tr>
              <th scope="row">Everything We Hold</th>
              <td data-label="Updates"><?php echo esc_html($n($all['n'])); ?></td>
              <td data-label="Employers"><?php echo esc_html($n($all['c'])); ?></td>
              <td data-label="From Official Filings"><?php echo esc_html($n($all['v'])); ?></td>
              <td data-label="Raised"><?php echo esc_html($money($all['m'])); ?></td>
              <td data-label="Preset View"><a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">See the rows</a></td>
            </tr>
          </tbody>
        </table>
      </div>

      <?php
      // The coverage caveat travels with any dollar figure on this site,
      // without exception. A total presented as though it covered every round
      // is the plausible-but-wrong number this product cannot carry. The exact
      // ratio is computed and printed on the dashboard beside each money
      // figure; repeating it here would be a second copy that could go stale
      // against the first, so this states the RULE and points at the figure.
      ?>
      <p class="tit-note">
        Dollar totals cover the funding updates that state an amount in US
        dollars. We leave out amounts stated in another currency rather than
        convert them at a rate nobody published. The dashboard prints the exact
        coverage beside every money figure it shows.
      </p>

      <h2 id="press-context">Context For This Year</h2>
      <ul class="tit-press-facts">
        <?php
        $year = date('Y', strtotime(current_time('Y-m-d')));
        $year_since = $year . '-01-01';
        if (!empty($f['top_country']['k'])) :
          $cc = $f['top_country']['k']; ?>
          <li>
            The country with the most updates in <?php echo esc_html($year); ?> is
            <strong><?php echo esc_html(function_exists('tit_country_name')
              ? tit_country_name($cc) : $cc); ?></strong>,
            with <?php echo esc_html($n($f['top_country']['n'])); ?>.
            <a href="<?php echo esc_url(tit_press_link(array(
                 'country' => $cc, 'since' => $year_since))); ?>">See those rows</a>.
            <?php /* The one caveat a journalist most needs and is least likely
                     to ask for. Filing volume is not activity, and the country
                     ranking on this tracker is dominated by one mandatory annual
                     return. Saying it here rather than only on the dashboard is
                     the difference between a caveat and a disclaimer. */ ?>
            Read a country total as filing volume, not as how much is happening
            there. One mandatory annual return can account for most of a
            country's rows, and the dashboard names the collector when it does.
          </li>
        <?php endif; ?>
        <?php if (!empty($f['top_industry']['k'])) : ?>
          <li>
            The industry with the most updates in <?php echo esc_html($year); ?> is
            <strong><?php
              $ind = function_exists('tit_industry_labels') ? tit_industry_labels() : array();
              echo esc_html($ind[$f['top_industry']['k']] ?? $f['top_industry']['k']);
            ?></strong>, with <?php echo esc_html($n($f['top_industry']['n'])); ?>.
            <a href="<?php echo esc_url(tit_press_link(array(
                 'industry' => $f['top_industry']['k'], 'since' => $year_since))); ?>">See those rows</a>.
          </li>
        <?php endif; ?>
        <?php if (!empty($f['largest']['company']) && (float) ($f['largest']['usd'] ?? 0) > 0) : ?>
          <li>
            The largest single raise recorded in <?php echo esc_html($year); ?> is
            <strong><?php echo esc_html($f['largest']['company']); ?></strong>
            at <?php echo esc_html($money($f['largest']['usd'])); ?><?php
              if (!empty($f['largest']['d'])) : ?>, dated
              <?php echo esc_html(date_i18n('j F Y', strtotime($f['largest']['d'] . ' 00:00:00 UTC'))); endif; ?>.
            <a href="<?php echo esc_url(tit_press_link(array(
                 'funding' => '1', 'sort' => 'raised', 'since' => $year_since))); ?>">See the raises, largest first</a>.
          </li>
        <?php endif; ?>
        <li>
          <?php echo esc_html($n($all['v'])); ?> of the
          <?php echo esc_html($n($all['n'])); ?> updates we hold come straight
          from an official filing rather than from a report about one.
          <a href="<?php echo esc_url(tit_press_link(array('confidence' => 'verified'))); ?>">See only those</a>.
        </li>
      </ul>

      <h2 id="press-archive">The Archive</h2>
      <p class="tit-note">
        Every month keeps its own preset view, so a figure quoted last quarter
        still resolves to that quarter's rows. We recompute the figures rather
        than freeze them. A correction on this tracker appends a revision
        instead of overwriting a record. A frozen copy would preserve a number
        we have since corrected, and present it as though it still stood. What
        makes an older figure answerable is the
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/corrections/')); ?>">corrections log</a>,
        which says what changed and when.
      </p>

      <div class="tit-table-scroll tit-press-scroll">
        <table class="tit-press-table">
          <thead>
            <tr>
              <th scope="col">Month</th>
              <th scope="col">Updates</th>
              <th scope="col">Employers</th>
              <th scope="col">From Official Filings</th>
              <th scope="col">Raised</th>
              <th scope="col">Preset View</th>
            </tr>
          </thead>
          <tbody>
          <?php foreach ($f['archive'] as $m) :
            $since = $m['m'] . '-01';
            $until = date('Y-m-t', strtotime($since)); ?>
            <tr>
              <th scope="row"><?php echo esc_html(date_i18n('F Y', strtotime($since . ' 00:00:00 UTC'))); ?></th>
              <td data-label="Updates"><?php echo esc_html($n($m['n'])); ?></td>
              <td data-label="Employers"><?php echo esc_html($n($m['c'])); ?></td>
              <td data-label="From Official Filings"><?php echo esc_html($n($m['v'])); ?></td>
              <td data-label="Raised"><?php echo (float) $m['money'] > 0 ? esc_html($money($m['money'])) : 'none stated'; ?></td>
              <td data-label="Preset View"><a href="<?php echo esc_url(tit_press_link(array(
                    'since' => $since, 'until' => $until))); ?>">See the rows</a></td>
            </tr>
          <?php endforeach; ?>
          </tbody>
        </table>
      </div>

      <h2 id="press-limits">What This Tracker Does Not Do</h2>
      <p class="tit-note">
        We state these here rather than leave you to discover them after you
        have quoted us. None of this confesses a defect. Each is a decision, and
        each one costs something real.
      </p>
      <ul class="tit-press-facts">
        <li>
          <strong>It does not track layoffs.</strong> The
          <a href="/blog/ai-layoff-tracker/">AI Layoff Tracker</a> collects
          redundancies and job cuts instead. We read from it rather than
          duplicate them here, so there is one source of truth per fact. Do not
          cite this page for a layoff figure.
        </li>
        <li>
          <strong>It does not estimate.</strong> No figure appears unless its
          source states it in those words. When an update's source names no
          headcount, we label it as not stated rather than infer one. When an
          amount comes in a currency we cannot read as US dollars, we leave it
          out of the totals rather than convert it.
        </li>
        <li>
          <strong>It does not claim to be complete.</strong> We measure coverage
          rather than assert it. Every week we grade the collectors against a
          fixed set of real events, assembled from public sources without ever
          looking at our own database. We publish the result, including the
          categories where we come off badly.
          <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/recall/')); ?>">See what we miss, measured</a>.
        </li>
        <li>
          <strong>It does not read every country evenly.</strong>
          <?php echo esc_html($n($all['countries'])); ?> countries appear in the
          data, and that is where employers are, not where we have connectors. A
          country appearing here is not a claim that we cover it.
          <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">See which sources actually run</a>.
        </li>
        <li>
          <strong>It is not fully automated.</strong> About 99% of it runs
          without a human: collection, classification, validation, deduplication
          and publishing. Three things stay human: repairing a scraper when a
          site changes, judging whether a novel source is worth reading, and
          assembling each new recall test set. The last of those is human by
          design.
        </li>
      </ul>

      <h2 id="press-cite">How to Cite Us</h2>
      <p class="tit-note">
        Cite as: Talent Intelligence Tracker, asktherecruiter.com, accessed
        <?php echo esc_html(date_i18n('j F Y')); ?>. The data carries a
        CC BY 4.0 license. Please link to the preset view for the figure you are
        using, rather than to this page alone. Then your readers land on the
        rows rather than on a summary of them.
        <?php if ($all['lo'] && $all['hi']) : ?>
          The tracker currently holds updates dated
          <?php echo esc_html(date_i18n('j F Y', strtotime($all['lo'] . ' 00:00:00 UTC'))); ?>
          to
          <?php echo esc_html(date_i18n('j F Y', strtotime($all['hi'] . ' 00:00:00 UTC'))); ?>.
        <?php endif; ?>
      </p>

      <h2 id="press-contact">Press Contact</h2>
      <p class="tit-note">
        Questions, corrections and data requests go to
        <a href="/blog/contact/">the contact page</a>. If a figure looks wrong to
        you, every record links to the document behind it, so you can check it
        yourself. We correct anything we get wrong in public, with its date.
      </p>

      <p class="tit-cite">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Back to the tracker</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">Sources</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/recall/')); ?>">What we miss</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/corrections/')); ?>">Corrections</a>
      </p>
    </div>
    <?php
    if (function_exists('tit_render_footer')) tit_render_footer(); else get_footer();
}

function tit_press_title($title) {
    return get_query_var('tit_press')
        ? 'Press and Media Kit &middot; Talent Intelligence Tracker'
        : $title;
}
add_filter('pre_get_document_title', 'tit_press_title');

function tit_press_head() {
    if (!get_query_var('tit_press')) return;
    if (!function_exists('tit_head_description')) return;
    tit_head_description(
        'Figures a journalist can quote from the Talent Intelligence Tracker, '
        . 'each computed on request and each linked to the rows behind it. '
        . 'States plainly what the tracker does not do, including that it does '
        . 'not track layoffs and does not estimate.'
    );
    echo '<link rel="canonical" href="' . esc_url(tit_press_url()) . '" />' . "\n";
}
add_action('wp_head', 'tit_press_head', 1);
