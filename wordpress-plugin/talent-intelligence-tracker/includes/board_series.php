<?php
/**
 * Job-posting volume over time, per employer.
 *
 * The counts come from the employer's OWN job board (Greenhouse, Lever, Ashby,
 * Workable), read once a day by the ats_boards collector. Those APIs publish no
 * history and no closed-on date, and the Wayback Machine holds no snapshots of
 * them, so the series only exists because somebody wrote each day down. That is
 * the whole value of this panel, and also why it can never be back-filled.
 *
 * Three rules this file exists to keep:
 *
 * 1. **A count is a measurement, not an announcement.** It is our reading of a
 *    page the employer publishes, so the panel names the board and links to it.
 * 2. **Rising means hiring. Falling means nothing on its own.** Roles leave a
 *    board when they are filled, withdrawn or reposted, and none of those is a
 *    job cut. The panel says so in words next to a falling line, every time.
 * 3. **"We cannot tell" is a real answer.** A board with three readings gets no
 *    direction at all, and the panel says why rather than drawing a confident
 *    line through two dots.
 *
 * Data arrives the same way the recall page's does: a shipped JSON seed, with a
 * keyed endpoint that overrides it, because the plugin deploy is deliberately
 * not armed on push and a file-only page would freeze at shipping-day values.
 */

if (!defined('ABSPATH')) exit;

/**
 * The series, option first and shipped file as the seed.
 */
function tit_board_series_data() {
    $stored = get_option('tit_board_series');
    if (is_array($stored) && !empty($stored['boards'])) return $stored;

    $file = TIT_PATH . 'data/board_series.json';
    if (!is_readable($file)) return array();
    $data = json_decode(file_get_contents($file), true);
    return is_array($data) ? $data : array();
}

/**
 * Every board we hold for one employer, looked up BY SLUG.
 *
 * Compared in slug space for the same reason tit_company_rows() is: the
 * space -> hyphen direction is total and the reverse is not. See the regression
 * note in company.php before changing this.
 */
function tit_board_series_for($slug) {
    $data = tit_board_series_data();
    if (empty($data['boards']) || !is_array($data['boards'])) return array();
    foreach ($data['boards'] as $company_key => $boards) {
        if (str_replace(' ', '-', $company_key) === $slug) {
            return is_array($boards) ? $boards : array();
        }
    }
    return array();
}

/** Reader-facing wording for each verdict the rule can reach. */
function tit_board_direction_label($direction) {
    $map = array(
        'rising'  => 'Job board growing',
        'falling' => 'Job board shrinking',
        'flat'    => 'Job board holding steady',
        'unknown' => 'Not enough readings yet',
    );
    return $map[$direction] ?? 'Not enough readings yet';
}

/**
 * A sparkline as inline SVG. No library, no request, no script: the panel has
 * to render inside a cached page on shared hosting.
 */
function tit_board_sparkline($series, $width = 320, $height = 56) {
    $points = array();
    $totals = array();
    foreach ($series as $point) {
        if (is_array($point) && isset($point[1])) $totals[] = (int) $point[1];
    }
    $count = count($totals);
    if ($count < 2) return '';

    $min = min($totals);
    $max = max($totals);
    $span = max(1, $max - $min);
    foreach ($totals as $i => $total) {
        $x = round($i * ($width - 2) / ($count - 1) + 1, 1);
        $y = round($height - 3 - (($total - $min) / $span) * ($height - 6), 1);
        $points[] = $x . ',' . $y;
    }

    return '<svg class="tit-spark" viewBox="0 0 ' . (int) $width . ' ' . (int) $height . '" '
         . 'width="100%" height="' . (int) $height . '" role="img" preserveAspectRatio="none" '
         . 'aria-label="' . esc_attr(sprintf('%d daily readings, from %d to %d open roles',
                                             $count, $totals[0], $totals[$count - 1])) . '">'
         . '<polyline fill="none" stroke="currentColor" stroke-width="2" '
         . 'stroke-linejoin="round" stroke-linecap="round" points="' . esc_attr(implode(' ', $points)) . '" />'
         . '</svg>';
}

/**
 * The panel itself. Returns HTML, or '' when we hold no series for the
 * employer — an empty panel would imply an employer with no job board, which is
 * a claim we have not made.
 */
function tit_board_series_panel($slug) {
    $boards = tit_board_series_for($slug);
    if (!$boards) return '';
    $data = tit_board_series_data();

    ob_start(); ?>
    <section class="tit-board-volume">
      <h2 class="tit-h2">Open roles on their own job board</h2>
      <?php foreach ($boards as $board) :
          $trajectory = is_array($board['trajectory'] ?? null) ? $board['trajectory'] : array();
          $direction  = $trajectory['direction'] ?? 'unknown';
          $latest     = is_array($board['latest'] ?? null) ? $board['latest'] : array();
          $series     = is_array($board['series'] ?? null) ? $board['series'] : array(); ?>
        <div class="tit-board tit-board-<?php echo esc_attr($direction); ?>">
          <div class="tit-board-head">
            <span class="tit-n"><?php echo (int) ($latest['total'] ?? 0); ?></span>
            <span class="tit-l">
              open roles on <?php echo esc_html($latest['date'] ?? ''); ?>
            </span>
            <span class="tit-tag"><?php echo esc_html(tit_board_direction_label($direction)); ?></span>
          </div>
          <?php echo tit_board_sparkline($series); // phpcs:ignore — built above, already escaped ?>
          <p class="tit-board-basis"><?php echo esc_html($trajectory['basis'] ?? ''); ?></p>
          <p class="tit-event-meta">
            Counted by us from
            <a href="<?php echo esc_url($board['source_url'] ?? ''); ?>" rel="nofollow noopener" target="_blank"><?php
              echo esc_html($board['source_name'] ?: 'the employer job board'); ?></a>
            once a day since <?php echo esc_html($board['first_seen'] ?? ''); ?>.
          </p>
        </div>
      <?php endforeach; ?>
      <p class="tit-note"><?php echo esc_html($data['rule'] ?? ''); ?></p>
    </section>
    <?php
    return ob_get_clean();
}

/**
 * POST /talent/v1/board-series - keyed. How a collector run updates the panel
 * without a deploy.
 *
 * Registered in this file rather than api.php so the whole feature is one file
 * and a mistake in it cannot reach any other route, exactly as recall.php does.
 *
 * It stores a measurement, never a claim: every board must carry the URL that
 * backs it and at least one dated reading, and a trajectory may only be one of
 * the four verdicts the rule can produce. A payload asserting a direction with
 * no series behind it is rejected.
 */
function tit_board_series_register_route() {
    register_rest_route('talent/v1', '/board-series', array(
        'methods'  => 'POST',
        'callback' => 'tit_api_board_series',
        'permission_callback' => function_exists('tit_api_permission')
            ? 'tit_api_permission' : '__return_false',
    ));
}
add_action('rest_api_init', 'tit_board_series_register_route');

function tit_api_board_series(WP_REST_Request $req) {
    $body = $req->get_json_params();
    if (!is_array($body) || empty($body['boards']) || !is_array($body['boards'])
        || empty($body['rule']) || empty($body['as_of'])) {
        return new WP_Error('tit_board_series_bad_body',
            'A series needs as_of, the rule it was read by, and at least one board.',
            array('status' => 400));
    }

    $allowed = array('rising', 'falling', 'flat', 'unknown');
    $boards = 0;
    foreach ($body['boards'] as $company_key => $entries) {
        if (!is_array($entries)) {
            return new WP_Error('tit_board_series_bad_body',
                'Each employer must carry a list of boards.', array('status' => 400));
        }
        foreach ($entries as $entry) {
            if (empty($entry['source_url']) || empty($entry['series'])
                || !is_array($entry['series'])) {
                return new WP_Error('tit_board_series_unsourced',
                    'Every board needs the board URL it was counted from and its '
                    . 'dated readings: a number with no source is not a measurement.',
                    array('status' => 400));
            }
            $direction = $entry['trajectory']['direction'] ?? 'unknown';
            if (!in_array($direction, $allowed, true)) {
                return new WP_Error('tit_board_series_bad_direction',
                    'Unknown direction: ' . sanitize_text_field((string) $direction),
                    array('status' => 400));
            }
            $boards++;
        }
    }

    update_option('tit_board_series', $body, false);
    if (function_exists('tit_flush_caches')) tit_flush_caches();

    return rest_ensure_response(array(
        'stored'    => true,
        'as_of'     => sanitize_text_field((string) $body['as_of']),
        'employers' => count($body['boards']),
        'boards'    => $boards,
    ));
}
