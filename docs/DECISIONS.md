# Owner rulings

One line per ruling, newest last. A ruling here is the owner's, relayed and
dated; code that implements one names the line.

- **2026-11-16** — Current, published `uk_paygap` and `sec_execcomp` rows whose
  event date (`published_date`) is older than 400 days may move to a read-only
  archive, to bring `data/talent_intel.db` under backup_check's 90 MiB line and
  get main green. Owner typed "you have my okay" in chat, answering the
  question raised by the "TIT 100%" session. Implemented by
  `pipeline/archive.py` (`RULED_COLLECTORS`, `RULED_AGE_DAYS`).
