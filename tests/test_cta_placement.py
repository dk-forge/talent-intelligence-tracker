"""Each tracker placement keeps a distinct content tag."""
import json
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PLUGIN = Path(__file__).resolve().parents[1] / "wordpress-plugin/talent-intelligence-tracker"


def test_cta_placement_has_four_attribution_values():
    php = "define('ABSPATH', 1); require " + json.dumps(str(PLUGIN / "includes/atr_signup_cta.php")) + ";"
    php += "echo json_encode(array(tit_atr_cta_url('dashboard', 'inline'), tit_atr_cta_url('sticky_bar', 'sticky'), tit_atr_cta_url('llms_txt', 'llms_txt')));"
    result = subprocess.run(["php", "-r", php], capture_output=True, text=True, check=True)
    for url, campaign, placement in zip(json.loads(result.stdout), ["dashboard", "sticky_bar", "llms_txt"], ["inline", "sticky", "llms_txt"]):
        parsed = urlparse(url)
        assert parsed.scheme == "https" and parsed.netloc == "asktherecruiter.com"
        assert parsed.path == "/resume-score"
        assert parse_qs(parsed.query) == {"utm_source": ["talent_tracker"], "utm_medium": ["cta"], "utm_campaign": [campaign], "utm_content": [placement]}


def test_renderers_pass_their_placement_to_the_shared_builder():
    inline = (PLUGIN / "includes/atr_signup_cta.php").read_text()
    sticky = (PLUGIN / "includes/atr_bar.php").read_text()
    llms = (PLUGIN / "includes/llms.php").read_text()
    assert "tit_atr_cta_url($campaign, 'inline')" in inline
    assert "tit_atr_cta_url('sticky_bar', 'sticky')" in sticky
    assert "tit_atr_cta_url('llms_txt', 'llms_txt')" in llms
