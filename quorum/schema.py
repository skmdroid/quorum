"""Core data types. Plain dataclasses — no third-party deps."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

# Lower number = more severe (used for sorting and verdict thresholds).
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SEVERITIES = list(SEVERITY_ORDER)


@dataclass
class Finding:
    file: str
    line: Optional[int]
    severity: str
    category: str          # which specialist raised it: security/performance/...
    title: str
    detail: str = ""
    suggestion: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_ORDER:
            self.severity = "info"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentResult:
    agent: str
    model: str
    findings: list = field(default_factory=list)
    ok: bool = True
    error: str = ""


@dataclass
class ReviewResult:
    verdict: str           # APPROVE | COMMENT | REQUEST_CHANGES
    summary: str
    findings: list = field(default_factory=list)
    agent_results: list = field(default_factory=list)

    def stats(self) -> dict:
        s = {k: 0 for k in SEVERITY_ORDER}
        for f in self.findings:
            s[f.severity] = s.get(f.severity, 0) + 1
        return s

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "stats": self.stats(),
            "findings": [f.to_dict() for f in self.findings],
            "agents": [
                {"agent": a.agent, "model": a.model, "ok": a.ok,
                 "error": a.error, "findings": len(a.findings)}
                for a in self.agent_results
            ],
        }
