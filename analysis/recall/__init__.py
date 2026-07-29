"""Published recall measurement.

A tracker that publishes its own misses is worth more than one that claims
completeness. This package holds the sealed gold set, the matching rules, and
the loader used by `measure_recall.py` at the repository root.

The gold set was assembled from public sources BEFORE any matching ran, and
never by querying our own database. That order is the whole point: a reference
set drawn from what we already hold measures nothing.
"""
