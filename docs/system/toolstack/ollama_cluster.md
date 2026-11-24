# Ollama Cluster — Multi-Node LLM Inference

## What is Ollama?

Ollama is a local LLM inference server that loads models in GGUF format and exposes an OpenAI-compatible REST API. It manages model loading, VRAM allocation, and parallel requests internally.

Sovereign MoE runs Ollama on **multiple inference nodes with heterogeneous GPU hardware**. The orchestrator communicates directly with each node via the `INFERENCE_SERVERS` configuration — no gateway layer is required.

## Why Multi-Node on Heterogeneous Hardware?

The cluster does not have a uniform GPU generation. It combines inference nodes with modern GPUs (Ampere/Ada Lovelace architecture, CUDA 8.6/8.9) and legacy GPU nodes with older Maxwell/Kepler architecture (CUDA 5.2/3.5).

**The problem with legacy Kepler GPUs:** Ollama by default requires CUDA ≥ 5.0 and uses newer CUDA APIs not available on Kepler (CUDA 3.5). The standard Ollama build fails on Kepler-based nodes.

**Solution: Ollama37 fork** — A community fork that supports CUDA 3.7 (Kepler architecture) and includes older cuBLAS paths. Legacy nodes run on this fork; modern nodes run standard upstream Ollama.

## Quantization: q8_0

All models are operated at the `q8_0` quantization level:

| Quantization | Bits per weight | VRAM (70B model) | Quality loss |
|---|---|---|---|
| f16 | 16 | ~140 GB | none (reference) |
| q8_0 | 8 | ~70 GB | < 0.5% |
| q4_0 | 4 | ~35 GB | 3–8% |
| q2_K | 2 | ~18 GB | 15–25% |

`q8_0` is the optimal trade-off for the cluster: near-full model quality close to f16, but half the VRAM usage compared to float16. For legacy nodes with limited VRAM per GPU, smaller models (7B/14B) are used in `q8_0`.

## Flash Attention

Flash Attention is an algorithmically more efficient attention kernel that reduces the quadratic VRAM requirement of standard attention for long sequences to linear.

**Availability:**

- Modern GPUs (Ampere+): Flash Attention 2 fully supported
- Maxwell-based nodes: Flash Attention 1 (slower fallback)
- Kepler-based nodes: No Flash Attention — Ollama37 uses the classic cuBLAS attention path

Ollama activates Flash Attention automatically when the hardware supports it. No manual configuration required.

## Configuration

```bash
# .env
# INFERENCE_SERVERS — JSON array of configured inference endpoints
# Configure via Admin UI → Configuration → Inference Servers
# Example:
# INFERENCE_SERVERS='[{"url":"http://<node1>:11434","name":"N04-RTX"},{"url":"http://<node2>:11434","name":"N06-M10"}]'
```

Inference servers are registered and managed in the **Admin UI → Configuration** panel.
No static config file is needed — the orchestrator reads the list at startup from `.env`.

### Per-server fields and timeouts

Each entry of `INFERENCE_SERVERS` may carry these optional fields (besides `name` and `url`):

| Field | Meaning |
|---|---|
| `timeout` | Read timeout in seconds for native/proxy inference calls to this node. Overrides `INFERENCE_DEFAULT_TIMEOUT`. |
| `vram_gb` | Total VRAM of the host in GB. Drives routing, pre-evict and the `vram_high` alert, so it must be the real total (e.g. 4× Tesla M10 = 32). |
| `gpu_count` | Number of GPUs of the host. |

`INFERENCE_DEFAULT_TIMEOUT` (environment variable, seconds) is the single fallback for every
server or user connection without an explicit `timeout`. The code default is `300`; `.env.example`
suggests `1800`. Example for a slow-prefill node:

```bash
INFERENCE_DEFAULT_TIMEOUT=1800
# INFERENCE_SERVERS entry with its own limit:
# {"name":"N11-TM10","url":"http://<node>:11434","timeout":18000,"vram_gb":32,"gpu_count":4}
```

**Streaming semantics:** for streamed responses the timeout is the **maximum gap between two
bytes** (`read=`), not the total duration. It therefore has to cover model loading plus prompt
prefill until the first token arrives; on slow GPUs (Maxwell/M10, roughly 23 tok/s prefill) a
~10k-token agent prompt alone can exceed 300 s. The Ollama proxy (`ollama-proxy.py`) uses an
upstream limit of 1800 s for inference paths, so a larger per-server `timeout` has no effect
beyond that when the node is reached through the proxy.

### Routing `model@node`

A request such as `qwen3.6:35b@N11-TM10` is routed natively only if **both** hold:

1. the node name after `@` matches a `name` in `INFERENCE_SERVERS` **exactly**, and
2. the user has a matching `model_endpoint` permission (`model@node` or `*@node`).

Otherwise the request does **not** fail: it silently falls back to the orchestrator. A typo in the
node name (for example `@N11-M10` instead of `@N11-TM10`) therefore looks like a working request
but is not answered by the intended node.

Observed example: `qwen3.6:35b@N11-M10` returned a response after about 520 s with roughly 1,000–12,000
prompt tokens instead of 15, because it was answered by the orchestrator. `N11-M10` points to the same
host (`192.168.155.231`) as `N11-TM10` but the user had no `model_endpoint` permission for it. The correct
name is `@N11-TM10`.

## Measured node performance (qwen3.6:35b, 2026-11-25)

Measurement taken by the operator's infrastructure session; the raw data are not part of this repository. Treat the numbers as **single-run indications**, not as a benchmark: 2 short runs (mean) and 1 large run per configuration, run sequentially, no repetition.

**Setup:** model `qwen3.6:35b`, all three nodes warm with `context_length` 262144. Each node was queried directly (Ollama `:11434/v1`) and through the MoE gateway (`:8002/v1`, model `qwen3.6:35b@<node>`), streaming. Short prompt ≈ 52 tokens; large prompt ≈ 16,000 tokens with a unique random identifier per run (no KV-cache hit). "Time to first chunk" is the first content chunk, which for a reasoning model is already reasoning text. Prefill = prompt tokens / time to first chunk. Decode is measured in chunks/s and approximates tokens/s.

| | N04-RTX | N02-M60-01 | N11-TM10 |
|---|---|---|---|
| Short: time to first chunk, direct / gateway | 1.28 / 1.71 s | 1.33 / 1.37 s | 2.07 / 1.03 s |
| Short: decode, direct / gateway | 26.7 / 27.0 | 18.6 / 20.1 | 8.8 / 9.4 |
| Large: prompt tokens, direct / gateway | 15,977 / 15,976 | 15,490 / 15,985 | 16,266 / 16,267 |
| Large: time to first chunk, direct / gateway | 10.1 / 10.6 s | 235 / 245 s | 870 / 875 s |
| Large: prefill, direct / gateway | 1,581 / 1,509 tok/s | 66.0 / 65.3 tok/s | 18.7 / 18.6 tok/s |
| Large: decode, direct / gateway | 23.6 / 25.1 | 14.0 / 14.5 | 5.9 / 6.3 |

**Findings**
1. The gateway (a pure Ollama-to-OpenAI conversion) adds no measurable cost. Prompt-token counts match between direct and gateway; deviations up to 5 % are within the noise of a single run.
2. N04-RTX prefills about 24× faster than N02-M60 and about 85× faster than N11-TM10; decode is about 3× and 4× faster.
3. Decode slows down with large context (N11 8.8 → 5.9, N02 18.6 → 14.0).
4. Consequence for agentic clients such as OpenCode: one step with more than 10k prompt tokens needs a little over 9 minutes for prefill alone on N11 (an observed README run took 39 minutes). N04-RTX is the sensible choice for interactive work; N11 only for batch/audit.

**Limitations:** one large run per configuration; the N02 prompt was slightly smaller (15,490 tokens direct), which flatters its prefill figure only marginally.

### N11 hardware note (open point, cause not confirmed)

`ki-m10-node11` (4× Tesla M10, i5-3470T) shows a CPU package temperature of 70–76 °C at idle (limits max 87 / crit 91) at only 2–7 % daily load according to `atop`, while the board sensors read 27–29 °C. Thermal-throttle counters since boot: 630,842 (package) and 183,964 (core). During the large prefill one CPU thread runs permanently at 100 %. A likely cause is the CPU cooling (thermal paste, dust, fan), not load. This is a hypothesis to check on site, not a verified cause.

## Dynamic VRAM Management

VRAM management is handled entirely by Ollama on each node:

- **Ollama** manages VRAM per node internally and queues requests when the GPU is saturated
- **Expert routing** in MoE Sovereign selects the appropriate node by matching the expert template's configured inference server
- **Priority routing** is achieved through template assignment — high-priority workloads bind to dedicated nodes via the Admin UI

This design scales to any number of GPU nodes without changing the orchestrator code.

## Model Inventory (example)

```bash
# Check available models on an inference node
curl http://<inference-node>:11434/api/tags

# Download a model
curl -X POST http://<inference-node>:11434/api/pull \
  -d '{"name": "qwen2.5:72b-instruct-q8_0"}'
```

Model assignment to expert categories: see [`docs/experts/index.md`](../../experts/index.md)
