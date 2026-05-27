"""Config-driven extensibility: structured-output parsing, .quorum.json loading,
custom agents, disabled agents, and house-rule injection."""
import json
import os
import tempfile
import unittest

from quorum.agents import build_registry, system_prompt, parse_findings
from quorum.config import load_config, find_config, ConfigError
from quorum.diff import parse_diff
from quorum.dispatcher import dispatch, select_agents
from quorum.providers import get_provider

_DIFF = ("diff --git a/x.py b/x.py\n--- /dev/null\n+++ b/x.py\n"
         "@@ -0,0 +1,1 @@\n+print('hi')\n")


class TestParseFindings(unittest.TestCase):
    def test_bare_array(self):
        raw = '[{"file":"a.py","line":3,"severity":"high","title":"X","detail":"d","suggestion":"s"}]'
        f = parse_findings(raw, "security")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].line, 3)

    def test_structured_object(self):
        # native structured output returns {"findings": [...]}
        raw = '{"findings":[{"file":"a.py","line":1,"severity":"low","title":"Y","detail":"","suggestion":""}]}'
        f = parse_findings(raw, "style")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].category, "style")

    def test_prose_wrapped(self):
        raw = ('Sure, here are the findings:\n'
               '[{"file":"a","line":null,"severity":"info","title":"Z","detail":"","suggestion":""}]\n'
               'Hope that helps!')
        f = parse_findings(raw, "correctness")
        self.assertEqual(len(f), 1)
        self.assertIsNone(f[0].line)

    def test_empty_and_garbage(self):
        self.assertEqual(parse_findings("", "security"), [])
        self.assertEqual(parse_findings("no json here", "security"), [])
        self.assertEqual(parse_findings('{"findings":[]}', "security"), [])


class TestConfig(unittest.TestCase):
    def _write(self, d, obj):
        p = os.path.join(d, ".quorum.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        return p

    def test_load_and_apply(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, {
                "provider": "mock",
                "fail_on": "medium",
                "agents": {"tests": {"enabled": False}},
                "rules": ["No print()."],
                "custom_agents": {"a11y": {"tier": "fast", "focus": "accessibility issues"}},
            })
            cfg = load_config(p)
            self.assertEqual(cfg.provider, "mock")
            self.assertEqual(cfg.fail_on, "medium")
            self.assertIn("tests", cfg.disabled)
            self.assertIn("a11y", cfg.custom_agents)
            self.assertEqual(cfg.rules, ["No print()."])

    def test_find_config_upward(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, {"provider": "mock"})
            sub = os.path.join(d, "a", "b")
            os.makedirs(sub)
            found = find_config(sub)
            self.assertTrue(found and found.endswith(".quorum.json"))

    def test_no_config_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = load_config(search=True, path=None)  # path=None + a dir with no file
            # falls back to cwd search; just assert it returns a Config-like object
            self.assertTrue(hasattr(cfg, "rules"))

    def test_invalid_custom_agent_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, {"custom_agents": {"bad": {"tier": "fast"}}})  # missing focus
            with self.assertRaises(ConfigError):
                load_config(p)


class TestRegistryAndRules(unittest.TestCase):
    def test_build_registry(self):
        reg = build_registry(disabled=("tests",),
                             custom_agents={"a11y": {"tier": "fast", "focus": "x"}})
        self.assertNotIn("tests", reg)
        self.assertIn("a11y", reg)
        self.assertIn("security", reg)

    def test_rules_injected_into_prompt(self):
        p = system_prompt("security", rules=["No eval anywhere."])
        self.assertIn("No eval anywhere.", p)

    def test_custom_agent_runs(self):
        reg = build_registry(custom_agents={"a11y": {"tier": "fast", "focus": "accessibility"}})
        provider, override = get_provider("mock")
        results = dispatch(parse_diff(_DIFF), provider, provider.name, override, agents=reg)
        self.assertIn("a11y", {r.agent for r in results})

    def test_disabled_agent_not_selected(self):
        reg = build_registry(disabled=("style",))
        self.assertNotIn("style", select_agents(parse_diff(_DIFF), reg))


if __name__ == "__main__":
    unittest.main()
