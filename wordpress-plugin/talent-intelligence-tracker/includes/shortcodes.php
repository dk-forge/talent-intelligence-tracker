<?php
/**
 * Renders the dashboard. Server-side first so the page is useful (and
 * indexable) before any JavaScript runs; the filters then talk to /query.
 *
 * UI copy here uses no em-dashes, matching the house style.
 */

if (!defined('ABSPATH')) exit;

function tit_dashboard_shortcode() {
    // Enqueue from INSIDE the shortcode as well as from wp_enqueue_scripts.
    // The hook's guard asks has_shortcode($post->post_content, ...), which is
    // FALSE whenever the shortcode reaches the page through a block, pattern,
    // template part or reusable block rather than sitting raw in post_content.
    // The dashboard then rendered with no stylesheet at all -- every tit- class
    // inert, the page raw HTML (observed live 2026-07-28). Enqueuing where the
    // markup is actually produced cannot drift from where it is used.
    if (function_exists('tit_enqueue_dashboard_assets')) tit_enqueue_dashboard_assets();
    global $wpdb;
    $table = tit_table_name();

    $total_all = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$table} WHERE is_current = 1");

    ob_start();

    if ($total_all === 0) {
        ?>
        <div class="tit-wrap">
          <div class="tit-empty">
            <h2>No signals published yet</h2>
            <p>Collection has not been switched on. Every record here has to
               link to the article that makes the claim, and that sourcing is
               still being proven out, so an empty table is the honest state
               rather than a broken one.</p>
            <p class="tit-empty-note">This page will fill in once collection is
               armed. It will not be backfilled with anything unsourced.</p>
          </div>
        </div>
        <?php
        return ob_get_clean();
    }

    /*
      The page LEADS with what is worth a reader's time, and says so.

      An SEC backfill that worked landed thousands of routine officer changes:
      each one accurate, each one verified, and together they buried the hiring
      and funding news anyone actually opened this page for. The first screen
      was micro-cap CFO appointments as far as you could scroll.

      So the default view sets routine filings aside. Nothing is deleted and
      nothing is hidden: the control is right above the table, it states both
      counts, and it says in one sentence what we mean by routine, so a reader
      can judge our judgement instead of trusting it. Every server-rendered
      figure below sits under the SAME clause the table does, or the hero would
      describe a set the rows do not belong to.
    */
    $notable_sql = function_exists('tit_notable_where') ? tit_notable_where() : '1 = 1';
    $base = "is_current = 1 AND {$notable_sql}";

    // Both sides of the control, counted, never written down.
    $md = $wpdb->get_row(
        "SELECT SUM(materiality = 'routine') routine,
                SUM(materiality IS NULL OR materiality <> 'routine') notable
           FROM {$table} WHERE is_current = 1", ARRAY_A) ?: array();
    $n_routine = (int) ($md['routine'] ?? 0);
    $n_notable = (int) ($md['notable'] ?? $total_all);
    $total = $n_notable;

    // How many updates in the default view actually state a headcount. Printed
    // beside the toggle, so a reader sees what it would do before using it.
    $n_stated = (int) $wpdb->get_var(
        "SELECT COUNT(*) FROM {$table} WHERE {$base}
          AND signal_direction IN ('hiring', 'displacement')"
    );

    $by_pillar = $wpdb->get_results(
        "SELECT pillar, COUNT(*) n FROM {$table} WHERE {$base} GROUP BY pillar ORDER BY n DESC",
        ARRAY_A
    ) ?: array();

    $countries = (int) $wpdb->get_var(
        "SELECT COUNT(DISTINCT COALESCE(country, hq_country)) FROM {$table} WHERE {$base}"
    );
    $companies = (int) $wpdb->get_var(
        "SELECT COUNT(DISTINCT company_key) FROM {$table} WHERE {$base}"
    );
    $verified = (int) $wpdb->get_var(
        "SELECT COUNT(*) FROM {$table} WHERE {$base} AND confidence = 'verified'"
    );

    // Bounds for the date inputs. The sibling can offer years, quarters and
    // months because it holds years; we hold days. Letting the control ask for
    // a period we have nothing in is a control that manufactures empty states
    // and makes thin coverage look like a broken filter.
    // ONE query, two scopes. The NOTE describes the set the page is showing, so
    // it agrees with every other figure in the hero; the date inputs keep the
    // full range, so the control can never refuse a date that exists. Reading
    // these from two queries is how a range and its own label drift apart.
    $span = $wpdb->get_row(
        "SELECT MIN(COALESCE(published_date, DATE(captured_at))) lo_all,
                MAX(COALESCE(published_date, DATE(captured_at))) hi_all,
                MIN(CASE WHEN {$notable_sql} THEN COALESCE(published_date, DATE(captured_at)) END) lo,
                MAX(CASE WHEN {$notable_sql} THEN COALESCE(published_date, DATE(captured_at)) END) hi
           FROM {$table} WHERE is_current = 1", ARRAY_A) ?: array();
    $span_lo = $span['lo_all'] ?? '';
    $span_hi = $span['hi_all'] ?? '';
    $view_lo = $span['lo'] ?? $span_lo;
    $view_hi = $span['hi'] ?? $span_hi;

    $newest_run = $wpdb->get_var("SELECT MAX(captured_at) FROM {$table} WHERE is_current = 1");
    $glance = tit_glance_matrix($table, $base);
    // The money views and the matrix's money row share one coverage figure, so
    // a dollar total can never sit next to a sentence describing a different
    // set of rows.
    $money = tit_money_aggregate($table, $base);
    $glance['coverage'] = $money['coverage'];
    $counts_by_country = array_column($wpdb->get_results(
        "SELECT COALESCE(country, hq_country) k, COUNT(*) n FROM {$table}
          WHERE {$base} AND COALESCE(country, hq_country) IS NOT NULL
          GROUP BY k", ARRAY_A) ?: array(), 'n', 'k');
    // 40, matching /aggregate, not 6. The chart scrolls and expands, so a short
    // list is no longer what keeps the card small -- and a hard six meant the
    // World view could not show two of the eight countries we actually hold.
    $by_country = $wpdb->get_results(
        "SELECT COALESCE(country, hq_country) k, COUNT(*) n FROM {$table}
          WHERE {$base} AND COALESCE(country, hq_country) IS NOT NULL
          GROUP BY k ORDER BY n DESC LIMIT 40", ARRAY_A) ?: array();
    $by_direction = $wpdb->get_results(
        "SELECT signal_direction k, COUNT(*) n FROM {$table} WHERE {$base}
          GROUP BY signal_direction ORDER BY n DESC", ARRAY_A) ?: array();

    // Materiality first, recency inside it, matching /query's default sort so
    // the first paint and the first repaint cannot put the rows in a different
    // order. A stated headcount or a real funding amount outranks a bare
    // officer change; an unjudged row outranks a judged-routine one.
    $rows = $wpdb->get_results(
        "SELECT signal_id, headline, talent_readthrough, company, company_key, pillar, signal_direction,
                city, country, hq_city, hq_country, confidence, source_url, source_name,
                published_date
           FROM {$table} WHERE {$base}
          ORDER BY CASE materiality WHEN 'high' THEN 0 WHEN 'medium' THEN 1
                                    WHEN 'routine' THEN 3 ELSE 2 END ASC,
                   COALESCE(published_date, DATE(captured_at)) DESC, row_id DESC
          LIMIT 50",
        ARRAY_A
    ) ?: array();

    // Recruiter language, not ours. "Pillar" and "signal direction" are
    // internal vocabulary and never appear on the page.
    $labels = array(
        'company_development' => 'Growing and expanding',
        'leadership_change'   => 'Leadership moves',
        'rewards_comp'        => 'Pay and benefits',
        'how_we_work'         => 'Ways of working',
    );
    $directions = array(
        'hiring'       => 'Hiring up',
        'displacement' => 'Cutting back',
        'comp_shift'   => 'Pay change',
        // "Other change" told the reader nothing: it is the bucket for updates
        // whose source says nothing about headcount at all (a funding round
        // with no hiring plan, a CEO succession). Naming that plainly is both
        // clearer and truer to the rule that we never infer a direction the
        // source did not state.
        'neutral'      => 'Headcount not stated',
    );
    $functions = array(
        'engineering' => 'Engineering', 'data_ai' => 'Data & AI',
        'it_infrastructure' => 'IT & infrastructure', 'product' => 'Product',
        'design' => 'Design', 'finance' => 'Finance', 'hr_people' => 'HR & people',
        'sales' => 'Sales', 'marketing' => 'Marketing',
        'customer_support' => 'Customer support', 'operations' => 'Operations',
        'supply_chain' => 'Supply chain', 'manufacturing' => 'Manufacturing',
        'legal_compliance' => 'Legal & compliance', 'research' => 'Research',
        'clinical_healthcare' => 'Clinical & healthcare', 'executive' => 'Executive',
    );
    $industries = tit_industry_labels();
    $confidences = tit_confidence_labels();
    ?>
    <!--
      The config also rides on the element, not only on wp_localize_script.
      Autoptimize aggregates INLINE scripts into a bundle, and our
      autoptimize_filter_js_exclude only protects assets matched by path, so
      dashboard.js stays where it is while the inline `TIT` it depends on gets
      swept into a bundle that loads after it. The script then saw
      `typeof TIT === "undefined"`, returned on its first statement, and every
      filter, the region strip and the quick views did nothing at all on the
      live site while looking perfectly fine. Markup cannot be reordered away
      from the element it describes.
    -->
    <div class="tit-wrap" id="tit-dashboard"
         data-api="<?php echo esc_attr(rest_url('talent/v1/')); ?>"
         data-countries="<?php echo esc_attr(wp_json_encode(tit_country_names())); ?>">

      <div class="tit-hero">
        <div class="tit-hero-top">
          <?php
          /*
            The benefit, then the proof.

            "Who is hiring, who is raising money, and who is changing
            leadership" described the CONTENTS and buried the reason to be
            here. A funding round, a new CEO or a new office all happen weeks
            before the roles get posted: for a recruiter that is prospecting
            ahead of everyone else, for a job seeker it is applying before the
            flood. That is the product, and it now says so.

            Deliberately an h2 and not an h1, though the copy is the page's
            headline: the theme already renders "Talent Intelligence Tracker"
            as the h1, that is the keyword-bearing heading we were asked to
            keep for search, and a second h1 would split the signal it exists
            to carry. It is styled as the dominant heading, which is what
            actually matters to a reader.

            No superlatives here or anywhere else on the page: "most advanced"
            and its family are the one class of claim a skeptic can disprove in
            thirty seconds, and everything visible has to survive a check.
          */
          ?>
          <div class="tit-hero-head">
            <h2>Know who's hiring before the job ad appears</h2>
            <p class="tit-hero-sub">Every update links to the filing or report behind it</p>
          </div>
          <?php /* The sibling's freshness shape: the absolute timestamp WITH
                   its timezone is the primary fact ("Live · updated Jul 28,
                   1:51 AM EDT"); the relative time and the next-run note live
                   on Roo's quieter line below. */ ?>
          <div class="tit-live"><span class="tit-live-dot"></span>
            Live<?php if ($newest_run) : ?> · updated
            <?php echo esc_html(tit_local_datetime($newest_run)); ?>
            <?php endif; ?>
          </div>
        </div>

        <div class="tit-roo-row"><?php tit_roo($newest_run); ?></div>

        <div class="tit-glance" id="tit-glance">
          <?php echo tit_glance_matrix_html($glance); ?>
        </div>
        <?php $span_note = tit_span_note($view_lo, $view_hi); ?>
        <?php if ($span_note) : ?>
          <p class="tit-span" id="tit-span"><?php echo esc_html($span_note); ?></p>
        <?php endif; ?>

        <p class="tit-hero-fine">
          <span class="tit-fine-figures"><?php
            /* Money sits WITH the other headline figures, not trailing after
               the sentence. It is still a link, because it is a sum of dollars
               among counts and only honest beside the coverage line the money
               section prints; both read the same aggregate, so the figure and
               its caveat cannot drift apart. */
            $bits = array(
                esc_html(sprintf(_n('%s update', '%s updates', $total, 'tit'), number_format_i18n($total))),
                esc_html(sprintf(_n('%s employer', '%s employers', $companies, 'tit'), number_format_i18n($companies))),
                esc_html(sprintf(_n('%s country', '%s countries', $countries, 'tit'), number_format_i18n($countries))),
            );
            if ($money['total'] > 0) {
                $bits[] = sprintf(
                    '<a class="tit-fine-money" href="#chart-money-country" title="%s">%s raised</a>',
                    esc_attr(tit_money_full($money['total']) . '. '
                             . tit_money_coverage_sentence($money['coverage'])),
                    esc_html(tit_money_short($money['total']))
                );
            }
            $bits[] = esc_html(number_format_i18n($verified)) . ' from official filings';
            echo implode(' · ', $bits);
          ?></span>
          <?php /* Two beats, not one run-on line: the figures, then the promise.
                   They were competing, and the promise is the more important of
                   the two. The links move to a line of their own for the same
                   reason: three different jobs in one paragraph means none of
                   them lands. */ ?>
          <?php /* The linking half of this promise is now the hero sub-line
                   directly above, so saying it twice on one screen would make
                   both readings weaker. What is left is the half the hero does
                   not carry. */ ?>
          <span class="tit-fine-say">No figure appears unless its source states it.</span>
        </p>
        <p class="tit-hero-links">
          <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">Every source</a>
          · <a href="/blog/ai-layoff-tracker/">Layoffs are tracked separately</a>
        </p>
      </div>

      <div class="tit-sec">
        <h3><span class="tit-sec-eyebrow">Activity</span>The market right now</h3>
        <p>Pick a region to narrow the updates below.</p>
      </div>

      <div class="tit-regions" role="group" aria-label="Filter by region">
        <?php foreach (tit_regions($counts_by_country) as $r) : ?>
          <button type="button" class="tit-region<?php echo $r['codes'] === '' ? ' is-on' : ''; ?>"
                  data-codes="<?php echo esc_attr($r['codes']); ?>">
            <span class="tit-region-flag" aria-hidden="true"><?php echo $r['flag']; ?></span>
            <span class="tit-region-name"><?php echo esc_html($r['name']); ?></span>
            <span class="tit-region-n"><?php echo esc_html(number_format_i18n($r['n'])); ?></span>
          </button>
        <?php endforeach; ?>
      </div>

      <?php
      /*
        Quick views, cut back to the ones the at-a-glance matrix cannot express.

        This row used to hold nine chips mixing two different axes: four time
        periods and four signal types, side by side, with nothing saying that
        picking "This month" and picking "Hiring up" narrow the page in
        completely different ways. Since the signal-by-period matrix shipped,
        every one of those eight is a cell in that matrix, done better: the
        matrix shows the count BEFORE you click it, and it crosses time with
        signal instead of making you apply two chips and hope.

        What survives is what the matrix has no axis for. "From Official
        Filings" is a confidence filter, and the matrix has no confidence
        dimension. "Biggest Raises" is a SORT, which a matrix cell cannot be at
        all, and it only became possible when funding_amount_usd gave us a
        number to sort on (the old display string put $9M above $10B).
      */
      $quick_views = array(
          '' => 'Everything',
          'confidence=verified' => 'From Official Filings',
          'funding=1&sort=raised' => 'Biggest Raises',
      );
      ?>
      <div class="tit-quick" role="group" aria-label="Quick views">
        <span class="tit-quick-label">Quick views</span>
        <?php foreach ($quick_views as $spec => $label) : ?>
          <button type="button" class="tit-qv" data-qv="<?php echo esc_attr($spec); ?>"><?php
            echo esc_html($label); ?></button>
        <?php endforeach; ?>
        <span class="tit-quick-hint">For a period, click a number in the table above.</span>
      </div>

      <?php
      /*
        The filter block, in two deliberately different registers.

        The PRIMARY ROW asks questions, because it is where a recruiter or job
        seeker begins and should feel spoken to. MORE FILTERS uses short Title
        Case nouns, because it is a reference list to scan rather than a
        conversation. The two styles never mix inside a group; mixing them is
        what made an earlier version read as a wall of unrelated fragments.

        The front controls do NOT replace the underlying filters. The pillar,
        direction, country, state and city selects still exist, still carry the
        same values, and are still what the querystring, the chips bar, the
        exports and the click-to-filter charts read and write. They are hidden,
        and the visible controls drive them. That keeps this a presentation
        change: nothing about what any filter MEANS has moved.

        No internal vocabulary is reachable from here. "Pillar", "signal",
        "direction", "confidence" and "basis" appear in the code and never on
        the page.
      */
      ?>
      <div class="tit-primary">
        <label class="tit-field tit-field--stack tit-primary-main">
          <span class="tit-field-l">What are you looking for?</span>
          <select id="tit-f-looking" aria-label="What are you looking for">
            <?php foreach (tit_looking_options() as $spec => $label) : ?>
              <option value="<?php echo esc_attr($spec); ?>"><?php echo esc_html($label); ?></option>
            <?php endforeach; ?>
          </select>
        </label>

        <?php /* One place control, not three. Options are grouped Countries,
                 then US States, then Cities, and each one knows which parameter
                 it sets, so the reader picks a place and never a column. The
                 note below is part of THIS control, not a floating sentence:
                 it explains the one thing a reader is most likely to question
                 about a location, and offers the alternative by name. */ ?>
        <div class="tit-field tit-field--stack tit-primary-where">
          <label class="tit-where-label">
            <span class="tit-field-l">Where?</span>
            <select id="tit-f-place" aria-label="Where">
              <option value="">Anywhere</option>
            </select>
          </label>
          <p class="tit-basis">
            <span class="tit-basis-say" id="tit-basis-say">Showing where the work
              is. When a source names no place we use the employer's head
              office.</span>
            <?php /* The button names its DESTINATION, and the server renders
                     the same words JavaScript would, so a reader without it (or
                     anyone looking at the first paint) is never shown a label
                     that describes the wrong direction. The default basis is
                     "any", so the offer is the strict alternative. */ ?>
            <button type="button" class="tit-basis-btn" id="tit-basis-btn"
                    aria-pressed="false">Only use places a source named</button>
          </p>
        </div>

        <label class="tit-field tit-field--stack">
          <span class="tit-field-l">Which employer?</span>
          <input type="search" id="tit-f-company" placeholder="e.g. Apple"
                 aria-label="Which employer">
        </label>

        <?php /* One control that explains itself, with the number INSIDE its own
                 label. It used to render as three stacked lines, "Headcount",
                 "Only updates that state a headcount", "4,018", with nothing
                 saying whether the number was the current count, the count if
                 applied, or something else. It is the count you WOULD see, under
                 the filters in force, and it moves with them.

                 Most of what we hold states no headcount at all, so filtering TO
                 that is asking for the least informative rows. This is the
                 inverse, and nothing could express it before. */ ?>
        <div class="tit-field tit-primary-toggle">
          <label class="tit-check">
            <input type="checkbox" id="tit-f-stated_headcount" value="1">
            <span class="tit-check-t">Only show updates that state a headcount
              <span class="tit-check-n" id="tit-stated-n"><?php
                echo esc_html('(' . number_format_i18n($n_stated) . ')'); ?></span></span>
          </label>
        </div>
      </div>

      <details class="tit-more" id="tit-more">
        <summary><span id="tit-more-label">More filters</span></summary>
        <div class="tit-filters">
          <?php
          /*
            Five of these take SEVERAL values at once, because a recruiter wants
            "Technology or Healthcare" and not one at a time. They are native
            multiple selects: keyboard reachable without a line of our own code,
            scrollable in place, and every choice becomes its own removable chip
            in the filtering bar. /query takes them comma separated and each
            value is checked against its closed vocabulary before it reaches SQL.

            Employer Type, Work Setup and Deal Type are filled from /facets and
            HIDE THEMSELVES when their column is empty. Shipping a control that
            always returns nothing is worse than shipping no control, and a
            hardcoded judgement about which columns are populated goes stale the
            week after it is written. These appear by themselves the day the
            pipeline fills them.
          */
          $multi_note = 'Choose more than one if you like';
          ?>
          <label class="tit-field tit-field--stack">
            <span class="tit-field-l">Team Or Function</span>
            <select id="tit-f-function" multiple size="5"
                    aria-label="Team or function, choose more than one if you like">
              <?php foreach ($functions as $k => $v) : ?>
                <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
              <?php endforeach; ?>
            </select>
            <span class="tit-field-h"><?php echo esc_html($multi_note); ?></span>
          </label>
          <label class="tit-field tit-field--stack">
            <span class="tit-field-l">Industry</span>
            <select id="tit-f-industry" multiple size="5"
                    aria-label="Industry, choose more than one if you like">
              <?php foreach ($industries as $k => $v) : ?>
                <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
              <?php endforeach; ?>
            </select>
            <span class="tit-field-h"><?php echo esc_html($multi_note); ?></span>
          </label>
          <label class="tit-field tit-field--stack" id="tit-field-employer_type" hidden>
            <span class="tit-field-l">Employer Type</span>
            <select id="tit-f-employer_type" multiple size="5"
                    aria-label="Employer type, choose more than one if you like"></select>
            <span class="tit-field-h"><?php echo esc_html($multi_note); ?></span>
          </label>
          <label class="tit-field tit-field--stack" id="tit-field-work_mode" hidden>
            <span class="tit-field-l">Work Setup</span>
            <select id="tit-f-work_mode" multiple size="5"
                    aria-label="Work setup, choose more than one if you like"></select>
            <span class="tit-field-h"><?php echo esc_html($multi_note); ?></span>
          </label>
          <?php /* Bands, not a box to type a number in. A recruiter thinks in
                   orders of magnitude, and an exact figure produces an
                   empty-looking page for anyone who guesses a threshold nothing
                   sits above. Single choice: bands already nest. */ ?>
          <label class="tit-field tit-field--stack">
            <span class="tit-field-l">Amount Raised</span>
            <select id="tit-f-min_funding_usd" aria-label="Smallest amount raised">
              <option value="">Any Amount</option>
              <?php foreach (tit_funding_bands() as $value => $label) : ?>
                <option value="<?php echo esc_attr($value); ?>"><?php echo esc_html($label); ?></option>
              <?php endforeach; ?>
            </select>
          </label>
          <label class="tit-field tit-field--stack" id="tit-field-funding_stage" hidden>
            <span class="tit-field-l">Funding Stage</span>
            <select id="tit-f-funding_stage" multiple size="5"
                    aria-label="Funding stage, choose more than one if you like"></select>
            <span class="tit-field-h"><?php echo esc_html($multi_note); ?></span>
          </label>
          <label class="tit-field tit-field--stack" id="tit-field-deal_type" hidden>
            <span class="tit-field-l">Deal Type</span>
            <select id="tit-f-deal_type" multiple size="5"
                    aria-label="Deal type, choose more than one if you like"></select>
            <span class="tit-field-h"><?php echo esc_html($multi_note); ?></span>
          </label>
          <label class="tit-field tit-field--stack">
            <span class="tit-field-l">Evidence</span>
            <select id="tit-f-confidence" aria-label="What the record is based on">
              <option value="">Any Evidence</option>
              <?php foreach (tit_confidence_labels() as $k => $v) : ?>
                <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
              <?php endforeach; ?>
            </select>
          </label>
          <label class="tit-field tit-field--stack tit-field--wide">
            <span class="tit-field-l">Keyword Search</span>
            <input type="search" id="tit-f-q" placeholder="Company, industry or keyword"
                   aria-label="Search headlines and read-throughs">
          </label>
          <?php /* One row, always. As two separate grid cells these landed in
                   different rows whenever the column count was even, so a range
                   read as two unrelated dates. The sibling tracker keeps its own
                   From and To together in one box for exactly this reason. */ ?>
          <div class="tit-field tit-field--stack tit-field--wide">
            <span class="tit-field-l">Date From / Date To</span>
            <div class="tit-daterange">
              <label><span class="tit-sr">Earliest date</span>
                <input type="date" id="tit-f-since" aria-label="Earliest date"
                       min="<?php echo esc_attr($span_lo); ?>" max="<?php echo esc_attr($span_hi); ?>"></label>
              <span class="tit-daterange-to" aria-hidden="true">to</span>
              <label><span class="tit-sr">Latest date</span>
                <input type="date" id="tit-f-until" aria-label="Latest date"
                       min="<?php echo esc_attr($span_lo); ?>" max="<?php echo esc_attr($span_hi); ?>"></label>
            </div>
          </div>
          <?php /* Reset sits with the controls it resets, not alone in a bar
                   where it read as a faint link. */ ?>
          <div class="tit-field tit-reset-field">
            <button type="button" class="tit-reset-btn" id="tit-reset">Reset all filters</button>
          </div>
        </div>
      </details>

      <?php /* The state the visible controls drive. Hidden, never focusable,
               and deliberately still real select elements: every existing
               mechanism (the querystring, the chips bar, the exports, the
               click-to-filter charts, the matrix cells) reads and writes these,
               and re-pointing all of it at new state would have turned a
               presentation change into a semantics change.

               `direction` lives here now rather than in More filters. It was
               showing as a second control also labelled "Headcount", beside the
               primary-row checkbox, with different behaviour: one label, two
               controls, which is worse than either alone. Hiring is still
               reachable through "What are you looking for". */ ?>
      <div class="tit-state" hidden aria-hidden="true">
        <select id="tit-f-pillar" tabindex="-1">
          <option value=""></option>
          <?php foreach ($labels as $k => $v) : ?>
            <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
          <?php endforeach; ?>
        </select>
        <select id="tit-f-direction" tabindex="-1">
          <option value=""></option>
          <?php foreach ($directions as $k => $v) : ?>
            <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
          <?php endforeach; ?>
        </select>
        <select id="tit-f-country" tabindex="-1"><option value=""></option></select>
        <select id="tit-f-state" tabindex="-1"><option value=""></option></select>
        <select id="tit-f-city" tabindex="-1"><option value=""></option></select>
        <select id="tit-f-country_basis" tabindex="-1">
          <option value="any"></option>
          <option value="location"></option>
        </select>
      </div>

      <!--
        What is currently being filtered, in words, with a way out of each one.
        Nine controls spread over three rows means a reader can easily be looking
        at a narrowed page without remembering why. This is the sibling's best
        idea and the cheapest thing on the page: the dashboard states its own
        scope instead of leaving the reader to reconstruct it.
      -->
      <div class="tit-active" id="tit-active" hidden>
        <span class="tit-active-label">Filtering</span>
        <span class="tit-active-chips" id="tit-active-chips"></span>
      </div>

      <div class="tit-charts">
      <div class="tit-chart" id="chart-kind">
        <?php /* Headings name what a recruiter or job seeker GETS from the chart,
                 not what the chart is made of. "What kind of update" described
                 the axis; "What is moving" answers the question they opened the
                 page with. The rows are buttons because they ARE filters:
                 dashboard.js routes a click through the same state as the
                 dropdowns, so the subtitle may promise it. Buttons hold span
                 children only (phrasing content), never divs. */ ?>
        <?php tit_chart_head('What is moving', 'Hiring, funding, leadership changes and pay news, ranked by how much of it we are seeing. Click a row to filter.', 'kind'); ?>
      <div class="tit-pillars">
        <?php foreach ($by_pillar as $p) :
            $key = $p['pillar'];
            $pct = $total ? round(100 * $p['n'] / $total) : 0; ?>
          <button type="button" class="tit-pillar" data-k="<?php echo esc_attr($key); ?>" aria-pressed="false">
            <span class="tit-pillar-head">
              <span class="tit-pillar-name"><?php echo esc_html($labels[$key] ?? $key); ?></span>
              <span class="tit-pillar-n"><?php echo esc_html(number_format_i18n($p['n'])); ?></span>
            </span>
            <span class="tit-bar"><span style="width:<?php echo esc_attr($pct); ?>%"></span></span>
          </button>
        <?php endforeach; ?>
      </div>
      </div>
        <div class="tit-chart" id="chart-place">
          <?php tit_chart_head('Where the jobs are', "Counted where the work sits. When a source does not name a place, the employer's head office stands in. Click a row to filter.", 'place'); ?>
          <div class="tit-rank" tabindex="0" role="group" aria-label="Activity by place">
            <?php
            $cmax = $by_country ? max(array_map('intval', array_column($by_country, 'n'))) : 1;
            foreach ($by_country as $c) : ?>
              <button type="button" class="tit-rank-row" data-k="<?php echo esc_attr($c['k']); ?>" aria-pressed="false">
                <span class="tit-rank-name"><?php echo esc_html(tit_country_name($c['k'])); ?></span>
                <span class="tit-rank-track"><span class="tit-rank-fill"
                  style="width:<?php echo esc_attr(max(4, round(100 * $c['n'] / $cmax))); ?>%"></span></span>
                <span class="tit-rank-n"><?php echo (int) $c['n']; ?></span>
              </button>
            <?php endforeach; ?>
          </div>
        </div>

        <div class="tit-chart" id="chart-direction">
          <?php tit_chart_head('Which way headcount is going', 'What the source itself says: roles being added, roles being cut, or a pay action. Most updates say nothing about headcount, and those are counted as such rather than guessed. Click a row to filter.', 'direction'); ?>
          <div class="tit-rank" tabindex="0" role="group" aria-label="Activity by direction">
            <?php
            $dmax = $by_direction ? max(array_map('intval', array_column($by_direction, 'n'))) : 1;
            foreach ($by_direction as $d) : ?>
              <button type="button" class="tit-rank-row" data-k="<?php echo esc_attr($d['k']); ?>"
                      data-dir="<?php echo esc_attr($d['k']); ?>" aria-pressed="false">
                <span class="tit-rank-name"><?php echo esc_html($directions[$d['k']] ?? $d['k']); ?></span>
                <span class="tit-rank-track"><span class="tit-rank-fill"
                  style="width:<?php echo esc_attr(max(4, round(100 * $d['n'] / $dmax))); ?>%"></span></span>
                <span class="tit-rank-n"><?php echo (int) $d['n']; ?></span>
              </button>
            <?php endforeach; ?>
          </div>
        </div>
      </div>

      <!--
        Money. Three rankings of summed US dollars, built from the same chart
        card as everything above them, and each one printing what its totals
        are based on. The coverage line is not decoration: only some rows carry
        a dollar figure, so a total shown without it would read as the whole
        market when it is a floor.
      -->
      <div class="tit-sec tit-sec--money">
        <h3><span class="tit-sec-eyebrow">Funding</span>How much money is being raised</h3>
        <p>Funding rounds added up, in US dollars. Click a row to narrow the
           whole page to that place or industry.</p>
      </div>

      <div class="tit-charts tit-charts-money">
        <?php
        tit_money_chart(
            'country', 'Money raised by country',
            "Totalled where the work sits. When a source names no place, the employer's head office stands in. Click a row to filter.",
            $money['by_country'], $money, 'country',
            function ($k) { return tit_country_name($k); }
        );
        tit_money_chart(
            'city', 'Money raised by city',
            "The cities where funded employers are hiring. Head office stands in when a source names no city. Click a row to filter.",
            $money['by_city'], $money, 'city',
            function ($k) { return $k; }
        );
        tit_money_chart(
            'industry', 'Money raised by industry',
            'Which industries the money is going into. Click a row to filter.',
            $money['by_industry'], $money, 'industry',
            function ($k) use ($industries) { return $industries[$k] ?? $k; }
        );
        ?>
      </div>

      <!--
        The detail control, and the promise that goes with it.

        A default that sets thousands of rows aside has to be visible, has to
        state both counts, and has to say what it means by routine, all in the
        place the reader is about to look at rows. A quiet default with no
        explanation would be withholding data; a page that leads with two
        thousand CFO appointments would be burying it. This is the only way to
        do both.
      -->
      <div class="tit-detail">
        <label class="tit-detail-pick">
          <span class="tit-detail-l">Show</span>
          <select id="tit-f-detail" aria-label="How much detail to show">
            <option value="notable">Notable updates</option>
            <option value="all">Everything, including routine filings</option>
          </select>
        </label>
        <p class="tit-detail-note" id="tit-detail-note"><?php
          echo esc_html(tit_detail_note('notable', $n_notable, $n_routine)); ?></p>
        <?php /* The sort belongs with the rows it orders. It used to sit up in
                 the quick-views strip, three screens above the table, which is
                 where a reader chooses a VIEW and not where they reorder one
                 they are already reading. Column headers below do the same job
                 for the columns they name; this covers the orderings that are
                 not a column, like "most useful" and "biggest raises". */ ?>
        <label class="tit-detail-sort">
          <span class="tit-detail-l">Sort</span>
          <select id="tit-f-sort" class="tit-sort" aria-label="Sort the updates">
            <?php /* Its own option, never a silent tweak to "Newest first": a
                     control labelled newest that does not put the newest row
                     first is a control that lies. */ ?>
            <option value="notable">Most useful first</option>
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="employer">Employer A to Z</option>
            <?php /* Sorting on money only works because funding_amount_usd is
                     a number; the display string beside it cannot be ordered. */ ?>
            <option value="raised">Biggest raises first</option>
          </select>
        </label>
      </div>

      <div class="tit-table-scroll">
        <table class="tit-table">
          <?php
          /*
            Sortable columns. Each one drives the SERVER sort through /query, so
            it orders the whole filtered set and not the fifty rows that happen
            to be on screen; a header that reordered only the visible page would
            be a sort that lies about its own scope. The state rides on the same
            `sort` parameter as the select above, so it round-trips through the
            URL and the exports like everything else, and aria-sort carries it
            for anyone not looking at the arrow.
          */
          $sortable = array(
              'Employer' => 'employer',
              'Where'    => 'place',
              'Evidence' => 'evidence',
              'When'     => 'when',
          );
          $columns = array('Employer', 'What happened', 'Where',
                           'What it means', 'Evidence', 'When', 'Source');
          ?>
          <thead>
            <tr>
              <?php foreach ($columns as $col) :
                if (!isset($sortable[$col])) : ?>
                  <th scope="col"><?php echo esc_html($col); ?></th>
                <?php else : ?>
                  <th scope="col" class="tit-th-sort" aria-sort="none"
                      data-col="<?php echo esc_attr($sortable[$col]); ?>">
                    <button type="button"><?php echo esc_html($col); ?><span
                      class="tit-th-arrow" aria-hidden="true"></span></button>
                  </th>
                <?php endif;
              endforeach; ?>
            </tr>
          </thead>
          <tbody id="tit-rows">
            <?php foreach ($rows as $r) : ?>
              <tr>
                <td class="tit-eyebrow" data-label="Employer"><?php
                  $ck = $r['company_key'] ?? '';
                  if ($ck && function_exists('tit_company_url')) {
                      printf('<a href="%s">%s</a>', esc_url(tit_company_url($ck)), esc_html($r['company']));
                  } else {
                      echo esc_html($r['company']);
                  }
                ?></td>
                <td class="tit-headline" data-label="What happened">
                  <span class="tit-h"><?php echo esc_html($r['headline']); ?></span>
                  <span class="tit-rt"><?php echo esc_html($r['talent_readthrough']); ?></span>
                </td>
                <td class="tit-meta" data-label="Where">
                  <?php
                  $place = $r['city'] ?: $r['hq_city'];
                  $cc    = $r['country'] ?: $r['hq_country'];
                  $is_hq = !$r['city'] && !$r['country'];
                  $where = trim(($place ? $place . ', ' : '') . tit_country_name($cc), ', ');
                  if ($where === '') {
                      // Stored anyway: geography is how we segment, not what
                      // makes the record true. Saying so beats a blank cell.
                      echo '<span class="tit-nowhere">Location not stated</span>';
                  } else {
                      echo esc_html($where);
                      if ($is_hq) echo ' <span class="tit-hq" title="Employer headquarters, not a location named in the source">HQ</span>';
                  }
                  ?>
                </td>
                <td class="tit-meta" data-label="What it means"><span class="tit-tag tit-<?php echo esc_attr($r['signal_direction']); ?>"><?php echo esc_html($directions[$r['signal_direction']] ?? $r['signal_direction']); ?></span></td>
                <td class="tit-meta" data-label="Evidence"><span class="tit-conf tit-c-<?php echo esc_attr($r['confidence']); ?>"><?php
                  echo esc_html($confidences[$r['confidence']] ?? $r['confidence']); ?></span></td>
                <td class="tit-meta tit-when" data-label="When"><?php
                  $when = $r['published_date'] ?: '';
                  echo $when ? esc_html(date_i18n('j M Y', strtotime($when)))
                             : '<span class="tit-nowhere">Date not stated</span>';
                ?></td>
                <td class="tit-meta" data-label="Source"><a href="<?php echo esc_url($r['source_url']); ?>" rel="nofollow noopener" target="_blank"><?php echo esc_html($r['source_name']); ?></a></td>
              </tr>
            <?php endforeach; ?>
          </tbody>
        </table>
      </div>

      <!--
        Download exactly what the filters show. The hrefs are server-rendered
        pointing at the whole dataset; dashboard.js rewrites them with the
        current querystring on every refresh, and the scope word flips between
        "all" and "filtered" so the link says which set it hands over.
      -->
      <div class="tit-export">
        <span class="tit-export-label">Download this view</span>
        <a class="tit-export-link" id="tit-export-csv"
           data-base="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_csv')); ?>"
           href="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_csv')); ?>">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/></svg>
          CSV<span class="tit-export-scope" id="tit-export-csv-scope"> · all</span></a>
        <a class="tit-export-link" id="tit-export-json"
           data-base="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_json')); ?>"
           href="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_json')); ?>">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/></svg>
          JSON<span class="tit-export-scope" id="tit-export-json-scope"> · all</span></a>
        <span class="tit-export-note">Every matching update, not just this page,
          and routine filings are included whichever way Show is set. Each row
          carries its own materiality, so you can set them aside yourself.
          Free to reuse, CC BY 4.0.</span>
      </div>

      <p class="tit-cite">
        Data licensed CC BY 4.0. Cite as: Talent Intelligence Tracker,
        asktherecruiter.com. Layoff and redundancy data is not collected here;
        see the
        <a href="/blog/ai-layoff-tracker/">AI Layoff Tracker</a>.
      </p>
    </div>
    <?php
    return ob_get_clean();
}
add_shortcode('talent_intelligence_dashboard', 'tit_dashboard_shortcode');

/**
 * The at-a-glance matrix: signals down the side, periods across the top.
 *
 * This replaced five period tiles that each printed the same sentence shape.
 * On a young dataset several periods hold the same records, so the strip said
 * almost the same thing five times and never answered "what kind, and is it
 * accelerating". Rows are what a recruiter or job seeker actually asks for.
 *
 * Row definitions use existing columns only, and they OVERLAP by design (a
 * funded employer can also be hiring), which the note under the matrix says
 * out loud so the columns are not read as sums:
 *   Hiring up          signal_direction = 'hiring'
 *   Funding raised     funding_amount present
 *   Leadership moves   pillar = 'leadership_change'
 *   Pay changes        signal_direction = 'comp_shift' — the direction column,
 *                      NOT pillar = 'rewards_comp': direction is a closed
 *                      vocabulary the pipeline always sets, and it also
 *                      catches a pay change filed under another pillar.
 *   All updates        every row in the period (the emphasised total row)
 *
 * One query, not twenty-five: conditional aggregation over the widest window,
 * one SUM per cell. Dates come from published_date, the date the source
 * carries, falling back to capture date — same rule as everywhere else.
 * Quarter start stays derived, never hardcoded.
 *
 * $where/$params let /aggregate reuse this under the caller's own filters, so
 * the matrix the server renders and the one JavaScript repaints on a filtered
 * page cannot describe the world differently.
 */
function tit_glance_matrix($table, $where = 'is_current = 1', array $params = array()) {
    global $wpdb;
    $today = current_time('Y-m-d');
    $q_month = (intdiv((int) date('n', strtotime($today)) - 1, 3) * 3) + 1;
    $periods = array(
        array('Today',        $today),
        array('This week',    date('Y-m-d', strtotime($today . ' -6 days'))),
        array('This month',   date('Y-m-01', strtotime($today))),
        array('This quarter', sprintf('%s-%02d-01', date('Y', strtotime($today)), $q_month)),
        // Spelled out, never "YTD".
        array(date('Y', strtotime($today)) . ' so far', date('Y-01-01', strtotime($today))),
    );

    // key, reader-facing label, the filter a cell click applies, SQL condition,
    // and what the cells hold: a COUNT of updates, or a SUM of dollars.
    //
    // "Money raised" is the one row that is not a count, which is exactly why
    // it is labelled, prefixed and coloured as money everywhere it appears. A
    // reader who mistakes a dollar sum for a number of updates has been misled
    // by the table, not by their own carelessness.
    $funding = function_exists('tit_funding_where')
        ? tit_funding_where()
        : "((funding_amount IS NOT NULL AND funding_amount <> '')"
          . " OR (funding_stage IS NOT NULL AND funding_stage <> ''))";

    $defs = array(
        array('hiring',     'Hiring up',        'direction=hiring',         "signal_direction = 'hiring'", 'count'),
        array('funded',     'Funding raised',   'funding=1',                $funding,                      'count'),
        array('money',      'Money raised',     'funding=1',                '',                            'money'),
        array('leadership', 'Leadership moves', 'pillar=leadership_change', "pillar = 'leadership_change'", 'count'),
        array('pay',        'Pay changes',      'direction=comp_shift',     "signal_direction = 'comp_shift'", 'count'),
        array('total',      'All updates',      '',                         '1 = 1',                       'count'),
    );

    $date_expr = 'COALESCE(published_date, DATE(captured_at))';
    $select = array();
    $select_params = array();
    foreach ($periods as $pi => $p) {
        foreach ($defs as $di => $d) {
            // A NULL funding_amount_usd is a figure we could not read as US
            // dollars, not a round of nothing, so SUM skips it rather than
            // treating it as zero. COALESCE only turns "no rows at all" into 0.
            $select[] = ($d[4] === 'money')
                ? "COALESCE(SUM(CASE WHEN {$date_expr} >= %s THEN funding_amount_usd END), 0) AS c_{$di}_{$pi}"
                : "SUM(({$d[3]}) AND {$date_expr} >= %s) AS c_{$di}_{$pi}";
            $select_params[] = $p[1];
        }
    }
    // SELECT placeholders precede WHERE placeholders in statement order, so
    // the select params go first.
    $sql = 'SELECT ' . implode(', ', $select) . " FROM {$table} WHERE {$where}";
    $row = $wpdb->get_row(
        $wpdb->prepare($sql, array_merge($select_params, $params)),
        ARRAY_A
    ) ?: array();

    $rows = array();
    foreach ($defs as $di => $d) {
        $cells = array();
        foreach ($periods as $pi => $p) {
            $cells[] = (int) ($row["c_{$di}_{$pi}"] ?? 0);
        }
        $rows[] = array('key' => $d[0], 'label' => $d[1], 'filter' => $d[2],
                        'kind' => $d[4], 'cells' => $cells);
    }

    return array(
        'periods' => array_column($periods, 0),
        'starts'  => array_column($periods, 1),
        'rows'    => $rows,
    );
}

/**
 * Render the matrix. dashboard.js mirrors this markup when it repaints from
 * /aggregate, the same contract renderRow() has with the table: the classes
 * and attributes here must match what the JS emits.
 *
 * Cells are buttons: a click applies the row's filter plus the column's
 * period, a second click clears them (wired in dashboard.js). Zero renders as
 * a muted 0, never blank — a real zero is information.
 */
function tit_glance_matrix_html(array $m) {
    ob_start(); ?>
    <div class="tit-matrix-scroll">
      <table class="tit-matrix">
        <thead>
          <tr>
            <th scope="col"><span class="tit-sr">Signal</span></th>
            <?php foreach ($m['periods'] as $p) : ?>
              <th scope="col"><?php echo esc_html($p); ?></th>
            <?php endforeach; ?>
          </tr>
        </thead>
        <tbody>
          <?php foreach ($m['rows'] as $r) :
            // Each signal keeps one hue across the whole product, and the cell
            // tint is scaled to the biggest number in ITS OWN row. Rows differ
            // by orders of magnitude (leadership moves ran 1,726 against 235
            // pay changes), so a table-wide scale would wash every row but one
            // out to nothing. Colour is a second read here, never the only
            // one: the number is always printed.
            $row_max = 0;
            foreach ($r['cells'] as $n) { $row_max = max($row_max, (int) $n); }
            $money = (($r['kind'] ?? 'count') === 'money');
            ?>
            <tr class="tit-matrix-row<?php echo $r['key'] === 'total' ? ' tit-matrix-total' : ''; ?><?php
                echo $money ? ' tit-matrix-money' : ''; ?>"
                data-signal="<?php echo esc_attr($r['key']); ?>">
              <th scope="row"><?php echo esc_html($r['label']); ?><?php
                if ($money) : ?><span class="tit-matrix-unit">sum of dollars</span><?php endif;
              ?></th>
              <?php foreach ($r['cells'] as $i => $n) :
                $n = (int) $n;
                // Square-rooted so a single dominant period does not flatten
                // every smaller one to invisible, and floored at 0.14 so any
                // real activity is still tinted.
                $intensity = ($row_max > 0 && $n > 0)
                    ? max(0.14, round(sqrt($n / $row_max), 3)) : 0;
                // Money reads as money in every part of the cell: the visible
                // figure is abbreviated with a $ in front, the title carries
                // the exact number, and the screen-reader label spells out the
                // unit rather than leaving "1.2" to be read as a count.
                $text  = $money ? tit_money_short($n) : number_format_i18n($n);
                $full  = $money ? tit_money_full($n)  : number_format_i18n($n);
                $spoken = $money
                    ? sprintf('%s, %s, %s in US dollars', $r['label'], $m['periods'][$i], $full)
                    : sprintf('%s, %s', $r['label'], $m['periods'][$i]);
                ?>
                <td><button type="button"
                    class="tit-cell<?php echo $n === 0 ? ' tit-cell-zero' : ''; ?><?php
                      echo $money ? ' tit-cell-money' : ''; ?>"
                    style="--i:<?php echo esc_attr($intensity); ?>"
                    data-filter="<?php echo esc_attr($r['filter']); ?>"
                    data-since="<?php echo esc_attr($m['starts'][$i]); ?>"
                    <?php if ($money) : ?>title="<?php echo esc_attr($full); ?>"<?php endif; ?>
                    aria-pressed="false"
                    aria-label="<?php echo esc_attr($spoken); ?>"><?php
                    echo esc_html($text);
                ?></button></td>
              <?php endforeach; ?>
            </tr>
          <?php endforeach; ?>
        </tbody>
      </table>
    </div>
    <div class="tit-matrix-note">
      <p>Colour shows how much activity, scaled within each row. Rows can
         overlap: a funded employer may also be hiring, so the columns do not
         add up. <strong>Click any number to filter the page.</strong></p>
      <p class="tit-matrix-money-note">Money raised is the exception. It sums
         dollars while every other row counts updates.
         <?php echo esc_html(tit_money_coverage_sentence($m['coverage'] ?? null)); ?></p>
    </div>
    <?php
    return ob_get_clean();
}

/* =========================================================================
   Money.

   funding_amount has always been a display string ("$1.45 Million"), which
   nothing could add up or sort. funding_amount_usd is the numeric companion:
   plain US dollars, and NULL rather than a converted guess when the source
   stated a figure in another currency. Everything below reads only the numeric
   column, and everything below says how much of the data it is based on.
   ========================================================================= */

/**
 * A dollar figure at a glance: $1.2B, $450M, $3.6M, $920K, $540.
 *
 * One decimal below 100 and none above, so $3.6M keeps the precision that
 * matters at that size while $450M does not gain a false one. A figure is
 * NEVER rounded into a different order of magnitude: $999M stays $999M, and
 * the promotion step exists only so a figure that genuinely rounds to a
 * thousand of its unit prints as $1B rather than $1,000M.
 *
 * The exact number always travels with it, in a title attribute (see
 * tit_money_full), because an abbreviation is a reading aid and not the fact.
 */
function tit_money_short($n) {
    $n = (float) $n;
    if ($n <= 0) return '$0';
    if ($n < 1000) return '$' . number_format_i18n(round($n));

    $units = array('K', 'M', 'B', 'T');
    $i = 0;
    $v = $n / 1000;
    while ($v >= 1000 && $i < count($units) - 1) { $v /= 1000; $i++; }
    $v = ($v < 100) ? round($v, 1) : round($v);
    if ($v >= 1000 && $i < count($units) - 1) { $v = round($v / 1000, 1); $i++; }

    // Trailing ".0" is noise: $27M, never $27.0M.
    $s = rtrim(rtrim(number_format((float) $v, 1, '.', ''), '0'), '.');
    return '$' . $s . $units[$i];
}

/** The exact figure, for the title attribute and for screen readers. */
function tit_money_full($n) {
    return '$' . number_format_i18n(round((float) $n));
}

/**
 * Summed US dollars under the caller's filters, plus the coverage figures the
 * page is required to print beside them.
 *
 * Coverage is the whole point. Only some funding updates carry a US dollar
 * figure: a round announced in euros is deliberately NULL rather than
 * converted at a rate nobody stated, and plenty of funding news names no
 * amount at all. A total presented as if it covered every round would be the
 * plausible-but-wrong number this product cannot carry, so N and M are
 * computed here, never hardcoded, and travel with every total.
 *
 * One query for the headline figures, three for the rankings. Place uses the
 * same COALESCE(location, headquarters) rule as the rest of the dashboard, so
 * the money charts and the activity charts put an employer in the same place.
 */
function tit_money_aggregate($table, $where = 'is_current = 1', array $params = array(), $limit = 40) {
    global $wpdb;
    $limit = max(1, min(200, (int) $limit));

    $funding = function_exists('tit_funding_where')
        ? tit_funding_where()
        : "((funding_amount IS NOT NULL AND funding_amount <> '')"
          . " OR (funding_stage IS NOT NULL AND funding_stage <> ''))";

    $country_expr  = 'COALESCE(country, hq_country)';
    $city_expr     = 'COALESCE(city, hq_city)';

    // "Placed" counts say how many of the summable rows each chart can
    // actually show, so a card can admit what it is leaving out instead of
    // quietly dropping rows off the bottom of a ranking.
    $head_sql = "SELECT COALESCE(SUM(funding_amount_usd), 0) AS total,
                        SUM(funding_amount_usd IS NOT NULL) AS with_usd,
                        SUM({$funding}) AS funding_rows,
                        SUM(funding_amount_usd IS NOT NULL
                            AND {$country_expr} IS NOT NULL AND {$country_expr} <> '') AS placed_country,
                        SUM(funding_amount_usd IS NOT NULL
                            AND {$city_expr} IS NOT NULL AND {$city_expr} <> '') AS placed_city,
                        SUM(funding_amount_usd IS NOT NULL
                            AND industry IS NOT NULL AND industry <> '') AS placed_industry
                   FROM {$table} WHERE {$where}";
    $head = $wpdb->get_row($params ? $wpdb->prepare($head_sql, $params) : $head_sql, ARRAY_A) ?: array();

    $with = (int) ($head['with_usd'] ?? 0);
    // A row can carry a dollar figure without matching the funding test if the
    // pipeline ever fills one column and not the other. The denominator must
    // never be smaller than the numerator, or the sentence reads "53 of 40".
    $all  = max($with, (int) ($head['funding_rows'] ?? 0));

    $by = function ($expr) use ($wpdb, $table, $where, $params, $limit) {
        $sql = "SELECT {$expr} AS k, SUM(funding_amount_usd) AS v, COUNT(*) AS n
                  FROM {$table}
                 WHERE {$where} AND funding_amount_usd IS NOT NULL
                   AND {$expr} IS NOT NULL AND {$expr} <> ''
                 GROUP BY k ORDER BY v DESC LIMIT {$limit}";
        $rows = $wpdb->get_results($params ? $wpdb->prepare($sql, $params) : $sql, ARRAY_A) ?: array();
        return array_map(function ($r) {
            return array('k' => $r['k'], 'v' => (float) $r['v'], 'n' => (int) $r['n']);
        }, $rows);
    };

    return array(
        'total'    => (float) ($head['total'] ?? 0),
        'coverage' => array('with' => $with, 'all' => $all),
        'placed'   => array(
            'country'  => (int) ($head['placed_country'] ?? 0),
            'city'     => (int) ($head['placed_city'] ?? 0),
            'industry' => (int) ($head['placed_industry'] ?? 0),
        ),
        'by_country'  => $by($country_expr),
        'by_city'     => $by($city_expr),
        'by_industry' => $by('industry'),
    );
}

/**
 * "Totals cover the 53 of 56 funding updates that state an amount in US
 * dollars." Computed, never written down, and printed beside every money view.
 */
function tit_money_coverage_sentence($coverage) {
    if (!is_array($coverage)) return '';
    $with = (int) ($coverage['with'] ?? 0);
    $all  = (int) ($coverage['all'] ?? 0);
    if ($all === 0) {
        return 'No funding updates in this view yet, so there is nothing to add up.';
    }
    // "the 3,992 of 3,992" reads as a mistake. When coverage is complete, say
    // so plainly; keep the two numbers only when they actually differ, which is
    // the case the sentence exists for.
    $lead = ($with >= $all)
        ? sprintf(
            _n('All %s funding update states a US dollar amount',
               'All %s funding updates state a US dollar amount', $all, 'tit'),
            number_format_i18n($all))
        : sprintf(
            _n('Totals cover the %1$s of %2$s funding update that states a US dollar amount',
               'Totals cover the %1$s of %2$s funding updates that state a US dollar amount',
               $all, 'tit'),
            number_format_i18n($with), number_format_i18n($all));

    return $lead . '; amounts in other currencies are left out rather than'
         . ' converted at a rate nobody published.';
}

/** The same sentence, plus what this particular chart cannot place. */
function tit_money_coverage_note(array $money, $dimension = '') {
    $note = tit_money_coverage_sentence($money['coverage'] ?? null);
    $with = (int) ($money['coverage']['with'] ?? 0);
    $placed = (int) ($money['placed'][$dimension] ?? $with);
    $missing = $with - $placed;
    if ($dimension === '' || $missing <= 0) return $note;

    $names = array('country' => 'no country', 'city' => 'no city', 'industry' => 'no industry');
    return $note . ' ' . sprintf(
        _n('%1$s of those names %2$s, so it is not on this chart.',
           '%1$s of those name %2$s, so they are not on this chart.',
           $missing, 'tit'),
        number_format_i18n($missing), $names[$dimension] ?? 'no category'
    );
}

/**
 * What the detail control is showing and what it is setting aside, in numbers.
 *
 * Both counts, always, and what "routine" means, always. The sentence is
 * computed from the current view, so it stays true under every filter, and it
 * is the reason the default is allowed to hold rows back at all: a reader can
 * see exactly how many and decide for themselves in one click.
 *
 * dashboard.js mirrors this wording when a filter changes.
 */
function tit_detail_note($mode, $notable, $routine) {
    $notable = (int) $notable;
    $routine = (int) $routine;
    $meaning = ' Routine means a bare officer or director change with no'
             . ' headcount, no money and no location named.';

    if ($routine === 0) {
        // Also the state before the pipeline has judged anything, so it must
        // not imply a filter is doing work it is not.
        return sprintf(
            _n('Showing all %s update. Nothing in this view is a routine filing.',
               'Showing all %s updates. Nothing in this view is a routine filing.',
               $notable, 'tit'),
            number_format_i18n($notable)
        ) . $meaning;
    }

    if ($mode === 'all') {
        return sprintf(
            'Showing all %s updates, routine filings included. %s of them are routine.',
            number_format_i18n($notable + $routine), number_format_i18n($routine)
        ) . $meaning;
    }

    return sprintf(
        'Showing %s notable updates. %s routine filings join them when you switch to Everything.',
        number_format_i18n($notable), number_format_i18n($routine)
    ) . $meaning;
}

/**
 * Amount-raised bands, keyed by the dollar floor they send to /query.
 *
 * Bands rather than a number box. Someone looking for funded employers thinks
 * "bigger than about ten million", not "at least 12,400,000", and a typed
 * threshold is the fastest way to produce a page that looks empty because of
 * one digit. Four orders of magnitude cover the whole range we hold.
 */
function tit_funding_bands() {
    return array(
        '1000000'    => 'Over $1M',
        '10000000'   => 'Over $10M',
        '100000000'  => 'Over $100M',
        '1000000000' => 'Over $1B',
    );
}

/**
 * What a record is based on, in words a reader already owns.
 *
 * "verified", "reported" and "rumored" are the stored values and they are our
 * vocabulary, not anyone else's: "verified" sounds like a badge rather than a
 * statement about the SOURCE. These say what the source actually is.
 */
function tit_confidence_labels() {
    return array(
        'verified' => 'Official filing',
        'reported' => 'News report',
        'rumored'  => 'Unconfirmed',
    );
}

/** Round names as a reader would say them, matching the pipeline's vocabulary. */
function tit_funding_stage_labels() {
    return array(
        'pre_seed' => 'Pre-seed', 'seed' => 'Seed',
        'series_a' => 'Series A', 'series_b' => 'Series B',
        'series_c' => 'Series C', 'series_d_plus' => 'Series D or later',
        'growth' => 'Growth', 'debt' => 'Debt', 'grant' => 'Grant',
        'ipo' => 'IPO', 'other' => 'Other',
    );
}

/**
 * The primary control, phrased as what someone came here to find.
 *
 * Keys are parameter specs, not column values, because the things people
 * actually look for do not all live in one column: "Hiring" is a direction,
 * "Funding" is a funding update, and the rest are pillars. Making the reader
 * work out which is which was the whole problem with the old twelve-control bar.
 *
 * Every pillar and both headcount directions appear, so nothing reachable
 * through the old What happened and Headcount dropdowns is shut off by folding
 * them into one control.
 */
function tit_looking_options() {
    return array(
        ''                           => 'Anything',
        'direction=hiring'           => 'Hiring',
        'funding=1'                  => 'Funding',
        'pillar=leadership_change'   => 'Leadership changes',
        'pillar=rewards_comp'        => 'Pay news',
        'pillar=how_we_work'         => 'Ways of working',
    );
}

/**
 * What the Headcount filter OFFERS, which is not the same list as what a row
 * can BE.
 *
 * comp_shift is a real stored value and rows wearing it still say "Pay change",
 * but it is not a headcount direction and it duplicates a filter that already
 * exists on the right axis. "Not stated" is shortened from the badge's
 * "Headcount not stated" because the control above it already says Headcount.
 */
function tit_direction_filter_options() {
    return array(
        'hiring'  => 'Hiring up',
        'neutral' => 'Not stated',
    );
}

/**
 * Why "Cutting back" is no longer offered.
 *
 * Measured live: hiring 4,018, comp_shift 9,217, neutral 3,361, displacement
 * SEVEN, out of 16,603. An option returning 0.04% is not a filter, it is a way
 * to make the page look broken, and this tracker's own footer says layoffs are
 * collected separately. The stored value is untouched and those rows still
 * appear in the table, because deleting accurate records to tidy a dropdown
 * would be the dishonest fix. /query still accepts direction=displacement, so
 * an existing link keeps working.
 *
 * Anyone looking for cuts is sent to the sibling tracker, which is the product
 * that actually collects them.
 */

/** Reader-facing industry names, in one place so the page and the money charts agree. */
function tit_industry_labels() {
    return array(
        'technology' => 'Technology', 'financial_services' => 'Financial services',
        'healthcare' => 'Healthcare', 'pharma_biotech' => 'Pharma & biotech',
        'retail_ecommerce' => 'Retail & e-commerce', 'manufacturing' => 'Manufacturing',
        'energy_utilities' => 'Energy & utilities', 'telecom' => 'Telecom',
        'media_entertainment' => 'Media & entertainment',
        'transport_logistics' => 'Transport & logistics',
        'professional_services' => 'Professional services',
        'public_sector' => 'Public sector', 'hospitality_travel' => 'Hospitality & travel',
        'education' => 'Education', 'food_beverage' => 'Food & beverage',
        'automotive' => 'Automotive', 'aerospace_defence' => 'Aerospace & defence',
        'real_estate_construction' => 'Real estate & construction',
    );
}

/**
 * One money card, built on the same chart-card parts as every other card:
 * tit_chart_head (heading, share, CSV download, expand), a .tit-rank list of
 * bar rows, and rows that are real buttons so a click filters the whole page.
 *
 * The only differences are the ones that have to exist: the bar carries a
 * summed dollar figure rather than a count, the figure is abbreviated with the
 * exact number on the title attribute, and the card prints what its totals are
 * based on. dashboard.js mirrors this markup when it repaints from /aggregate.
 */
function tit_money_chart($id, $title, $sub, array $rows, array $money, $dimension, callable $labeller) {
    $max = 0.0;
    foreach ($rows as $r) { $max = max($max, (float) $r['v']); }
    ?>
    <div class="tit-chart tit-chart-money" id="chart-money-<?php echo esc_attr($id); ?>">
      <?php tit_chart_head($title, $sub, 'money-' . $id); ?>
      <div class="tit-rank" tabindex="0" role="group" aria-label="<?php echo esc_attr($title); ?>">
        <?php if (!$rows) : ?>
          <p class="tit-rank-empty">No US dollar amounts in this view yet.</p>
        <?php else : foreach ($rows as $r) :
          $v = (float) $r['v']; ?>
          <button type="button" class="tit-rank-row" data-k="<?php echo esc_attr($r['k']); ?>"
                  <?php /* number_format, not a float cast: PHP renders a large
                           float in scientific notation, and "1.0E+9" in a CSV
                           column is worse than no column. */ ?>
                  data-v="<?php echo esc_attr(number_format($v, 0, '.', '')); ?>" aria-pressed="false">
            <span class="tit-rank-name"><?php echo esc_html($labeller($r['k'])); ?></span>
            <span class="tit-rank-track"><span class="tit-rank-fill"
              style="width:<?php echo esc_attr($max > 0 ? max(4, round(100 * $v / $max)) : 4); ?>%"></span></span>
            <span class="tit-rank-n" title="<?php echo esc_attr(tit_money_full($v)); ?>"><?php
              echo esc_html(tit_money_short($v)); ?></span>
          </button>
        <?php endforeach; endif; ?>
      </div>
      <p class="tit-money-note"><?php echo esc_html(tit_money_coverage_note($money, $dimension)); ?></p>
    </div>
    <?php
}

/**
 * Absolute time in the SITE's configured timezone with its abbreviation:
 * "Jul 28, 1:51 PM EDT". The sibling tracker leads with exactly this shape.
 * wp_date() renders in wp_timezone(), so a change under Settings > General
 * moves every timestamp with it; never a hardcoded offset. Accepts a UTC
 * MySQL datetime or a Unix timestamp.
 */
function tit_local_datetime($utc) {
    $ts = is_numeric($utc) ? (int) $utc : strtotime($utc . ' UTC');
    if (!$ts) return '';
    // A site whose timezone is set to plain UTC renders 'T' as "GMT+0000",
    // which reads like a machine header, not a time a person wrote. Same
    // instant, human label. Named zones (EDT, CET) pass through untouched.
    return str_replace('GMT+0000', 'UTC', wp_date('M j, g:i A T', $ts));
}

/**
 * When the next collection run is due.
 *
 * "Resting until the next run" invites the obvious question and then does not
 * answer it. The schedule is a cron in collect.yml, so the times are fixed and
 * known: 06:00 and 18:00 UTC. Printed in the site's own timezone, because a
 * reader should not have to convert.
 */
function tit_next_run() {
    $now = time();
    foreach (array(0, 1) as $day) {
        foreach (array(6, 18) as $hour) {
            $t = strtotime(gmdate('Y-m-d', $now + $day * DAY_IN_SECONDS)
                           . sprintf(' %02d:00:00 UTC', $hour));
            if ($t > $now) return $t;
        }
    }
    return 0;
}

/**
 * How much history actually sits behind those tiles, in words.
 *
 * Printed under the glance block because the tiles alone cannot say it: "46
 * updates" reads like a running total from a tracker that has been going a
 * while, and right now it is eight days of collection.
 */
function tit_span_note($lo, $hi) {
    if (!$lo || !$hi) return '';

    /*
      This line said "Everything here spans 3,318 days, 28 Jun to 28 Jul 2026",
      which is nine years of days against thirty days of dates, and a page whose
      own date range does not add up undermines every number beside it.

      Both halves came from the same query and the same two values all along.
      What broke was the FORMAT: the low bound printed as 'j M' with no year.
      That read correctly while everything we held sat inside one year, and
      turned into a contradiction the moment the UK pay-gap rows reached back to
      2017 and the Form D sweep began adding 2009 onward. A format that is only
      correct for a range you happen to hold today is a bug with a fuse on it.

      Now: both bounds carry their year, and the day count is gone. It was
      there to stop "46 updates" reading like a long-running tracker, and that
      job is done by the real dates the moment they span years.
    */
    if (substr($lo, 0, 10) === substr($hi, 0, 10)) {
        return sprintf('Covering %s.', date_i18n('j M Y', strtotime($hi)));
    }
    return sprintf(
        /* translators: 1: earliest date, 2: latest date, both with the year */
        'Covering %1$s to %2$s.',
        date_i18n('j M Y', strtotime($lo)),
        date_i18n('j M Y', strtotime($hi))
    );
}

/**
 * Roo, the AskTheRecruiter robot, ported from the sibling tracker.
 *
 * He reports the state of COLLECTION, not the state of the page. Wiring him to
 * the dashboard's own fetches would have been easier and would have made him
 * animate far more often, but it would also have meant a mascot that looks busy
 * because someone changed a dropdown. He works when a run has just landed and
 * sleeps the rest of the time, which is the truth and is usually "asleep".
 *
 * State is decided server-side from the newest capture, so the first paint is
 * already right; no request, and nothing to get wrong before JavaScript runs.
 */
function tit_roo($newest_run) {
    $ts     = $newest_run ? strtotime($newest_run . ' UTC') : 0;
    $ago    = $ts ? (time() - $ts) : null;
    // A run writes over a few minutes, so anything inside the last quarter hour
    // is treated as still in progress rather than finished.
    $working = ($ago !== null && $ago >= 0 && $ago < 900);
    $state   = $working ? 'tit-roo-working' : 'tit-roo-sleeping';

    $next = tit_next_run();

    // The absolute timestamp with timezone leads up in the Live pill; down
    // here the relative time is the secondary fact and the next-run note is
    // the quietest part of the line (its own span, styled smaller). Next-run
    // times go through tit_local_datetime() too: the site's timezone, never a
    // hand-converted hour.
    if ($working) {
        $say = 'Roo is pulling in new filings and news';
    } elseif ($ago !== null) {
        $say = sprintf('Roo pulled the latest data %s ago.', human_time_diff($ts, time()));
    } else {
        $say = 'Roo is resting until the next run.';
    }
    $next_note = $next
        ? sprintf('Next run %s, in %s.', tit_local_datetime($next), human_time_diff(time(), $next))
        : '';
    ?>
    <span class="tit-roo-wrap<?php echo $working ? ' is-working' : ' is-sleeping'; ?>" aria-hidden="true">
      <span class="tit-zzz"><i>z</i><i>z</i><i>z</i></span>
      <svg class="tit-roo <?php echo esc_attr($state); ?>" width="52" height="56" viewBox="0 0 140 150">
        <line x1="70" y1="14" x2="70" y2="26" stroke="var(--roo-deep)" stroke-width="3" stroke-linecap="round"/>
        <circle class="roo-bulb" cx="70" cy="10" r="5" fill="var(--roo-accent)"/>
        <rect x="26" y="24" width="88" height="40" rx="20" fill="var(--roo-surface)" stroke="var(--roo-deep)" stroke-width="3.5"/>
        <g class="roo-eyes">
          <circle cx="51" cy="44" r="12" fill="var(--roo-soft)" stroke="var(--roo-deep)" stroke-width="2.5"/>
          <g class="roo-pupil"><circle cx="53.5" cy="44" r="5.5" fill="var(--roo-deep)"/><circle cx="55.5" cy="42" r="1.8" fill="var(--roo-surface)"/></g>
          <circle cx="89" cy="44" r="12" fill="var(--roo-soft)" stroke="var(--roo-deep)" stroke-width="2.5"/>
          <g class="roo-pupil"><circle cx="86.5" cy="44" r="5.5" fill="var(--roo-deep)"/><circle cx="88.5" cy="42" r="1.8" fill="var(--roo-surface)"/></g>
        </g>
        <path d="M 59 57 Q 70 61 81 57" fill="none" stroke="var(--roo-deep)" stroke-width="2.5" stroke-linecap="round"/>
        <g class="roo-body-group">
          <rect x="64" y="64" width="12" height="8" fill="var(--roo-deep)" rx="2"/>
          <rect class="roo-arm-l" x="18" y="82" width="14" height="28" rx="7" fill="var(--roo-surface)" stroke="var(--roo-deep)" stroke-width="3"/>
          <rect class="roo-arm-r" x="108" y="82" width="14" height="28" rx="7" fill="var(--roo-surface)" stroke="var(--roo-deep)" stroke-width="3"/>
          <rect x="36" y="72" width="68" height="46" rx="10" fill="var(--roo-surface)" stroke="var(--roo-deep)" stroke-width="3.5"/>
          <rect x="48" y="80" width="44" height="30" rx="5" fill="var(--roo-tint)" stroke="var(--roo-deep)" stroke-width="2"/>
          <rect class="roo-line" x="54" y="86" height="3.5" width="26" rx="1.75" fill="var(--roo-deep)"/>
          <rect class="roo-line" x="54" y="93" height="3.5" width="18" rx="1.75" fill="var(--roo-deep)"/>
          <rect class="roo-line" x="54" y="100" height="3.5" width="22" rx="1.75" fill="var(--roo-deep)"/>
        </g>
        <rect x="30" y="122" width="80" height="14" rx="7" fill="var(--roo-soft)" stroke="var(--roo-deep)" stroke-width="2.5"/>
        <circle class="roo-tread-dot" cx="42" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
        <circle class="roo-tread-dot" cx="56" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
        <circle class="roo-tread-dot" cx="70" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
        <circle class="roo-tread-dot" cx="84" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
        <circle class="roo-tread-dot" cx="98" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
      </svg>
    </span>
    <span class="tit-roo-say"><?php echo esc_html($say); ?><?php
      if ($next_note) : ?> <span class="tit-roo-next"><?php echo esc_html($next_note); ?></span><?php endif;
    ?></span>
    <?php
}

/**
 * The heading block every chart card shares, plus its expand control.
 *
 * The button ships `hidden` and dashboard.js reveals it. A control that only
 * works with JavaScript should not be visible without it, and rendering it
 * server-side (rather than injecting it) keeps its label and markup in one
 * place with the heading it belongs to.
 */
function tit_chart_head($title, $sub, $id = '') {
    ?>
    <div class="tit-chart-head">
      <div class="tit-chart-titles">
        <h3><?php echo esc_html($title); ?></h3>
        <p class="tit-sub"><?php echo esc_html($sub); ?></p>
      </div>
      <div class="tit-chart-tools">
        <button type="button" class="tit-ctl tit-chart-share" data-chart="<?php echo esc_attr($id); ?>"
                title="Copy a link to this view" aria-label="Copy a link to this view" hidden>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 10.7l6.8-4M8.6 13.3l6.8 4"/></svg>
        </button>
        <button type="button" class="tit-ctl tit-chart-dl" data-chart="<?php echo esc_attr($id); ?>"
                title="Download this chart as CSV" aria-label="Download this chart as CSV" hidden>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/></svg>
        </button>
        <button type="button" class="tit-ctl tit-expand" aria-expanded="false"
                title="Expand this chart" aria-label="Expand this chart" hidden>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7"/></svg>
          <span class="tit-sr tit-expand-t">Expand</span>
        </button>
      </div>
    </div>
    <?php
}

/**
 * The region strip above the charts.
 *
 * Regions are groups of country codes, not a separate dimension in the data —
 * "Europe" is a shorthand for a list, and the tab sends that list to the API.
 *
 * A region with nothing in it is dropped rather than shown at zero. An empty
 * tab reads as a filter that broke, and a strip of them reads as coverage we
 * do not have. Worldwide always survives so there is always a way back.
 */
function tit_regions(array $counts) {
    // Same regions and the same names as the sibling tracker, so one brand does
    // not describe the world two different ways. India keeps its own tab, which
    // the sibling has no need of: it is one of the largest hiring markets we
    // read, and burying it inside Asia would hide the thing people came for.
    //
    // Every list is the WHOLE region, never a shortlist of the big names. A
    // Latvian employer once fell outside 'Europe' because LV was missing, and a
    // Namibian one outside 'Africa' because NA was, which is a filter quietly
    // lying about its own name. A code costs nothing to carry and a missing one
    // costs a record.
    $defs = array(
        array('World',         '🌐', ''),
        array('USA',           '🇺🇸', 'US'),
        array('Canada',        '🇨🇦', 'CA'),
        array('Latin America', '🌎', 'BR,MX,AR,CL,CO,PE,UY,CR,EC,BO,PY,VE,GT,HN,SV,NI,PA,DO,CU,JM,TT,HT,BZ,GY,SR'),
        array('Europe',        '🇪🇺', 'GB,IE,DE,FR,NL,ES,IT,SE,PL,CH,BE,DK,NO,FI,AT,PT,'
                                     . 'CZ,GR,RO,HU,LV,LT,EE,SK,SI,HR,BG,RS,UA,IS,LU,'
                                     . 'MT,CY,AL,BA,ME,MK,MD,BY,MC,LI,AD,SM,VA,XK,RU,GE,AM,AZ'),
        array('UK',            '🇬🇧', 'GB'),
        array('Middle East',   '🕌', 'AE,SA,IL,QA,KW,BH,OM,TR,JO,LB,IQ,IR,SY,YE,PS'),
        array('Africa',        '🌍', 'ZA,NG,KE,EG,MA,GH,ET,NA,TZ,UG,ZM,ZW,BW,MZ,AO,SN,CI,CM,'
                                     . 'DZ,TN,LY,SD,RW,MW,MU,MG,CD,CG,GA,BJ,BF,ML,NE,TD,SO,SL,LR,GM,SS'),
        array('India',         '🇮🇳', 'IN'),
        array('Asia',          '🌏', 'IN,SG,JP,CN,HK,KR,MY,PH,ID,TH,VN,TW,PK,BD,LK,NP,MM,KH,LA,MN,MO,BN,MV,KZ,UZ'),
        array('Australia',     '🇦🇺', 'AU,NZ,FJ,PG'),
    );
    $total = array_sum(array_map('intval', $counts));

    $out = array();
    foreach ($defs as [$name, $flag, $codes]) {
        if ($codes === '') {
            $out[] = compact('name', 'flag', 'codes') + array('n' => $total);
            continue;
        }
        $n = 0;
        foreach (explode(',', $codes) as $c) {
            $n += (int) ($counts[$c] ?? 0);
        }
        if ($n > 0) {
            $out[] = compact('name', 'flag', 'codes') + array('n' => $n);
        }
    }
    return $out;
}

/**
 * Enqueue the dashboard assets. Idempotent: WordPress ignores a second
 * enqueue of the same handle, so both the wp_enqueue_scripts hook and the
 * shortcode itself can call this safely.
 */
function tit_enqueue_dashboard_assets() {
    // Version is TIT_VERSION plus the file's own mtime, the same shape the
    // sibling plugin uses. TIT_VERSION alone is not enough: an FTP deploy can
    // change the stylesheet without the constant moving (a CSS-only fix), and
    // this site runs Autoptimize, which caches a rewritten copy of the file
    // keyed on that version string. Without the mtime the visitor keeps the
    // old rewritten copy and the deploy appears not to have landed.
    wp_enqueue_style('tit-dashboard', TIT_URL . 'assets/dashboard.css', array(),
        tit_asset_version('assets/dashboard.css'));
    wp_enqueue_script('tit-dashboard', TIT_URL . 'assets/dashboard.js', array(),
        tit_asset_version('assets/dashboard.js'), true);
    wp_localize_script('tit-dashboard', 'TIT', array(
        'api' => esc_url_raw(rest_url('talent/v1/')),
        // The filtered rows are rendered in the browser, so it needs the same
        // country names the server used. Two copies of this list would drift.
        'countries' => tit_country_names(),
    ));
}

function tit_enqueue_assets() {
    // Our own routed pages (company profiles, sources) carry no shortcode and
    // are not singular posts, so a shortcode-only check leaves them completely
    // unstyled. Ask each route, rather than naming them one at a time — that
    // omission is exactly how the sources page shipped unstyled.
    $is_plugin_route = (bool) get_query_var('tit_company')
                    || (bool) get_query_var('tit_sources');

    if (!$is_plugin_route) {
        if (!is_singular()) return;
        global $post;
        if (!$post || !has_shortcode($post->post_content, 'talent_intelligence_dashboard')) return;
    }

    tit_enqueue_dashboard_assets();
}
add_action('wp_enqueue_scripts', 'tit_enqueue_assets');

/**
 * Keep Autoptimize's hands off this plugin's assets.
 *
 * The live site runs Autoptimize with CSS aggregation on: it strips every
 * <link rel="stylesheet"> and inlines the combined result. Our dashboard.css
 * was enqueued correctly and still never reached the page - the aggregated
 * bundle simply did not contain a single .tit- rule, so the dashboard rendered
 * as raw HTML with every class inert (observed live 2026-07-28).
 *
 * The sibling plugin carries the same two filters for the same reason. Ours
 * were missing, which is why "the CSS exists and is enqueued" and "the page has
 * no styles" were both true at once.
 */
function tit_autoptimize_exclude_css($exclude) {
    return $exclude . ', talent-intelligence-tracker/assets';
}
add_filter('autoptimize_filter_css_exclude', 'tit_autoptimize_exclude_css');

function tit_autoptimize_exclude_js($exclude) {
    // Two entries, not one. The path keeps our file out of the bundle; the
    // `var TIT` string keeps the inline object wp_localize_script prints out of
    // it too. Excluding only the path split the pair apart and left the file
    // running before the object it reads.
    return $exclude . ', talent-intelligence-tracker/assets, var TIT';
}
add_filter('autoptimize_filter_js_exclude', 'tit_autoptimize_exclude_js');
