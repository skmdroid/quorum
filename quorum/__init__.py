"""Quorum — a multi-agent code reviewer.

A panel of specialist LLM agents (security, performance, correctness, tests,
style) reviews a diff in parallel; a synthesizer consolidates their findings
into a single prioritized verdict.
"""
from .schema import Finding, AgentResult, ReviewResult
from .diff import parse_diff
from .dispatcher import dispatch
from .synthesizer import synthesize
from .providers import get_provider

__version__ = "0.1.0"
__all__ = [
    "Finding", "AgentResult", "ReviewResult",
    "parse_diff", "dispatch", "synthesize", "get_provider",
]
