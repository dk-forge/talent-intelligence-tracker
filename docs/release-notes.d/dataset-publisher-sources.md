---
title: Name the hiring Dataset publisher and link its sources
date: 2026-11-24
files: wordpress-plugin/talent-intelligence-tracker/includes/dataset_schema.php, tests/test_dataset_schema.py, wordpress-plugin/talent-intelligence-tracker/talent-intelligence-tracker.php, wordpress-plugin/talent-intelligence-tracker/data/update-log.json
tests: Tests written first for populated and empty datasets, source-text fields and script safety; tests not run locally, CI runs them
rollback: Revert this schema change before any later deployment
env: none
---

The hiring Dataset names Ask The Recruiter Research as publisher and links the existing sources page. Creator and other schema nodes are unchanged. Pushed, not deployed.
