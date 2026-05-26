# Quorum

**A multi-agent code reviewer.** Point it at a diff and a *panel* of specialist LLM agents — security, performance, correctness, tests, and style — review it in parallel. A synthesizer then de-duplicates, ranks, and consolidates everything into a single verdict: **APPROVE**, **COMMENT**, or **REQUEST_CHANGES**.

[![CI](https://github.com/skmdroid/quorum/actions/workflows/ci.yml/badge.svg)](https://github.com/skmdroid/quorum/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Dependencies](https://img.shields.io/badge/runtime%20deps-0-brightgreen)

> A single LLM asked to "review this code" spreads itself thin and misses things. Quorum gives each concern its **own** agent with a focused prompt, runs them **concurrently**, routes a **stronger model** to the high-stakes checks (security, correctness) and a **cheaper one** to nits (style) — then has a synthesizer make the final call. It's built like production software: zero runtime dependencies, a deterministic offline mode, graceful degradation when an agent fails, and a CI-friendly exit code.

---

## How it works

```mermaid
flowchart TB
    A["Unified diff<br/>(git diff · PR · stdin)"] --> B["Diff parser<br/>files · hunks · language · test/doc"]
    B --> C{"Dispatcher<br/>routing + model tiers"}

    C -->|strong model| S["🛡 Security agent"]
    C -->|strong model| P["⚡ Performance agent"]
    C -->|strong model| K["🎯 Correctness agent"]
    C -->|cheap model| T["🧪 Tests agent"]
    C -->|cheap model| Y["✨ Style agent"]

    S --> F["Structured findings<br/>(file · line · severity · fix)"]
    P --> F
    K --> F
    T --> F
    Y --> F

    F --> Z["Synthesizer<br/>de-dup · rank · decide · summarize"]
    Z --> V{{"VERDICT<br/>APPROVE · COMMENT · REQUEST_CHANGES"}}
    Z --> O["Output<br/>text · markdown · JSON · CI exit code"]
```

1. **Parse** — the diff is split into files and hunks; each file is classified by language and whether it's a test or a doc.
2. **Route** — the dispatcher decides *which* specialists to run for this changeset (a docs-only change skips the security/perf agents; if tests were already updated, the "missing tests" check is dropped) and assigns each agent a **model tier**.
3. **Review in parallel** — every specialist runs concurrently against the added lines and returns **structured JSON findings**. If one agent errors out, it's recorded and the panel continues — a single failure never aborts the review.
4. **Synthesize** — findings are de-duplicated, sorted by severity, reduced to a verdict, and summarized for the PR author.
5. **Report** — render as colored terminal text, GitHub-flavored markdown (for a PR comment), or JSON. The process exit code is CI-friendly.

---

## Quickstart (10 seconds, no API key)

Quorum ships with a deterministic **mock reviewer** so you can see it run immediately:

```bash
git clone https://github.com/skmdroid/quorum.git
cd quorum
pip install -e .

# review your current working changes
quorum review

# or a saved diff file
quorum review --diff tests/fixtures/sample.diff
```

```text
VERDICT: REQUEST_CHANGES
Panel review complete: 6 finding(s) consolidated across the specialist agents.

⛔ 1 critical 🔴 1 high 🟡 3 medium ⚪ 1 info

⛔ [CRITICAL] Use of eval()
    app/handler.py:4  ·  security
    eval() on dynamic input allows arbitrary code execution.
    ↳ Parse explicitly or use ast.literal_eval().
...
```

The mock provider needs no model — it's also what the test suite runs against, so CI is fast and free.

---

## Real reviews

Pick a provider with `--provider`. Quorum is model-agnostic; you bring your own key (or use a local model):

| Provider | Setup | Cost |
|---|---|---|
| `mock` | nothing — built in | free, offline |
| `claude-cli` | the [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) on your `PATH` | uses your Claude subscription |
| `anthropic` | `pip install 'quorum-review[anthropic]'` · `export ANTHROPIC_API_KEY=…` | per-token |
| `openai` | `pip install 'quorum-review[openai]'` · `export OPENAI_API_KEY=…` | per-token |
| `ollama` | a running [Ollama](https://ollama.com) server | free, local |

```bash
# real review through the Claude CLI (no API key — uses your subscription)
quorum review --diff pr.diff --provider claude-cli

# Anthropic API, JSON output
export ANTHROPIC_API_KEY=sk-ant-...
quorum review --staged --provider anthropic --format json

# fully local with Ollama, pinning one model for every agent
quorum review --provider ollama:llama3
```

See [`examples/sample_review.md`](examples/sample_review.md) for a full real review (13 findings) produced by the panel.

### Model routing

High-stakes checks deserve a stronger model; nits don't. Each agent declares a **tier** (`strong` or `cheap`) and the dispatcher resolves it per provider:

| Agent | Tier | Anthropic default | OpenAI default |
|---|---|---|---|
| security, performance, correctness | `strong` | `claude-sonnet-4-5` | `gpt-4o` |
| tests, style | `cheap` | `claude-3-5-haiku-latest` | `gpt-4o-mini` |

Pin a single model for everything with `--provider anthropic:claude-opus-4-...` (or any `name:model`).

---

## Usage

```text
quorum review [options]

  --diff FILE        unified diff file, or '-' for stdin
  --staged           review staged changes (git diff --cached)
  --base REF         review commits since REF (git diff REF...HEAD)
  --provider NAME    mock | claude-cli | anthropic | openai | ollama[:model]
  --format FMT       text (default) | markdown | json
  --fail-on SEV      exit non-zero if any finding ≥ SEV
                     critical|high|medium|low|never   (default: high)
  --no-color
```

**Exit codes** make it drop-in for CI: `0` when clean (or all findings are below `--fail-on`), `1` when the gate trips, `2` on a usage/provider error.

```bash
# block a merge on any high/critical finding
quorum review --base origin/main --provider anthropic --fail-on high

# review a diff piped from anywhere
git diff HEAD~3 | quorum review --diff - --format markdown
```

---

## Continuous integration

Two workflows ship in [`.github/workflows`](.github/workflows):

- **`ci.yml`** — runs the test suite on Python 3.10–3.13 (mock provider, no secrets).
- **`quorum-review.yml`** — an example PR gate that runs Quorum on the diff and posts the result to the job summary. Swap the mock provider for a real one by adding an API key as a repository secret.

```yaml
# .github/workflows/quorum-review.yml (excerpt)
- run: pip install -e .
- run: |
    git diff origin/${{ github.base_ref }}...HEAD > pr.diff
    quorum review --diff pr.diff --format markdown --fail-on never >> "$GITHUB_STEP_SUMMARY"
```

---

## Design decisions

- **Zero runtime dependencies.** The core is pure standard library; provider SDKs are optional extras imported lazily. The package installs and runs anywhere instantly.
- **Deterministic offline mode.** The mock provider is a real heuristic reviewer, which means the whole pipeline is testable without a network, a key, or flakiness — the test suite runs in milliseconds.
- **Graceful degradation.** Agents run in a thread pool; an exception in one is captured as a failed `AgentResult` and surfaced, but the rest of the panel still produces a verdict. Reviews don't fail closed on a transient model hiccup.
- **Structured findings, tolerant parsing.** Agents are asked for strict JSON; the parser extracts the JSON array even if a model wraps it in prose, and coerces/validates every field.
- **Separation of concerns.** Routing, model selection, agent prompts, synthesis, and rendering are independent modules — adding an agent or a provider is a localized change.

---

## Project layout

```
quorum/
├── schema.py        # Finding / AgentResult / ReviewResult dataclasses
├── providers.py     # provider-agnostic LLM layer (mock, claude-cli, anthropic, openai, ollama)
├── diff.py          # unified-diff parser + language/test/doc classification
├── agents.py        # specialist agents: focus, prompts, structured-output parsing
├── dispatcher.py    # routing + model tiers + parallel execution
├── synthesizer.py   # de-dup, ranking, verdict, summary
├── render.py        # text / markdown / json renderers
└── cli.py           # `quorum review` entry point
tests/               # deterministic suite (mock provider, no keys)
```

## Testing

```bash
python -m unittest discover -s tests -t .
```

No API keys, no network — the suite runs entirely against the mock provider.

## Roadmap

- Inline GitHub PR comments (line-anchored) via the Action
- Per-repo config file (`.quorum.toml`) for agent/severity tuning
- A "fix-it" agent that proposes patches for accepted findings
- PyPI release

## License

MIT © Sumanth Kumar M — see [LICENSE](LICENSE).

> Built as an exploration of practical multi-agent orchestration: routing, model-tiering, structured outputs, and the reliability glue that makes a panel of agents usable in CI. Contributions welcome.
