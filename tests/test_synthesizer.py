import unittest

from quorum.schema import Finding, AgentResult
from quorum.synthesizer import decide_verdict, synthesize, _dedup


def F(severity, title="t", file="a.py", line=1, category="security"):
    return Finding(file=file, line=line, severity=severity,
                   category=category, title=title)


class TestSynthesizer(unittest.TestCase):
    def test_verdict_levels(self):
        self.assertEqual(decide_verdict([]), "APPROVE")
        self.assertEqual(decide_verdict([F("info")]), "APPROVE")
        self.assertEqual(decide_verdict([F("low")]), "APPROVE")
        self.assertEqual(decide_verdict([F("medium")]), "COMMENT")
        self.assertEqual(decide_verdict([F("high")]), "REQUEST_CHANGES")
        self.assertEqual(decide_verdict([F("critical"), F("low")]), "REQUEST_CHANGES")

    def test_dedup_collapses_identical(self):
        dups = [F("high", "x"), F("high", "x"), F("low", "y")]
        self.assertEqual(len(_dedup(dups)), 2)

    def test_synthesize_sorts_and_sets_verdict(self):
        results = [AgentResult(agent="security", model="mock",
                               findings=[F("low", "l"), F("critical", "c")])]
        out = synthesize(results, provider=None)  # no provider -> fallback summary
        self.assertEqual(out.verdict, "REQUEST_CHANGES")
        self.assertEqual(out.findings[0].severity, "critical")  # most severe first
        self.assertTrue(out.summary)
        self.assertEqual(out.stats()["critical"], 1)


if __name__ == "__main__":
    unittest.main()
