"""The discovery tripwire: ask an outside view what happened, diff it against
what we hold, and emit the difference as a work list.

The recall measurement (analysis/recall) answers "what fraction of a sealed
reference set do we hold?". It is honest and it is slow: a gold set has to be
assembled by hand, and it only ever names events somebody already researched.

This is its discovery-side twin. Instead of a fixed reference set it asks a
search-backed model, every run, what it can see — in the countries our own
recall measurement says we are blind in, and across every industry. Whatever it
names that we do not hold becomes a LEAD.

A lead is never a record. The model states amounts and dates with total
confidence and gets them wrong, so nothing it says is stored: the lead points
the chase collector at the employer, the chase finds the publisher's own
article, and that article goes through classify -> validate -> store like every
other candidate. No figure exists here unless a source states it.
"""
