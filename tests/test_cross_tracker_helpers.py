"""Synthetic employer identities, without sibling HTTP or WordPress."""
import json
import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "wordpress-plugin/talent-intelligence-tracker"


def test_normalisation_pairing_and_default_off():
    php = """define('ABSPATH', 1);
function get_option($key, $default) { return $default; }
function apply_filters($key, $value) { return $value; }
""" + "require " + json.dumps(str(PLUGIN / "includes/cross_tracker.php")) + ";" + """
echo json_encode(array(
 tit_company_key_like('  Example & Partners, Inc. '),
 tit_company_key_like('Example GmbH'), tit_company_key_like('---'),
 tit_pair_is_same_employer('example', 'Example Corporation'),
 tit_pair_is_same_employer('example', 'Example Subsidiary Ltd'),
 tit_pair_is_same_employer('example', 'Unrelated Example Ltd'),
 tit_pair_is_same_employer('', ''), tit_cross_tracker_enabled(),
 tit_cross_tracker_html(array())
));"""
    result = subprocess.run(["php", "-r", php], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == ["example and partners", "example", "", True, False, False, False, False, ""]
