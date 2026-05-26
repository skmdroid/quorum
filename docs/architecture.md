# Architecture

Quorum is a small orchestration pipeline. Each stage is a separate module with a
narrow responsibility, so adding an agent or a provider is a localized change.

## Pipeline

```
diff text ──▶ parse_diff ──▶ dispatch ──▶ [agents in parallel] ──▶ synthesize ──▶ render
                (diff.py)    (dispatcher)      (agents.py)        (synthesizer)   (render.py)
```

### 1. Parsing — `diff.py`
A unified diff is reduced to a list of `FileDiff` objects, each holding the
**added** lines with their new-file line numbers. Files are classified by
language (extension) and tagged as test or doc files. Only added lines are
reviewed — Quorum comments on what you're introducing, not pre-existing code.

### 2. Routing — `dispatcher.py`
`select_agents()` decides which specialists are worth running:

- docs-only change → only the **style** agent
- tests already updated in the diff → drop the **tests** ("missing coverage") agent
- otherwise → the full panel

Each agent declares a **tier** (`strong` / `cheap`); `resolve_models()` maps the
tier to a concrete model for the active provider. `--provider name:model` pins a
single model for everything.

Agents run in a `ThreadPoolExecutor`. Each `run_agent` call is wrapped so an
exception becomes a failed `AgentResult(ok=False, error=...)` instead of
propagating — **one agent failing never aborts the panel.**

### 3. Agents — `agents.py`
An agent is data, not a class hierarchy: a `focus` string and a model `tier`.
`system_prompt()` builds a focused reviewer prompt; `build_user()` renders the
added code in a stable, line-numbered format with machine-readable markers
(`AGENT_CATEGORY`, `TEST_FILES_PRESENT`). `parse_findings()` extracts the JSON
array from the model's reply — tolerant of surrounding prose — and validates
every field into a `Finding`.

### 4. Synthesis — `synthesizer.py`
All findings are merged, de-duplicated on `(file, line, title)`, and sorted by
severity. The verdict is deterministic:

| Any finding at… | Verdict |
|---|---|
| critical / high | `REQUEST_CHANGES` |
| medium | `COMMENT` |
| low / info / none | `APPROVE` |

A short author-facing summary is written by the provider, with a deterministic
fallback if the call fails.

### 5. Rendering — `render.py`
`to_text` (colored terminal), `to_markdown` (PR comment / CI job summary), and
`to_json` (machine-readable) all consume the same `ReviewResult`.

## Providers — `providers.py`
A provider is anything with `complete(system, user, model) -> str`. The core has
no third-party dependencies; the `anthropic` / `openai` SDKs are optional extras
imported lazily inside their provider classes. The `MockProvider` is a real
heuristic reviewer used by the test suite and the zero-setup first run, which
keeps the whole pipeline deterministically testable offline.

## Extending

- **New agent:** add an entry to `AGENTS` in `agents.py` (focus + tier). Routing,
  parallelism, synthesis, and rendering pick it up automatically.
- **New provider:** implement `Provider.complete`, register it in `_PROVIDERS`,
  and add its model tiers to `PROVIDER_TIERS`.
