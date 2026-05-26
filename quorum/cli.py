"""`quorum review` — entry point. Gets a diff (file/stdin/git), runs the panel,
prints the verdict, and returns a CI-friendly exit code."""
from __future__ import annotations

import argparse
import subprocess
import sys

from . import render
from .diff import parse_diff
from .dispatcher import dispatch
from .providers import get_provider, ProviderError, PROVIDER_TIERS
from .schema import SEVERITY_ORDER
from .synthesizer import synthesize


def _read_diff(args) -> str:
    if args.diff:
        if args.diff == "-":
            return sys.stdin.read()
        with open(args.diff, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    if args.base:
        cmd = ["git", "diff", f"{args.base}...HEAD"]
    elif args.staged:
        cmd = ["git", "diff", "--cached"]
    else:
        cmd = ["git", "diff", "HEAD"]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except Exception as e:  # noqa: BLE001
        print(f"could not read git diff ({' '.join(cmd)}): {e}", file=sys.stderr)
        sys.exit(2)


def _synth_model(provider_name, model_override):
    if model_override:
        return model_override
    return PROVIDER_TIERS.get(provider_name, {}).get("cheap")


def _exit_code(result, fail_on) -> int:
    if fail_on == "never":
        return 0
    threshold = SEVERITY_ORDER[fail_on]
    worst = min((SEVERITY_ORDER.get(f.severity, 9) for f in result.findings), default=9)
    return 1 if worst <= threshold else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="quorum",
        description="Multi-agent code reviewer — a panel of specialist LLM agents "
                    "reviews your diff and returns a single verdict.")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("review", help="review a diff")
    r.add_argument("--diff", metavar="FILE", help="unified diff file, or '-' for stdin")
    r.add_argument("--staged", action="store_true", help="review staged changes (git diff --cached)")
    r.add_argument("--base", metavar="REF", help="review commits since REF (git diff REF...HEAD)")
    r.add_argument("--provider", default="mock",
                   help="mock | claude-cli | anthropic | openai | ollama  (append :model to pin)")
    r.add_argument("--format", default="text", choices=["text", "markdown", "json"])
    r.add_argument("--fail-on", default="high",
                   choices=["critical", "high", "medium", "low", "never"],
                   help="exit non-zero if any finding is at/above this severity (default: high)")
    r.add_argument("--no-color", action="store_true")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd != "review":
        parser.print_help()
        return 0

    files = parse_diff(_read_diff(args))
    if not files:
        print("No added lines found in the diff — nothing to review.")
        return 0

    try:
        provider, model_override = get_provider(args.provider)
    except ProviderError as e:
        print(f"provider error: {e}", file=sys.stderr)
        return 2

    if provider.name == "mock":
        print("ℹ  using the built-in mock reviewer (deterministic, no LLM). Pass "
              "--provider claude-cli|anthropic|openai|ollama for a real review.\n",
              file=sys.stderr)

    agent_results = dispatch(files, provider, provider.name, model_override)
    result = synthesize(agent_results, provider, _synth_model(provider.name, model_override))

    if args.format == "json":
        print(render.to_json(result))
    elif args.format == "markdown":
        print(render.to_markdown(result))
    else:
        print(render.to_text(result, color=not args.no_color))

    return _exit_code(result, getattr(args, "fail_on"))


if __name__ == "__main__":
    raise SystemExit(main())
