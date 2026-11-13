<?php
/**
 * OPEN ROLES NOW: counts, and a few role titles that link STRAIGHT to the
 * employer's own apply page. Owner rulings 2026-11-12 (no public job listings)
 * and 2026-11-13 ("yes, employer links").
 *
 * Three things this file never does, each a ruling and each tested:
 *  1. It never makes a job page on our site. Every role link is the employer's
 *     own Greenhouse, Ashby or Lever posting, https, and never our host.
 *  2. It never emits schema.org JobPosting markup. That would make these
 *     listings in Google's eyes, which is the line the owner drew.
 *  3. It never shows a stale role. The snapshot is replaced whole on every
 *     daily publish (open_roles.py), and anything older than
 *     TIT_OPEN_ROLES_MAX_AGE_H hides every block rather than showing yesterday.
 *
 * Data arrives on a keyed POST from open_roles.py, run by
 * collect-structured.yml straight after the daily ats_boards collection. There
 * is no shipped seed on purpose: a seed is by definition stale.
 *
 * Two readers:
 *   tit_open_roles_company_block($company_key)   the company page block
 *   tit_open_roles_email_section($freq, $args)   the "Open roles now" email
 *       section, called in-process by the sibling's digest composer, the same
 *       way it already calls tit_follow_rows(). There is NO public GET route:
 *       a public feed of roles by country is a listings feed.
 */
if (!defined('ABSPATH')) exit;

if (!defined('TIT_OPEN_ROLES_MAX_AGE_H')) define('TIT_OPEN_ROLES_MAX_AGE_H', 30);
if (!defined('TIT_OPEN_ROLES_COMPANY_N')) define('TIT_OPEN_ROLES_COMPANY_N', 5);
if (!defined('TIT_OPEN_ROLES_DAILY_N')) define('TIT_OPEN_ROLES_DAILY_N', 10);
if (!defined('TIT_OPEN_ROLES_WEEKLY_N')) define('TIT_OPEN_ROLES_WEEKLY_N', 20);
if (!defined('TIT_OPEN_ROLES_CV_URL')) define('TIT_OPEN_ROLES_CV_URL', 'https://asktherecruiter.com/');

/** The providers whose terms allow titles and outbound links (docs/SOURCE-TERMS-open-roles.md). */
function tit_open_roles_sources() {
    return array('greenhouse', 'lever', 'ashby');
}

/** True for an https link to somebody else's host. */
function tit_open_roles_link_ok($url) {
    $url = (string) $url;
    if (stripos($url, 'https://') !== 0) return false;
    $host = strtolower((string) wp_parse_url($url, PHP_URL_HOST));
    if ($host === '') return false;
    return !($host === 'asktherecruiter.com' || substr($host, -strlen('.asktherecruiter.com')) === '.asktherecruiter.com');
}

/**
 * The stored snapshot, or null when there is none or it is too old to show.
 * $now is injectable for tests.
 */
function tit_open_roles_data($now = null) {
    $d = get_option('tit_open_roles');
    if (!is_array($d) || empty($d['as_of'])) return null;
    $t = strtotime((string) $d['as_of']);
    $now = $now === null ? time() : (int) $now;
    if (!$t || ($now - $t) > TIT_OPEN_ROLES_MAX_AGE_H * 3600) return null;
    return $d;
}

/** "Mon 13 Nov 2026" style date for "Last checked". */
function tit_open_roles_checked_label($d) {
    $t = strtotime((string) ($d['checked_on'] ?? ''));
    return $t ? gmdate('j M Y', $t) : '';
}

/** Employer name without the legal suffix, for a short line. */
function tit_open_roles_employer($name) {
    $n = trim((string) $name);
    $n = preg_replace('/,?\s+(Inc\.?|Incorporated|LLC|L\.L\.C\.|Ltd\.?|Limited|PLC|plc|GmbH|AG|S\.A\.|SA|Corp\.?|Corporation|Holdings,? Inc\.?|Holdings|Co\.)$/u', '', $n);
    $n = preg_replace('/,?\s+(Inc\.?|Holdings)$/u', '', $n);
    return trim($n, " ,");
}

/** "San Francisco", "Remote, US", "Germany" or ''. */
function tit_open_roles_where($r) {
    $city = trim((string) ($r['city'] ?? ''));
    $cc = strtoupper((string) ($r['country'] ?? ''));
    if ($city !== '') return $city;
    $country = $cc !== '' && function_exists('tit_country_name') ? tit_country_name($cc) : $cc;
    if (!empty($r['remote'])) return $country !== '' ? 'Remote, ' . $country : 'Remote';
    return $country;
}

function tit_open_roles_family_label($slug, $d = null) {
    $labels = is_array($d['family_labels'] ?? null) ? $d['family_labels'] : array();
    if (isset($labels[$slug])) return (string) $labels[$slug];
    return ucfirst(str_replace('_', ' ', (string) $slug));
}

// --- Company page ------------------------------------------------------------

/**
 * "Open roles now: N", up to five titles, "Last checked <date>". Returns ''
 * when the employer has no open roles or the snapshot is stale: no block, not
 * a zero, because "0 open roles" from a board we could not read would be a
 * claim we have not made.
 */
function tit_open_roles_company_block($company_key, $now = null) {
    $d = tit_open_roles_data($now);
    if (!$d) return '';
    $c = $d['companies'][(string) $company_key] ?? null;
    if (!is_array($c) || (int) ($c['count'] ?? 0) <= 0 || empty($c['roles'])) return '';
    $roles = array();
    foreach ((array) $c['roles'] as $r) {
        if (!empty($r['title']) && tit_open_roles_link_ok($r['url'] ?? '')) $roles[] = $r;
        if (count($roles) >= TIT_OPEN_ROLES_COMPANY_N) break;
    }
    if (!$roles) return '';
    $n = (int) $c['count'];
    $id = 'tit-open-roles-h';
    ob_start(); ?>
    <section class="tit-open-roles" aria-labelledby="<?php echo esc_attr($id); ?>">
      <h2 class="tit-h2" id="<?php echo esc_attr($id); ?>">Open roles now: <?php echo esc_html(number_format_i18n($n)); ?></h2>
      <ul class="tit-open-roles-list">
        <?php foreach ($roles as $r) : $where = tit_open_roles_where($r); ?>
        <li><a href="<?php echo esc_url($r['url']); ?>" rel="nofollow noopener" target="_blank"><?php
            echo esc_html($r['title']); ?><span class="screen-reader-text"> (opens the employer's own job page in a new tab)</span></a><?php
            if ($where !== '') : ?> <span class="tit-open-roles-where"><?php echo esc_html($where); ?></span><?php endif; ?></li>
        <?php endforeach; ?>
      </ul>
      <p class="tit-event-meta">Links go to the employer's own job board. Last checked <?php
        echo esc_html(tit_open_roles_checked_label($d)); ?>.</p>
    </section>
    <?php
    return ob_get_clean();
}

// --- Email section -----------------------------------------------------------

/** The "See all matches for your CV" link, UTM-tagged per edition. */
function tit_open_roles_cv_url($freq) {
    $freq = in_array($freq, array('daily', 'weekly'), true) ? $freq : 'daily';
    return add_query_arg(array(
        'utm_source'   => 'talent-tracker',
        'utm_medium'   => 'email',
        'utm_campaign' => 'open-roles-' . $freq,
        'utm_content'  => 'cv-matches',
    ), TIT_OPEN_ROLES_CV_URL) . '#start-intake';
}

/** Roles matching a reader's choices, newest first, at most two per employer. */
function tit_open_roles_pick($d, $country, $family, $limit) {
    $out = array();
    $per = array();
    foreach ((array) ($d['pool'] ?? array()) as $r) {
        if ($country !== '' && strtoupper((string) ($r['country'] ?? '')) !== $country) continue;
        if ($family !== '' && (string) ($r['family'] ?? '') !== $family) continue;
        if (empty($r['title']) || !tit_open_roles_link_ok($r['url'] ?? '')) continue;
        $k = (string) ($r['company_key'] ?? '');
        if (($per[$k] ?? 0) >= 2) continue;
        $per[$k] = ($per[$k] ?? 0) + 1;
        $out[] = $r;
        if (count($out) >= $limit) break;
    }
    return $out;
}

/** Top-N of a count map as [[key, n], ...]. */
function tit_open_roles_top($map, $n) {
    $map = is_array($map) ? $map : array();
    unset($map['']);
    arsort($map);
    $out = array();
    foreach (array_slice($map, 0, $n, true) as $k => $v) $out[] = array((string) $k, (int) $v);
    return $out;
}

/** "+12%" / "-3%" / "" (no prior reading, or too small to mean anything). */
function tit_open_roles_change($now, $before) {
    $now = (int) $now; $before = (int) $before;
    if ($before < 20) return '';
    $pct = (int) round(100 * ($now - $before) / $before);
    if ($pct === 0) return 'no change';
    return ($pct > 0 ? '+' : "\xe2\x88\x92") . abs($pct) . '% on last week';
}

/**
 * The "Open roles now" email section.
 *
 * $freq  'daily' (counts plus up to 10 roles) or 'weekly' (counts, trends on
 *        the week, and a fuller roundup of up to 20 roles grouped by family).
 * $args  country  ISO2 the reader chose, '' for everywhere
 *        family   role family slug the reader chose (vocab FUNCTIONS), '' for all
 *        link     callable($url) the composer wraps the CV button with (its
 *                 click tracking); identity when absent. Role links are
 *                 NEVER wrapped: they go straight to the employer's page.
 *        now      timestamp, for tests
 *
 * Returns array('html', 'text', 'count') or null when there is nothing current
 * to say. Inline styles only: email clients drop <style>. Colours are the
 * tracker's own tokens (dashboard.css): ink #1b2130, muted #4a4d55, link
 * #1a5fb4 (all 6.2:1 or more on white); the button is white on accent
 * #0072B2 at 5.2:1, which passes WCAG AA for its 15px bold text.
 */
function tit_open_roles_email_section($freq, $args = array()) {
    $freq = ($freq === 'weekly') ? 'weekly' : 'daily';
    $d = tit_open_roles_data($args['now'] ?? null);
    if (!$d || (int) ($d['totals']['roles'] ?? 0) <= 0) return null;
    $country = strtoupper(trim((string) ($args['country'] ?? '')));
    $family = trim((string) ($args['family'] ?? ''));
    $link = (isset($args['link']) && is_callable($args['link'])) ? $args['link'] : function ($u) { return $u; };
    $h = function ($s) { return esc_html((string) $s); };
    $cname = function ($cc) { return function_exists('tit_country_name') ? tit_country_name($cc) : $cc; };

    // The headline count is the reader's own slice.
    if ($country !== '' && $family !== '') {
        $n = (int) ($d['by_country_family'][$country][$family] ?? 0);
    } elseif ($country !== '') {
        $n = (int) ($d['by_country'][$country] ?? 0);
    } elseif ($family !== '') {
        $n = (int) ($d['by_family'][$family] ?? 0);
    } else {
        $n = (int) $d['totals']['roles'];
    }
    $scope = array();
    if ($family !== '') $scope[] = tit_open_roles_family_label($family, $d);
    if ($country !== '') $scope[] = 'in ' . $cname($country);
    $scope_txt = $scope ? ' ' . implode(' ', $scope) : '';
    $employers = (int) ($d['totals']['employers'] ?? 0);

    $limit = $freq === 'weekly' ? TIT_OPEN_ROLES_WEEKLY_N : TIT_OPEN_ROLES_DAILY_N;
    $roles = tit_open_roles_pick($d, $country, $family, $limit);
    if ($n <= 0 && !$roles) return null;

    $ink = '#1b2130'; $mut = '#4a4d55'; $lnk = '#1a5fb4'; $acc = '#0072B2';
    $p = 'margin:0 0 8px;font-size:15px;line-height:1.5;color:' . $ink . ';';
    $small = 'margin:0 0 6px;font-size:13px;line-height:1.5;color:' . $mut . ';';
    $a = 'color:' . $lnk . ';text-decoration:underline;';

    $html = '<h2 style="margin:24px 0 8px;font-size:20px;line-height:1.3;color:' . $ink . ';">Open roles now</h2>';
    $text = "\nOpen roles now\n";
    $lead = number_format($n) . ' open roles' . $scope_txt . ' on the job boards of the ' . number_format($employers)
          . ' employers we track.';
    $html .= '<p style="' . $p . '">' . $h($lead) . '</p>';
    $text .= $lead . "\n";

    // Counts: where (country, or state and city inside a chosen country) and what.
    $where_rows = array();
    if ($country === 'US') {
        foreach (tit_open_roles_top($d['by_state'] ?? array(), 5) as $r) $where_rows[] = array($r[0], $r[1]);
    } elseif ($country === '') {
        foreach (tit_open_roles_top($d['by_country'] ?? array(), 5) as $r) $where_rows[] = array($cname($r[0]), $r[1]);
    }
    $city_rows = array();
    $cities = $country !== '' ? (array) ($d['by_country_city'][$country] ?? array()) : (array) ($d['by_city'] ?? array());
    foreach (tit_open_roles_top($cities, 5) as $r) $city_rows[] = $r;
    $fam_map = $country !== '' ? (array) ($d['by_country_family'][$country] ?? array()) : (array) ($d['by_family'] ?? array());
    $fam_rows = array();
    foreach (tit_open_roles_top($fam_map, 5) as $r) $fam_rows[] = array(tit_open_roles_family_label($r[0], $d), $r[1]);

    $week = is_array($d['week_ago'] ?? null) ? $d['week_ago'] : null;
    $line = function ($label, $rows, $prior = null) use ($h, $small) {
        if (!$rows) return array('', '');
        $parts = array();
        foreach ($rows as $r) $parts[] = $r[0] . ' ' . number_format($r[1]);
        $s = $label . ': ' . implode(' · ', $parts) . '.';
        return array('<p style="' . $small . '">' . $h($s) . '</p>', '  ' . $s . "\n");
    };
    foreach (array(array($country === 'US' ? 'By state' : 'By country', $where_rows),
                   array('By city', $city_rows), array('By role family', $fam_rows)) as $blk) {
        list($bh, $bt) = $line($blk[0], $blk[1]);
        $html .= $bh; $text .= $bt;
    }

    // Weekly only: the trend on the week, from the recorded daily counts.
    if ($freq === 'weekly' && $week) {
        $before = (int) ($week['total'] ?? 0);
        if ($country !== '') $before = (int) ($week['country'][$country] ?? 0);
        elseif ($family !== '') $before = (int) ($week['family'][$family] ?? 0);
        $chg = ($country !== '' && $family !== '') ? '' : tit_open_roles_change($n, $before);
        $moves = array();
        foreach ((array) ($d['by_family'] ?? array()) as $f => $v) {
            $b = (int) ($week['family'][$f] ?? 0);
            if ($b >= 50) $moves[$f] = ($v - $b) / $b;
        }
        arsort($moves);
        $up = array_slice($moves, 0, 2, true);
        $bits = array();
        if ($chg !== '') $bits[] = 'Total ' . $chg . '.';
        $rising = array();
        foreach ($up as $f => $r) if ($r >= 0.03) $rising[] = tit_open_roles_family_label($f, $d) . ' +' . (int) round($r * 100) . '%';
        if ($rising) $bits[] = 'Growing fastest: ' . implode(', ', $rising) . '.';
        if ($bits) {
            $s = 'This week: ' . implode(' ', $bits) . ' Compared with ' . gmdate('j M', strtotime($week['date'])) . '.';
            $html .= '<p style="' . $small . '">' . $h($s) . '</p>';
            $text .= '  ' . $s . "\n";
        }
    }

    // The roles. One line each: company, role, city, a link to the employer's own page.
    if ($roles) {
        $hd = $freq === 'weekly' ? 'Roles to look at this week' : 'New on employer job boards';
        $html .= '<h3 style="margin:16px 0 6px;font-size:16px;line-height:1.3;color:' . $ink . ';">' . $h($hd) . '</h3>';
        $text .= "\n" . $hd . "\n";
        $groups = array();
        if ($freq === 'weekly' && $family === '') {
            $other = array();
            foreach ($roles as $r) {
                if (($r['family'] ?? '') === '') { $other[] = $r; continue; }
                $groups[tit_open_roles_family_label($r['family'], $d)][] = $r;
            }
            ksort($groups);
            if ($other) $groups['Other roles'] = $other;
        } else {
            $groups[''] = $roles;
        }
        foreach ($groups as $g => $rs) {
            if ($g !== '') {
                $html .= '<p style="margin:10px 0 4px;font-size:13px;font-weight:700;color:' . $mut . ';">' . $h($g) . '</p>';
                $text .= '  ' . $g . "\n";
            }
            $html .= '<ul style="margin:0 0 8px;padding:0 0 0 18px;">';
            foreach ($rs as $r) {
                $who = tit_open_roles_employer($r['company'] ?? '');
                $where = tit_open_roles_where($r);
                $url = (string) $r['url'];
                $html .= '<li style="margin:0 0 6px;font-size:15px;line-height:1.45;color:' . $ink . ';">'
                       . '<strong>' . $h($who) . '</strong>: '
                       // NEVER wrapped in $link: a click-tracking redirect would send the
                       // reader through our site, and the ruling is straight to the employer.
                       . '<a href="' . esc_url($url) . '" style="' . $a . '">' . $h($r['title']) . '</a>'
                       . ($where !== '' ? ' <span style="color:' . $mut . ';">· ' . $h($where) . '</span>' : '')
                       . '</li>';
                $text .= '  - ' . $who . ': ' . $r['title'] . ($where !== '' ? ' · ' . $where : '') . "\n    " . $url . "\n";
            }
            $html .= '</ul>';
        }
        $note = "Links go to the employer's own job page. Checked " . tit_open_roles_checked_label($d) . '.';
        $html .= '<p style="' . $small . '">' . $h($note) . '</p>';
        $text .= '  ' . $note . "\n";
    }

    $cv = tit_open_roles_cv_url($freq);
    $html .= '<p style="margin:14px 0 4px;"><a href="' . esc_url(call_user_func($link, $cv)) . '" style="display:inline-block;'
           . 'background:' . $acc . ';color:#ffffff;font-size:15px;font-weight:700;line-height:1.2;text-decoration:none;'
           . 'padding:12px 18px;border-radius:6px;">See all matches for your CV</a></p>';
    $text .= "\nSee all matches for your CV: " . $cv . "\n";

    return array('html' => $html, 'text' => $text, 'count' => $n);
}

/**
 * What a reader can choose in their email settings: countries that have open
 * roles today, and the role families, as array('countries' => [code => name],
 * 'families' => [slug => label]). Empty arrays when there is no current data;
 * the settings form then shows no choice rather than a stale one.
 */
function tit_open_roles_choices($now = null) {
    $d = tit_open_roles_data($now);
    if (!$d) return array('countries' => array(), 'families' => array());
    $countries = array();
    foreach ((array) ($d['by_country'] ?? array()) as $cc => $n) {
        if ((int) $n <= 0 || !preg_match('/^[A-Z]{2}$/', (string) $cc)) continue;
        $countries[$cc] = function_exists('tit_country_name') ? tit_country_name($cc) : $cc;
    }
    asort($countries);
    $families = array();
    foreach ((array) ($d['by_family'] ?? array()) as $f => $n) {
        if ((int) $n > 0) $families[(string) $f] = tit_open_roles_family_label($f, $d);
    }
    asort($families);
    return array('countries' => $countries, 'families' => $families);
}

// --- Ingest ----------------------------------------------------------------

function tit_open_roles_register_route() {
    register_rest_route('talent/v1', '/open-roles', array(
        'methods'  => 'POST',
        'callback' => 'tit_api_open_roles',
        'permission_callback' => function_exists('tit_api_permission') ? 'tit_api_permission' : '__return_false',
    ));
}
add_action('rest_api_init', 'tit_open_roles_register_route');

/**
 * Stores the snapshot whole, replacing the last one. Rejects a payload whose
 * roles are not all https links off our host, or that names a source outside
 * tit_open_roles_sources(): one bad row refuses the lot rather than being
 * silently dropped, because a silent drop is how a rule erodes.
 */
function tit_api_open_roles(WP_REST_Request $req) {
    $b = $req->get_json_params();
    if (!is_array($b) || empty($b['as_of']) || empty($b['checked_on'])
        || !isset($b['companies']) || !is_array($b['companies'])
        || !isset($b['pool']) || !is_array($b['pool'])) {
        return new WP_Error('tit_open_roles_bad_body', 'Needs as_of, checked_on, companies and pool.', array('status' => 400));
    }
    if (!strtotime((string) $b['as_of']) || !preg_match('/^\d{4}-\d{2}-\d{2}$/', (string) $b['checked_on'])) {
        return new WP_Error('tit_open_roles_bad_date', 'as_of and checked_on must be dates.', array('status' => 400));
    }
    foreach ((array) ($b['sources'] ?? array()) as $s) {
        if (!in_array($s, tit_open_roles_sources(), true)) {
            return new WP_Error('tit_open_roles_source', 'Source not cleared for display: ' . sanitize_text_field((string) $s), array('status' => 400));
        }
    }
    $check = function ($r) {
        return is_array($r) && !empty($r['title']) && tit_open_roles_link_ok($r['url'] ?? '');
    };
    foreach ($b['companies'] as $c) {
        foreach ((array) ($c['roles'] ?? array()) as $r) {
            if (!$check($r)) return new WP_Error('tit_open_roles_link', 'Every role needs a title and an https link to the employer.', array('status' => 400));
        }
    }
    foreach ($b['pool'] as $r) {
        if (!$check($r)) return new WP_Error('tit_open_roles_link', 'Every role needs a title and an https link to the employer.', array('status' => 400));
    }
    update_option('tit_open_roles', $b, false);
    if (function_exists('tit_flush_caches')) tit_flush_caches();
    return rest_ensure_response(array(
        'stored' => true, 'as_of' => sanitize_text_field((string) $b['as_of']),
        'employers' => count($b['companies']), 'pool' => count($b['pool']),
        'roles' => (int) ($b['totals']['roles'] ?? 0),
    ));
}
