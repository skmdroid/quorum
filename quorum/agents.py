"""Specialist review agents. Each agent has a focus + a model tier; it sends
the added code to the provider and parses back structured findings."""
from __future__ import annotations

import json

from .schema import Finding
from .schema import AgentResult
from .providers import ProviderError

# tier -> resolved to a concrete model by the dispatcher (cheap vs strong).
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
        "tier": "cheap",
        "focus": "test coverage: whether the changed behavior is covered by new or updated "
                 "tests, and missing edge-case tests",
    },
    "style": {
        "tier": "cheap",
        "focus": "readability and maintainability: naming, dead code, leftover debug prints, "
                 "overly long lines, unclear structure (non-blocking nits)",
    },
}

_SCHEMA_HINT = (
    'Respond with ONLY a JSON array (no prose, no markdown fences). Each element: '
    '{"file": string, "line": integer or null, '
    '"severity": one of "critical"|"high"|"medium"|"low"|"info", '
    '"title": short string, "detail": string, "suggestion": string}. '
    "Return [] if you find nothing."
)


def system_prompt(category: str) -> str:
    focus = AGENTS[category]["focus"]
    return (
        f"You are a meticulous senior engineer serving as the **{category}** specialist on a "
        f"code-review panel. Review ONLY for: {focus}. Only flag real issues in the ADDED lines; "
        f"avoid false positives and do not comment outside your specialty. {_SCHEMA_HINT}"
    )


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


def parse_findings(raw: str, category: str) -> list:
    text = (raw or "").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        items = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return []
    findings = []
    for it in items:
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


def run_agent(category: str, provider, model, files, tests_present: bool) -> AgentResult:
    label = model or provider.name
    try:
        raw = provider.complete(system_prompt(category),
                                build_user(category, files, tests_present), model)
        return AgentResult(agent=category, model=label,
                           findings=parse_findings(raw, category))
    except ProviderError as e:
        return AgentResult(agent=category, model=label, findings=[], ok=False, error=str(e))
    except Exception as e:  # noqa: BLE001 — one agent failing must not abort the panel
        return AgentResult(agent=category, model=label, findings=[], ok=False,
                           error=f"{type(e).__name__}: {e}")
