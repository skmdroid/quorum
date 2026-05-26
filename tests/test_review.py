import os
import unittest

from quorum.diff import parse_diff
from quorum.dispatcher import dispatch
from quorum.providers import get_provider
from quorum.synthesizer import synthesize

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "sample.diff")
with open(FIX) as fh:
    SAMPLE = fh.read()


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.provider, self.model = get_provider("mock")
        self.files = parse_diff(SAMPLE)
        self.agents = dispatch(self.files, self.provider, self.provider.name, self.model)

    def test_all_agents_succeed(self):
        self.assertTrue(all(a.ok for a in self.agents))
        cats = {a.agent for a in self.agents}
        self.assertIn("security", cats)
        self.assertIn("tests", cats)  # runs because no test file is present

    def test_verdict_and_findings(self):
        result = synthesize(self.agents, self.provider, self.model)
        self.assertEqual(result.verdict, "REQUEST_CHANGES")
        titles = {f.title for f in result.findings}
        self.assertIn("Use of eval()", titles)            # security, critical
        self.assertIn("No tests for changed code", titles)  # tests agent
        self.assertTrue(any(f.severity == "critical" for f in result.findings))
        # findings are sorted most-severe first
        from quorum.schema import SEVERITY_ORDER
        order = [SEVERITY_ORDER[f.severity] for f in result.findings]
        self.assertEqual(order, sorted(order))

    def test_failing_agent_does_not_abort(self):
        class Boom:
            name = "boom"
            def complete(self, *a, **k):
                raise RuntimeError("provider exploded")
        agents = dispatch(self.files, Boom(), "mock", "mock")
        # every agent reports failure, but dispatch still returns a full panel
        self.assertTrue(agents)
        self.assertTrue(all(not a.ok for a in agents))
        result = synthesize(agents, provider=None)
        self.assertEqual(result.verdict, "APPROVE")  # no findings -> approve


if __name__ == "__main__":
    unittest.main()
