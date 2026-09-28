---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.8-27B
tags:
- compound-ai
- judge
- quality-assurance
- evaluation
- paraconsistent
- reasoning
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/judge-evaluation-sft
pipeline_tag: text-generation
library_name: transformers
---

# ⚖️ MoE Sovereign Judge 27B GGUF (`moe-sovereign-judge-27b`)
*High-Precision Compound-AI Quality Evaluation, Paraconsistent Reconciliation & Zero-Fallback Judge*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.8 27B](https://img.shields.io/badge/Base_Model-Qwen3.8--27B-violet.svg)](https://huggingface.co/Qwen/Qwen3.8-27B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-sovereign-judge-27b`** is a specialized 27-billion parameter LLM fine-tuned on the **EuroHPC LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs) for high-assurance, paraconsistent quality evaluation within the **MoE Sovereign** compound-AI platform.

Built on top of **`Qwen3.8-27B`**, this model serves as the primary **Sovereign Judge** responsible for:
1. **Semantic Code & Solution Verification:** Evaluating complex systems code (C++20 lock-free data structures, Linux eBPF/XDP filters, Rust memory safety).
2. **Paraconsistent Knowledge Reconciliation:** Reconciling conflicting retrieved knowledge nodes, provenance data, and temporal policy updates without hallucinating.
3. **Structured JSON Output:** Producing strict, schema-compliant JSON evaluation verdicts containing `quality_score`, `factuality_score`, `overall_score`, and detailed analytical rationale.

This model directly replaces the previous `sovereign-judge:35b-q4km` checkpoint, eliminating socket timeouts, lowering VRAM requirements, and completely eliminating `UNSCORED_FALLBACK` verdicts.

---

## ⚙️ Model Details & Training Infrastructure

* **Base Model:** `Qwen/Qwen3.8-27B`
* **Training Supercomputer:** **EuroHPC LUMI-G** (CSC Finland)
* **Hardware Allocation:** 1 Node (8× AMD Instinct™ MI250X 128GB OAM GPUs, 112 CPU cores)
* **Job Execution:** Slurm Job ID `21263413` (`moe_expert_ensemble_pipeline`)
* **Training Duration:** 24 hours, 39 minutes, 57 seconds (`1-00:39:57`)
* **Training Methodology:** Supervised Fine-Tuning (SFT) + Direct Preference Optimization (DPO) on verified MoE Sovereign execution traces.
* **Format & Quantization:** GGUF (`Q4_K_M`), 262144 context length.

---

## 📊 Benchmark Verification Highlights

Evaluated against the **MoE Sovereign Scientific Multidisciplinary Benchmark** (August 2026):

| Benchmark Category | Task | Judge Verdict Score | Deterministic Verification |
| :--- | :--- | :---: | :---: |
| **Paraconsistent Knowledge** | `sci-graphrag-02` (Graph Reconciliation) | **`9.4 / 10.0`** 🌟 | **`10.0 / 10.0`** (100% Factually Grounded) |
| **Systems Programming** | `sci-sysprog-01` (Lock-Free MPSC Ring Buffer) | **`7.0 / 10.0`** | **`10.0 / 10.0`** (100% Memory Barrier Compliant) |
| **Network Infrastructure** | `sci-sysprog-02` (eBPF XDP Map Sync) | **`7.0 / 10.0`** | **`10.0 / 10.0`** (100% Kernel Verified) |
| **Governance & Sovereignty** | `sci-governance-01` (Technical Sovereignty) | **`6.2 / 10.0`** | **`8.0 / 10.0`** (Policy Grounded) |

---

## 🚀 Deployment & Ollama Integration

### 1. Modelfile Configuration

Create a file named `Modelfile`:

```dockerfile
FROM ./moe-sovereign-judge-27b-q4km.gguf

TEMPLATE """{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
"""

PARAMETER stop "<|im_start|>"
PARAMETER stop "<|im_end|>"
PARAMETER temperature 0.1
PARAMETER num_ctx 262144
PARAMETER num_predict 512

SYSTEM """You are the MoE Sovereign Scientific Quality Judge. Evaluate the provided solution strictly against factual, technical, and mathematical correctness requirements. Respond ONLY with a valid JSON object."""
```

### 2. Import into Ollama

```bash
# Pull model directly or build from GGUF
ollama create sovereign-judge:27b-q4km -f Modelfile
```

---

## 📜 Citation & License

* **License:** Apache 2.0
* **Repository:** [https://github.com/moe-sovereign/moe-sovereign](https://github.com/moe-sovereign/moe-sovereign)
* **Documentation:** [https://docs.moe-sovereign.org](https://docs.moe-sovereign.org)
