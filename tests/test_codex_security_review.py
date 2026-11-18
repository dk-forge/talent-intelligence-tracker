"""Tests for codex_security_review.py (dk-forge/todo#1852): path matching and
the once-per-head-SHA marker. Stdlib unittest; pytest collects it too."""
import importlib.util
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "codex_security_review.py"
_spec = importlib.util.spec_from_file_location("codex_security_review", _SRC)
csr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csr)

RISKY = ["wordpress-plugin/talent-intelligence-tracker/includes/rest-api.php", "opsmail.py", "wordpress-plugin/talent-intelligence-tracker/includes/contact-form.php", "wordpress-plugin/mu-plugins/tit.php"]
SAFE = ["collectors/gdelt.py", "tests/test_author_is_research_team.py", "docs/README.md", "build_board_series.py"]


class PathMatching(unittest.TestCase):
    def test_risky_paths_match(self):
        for p in RISKY:
            self.assertEqual(csr.risky_files([p]), [p], p)

    def test_safe_paths_do_not_match(self):
        for p in SAFE:
            self.assertEqual(csr.risky_files([p]), [], p)

    def test_this_gate_never_flags_itself(self):
        own = [".github/workflows/codex-security-review.yml", "x/codex_security_review.py",
               "x/tests/test_codex_security_review.py"]
        self.assertEqual(csr.risky_files(own), [])


class Marker(unittest.TestCase):
    def fake(self, comments):
        posted = []

        def get_all(url, token):
            if url.endswith("/files"):
                return [{"filename": RISKY[0]}, {"filename": SAFE[0]}]
            return [{"body": b, "user": {"login": "github-actions[bot]"}} for b in comments]

        def post(url, token, body):
            posted.append(body)
            comments.append(body)

        return get_all, post, posted

    def test_posts_once_per_sha_then_never_again(self):
        comments = []
        get_all, post, posted = self.fake(comments)
        self.assertEqual(csr.run("o/r", 1, "abc123", "t", get_all, post), "posted")
        self.assertTrue(posted[0].startswith("@codex security review"))
        self.assertIn(csr.marker("abc123"), posted[0])
        self.assertEqual(csr.run("o/r", 1, "abc123", "t", get_all, post), "duplicate")
        self.assertEqual(len(posted), 1)

    def test_new_head_sha_gets_a_new_request(self):
        comments = [csr.build_body("abc123", [RISKY[0]])]
        get_all, post, posted = self.fake(comments)
        self.assertEqual(csr.run("o/r", 1, "def456", "t", get_all, post), "posted")
        self.assertEqual(len(posted), 1)

    def test_marker_from_someone_else_does_not_suppress(self):
        posted = []

        def get_all(url, token):
            if url.endswith("/files"):
                return [{"filename": RISKY[0]}]
            return [{"body": csr.marker("abc"), "user": {"login": "someone"}}]

        self.assertEqual(csr.run("o/r", 1, "abc", "t", get_all, lambda u, t, b: posted.append(b)), "posted")

    def test_rename_out_of_a_risky_path_counts(self):
        posted = []

        def get_all(url, token):
            if url.endswith("/files"):
                return [{"filename": SAFE[0], "previous_filename": RISKY[0]}]
            return []

        self.assertEqual(csr.run("o/r", 1, "abc", "t", get_all, lambda u, t, b: posted.append(b)), "posted")

    def test_no_risky_path_posts_nothing(self):
        posted = []
        get_all = lambda url, token: [{"filename": SAFE[0]}] if url.endswith("/files") else []
        self.assertEqual(csr.run("o/r", 1, "abc", "t", get_all, posted.append), "skip")
        self.assertEqual(posted, [])


if __name__ == "__main__":
    unittest.main()
