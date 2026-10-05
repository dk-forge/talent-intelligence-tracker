/*
 * Reference sections (includes/reference_render.php): redraw on a control
 * change. The server already rendered the default view, so this file only
 * runs once a reader touches a control, and it reads the public
 * /reference/<source> route named in each section's data-api attribute (never
 * an inline object: Autoptimize sweeps those; CLAUDE.md gotcha 10).
 * Everything is built with DOM calls and textContent, never innerHTML.
 */
(function () {
  'use strict';
  var SVG = 'http://www.w3.org/2000/svg';
  var MAX_COUNTRIES = 6;
  var NAMES = { US: 'United States', GB: 'United Kingdom', CA: 'Canada', DE: 'Germany',
    FR: 'France', AU: 'Australia', IE: 'Ireland', NL: 'Netherlands', ES: 'Spain',
    IT: 'Italy', EA: 'Euro area' };
  var cache = {};

  function load(url) {
    if (!cache[url]) {
      cache[url] = fetch(url, { credentials: 'omit' }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
    }
    return cache[url];
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function svgEl(tag, attrs, text) {
    var e = document.createElementNS(SVG, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }
  function fmt(n, d) {
    return Number(n).toLocaleString('en-US', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
  }
  function signed(v) {
    if (typeof v !== 'number') return 'n/a';
    return (v > 0 ? '+' : v < 0 ? '−' : '') + fmt(Math.abs(v), 1);
  }
  function ts(d) { return Date.parse(d + 'T00:00:00Z'); }
  function iso(t) { return new Date(t).toISOString().slice(0, 10); }

  /* Mirrors tit_hd_points(): weekly when present, else three points. */
  function points(s) {
    if (!s || typeof s.index !== 'number' || !s.as_of) return [];
    if (s.weekly && s.weekly.length >= 2) {
      return s.weekly.filter(function (p) { return typeof p[1] === 'number'; });
    }
    var out = [], t = ts(s.as_of);
    if (typeof s.chg_52w === 'number') out.push([iso(t - 364 * 864e5), +(s.index - s.chg_52w).toFixed(2)]);
    if (typeof s.chg_4w === 'number') out.push([iso(t - 28 * 864e5), +(s.index - s.chg_4w).toFixed(2)]);
    out.push([s.as_of, s.index]);
    return out;
  }

  function chart(lines, label) {
    var w = 640, h = 240, l = 44, r = 10, t = 12, b = 26, vals = [], dates = [];
    lines.forEach(function (ln) { ln.pts.forEach(function (p) { vals.push(p[1]); dates.push(p[0]); }); });
    if (vals.length < 2) return null;
    dates.sort();
    var t0 = ts(dates[0]), t1 = ts(dates[dates.length - 1]), span = Math.max(1, t1 - t0);
    var min = Math.floor(Math.min.apply(null, vals) / 5) * 5;
    var max = Math.ceil(Math.max.apply(null, vals) / 5) * 5;
    if (max <= min) max = min + 5;
    function x(d) { return (l + (ts(d) - t0) / span * (w - l - r)).toFixed(1); }
    function y(v) { return (t + (1 - (v - min) / (max - min)) * (h - t - b)).toFixed(1); }
    var s = svgEl('svg', { 'class': 'tit-hd-svg', viewBox: '0 0 ' + w + ' ' + h, width: '100%',
      role: 'img', preserveAspectRatio: 'xMidYMid meet', 'aria-label': label });
    [min, (min + max) / 2, max].forEach(function (g) {
      s.appendChild(svgEl('line', { 'class': 'tit-hd-grid', x1: l, x2: w - r, y1: y(g), y2: y(g) }));
      s.appendChild(svgEl('text', { 'class': 'tit-hd-ax', x: l - 6, y: +y(g) + 4, 'text-anchor': 'end' }, fmt(g)));
    });
    if (min <= 100 && max >= 100) {
      s.appendChild(svgEl('line', { 'class': 'tit-hd-base', x1: l, x2: w - r, y1: y(100), y2: y(100) }));
    }
    s.appendChild(svgEl('text', { 'class': 'tit-hd-ax', x: l, y: h - 6 }, dates[0]));
    s.appendChild(svgEl('text', { 'class': 'tit-hd-ax', x: w - r, y: h - 6, 'text-anchor': 'end' }, dates[dates.length - 1]));
    lines.forEach(function (ln, i) {
      s.appendChild(svgEl('polyline', { 'class': 'tit-hd-line tit-hd-s' + i,
        points: ln.pts.map(function (p) { return x(p[0]) + ',' + y(p[1]); }).join(' ') }));
    });
    return s;
  }

  function initDemand(sec) {
    var boxes = sec.querySelectorAll('input[name="tit-hd-c"]');
    var occ = sec.querySelector('.tit-hd-occ');
    var status = sec.querySelector('.tit-hd-status');
    function draw() {
      var picked = [];
      boxes.forEach(function (b) { if (b.checked) picked.push(b.value); });
      boxes.forEach(function (b) { b.disabled = !b.checked && picked.length >= MAX_COUNTRIES; });
      var cat = occ ? occ.value : '';
      load(sec.getAttribute('data-api')).then(function (data) {
        var lines = [], missing = [];
        picked.forEach(function (cc) {
          var blk = (data.countries || {})[cc] || {};
          var s = cat ? (blk.categories || {})[cat] : blk.total;
          var pts = points(s);
          if (pts.length < 2) { missing.push(NAMES[cc] || cc); return; }
          lines.push({ cc: cc, name: NAMES[cc] || cc, pts: pts, s: s });
        });
        var box = sec.querySelector('.tit-hd-chart');
        var legend = sec.querySelector('.tit-hd-legend');
        var miss = sec.querySelector('.tit-hd-missing');
        box.textContent = ''; legend.textContent = '';
        var svg = chart(lines, 'Indeed job postings index, ' + (cat || 'all postings') + ', ' +
          lines.map(function (l) { return l.name; }).join(', '));
        if (svg) box.appendChild(svg);
        lines.forEach(function (ln, i) {
          var li = el('li');
          li.appendChild(el('span', 'tit-hd-key tit-hd-k' + i)).setAttribute('aria-hidden', 'true');
          li.appendChild(document.createTextNode(' '));
          li.appendChild(el('strong', null, ln.name));
          li.appendChild(document.createTextNode(' ' + fmt(ln.s.index, 1) + ' '));
          li.appendChild(el('span', 'tit-hd-chg', signed(ln.s.chg_4w) + ' in 4 weeks, ' + signed(ln.s.chg_52w) + ' in a year'));
          legend.appendChild(li);
        });
        miss.hidden = !missing.length;
        miss.textContent = missing.length ? 'No published series for: ' + missing.join(', ') + '.' : '';
        status.textContent = lines.length ? '' : 'Pick at least one country with a published series.';
      }).catch(function () { status.textContent = 'Could not load the series. The chart above is the default view.'; });
    }
    boxes.forEach(function (b) { b.addEventListener('change', draw); });
    if (occ) occ.addEventListener('change', draw);
  }

  function initSponsors(sec) {
    var q = sec.querySelector('.tit-h1b-q'), role = sec.querySelector('.tit-h1b-role'),
      state = sec.querySelector('.tit-h1b-state'), status = sec.querySelector('.tit-hd-status');
    var form = sec.querySelector('form');
    var n = parseInt(sec.getAttribute('data-rows'), 10) || 25;
    if (form) form.addEventListener('submit', function (e) { e.preventDefault(); });
    function top(map) {
      var best = '', v = -1;
      for (var k in (map || {})) if (map[k] > v) { v = map[k]; best = k; }
      return best;
    }
    function money(v) { return typeof v === 'number' && v > 0 ? '$' + fmt(v) : 'n/a'; }
    function td(text, cls) { return el('td', cls, text); }
    function draw() {
      load(sec.getAttribute('data-api')).then(function (data) {
        var term = (q && q.value || '').trim().toLowerCase();
        var r = role ? role.value : '', s = state ? state.value : '';
        var rows = (data.top_employers || []).filter(function (e) {
          if (term && String(e.employer).toLowerCase().indexOf(term) < 0) return false;
          if (r && !(e.roles && e.roles[r])) return false;
          if (s && !(e.states && e.states[s])) return false;
          return true;
        });
        var key = r ? function (e) { return e.roles[r]; } : s ? function (e) { return e.states[s]; } : null;
        if (key) rows.sort(function (a, b) { return key(b) - key(a); });
        var body = sec.querySelector('tbody');
        body.textContent = '';
        rows.slice(0, n).forEach(function (e) {
          var tr = el('tr');
          tr.appendChild(td(e.employer));
          tr.appendChild(td(fmt(e.cases), 'tit-num'));
          tr.appendChild(td(fmt(e.certified || 0), 'tit-num'));
          tr.appendChild(td(top(e.roles), 'tit-h1b-opt'));
          tr.appendChild(td(top(e.states), 'tit-h1b-opt'));
          tr.appendChild(td(money(e.wage_median), 'tit-num tit-h1b-opt'));
          body.appendChild(tr);
        });
        status.textContent = rows.length
          ? 'Showing ' + Math.min(n, rows.length) + ' of ' + rows.length + ' matching employers' +
            (key ? ', ranked by certified filings for that filter.' : '.')
          : 'No employer in the top ' + (data.top_employers || []).length + ' matches.';
      }).catch(function () { status.textContent = 'Could not load the full list. The table shows the top employers.'; });
    }
    var timer;
    if (q) q.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(draw, 200); });
    if (role) role.addEventListener('change', draw);
    if (state) state.addEventListener('change', draw);
  }

  function start() {
    var d = document.getElementById('tit-hiring-demand');
    if (d && window.fetch) initDemand(d);
    var s = document.getElementById('tit-h1b-sponsors');
    if (s && window.fetch) initSponsors(s);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
