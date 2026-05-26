"""Output renderers: colored terminal text, GitHub-flavored markdown, and JSON."""
from __future__ import annotations

import json

from .schema import SEVERITY_ORDER

_COLOR = {"critical": "\033[1;31m", "high": "\033[31m", "medium": "\033[33m",
          "low": "\033[36m", "info": "\033[90m"}
_RESET = "\033[0m"
_VERDICT_COLOR = {"REQUEST_CHANGES": "\033[1;31m", "COMMENT": "\033[1;33m",
                  "APPROVE": "\033[1;32m"}
_ICON = {"critical": "⛔", "high": "🔴", "medium": "🟡", "low": "🔵", "info": "⚪"}


def _loc(f) -> str:
    if f.line:
        return f"{f.file}:{f.line}"
    return f.file or "(general)"


def to_text(result, color: bool = True) -> str:
    def paint(s, code):
        return f"{code}{s}{_RESET}" if (color and code) else s

    out = [paint(f"VERDICT: {result.verdict}", _VERDICT_COLOR.get(result.verdict, "")),
           result.summary, ""]
    stats = result.stats()
    out.append(" ".join(f"{_ICON[k]} {stats[k]} {k}" for k in SEVERITY_ORDER if stats[k])
               or "no findings")
    out.append("")
    for f in result.findings:
        out.append(paint(f"{_ICON.get(f.severity, '')} [{f.severity.upper()}] {f.title}",
                         _COLOR.get(f.severity, "")))
        out.append(f"    {_loc(f)}  ·  {f.category}")
        if f.detail:
            out.append(f"    {f.detail}")
        if f.suggestion:
            out.append(f"    ↳ {f.suggestion}")
        out.append("")
    failed = [a for a in result.agent_results if not a.ok]
    if failed:
        out.append("⚠ agents that failed (review continued): "
                   + ", ".join(f"{a.agent} ({a.error})" for a in failed))
    return "\n".join(out).rstrip() + "\n"


def to_markdown(result) -> str:
    out = [f"### 🤖 Quorum review — **{result.verdict}**", "", result.summary, ""]
    stats = result.stats()
    out.append(" · ".join(f"**{stats[k]}** {k}" for k in SEVERITY_ORDER if stats[k])
               or "_no findings_")
    out.append("")
    for f in result.findings:
        loc = f"`{_loc(f)}`"
        out.append(f"- {_ICON.get(f.severity, '')} **[{f.severity}]** {f.title} — {loc} "
                   f"_({f.category})_")
        if f.detail:
            out.append(f"  - {f.detail}")
        if f.suggestion:
            out.append(f"  - ↳ _{f.suggestion}_")
    return "\n".join(out) + "\n"


def to_json(result) -> str:
    return json.dumps(result.to_dict(), indent=2)
