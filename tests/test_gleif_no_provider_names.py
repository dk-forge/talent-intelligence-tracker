"""company_lei.json must not carry a banned data-provider name (2026-10-13).

The first GLEIF refresh (eb324dc) wrote an employer whose name contains a
banned provider name into the plugin's public data file, and
test_no_provider_names went red on main. The writer now drops such keys, the
same rule the redactor applies everywhere else. No name is spelled here.
"""
import json

import gleif_lei
from pipeline import provider_names


def test_write_output_drops_a_key_holding_a_banned_name(tmp_path):
    banned = provider_names.BANNED[0]
    out = tmp_path / "company_lei.json"
    gleif_lei.write_output(out, {f"{banned} technologies": "LEI1",
                                 "acme corp": "LEI2"}, "2026-10-13")
    doc = json.loads(out.read_text())
    assert doc["companies"] == {"acme corp": "LEI2"}
    assert not provider_names.contains(out.read_text())
