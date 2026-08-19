"""The freshness panel publishes WHEN THE DATABASE LAST TOOK A ROW.

WHY THIS FIELD EXISTS. The layoff tracker's email digest composes a talent
section from these endpoints and prints a citation under it. Chicago asks for a
last-modified stamp and APA asks for a retrieval date on a source designed to
change; the layoff half of that email carries both, out of the layoff plugin's
own last write. The talent half could carry only the retrieval date, because
nothing here published a last write, and the digest states that absence rather
than borrowing the layoff tracker's stamp. They are separate databases on
separate ingest schedules.

WHY IT IS NOT `generated`. `generated` is when the response was built, so it
moves every time anybody asks. It answers "is this reply fresh", never "is the
data fresh", and a citation needs the second one.

WHY IT IS NOT UNDER THE CALLER'S FILTER, which is the assertion below that
matters most. "When did we last collect anything" is a fact about the database.
Scoped to a WHERE clause it silently answers a different question in the same
words: a caller asking about 2024 would be told the database last changed in
2024, and read that as a dead pipeline.

These are source-shape assertions, in the same spirit as
tests/test_form_d_correction.py. A live check would need the WordPress runtime,
and the property at risk here is which SQL was written, not what the row said.
"""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
API = os.path.join(ROOT, "wordpress-plugin", "talent-intelligence-tracker",
                   "includes", "api.php")


def fresh_block():
    """The body of the include=fresh branch, and nothing else."""
    src = open(API, encoding="utf-8").read()
    start = src.index("if (trim((string) $req->get_param('include')) === 'fresh')")
    end = src.index("return tit_public_response($out);", start)
    return src[start:end]


class TheFreshPanelPublishesTheDataCut(unittest.TestCase):

    def test_the_field_is_present(self):
        self.assertIn("'data_last_changed'", fresh_block())

    def test_it_reads_the_ingest_stamp_and_not_the_response_clock(self):
        block = fresh_block()
        self.assertIn("MAX(captured_at)", block)
        self.assertIn("'generated' => gmdate('c')", block,
                      "generated is a different fact and must survive")

    def test_the_stamp_is_not_scoped_to_the_caller_filter(self):
        """The one that would be silent if it broke. A stamp under $where
        reports the newest row INSIDE a filter, which for an old slice reads as
        a database that stopped collecting."""
        line = [l for l in fresh_block().splitlines()
                if "MAX(captured_at)" in l and "$wpdb->get_var" in l]
        self.assertEqual(len(line), 1, "more than one data-cut query")
        self.assertNotIn("$where", line[0])
        self.assertNotIn("WHERE", line[0])

    def test_an_empty_table_prints_nothing_rather_than_a_guess(self):
        self.assertRegex(fresh_block(),
                         r"\$cut \? gmdate\('c', strtotime\(\$cut \. ' UTC'\)\) : ''")


if __name__ == "__main__":
    unittest.main()
