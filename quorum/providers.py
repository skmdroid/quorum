"""Provider-agnostic LLM layer.

Every provider implements `complete(system, user, model, schema) -> str`. When a
`schema` is passed and the provider supports it, the reply is constrained to
that schema with **native structured output** (OpenAI `json_schema`, Anthropic
forced tool-use, Ollama `format`) — so we get schema-valid JSON instead of
hoping the prose parses. Providers that can't enforce a schema (the `claude-cli`
subprocess, arbitrary local models) ignore it and lean on the prompt's JSON
instruction plus the tolerant parser in `agents.parse_findings`.

Real agents bring their own keys (BYO); the built-in MockProvider is
deterministic and needs no network — it powers the test suite and a zero-setup
first run.

Spec syntax for `get_provider`:  "mock", "claude-cli", "anthropic",
"openai", "ollama", or "<name>:<model>" to pin a single model.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

# Default (strong, fast) model per provider. The dispatcher routes
# security/performance/correctness -> strong, and tests/style -> fast.
PROVIDER_TIERS = {
    "mock":       {"strong": "mock", "fast": "mock"},
    "claude-cli": {"strong": "opus", "fast": "haiku"},
    "anthropic":  {"strong": "claude-sonnet-4-5", "fast": "claude-3-5-haiku-latest"},
    "openai":     {"strong": "gpt-4o", "fast": "gpt-4o-mini"},
    "ollama":     {"strong": None, "fast": None},
}


class ProviderError(RuntimeError):
    pass


class Provider:
    name = "base"

    def complete(self, system: str, user: str, model: str | None = None,
                 schema: dict | None = None) -> str:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Mock provider: a deterministic, offline "reviewer" used by tests and the
# zero-setup demo. It scans the added lines for well-known smells per category.
# --------------------------------------------------------------------------- #
_MOCK_PATTERNS = {
    "security": [
        ("eval(", "critical", "Use of eval()",
         "eval() on dynamic input allows arbitrary code execution.",
         "Parse explicitly or use ast.literal_eval()."),
        ("exec(", "critical", "Use of exec()",
         "exec() can run arbitrary code.", "Refactor to explicit logic."),
        ("shell=true", "high", "subprocess with shell=True",
         "Shell injection risk when arguments are untrusted.",
         "Pass args as a list with shell=False."),
        ("pickle.load", "high", "Unsafe deserialization",
         "pickle can execute arbitrary code on load.",
         "Use json or a safe serializer."),
        ("verify=false", "high", "TLS verification disabled",
         "Disabling certificate verification enables MITM.", "Remove verify=False."),
        ("md5(", "medium", "Weak hash (MD5)",
         "MD5 is unsuitable for security use.", "Use SHA-256+ / bcrypt for passwords."),
        ("password", "medium", "Possible hardcoded secret",
         "Secrets committed to source can leak via VCS history.",
         "Load from an environment variable or secret manager."),
        ("api_key", "medium", "Possible hardcoded secret",
         "API keys in source can leak.", "Load from env / secret manager."),
    ],
    "performance": [
        ("select *", "medium", "SELECT * query",
         "Fetching all columns wastes I/O and breaks on schema change.",
         "Select only the columns you use."),
        ("time.sleep(", "low", "Blocking sleep",
         "Blocking sleep can stall a thread or the event loop.",
         "Use async sleep or a scheduler."),
    ],
    "correctness": [
        ("== none", "low", "Comparison to None with ==",
         "None should be compared by identity.", "Use 'is None'."),
        ("except:", "medium", "Bare except",
         "A bare except hides errors and swallows SystemExit/KeyboardInterrupt.",
         "Catch specific exception types."),
        ("fixme", "low", "FIXME shipped in code",
         "An unresolved FIXME was committed.", "Resolve it or file an issue."),
        ("todo", "info", "TODO left in code",
         "An unresolved TODO was committed.", "Track it in an issue."),
    ],
    "style": [],  # handled by line-length / debug-print heuristics below
}


class MockProvider(Provider):
    name = "mock"

    def complete(self, system, user, model=None, schema=None):
        category = _marker(user, "AGENT_CATEGORY")
        if category == "synthesis":
            return _mock_summary(user)
        findings = []
        for fpath, lineno, code in _iter_added(user):
            low = code.lower()
            if category == "style":
                if len(code) > 100:
                    findings.append(_mf(fpath, lineno, "low", "Line exceeds 100 characters",
                                        "Long lines hurt readability.", "Wrap or refactor."))
                if "print(" in low:
                    findings.append(_mf(fpath, lineno, "info", "Debug print left in code",
                                        "print() looks like leftover debugging.", "Remove or use logging."))
                continue
            for needle, sev, title, detail, sugg in _MOCK_PATTERNS.get(category, []):
                if needle in low:
                    findings.append(_mf(fpath, lineno, sev, title, detail, sugg))
        if category == "tests" and _marker(user, "TEST_FILES_PRESENT") == "false":
            findings.append(_mf("", None, "medium", "No tests for changed code",
                                "Code changed but no test files were added or updated.",
                                "Add unit tests covering the new behavior."))
        return json.dumps(findings)


def _mf(file, line, severity, title, detail, suggestion):
    return {"file": file, "line": line, "severity": severity,
            "title": title, "detail": detail, "suggestion": suggestion}


def _marker(text, key):
    m = re.search(rf"{key}:\s*(\S+)", text)
    return m.group(1).strip().lower() if m else ""


def _iter_added(text):
    """Yield (file, lineno, code) from the prompt format produced by agents.format_files."""
    current = ""
    for ln in text.splitlines():
        fm = re.match(r"File:\s*(\S+)", ln)
        if fm:
            current = fm.group(1)
            continue
        am = re.match(r"\+(\d+):\s?(.*)", ln)
        if am:
            yield current, int(am.group(1)), am.group(2)


def _mock_summary(user):
    n = len(re.findall(r"^- ", user, re.M))
    if n == 0:
        return "Panel review complete — no issues found. Looks good to merge."
    return (f"Panel review complete: {n} finding(s) consolidated across the specialist "
            f"agents. Address the higher-severity items before merging.")


# --------------------------------------------------------------------------- #
# Real providers (lazy imports so the core stays dependency-free).
# --------------------------------------------------------------------------- #
class ClaudeCLIProvider(Provider):
    """Routes through the local `claude` CLI — uses your subscription, not the API.

    The CLI can't enforce a response schema, so `schema` is accepted for a
    uniform interface but ignored; we rely on the prompt's JSON instruction and
    the tolerant parser.
    """
    name = "claude-cli"

    def complete(self, system, user, model=None, schema=None):
        cmd = ["claude", "--print", "--dangerously-skip-permissions"]
        if model:
            cmd += ["--model", model]
        prompt = system + "\n\n---\n\n" + user
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=300)
        except FileNotFoundError:
            raise ProviderError("`claude` CLI not found on PATH.")
        except subprocess.TimeoutExpired:
            raise ProviderError("claude CLI timed out.")
        if r.returncode != 0:
            raise ProviderError((r.stderr or "claude CLI failed").strip()[:300])
        return r.stdout.strip()


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, host=None):
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")

    def complete(self, system, user, model=None, schema=None):
        import urllib.request
        payload = {
            "model": model or "llama3", "system": system,
            "prompt": user, "stream": False,
        }
        if schema is not None:
            payload["format"] = schema   # Ollama constrains decoding to the schema
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.host + "/api/generate", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read()).get("response", "")
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"ollama request failed: {e}")


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self):
        try:
            import anthropic
        except ImportError:
            raise ProviderError("anthropic not installed — `pip install quorum-review[anthropic]`")
        self._client = anthropic.Anthropic()

    def complete(self, system, user, model=None, schema=None):
        model = model or "claude-3-5-haiku-latest"
        if schema is not None:
            # Native structured output via forced tool use: the model must
            # return arguments matching `schema`, not free-form prose.
            tool = {"name": "report_findings",
                    "description": "Return the structured code-review findings.",
                    "input_schema": schema}
            msg = self._client.messages.create(
                model=model, max_tokens=2048, system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool], tool_choice={"type": "tool", "name": "report_findings"})
            for b in msg.content:
                if getattr(b, "type", None) == "tool_use":
                    return json.dumps(b.input)
            return "[]"
        msg = self._client.messages.create(
            model=model, max_tokens=2048,
            system=system, messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self):
        try:
            import openai
        except ImportError:
            raise ProviderError("openai not installed — `pip install quorum-review[openai]`")
        self._client = openai.OpenAI()

    def complete(self, system, user, model=None, schema=None):
        kwargs = {
            "model": model or "gpt-4o-mini",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        if schema is not None:
            # Native Structured Outputs: decoding is constrained to the schema.
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "findings", "schema": schema, "strict": True},
            }
        r = self._client.chat.completions.create(**kwargs)
        return r.choices[0].message.content or ""


_PROVIDERS = {
    "mock": MockProvider,
    "claude-cli": ClaudeCLIProvider,
    "claudecli": ClaudeCLIProvider,
    "cli": ClaudeCLIProvider,
    "ollama": OllamaProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


def get_provider(spec: str):
    """Return (provider, model_override). `model_override` pins a single model."""
    spec = (spec or "mock").strip()
    name, _, model = spec.partition(":")
    name = name.lower()
    if name not in _PROVIDERS:
        raise ProviderError(
            f"unknown provider '{name}'. Choose: {', '.join(sorted(set(_PROVIDERS)))}")
    return _PROVIDERS[name](), (model or None)
