"""The dispatcher: routes a changeset to the right specialist agents, assigns a
model tier to each, and runs them in parallel. One agent failing never aborts
the panel — it's recorded and the review continues."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from .agents import AGENTS, run_agent
from .providers import PROVIDER_TIERS


def select_agents(files) -> list:
    """Routing rules — which specialists are worth running for this changeset."""
    code_files = [f for f in files if not f.is_doc]
    has_tests = any(f.is_test for f in files)
    selected = []
    for cat in AGENTS:
        # Docs-only change: skip code-specific specialists, keep only style.
        if not code_files and cat in ("security", "performance", "tests", "correctness"):
            continue
        # Tests were already updated: the missing-tests check is moot.
        if cat == "tests" and has_tests:
            continue
        selected.append(cat)
    return selected or ["style"]


def resolve_models(provider_name: str, model_override) -> dict:
    tiers = dict(PROVIDER_TIERS.get(provider_name, {"strong": None, "cheap": None}))
    if model_override:
        tiers = {"strong": model_override, "cheap": model_override}
    return tiers


def dispatch(files, provider, provider_name: str, model_override=None, max_workers: int = 5) -> list:
    tiers = resolve_models(provider_name, model_override)
    has_tests = any(f.is_test for f in files)
    code_files = [f for f in files if not f.is_doc] or files

    jobs = []
    for cat in select_agents(files):
        model = tiers.get(AGENTS[cat]["tier"])
        targets = files if cat == "style" else code_files
        jobs.append((cat, model, targets))

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(run_agent, cat, provider, model, targets, has_tests)
                   for cat, model, targets in jobs]
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: r.agent)
    return results
