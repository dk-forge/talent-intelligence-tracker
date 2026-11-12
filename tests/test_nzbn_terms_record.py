"""The MBIE NZBN terms must stay recorded, dated, and tied to the case number."""
import pathlib

DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "SOURCE-nz-nzbn.md"


def test_terms_record_holds_the_obligations():
    t = DOC.read_text()
    for needle in ("2026-11-12", "00094961", "schedule 2", "sole trader",
                   "Change Event Search", "If-Match", "Last refreshed"):
        assert needle.lower() in t.lower(), needle
    assert "—" not in t
