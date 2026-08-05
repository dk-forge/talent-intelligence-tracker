"""The dashboard prints the SHARED email-digest signup, safely.

The subscriber store, the double-opt-in flow and the sender live in the sibling
plugin (AI Layoff Tracker, includes/subscribe.php): both plugins run on one
WordPress install, and one person gets ONE consent record, not two. This side
only renders the form.

What is pinned here is the ISOLATION promise from the top of
talent-intelligence-tracker.php: no shared code, no require across plugins. The
embed must be a function_exists()-guarded call, so a missing or mid-deploy
sibling renders nothing and fatals nothing.
"""
import os
import re
import unittest

HERE = os.path.dirname(__file__)
PLUGIN = os.path.abspath(os.path.join(
    HERE, "..", "wordpress-plugin", "talent-intelligence-tracker"))
SHORTCODES = os.path.join(PLUGIN, "includes", "shortcodes.php")


class DigestSignupEmbed(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(SHORTCODES, encoding="utf-8") as fh:
            cls.src = fh.read()

    def test_the_dashboard_prints_the_shared_signup_form(self):
        self.assertIn("alt_digest_subscribe_form", self.src,
                      "the talent dashboard must render the shared digest signup")

    def test_every_call_is_function_exists_guarded(self):
        """A bare call fatals the whole plugin whenever the sibling is absent
        or an FTP deploy is mid-upload. Every call site must sit inside a
        function_exists() guard."""
        for m in re.finditer(r"alt_digest_subscribe_form", self.src):
            window = self.src[max(0, m.start() - 400):m.start()]
            if "function_exists('alt_digest_subscribe_form')" in window:
                continue
            # the guard's own condition names the function too; skip it
            after = self.src[m.end():m.end() + 2]
            if after.startswith("')"):
                continue
            self.fail("alt_digest_subscribe_form is called without a "
                      "function_exists() guard")

    def test_no_cross_plugin_require(self):
        """Rendering the sibling's form must never become loading the
        sibling's code."""
        self.assertNotRegex(self.src, r"(require|include)[^\n]*ai-layoff-tracker",
                            "no require/include may cross the plugin boundary")

    def test_no_em_or_en_dashes_in_the_embed_block(self):
        idx = self.src.index("alt_digest_subscribe_form")
        block = self.src[max(0, idx - 800):idx + 200]
        for ch in ("—", "–"):
            self.assertNotIn(ch, block)


if __name__ == "__main__":
    unittest.main()
