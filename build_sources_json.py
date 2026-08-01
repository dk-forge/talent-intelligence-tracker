#!/usr/bin/env python3
"""Write the plugin's sources.json from the registry.

The sources page is generated, never hand-maintained: a hand-written coverage
table drifts from reality within a week, and a table implying coverage we do not
have is a lie told in a grid. Run this whenever the registry changes; a test
asserts the two are in sync.

THIS IS THE RENDER BOUNDARY, so it is where the no-em-dash rule is enforced.
`data/sources_catalogue.csv` is two things at once: an engineering log of what
was probed on which host, and the copy for a public page. Em-dashes are fine in
the first and banned in the second, and nothing marked which was which - so on
2026-07-31 two of the thirteen dashes in the catalogue reached the live sources
page and sat there.

The check REFUSES rather than rewriting. A silent substitution would put words
on a public page that nobody chose, and one replacement does not fit every
sentence: of those two, one wanted a full stop and the other wanted a comma.
Naming the field and making the author repair it is the same reasoning as
everything else here failing loudly instead of guessing.
"""
import json
import sys
from pathlib import Path

import source_registry as registry

OUT = Path(__file__).parent / "wordpress-plugin" / "talent-intelligence-tracker" / "data" / "sources.json"

# Em dash and en dash. Hyphen-minus is ordinary punctuation and is not banned.
BANNED_DASHES = ("—", "–")


def dash_offences(manifest):
    """Every (source, field, fragment) whose text carries a banned dash.

    Returns all of them rather than the first: an author repairing one entry
    should see the whole list instead of rebuilding once per offence.
    """
    found = []
    for i, source in enumerate(manifest):
        label = source.get("name") or source.get("id") or "entry %d" % i
        for field, value in source.items():
            if not isinstance(value, str):
                continue
            for dash in BANNED_DASHES:
                at = value.find(dash)
                if at == -1:
                    continue
                start = max(0, at - 60)
                found.append((label, field,
                              value[start:at + 60].replace("\n", " ")))
                break
    return found


def main() -> int:
    manifest = registry.sources_manifest()

    offences = dash_offences(manifest)
    if offences:
        print("REFUSING TO BUILD: %d field(s) carry an em or en dash, and this "
              "file feeds a public page." % len(offences), file=sys.stderr)
        for label, field, fragment in offences:
            print("\n  %s  (%s)" % (label, field), file=sys.stderr)
            print("    ...%s..." % fragment, file=sys.stderr)
        print("\nRewrite the text in data/sources_catalogue.csv. Nothing is "
              "substituted automatically because the right repair is a full "
              "stop in some sentences and a comma in others, and a public page "
              "should not carry words nobody chose.", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=1))
    live = sum(1 for s in manifest if s["status"] == "live")
    print(f"{len(manifest)} sources ({live} live) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
