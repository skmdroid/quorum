import os
import unittest

from quorum.diff import parse_diff
from quorum.dispatcher import select_agents

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "sample.diff")
with open(FIX) as fh:
    SAMPLE = fh.read()

DOCS_DIFF = (
    "diff --git a/README.md b/README.md\n"
    "--- a/README.md\n+++ b/README.md\n"
    "@@ -0,0 +1,1 @@\n+# Hello world\n"
)


class TestDiff(unittest.TestCase):
    def test_added_line_numbers(self):
        files = parse_diff(SAMPLE)
        self.assertEqual(len(files), 1)
        f = files[0]
        self.assertEqual(f.path, "app/handler.py")
        self.assertEqual(f.language, "python")
        self.assertEqual(f.added[0], (1, "import subprocess"))
        self.assertEqual(f.added[-1][0], 9)
        self.assertTrue(any("eval(cmd)" in code for _, code in f.added))

    def test_classification(self):
        f = parse_diff(SAMPLE)[0]
        self.assertFalse(f.is_test)
        self.assertFalse(f.is_doc)

    def test_docs_only_routing(self):
        docs = parse_diff(DOCS_DIFF)
        self.assertTrue(docs[0].is_doc)
        selected = select_agents(docs)
        self.assertNotIn("security", selected)
        self.assertNotIn("performance", selected)
        self.assertEqual(selected, ["style"])


if __name__ == "__main__":
    unittest.main()
