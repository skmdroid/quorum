"""The dispatcher: routes a changeset to the right specialist agents, assigns a
model tier to each, and runs them in parallel. One agent failing never aborts
the panel — it's recorded and the review continues.

`agents` is the effective registry (built-ins, possibly tweaked by config); it
defaults to the built-in `AGENTS`. `rules` are project house-rules injected into
every agent's prompt."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from .agents import AGENTS, run_agent
from .providers import PROVIDER_TIERS

# Built-in code specialists that a docs-only change should skip.
_CODE_BUILTINS = ("security", "performance", "tests", "correctness")


def select_agents(files, agents=None) -> list:
    """Routing rules — which specialists are worth running for this changeset."""
    agents = agents or AGENTS
    code_files = [f for f in files if not f.is_doc]
    has_tests = any(f.is_test for f in files)
    selected = []
    for cat in agents:
        # Docs-only change: skip code-specific built-ins, keep style + any custom.
        if not code_files and cat in _CODE_BUILTINS:
            continue
        # Tests were already updated: the missing-tests check is moot.
        if cat == "tests" and has_tests:
            continue
        selected.append(cat)
    if selected:
        return selected
    return ["style"] if "style" in agents else list(agents)[:1]


def resolve_models(provider_name: str, model_override) -> dict:
    tiers = dict(PROVIDER_TIERS.get(provider_name, {"strong": None, "fast": None}))
    if model_override:
        tiers = {"strong": model_override, "fast": model_override}
    return tiers


def dispatch(files, provider, provider_name: str, model_override=None,
             max_workers: int = 5, agents=None, rules=None) -> list:
    agents = agents or AGENTS
    tiers = resolve_models(provider_name, model_override)
    has_tests = any(f.is_test for f in files)
    code_files = [f for f in files if not f.is_doc] or files

    jobs = []
    for cat in select_agents(files, agents):
        model = tiers.get(agents[cat]["tier"])
        focus = agents[cat]["focus"]
        targets = files if cat == "style" else code_files
        jobs.append((cat, model, focus, targets))

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(run_agent, cat, provider, model, targets, has_tests, focus, rules)
                   for cat, model, focus, targets in jobs]
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: r.agent)
    return results
