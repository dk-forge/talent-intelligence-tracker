"""Job-ad aggregators are dropped before any paid call (coverage audit 2026-09-24).

The 2026-09-22 run log shows alwadifa-club.com and dimajadid.com (Moroccan
recruitment-ad boards) reaching the classifier on the bare Arabic "توظيف"
query. A job advert is not intelligence about an employer's plans, and every
one of them costs a gate call to reject. The domain is known BEFORE resolution
(the RSS <source url>), so the check is free.
"""

from __future__ import annotations

from pipeline import prefilter


def test_known_job_ad_hosts_are_blocked():
    for url in ("https://www.alwadifa-club.com/", "https://dimajadid.com",
                "https://in.indeed.com/viewjob?jk=1", "https://www.naukri.com/x",
                "https://sub.bayt.com/en/job"):
        assert prefilter.job_ad_domain(url), url


def test_news_hosts_and_lookalikes_are_not_blocked():
    for url in ("https://www.reuters.com/business/x", "https://technode.com",
                "https://notindeed.com.example.org/", "", None,
                "https://www.theguardian.com/money/indeed-story"):
        assert not prefilter.job_ad_domain(url), url


def test_item_check_reads_both_the_source_and_the_link():
    assert prefilter.job_ad_item({"source_url": "https://alwadifa-club.com"})
    assert prefilter.job_ad_item({"source_url": "https://news.google.com/rss/x",
                                  "url": "https://www.naukri.com/job"})
    assert not prefilter.job_ad_item({"source_url": "https://www.ft.com"})


def test_the_collector_gate_drops_a_job_ad_before_the_keyword_filter():
    import run_collect
    ad = {"source_url": "https://www.alwadifa-club.com",
          "raw_text": "Acme is hiring 50 engineers"}
    ok, reason = run_collect.free_gate(ad)
    assert not ok and "job-ad" in reason
    news = dict(ad, source_url="https://www.reuters.com")
    assert run_collect.free_gate(news)[0]
    assert run_collect.free_gate(ad, skip_prefilter=True)[0]
