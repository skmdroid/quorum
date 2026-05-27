"""Specialist review agents. Each agent has a focus + a model tier; it sends
the added code to the provider and parses back structured findings.

Agents are *data*, not a class hierarchy — so a config file can disable one,
retune another, or add an entirely new specialist with no code change (see
`build_registry` and `config.py`)."""
from __future__ import annotations

import json

from .schema import Finding
from .schema import AgentResult
from .providers import ProviderError

# tier -> resolved to a concrete model by the dispatcher (fast vs strong).
AGENTS = {
    "security": {
        "tier": "strong",
        "focus": "security vulnerabilities: injection, unsafe deserialization, secrets "
                 "committed to code, broken auth/authz, SSRF, path traversal, unsafe crypto",
    },
    "performance": {
        "tier": "strong",
        "focus": "performance problems: needless allocations, N+1 queries, blocking I/O on "
                 "hot paths, accidental O(n^2), missing pagination or caching",
    },
    "correctness": {
        "tier": "strong",
        "focus": "correctness bugs: edge cases, off-by-one, null/None handling, missing error "
                 "handling, race conditions, and plainly incorrect logic",
    },
    "tests": {
        "tier": "fast",
        "focus": "test coverage: whether the changed behavior is covered by new or updated "
                 "tests, and missing edge-case tests",
    },
    "style": {
        "tier": "fast",
        "focus": "readability and maintainability: naming, dead code, leftover debug prints, "
                 "overly long lines, unclear structure (non-blocking nits)",
    },
}

_SCHEMA_HINT = (
    'Respond with ONLY a JSON object: {"findings": [ ... ]} (no prose, no markdown '
    'fences). Each finding: {"file": string, "line": integer or null, '
    '"severity": one of "critical"|"high"|"medium"|"low"|"info", '
    '"title": short string, "detail": string, "suggestion": string}. '
    'Use {"findings": []} if you find nothing.'
)

# JSON schema for native structured output. Providers that support it (OpenAI
# json_schema, Anthropic tool-use, Ollama format=) are constrained to this, so
# the reply is schema-valid by construction; the rest rely on the prompt hint
# above plus the tolerant parser in `parse_findings`.
_FINDING_PROPS = {
    "file": {"type": "string"},
    "line": {"type": ["integer", "null"]},
    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
    "title": {"type": "string"},
    "detail": {"type": "string"},
    "suggestion": {"type": "string"},
}
FINDINGS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": _FINDING_PROPS,
                "required": list(_FINDING_PROPS),
            },
        }
    },
    "required": ["findings"],
}


def build_registry(disabled=(), custom_agents=None) -> dict:
    """The effective agent set: built-ins minus `disabled`, plus `custom_agents`
    (each a {'tier', 'focus'} dict). Used to apply a `.quorum.json` config."""
    reg = {name: spec for name, spec in AGENTS.items() if name not in disabled}
    for name, spec in (custom_agents or {}).items():
        reg[name] = {"tier": spec.get("tier", "fast"), "focus": spec["focus"]}
    return reg


def system_prompt(category: str, focus: str | None = None, rules=None) -> str:
    focus = focus or AGENTS[category]["focus"]
    prompt = (
        f"You are a meticulous senior engineer serving as the **{category}** specialist on a "
        f"code-review panel. Review ONLY for: {focus}. Only flag real issues in the ADDED lines; "
        f"avoid false positives and do not comment outside your specialty. {_SCHEMA_HINT}"
    )
    if rules:
        prompt += ("\n\nAlso enforce these project-specific rules where they fall within your "
                   "specialty, reporting any violation as a finding:\n"
                   + "\n".join(f"- {r}" for r in rules))
    return prompt


def format_files(files) -> str:
    """Render files into the line-numbered prompt format the providers expect."""
    out = []
    for f in files:
        tag = " [test file]" if f.is_test else ""
        out.append(f"File: {f.path} ({f.language}){tag}")
        for lineno, code in f.added:
            out.append(f"+{lineno}: {code}")
        out.append("")
    return "\n".join(out)


def build_user(category: str, files, tests_present: bool) -> str:
    header = (f"AGENT_CATEGORY: {category}\n"
              f"TEST_FILES_PRESENT: {'true' if tests_present else 'false'}\n\n")
    return header + "Review the following added code:\n\n" + format_files(files)


def _json_candidates(text: str):
    """Substrings of `text` that might be the JSON payload, most-likely first."""
    yield text
    o, oc = text.find("{"), text.rfind("}")
    if 0 <= o < oc:
        yield text[o:oc + 1]
    a, ac = text.find("["), text.rfind("]")
    if 0 <= a < ac:
        yield text[a:ac + 1]


def _extract_items(raw: str) -> list:
    """Pull the findings list out of a model reply — whether it's a structured
    object {"findings": [...]}, a bare JSON array, or an array embedded in prose.
    Robust to providers that couldn't enforce a schema."""
    text = (raw or "").strip()
    if not text:
        return []
    for candidate in _json_candidates(text):
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            items = data.get("findings") or data.get("issues")
            if isinstance(items, list):
                return items
            continue   # a dict without a findings list — try the next candidate
        if isinstance(data, list):
            return data
    return []


def parse_findings(raw: str, category: str) -> list:
    findings = []
    for it in _extract_items(raw):
        if not isinstance(it, dict):
            continue
        line = it.get("line")
        findings.append(Finding(
            file=str(it.get("file") or ""),
            line=line if isinstance(line, int) else None,
            severity=str(it.get("severity") or "info").lower(),
            category=category,
            title=str(it.get("title") or "Issue")[:140],
            detail=str(it.get("detail") or ""),
            suggestion=str(it.get("suggestion") or ""),
        ))
    return findings


def run_agent(category: str, provider, model, files, tests_present: bool,
              focus: str | None = None, rules=None) -> AgentResult:
    label = model or provider.name
    try:
        raw = provider.complete(system_prompt(category, focus, rules),
                                build_user(category, files, tests_present),
                                model, schema=FINDINGS_SCHEMA)
        return AgentResult(agent=category, model=label,
                           findings=parse_findings(raw, category))
    except ProviderError as e:
        return AgentResult(agent=category, model=label, findings=[], ok=False, error=str(e))
    except Exception as e:  # noqa: BLE001 — one agent failing must not abort the panel
        return AgentResult(agent=category, model=label, findings=[], ok=False,
                           error=f"{type(e).__name__}: {e}")
