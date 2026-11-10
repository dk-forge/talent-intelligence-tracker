/*
  CONVERSION EVENTS, through the Google tag the site already loads.

  Site Kit prints the one site-wide gtag on every page. This file never loads a
  tag of its own: when window.gtag is absent (blocked, consent withheld, Site
  Kit off) every call here is a silent no-op, and nothing on the page depends
  on it.

  NO PERSONAL DATA. Event names and parameter keys are a closed list below.
  No value is ever an email, a company someone starred, a URL, a path or a
  querystring: the outbound click sends the destination's hostname only, the
  digest signup sends the form's context word ("talent") and never a field
  value. Nothing is stored in the browser.

  CAPTURE PHASE, so the star's aria-pressed is read BEFORE the dashboard's own
  handler flips it (add vs remove), and so a link that navigates away still
  reports first.
*/
(function () {
  'use strict';

  var DOWNLOADS = {
    'tit-export-csv': 'csv',
    'tit-export-json': 'json',
    'tit-export-hubspot': 'csv',
    'tit-export-salesforce': 'csv'
  };
  var KINDS = {
    'tit-export-csv': 'csv',
    'tit-export-json': 'json',
    'tit-export-hubspot': 'hubspot',
    'tit-export-salesforce': 'salesforce'
  };

  function hostOf(href) {
    try { return new URL(href).hostname.slice(0, 64); } catch (e) { return ''; }
  }

  function eventFor(target) {
    if (!target || typeof target.closest !== 'function') return null;
    var a = target.closest('a.tit-export-link');
    if (a) {
      if (a.id === 'tit-export-rss') {
        return { name: 'rss_click', params: { link_location: 'export_bar' } };
      }
      if (DOWNLOADS[a.id]) {
        return { name: 'file_download',
                 params: { file_extension: DOWNLOADS[a.id], export_kind: KINDS[a.id] } };
      }
    }
    var star = target.closest('.tit-watch-star');
    if (star) {
      return { name: 'watchlist_star',
               params: { action: star.getAttribute('aria-pressed') === 'true' ? 'remove' : 'add' } };
    }
    var src = target.closest('.tit-card-src a');
    if (src) {
      return { name: 'outbound_source_click', params: { link_domain: hostOf(src.href) } };
    }
    return null;
  }

  function formEvent(form) {
    if (!form || !form.classList || !form.classList.contains('alt-digest-form')) return null;
    var ctx = String(form.getAttribute('data-alt-context') || 'site').replace(/[^a-z_-]/gi, '').slice(0, 32);
    return { name: 'digest_signup', params: { form_context: ctx } };
  }

  function track(ev) {
    if (!ev || typeof window.gtag !== 'function') return;
    try { window.gtag('event', ev.name, ev.params); } catch (e) { /* analytics never breaks the page */ }
  }

  document.addEventListener('click', function (e) { track(eventFor(e.target)); }, true);
  document.addEventListener('submit', function (e) { track(formEvent(e.target)); }, true);

  window.TITAnalytics = { eventFor: eventFor, formEvent: formEvent };
})();
