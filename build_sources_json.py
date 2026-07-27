#!/usr/bin/env python3
"""Write the plugin's sources.json from the registry.

The sources page is generated, never hand-maintained: a hand-written coverage
table drifts from reality within a week, and a table implying coverage we do not
have is a lie told in a grid. Run this whenever the registry changes; a test
asserts the two are in sync.
"""
import json
from pathlib import Path

import source_registry as registry

OUT = Path(__file__).parent / "wordpress-plugin" / "talent-intelligence-tracker" / "data" / "sources.json"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(registry.sources_manifest(), indent=1))
    live = sum(1 for s in registry.SOURCES if s.status == "live")
    print(f"{len(registry.SOURCES)} sources ({live} live) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
