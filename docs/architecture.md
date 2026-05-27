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

- docs-only change → only the **style** agent (plus any custom agents)
- tests already updated in the diff → drop the **tests** ("missing coverage") agent
- otherwise → the full panel

The set it routes over is the **effective registry** — the built-ins, minus any
disabled in `.quorum.json`, plus any `custom_agents` defined there (see
`agents.build_registry`). Each agent declares a **tier** (`strong` / `fast`);
`resolve_models()` maps the tier to a concrete model for the active provider.
`--provider name:model` pins a single model for everything.

Agents run in a `ThreadPoolExecutor`. Each `run_agent` call is wrapped so an
exception becomes a failed `AgentResult(ok=False, error=...)` instead of
propagating — **one agent failing never aborts the panel.**

### 3. Agents — `agents.py`
An agent is data, not a class hierarchy: a `focus` string and a model `tier`, so
config can disable one or register a new one with no code. `system_prompt()`
builds a focused reviewer prompt (and appends any project `rules`); `build_user()`
renders the added code in a stable, line-numbered format with machine-readable
markers (`AGENT_CATEGORY`, `TEST_FILES_PRESENT`). Agents request **native
structured output** against `FINDINGS_SCHEMA`; `parse_findings()` then reads the
result — whether it's a structured `{"findings": [...]}` object, a bare array, or
an array embedded in prose — and validates every field into a `Finding`. The
structured path keeps capable providers exact; the tolerant parser keeps the
panel from breaking on ones that can't enforce a schema.

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
A provider is anything with `complete(system, user, model, schema) -> str`. When
`schema` is set and the provider supports it, the reply is constrained with
native structured output — OpenAI `json_schema`, Anthropic forced tool-use,
Ollama `format`; otherwise `schema` is ignored and the prompt instruction plus
the tolerant parser carry it. The core has no third-party dependencies; the
`anthropic` / `openai` SDKs are optional extras imported lazily inside their
provider classes. The `MockProvider` is a real heuristic reviewer used by the
test suite and the zero-setup first run, which keeps the whole pipeline
deterministically testable offline.

## Evaluation — `evals.py`
`evaluate()` runs the full panel over a labeled corpus (`evals/dataset/`: one
unified diff per case + a `labels.json` of expected findings) and scores
**recall** on the planted defects and **false alarms** on the clean diffs. A
prediction matches a label when the category agrees and the line is within ±2
(`LINE_TOL`); matching is greedy 1:1 within a category. It deliberately omits a
global precision/F1 — a thorough reviewer flags real issues the corpus doesn't
label, and counting those against it would reward silence; extra findings on
defect diffs are reported separately, as information. The coverage agent's
"missing tests" nudge is out of scope (`SCORE_CATEGORIES`). Because the `mock`
provider is deterministic, the score never drifts, so CI gates on it with
`quorum eval --min-recall … --max-clean-fp …` — no key, no flakiness. Swap in a
real provider to measure the live panel on the same corpus.

## Extending

- **No code at all:** a `.quorum.json` can disable a built-in agent, add a
  `custom_agents` entry (focus + tier), or inject project `rules` — loaded by
  `config.py` and applied via `agents.build_registry`.
- **New built-in agent:** add an entry to `AGENTS` in `agents.py` (focus + tier).
  Routing, parallelism, synthesis, and rendering pick it up automatically.
- **New provider:** implement `Provider.complete(…, schema)`, register it in
  `_PROVIDERS`, and add its model tiers to `PROVIDER_TIERS`.
