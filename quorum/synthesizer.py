"""The synthesizer: merges every agent's findings, de-duplicates, sorts by
severity, decides a single verdict, and writes a short author-facing summary."""
from __future__ import annotations

from .schema import ReviewResult, SEVERITY_ORDER


def _dedup(findings: list) -> list:
    seen, out = set(), []
    for f in sorted(findings, key=lambda x: SEVERITY_ORDER.get(x.severity, 9)):
        key = (f.file, f.line, f.title)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def decide_verdict(findings: list) -> str:
    severities = {f.severity for f in findings}
    if severities & {"critical", "high"}:
        return "REQUEST_CHANGES"
    if "medium" in severities:
        return "COMMENT"
    return "APPROVE"


def _summary_prompt(findings: list, verdict: str) -> str:
    lines = [f"- [{f.severity}] {f.title} ({f.file}:{f.line})" for f in findings]
    return (
        "AGENT_CATEGORY: synthesis\n\n"
        f"Proposed verdict: {verdict}. The review panel produced these findings:\n"
        + ("\n".join(lines) if lines else "(none)")
        + "\n\nWrite a concise 2-3 sentence summary for the PR author, leading with the most "
          "important point. Plain prose, no JSON."
    )


def _fallback_summary(findings: list, verdict: str) -> str:
    if not findings:
        return "No issues found by the review panel. Looks good to merge."
    return f"The panel raised {len(findings)} finding(s); verdict {verdict}. See details below."


def synthesize(agent_results: list, provider=None, model=None) -> ReviewResult:
    findings = []
    for r in agent_results:
        findings.extend(r.findings)
    findings = _dedup(findings)
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
    verdict = decide_verdict(findings)

    summary = ""
    if provider is not None:
        try:
            summary = provider.complete(
                "You are the lead reviewer summarizing a panel code review.",
                _summary_prompt(findings, verdict), model).strip()
        except Exception:  # noqa: BLE001 — summary is best-effort
            summary = ""
    if not summary:
        summary = _fallback_summary(findings, verdict)

    return ReviewResult(verdict=verdict, summary=summary,
                        findings=findings, agent_results=agent_results)
