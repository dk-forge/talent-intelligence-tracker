"""Which reference sets exist, and where each one's everything lives.

ONE definition, imported by the measurement (`measure_recall.py`), the health
surface (`health_digest.py`, and the page data this writes) and the session
check (`ops_status.py`). Three copies of "where do the US results live" is how
a dashboard ends up green about a file nothing has written for a month.

A FAMILY is a population, not a filter. `world` measures the tracker against
events sampled from everywhere; `us` measures it against events sampled from
inside the United States and broken out by metro. They are separate families
rather than a slice of one set because the two want opposite shape guards: the
world set FAILS validation if it is more than 45% one country, and a US set is
100% one country by construction. Running them as one set would mean either the
world guard is off or the US set is illegal, and both of those are worse than
two directories.

They never share a directory, and that is load-bearing rather than tidy.
`goldset.latest_path()` takes the newest `goldset-*.json` in a directory as the
set to measure; a US set dropped into `analysis/recall/` would silently become
THE set the worldwide measurement runs against, and the published worldwide
number would become a US number without one line of code changing. The US
family lives under `analysis/recall/us/` for that reason, and
`tests/test_recall_us.py` asserts the separation rather than trusting it.
"""

from __future__ import annotations

import os

from analysis.recall import goldset

DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(DIR))
PLUGIN_DATA_DIR = os.path.join(
    ROOT, "wordpress-plugin", "talent-intelligence-tracker", "data")


class Family:
    """One measurable population.

    Everything a caller needs to run, store, gate and publish a measurement of
    that population, so that no caller has to reconstruct a path from a family
    name and get it subtly wrong.
    """

    def __init__(self, id, label, subdir, shape, spread_key, spread_label,
                 breakdowns, health_source, plugin_file, page_anchor,
                 sampling_note):
        self.id = id
        self.label = label
        self.goldset_dir = os.path.join(DIR, subdir) if subdir else DIR
        self.results_dir = os.path.join(self.goldset_dir, "results")
        self.shape = shape
        # The dimension the anti-flattery guards and the ratchet measure spread
        # over. "country" worldwide; "metro" inside one country, because within
        # the US the interesting variation is between hiring markets and there
        # is exactly one country to count.
        self.spread_key = spread_key
        self.spread_label = spread_label
        # Extra `by_*` groups this family's summary carries beyond the shared
        # ones. Read by thresholds.CELL_GROUPS so a collapsed metro reds the
        # gate the same way a collapsed source type does.
        self.breakdowns = tuple(breakdowns)
        self.health_source = health_source
        self.plugin_data = os.path.join(PLUGIN_DATA_DIR, plugin_file)
        self.page_anchor = page_anchor
        self.sampling_note = sampling_note

    @property
    def is_default(self) -> bool:
        return self.goldset_dir == DIR

    def latest_goldset(self) -> str:
        return goldset.latest_path(self.goldset_dir)

    def __repr__(self) -> str:                      # pragma: no cover - debug aid
        return f"<Family {self.id}>"


WORLD = Family(
    id="world",
    label="Worldwide",
    subdir="",
    shape=goldset.REQUIRED_SHAPE,
    spread_key="country",
    spread_label="countries",
    breakdowns=(),
    health_source="recall",
    plugin_file="recall.json",
    page_anchor="worldwide",
    sampling_note=(
        "Independent research passes, one per world region, each forbidden "
        "from consulting this tracker or its database."),
)

US = Family(
    id="us",
    label="United States",
    subdir="us",
    shape=goldset.US_REQUIRED_SHAPE,
    spread_key="metro",
    spread_label="metros",
    breakdowns=("by_metro",),
    health_source="recall_us",
    plugin_file="recall-us.json",
    page_anchor="united-states",
    sampling_note=(
        "Independent research passes, one per metro and event type, each "
        "forbidden from consulting this tracker or its database."),
)

ALL = (WORLD, US)
BY_ID = {family.id: family for family in ALL}
DEFAULT = WORLD


def by_id(name: str) -> Family:
    try:
        return BY_ID[name]
    except KeyError:
        raise SystemExit(
            f"unknown reference-set family {name!r}. Known: "
            f"{', '.join(sorted(BY_ID))}") from None
