/* Filters talk to talent/v1/query. The table is already server-rendered, so
   this only ever replaces rows that are already there. If the API is
   unreachable the page keeps working with what the server sent. */

(function () {
  'use strict';

  var root = document.getElementById('tit-dashboard');
  if (!root) return;

  // Prefer the global that wp_localize_script prints, but never depend on it.
  // Autoptimize sweeps inline scripts into an aggregated bundle while leaving
  // this file where it is (it is excluded by path), so the global can be
  // undefined at the moment this runs and defined by the time anything checks.
  // Bailing on that read is what made every control on the live page inert.
  var TIT = window.TIT;
  if (!TIT || !TIT.api) {
    TIT = { api: root.getAttribute('data-api') || '', countries: {} };
    try {
      TIT.countries = JSON.parse(root.getAttribute('data-countries') || '{}');
    } catch (e) { /* names degrade to the raw country codes */ }
  }
  if (!TIT.api) return;

  var tbody = document.getElementById('tit-rows');
  // Keys are the API's query parameter names, so refresh() can build the
  // querystring straight from this map without a translation layer.
  var inputs = {
    pillar: document.getElementById('tit-f-pillar'),
    direction: document.getElementById('tit-f-direction'),
    'function': document.getElementById('tit-f-function'),
    industry: document.getElementById('tit-f-industry'),
    country: document.getElementById('tit-f-country'),
    state: document.getElementById('tit-f-state'),
    city: document.getElementById('tit-f-city'),
    confidence: document.getElementById('tit-f-confidence'),
    min_funding_usd: document.getElementById('tit-f-min_funding_usd'),
    funding_stage: document.getElementById('tit-f-funding_stage'),
    country_basis: document.getElementById('tit-f-country_basis'),
    company: document.getElementById('tit-f-company'),
    q: document.getElementById('tit-f-q'),
    since: document.getElementById('tit-f-since'),
    until: document.getElementById('tit-f-until'),
    detail: document.getElementById('tit-f-detail'),
    sort: document.getElementById('tit-f-sort')
  };

  // Values that narrow nothing. They are never sent, never become a chip, and
  // never make the page count as filtered. Without this, country_basis="any"
  // and sort="newest" would each show as an active filter on a page nobody had
  // touched, which is the fastest way to make a filter bar mean nothing.
  // `detail` is here even though it is not neutral in the API's sense: it does
  // narrow the page. It gets no chip because the detail bar above the table
  // states it far more loudly than a chip could, with both counts and our
  // definition of routine printed in full. A chip would be a quieter version of
  // something already impossible to miss.
  var NEUTRAL = { sort: 'notable', country_basis: 'any', detail: 'notable' };

  // How each filter names itself in the active-filter bar.
  // These name the filter in the active-filter bar, and they mirror the labels
  // above the controls themselves. A chip reading "Roles" over a control
  // reading "Team or function" is two names for one thing.
  var FILTER_LABEL = {
    pillar: 'What happened', direction: 'Headcount', 'function': 'Team',
    industry: 'Industry', country: 'Where', state: 'Where', city: 'Where',
    stated_headcount: 'Headcount',
    confidence: 'Evidence', country_basis: 'Location shown', company: 'Employer',
    min_funding_usd: 'Amount raised', funding_stage: 'Stage',
    q: 'Search', since: 'From', until: 'To', region: 'Region', quickview: 'View'
  };

  // Saved views that no longer have a button of their own but can still be
  // switched on (by a matrix cell, or by a shared link).
  var QV_LABEL = { 'funding=1': 'Funding updates' };

  // Round names as a reader says them. Mirrors tit_funding_stage_labels().
  var STAGE_LABEL = {
    pre_seed: 'Pre-seed', seed: 'Seed', series_a: 'Series A',
    series_b: 'Series B', series_c: 'Series C',
    series_d_plus: 'Series D or later', growth: 'Growth', debt: 'Debt',
    grant: 'Grant', ipo: 'IPO', other: 'Other'
  };

  // What a record is BASED ON, said plainly. The stored values (verified,
  // reported, rumored) are our vocabulary: "verified" reads as a badge we
  // awarded rather than a statement about the source.
  var CONFIDENCE_LABEL = {
    verified: 'Official filing',
    reported: 'News report',
    rumored: 'Unconfirmed'
  };

  var DIRECTION_CLASS = {
    hiring: 'tit-hiring',
    displacement: 'tit-displacement',
    comp_shift: 'tit-comp_shift'
  };

  // Recruiter language. Colour never carries the meaning on its own, so the
  // words have to be right.
  var DIRECTION_LABEL = {
    hiring: 'Hiring up',
    displacement: 'Cutting back',
    comp_shift: 'Pay change',
    // Matches the PHP label. This bucket is "the source says nothing about
    // headcount" (a funding round with no hiring plan, a CEO succession), not
    // a vague other-category, and naming it that way is truer to the rule that
    // we never infer a direction the source did not state.
    neutral: 'Headcount not stated'
  };

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function populateFacets() {
    fetch(TIT.api + 'facets')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        // Only geography is data-driven; roles and industries are closed
        // vocabularies and are already rendered server-side.
        // Countries are listed by name and sorted by name. /facets returns ISO
        // codes, and a dropdown reading "AE, AR, AT" asks the reader to know
        // the codebook before they can pick a country.
        fill(inputs.country, data.countries, true);
        fill(inputs.state, data.states);
        fill(inputs.city, data.cities);
        // Only the rounds we actually hold, so the control cannot offer a
        // stage that returns an empty page.
        fill(inputs.funding_stage, data.funding_stages, false, STAGE_LABEL);
        fillPlaces(data);
      })
      .catch(function () { /* filters degrade to what the server rendered */ });
  }

  function fill(select, values, asCountries, labels) {
    if (!select || !values) return;
    var items = values.map(function (v) {
      return { value: v, label: (asCountries && TIT.countries[v]) || (labels && labels[v]) || v };
    });
    if (asCountries) {
      items.sort(function (a, b) { return a.label.localeCompare(b.label); });
    }
    items.forEach(function (item) {
      // Never a second copy of a value the select already holds: a shared link
      // is restored before /facets answers, so the value it carried has
      // already been added by hand (see applyUrlState).
      var seen = Array.prototype.some.call(select.options, function (o) {
        return o.value === item.value;
      });
      if (seen) return;
      var opt = document.createElement('option');
      opt.value = item.value;
      opt.textContent = item.label;
      select.appendChild(opt);
    });
  }

  // One "Where" control over three columns, grouped so a reader picks a place
  // and never a column. Three separate geography dropdowns was the clearest
  // symptom of the schema showing through the paint.
  function fillPlaces(data) {
    var sel = document.getElementById('tit-f-place');
    if (!sel || !data) return;
    var groups = [
      ['Countries', 'country', (data.countries || []).map(function (v) {
        return { v: v, l: (TIT.countries && TIT.countries[v]) || v };
      }).sort(function (a, b) { return a.l.localeCompare(b.l); })],
      ['US states', 'state', (data.states || []).map(function (v) { return { v: v, l: v }; })],
      ['Cities', 'city', (data.cities || []).map(function (v) { return { v: v, l: v }; })]
    ];
    groups.forEach(function (g) {
      var box = document.createElement('optgroup');
      box.label = g[0];
      g[2].forEach(function (item) {
        var value = g[1] + ':' + item.v;
        var seen = Array.prototype.some.call(sel.options, function (o) { return o.value === value; });
        if (seen) return;
        var opt = document.createElement('option');
        opt.value = value;
        opt.textContent = item.l;
        box.appendChild(opt);
      });
      if (box.children.length) sel.appendChild(box);
    });
    syncPlace();
  }

  function renderRow(r) {
    // Fall back to headquarters when the source named no place, and say so.
    var isHq = !r.city && !r.country;
    var place = r.city || r.hq_city || '';
    var code = r.country || r.hq_country || '';
    var country = (TIT.countries && TIT.countries[code]) || code;
    var where = esc([place, country].filter(Boolean).join(', '));
    if (!where) {
      where = '<span class="tit-nowhere">Location not stated</span>';
    } else if (isHq) {
      where += ' <span class="tit-hq" title="Employer headquarters, not a location named in the source">HQ</span>';
    }

    // data-label mirrors the header text. Below the table breakpoint each row
    // becomes a card and the labels are the only thing naming the fields.
    // The classes here must match what shortcodes.php renders, or a filtered
    // row would lay out differently from the row it replaced.
    return '<tr>' +
      '<td class="tit-eyebrow" data-label="Employer">' + esc(r.company) + '</td>' +
      '<td class="tit-headline" data-label="What happened"><span class="tit-h">' + esc(r.headline) + '</span>' +
      '<span class="tit-rt">' + esc(r.talent_readthrough) + '</span></td>' +
      '<td class="tit-meta" data-label="Where">' + where + '</td>' +
      '<td class="tit-meta" data-label="What it means"><span class="tit-tag ' + (DIRECTION_CLASS[r.signal_direction] || '') + '">' +
        esc(DIRECTION_LABEL[r.signal_direction] || r.signal_direction) + '</span></td>' +
      '<td class="tit-meta" data-label="Evidence"><span class="tit-conf tit-c-' + esc(r.confidence) + '">' +
        esc(CONFIDENCE_LABEL[r.confidence] || r.confidence) + '</span></td>' +
      '<td class="tit-meta" data-label="Source"><a href="' + esc(r.source_url) + '" rel="nofollow noopener" target="_blank">' +
        esc(r.source_name) + '</a></td>' +
      '</tr>';
  }

  // --- The rest of the page follows the filters -----------------------------
  // Until now only the table re-rendered, so the hero said "13 updates · 3
  // countries" and the charts drew the whole world while the rows underneath
  // showed one region. The page implied the filter applied to everything, and
  // that implication is exactly what a dashboard must not get wrong.

  var PILLAR_LABEL = {
    company_development: 'Growing and expanding',
    leadership_change: 'Leadership moves',
    rewards_comp: 'Pay and benefits',
    how_we_work: 'Ways of working'
  };

  var INDUSTRY_LABEL = {
    technology: 'Technology', financial_services: 'Financial services',
    healthcare: 'Healthcare', pharma_biotech: 'Pharma & biotech',
    retail_ecommerce: 'Retail & e-commerce', manufacturing: 'Manufacturing',
    energy_utilities: 'Energy & utilities', telecom: 'Telecom',
    media_entertainment: 'Media & entertainment',
    transport_logistics: 'Transport & logistics',
    professional_services: 'Professional services',
    public_sector: 'Public sector', hospitality_travel: 'Hospitality & travel',
    education: 'Education', food_beverage: 'Food & beverage',
    automotive: 'Automotive', aerospace_defence: 'Aerospace & defence',
    real_estate_construction: 'Real estate & construction'
  };

  function nfmt(n) { return Number(n || 0).toLocaleString(); }

  // --- Money ----------------------------------------------------------------
  // These mirror tit_money_short() and tit_money_full() in shortcodes.php
  // exactly. Two formatters would drift, and a filtered page would abbreviate
  // the same figure differently from the one the server rendered.
  var MONEY_UNITS = ['K', 'M', 'B', 'T'];

  function moneyShort(n) {
    n = Number(n) || 0;
    if (n <= 0) return '$0';
    if (n < 1000) return '$' + nfmt(Math.round(n));
    var i = 0, v = n / 1000;
    while (v >= 1000 && i < MONEY_UNITS.length - 1) { v /= 1000; i++; }
    // One decimal below 100, none above, and a figure is never rounded into a
    // different order of magnitude: $999M stays $999M.
    v = (v < 100) ? Math.round(v * 10) / 10 : Math.round(v);
    if (v >= 1000 && i < MONEY_UNITS.length - 1) { v = Math.round(v / 100) / 10; i++; }
    return '$' + String(v) + MONEY_UNITS[i];
  }

  function moneyFull(n) { return '$' + nfmt(Math.round(Number(n) || 0)); }

  // The coverage sentence, mirroring tit_money_coverage_note(). Never a
  // hardcoded pair of numbers: a dollar total that does not say what share of
  // the data it covers is the one number this product must not print.
  function coverageNote(money, dim) {
    if (!money || !money.coverage) return '';
    var withUsd = Number(money.coverage.with) || 0;
    var all = Number(money.coverage.all) || 0;
    if (!all) return 'No funding updates in this view yet, so there is nothing to add up.';

    var note = 'Totals cover the ' + nfmt(withUsd) + ' of ' + nfmt(all) +
      (all === 1 ? ' funding update that states' : ' funding updates that state') +
      ' an amount in US dollars.' +
      ' Amounts stated in another currency are left out rather than converted' +
      ' at a rate nobody published.';

    var placed = (money.placed && dim && money.placed[dim] != null)
      ? Number(money.placed[dim]) : withUsd;
    var missing = withUsd - placed;
    if (!dim || missing <= 0) return note;
    var names = { country: 'no country', city: 'no city', industry: 'no industry' };
    return note + ' ' + nfmt(missing) +
      (missing === 1 ? ' of those names ' : ' of those name ') +
      (names[dim] || 'no category') +
      (missing === 1 ? ', so it is not on this chart.' : ', so they are not on this chart.');
  }

  // Filtering to a single country produced "1 countries" in the hero, which is
  // a small thing that reads as carelessness on a page whose whole argument is
  // that the details are checked.
  function plural(n, one, many) {
    return nfmt(n) + ' ' + (Number(n) === 1 ? one : (many || one + 's'));
  }

  function paintRank(chart, rows, label, dirKey) {
    var wrap = chart && chart.querySelector('.tit-rank');
    if (!wrap) return;
    if (!rows.length) {
      wrap.innerHTML = '<p class="tit-rank-empty">Nothing in this view.</p>';
      return;
    }
    var max = Math.max.apply(null, rows.map(function (r) { return +r.n; })) || 1;
    // Every row the API sent, not the first six. /aggregate already returns up
    // to 40 per group, so slicing here discarded rows that had been fetched,
    // with no control anywhere on the page that could bring them back. The card
    // stays small because the list scrolls, not because the data is cut.
    // Rows are real buttons: each one is a filter (see the chart-row wiring
    // below), and a button is what makes that reachable by keyboard.
    wrap.innerHTML = rows.map(function (r) {
      var pct = Math.max(4, Math.round(100 * r.n / max));
      return '<button type="button" class="tit-rank-row" data-k="' + esc(r.k) + '"' +
        (dirKey ? ' data-dir="' + esc(r.k) + '"' : '') + ' aria-pressed="false">' +
        '<span class="tit-rank-name">' + esc(label(r.k)) + '</span>' +
        '<span class="tit-rank-track"><span class="tit-rank-fill" style="width:' + pct + '%"></span></span>' +
        '<span class="tit-rank-n">' + nfmt(r.n) + '</span></button>';
    }).join('');
  }

  function paintAggregate(data) {
    var total = +data.total || 0;

    // Hero fine print: restate the view, do not describe a set the reader is
    // no longer looking at.
    var fine = root.querySelector('.tit-hero-fine');
    if (fine) {
      var lead = fine.querySelector('.tit-fine-figures');
      if (!lead) {
        lead = document.createElement('span');
        lead.className = 'tit-fine-figures';
        fine.insertBefore(lead, fine.firstChild);
      }
      lead.textContent = plural(total, 'update') + ' · ' +
        plural(data.companies, 'employer') + ' · ' +
        plural(data.countries, 'country', 'countries') + ' · ' +
        nfmt(data.verified) + ' from official filings. ';
    }

    // The at-a-glance matrix moves with the filters like everything else. It
    // used to be the one part of the hero that did not, so a filtered page
    // had its own summary contradicting its own strip. Markup mirrors
    // tit_glance_matrix_html() in shortcodes.php, the same contract renderRow
    // has with the table.
    var glance = root.querySelector('.tit-glance');
    if (glance && data.glance && data.glance.rows) {
      glance.innerHTML = matrixHtml(data.glance);
    }

    // Cards are found by id, never by position. They used to be indexed out of
    // a querySelectorAll over every .tit-chart on the page, so adding a card
    // anywhere above would have silently repainted the wrong three.
    // Pillars keep their own markup (a bar per pillar), so they are painted
    // separately from the two rank charts.
    var pillars = root.querySelector('.tit-pillars');
    if (pillars) {
      var rows = data.by_pillar || [];
      // Buttons with span children, never divs inside a button: a button may
      // only contain phrasing content, and the spans carry the layout in CSS.
      pillars.innerHTML = rows.length ? rows.map(function (r) {
        var pct = total ? Math.round(100 * r.n / total) : 0;
        return '<button type="button" class="tit-pillar" data-k="' + esc(r.k) + '" aria-pressed="false">' +
          '<span class="tit-pillar-head">' +
          '<span class="tit-pillar-name">' + esc(PILLAR_LABEL[r.k] || r.k) + '</span>' +
          '<span class="tit-pillar-n">' + nfmt(r.n) + '</span></span>' +
          '<span class="tit-bar"><span style="width:' + pct + '%"></span></span></button>';
      }).join('') : '<p class="tit-rank-empty">Nothing in this view.</p>';
    }
    paintRank(document.getElementById('chart-place'), data.by_country || [], countryLabel);
    paintRank(document.getElementById('chart-direction'), data.by_direction || [], function (k) {
      return DIRECTION_LABEL[k] || k;
    }, true);

    // The money cards move with the filters like everything else, coverage
    // sentence included: the sentence describes the filtered set, so it has to
    // be recomputed whenever the set changes.
    var money = data.money || null;
    paintMoney(document.getElementById('chart-money-country'),
      money && money.by_country, countryLabel, money, 'country');
    paintMoney(document.getElementById('chart-money-city'),
      money && money.by_city, function (k) { return k; }, money, 'city');
    paintMoney(document.getElementById('chart-money-industry'),
      money && money.by_industry, function (k) { return INDUSTRY_LABEL[k] || k; },
      money, 'industry');

    // What the detail control is setting aside, restated for the filtered set.
    // The counts are computed WITHOUT the detail filter applied (see
    // /aggregate), so switching to notable does not report zero routine
    // filings at exactly the moment the number matters.
    var note = document.getElementById('tit-detail-note');
    if (note && data.materiality) {
      note.textContent = detailNote(
        inputs.detail ? inputs.detail.value : 'notable',
        data.materiality.notable, data.materiality.routine);
    }

    // What the stated-headcount toggle would leave, so the reader sees what it
    // does before using it.
    var statedN = document.getElementById('tit-stated-n');
    if (statedN && data.stated_headcount != null) {
      statedN.textContent = nfmt(data.stated_headcount);
    }

    // Re-rendering wiped the pressed state off every row; put it back.
    syncChartStates();
  }

  function countryLabel(k) { return (TIT.countries && TIT.countries[k]) || k; }

  // Mirrors tit_detail_note(). Both counts, always, plus what routine means:
  // that sentence is the reason the default is allowed to hold rows back at
  // all. A reader can see exactly how many and change it in one click.
  var ROUTINE_MEANS = ' Routine means a bare officer or director change with' +
    ' no headcount, no money and no location named.';

  function detailNote(mode, notable, routine) {
    notable = Number(notable) || 0;
    routine = Number(routine) || 0;
    if (!routine) {
      return 'Showing all ' + nfmt(notable) +
        (notable === 1 ? ' update.' : ' updates.') +
        ' Nothing in this view is a routine filing.' + ROUTINE_MEANS;
    }
    if (mode === 'all') {
      return 'Showing all ' + nfmt(notable + routine) +
        ' updates, routine filings included. ' + nfmt(routine) +
        ' of them are routine.' + ROUTINE_MEANS;
    }
    return 'Showing ' + nfmt(notable) + ' notable updates. ' + nfmt(routine) +
      ' routine filings join them when you switch to Everything.' + ROUTINE_MEANS;
  }

  // Mirrors tit_money_chart() in shortcodes.php: same classes, same
  // data attributes, same title-attribute exact figure. The CSV download and
  // the click-to-filter wiring both read this markup, so it cannot drift.
  function paintMoney(chart, rows, label, money, dim) {
    if (!chart) return;
    rows = rows || [];
    var wrap = chart.querySelector('.tit-rank');
    if (wrap) {
      if (!rows.length) {
        wrap.innerHTML = '<p class="tit-rank-empty">No US dollar amounts in this view yet.</p>';
      } else {
        var max = Math.max.apply(null, rows.map(function (r) { return +r.v; })) || 1;
        wrap.innerHTML = rows.map(function (r) {
          var pct = Math.max(4, Math.round(100 * r.v / max));
          return '<button type="button" class="tit-rank-row" data-k="' + esc(r.k) + '"' +
            ' data-v="' + esc(Math.round(Number(r.v) || 0)) + '" aria-pressed="false">' +
            '<span class="tit-rank-name">' + esc(label(r.k)) + '</span>' +
            '<span class="tit-rank-track"><span class="tit-rank-fill" style="width:' + pct + '%"></span></span>' +
            '<span class="tit-rank-n" title="' + esc(moneyFull(r.v)) + '">' +
            esc(moneyShort(r.v)) + '</span></button>';
        }).join('');
      }
    }
    var note = chart.querySelector('.tit-money-note');
    if (note) note.textContent = coverageNote(money, dim);
  }

  function matrixHtml(m) {
    var h = '<div class="tit-matrix-scroll"><table class="tit-matrix"><thead><tr>' +
      '<th scope="col"><span class="tit-sr">Signal</span></th>';
    m.periods.forEach(function (p) { h += '<th scope="col">' + esc(p) + '</th>'; });
    h += '</tr></thead><tbody>';
    m.rows.forEach(function (r) {
      var money = r.kind === 'money';
      // Row max, so the heat tint is scaled inside the row, matching the PHP.
      var rowMax = 0;
      r.cells.forEach(function (n) { rowMax = Math.max(rowMax, +n || 0); });
      var cls = 'tit-matrix-row' + (r.key === 'total' ? ' tit-matrix-total' : '') +
        (money ? ' tit-matrix-money' : '');
      h += '<tr class="' + cls + '" data-signal="' + esc(r.key) + '">' +
        '<th scope="row">' + esc(r.label) +
        (money ? '<span class="tit-matrix-unit">sum of dollars</span>' : '') + '</th>';
      r.cells.forEach(function (n, i) {
        n = +n || 0;
        var intensity = (rowMax > 0 && n > 0)
          ? Math.max(0.14, Math.round(Math.sqrt(n / rowMax) * 1000) / 1000) : 0;
        var text = money ? moneyShort(n) : nfmt(n);
        var full = money ? moneyFull(n) : nfmt(n);
        var spoken = money
          ? r.label + ', ' + m.periods[i] + ', ' + full + ' in US dollars'
          : r.label + ', ' + m.periods[i];
        h += '<td><button type="button" class="tit-cell' + (n === 0 ? ' tit-cell-zero' : '') +
          (money ? ' tit-cell-money' : '') +
          '" style="--i:' + intensity + '" data-filter="' + esc(r.filter) +
          '" data-since="' + esc(m.starts[i]) + '"' +
          (money ? ' title="' + esc(full) + '"' : '') +
          ' aria-pressed="false" aria-label="' + esc(spoken) + '">' +
          esc(text) + '</button></td>';
      });
      h += '</tr>';
    });
    h += '</tbody></table></div>' +
      '<p class="tit-matrix-note">Stronger colour means more activity, measured ' +
      'across each row. Rows overlap on purpose: a funded employer can also be ' +
      'hiring up, so columns are not sums. ' +
      '<strong>Click any number to filter the whole page.</strong> ' +
      '<span class="tit-matrix-money-note">Money raised is the odd row out: it ' +
      'adds up dollars, while every other row counts updates. ' +
      esc(coverageNote({ coverage: m.coverage }, '')) + '</span></p>';
    return h;
  }

  var pendingAgg = null;

  function refreshAggregate(params) {
    if (pendingAgg) pendingAgg.abort();
    pendingAgg = new AbortController();
    fetch(TIT.api + 'aggregate?' + params.toString(), { signal: pendingAgg.signal })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) paintAggregate(data); })
      .catch(function (err) {
        // Leave the server-rendered numbers alone rather than blanking them.
        if (err && err.name === 'AbortError') return;
      });
  }

  var pending = null;
  var lastQuery = '';

  // The querystring people see and share, which is not the one the API gets:
  // per_page is paging, and sort/country_basis at their defaults say nothing.
  // A link should carry the choices someone actually made.
  function shareQuery(params) {
    var out = new URLSearchParams();
    params.forEach(function (value, key) {
      if (key === 'per_page') return;
      if (NEUTRAL[key] === value) return;
      out.set(key, value);
    });
    return out.toString();
  }

  // Restore a shared link. Anything the page does not recognise is left alone
  // rather than guessed at, and the region strip is matched by its code list so
  // a link to "Europe" comes back as the Europe tab and not a bare country
  // filter that looks nothing like what was sent.
  function applyUrlState() {
    var q = new URLSearchParams(location.search);
    if (!q.toString()) return;

    var country = q.get('country') || '';
    var tab = quickFind(tabs, function (t) { return (t.dataset.codes || '') === country; });
    if (country && tab) {
      setRegion(country);
      q.delete('country');
    }

    if (q.get('stated_headcount') === '1') setStated(true);

    // Saved views are matched by their PARAMETERS, never by a token, because a
    // shared link carries the expanded querystring: "Biggest Raises" arrives as
    // funding=1&sort=raised. A view whose every parameter is present in the
    // link is the view that was sent, and its parameters are then removed so
    // the same narrowing does not also appear as a second chip.
    var qv = quickFind(quickButtons, function (b) {
      var spec = b.dataset.qv || '';
      if (!spec) return false;
      return spec.split('&').every(function (pair) {
        var kv = pair.split('=');
        return q.get(kv[0]) === decodeURIComponent(kv[1] || '');
      });
    });
    if (qv) {
      setQuickView(qv.dataset.qv);
      qv.dataset.qv.split('&').forEach(function (pair) { q.delete(pair.split('=')[0]); });
    } else if (q.get('funding') === '1') {
      // funding=1 is the one shareable param with no input of its own, and no
      // button of its own either since the quick views were cut back. It is
      // still applied by the matrix's funding and money cells, so a link
      // carrying it must come back as a funding view rather than as the
      // unfiltered page. Without this a deep link quietly drops the very thing
      // it was sent to show.
      setQuickView('funding=1');
      q.delete('funding');
    }

    Object.keys(inputs).forEach(function (key) {
      var el = inputs[key];
      if (!el || !q.has(key)) return;
      var value = q.get(key);
      if (el.tagName === 'SELECT') {
        var ok = Array.prototype.some.call(el.options, function (o) { return o.value === value; });
        if (!ok) {
          // Countries, states, cities and funding stages come from /facets,
          // which is still in flight at this point: this runs on the first
          // paint, and that fetch has not answered yet. Dropping the value
          // because its option does not exist YET made every shared link to a
          // single country come back as the whole world. The closed
          // vocabularies (kind, direction, team, industry, evidence, amount
          // band) are all server-rendered, so a value missing from one of
          // those really is a value we do not have, and is still ignored
          // rather than guessed at.
          if (!FACET_SELECT[key]) return;
          ensureOption(el, value,
            key === 'country' ? countryLabel(value)
              : (key === 'funding_stage' ? (STAGE_LABEL[value] || value) : value));
        }
      }
      el.value = value;
    });
  }

  // Selects whose options are fetched rather than rendered by the server.
  var FACET_SELECT = { country: 1, state: 1, city: 1, funding_stage: 1 };

  // --- The two front controls ----------------------------------------------
  // The filter bar used to be twelve controls of equal weight, each labelled
  // with a database column. These two are the visible layer over five of the
  // old ones: they READ and WRITE the same hidden selects, so the querystring,
  // the chips bar, the exports and every click-to-filter chart keep working
  // against exactly the state they always did. Nothing about what a filter
  // means has changed; only what a person has to look at.

  var lookingSel = document.getElementById('tit-f-looking');
  var placeSel = document.getElementById('tit-f-place');

  // "I'm looking for" writes the pillar select, the direction select, or the
  // funding view, depending on which one its option names.
  function applyLooking(spec) {
    if (inputs.pillar) inputs.pillar.value = '';
    if (inputs.direction) inputs.direction.value = '';
    if (quickView === 'funding=1') setQuickView(null);
    if (!spec) return;
    var kv = spec.split('=');
    if (kv[0] === 'funding') { setQuickView('funding=1'); return; }
    if (inputs[kv[0]]) inputs[kv[0]].value = kv[1];
  }

  // And the reverse, so a chart click or a matrix cell leaves the control
  // showing what is actually filtering the page. A control that says "All
  // updates" while the page is filtered to leadership changes is worse than no
  // control at all.
  function syncLooking() {
    if (!lookingSel) return;
    var want = '';
    if (inputs.pillar && inputs.pillar.value) want = 'pillar=' + inputs.pillar.value;
    else if (inputs.direction && inputs.direction.value) want = 'direction=' + inputs.direction.value;
    else if (quickView === 'funding=1') want = 'funding=1';
    var has = Array.prototype.some.call(lookingSel.options, function (o) { return o.value === want; });
    // A combination the control cannot express (two of them at once, or a
    // direction it does not offer) shows as blank rather than as a lie.
    lookingSel.value = has ? want : '';
  }

  // One "Where", three underlying columns. Option values carry which one:
  // country:US, state:CA, city:London.
  function applyPlace(value) {
    if (inputs.country) inputs.country.value = '';
    if (inputs.state) inputs.state.value = '';
    if (inputs.city) inputs.city.value = '';
    if (!value) return;
    var at = value.indexOf(':');
    var key = value.slice(0, at);
    var val = value.slice(at + 1);
    if (!inputs[key]) return;
    ensureOption(inputs[key], val, val);
    inputs[key].value = val;
    // A region and a single country are two answers to one question, and
    // sending both would return nothing while looking like a broken filter.
    if (key === 'country') setRegion(null);
  }

  function syncPlace() {
    if (!placeSel) return;
    var want = '';
    // A region strip selection overrides the country parameter in refresh(),
    // so while a region is on, no single place is what is narrowing the page.
    // Showing "United States" in this box while a Region chip was doing the
    // work made the system's choice look like the reader's.
    if (!region) {
      if (inputs.country && inputs.country.value) want = 'country:' + inputs.country.value;
      else if (inputs.state && inputs.state.value) want = 'state:' + inputs.state.value;
      else if (inputs.city && inputs.city.value) want = 'city:' + inputs.city.value;
    }
    var has = Array.prototype.some.call(placeSel.options, function (o) { return o.value === want; });
    if (!has && want) {
      ensureOption(placeSel, want, placeLabel(want));
      has = true;
    }
    placeSel.value = has ? want : '';
  }

  function placeLabel(value) {
    var at = value.indexOf(':');
    var key = value.slice(0, at), val = value.slice(at + 1);
    return key === 'country' ? countryLabel(val) : val;
  }

  if (lookingSel) {
    lookingSel.addEventListener('change', function () {
      applyLooking(lookingSel.value);
      refresh();
    });
  }
  if (placeSel) {
    placeSel.addEventListener('change', function () {
      applyPlace(placeSel.value);
      refresh();
    });
  }

  // --- Only updates that state a headcount ----------------------------------
  // Its own state rather than a member of `inputs`, because a checkbox has no
  // meaningful .value and everything in that map is read as one.
  var statedBox = document.getElementById('tit-f-stated_headcount');
  var stated = false;

  function setStated(on) {
    stated = !!on;
    if (statedBox) statedBox.checked = stated;
  }

  if (statedBox) {
    statedBox.addEventListener('change', function () {
      setStated(statedBox.checked);
      refresh();
    });
  }

  // --- How places are decided ----------------------------------------------
  // A methodology choice, not a filter, so it lives behind a sentence about how
  // places are decided and shows only when someone questions one. The select
  // ships visible and is hidden here, so a reader without JavaScript keeps a
  // working control rather than losing it to a button that does nothing.
  var basisBtn = document.getElementById('tit-basis-btn');
  var basisPick = document.getElementById('tit-basis-pick');
  if (basisBtn && basisPick) {
    basisPick.hidden = true;
    basisBtn.hidden = false;
    basisBtn.addEventListener('click', function () {
      var open = basisPick.hidden;
      basisPick.hidden = !open;
      basisBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open && inputs.country_basis) inputs.country_basis.focus();
    });
  }

  // --- More filters ---------------------------------------------------------
  // Native <details>, so it collapses with no JavaScript at all. What JS adds
  // is the count, and opening the panel when something inside it is already on:
  // a shared link must never narrow the page with the control that did it
  // folded out of sight.
  var moreBox = document.getElementById('tit-more');
  var moreLabel = document.getElementById('tit-more-label');
  var MORE_KEYS = ['direction', 'function', 'industry', 'min_funding_usd',
                   'funding_stage', 'confidence', 'q', 'since', 'until'];

  function moreActive() {
    var n = 0;
    MORE_KEYS.forEach(function (k) {
      var el = inputs[k];
      if (!el) return;
      var v = (el.value || '').trim();
      if (v && v !== NEUTRAL[k]) n++;
    });
    return n;
  }

  // $open is passed only when restoring a shared link. Opening on every
  // refresh would throw the panel open each time a matrix cell set a date,
  // which is a panel fighting the reader rather than serving them.
  function syncMore(open) {
    var n = moreActive();
    if (moreLabel) moreLabel.textContent = n ? 'More filters (' + n + ')' : 'More filters';
    if (open && moreBox && n && !moreBox.open) moreBox.open = true;
  }

  // The CSV and JSON links under the table download exactly what is on screen:
  // the current filters ride along as query params, and the scope word says
  // which set the file will hold. Server-rendered hrefs point at the whole
  // dataset, so a reader without JavaScript still gets a working download.
  function updateExportLinks() {
    ['tit-export-csv', 'tit-export-json'].forEach(function (id) {
      var a = document.getElementById(id);
      if (!a) return;
      var base = a.getAttribute('data-base');
      if (!base) return;
      a.href = base + (lastQuery ? '&' + lastQuery : '');
      var scope = document.getElementById(id + '-scope');
      if (scope) scope.textContent = lastQuery ? ' · filtered' : ' · all';
    });
  }

  function refresh() {
    var params = new URLSearchParams();
    Object.keys(inputs).forEach(function (key) {
      var value = inputs[key] && inputs[key].value.trim();
      // `detail` is the one control whose page default differs from the API's
      // own. The endpoint returns everything unless asked, deliberately: an
      // endpoint that quietly withheld two thirds of its rows would be a worse
      // lie than a cluttered page. So the page asks, every time, even at its
      // default value.
      if (value && (key === 'detail' || value !== NEUTRAL[key])) params.set(key, value);
    });
    // A region is a list of country codes, so it takes the same parameter as the
    // country select. Whichever the person touched last is the one that counts —
    // silently ANDing "Europe" with "Japan" would return nothing and look broken.
    if (region) params.set('country', region);
    if (stated) params.set('stated_headcount', '1');
    // A quick view is a saved set of parameters, applied on top of whatever
    // else is selected rather than replacing it: someone who has picked Europe
    // and then clicks "Raised money" means both.
    if (quickView) {
      quickView.split('&').forEach(function (pair) {
        var kv = pair.split('=');
        // Anything the view wrote into a visible control is already in the
        // inputs loop above; re-applying it here would let a quick view move
        // the window (or the sort) while the control still showed the reader's
        // own choice.
        if (kv[0] && !QV_VISIBLE[kv[0]]) {
          params.set(kv[0], decodeURIComponent(kv[1] || ''));
        }
      });
    }
    params.set('per_page', '50');

    // The visible controls follow the state, never the other way round: a
    // chart click or a matrix cell writes the hidden selects, and these put the
    // two front controls back in agreement with them.
    syncLooking();
    syncPlace();
    syncMore();

    paintActive();
    syncChartStates();

    // Put the view in the address bar. A dashboard whose URL never changes
    // cannot be sent to anyone: the recipient gets the unfiltered page and no
    // idea what they were meant to be looking at. replaceState, not pushState,
    // so typing in the search box does not bury the back button under a history
    // entry per keystroke.
    lastQuery = shareQuery(params);
    try {
      history.replaceState(null, '',
        location.pathname + (lastQuery ? '?' + lastQuery : '') + location.hash);
    } catch (e) { /* a URL we cannot write is not worth failing the render for */ }

    updateExportLinks();

    // Charts and figures move with the same querystring the table uses, so the
    // two can never disagree about what is being shown.
    refreshAggregate(params);

    if (pending) pending.abort();
    pending = new AbortController();

    fetch(TIT.api + 'query?' + params.toString(), { signal: pending.signal })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        tbody.innerHTML = data.rows.length
          ? data.rows.map(renderRow).join('')
          : '<tr><td colspan="6">Nothing matches those filters yet.</td></tr>';
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        /* leave the existing rows in place */
      });
  }

  var timer;
  function debounced() {
    clearTimeout(timer);
    timer = setTimeout(refresh, 250);
  }

  Object.keys(inputs).forEach(function (key) {
    if (!inputs[key]) return;
    inputs[key].addEventListener(inputs[key].tagName === 'SELECT' ? 'change' : 'input', function () {
      if (key === 'country') setRegion(null);
      // Editing a control a saved view had taken over means leaving that view.
      // "Biggest Raises" is a sort plus a filter; change the sort by hand and
      // the chip would otherwise keep claiming a view the page no longer
      // shows. What the reader just chose is kept, the view is dropped.
      if (QV_VISIBLE[key] && quickView &&
          quickView.split('&').some(function (p) { return p.split('=')[0] === key; })) {
        var kept = inputs[key].value;
        setQuickView(null);
        inputs[key].value = kept;
      }
      debounced();
    });
  });

  var region = null;
  var tabs = Array.prototype.slice.call(root.querySelectorAll('.tit-region'));

  function setRegion(codes) {
    region = codes || null;
    tabs.forEach(function (t) {
      var on = (t.dataset.codes || '') === (region || '');
      t.classList.toggle('is-on', on);
      t.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  tabs.forEach(function (t) {
    t.addEventListener('click', function () {
      setRegion(t.dataset.codes);
      if (inputs.country) inputs.country.value = '';
      refresh();
    });
  });
  setRegion(null);

  var quickView = null;
  var quickButtons = Array.prototype.slice.call(root.querySelectorAll('.tit-qv'));

  // Parameters a quick view writes into a VISIBLE control rather than straight
  // into the querystring. A view that sorts by size while the sort box still
  // reads "Newest first" is a page ordering itself by a rule nothing on screen
  // reports, which is the same failure as a date filter with an empty From box.
  var QV_VISIBLE = { since: 1, until: 1, sort: 1 };

  function setQuickView(v) {
    var prev = quickView;
    quickView = v || null;
    quickButtons.forEach(function (o) {
      var on = (o.dataset.qv || '') === (quickView || '');
      o.classList.toggle('is-on', on);
      o.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    // Leaving a view puts back whatever control it had taken over, or the sort
    // it chose would outlive the view that chose it.
    if (prev && prev !== quickView) {
      prev.split('&').forEach(function (pair) {
        var k = pair.split('=')[0];
        if (QV_VISIBLE[k] && inputs[k]) inputs[k].value = NEUTRAL[k] || '';
      });
    }
    if (quickView) {
      quickView.split('&').forEach(function (pair) {
        var kv = pair.split('=');
        if (QV_VISIBLE[kv[0]] && inputs[kv[0]]) {
          inputs[kv[0]].value = decodeURIComponent(kv[1] || '');
        }
      });
    }
  }

  quickButtons.forEach(function (b) {
    b.addEventListener('click', function () {
      setQuickView(b.dataset.qv);
      refresh();
    });
  });
  if (quickButtons.length) quickButtons[0].classList.add('is-on');

  // --- What is currently being filtered ------------------------------------
  var activeBar = document.getElementById('tit-active');
  var activeChips = document.getElementById('tit-active-chips');
  var resetBtn = document.getElementById('tit-reset');

  function chip(key, text) {
    return '<button type="button" class="tit-chip" data-clear="' + esc(key) + '">' +
      '<span class="tit-chip-k">' + esc(FILTER_LABEL[key] || key) + '</span>' +
      '<span class="tit-chip-v">' + esc(text) + '</span>' +
      '<span class="tit-chip-x" aria-hidden="true">&#215;</span>' +
      '<span class="tit-sr">, remove this filter</span></button>';
  }

  function paintActive() {
    if (!activeBar || !activeChips) return;
    var chips = [];

    if (region) {
      var tab = quickFind(tabs, function (t) { return (t.dataset.codes || '') === region; });
      var name = tab && tab.querySelector('.tit-region-name');
      chips.push(chip('region', name ? name.textContent : region));
    }
    if (stated) {
      chips.push(chip('stated_headcount', 'Only with a stated headcount'));
    }
    if (quickView) {
      // A view with no button of its own still has to appear here. funding=1
      // is applied by the matrix's funding and money cells and no longer has a
      // chip in the strip, and a filter narrowing the page while nothing on
      // screen names it is exactly what this bar exists to prevent.
      var qb = quickFind(quickButtons, function (b) { return (b.dataset.qv || '') === quickView; });
      chips.push(chip('quickview', qb ? qb.textContent.trim()
                                      : (QV_LABEL[quickView] || quickView)));
    }
    Object.keys(inputs).forEach(function (key) {
      var el = inputs[key];
      // `detail` gets no chip: the bar above the table states it in full, with
      // both counts and what routine means. A chip would be a quieter copy of
      // something already impossible to miss.
      if (!el || key === 'sort' || key === 'detail') return;
      var value = (el.value || '').trim();
      if (!value || value === NEUTRAL[key]) return;
      // The country select is slaved to the region strip; showing both would
      // be two chips for one narrowing.
      if (key === 'country' && region) return;
      var text = el.tagName === 'SELECT'
        ? ((el.options[el.selectedIndex] || {}).text || value)
        : value;
      chips.push(chip(key, text));
    });

    activeChips.innerHTML = chips.join('');
    activeBar.hidden = chips.length === 0;
  }

  function quickFind(list, test) {
    for (var i = 0; i < list.length; i++) { if (test(list[i])) return list[i]; }
    return null;
  }

  function clearOne(key) {
    if (key === 'region') setRegion(null);
    else if (key === 'quickview') setQuickView(null);
    else if (key === 'stated_headcount') setStated(false);
    else if (inputs[key]) inputs[key].value = NEUTRAL[key] || '';
    refresh();
  }

  if (activeChips) {
    activeChips.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('[data-clear]') : null;
      if (btn) clearOne(btn.getAttribute('data-clear'));
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener('click', function () {
      Object.keys(inputs).forEach(function (k) {
        if (!inputs[k] || k === 'sort') return;
        inputs[k].value = NEUTRAL[k] || '';
      });
      setRegion(null);
      setQuickView(null);
      setStated(false);
      refresh();
    });
  }

  // --- Per-chart controls ---------------------------------------------------
  // Every card gets its own expand, share and download, and each one acts on
  // that card alone. The buttons ship hidden from the shortcode and are
  // revealed here, so a reader without JavaScript is never offered a control
  // that does nothing.
  function flash(btn) {
    btn.classList.add('is-done');
    setTimeout(function () { btn.classList.remove('is-done'); }, 1200);
  }

  function copyText(text, done) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { done(); });
      return;
    }
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) { /* nothing else to try */ }
    document.body.removeChild(ta);
    done();
  }

  // The chart is a list of labelled bars, not a canvas, so the useful download
  // is the numbers rather than a picture of them. Read from the rendered rows
  // so an export can never disagree with what is on screen.
  function chartCsv(chart) {
    var rows = Array.prototype.slice.call(
      chart.querySelectorAll('.tit-rank-row, .tit-pillar'));
    // A money card downloads the exact dollar figure from data-v, never the
    // abbreviation on screen: "$1.2B" in a spreadsheet is a string nobody can
    // add up, which is the problem these views exist to solve.
    var money = chart.classList.contains('tit-chart-money');
    var out = [['label', money ? 'usd' : 'count']];
    rows.forEach(function (r) {
      var name = r.querySelector('.tit-rank-name, .tit-pillar-name');
      var n = r.querySelector('.tit-rank-n, .tit-pillar-n');
      if (!name || !n) return;
      out.push([name.textContent.trim(),
                money ? (r.getAttribute('data-v') || '') : n.textContent.trim()]);
    });
    return out.map(function (cells) {
      return cells.map(function (c) { return '"' + String(c).replace(/"/g, '""') + '"'; }).join(',');
    }).join('\n');
  }

  function download(name, text) {
    var blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  Array.prototype.slice.call(root.querySelectorAll('.tit-chart')).forEach(function (chart) {
    var expand = chart.querySelector('.tit-expand');
    var share = chart.querySelector('.tit-chart-share');
    var dl = chart.querySelector('.tit-chart-dl');

    if (expand) {
      expand.hidden = false;
      var label = expand.querySelector('.tit-expand-t');
      expand.addEventListener('click', function () {
        var on = chart.classList.toggle('is-expanded');
        expand.setAttribute('aria-expanded', on ? 'true' : 'false');
        expand.title = on ? 'Collapse this chart' : 'Expand this chart';
        if (label) label.textContent = on ? 'Collapse' : 'Expand';
      });
    }

    if (share) {
      share.hidden = false;
      share.addEventListener('click', function () {
        // The filters live in the querystring, so the link reproduces the view
        // rather than just the page, and the hash lands on this card.
        var url = location.origin + location.pathname +
          (lastQuery ? '?' + lastQuery : '') + (chart.id ? '#' + chart.id : '');
        copyText(url, function () { flash(share); });
      });
    }

    if (dl) {
      dl.hidden = false;
      dl.addEventListener('click', function () {
        download('talent-' + (dl.dataset.chart || 'chart') + '.csv', chartCsv(chart));
        flash(dl);
      });
    }
  });

  // --- Clickable chart rows -------------------------------------------------
  // Each bar row is a filter. A click routes through the SAME state the
  // dropdowns and quick views use (the pillar/country/direction inputs), so
  // refresh() carries it into the chips bar, the table, the other charts, the
  // address bar and the export links in one pass. Clicking the row again
  // clears it. Delegated to the card, so the server-rendered rows and every
  // JS re-render are wired identically.
  var CHART_FILTER = [
    { el: document.getElementById('chart-kind'), key: 'pillar' },
    { el: document.getElementById('chart-place'), key: 'country' },
    { el: document.getElementById('chart-direction'), key: 'direction' },
    // The money cards are filters like any other chart: same wiring, so a
    // click on "United States" in a dollar ranking narrows the table, the
    // chips bar, the address bar and the export links in one pass.
    { el: document.getElementById('chart-money-country'), key: 'country' },
    { el: document.getElementById('chart-money-city'), key: 'city' },
    { el: document.getElementById('chart-money-industry'), key: 'industry',
      label: function (k) { return INDUSTRY_LABEL[k] || k; } }
  ];

  function syncChartStates() {
    CHART_FILTER.forEach(function (cf) {
      if (!cf.el) return;
      // A selected region overrides the country select in refresh(), so while
      // a region is on, no single country row is what is filtering the page.
      var current = cf.key === 'country'
        ? (region ? '' : (inputs.country ? inputs.country.value : ''))
        : (inputs[cf.key] ? inputs[cf.key].value : '');
      Array.prototype.forEach.call(cf.el.querySelectorAll('[data-k]'), function (b) {
        var on = current !== '' && b.getAttribute('data-k') === current;
        b.classList.toggle('is-on', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    });
    syncGlance();
  }

  // /facets lists values from the location columns only, so a place that only
  // ever appears as an employer's head office can show up in a chart with no
  // option to select. Add it rather than letting the click silently do nothing.
  function ensureOption(select, value, text) {
    if (!select || value === '' || value == null) return;
    var has = Array.prototype.some.call(select.options, function (o) { return o.value === value; });
    if (has) return;
    var opt = document.createElement('option');
    opt.value = value;
    opt.textContent = text || value;
    select.appendChild(opt);
  }

  CHART_FILTER.forEach(function (cf) {
    if (!cf.el) return;
    cf.el.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('[data-k]') : null;
      if (!btn || !cf.el.contains(btn)) return;
      var k = btn.getAttribute('data-k');
      if (cf.key === 'country') {
        var was = !region && inputs.country && inputs.country.value === k;
        setRegion(null); // the region strip overrides the country parameter
        ensureOption(inputs.country, k, countryLabel(k));
        if (inputs.country) inputs.country.value = was ? '' : k;
      } else if (inputs[cf.key]) {
        ensureOption(inputs[cf.key], k, cf.label ? cf.label(k) : k);
        inputs[cf.key].value = inputs[cf.key].value === k ? '' : k;
      }
      refresh();
    });
  });

  // --- Clickable at-a-glance matrix cells -----------------------------------
  // A cell is that row's filter PLUS that column's period, routed through the
  // same inputs and quick-view state everything else uses; clicking the lit
  // cell clears both. The "All updates" row carries no row filter, so its
  // click means "everything in this period" and clears the other row filters.
  var glanceBox = root.querySelector('.tit-glance');

  function glanceFilterOn(f) {
    if (f === 'funding=1') return quickView === 'funding=1';
    if (f) {
      var kv = f.split('=');
      return !!(inputs[kv[0]] && inputs[kv[0]].value === kv[1]);
    }
    // Total row: on only when no matrix row filter is narrowing the page.
    return !(inputs.direction && inputs.direction.value) &&
           !(inputs.pillar && inputs.pillar.value) &&
           quickView !== 'funding=1';
  }

  function setGlanceFilter(f, on) {
    if (f === 'funding=1') { setQuickView(on ? 'funding=1' : null); return; }
    if (f) {
      var kv = f.split('=');
      if (inputs[kv[0]]) inputs[kv[0]].value = on ? kv[1] : '';
      return;
    }
    if (on) {
      // "All updates": the period is the whole filter.
      if (inputs.direction) inputs.direction.value = '';
      if (inputs.pillar) inputs.pillar.value = '';
      if (quickView === 'funding=1') setQuickView(null);
    }
  }

  function syncGlance() {
    if (!glanceBox) return;
    var since = inputs.since ? inputs.since.value : '';
    Array.prototype.forEach.call(glanceBox.querySelectorAll('.tit-cell'), function (b) {
      var on = since !== '' && b.getAttribute('data-since') === since &&
               glanceFilterOn(b.getAttribute('data-filter') || '');
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  if (glanceBox) {
    glanceBox.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('.tit-cell') : null;
      if (!btn) return;
      var f = btn.getAttribute('data-filter') || '';
      var wasOn = btn.classList.contains('is-on');
      setGlanceFilter(f, !wasOn);
      if (inputs.since) inputs.since.value = wasOn ? '' : (btn.getAttribute('data-since') || '');
      if (!wasOn && inputs.until) inputs.until.value = '';
      refresh();
    });
  }

  populateFacets();

  // Last, because it needs the inputs, the region tabs and refresh() to exist.
  // Only fetches when the link actually carried a view; the plain page is
  // already rendered by the server.
  applyUrlState();
  // A link that narrows the page with a control folded out of sight is a link
  // whose recipient cannot see why they are looking at what they are looking
  // at. Open the panel once, here, and never again on its own.
  syncMore(true);
  syncLooking();
  syncPlace();
  if (location.search) refresh();
})();
