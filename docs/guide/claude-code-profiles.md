# Claude Code Profiles

Claude Code profiles control how the MoE Sovereign orchestrator handles requests
from Claude Code CLI, VS Code extension, and other Anthropic API clients.
Each profile maps to a different processing mode with distinct trade-offs.

## Three Reference Profiles

### Native (Direct LLM)

```
Profile: cc-ref-native
Mode:    native
```

The request is forwarded **directly** to a single LLM (e.g., `phi4:14b-fp16`)
without any MoE pipeline involvement. The model handles tool calls natively.

- **Latency**: 5-30 seconds
- **Use case**: Quick edits, simple bug fixes, interactive coding
- **Trade-off**: No multi-expert synthesis, no GraphRAG, no knowledge accumulation

### Reasoning (Thinking Node)

```
Profile: cc-ref-reasoning
Mode:    moe_reasoning
```

The request passes through the MoE pipeline with the **Thinking Node** enabled.
The LLM performs chain-of-thought reasoning with `<think>` blocks before generating
the response.

- **Latency**: 30-120 seconds
- **Use case**: Architecture decisions, complex debugging, code review
- **Trade-off**: Deeper analysis but slower; no parallel expert routing

### Orchestrated (Full Pipeline)

```
Profile: cc-ref-orchestrated
Mode:    moe_orchestrated
```

The full MoE pipeline: **Planner** decomposes the task, **parallel expert LLMs**
process sub-tasks, **Merger** synthesizes results, **Judge** evaluates quality,
and **GraphRAG** accumulates knowledge for future requests.

- **Latency**: 2-10 minutes
- **Use case**: Deep research, multi-domain synthesis, knowledge-enriched analysis
- **Trade-off**: Highest quality but impractical for interactive coding

## Choosing a Profile

| Scenario | Recommended Profile |
|----------|-------------------|
| Fix a typo or syntax error | Native |
| Add a simple feature | Native |
| Debug a complex race condition | Reasoning |
| Architecture review | Reasoning |
| Security audit of a codebase | Orchestrated |
| Research + implementation plan | Orchestrated |
| Multi-file refactoring with tests | Reasoning |

## Configuration

### Admin UI

Navigate to **CC Profile** in the admin navigation. Each profile has:

| Field | Description |
|-------|-------------|
| `name` | Display name shown in clients |
| `moe_mode` | `native`, `moe_reasoning`, or `moe_orchestrated` |
| `tool_model` | LLM for tool execution (e.g., `phi4:14b-fp16`) |
| `tool_endpoint` | Inference server node (e.g., `N04-RTX`) |
| `expert_template_id` | Expert template for orchestrated mode (optional) |
| `tool_max_tokens` | Max output tokens for tool calls — see note below |
| `reasoning_max_tokens` | Max tokens for thinking blocks — see note below |
| `tool_choice` | `auto`, `required`, or `any` |

#### `tool_max_tokens` and `reasoning_max_tokens` — Template is the Source of Truth

!!! warning "Profile values must not exceed the template's `context_window`"
    When a profile references an expert template via `expert_template_id`, the template's
    `context_window` per expert category is the **authoritative upper bound**.

    Setting `tool_max_tokens: 65536` in the profile while the template defines
    `context_window: 32768` is a misconfiguration: the orchestrator will silently cap the
    value and log a warning — but Claude Code never learns that its advertised token budget
    is wrong. The session appears to work, but responses are shorter than expected with no
    explanation.

    **Rule:** Set `tool_max_tokens` ≤ template `context_window`. When you change the
    hardware or switch to a model with a different context window, update the
    **template first**, then align the profile.

    ```
    Template context_window: 32768   ← source of truth (set in Admin UI → Expert Templates)
    Profile tool_max_tokens: 32768   ← must be ≤ template value
    Profile reasoning_max_tokens: 32768
    ```

    The orchestrator validates this at session build time (Phase 6 of CC session resolution)
    and logs a `WARNING` with a fix hint if the values diverge.

### User Portal

Users can create personal profiles under **My Templates** > **CC Profiles**.
Personal profiles override admin-assigned profiles.

### API Key Binding

Each API key can be bound to a specific CC profile:

1. Admin UI > **Users** > select user > **API Keys**
2. Set the **CC Profile** dropdown for the key
3. All requests with that key will use the bound profile

### Client Configuration

Point Claude Code to your MoE Sovereign instance:

```bash
# Claude Code CLI
export ANTHROPIC_BASE_URL=https://your-moe-instance.example.com
export ANTHROPIC_API_KEY=moe-sk-xxxxxxxx...

# VS Code settings.json
{
  "claude-code.apiEndpoint": "https://your-moe-instance.example.com",
  "claude-code.apiKey": "moe-sk-xxxxxxxx..."
}
```

## Innovator Profiles

The **Innovator** profile family (`cc-innovator-*`) is designed for Claude Code
power users who want the full MoE pipeline with different quality/speed trade-offs.
All three profiles use `moe_orchestrated` mode with dedicated expert templates.

### Profile Comparison

| Profile | ID | Tool Model | Target Latency | Thinking | Max Tokens |
|---------|-----|-----------|----------------|----------|------------|
| Fast | `cc-innovator-fast` | `phi4:14b-fp16` | 30-90s | off | 4,096 |
| Balanced | `cc-innovator-balanced` | `Qwen3-Coder-Next` | 2-5 min | on | 8,192 / 16K reasoning |
| Deep | `cc-innovator-deep` | `Qwen3-Coder-Next` | 5-15 min | on | 8,192 / 32K reasoning |

**Key differences:**

- **Fast** uses `tool_choice: required` with lightweight models and `stream_think: false`
  for minimal overhead. Best for rapid iteration cycles.
- **Balanced** enables thinking blocks and escalates to domain-specialist models
  via T2 fallback. Good default for everyday development.
- **Deep** uses the largest available models with a security-analyst expert category
  and an assertive system prompt requiring complete, production-grade code.
  Best for security audits, architecture reviews, and complex refactoring.

### 5-Epoch Benchmark Results

A controlled benchmark across 5 consecutive runs measures the **accumulation effect**
of the MoE knowledge pipeline. Each epoch re-runs the same test suite; GraphRAG
accumulates knowledge from prior runs, improving accuracy and reducing latency.

| Epoch | Avg Score | Avg Latency | Latency vs Epoch 1 |
|------:|----------:|------------:|--------------------:|
| 1 | 5.2 / 10 | 280s | baseline |
| 2 | 6.4 / 10 | 125s | 0.45x |
| 3 | 7.1 / 10 | 72s | 0.26x |
| 4 | 7.8 / 10 | 45s | 0.16x |
| 5 | 8.1 / 10 | 30s | 0.11x |

**Accumulation effect:** By Epoch 5, the system delivers **9.3x faster** responses
than Epoch 1 while simultaneously improving answer quality by 56%. This is driven
by three mechanisms:

1. **GraphRAG context enrichment** — prior synthesis results are stored as
   `SYNTHESIS_INSIGHT` relations and injected into future expert prompts
2. **L2 plan cache** — identical task decompositions hit the Valkey SHA-256
   plan cache, skipping the planner LLM entirely
3. **Model warmth** — sticky sessions and the model registry keep frequently
   used models loaded in VRAM, eliminating cold-start overhead

The accumulation effect is most pronounced in Epochs 1-3 (steep improvement) and
plateaus around Epoch 4-5 as the knowledge graph saturates for the test domain.

---

## Download Reference Profiles

Pre-configured profile JSONs are available for download:

- [`cc-ref-native.json`](https://github.com/moe-sovereign/moe-sovereign/blob/main/configs/cc_profiles/downloads/cc-ref-native.json) — Direct LLM
- [`cc-ref-reasoning.json`](https://github.com/moe-sovereign/moe-sovereign/blob/main/configs/cc_profiles/downloads/cc-ref-reasoning.json) — Thinking Node
- [`cc-ref-orchestrated.json`](https://github.com/moe-sovereign/moe-sovereign/blob/main/configs/cc_profiles/downloads/cc-ref-orchestrated.json) — Full Pipeline

Replace `<YOUR_OLLAMA_HOST>` and `<YOUR_TEMPLATE_ID>` with your actual values.

## Conversation History Compression

Long MoE synthesis responses in the conversation history can fill the tool model's
context window over many turns. The orchestrator compresses older assistant and tool
messages automatically before sending the history to the tool LLM.

### How It Works

1. After converting the incoming message history to OpenAI format, assistant and tool
   messages older than **`CC_HISTORY_COMPRESS_KEEP_TURNS`** turns are inspected.
2. Messages exceeding **`CC_HISTORY_COMPRESS_THRESHOLD`** characters are condensed:
   the first 800 chars and last 200 chars are kept; the middle is replaced with
   `[…N chars — condensed for context window]`.
3. The **full original content** is cached in Redis under
   `cc:hist:<session_id>:<sha1[:12]>` with a 1-hour TTL — no data is permanently lost.
4. The existing `_trim_oai_to_budget()` then operates on the already-compressed history,
   so it needs to drop far fewer complete turns.

### Configuration (`.env`)

| Variable | Default | Effect |
|---|---|---|
| `CC_HISTORY_COMPRESS_THRESHOLD` | `3000` | Chars above which a message is compressed |
| `CC_HISTORY_COMPRESS_KEEP_TURNS` | `2` | Most recent N turns are never compressed |

Increase `CC_HISTORY_COMPRESS_THRESHOLD` for models with large context windows where
you want to retain more verbatim history. Decrease it if the tool model runs out of
context quickly.

---

## API Compatibility

The `/v1/messages` endpoint is fully compatible with the Anthropic Messages API.
Claude Code CLI, Anthropic Python SDK, and VS Code extensions work without
modification — just point `ANTHROPIC_BASE_URL` to your MoE Sovereign instance.

Supported features:

- Streaming responses (SSE)
- Tool use / function calling
- Multi-turn conversations
- System prompts
- Thinking blocks (reasoning mode)
- Image inputs (forwarded to vision-capable models)
