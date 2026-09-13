"""Suite-wide guarantees. There is exactly one, and it is about the network.

THREE now, and they are all the same lesson.

1. THE PLUGIN PREFLIGHT. `publish.check_plugin_version` GETs the live site to
   refuse a write into a schema the plugin has not got yet. Several tests in
   tests/test_publish.py set WP_SITE_URL to the REAL production URL and stub
   only `_post_batch`, so with the preflight armed the offline suite asks
   asktherecruiter.com for its version on every publish test. It is off here
   and tests/test_plugin_preflight.py turns it back on for itself with a
   stubbed session.

2. THE IDENTITY LOOKUP. `validate.build_signal` resolves ONE employer over the network when the row it
is building would otherwise be stored with no country in either column
(pipeline/identity.place_if_unplaced). That is deliberate on the ingestion
path and intolerable in a unit test: five existing tests hand `build_signal` a
real connection, and any of them could reach Wikidata depending on what the
fixture employer happens to be called.

So the suite runs with the lookup off, and the tests that are ABOUT the lookup
turn it back on for themselves and stub the resolver. A test that reaches the
open internet is not a unit test, and one that reaches it only sometimes is
worse than one that always does.

3. THE PUBLISHED-FIGURE CHECKS. `published_figures.check_all()` is five
   read-only GETs against the live public dashboard, and three tests in
   tests/test_rejection_audit_surfaced.py spawn `ops_status.py` as a SUBPROCESS
   to read one section it prints. `ops_status.main()` runs every section,
   including that one, so those three tests made 15 requests to
   asktherecruiter.com per suite run. Nothing in any test file named the host,
   the module or the URL, and not one frame of the stack was a test frame.

   That suite runs on `cron: '43 * * * *'` -- hourly, forever -- plus on every
   push and every pull request. On 2026-09-12 and again on 2026-09-13 the shared
   ChemiCloud account that serves asktherecruiter.com timed out every PHP
   request for 10s account-wide, twice in twelve hours, under load. Our own
   hourly suite was part of that load.

   `TIT_LIVE_FIGURES=off` makes `published_figures.Ctx` refuse rather than
   fetch, and every check turns that refusal into a loud UNKNOWN that names the
   switch. It is set here rather than in the three tests because the defect was
   never in those three tests: any test that spawns any of this repository's
   scripts inherits this environment and is offline by construction. A child
   process inherits os.environ, which is why setting it here reaches a
   subprocess at all.

   PASS / FAIL / UNKNOWN are three states in this project and the absence of a
   signal is never a pass, so switching a live check off is only tolerable while
   the run SAYS it did not run it. tests/test_offline_suite_is_offline.py holds
   all of that, positive control included.
"""

import os

import pytest

os.environ["TIT_IDENTITY_LOOKUP"] = "off"
os.environ["TIT_PLUGIN_PREFLIGHT"] = "off"
os.environ["TIT_LIVE_FIGURES"] = "off"


@pytest.fixture(autouse=True)
def _placement_budget_is_per_test():
    """The budget counter is per PROCESS, so it leaks between tests otherwise."""
    from pipeline import identity
    identity.reset_placement_budget()
    yield
    identity.reset_placement_budget()
