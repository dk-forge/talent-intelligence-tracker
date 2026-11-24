---
title: Hiring tracker helper coverage and link placement
date: 2026-11-24
version: 1.122.7
files: sentry_init.py, check_sitemap_urls.py, wordpress-plugin/talent-intelligence-tracker/includes/cross_tracker.php, wordpress-plugin/talent-intelligence-tracker/includes/atr_signup_cta.php, wordpress-plugin/talent-intelligence-tracker/includes/atr_bar.php, wordpress-plugin/talent-intelligence-tracker/includes/llms.php
tests: New offline Python and PHP harness tests; CI runs them, not run locally.
rollback: Revert this change.
env: none
---

Add offline tests for optional telemetry, sitemap status checks and conservative employer pairing. Resume match links retain source and campaign attribution and add the inline, sticky or text-summary placement. Existing public log entries and tests are unchanged.
