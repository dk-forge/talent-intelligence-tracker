<?php
/**
 * The recall page: /talent-intelligence-tracker/recall/
 *
 * This page publishes what the tracker MISSES. It renders `data/recall.json`
 * and nothing else, so what a reader sees is exactly what `measure_recall.py`
 * measured, on the date it says, against the gold set digest it names.
 *
 * Nothing here is hand-typed. A hand-typed recall figure is a claim; a rendered
 * measurement is a result that can be re-run and contradicted. If the file is
 * absent the page says no measurement has been published rather than inventing
 * an encouraging number.
 */

if (!defined('ABSPATH')) exit;

const TIT_RECALL_PATH = 'talent-intelligence-tracker/recall';

function tit_recall_rewrite() {
    add_rewrite_rule('^' . TIT_RECALL_PATH . '/?$', 'index.php?tit_recall=1', 'top');
}
add_action('init', 'tit_recall_rewrite');

function tit_recall_query_var($vars) {
    $vars[] = 'tit_recall';
    return $vars;
}
add_filter('query_vars', 'tit_recall_query_var');

function tit_recall_url() {
    return home_url('/' . TIT_RECALL_PATH . '/');
}

/**
 * The measurement this page renders.
 *
 * Two sources, option first. The file that ships with the plugin is the seed,
 * so a fresh install has something true to show; the option is how a scheduled
 * measurement updates the page WITHOUT a deploy.
 *
 * That distinction is the difference between automated and nearly automated.
 * The measurement runs weekly and commits its result, but the plugin deploy is
 * deliberately not armed on push, so a file-only page would have gone on
 * showing the shipping-day figure forever while the repository quietly
 * accumulated newer ones. The number on a page about honesty being the stalest
 * thing in the system is not a joke anybody needs.
 */
function tit_recall_data() {
    $stored = get_option('tit_recall');
    if (is_array($stored) && !empty($stored['summary'])) return $stored;

    $file = TIT_PATH . 'data/recall.json';
    if (!is_readable($file)) return array();
    $data = json_decode(file_get_contents($file), true);
    return is_array($data) ? $data : array();
}

/**
 * POST /talent/v1/recall - keyed. How a scheduled run updates this page.
 *
 * Registered here rather than in api.php so the whole feature is one file and
 * a mistake in it cannot reach any other route.
 *
 * It stores a measurement, never a claim: the body must carry the summary, the
 * items and the gold set identity, and the counts must add up. A payload that
 * says 90% with no items behind it is rejected, because the one thing this
 * endpoint must never allow is a recall figure that was typed rather than
 * measured.
 */
function tit_recall_register_route() {
    register_rest_route('talent/v1', '/recall', array(
        'methods'  => 'POST',
        'callback' => 'tit_api_recall',
        'permission_callback' => function_exists('tit_api_permission')
            ? 'tit_api_permission' : '__return_false',
    ));
}
add_action('rest_api_init', 'tit_recall_register_route');

function tit_api_recall(WP_REST_Request $req) {
    $body = $req->get_json_params();

    if (!is_array($body) || empty($body['summary']['overall']) || empty($body['items'])
        || empty($body['measured_on']) || empty($body['goldset']['digest'])) {
        return new WP_Error('tit_recall_bad_body',
            'A measurement needs measured_on, a gold set digest, a summary and its items.',
            array('status' => 400));
    }

    $overall = $body['summary']['overall'];
    $items = $body['items'];
    if ((int) $overall['total'] !== count($items)) {
        return new WP_Error('tit_recall_mismatch',
            'summary.overall.total does not match the number of items: a figure with '
            . 'no events behind it is not a measurement.', array('status' => 400));
    }
    if ((int) $overall['held'] + (int) $overall['missed'] !== (int) $overall['total']) {
        return new WP_Error('tit_recall_mismatch',
            'held plus missed does not equal total.', array('status' => 400));
    }

    update_option('tit_recall', $body, false);
    if (function_exists('tit_flush_caches')) tit_flush_caches();

    return rest_ensure_response(array(
        'stored'      => true,
        'measured_on' => $body['measured_on'],
        'held'        => (int) $overall['held'],
        'total'       => (int) $overall['total'],
        'series'      => count($body['series'] ?? array()),
    ));
}

function tit_recall_template() {
    if (!get_query_var('tit_recall')) return;
    tit_recall_render(tit_recall_data());
    exit;
}
add_action('template_redirect', 'tit_recall_template');

function tit_recall_title($title) {
    return get_query_var('tit_recall')
        ? 'Measured Recall, And What We Miss · Talent Intelligence Tracker'
        : $title;
}
add_filter('pre_get_document_title', 'tit_recall_title');

/**
 * The measured figure, read out of the published measurement.
 *
 * Never typed. This is the page that reports what the collectors MISS, and a
 * hand-written percentage on it would be the least defensible number on the
 * site. If no measurement has been published yet the description says nothing
 * about coverage rather than implying a result.
 */
function tit_recall_head() {
    if (!get_query_var('tit_recall')) return;
    if (!function_exists('tit_head_description')) return;

    $data = tit_recall_data();
    $series = isset($data['series']) && is_array($data['series']) ? $data['series'] : array();
    $latest = $series ? end($series) : null;
    $overall = is_array($latest) && isset($latest['overall']) ? $latest['overall'] : array();

    $text = 'How much of what happened this tracker actually holds, measured '
          . 'against a fixed set of real events assembled from public sources '
          . 'without consulting our own database.';
    if (!empty($overall['total'])) {
        $text .= sprintf(
            ' Latest measurement: %s of %s events held, including the categories '
            . 'we come off badly in.',
            number_format_i18n((int) ($overall['held'] ?? 0)),
            number_format_i18n((int) $overall['total'])
        );
    }
    tit_head_description($text);
    echo '<link rel="canonical" href="'
       . esc_url(home_url('/talent-intelligence-tracker/recall/')) . '" />' . "\n";
}
add_action('wp_head', 'tit_recall_head', 1);

/** Human labels for the cell keys the measurement emits. */
function tit_recall_label($key) {
    $map = array(
        'funding'         => 'Funding rounds',
        'leadership'      => 'Leadership changes',
        'US'              => 'United States',
        'non-US'          => 'Outside the United States',
        'US funding'      => 'US funding rounds',
        'US leadership'   => 'US leadership changes',
        'non-US funding'  => 'Funding rounds outside the US',
        'non-US leadership' => 'Leadership changes outside the US',
        'filing'          => 'Mandatory filings',
        'press_release'   => 'Company press releases',
        'trade_press'     => 'Trade press',
        'national_news'   => 'National news',
        'large'           => 'Large events',
        'small'           => 'Small events',
        'wrong_category'  => 'Filed under the wrong heading, so it does not show where a reader would look',
        'country_missing' => 'No country recorded',
        'country_wrong'   => 'Country recorded wrongly',
        'amount_missing'  => 'No amount recorded',
        'amount_mismatch' => 'Amount does not match the source',
        'date_missing'    => 'No date recorded',
        'source_url_missing' => 'No source link',
    );
    return $map[$key] ?? $key;
}

/** A percentage never appears without the counts it came from. */
function tit_recall_cell_row($label, $cell) {
    $held  = (int) ($cell['held'] ?? 0);
    $clean = (int) ($cell['found'] ?? 0);
    $total = (int) ($cell['total'] ?? 0);
    $held_pct  = $cell['held_pct'];
    $clean_pct = $cell['clean_pct'];
    ?>
    <tr>
      <td data-label="Category"><?php echo esc_html($label); ?></td>
      <td class="tit-num" data-label="Held">
        <strong><?php echo $held_pct === null ? 'n/a' : esc_html($held_pct) . '%'; ?></strong>
        <span class="tit-rt"><?php echo esc_html("$held of $total"); ?></span>
      </td>
      <td class="tit-num" data-label="Held with every field right">
        <strong><?php echo $clean_pct === null ? 'n/a' : esc_html($clean_pct) . '%'; ?></strong>
        <span class="tit-rt"><?php echo esc_html("$clean of $total"); ?></span>
      </td>
    </tr>
    <?php
}

function tit_recall_table($title, $cells, $note = '') {
    if (empty($cells)) return;
    ?>
    <h2><?php echo esc_html($title); ?></h2>
    <?php if ($note) : ?><p class="tit-note"><?php echo esc_html($note); ?></p><?php endif; ?>
    <div class="tit-table-scroll">
      <table class="tit-table tit-recall-table">
        <thead><tr>
          <th>Category</th><th class="tit-num">In the tracker</th><th class="tit-num">And every field right</th>
        </tr></thead>
        <tbody>
        <?php foreach ($cells as $key => $cell) tit_recall_cell_row(tit_recall_label($key), $cell); ?>
        </tbody>
      </table>
    </div>
    <?php
}

/**
 * The trend chart.
 *
 * Drawn as inline SVG from the series, with no library and no script: the whole
 * point of automating the measurement is that the direction is visible, and a
 * direction that depends on a chart library loading is not visible.
 *
 * Deliberately plots BOTH lines. "In the tracker at all" rising while "every
 * field right" stays flat means we are collecting more and extracting no
 * better, which is a different problem from not collecting at all, and one line
 * would hide it.
 */
function tit_recall_sparkline($points) {
    $n = count($points);
    if ($n < 2) return '';

    $w = 720; $h = 220; $pad_l = 46; $pad_r = 16; $pad_t = 16; $pad_b = 34;
    $plot_w = $w - $pad_l - $pad_r;
    $plot_h = $h - $pad_t - $pad_b;

    // The y axis always starts at zero. A truncated axis turns a two point move
    // into a cliff, which is exactly the flattery this page exists to avoid.
    $max = 10;
    foreach ($points as $p) {
        $max = max($max, (float) $p['held_pct'], (float) $p['clean_pct']);
    }
    $max = ceil($max / 10) * 10;

    $x = function ($i) use ($pad_l, $plot_w, $n) {
        return $pad_l + ($plot_w * $i / ($n - 1));
    };
    $y = function ($v) use ($pad_t, $plot_h, $max) {
        return $pad_t + $plot_h - ($plot_h * min($v, $max) / $max);
    };
    $line = function ($key) use ($points, $x, $y) {
        $parts = array();
        foreach ($points as $i => $p) {
            $parts[] = ($i ? 'L' : 'M') . round($x($i), 1) . ' ' . round($y((float) $p[$key]), 1);
        }
        return implode(' ', $parts);
    };

    $described = '';
    foreach ($points as $p) {
        $described .= sprintf('%s: %s%% held, %s%% with every field right. ',
            $p['measured_on'], $p['held_pct'], $p['clean_pct']);
    }

    ob_start(); ?>
    <div class="tit-table-scroll">
    <svg class="tit-recall-chart" viewBox="0 0 <?php echo $w; ?> <?php echo $h; ?>"
         role="img" preserveAspectRatio="xMidYMid meet"
         aria-label="Recall over time. <?php echo esc_attr($described); ?>">
      <?php for ($g = 0; $g <= 4; $g++) :
        $value = $max * $g / 4; $gy = round($y($value), 1); ?>
        <line x1="<?php echo $pad_l; ?>" x2="<?php echo $w - $pad_r; ?>"
              y1="<?php echo $gy; ?>" y2="<?php echo $gy; ?>" class="tit-rc-grid"/>
        <text x="<?php echo $pad_l - 8; ?>" y="<?php echo $gy + 4; ?>"
              class="tit-rc-axis" text-anchor="end"><?php echo round($value); ?>%</text>
      <?php endfor; ?>

      <path d="<?php echo esc_attr($line('held_pct')); ?>" class="tit-rc-held" fill="none"/>
      <path d="<?php echo esc_attr($line('clean_pct')); ?>" class="tit-rc-clean" fill="none"/>

      <?php foreach ($points as $i => $p) : ?>
        <circle cx="<?php echo round($x($i), 1); ?>" cy="<?php echo round($y((float) $p['held_pct']), 1); ?>"
                r="4" class="tit-rc-dot-held"/>
        <circle cx="<?php echo round($x($i), 1); ?>" cy="<?php echo round($y((float) $p['clean_pct']), 1); ?>"
                r="4" class="tit-rc-dot-clean"/>
        <?php // Only the ends are labelled: a dated label per point becomes a
              // smear the moment there are more than about six of them. ?>
        <?php if ($i === 0 || $i === $n - 1) : ?>
          <text x="<?php echo round($x($i), 1); ?>" y="<?php echo $h - 10; ?>"
                class="tit-rc-axis"
                text-anchor="<?php echo $i === 0 ? 'start' : 'end'; ?>"><?php
            echo esc_html($p['measured_on']); ?></text>
        <?php endif; ?>
      <?php endforeach; ?>
    </svg>
    </div>
    <p class="tit-recall-legend">
      <span class="tit-rc-key tit-rc-k-held"></span> in the tracker at all
      <span class="tit-rc-key tit-rc-k-clean"></span> with every field right
    </p>
    <?php
    return ob_get_clean();
}

/**
 * One group's history, as counts across measurements.
 *
 * Counts and not percentages, because these cells are small: "2 of 22" moving
 * to "5 of 22" is a fact, while "9.1% to 22.7%" invites a reader to treat three
 * events as a trend.
 */
function tit_recall_history_table($title, $group, $series, $note = '') {
    if (count($series) < 2) return;

    $keys = array();
    foreach ($series as $point) {
        foreach (($point[$group] ?? array()) as $key => $ignored) $keys[$key] = true;
    }
    if (!$keys) return;
    $keys = array_keys($keys);
    sort($keys);
    ?>
    <h3><?php echo esc_html($title); ?></h3>
    <?php if ($note) : ?><p class="tit-note"><?php echo esc_html($note); ?></p><?php endif; ?>
    <div class="tit-table-scroll">
      <table class="tit-table tit-recall-history">
        <thead><tr>
          <th>Category</th>
          <?php foreach ($series as $point) : ?>
            <th class="tit-num"><?php echo esc_html($point['measured_on']); ?></th>
          <?php endforeach; ?>
        </tr></thead>
        <tbody>
        <?php foreach ($keys as $key) : ?>
          <tr>
            <td data-label="Category"><?php echo esc_html(tit_recall_label($key)); ?></td>
            <?php foreach ($series as $point) :
              $cell = $point[$group][$key] ?? null; ?>
              <td class="tit-num" data-label="<?php echo esc_attr($point['measured_on']); ?>">
                <?php
                // Blank, never a zero, when a category was not in that
                // measurement's test set at all. A zero would read as "we
                // looked there and found nothing".
                if ($cell === null) {
                    echo '<span class="tit-nowhere">not tested</span>';
                } else {
                    echo esc_html($cell['held'] . ' of ' . $cell['total']);
                }
                ?>
              </td>
            <?php endforeach; ?>
          </tr>
        <?php endforeach; ?>
        </tbody>
      </table>
    </div>
    <?php
}

/**
 * Flatten the stored series into what the chart and history tables need.
 *
 * Each point keeps the identity of the gold set it was measured against,
 * because two points measured against DIFFERENT sets are not strictly
 * comparable and the page has to be able to say which is which.
 */
function tit_recall_points($series) {
    $points = array();
    foreach ($series as $entry) {
        $overall = $entry['overall'] ?? null;
        if (!$overall || $overall['held_pct'] === null) continue;
        $points[] = array(
            'measured_on'     => $entry['measured_on'],
            'goldset_version' => $entry['goldset_version'],
            'held_pct'        => $overall['held_pct'],
            'clean_pct'       => $overall['clean_pct'],
            'held'            => $overall['held'],
            'found'           => $overall['found'],
            'total'           => $overall['total'],
            'by_segment'      => $entry['by_segment'] ?? array(),
            'by_source_type'  => $entry['by_source_type'] ?? array(),
            'by_country'      => $entry['by_country'] ?? array(),
        );
    }
    return $points;
}

/**
 * The weakest cells, named out loud.
 *
 * Computed rather than written, so the page cannot keep describing an old
 * weakness after it is fixed, and cannot quietly stop naming a new one.
 */
function tit_recall_weakest($cells, $min_total = 4, $limit = 3) {
    $ranked = array();
    foreach ($cells as $key => $cell) {
        if ((int) $cell['total'] < $min_total) continue;
        $ranked[$key] = $cell;
    }
    uasort($ranked, fn($a, $b) => $a['held_pct'] <=> $b['held_pct']);
    return array_slice($ranked, 0, $limit, true);
}

function tit_recall_render($data) {
    if (function_exists('tit_render_header')) tit_render_header(); else get_header();

    if (empty($data) || empty($data['summary'])) {
        ?>
        <div class="tit-wrap tit-recall">
          <h1>Measured recall</h1>
          <p class="tit-note">No measurement has been published yet. When one is,
            this page carries the number, the date it was measured, and the
            reference set it was measured against. Until then there is no recall
            figure to quote, and an unmeasured tracker should not be described as
            complete.</p>
        </div>
        <?php
        if (function_exists('tit_render_footer')) tit_render_footer(); else get_footer();
        return;
    }

    $summary = $data['summary'];
    $overall = $summary['overall'];
    $gold    = $data['goldset'];
    $window  = $gold['window'];
    $items   = $data['items'] ?? array();
    $missed  = array_values(array_filter($items, fn($i) => $i['verdict'] === 'MISSED'));
    $partial = array_values(array_filter($items, fn($i) => $i['verdict'] === 'FOUND_PARTIAL'));
    $weakest = tit_recall_weakest($summary['by_segment'] ?? array());
    $points  = tit_recall_points($data['series'] ?? array());
    $first   = $points ? $points[0] : null;
    $sets    = array_unique(array_column($points, 'goldset_version'));
    ?>
    <div class="tit-wrap tit-recall" id="tit-recall">
      <nav class="tit-crumb">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Talent Intelligence Tracker</a>
        <span aria-hidden="true">›</span> Measured recall
      </nav>

      <h1>How much do we miss?</h1>

      <p class="tit-note">
        Every tracker claims to be comprehensive. This one publishes the test.
        We fixed a list of <?php echo (int) $overall['total']; ?> real events that
        happened between <?php echo esc_html($window['start']); ?> and
        <?php echo esc_html($window['end']); ?>, assembled from public sources
        without ever looking at our own database, and then checked how many of
        them we actually hold. We held
        <strong><?php echo (int) $overall['held']; ?> of <?php echo (int) $overall['total']; ?></strong>.
        Measured <?php echo esc_html($data['measured_on']); ?>.
      </p>

      <div class="tit-stats">
        <div class="tit-stat">
          <span class="tit-n"><?php echo $overall['held_pct'] === null ? 'n/a' : esc_html($overall['held_pct']) . '%'; ?></span>
          <span class="tit-l">in the tracker at all<br><?php echo esc_html($overall['held'] . ' of ' . $overall['total']); ?></span>
        </div>
        <div class="tit-stat">
          <span class="tit-n"><?php echo $overall['clean_pct'] === null ? 'n/a' : esc_html($overall['clean_pct']) . '%'; ?></span>
          <span class="tit-l">with every field right<br><?php echo esc_html($overall['found'] . ' of ' . $overall['total']); ?></span>
        </div>
        <div class="tit-stat">
          <span class="tit-n"><?php echo (int) $overall['missed']; ?></span>
          <span class="tit-l">missed entirely</span>
        </div>
        <div class="tit-stat">
          <span class="tit-n"><?php echo esc_html(count($gold['counts']['country'] ?? array())); ?></span>
          <span class="tit-l">countries in the test set</span>
        </div>
      </div>

      <div class="tit-callout">
        <strong>A single blended percentage would hide the only interesting
        thing here.</strong> The same tracker can be near complete where a
        filing is legally required and weak where nothing has to be filed
        anywhere. That is why every number below is broken out by category and
        carries its own counts.
      </div>

      <h2>The direction</h2>
      <?php if (count($points) < 2) : ?>
        <p class="tit-note">
          One measurement so far, taken on
          <?php echo esc_html($data['measured_on']); ?>. The measurement runs
          weekly from here and every result is kept, so this becomes a line
          rather than a verdict. A single figure says where we are; only the
          series says whether we are getting better, and that is the number
          worth judging us on.
        </p>
      <?php else : ?>
        <p class="tit-note">
          <?php
          $delta = $points[count($points) - 1]['held_pct'] - $first['held_pct'];
          printf(
              esc_html('%1$s measurements between %2$s and %3$s. Held has gone from %4$s%% to %5$s%%, a change of %6$s points.'),
              esc_html(count($points)),
              esc_html($first['measured_on']),
              esc_html($points[count($points) - 1]['measured_on']),
              esc_html($first['held_pct']),
              esc_html($points[count($points) - 1]['held_pct']),
              esc_html(($delta > 0 ? '+' : '') . round($delta, 1))
          );
          ?>
        </p>
        <?php echo tit_recall_sparkline($points); // phpcs:ignore ?>
        <?php if (count($sets) > 1) : ?>
          <div class="tit-callout">
            <strong>These points are not all the same test.</strong>
            <?php printf(esc_html('%s different test sets appear in this series.'),
                esc_html(count($sets))); ?>
            A set is replaced once its window has aged out or the number against
            it has stopped moving, because re-running one fixed list of events
            forever would measure memory rather than reach and would walk to
            100% while meaning nothing. So a step between two points is a change
            in coverage plus a change in the test, and the per category tables
            below are the safer read.
          </div>
        <?php endif; ?>
      <?php endif; ?>

      <?php if ($weakest) : ?>
      <h2>Where we fall short</h2>
      <p class="tit-note">
        These are our worst categories in this measurement, computed from the
        table below rather than chosen.
      </p>
      <ul class="tit-recall-gaps">
        <?php foreach ($weakest as $key => $cell) : ?>
          <li>
            <strong><?php echo esc_html(tit_recall_label($key)); ?>:</strong>
            we hold <?php echo esc_html($cell['held'] . ' of ' . $cell['total']); ?>
            <?php if ($cell['held_pct'] !== null) : ?>
              (<?php echo esc_html($cell['held_pct']); ?>%)<?php endif; ?>.
          </li>
        <?php endforeach; ?>
      </ul>
      <p>
        Part of this is structural and will never fully close. In the United
        States a listed company changing an officer must file an 8-K, and a
        company selling securities must file a Form D. Those are mandatory,
        machine readable and free, which is why our best cell is US leadership
        changes. There is no equivalent anywhere in the world for a private
        funding round. A seed round in Nairobi, Jakarta or Bogota exists as a
        press release and whatever local outlet chose to write it up, in
        whatever language. Anyone telling you their private funding coverage
        outside the United States is complete has not measured it.
      </p>
      <p>
        <strong>The rest of it is simply us, and we are not going to dress that
        up as a structural problem.</strong> A gap this wide is not explained by
        filing regimes. It is a young tracker reading a small number of
        collectors, at a volume far below what the world actually announces in a
        month, and the events it does catch skew to the ones that arrive as
        machine readable filings. Even in the category with a mandatory filing
        we are well short of complete. This number is bad. It is published
        because a bad measured number is worth more than a good unmeasured
        claim, and because it is the only version of this page that can improve
        for a reason you can check.
      </p>
      <?php endif; ?>

      <?php
      tit_recall_table('Recall by category', $summary['by_segment'] ?? array(),
          'The four cells that matter most. "In the tracker" means the event is here at all. "Every field right" additionally requires the country, the amount, the date and a working source link to be correct.');
      tit_recall_table('By signal type', $summary['by_signal_type'] ?? array());
      tit_recall_table('By where the event happened', $summary['by_geography'] ?? array());
      tit_recall_table('By what kind of document announced it', $summary['by_source_type'] ?? array(),
          'A mandatory filing is a different collection problem from a press release in a local outlet. This row is the honest measure of that difference.');
      tit_recall_table('By size of the event', $summary['by_size_band'] ?? array(),
          'Large means a funding round of $50M or more, or a change at a large listed employer. Small events are deliberately over represented in the test set, because measuring only the large ones would flatter the result.');
      tit_recall_table('By country', $summary['by_country'] ?? array(),
          'Most countries carry only a handful of events, so treat a single country cell as an indication and not a rate.');
      ?>

      <?php if (count($points) > 1) : ?>
      <h2>Every category, over time</h2>
      <p class="tit-note">
        The same breakdowns across every measurement, as raw counts. This is
        where an improvement shows up as an improvement: a new set of feeds in a
        region moves that region's row and leaves the others alone, and a
        movement that appears everywhere at once is usually the test changing
        rather than the tracker.
      </p>
      <?php
      tit_recall_history_table('By category, over time', 'by_segment', $points);
      tit_recall_history_table('By kind of document, over time', 'by_source_type', $points);
      tit_recall_history_table('By country, over time', 'by_country', $points,
          'Countries appear here only in the measurements whose test set contained them, so a blank is "not tested" and never "found nothing".');
      ?>
      <?php endif; ?>

      <?php if (!empty($summary['defects'])) : ?>
      <h2>What is wrong with the ones we do hold</h2>
      <p class="tit-note">
        <?php echo esc_html(count($partial)); ?> of the
        <?php echo (int) $overall['held']; ?> events we hold have at least one
        field wrong or missing. These need a better extractor rather than a new
        source, and they are counted separately for that reason. A record with
        no country is invisible to every geographic filter on this site even
        though we have it.
      </p>
      <div class="tit-table-scroll">
        <table class="tit-table">
          <thead><tr><th>Defect</th><th class="tit-num">Records</th></tr></thead>
          <tbody>
          <?php foreach ($summary['defects'] as $name => $count) : ?>
            <tr>
              <td data-label="Defect"><?php echo esc_html(tit_recall_label($name)); ?></td>
              <td class="tit-num" data-label="Records"><?php echo (int) $count; ?></td>
            </tr>
          <?php endforeach; ?>
          </tbody>
        </table>
      </div>
      <?php endif; ?>

      <h2>How this was measured</h2>
      <p>
        One bounded period was chosen first:
        <?php echo esc_html($window['start']); ?> to
        <?php echo esc_html($window['end']); ?>. Within it we assembled
        <?php echo (int) $overall['total']; ?> events that genuinely happened at
        named employers, from public sources only: company announcements,
        regulatory filings, national and trade press. The reference set was
        never built by querying our own database, because a list drawn from what
        we already hold measures nothing at all.
      </p>
      <p>
        The set was then sealed. Its contents were fixed on
        <?php echo esc_html($gold['assembled_on']); ?>, before any matching ran,
        and the file carries a digest
        (<code><?php echo esc_html($gold['digest']); ?></code>) that is recorded
        alongside every published figure. Nothing was added or removed after
        seeing the result. Four of the events were known misses before the
        exercise began and were included deliberately, because a test set that
        drops its known failures is not a test.
      </p>
      <p>
        Each event was then looked up through the same public API anyone else
        can use, matched on employer name, kind of event and a date window that
        allows for a late write up. An event counts as held when we have a
        record of it. It counts as fully correct only when the country, the
        amount, the date and the source link are all right as well. Where a rule
        could go either way it was written to favour counting an event as held,
        so the misses reported here are conservative.
      </p>
      <h3>How this stays honest as it repeats</h3>
      <p>
        The measurement runs on a schedule and commits its result, so the series
        above is produced without anybody deciding to produce it. That solves
        one problem and creates another: re-running the same fixed list of
        events forever would stop measuring reach and start measuring memory.
        Once those particular events are collected the figure walks towards 100%
        and means nothing at all, which is the most flattering way a benchmark
        can fail.
      </p>
      <p>
        So a test set is retired rather than reused indefinitely. It is replaced
        when the window it covers has aged out, or earlier when the number
        against it stops moving, and the run itself says which. Every past set
        stays on disk with its digest so any figure ever published here can be
        re-derived from the exact list it was measured against.
      </p>
      <p>
        <strong>Assembling a replacement is not automated, and we are not going
        to pretend otherwise.</strong> Building a set means open ended research
        across national press in many languages, and judgement about what counts
        as an event. A generator that took the easy path would drift towards
        finding exactly the things we already collect, and the number would
        climb for the worst possible reason. So the schedule does the
        measuring, the arithmetic and the alerting, and a person still assembles
        and seals each new set. That is the honest sliver, it is a few times a
        year, and naming it is cheaper than a benchmark nobody should believe.
      </p>

      <?php if (!empty($gold['caveats'])) : ?>
      <h3>What is wrong with this test itself</h3>
      <p class="tit-note">
        A benchmark that hides its own weaknesses is a brochure. These are the
        ones we know about.
      </p>
      <ul class="tit-recall-gaps">
        <?php foreach ($gold['caveats'] as $caveat) : ?>
          <li><?php echo esc_html($caveat); ?></li>
        <?php endforeach; ?>
      </ul>
      <?php endif; ?>

      <p class="tit-note">
        The measurement is a script in the public repository and can be re run
        by anyone against the live API, which is what makes this number
        contestable rather than a claim. Coverage changes, so a recall figure
        without a date is worthless: this one was measured on
        <?php echo esc_html($data['measured_on']); ?> and will be measured
        again.
      </p>

      <h2>The test set, and every result</h2>
      <p class="tit-note">
        All <?php echo count($items); ?> events, with the source that proves
        each one happened, so the reference set itself can be audited rather
        than taken on trust. Sorted worst first.
      </p>

      <div class="tit-filters">
        <select id="tit-r-verdict" aria-label="Filter by result">
          <option value="">Every result</option>
          <option value="MISSED">Missed entirely</option>
          <option value="FOUND_PARTIAL">Held, with a field wrong</option>
          <option value="FOUND">Held and correct</option>
        </select>
        <input type="search" id="tit-r-q" placeholder="Search the test set" aria-label="Search the test set">
      </div>
      <p class="tit-note" id="tit-r-count"></p>

      <div class="tit-table-scroll">
        <table class="tit-table">
          <thead><tr>
            <th>Employer</th><th>Event</th><th>Where</th><th>Result</th><th>Proof it happened</th>
          </tr></thead>
          <tbody id="tit-r-rows">
          <?php
          $order = array('MISSED' => 0, 'FOUND_PARTIAL' => 1, 'FOUND' => 2);
          usort($items, fn($a, $b) => array($order[$a['verdict']], $a['company'])
                                   <=> array($order[$b['verdict']], $b['company']));
          foreach ($items as $item) :
            $verdict_label = array(
                'MISSED'        => 'Missed entirely',
                'FOUND_PARTIAL' => 'Held, field wrong',
                'FOUND'         => 'Held and correct',
            )[$item['verdict']];
            $verdict_class = array(
                'MISSED'        => 'tit-c-degraded',
                'FOUND_PARTIAL' => 'tit-c-partial',
                'FOUND'         => 'tit-c-verified',
            )[$item['verdict']];
            ?>
            <tr data-verdict="<?php echo esc_attr($item['verdict']); ?>"
                data-search="<?php echo esc_attr(strtolower($item['company'] . ' ' . $item['detail'] . ' ' . $item['country'] . ' ' . $item['source_name'])); ?>">
              <td class="tit-headline" data-label="Employer">
                <span class="tit-h"><?php echo esc_html($item['company']); ?></span>
                <span class="tit-rt"><?php echo esc_html($item['event_date']); ?></span>
              </td>
              <td data-label="Event"><?php echo esc_html($item['detail']); ?></td>
              <td class="tit-meta" data-label="Where"><?php echo esc_html($item['country']); ?></td>
              <td class="tit-meta" data-label="Result">
                <span class="tit-conf <?php echo esc_attr($verdict_class); ?>"><?php echo esc_html($verdict_label); ?></span>
                <?php if (!empty($item['defects'])) : ?>
                  <span class="tit-rt"><?php
                    echo esc_html(implode(', ', array_map('tit_recall_label', $item['defects'])));
                  ?></span>
                <?php endif; ?>
              </td>
              <td class="tit-meta" data-label="Proof it happened">
                <a href="<?php echo esc_url($item['source_url']); ?>" rel="nofollow noopener"
                   target="_blank"><?php echo esc_html($item['source_name']); ?></a>
              </td>
            </tr>
          <?php endforeach; ?>
          </tbody>
        </table>
      </div>

      <p class="tit-cite">
        Measured <?php echo esc_html($data['measured_on']); ?> against test set
        <?php echo esc_html($gold['version']); ?>
        (<code><?php echo esc_html($gold['digest']); ?></code>).
        <?php if (!empty($gold['url'])) : ?>
          The test set and the script that runs this are
          <a href="<?php echo esc_url($gold['url']); ?>" rel="nofollow noopener" target="_blank">public</a>.
        <?php endif; ?>
        Found a real event from this window that we missed?
        <a href="https://asktherecruiter.com/blog/contact/">Tell us</a> and it
        goes into the next test set.
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">Every source we read</a>
        <span aria-hidden="true">·</span>
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Back to the tracker</a>
      </p>
    </div>

    <script>
    /* The whole table is already in the page, so filtering is local and the
       page still works with JavaScript off: every row simply stays visible. */
    (function () {
      var rows = Array.prototype.slice.call(document.querySelectorAll('#tit-r-rows tr'));
      var verdict = document.getElementById('tit-r-verdict');
      var q = document.getElementById('tit-r-q');
      var count = document.getElementById('tit-r-count');
      function apply() {
        var term = (q.value || '').trim().toLowerCase();
        var shown = 0;
        rows.forEach(function (tr) {
          var ok = (!verdict.value || tr.dataset.verdict === verdict.value)
                && (!term || tr.dataset.search.indexOf(term) !== -1);
          tr.style.display = ok ? '' : 'none';
          if (ok) { shown++; tr.classList.toggle('tit-even', shown % 2 === 0); }
          else { tr.classList.remove('tit-even'); }
        });
        count.textContent = shown + (shown === 1 ? ' event' : ' events') + ' shown of ' + rows.length;
      }
      verdict.addEventListener('change', apply);
      q.addEventListener('input', apply);
      apply();
    })();
    </script>
    <?php
    if (function_exists('tit_render_footer')) tit_render_footer(); else get_footer();
}
