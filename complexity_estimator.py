"""
Complexity Estimator — heuristic query routing without an LLM call.

Routing rules:
  trivial   → 1 subtask, tier-1 model, no research/GraphRAG
  moderate  → standard MoE, no thinking node
  complex   → full stack (GraphRAG + web + thinking node)

Heuristics (no LLM, no network):
  1. Token count of the request
  2. Multi-step markers (keywords)
  3. Domain markers (law, medicine, math)
  4. Code/config markers
  5. Simple factual questions / single-word queries
  6. AIC signal: zlib compressibility as Kolmogorov complexity proxy
     (Kolmogorov 1965; Chaitin 1966 — algorithmic information theory)
"""

from __future__ import annotations
import os
import re
import zlib
import logging
from typing import Literal, Optional

# --- MODIFICATION START ---
# Added ONNX and Transformers runtime imports with try/except fallbacks
# to support ML-based complexity classification with a robust heuristic fallback.
logger = logging.getLogger("moe.complexity")

_ort_imported = False
_transformers_imported = False
_np_imported = False

try:
    import onnxruntime as ort
    _ort_imported = True
except ImportError:
    logger.warning("onnxruntime is not installed. Falling back to heuristic complexity estimation.")

try:
    from transformers import AutoTokenizer
    _transformers_imported = True
except ImportError:
    logger.warning("transformers is not installed. Falling back to heuristic complexity estimation.")

try:
    import numpy as np
    _np_imported = True
except ImportError:
    logger.warning("numpy is not installed. Falling back to heuristic complexity estimation.")

ComplexityLevel = Literal["trivial", "moderate", "complex", "memory_recall"]
COMPLEXITY_CLASSES: list[ComplexityLevel] = ["trivial", "moderate", "complex", "memory_recall"]

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ONNX_PATH = os.path.join(_SCRIPT_DIR, "models", "complexity_deberta.onnx")
_DEFAULT_TOKENIZER_PATH = os.path.join(_SCRIPT_DIR, "models", "complexity_tokenizer")

COMPLEXITY_ONNX_PATH = os.getenv("SOVEREIGN_COMPLEXITY_ONNX_PATH", _DEFAULT_ONNX_PATH)
COMPLEXITY_TOKENIZER_PATH = os.getenv("SOVEREIGN_COMPLEXITY_TOKENIZER_PATH", _DEFAULT_TOKENIZER_PATH)

_onnx_session = None
_tokenizer = None

def init_complexity_classifier() -> None:
    """Initializes the ONNX inference session and tokenizer for complexity estimation.

    Loads the DeBERTa model and tokenizer from disk. Falls back to None values on failure,
    which triggers the heuristic complexity path.
    """
    global _onnx_session, _tokenizer
    if not _ort_imported or not _transformers_imported or not _np_imported:
        return
    if not os.path.exists(COMPLEXITY_ONNX_PATH) or not os.path.exists(COMPLEXITY_TOKENIZER_PATH):
        logger.warning(
            f"ONNX model or tokenizer not found. Paths:\n"
            f"  ONNX: {COMPLEXITY_ONNX_PATH}\n"
            f"  Tokenizer: {COMPLEXITY_TOKENIZER_PATH}\n"
            f"Complexity estimation will fall back to heuristic rules."
        )
        return

    try:
        providers = ['CPUExecutionProvider']
        if ort.get_device() == 'GPU':
            available = ort.get_available_providers()
            if 'MIGraphXExecutionProvider' in available:
                providers.insert(0, 'MIGraphXExecutionProvider')
            elif 'ROCMExecutionProvider' in available:
                providers.insert(0, 'ROCMExecutionProvider')
            elif 'CUDAExecutionProvider' in available:
                providers.insert(0, 'CUDAExecutionProvider')
        
        _onnx_session = ort.InferenceSession(COMPLEXITY_ONNX_PATH, providers=providers)
        _tokenizer = AutoTokenizer.from_pretrained(COMPLEXITY_TOKENIZER_PATH, local_files_only=True)
        logger.info(f"🎯 ONNX complexity classifier loaded from {COMPLEXITY_ONNX_PATH}")
    except Exception as e:
        logger.error(f"❌ Failed to load ONNX complexity classifier: {e}")
        _onnx_session = None
        _tokenizer = None

# --- MODIFICATION END ---

# ── Thresholds ───────────────────────────────────────────────────────────────
_TRIVIAL_TOKEN_MAX  = 15   # queries with ≤15 words → trivial candidate
_COMPLEX_TOKEN_MIN  = 80   # queries with ≥80 words → always complex

# ── AIC thresholds (Kolmogorov complexity proxy via zlib) ────────────────────
# Compressibility = 1 - (compressed_len / raw_len). High = redundant = trivial.
# Tuned empirically: dense technical prose lands at 0.10–0.25; simple
# conversational text at 0.35–0.60; highly repetitive prompts above 0.65.
_AIC_TRIVIAL_FLOOR   = 0.55   # compressibility > 0.55 + short → trivial boost
_AIC_COMPLEX_CEILING = 0.15   # compressibility < 0.15 + ≥35 words → complex boost


def _aic_compressibility(text: str) -> Optional[float]:
    """Kolmogorov complexity proxy via zlib compression ratio.

    Returns a value in [0.0, 1.0] where high means the text is information-
    sparse (repetitive / trivial) and low means information-dense (complex).

    Returns None for texts shorter than 30 bytes (too short to be meaningful).

    Mathematical basis:
        Kolmogorov (1965) / Chaitin (1966) — the algorithmic information
        content (AIC) of a string is the length of its shortest description.
        Lossless compression (zlib/DEFLATE = LZ77 + Huffman) is a practical
        upper-bound approximation: if the string compresses well, it contains
        redundant structure and is relatively low-information.

    Args:
        text: The query string to analyse.

    Returns:
        Float in [0.0, 1.0] or None if text is too short.
    """
    encoded = text.encode("utf-8")
    if len(encoded) < 30:
        return None
    compressed = zlib.compress(encoded, level=9)
    return 1.0 - len(compressed) / len(encoded)

# ── Multi-step markers → complex ─────────────────────────────────────────────
_COMPLEX_MARKERS = re.compile(
    r'\b(vergleiche?n?|analysiere?n?|erkläre? warum|untersuche?n?|bewerte?n?|evaluiere?n?|'
    r'entwirf|entwickle?n?|plane?n?|implementiere?n?|refaktoriere?n?|optimiere?n?|'
    r'unterschied|vor- und nachteile?|pros? and cons?|step[- ]by[- ]step|'
    r'schritt für schritt|warum|wie genau|inwiefern|welche auswirkungen|'
    r'compare|analyze|explain why|evaluate|design|implement|optimize)\b',
    re.I,
)

# ── Memory-recall markers → skip research, read conversation history ─────────
# Questions that reference something the user said earlier in the conversation.
# These should NEVER trigger web search — the answer is in the chat history.
_MEMORY_RECALL_RE = re.compile(
    r'\b(was habe ich (gesagt|erwähnt|genannt)|what did i (say|tell|mention)|'
    r'ich habe (gesagt|erwähnt|genannt|dir gesagt)|i (said|told you|mentioned)|'
    r'wie hieß|wie war|du hast|you said|you told me|'
    r'aus unserem (gespräch|chat|dialog)|in our (conversation|chat|session)|'
    r'erinner(e|st) dich|kannst du dich erinnern|remember when|remember what|'
    r'ich habe (dir )?vorhin|weißt du noch|do you remember|'
    r'welche (datenbank|port|ip|adresse|name|version|limit|schlüssel|key|team)'
    r'\s+(habe ich|hatte ich|hab ich|have i|did i)\b)\b',
    re.I,
)

# ── Research-question markers → complex ──────────────────────────────────────
# Questions referencing named papers, studies, authors, or databases require
# multi-source research (3+ searches) and should never be capped at max_tasks=2.
_RESEARCH_MARKERS = re.compile(
    r'\b(paper|article|study|studies|journal|publication|published|according to|'
    r'researcher|professor|author|et al\.?|arxiv|doi|isbn|pubchem|orcid|'
    r'database|dataset|classification|compound|species|genus|wikipedia|'
    r'museum|collection|archive|standard|regulation|nonnative|invasive|'
    r'transcript|video|episode|season|series|channel)\b',
    re.I,
)

# ── Domain markers → at least moderate ───────────────────────────────────────
_DOMAIN_MARKERS = re.compile(
    r'\b(§+\s*\d+|bgh|bverfg|awmf|s3-leitlinie?|icd-\d+|dosierung|wirkstoff|'
    r'differentialdiagnose?|subnetz|cidr|bgp|ospf|ldap|oauth|openid|'
    r'integral|ableitung|differentialgleichung|eigenwert|fourier|'
    r'sql|cypher|neo4j|docker|kubernetes|terraform|ansible)\b',
    re.I,
)

# ── Code/config markers → moderate ───────────────────────────────────────────
_CODE_MARKERS = re.compile(
    r'```|`[^`]+`|\bdef \b|\bclass \b|\bfunction\b|\bimport \b|'
    r'\{["\']|\[\s*\{|<[a-z]+>|#!/',
    re.I,
)

# ── Trivial markers: factual questions / definitions ─────────────────────────
_TRIVIAL_MARKERS = re.compile(
    r'^(was ist|what is|wer ist|who is|wann ist|when is|wo ist|where is|'
    r'wie viel|how much|wie viele|how many|nenne|list|zeige mir|show me|'
    r'übersetze?|translate)\b',
    re.I,
)


def estimate_complexity(query: str) -> ComplexityLevel:
    """Returns the estimated complexity of a query without an LLM call.

    Combines five keyword heuristics with an AIC signal (zlib compressibility
    as a Kolmogorov complexity proxy) to resolve ambiguous cases where the
    word-count and keyword rules disagree.

    Args:
        query: The user's request text.

    Returns:
        'trivial' | 'moderate' | 'complex'
    """
    words = query.split()
    n = len(words)

    # CC-internal utility requests (topic detection, title generation) are
    # long English instructions and were misclassified as "complex" — they
    # must be trivial so downstream cost tiers stay minimal even when the
    # fast path is disabled.
    _q_low = query[:400].lower()
    if ("analyze if this message indicates a new conversation topic" in _q_low
            or "write a 5-10 word title" in _q_low
            or "generate a concise title" in _q_low):
        return "trivial"

    # Memory-recall questions: answered from chat history, no research needed.
    # Check FIRST — even a long "do you remember what I said about the database?" is memory_recall.
    if _MEMORY_RECALL_RE.search(query):
        return "memory_recall"

    # --- MODIFICATION START ---
    # Try running the DeBERTa ONNX classifier.
    # If the libraries/model are not available or inference fails, fall back to rule-based heuristics.
    global _onnx_session, _tokenizer
    if _onnx_session is None:
        init_complexity_classifier()

    if _onnx_session is not None and _tokenizer is not None:
        try:
            encodings = _tokenizer(query, truncation=True, max_length=64, return_tensors="np")
            inputs = {
                "input_ids": encodings["input_ids"].astype(np.int64),
                "attention_mask": encodings["attention_mask"].astype(np.int64)
            }
            outputs = _onnx_session.run(["logits"], inputs)
            logits = outputs[0][0]  # shape (4,)
            pred_idx = int(np.argmax(logits))
            predicted_level = COMPLEXITY_CLASSES[pred_idx]
            logger.info(f"🎯 ONNX Complexity Classifier: {predicted_level!r} for query: {query!r}")
            return predicted_level
        except Exception as e:
            logger.error(f"❌ ONNX complexity inference failed, falling back to heuristics: {e}")

    # --- Fallback Heuristics ---
    # Hard length limits decide immediately
    if n >= _COMPLEX_TOKEN_MIN:
        return "complex"

    # Multi-step markers → immediately complex
    if _COMPLEX_MARKERS.search(query):
        return "complex"

    # Research-question markers → complex (requires multiple paper/database lookups)
    if _RESEARCH_MARKERS.search(query):
        return "complex"

    # Short factual questions take priority over domain markers
    # ("What is Docker?" is trivial, not moderate)
    if n <= _TRIVIAL_TOKEN_MAX and _TRIVIAL_MARKERS.search(query):
        return "trivial"

    # Very short queries without domain markers → trivial
    if n <= 8 and not _DOMAIN_MARKERS.search(query) and not _CODE_MARKERS.search(query):
        return "trivial"

    # ── AIC tie-breaker: resolve ambiguous moderate candidates ───────────────
    # Only applied when keyword heuristics would return "moderate" — the AIC
    # signal can push the estimate up (complex) or down (trivial) when the text
    # is sufficiently long to yield a meaningful compressibility score.
    aic = _aic_compressibility(query)
    if aic is not None:
        if aic < _AIC_COMPLEX_CEILING and n >= 35:
            # Information-dense, long prompt: keyword heuristics underestimate.
            return "complex"
        if aic > _AIC_TRIVIAL_FLOOR and n <= _TRIVIAL_TOKEN_MAX:
            # Highly repetitive or low-information short prompt.
            return "trivial"

    # Domain markers → at least moderate
    has_domain = bool(_DOMAIN_MARKERS.search(query))

    # Code block → moderate
    has_code = bool(_CODE_MARKERS.search(query))

    if has_domain or has_code:
        return "moderate"

    # Default: moderate
    return "moderate"


def complexity_routing_hint(level: ComplexityLevel) -> dict:
    """Returns routing hints for the planner.

    Returns dict with:
      - max_tasks: maximum number of subtasks
      - skip_research: True = no web research node
      - skip_graph: True = no GraphRAG node
      - skip_thinking: True = no thinking node
      - force_tier1: True = use tier-1 models only
    """
    if level == "memory_recall":
        # Pure conversation-history lookup — no external research, no GraphRAG.
        # The answer lives in the injected chat_history; skip everything else.
        return {
            "max_tasks":      1,
            "skip_research":  True,
            "skip_graph":     True,
            "skip_thinking":  True,
            "force_tier1":    True,
        }
    if level == "trivial":
        return {
            "max_tasks":      1,
            "skip_research":  True,
            "skip_graph":     True,
            "skip_thinking":  True,
            "force_tier1":    True,
        }
    elif level == "moderate":
        return {
            "max_tasks":      3,
            "skip_research":  False,
            "skip_graph":     False,
            "skip_thinking":  True,
            "force_tier1":    False,
        }
    else:  # complex
        return {
            # Soft planner budget ("TASK BUDGET" in the planner prompt). The hard
            # contract ceiling is PLANNER_MAX_TASKS. Default kept at 4; raising it
            # is a separate, measured decision (see runbook gate G3).
            "max_tasks":      int(os.getenv("PLANNER_BUDGET_COMPLEX", "4")),
            "skip_research":  False,
            "skip_graph":     False,
            "skip_thinking":  False,
            "force_tier1":    False,
        }
