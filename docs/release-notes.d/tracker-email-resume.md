---
title: Tracker email match actions say resume
date: 2026-11-22
files: daily_digest.py, wordpress-plugin/talent-intelligence-tracker/includes/open_roles.php, wordpress-plugin/talent-intelligence-tracker/talent-intelligence-tracker.php, wordpress-plugin/talent-intelligence-tracker/data/update-log.json, tests/test_daily_digest.py, tests/test_digest_quality.py, tests/test_open_roles.py
tests: Test-first rendered HTML and text checks, digest suites, full offline pytest suite and dry-run pipeline
rollback: Revert the copy change and publish a new plugin version using the normal release process
env: none
---

The open-roles email button and text digest use resume. Source links and match-check tracking tags keep their current destinations. The companion email-composer change covers the daily layout and employer aliases.
