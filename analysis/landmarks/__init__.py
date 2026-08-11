"""The landmark guard.

A landmark is an event that no funding tracker could defend missing: the
largest disclosed round of its quarter, with the company's own announcement
behind it. `landmarks.py` loads and validates the committed set,
`check.py` decides HELD / WRONG_AMOUNT / MISSING per entry.

Why this exists, in one paragraph. On 2026-08-04 the owner measured that the
three biggest private AI rounds ever recorded were not on this site. Two were
never stored; one was stored, correct, and withheld by an unreviewed publish
quarantine for five days. Nothing in the system had an opinion about any of
it, because every check we had asked "did the collector run" or "does the
corpus hold a sample of the world". Neither question can notice a specific
enormous event going missing, and the answer arrived by a human looking.
"""
