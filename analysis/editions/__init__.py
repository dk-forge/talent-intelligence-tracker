"""What a Google News edition actually returns.

Measurement, never collection: nothing here writes a row, calls a model, or
costs a cent. `gl=` is chosen by us at fetch time and it is easy to assume it
selects a country. On 2026-08-01 it was measured instead, and seventeen English
non-US editions left the rotation as a result.

Run it rather than believing the numbers written down beside
`source_registry.WITHDRAWN_ENGLISH_EDITIONS`:

    python3 -m analysis.editions.measure
"""
