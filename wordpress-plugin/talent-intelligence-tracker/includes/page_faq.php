<?php
/**
 * Intro paragraph + H2 + 3-question FAQ on the tracker page and the static
 * routes, and FAQPage JSON-LD built from exactly that visible FAQ (1.105.0;
 * ai-layoff-tracker#446 items 2 and 3, ported). Owner ruling 2026-10-14:
 * Claude writes the wording, the owner edits it in the PR.
 *
 * Rules, pinned by tests/test_page_faq.py:
 *  - every figure and the year come from tit_page_faq_facts() at render time,
 *    read off tit_press_facts(), the bundle the press page already prints;
 *    a figure that cannot be read is 0 or '' and its sentence is dropped;
 *  - the JSON-LD is printed inside the same <section> as the visible FAQ,
 *    from the same strings, so it exists only where the FAQ is shown (the
 *    sibling's 2026-07-28 lesson, recorded in company.php and places.php);
 *  - no per-entity pages (company, place, role): thousands of near-identical
 *    FAQs is the pattern that lesson warns about.
 * This product has no Methodology page; method is linked as Data Sources and
 * Measured Recall.
 */
if (!defined('ABSPATH')) exit;

/** Render-time facts. Each value is 0 or '' when it cannot be read. */
function tit_page_faq_facts() {
    static $f = null;
    if ($f !== null) return $f;
    $f = array('y' => (int) current_time('Y'), 'upd_y' => 0, 'emp_y' => 0, 'ver_y' => 0,
               'all_n' => 0, 'all_c' => 0, 'countries' => 0, 'top_country' => '',
               'top_industry' => '', 'cadence' => '');
    try {
        if (function_exists('tit_press_facts') && function_exists('tit_table_name')) {
            $p = tit_press_facts(tit_table_name());
            foreach ((array) ($p['windows'] ?? array()) as $w) {
                if (($w['key'] ?? '') !== 'year') continue;
                $f['upd_y'] = max(0, (int) ($w['n'] ?? 0));
                $f['emp_y'] = max(0, (int) ($w['c'] ?? 0));
                $f['ver_y'] = max(0, (int) ($w['v'] ?? 0));
            }
            $f['all_n'] = max(0, (int) ($p['all']['n'] ?? 0));
            $f['all_c'] = max(0, (int) ($p['all']['c'] ?? 0));
            $f['countries'] = max(0, (int) ($p['all']['countries'] ?? 0));
            $f['top_country'] = (string) ($p['top_country']['k'] ?? '');
            $f['top_industry'] = (string) ($p['top_industry']['k'] ?? '');
        }
        if (function_exists('tit_ingest_cadence_phrase')) $f['cadence'] = (string) tit_ingest_cadence_phrase();
    } catch (\Throwable $e) {
        // Keep whatever was read; missing figures drop their sentences.
    }
    return $f;
}

/** Route URLs the copy links to, read from each route's own helper. */
function tit_page_faq_urls() {
    $u = function ($fn, $path) { return function_exists($fn) ? $fn() : home_url($path); };
    return array(
        'dashboard'   => home_url('/talent-intelligence-tracker/'),
        'sources'     => $u('tit_sources_url', '/talent-intelligence-tracker/sources/'),
        'recall'      => $u('tit_recall_url', '/talent-intelligence-tracker/recall/'),
        'corrections' => $u('tit_corrections_url', '/talent-intelligence-tracker/corrections/'),
        'places'      => $u('tit_places_url', '/talent-intelligence-tracker/places/'),
        'roles'       => $u('tit_roles_url', '/talent-intelligence-tracker/roles/'),
        'contact'     => home_url('/contact/'),
    );
}

/** Pure: join the non-empty sentences. */
function tit_page_faq_join($parts) {
    return trim(implode(' ', array_filter(array_map('trim', $parts), 'strlen')));
}

/** Pure: the copy for one page key, or null when the page gets none. */
function tit_page_faq_content($key, $f, $u) {
    $nf = function ($v) { return number_format((float) $v); };
    $y = (int) $f['y'];
    $a = function ($k, $label) use ($u) { return '<a href="' . esc_url($u[$k]) . '">' . esc_html($label) . '</a>'; };
    $has_ytd = $f['upd_y'] > 0 && $f['emp_y'] > 0;

    $ytd = $has_ytd ? 'So far in ' . $y . ', the tracker holds ' . $nf($f['upd_y']) . ' hiring updates from '
        . $nf($f['emp_y']) . ' employers.' : '';
    $ver = ($has_ytd && $f['ver_y'] > 0) ? $nf($f['ver_y']) . ' of them come straight from an official filing.' : '';
    $all = ($f['all_n'] > 0 && $f['all_c'] > 0 && $f['countries'] > 0)
        ? 'In all, it holds ' . $nf($f['all_n']) . ' updates from ' . $nf($f['all_c']) . ' employers in '
          . $nf($f['countries']) . ' countries.' : '';
    $top_c = $f['top_country'] !== '' ? esc_html($f['top_country']) . ' has the most updates so far this year.' : '';
    $top_i = $f['top_industry'] !== '' ? esc_html($f['top_industry']) . ' is the industry with the most updates so far this year.' : '';
    $most = 'Most updates means the most records, not the most jobs.';
    $cad = $f['cadence'] !== '' ? 'Collection runs ' . esc_html($f['cadence']) . '.' : '';
    $what = 'An update is a public record that an employer is adding roles, raising money, changing leadership or moving a site.';
    $not_ads = 'It is a sign of hiring, not a job ad, and we do not list open jobs.';
    $origin = 'From regulatory filings, employer announcements and news reports.';
    $linked = 'Each update links to the filing or report behind it.';
    $srcs = $a('sources', 'Data Sources');
    $recall = $a('recall', 'Measured Recall');
    $method = 'The ' . $srcs . ' page lists every source we read, and the ' . $recall . ' page shows how much we miss.';
    $q_where = array('Where does this data come from?', tit_page_faq_join(array($origin, $linked, $method)));
    $q_who = array('Which companies are hiring in ' . $y . '?', tit_page_faq_join(array(
        $ytd, 'The rankings on the ' . $a('dashboard', 'tracker') . ' list the employers with the most updates, and the '
        . $a('roles', "Who's Hiring") . ' page breaks them down by role.',
        'An update shows what a filing or report said, not a count of open jobs.')));

    $pages = array(
        'dashboard' => array(
            'intro' => array('This tracker follows public signs that employers are hiring: new roles, funding rounds, leadership changes and site moves.', $ytd, $ver),
            'h2' => 'Hiring News: Common Questions',
            'faq' => array(
                array('Which companies are hiring in ' . $y . '?', tit_page_faq_join(array(
                    $ytd, 'The rankings on this page list the employers with the most updates, and the '
                    . $a('roles', "Who's Hiring") . ' page breaks them down by role.',
                    'An update shows what a filing or report said, not a count of open jobs.'))),
                array('Which countries and industries have the most hiring news in ' . $y . '?', tit_page_faq_join(array(
                    $top_c, $top_i, ($top_c !== '' || $top_i !== '') ? $most : '',
                    'The ' . $a('places', 'Countries, Cities And Industries') . ' page links to a page for each one.'))),
                array('Where does this data come from?', tit_page_faq_join(array($origin, $linked, $method))),
            ),
        ),
        'sources' => array(
            'intro' => array('This page lists every source the Talent Intelligence Tracker reads, and the ones we have researched but do not read yet.', $all),
            'h2' => 'Data Sources: Common Questions',
            'faq' => array(
                array('Where does this data come from?', tit_page_faq_join(array(
                    $origin, $linked, 'The ' . $recall . ' page shows how much we miss.'))),
                array('Is a news report treated the same as a filing?', tit_page_faq_join(array(
                    'No. An update read from an official filing or an employer\'s own statement is labelled verified.',
                    'A claim made by a publication is labelled reported, and a news article never becomes verified.'))),
                array('How often is the data updated?', tit_page_faq_join(array(
                    $cad, 'The figures on the ' . $a('dashboard', 'tracker') . ' are recomputed at least every five minutes.'))),
            ),
        ),
        'press' => array(
            'intro' => array('This page gives journalists and researchers figures from the Talent Intelligence Tracker, each linked to the rows behind it.', $ytd, $ver),
            'h2' => 'For Journalists: Common Questions',
            'faq' => array(
                $q_who,
                array('Can I use this data in my reporting?', tit_page_faq_join(array(
                    'Yes, under a CC BY 4.0 license, citing the Talent Intelligence Tracker at asktherecruiter.com.',
                    'CSV and JSON downloads carry the same data.',
                    'Please give the date you read a figure, because totals change as new records arrive.'))),
                $q_where,
            ),
        ),
        'recall' => array(
            'intro' => array('This page measures how many real hiring events the tracker holds, against a fixed test list built without looking at our own data.', 'We publish the result, including the places where we do badly.'),
            'h2' => 'Measured Recall: Common Questions',
            'faq' => array(
                array('What does recall mean here?', tit_page_faq_join(array(
                    'Recall is the share of real events on the test list that the tracker holds.',
                    'A low score in a country means we read few sources there, not that nothing happened.'))),
                array('How is the test list built?', tit_page_faq_join(array(
                    'A person assembles a fixed list of real events from public sources, without looking at our database.',
                    'We then check how many of them we hold.',
                    'Building it by hand keeps the test from measuring only what is easy to find.'))),
                array('Where does this data come from?', tit_page_faq_join(array(
                    $origin, $linked, 'The ' . $srcs . ' page lists every source we read.'))),
            ),
        ),
        'corrections' => array(
            'intro' => array('This page lists every correction to the Talent Intelligence Tracker: what was wrong, how many records it touched and what they say now.', $all, $linked),
            'h2' => 'Corrections: Common Questions',
            'faq' => array(
                array('How do I report an error?', tit_page_faq_join(array(
                    'Use the ' . $a('contact', 'contact page') . ' and name the update you think is wrong.',
                    'Each update links to its source, so you can check the figure against the document first.'))),
                array('Do you delete records that turn out to be wrong?', tit_page_faq_join(array(
                    'No. A correction adds a new revision, and this page records what changed and when.'))),
                $q_where,
            ),
        ),
        'places' => array(
            'intro' => array('This page lists every country, city and industry with its own page on the Talent Intelligence Tracker.', 'A place gets a page once we hold enough updates, employers and sources on it.', $top_c),
            'h2' => 'Places: Common Questions',
            'faq' => array(
                array('Which countries have the most hiring news in ' . $y . '?', tit_page_faq_join(array(
                    $top_c, $top_c !== '' ? $most : '', 'Each country page ranks the employers behind its updates.'))),
                array('Which industries are hiring in ' . $y . '?', tit_page_faq_join(array(
                    $top_i, 'Each industry page lists the employers behind its updates.'))),
                $q_where,
            ),
        ),
        'roles' => array(
            'intro' => array('This page lists the roles, and roles in a city, that have their own page on the Talent Intelligence Tracker.', 'A role gets a page once we hold enough source documents, employers and sources on it.', $ytd),
            'h2' => "Who's Hiring: Common Questions",
            'faq' => array(
                $q_who,
                array('Is this a list of job openings?', tit_page_faq_join(array('No. ' . $what, $not_ads))),
                $q_where,
            ),
        ),
    );
    if (!isset($pages[$key])) return null;
    $p = $pages[$key];
    return array('intro' => tit_page_faq_join($p['intro']), 'h2' => $p['h2'], 'faq' => $p['faq']);
}

/** Pure: the intro paragraph, '' without content. */
function tit_page_faq_intro_html($c) {
    return $c ? '<p class="tit-page-intro">' . $c['intro'] . '</p>' : '';
}

/** Pure: the FAQ section with its FAQPage JSON-LD, '' without content. */
function tit_page_faq_section_html($c) {
    if (!$c) return '';
    $node = array('@context' => 'https://schema.org', '@type' => 'FAQPage', 'mainEntity' => array());
    $items = '';
    foreach ($c['faq'] as $qa) {
        $items .= '<h3>' . esc_html($qa[0]) . '</h3><p>' . $qa[1] . '</p>';
        $node['mainEntity'][] = array('@type' => 'Question', 'name' => $qa[0],
            'acceptedAnswer' => array('@type' => 'Answer', 'text' => $qa[1]));
    }
    return '<section class="tit-page-faq" aria-label="Common questions"><h2>' . esc_html($c['h2']) . '</h2>' . $items
        . '<script type="application/ld+json">'
        . wp_json_encode($node, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_HEX_TAG)
        . '</script></section>';
}

/** Runtime: the intro for a page key. */
function tit_page_faq_intro($key) {
    return tit_page_faq_intro_html(tit_page_faq_content($key, tit_page_faq_facts(), tit_page_faq_urls()));
}

/** Runtime: the FAQ section for a page key. */
function tit_page_faq_section($key) {
    return tit_page_faq_section_html(tit_page_faq_content($key, tit_page_faq_facts(), tit_page_faq_urls()));
}

/**
 * Pure: intro just inside the dashboard's opening <div class="tit-wrap">,
 * section inside its last closing </div>.
 *
 * The intro used to be prepended ahead of $html entirely, which put it
 * outside .tit-wrap. dashboard.css only re-inks `.tit-wrap p` for dark mode
 * (the site's own `.entry-content p { color:#2a2a2a !important }` wins
 * everywhere else), so that intro rendered at a 1.26 contrast ratio in dark
 * mode while every other route's intro -- echoed INSIDE its own .tit-wrap --
 * was fine. tests/test_page_faq.py::test_dashboard_intro_lands_inside_tit_wrap
 * pins the placement.
 */
function tit_page_faq_wrap_html($html, $intro, $section) {
    if ($section !== '') {
        $at = strrpos($html, '</div>');
        $html = $at === false ? $html . $section : substr($html, 0, $at) . $section . substr($html, $at);
    }
    if ($intro !== '') {
        $at = strpos($html, '>');
        $html = $at === false ? $intro . $html : substr($html, 0, $at + 1) . $intro . substr($html, $at + 1);
    }
    return $html;
}

/** Runtime: the dashboard shortcode's output with its intro and FAQ. */
function tit_page_faq_wrap_dashboard($html) {
    if ($html === '') return $html;
    return tit_page_faq_wrap_html($html, tit_page_faq_intro('dashboard'), tit_page_faq_section('dashboard'));
}
