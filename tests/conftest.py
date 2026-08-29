"""Suite-wide guarantees. There is exactly one, and it is about the network.

TWO now, and they are the same lesson.

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
"""

import os

import pytest

os.environ["TIT_IDENTITY_LOOKUP"] = "off"
os.environ["TIT_PLUGIN_PREFLIGHT"] = "off"


@pytest.fixture(autouse=True)
def _placement_budget_is_per_test():
    """The budget counter is per PROCESS, so it leaks between tests otherwise."""
    from pipeline import identity
    identity.reset_placement_budget()
    yield
    identity.reset_placement_budget()
