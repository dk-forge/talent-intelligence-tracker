---
title: Tracker CTA links carry utm_content for the placement
date: 2026-11-25
files: includes/atr_signup_cta.php; includes/atr_bar.php; includes/llms.php; tests
tests: tests/test_atr_bar.py; tests/test_llms_txt.py; tests/test_embed_chart.py; tests/php/atr_signup_cta.php
rollback: Revert this PR.
env: none
---
tit_atr_cta_url() takes an optional placement. The inline line (also used by chart embeds) sends utm_content=inline, the sticky bar sends sticky_bar and llms.txt sends llms_txt. Source, medium, campaign and destinations are unchanged. The chart embed test pinned the exact old query; it now expects the added utm_content=inline (exact dict compare kept), because the embed uses the shared inline line and that is the intended new behaviour.
