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
    confidence: document.getElementById('tit-f-confidence'),
    country_basis: document.getElementById('tit-f-country_basis'),
    company: document.getElementById('tit-f-company'),
    q: document.getElementById('tit-f-q'),
    since: document.getElementById('tit-f-since'),
    until: document.getElementById('tit-f-until'),
    sort: document.getElementById('tit-f-sort')
  };

  // Values that narrow nothing. They are never sent, never become a chip, and
  // never make the page count as filtered. Without this, country_basis="any"
  // and sort="newest" would each show as an active filter on a page nobody had
  // touched, which is the fastest way to make a filter bar mean nothing.
  var NEUTRAL = { sort: 'newest', country_basis: 'any' };

  // How each filter names itself in the active-filter bar.
  var FILTER_LABEL = {
    pillar: 'Kind', direction: 'Direction', 'function': 'Roles',
    industry: 'Industry', country: 'Country', state: 'US state',
    confidence: 'How solid', country_basis: 'Place basis', company: 'Employer',
    q: 'Search', since: 'From', until: 'To', region: 'Region', quickview: 'View'
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
      })
      .catch(function () { /* filters degrade to what the server rendered */ });
  }

  function fill(select, values, asCountries) {
    if (!select || !values) return;
    var items = values.map(function (v) {
      return { value: v, label: (asCountries && TIT.countries[v]) || v };
    });
    if (asCountries) {
      items.sort(function (a, b) { return a.label.localeCompare(b.label); });
    }
    items.forEach(function (item) {
      var opt = document.createElement('option');
      opt.value = item.value;
      opt.textContent = item.label;
      select.appendChild(opt);
    });
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
      '<td class="tit-meta" data-label="How solid"><span class="tit-conf tit-c-' + esc(r.confidence) + '">' + esc(r.confidence) + '</span></td>' +
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

  function nfmt(n) { return Number(n || 0).toLocaleString(); }

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

    var charts = root.querySelectorAll('.tit-charts .tit-chart');
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
    paintRank(charts[1], data.by_country || [], function (k) {
      return (TIT.countries && TIT.countries[k]) || k;
    });
    paintRank(charts[2], data.by_direction || [], function (k) {
      return DIRECTION_LABEL[k] || k;
    }, true);
    // Re-rendering wiped the pressed state off every row; put it back.
    syncChartStates();
  }

  function matrixHtml(m) {
    var h = '<div class="tit-matrix-scroll"><table class="tit-matrix"><thead><tr>' +
      '<th scope="col"><span class="tit-sr">Signal</span></th>';
    m.periods.forEach(function (p) { h += '<th scope="col">' + esc(p) + '</th>'; });
    h += '</tr></thead><tbody>';
    m.rows.forEach(function (r) {
      h += '<tr' + (r.key === 'total' ? ' class="tit-matrix-total"' : '') + '>' +
        '<th scope="row">' + esc(r.label) + '</th>';
      r.cells.forEach(function (n, i) {
        h += '<td><button type="button" class="tit-cell' + (+n === 0 ? ' tit-cell-zero' : '') +
          '" data-filter="' + esc(r.filter) + '" data-since="' + esc(m.starts[i]) +
          '" aria-pressed="false" aria-label="' + esc(r.label + ', ' + m.periods[i]) + '">' +
          nfmt(n) + '</button></td>';
      });
      h += '</tr>';
    });
    h += '</tbody></table></div>' +
      '<p class="tit-matrix-note">Rows overlap on purpose: a funded employer can ' +
      'also be hiring up, so columns are not sums. Click a number to filter.</p>';
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

    // funding=1 is the one shareable param with no input of its own: it only
    // exists as the "Raised money" quick view. Without this, a link to that
    // view came back as the unfiltered page, which is a deep link quietly
    // dropping the very thing it was sent to show.
    if (q.get('funding') === '1' &&
        quickFind(quickButtons, function (b) { return (b.dataset.qv || '') === 'funding=1'; })) {
      setQuickView('funding=1');
    }

    Object.keys(inputs).forEach(function (key) {
      var el = inputs[key];
      if (!el || !q.has(key)) return;
      var value = q.get(key);
      if (el.tagName === 'SELECT') {
        var ok = Array.prototype.some.call(el.options, function (o) { return o.value === value; });
        if (!ok) return;
      }
      el.value = value;
    });
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
      if (value && value !== NEUTRAL[key]) params.set(key, value);
    });
    // A region is a list of country codes, so it takes the same parameter as the
    // country select. Whichever the person touched last is the one that counts —
    // silently ANDing "Europe" with "Japan" would return nothing and look broken.
    if (region) params.set('country', region);
    // A quick view is a saved set of parameters, applied on top of whatever
    // else is selected rather than replacing it: someone who has picked Europe
    // and then clicks "Raised money" means both.
    if (quickView) {
      quickView.split('&').forEach(function (pair) {
        var kv = pair.split('=');
        // since/until are already in the date inputs (setQuickView put them
        // there), so re-applying them here would let a quick view move the
        // window while the From box still showed the reader's own date.
        if (kv[0] && kv[0] !== 'since' && kv[0] !== 'until') {
          params.set(kv[0], decodeURIComponent(kv[1] || ''));
        }
      });
    }
    params.set('per_page', '50');

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

  function setQuickView(v) {
    quickView = v || null;
    quickButtons.forEach(function (o) {
      var on = (o.dataset.qv || '') === (quickView || '');
      o.classList.toggle('is-on', on);
      o.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    // A quick view carrying a date writes it into the date inputs rather than
    // straight into the querystring. "This week" has to move the box a reader
    // can see, or the page would be filtering on a window nothing on screen
    // reports.
    if (quickView) {
      quickView.split('&').forEach(function (pair) {
        var kv = pair.split('=');
        if ((kv[0] === 'since' || kv[0] === 'until') && inputs[kv[0]]) {
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
    if (quickView) {
      var qb = quickFind(quickButtons, function (b) { return (b.dataset.qv || '') === quickView; });
      if (qb) chips.push(chip('quickview', qb.textContent.trim()));
    }
    Object.keys(inputs).forEach(function (key) {
      var el = inputs[key];
      if (!el || key === 'sort') return;
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
    var out = [['label', 'count']];
    rows.forEach(function (r) {
      var name = r.querySelector('.tit-rank-name, .tit-pillar-name');
      var n = r.querySelector('.tit-rank-n, .tit-pillar-n');
      if (name && n) out.push([name.textContent.trim(), n.textContent.trim()]);
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
    { el: document.getElementById('chart-direction'), key: 'direction' }
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

  function ensureCountryOption(code) {
    var el = inputs.country;
    if (!el) return;
    var has = Array.prototype.some.call(el.options, function (o) { return o.value === code; });
    if (has) return;
    // /facets lists codes from the location column only, so an HQ-only country
    // can appear in the chart with no option to select. Add it rather than
    // letting the click silently do nothing.
    var opt = document.createElement('option');
    opt.value = code;
    opt.textContent = (TIT.countries && TIT.countries[code]) || code;
    el.appendChild(opt);
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
        ensureCountryOption(k);
        if (inputs.country) inputs.country.value = was ? '' : k;
      } else if (inputs[cf.key]) {
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
  if (location.search) refresh();
})();
