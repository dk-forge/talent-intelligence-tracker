"""The 2026-10-19 slug-collision review (ops_status [1c] listed 12 slugs)."""
import ops_status
from pipeline import vocab


def test_two_employers_that_both_lose_a_script_are_not_a_collision():
    """SK Hynix and SK Telecom both fold to 'sk' because Han is deleted. With no
    clean owner, the plugin gives each its own percent-encoded URL
    (tests/php/route_company_slugs.php phase 'disambiguated-all-lossy'), so the
    checker must stop reporting them as an unpublishable pair."""
    assert ops_status._profile_slug("sk 海力士") == ops_status._profile_slug("sk 电信") == "sk"
    assert ops_status._auto_disambiguated(["sk 海力士", "sk 电信"])
    # Still the IBM shape: one clean owner, one lossy.
    assert ops_status._auto_disambiguated(["ibm", "日本ibm"])
    # Two clean owners are a genuine ambiguity and stay refused.
    assert not ops_status._auto_disambiguated(["오픈ai", "페르소나ai"])
    assert not ops_status._auto_disambiguated(["maersk", "mærsk"])


def test_each_reviewed_pair_now_shares_one_key():
    pairs = [
        ("Dangote Petroleum Refinery & Petrochemicals FZE",
         "Dangote Petroleum Refinery and Petrochemicals FZE"),
        ("E & R Engineering Corp.", "E&R Engineering Corp."),
        ("Figeac Aero", "Figeac Aéro"), ("Flae Robotics", "FLÆ Robotics"),
        ("KPMG Australia", "KPMG Austrália"), ("L Oreal", "L Oréal"),
        ("Maersk", "Mærsk"), ("Sante", "Santé"), ("T - Mobile", "T-Mobile"),
        ("Giày Thượng Đình", "Giầy Thượng Đình"),
        ("KCU NPL 대부", "KCU NPL대부"),
    ]
    for a, b in pairs:
        assert vocab.company_key(a) == vocab.company_key(b), (a, b)


def test_distinct_han_employers_are_never_merged():
    assert vocab.company_key("SK 海力士") != vocab.company_key("SK 电信")


def test_a_non_ascii_survivor_still_has_a_publishable_slug():
    for survivor in vocab.NON_ASCII_ALIAS_SURVIVORS:
        assert survivor in vocab.EMPLOYER_KEY_ALIASES.values()
        slug = ops_status._profile_slug(survivor)
        assert slug and "%" not in slug, (survivor, slug)
