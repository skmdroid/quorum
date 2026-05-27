"""`quorum review` — entry point. Gets a diff (file/stdin/git), runs the panel,
prints the verdict, and returns a CI-friendly exit code."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from . import render, evals
from .agents import build_registry
from .config import load_config, ConfigError
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
    return PROVIDER_TIERS.get(provider_name, {}).get("fast")


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
    r.add_argument("--provider", default=None,
                   help="mock | claude-cli | anthropic | openai | ollama  (append :model to pin). "
                        "Default: mock, or 'provider' from .quorum.json")
    r.add_argument("--config", metavar="FILE", default=None,
                   help="config file (default: discover .quorum.json upward from cwd)")
    r.add_argument("--no-config", action="store_true", help="ignore any .quorum.json")
    r.add_argument("--format", default=None, choices=["text", "markdown", "json"])
    r.add_argument("--fail-on", default=None,
                   choices=["critical", "high", "medium", "low", "never"],
                   help="exit non-zero if any finding is at/above this severity (default: high)")
    r.add_argument("--no-color", action="store_true")

    e = sub.add_parser("eval", help="run the detection benchmark over the labeled corpus")
    e.add_argument("--provider", default="mock",
                   help="mock | claude-cli | anthropic | openai | ollama  (append :model to pin)")
    e.add_argument("--dataset", metavar="DIR", default=None,
                   help="dataset directory (default: the bundled evals/dataset corpus)")
    e.add_argument("--format", default="text", choices=["text", "markdown", "json"])
    e.add_argument("--min-recall", type=float, default=None, metavar="X",
                   help="exit non-zero if overall recall < X (CI regression gate)")
    e.add_argument("--max-clean-fp", type=int, default=None, metavar="N",
                   help="exit non-zero if false alarms on clean diffs exceed N")
    return p


def _run_eval(args) -> int:
    try:
        report = evals.evaluate(args.provider, args.dataset)
    except ProviderError as e:
        print(f"provider error: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"dataset not found: {e}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    elif args.format == "markdown":
        print(evals.to_markdown(report))
    else:
        print(evals.to_text(report))

    failed = False
    if args.min_recall is not None and report.recall() + 1e-9 < args.min_recall:
        print(f"\nFAIL: recall {report.recall():.3f} < --min-recall {args.min_recall}",
              file=sys.stderr)
        failed = True
    if args.max_clean_fp is not None and report.clean_false_positives() > args.max_clean_fp:
        print(f"\nFAIL: clean-diff false alarms {report.clean_false_positives()} "
              f"> --max-clean-fp {args.max_clean_fp}", file=sys.stderr)
        failed = True
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "eval":
        return _run_eval(args)
    if args.cmd != "review":
        parser.print_help()
        return 0

    # Resolution order: explicit CLI flag > .quorum.json > built-in default.
    try:
        cfg = load_config(args.config, search=not args.no_config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    provider_spec = args.provider or cfg.provider or "mock"
    fail_on = args.fail_on or cfg.fail_on or "high"
    fmt = args.format or cfg.format or "text"
    if fail_on not in ("critical", "high", "medium", "low", "never"):
        print(f"config error: invalid fail_on '{fail_on}'", file=sys.stderr)
        return 2
    if fmt not in ("text", "markdown", "json"):
        print(f"config error: invalid format '{fmt}'", file=sys.stderr)
        return 2

    files = parse_diff(_read_diff(args))
    if not files:
        print("No added lines found in the diff — nothing to review.")
        return 0

    try:
        provider, model_override = get_provider(provider_spec)
    except ProviderError as e:
        print(f"provider error: {e}", file=sys.stderr)
        return 2

    registry = build_registry(cfg.disabled, cfg.custom_agents)

    if provider.name == "mock":
        print("ℹ  using the built-in mock reviewer (deterministic, no LLM). Pass "
              "--provider claude-cli|anthropic|openai|ollama for a real review.",
              file=sys.stderr)
    if cfg.source:
        extras = []
        if cfg.disabled:
            extras.append(f"{len(cfg.disabled)} disabled")
        if cfg.custom_agents:
            extras.append(f"{len(cfg.custom_agents)} custom")
        if cfg.rules:
            extras.append(f"{len(cfg.rules)} rule(s)")
        suffix = f" ({', '.join(extras)})" if extras else ""
        print(f"ℹ  config: {os.path.relpath(cfg.source)}{suffix}", file=sys.stderr)

    agent_results = dispatch(files, provider, provider.name, model_override,
                             agents=registry, rules=cfg.rules)
    result = synthesize(agent_results, provider, _synth_model(provider.name, model_override))

    if fmt == "json":
        print(render.to_json(result))
    elif fmt == "markdown":
        print(render.to_markdown(result))
    else:
        print(render.to_text(result, color=not args.no_color))

    return _exit_code(result, fail_on)


if __name__ == "__main__":
    raise SystemExit(main())
