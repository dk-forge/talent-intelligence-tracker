---
title: Hiring tracker helper coverage
date: 2026-11-24
files: sentry_init.py, check_sitemap_urls.py, wordpress-plugin/talent-intelligence-tracker/includes/cross_tracker.php
tests: New offline Python and PHP harness tests; CI runs them, not run locally.
rollback: Revert this change.
env: none
---

Add offline tests for optional telemetry, sitemap status checks and conservative employer pairing. Existing public log entries and tests are unchanged.
