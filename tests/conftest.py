"""Suite-wide guarantees. There is exactly one, and it is about the network.

`validate.build_signal` resolves ONE employer over the network when the row it
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


@pytest.fixture(autouse=True)
def _placement_budget_is_per_test():
    """The budget counter is per PROCESS, so it leaks between tests otherwise."""
    from pipeline import identity
    identity.reset_placement_budget()
    yield
    identity.reset_placement_budget()
