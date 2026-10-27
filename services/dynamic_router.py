import os
import re
import json
import time
import random
import logging
import uuid
from typing import Optional, List, Dict, Sequence
import httpx
import numpy as np
import onnxruntime as ort
import chromadb
from chromadb.utils import embedding_functions

import state
from services import routing_patterns
from admin_ui.database import _get_pool, log_dynamic_template_feedback
from config import (
    MOE_USERDB_URL, URL_MAP, API_TYPE_MAP, TOKEN_MAP, INFERENCE_SERVERS_LIST,
    EXPERT_TIER_BOUNDARY_B, PLANNER_NUM_CTX, JUDGE_NUM_CTX,
    PLANNER_MODEL as _PLANNER_MODEL_ENV, JUDGE_MODEL as _JUDGE_MODEL_ENV,
    EXPERT_MIN_DATAPOINTS, ROUTING_PATTERN_PRIOR_ENABLED,
    ROUTING_PATTERN_PRIOR_K, ROUTING_PATTERN_PRIOR_CAP,
)

logger = logging.getLogger("MOE-SOVEREIGN")

# Classes mapped to ONNX model outputs
EXPERT_CLASSES = [
    "code_reviewer",
    "creative_writer",
    "creative_writing",
    "data_analysis",
    "general",
    "legal_advisor",
    "mail_classify",
    "math",
    "precision_tools",
    "reasoning",
    "research",
    "science",
    "technical_support",
    "tool_agent"
]

COMPLEXITY_CLASSES = ["trivial", "moderate", "complex", "memory_recall"]
GATE_CLASSES = ["web_research", "graphrag"]

# Endpoints configuration — fully derived from admin-configured INFERENCE_SERVERS.
# No hardcoded URLs or tokens here; add/remove endpoints via MoE Admin UI instead.
#
# Local Ollama nodes: native API ("/api/tags", "/api/ps") requires stripping the
# OpenAI-compat "/v1" suffix that INFERENCE_SERVERS URLs carry.
OLLAMA_ENDPOINTS = {
    name: (url[:-len("/v1")] if url.endswith("/v1") else url)
    for name, url in URL_MAP.items()
    if API_TYPE_MAP.get(name) == "ollama"
}

# Cloud/OpenAI-compatible endpoints: all non-ollama entries in INFERENCE_SERVERS.
# Each carries its own URL and token — no additional env vars needed.
# Deployments without any cloud endpoint configured will simply skip cloud discovery.
CLOUD_ENDPOINTS: list[dict] = [
    {
        "name":  s["name"],
        "url":   s["url"].rstrip("/"),
        "token": s.get("token", ""),
    }
    for s in INFERENCE_SERVERS_LIST
    if s.get("api_type", "ollama") != "ollama"
    and s.get("enabled", True)
    and not s.get("no_auto_fallback")
]

# Heuristic helpers to generate training targets and fallbacks
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

_COMPLEX_MARKERS = re.compile(
    r'\b(vergleiche?n?|analysiere?n?|erkläre? warum|untersuche?n?|bewerte?n?|evaluiere?n?|'
    r'entwirf|entwickle?n?|plane?n?|implementiere?n?|refaktoriere?n?|optimiere?n?|'
    r'unterschied|vor- und nachteile?|pros? and cons?|step[- ]by[- ]step|'
    r'schritt für schritt|warum|wie genau|inwiefern|welche auswirkungen|'
    r'compare|analyze|explain why|evaluate|design|implement|optimize)\b',
    re.I,
)

_RESEARCH_MARKERS = re.compile(
    r'\b(paper|article|study|studies|journal|publication|published|according to|'
    r'researcher|professor|author|et al\.?|arxiv|doi|isbn|pubchem|orcid|'
    r'dataset|classification|compound|species|genus|wikipedia|'
    r'museum|collection|archive|standard|regulation|nonnative|invasive|'
    r'transcript|video|episode|season|series|channel)\b',
    re.I,
)

_TRIVIAL_MARKERS = re.compile(
    r'^(was ist|what is|wer ist|who is|wann ist|when is|wo ist|where is|'
    r'wie viel|how much|wie viele|how many|nenne|list|zeige mir|show me|'
    r'übersetze?|translate)\b',
    re.I,
)

_DOMAIN_MARKERS = re.compile(
    r'\b(§+\s*\d+|bgh|bverfg|awmf|s3-leitlinie?|icd-\d+|dosierung|wirkstoff|'
    r'differentialdiagnose?|subnetz|cidr|bgp|ospf|ldap|oauth|openid|'
    r'integral|ableitung|differentialgleichung|eigenwert|fourier|'
    r'sql|cypher|neo4j|docker|kubernetes|terraform|ansible)\b',
    re.I,
)

_CODE_MARKERS = re.compile(
    r'```|`[^`]+`|\bdef \b|\bclass \b|\bfunction\b|\bimport \b|'
    r'\{["\']|\[\s*\{|<[a-z]+>|#!/',
    re.I,
)

_WEB_INTENT_RE = re.compile(
    r'\b(aktuell|news|wetter|latest|recent|web search|online|search the web|internet|'
    r'google|heute|today|recent events|current affairs|suche im web|aktuellste)\b',
    re.I,
)

def get_complexity(query: str) -> str:
    n = len(query.split())
    if _MEMORY_RECALL_RE.search(query):
        return "memory_recall"
    if n >= 80 or _COMPLEX_MARKERS.search(query) or _RESEARCH_MARKERS.search(query):
        return "complex"
    if n <= 15 and _TRIVIAL_MARKERS.search(query):
        return "trivial"
    if n <= 8 and not _DOMAIN_MARKERS.search(query) and not _CODE_MARKERS.search(query):
        return "trivial"
    has_domain = bool(_DOMAIN_MARKERS.search(query))
    has_code = bool(_CODE_MARKERS.search(query))
    if has_domain or has_code:
        return "moderate"
    return "moderate"

def get_gates(query: str, complexity: str) -> tuple[float, float]:
    if complexity in ("trivial", "memory_recall"):
        return 0.0, 0.0
    web_research = 0.0
    if complexity == "complex" or _WEB_INTENT_RE.search(query):
        web_research = 1.0
    graphrag = 1.0
    return web_research, graphrag

ROUTER_ONNX_PATH = os.getenv("SOVEREIGN_ROUTER_ONNX_PATH", "/app/models/sovereign_router.onnx")
CHROMA_HOST = os.getenv("CHROMA_HOST", "")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))

_onnx_session = None
_chroma_client = None
_template_collection = None
_embedding_function = None

_cluster_state_cache = {"ts": 0.0, "models": []}

ROUTELLM_WEIGHTS_PATH = os.getenv("ROUTELLM_WEIGHTS_PATH", "/app/models/routellm_bge_weights.json")
ROUTELLM_THRESHOLD = float(os.getenv("ROUTELLM_THRESHOLD", "0.5"))
ROUTELLM_ENABLED = os.getenv("ROUTELLM_ENABLED", "true").lower() in ("true", "1", "yes")

_routellm_w = None
_routellm_b = 0.0

def init_router():
    """Initializes the ONNX session, ChromaDB collection, and RouteLLM weights."""
    global _onnx_session, _chroma_client, _template_collection, _embedding_function, _routellm_w, _routellm_b
    
    # 1. Load ONNX model
    if os.path.exists(ROUTER_ONNX_PATH):
        try:
            # CPU Execution Provider is default and highly optimized for AVX-512
            # GPU providers (CUDA/ROCm) can be used if available
            providers = ['CPUExecutionProvider']
            if ort.get_device() == 'GPU':
                # Auto-detect ROCm (AMD) or CUDA (Nvidia) execution provider
                available = ort.get_available_providers()
                if 'MIGraphXExecutionProvider' in available:
                    providers.insert(0, 'MIGraphXExecutionProvider')
                elif 'ROCMExecutionProvider' in available:
                    providers.insert(0, 'ROCMExecutionProvider')
                elif 'CUDAExecutionProvider' in available:
                    providers.insert(0, 'CUDAExecutionProvider')
            
            _onnx_session = ort.InferenceSession(ROUTER_ONNX_PATH, providers=providers)
            logger.info(f"🎯 Sovereign Router ONNX model loaded from {ROUTER_ONNX_PATH} (providers={providers})")
        except Exception as e:
            logger.error(f"❌ Failed to load Sovereign Router ONNX model: {e}")
    else:
        logger.warning(f"⚠️ Sovereign Router ONNX model not found at {ROUTER_ONNX_PATH}")

    # 2. Initialize ChromaDB client and collection
    if CHROMA_HOST:
        try:
            _chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
            _embedding_function = embedding_functions.DefaultEmbeddingFunction()
            _template_collection = _chroma_client.get_or_create_collection(
                name="moe_template_cache",
                embedding_function=_embedding_function,
                metadata={"hnsw:space": "cosine"}
            )
            logger.info("connected to ChromaDB moe_template_cache collection.")
        except Exception as e:
            logger.error(f"❌ Failed to connect to ChromaDB moe_template_cache: {e}")

    # 3. Load RouteLLM weights
    if ROUTELLM_ENABLED:
        if os.path.exists(ROUTELLM_WEIGHTS_PATH):
            try:
                with open(ROUTELLM_WEIGHTS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _routellm_w = np.array(data["w"], dtype=np.float32)
                    _routellm_b = float(data["b"])
                logger.info(f"🎯 RouteLLM weights loaded from {ROUTELLM_WEIGHTS_PATH}")
            except Exception as e:
                logger.error(f"❌ Failed to load RouteLLM weights: {e}")
        else:
            logger.warning(f"⚠️ RouteLLM weights not found at {ROUTELLM_WEIGHTS_PATH}")


async def get_bge_embedding(prompt: str) -> Optional[np.ndarray]:
    embed_url = os.getenv("MOE_EMBED_URL", "http://moe-embed:11434").rstrip("/") + "/api/embed"
    # CPU-only container (OLLAMA_NUM_GPU=0): bge-m3 cold-start takes up to ~60 s.
    # keep_alive=-1 instructs Ollama to keep the model resident until process exit,
    # preventing repeated evict/reload cycles across requests.
    _BGE_TIMEOUT = float(os.getenv("MOE_BGE_TIMEOUT", "90"))
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "bge-m3",
                "input": [prompt],
                "keep_alive": -1,
            }
            response = await client.post(embed_url, json=payload, timeout=_BGE_TIMEOUT)
            if response.status_code == 200:
                embeds = response.json().get("embeddings", [])
                if embeds:
                    return np.array(embeds[0], dtype=np.float32)
            else:
                logger.error(f"Failed to fetch BGE embedding: status={response.status_code}, response={response.text[:200]}")
    except Exception as e:
        logger.error(f"Exception fetching BGE embedding: {e}")
    return None


async def prewarm_bge_model() -> None:
    """Fire-and-forget startup task: load bge-m3 into memory before requests arrive.

    Runs once during lifespan startup. Failure is logged but never raises so
    the orchestrator starts normally even when moe-embed is temporarily down.
    """
    embed_url = os.getenv("MOE_EMBED_URL", "http://moe-embed:11434").rstrip("/") + "/api/embed"
    _BGE_TIMEOUT = float(os.getenv("MOE_BGE_TIMEOUT", "90"))
    try:
        async with httpx.AsyncClient() as client:
            payload = {"model": "bge-m3", "input": ["warmup"], "keep_alive": -1}
            resp = await client.post(embed_url, json=payload, timeout=_BGE_TIMEOUT)
            if resp.status_code == 200:
                logger.info("✅ BGE model pre-warm complete (bge-m3 resident)")
            else:
                logger.warning("BGE pre-warm returned HTTP %d — model may be slow on first request", resp.status_code)
    except Exception as exc:
        logger.warning("BGE pre-warm failed (moe-embed unreachable?): %s", exc)


async def _run_sovereign_classifier(prompt: str) -> Optional[dict]:
    """Run the Sovereign Router ONNX classifier on prompt.

    Returns a dict with active_experts (EXPERT_CLASSES order), expert_probs,
    complexity, enable_web_research, enable_graphrag — or None if the
    classifier/embedding function is unavailable or inference fails.
    """
    global _onnx_session
    if _onnx_session is None:
        init_router()
    if _onnx_session is None or _embedding_function is None:
        return None

    try:
        # Embed user prompt using the local MiniLM function
        embeds = _embedding_function([prompt])
        embedding = np.array(embeds, dtype=np.float32)  # shape [1, 384]

        outputs = _onnx_session.run(
            ["experts", "complexity", "gates"],
            {"input_embedding": embedding}
        )

        expert_logits = outputs[0][0]      # shape [14]
        complexity_logits = outputs[1][0]  # shape [4]
        gate_logits = outputs[2][0]        # shape [2]

        expert_probs = 1.0 / (1.0 + np.exp(-expert_logits))
        gate_probs = 1.0 / (1.0 + np.exp(-gate_logits))

        active_expert_indices = np.where(expert_probs >= 0.5)[0]
        active_experts = [EXPERT_CLASSES[idx] for idx in active_expert_indices]
        if not active_experts:
            active_experts = ["general"]

        complexity_idx = np.argmax(complexity_logits)
        complexity = COMPLEXITY_CLASSES[complexity_idx]

        enable_web_research = bool(gate_probs[0] >= 0.5)
        enable_graphrag = bool(gate_probs[1] >= 0.5)
        if complexity in ("trivial", "memory_recall"):
            enable_web_research = False
            enable_graphrag = False

        return {
            "active_experts": active_experts,
            "expert_probs": expert_probs,
            "complexity": complexity,
            "enable_web_research": enable_web_research,
            "enable_graphrag": enable_graphrag,
        }
    except Exception as e:
        logger.error(f"❌ Sovereign Router ONNX inference failed: {e}")
        return None


async def _get_cluster_state() -> list[dict]:
    """Gets lists of available models and warmed state, cached for 10s."""
    now = time.monotonic()
    if now - _cluster_state_cache["ts"] < 10 and _cluster_state_cache["models"]:
        return _cluster_state_cache["models"]

    models_list = []
    async with httpx.AsyncClient() as client:
        # 1. Query local Ollama endpoints
        for node, endpoint in OLLAMA_ENDPOINTS.items():
            try:
                # Get model tags
                res = await client.get(f"{endpoint}/api/tags", timeout=3.0)
                # Get currently active / warmed models
                ps_res = await client.get(f"{endpoint}/api/ps", timeout=3.0)
                
                warmed_names = set()
                if ps_res.status_code == 200:
                    warmed_names = {m.get("name") for m in ps_res.json().get("models", [])}
                    
                if res.status_code == 200:
                    for m in res.json().get("models", []):
                        m_name = m["name"]
                        models_list.append({
                            "model_id": f"{m_name}@{node}",
                            "model_name": m_name,
                            "endpoint": node,
                            "is_warmed": m_name in warmed_names,
                            "is_local": True
                        })
            except Exception as e:
                logger.debug(f"Telemetry: failed to poll local endpoint {node}: {e}")

        # 2. Query all configured cloud/OpenAI-compatible endpoints.
        # These are derived dynamically from INFERENCE_SERVERS (MoE Admin UI) —
        # no hardcoded URLs or tokens. Each entry has its own credential.
        for cloud in CLOUD_ENDPOINTS:
            try:
                headers = {"Authorization": f"Bearer {cloud['token']}"}
                res = await client.get(
                    f"{cloud['url']}/models", headers=headers, timeout=5.0
                )
                if res.status_code == 200:
                    for m in res.json().get("data", []):
                        m_id = m["id"]
                        m_name, m_ep = m_id.split("@", 1) if "@" in m_id else (m_id, cloud["name"])
                        models_list.append({
                            "model_id":   m_id,
                            "model_name": m_name,
                            "endpoint":   m_ep,
                            "is_warmed":  False,  # Cloud models: warmed status is opaque
                            "is_local":   False,
                        })
            except Exception as e:
                logger.debug(f"Telemetry: failed to poll cloud endpoint {cloud['name']}: {e}")

    _cluster_state_cache["models"] = models_list
    _cluster_state_cache["ts"] = now
    return models_list


async def _get_thompson_score(
    model: str,
    category: str,
    query_embedding: Optional[Sequence[float]] = None,
) -> float:
    """Draws a Thompson sample for (model, category) based on Valkey performance logs.

    When the (model, category) bucket is still below EXPERT_MIN_DATAPOINTS and
    ROUTING_PATTERN_PRIOR_ENABLED, blends in a capped, distance-weighted Beta
    prior from the k nearest historical observations in embedding space
    (services/routing_patterns.py) instead of returning the flat 0.5 fallback.
    Behaviour is unchanged when the flag is off or no embedding is available.
    """
    if state.redis_client is None:
        return 0.5
    try:
        safe_model = re.sub(r"[^a-zA-Z0-9_\-]", "_", model)
        key = f"moe:perf:{safe_model}:{category}"
        data = await state.redis_client.hgetall(key)

        # Redis hgetall returns bytes keys/values
        total = int(data.get(b"total", data.get("total", 0)))
        positive = int(data.get(b"positive", data.get("positive", 0)))
        if total < EXPERT_MIN_DATAPOINTS:
            if not (ROUTING_PATTERN_PRIOR_ENABLED and query_embedding is not None):
                return 0.5
            prior_pos, prior_total = await routing_patterns.prior(
                "expert", f"{model}:{category}", query_embedding,
                ROUTING_PATTERN_PRIOR_K, ROUTING_PATTERN_PRIOR_CAP,
            )
            if prior_total <= 0.0:
                return 0.5
            alpha = positive + prior_pos + 1
            beta = (total - positive) + (prior_total - prior_pos) + 1
        else:
            alpha = positive + 1
            beta = (total - positive) + 1

        # Load penalty to avoid overloading a node
        load_penalty = float(os.getenv("THOMPSON_LOAD_PENALTY", "2.0"))
        node = model.split("@")[-1] if "@" in model else ""
        node_load = 0.0
        if node:
            load_raw = await state.redis_client.get(f"moe:load:{node}")
            if load_raw:
                node_load = float(load_raw)
        beta = beta * (1.0 + load_penalty * node_load)
        
        return random.betavariate(alpha, beta)
    except Exception:
        return 0.5


async def _score_and_allocate_model(category: str, models: list[dict], model_metadata: dict, local_only: bool, complexity: str = "moderate", interventions: list = None, force_weak: bool = False, query_embedding: Optional[Sequence[float]] = None) -> list[dict]:
    """Scores models based on parameter size, context window, warmed status, strengths, and Thompson Sampling."""
    if force_weak:
        weak_models = []
        for m in models:
            meta = model_metadata.get(m["model_id"], {})
            param_size = meta.get("parameter_size_b", 7.0)
            if m["is_local"] and param_size <= EXPERT_TIER_BOUNDARY_B:
                weak_models.append(m)
        if weak_models:
            models = weak_models
            logger.debug(f"RouteLLM forced weak models: {', '.join([m['model_id'] for m in models])}")
        else:
            logger.debug("RouteLLM: force_weak is True, but no Tier-1 models found. Falling back to all models.")

    scored_models = []
    
    # Weights for scoring formula (optimized for quality, latency-agnostic)
    W_WARMED = 0.0
    W_LOCAL = 0.1
    W_BENCH = 5.0
    W_FEEDBACK = 1.0

    for m in models:
        m_id = m["model_id"]
        is_local = m["is_local"]
        
        # Compliance Gate: block cloud endpoints completely in local_only mode
        if local_only and not is_local:
            continue

        # Fetch metadata
        meta = model_metadata.get(m_id, {})
        
        # Benchmark score: check if model specializes in target strengths, else average baseline
        bench_scores = meta.get("benchmark_scores", {})
        if category in ("code_reviewer", "creative_writer", "creative_writing") and "human_eval" in bench_scores:
            bench_val = bench_scores["human_eval"] / 100.0
        elif category == "math" and "gsm8k" in bench_scores:
            bench_val = bench_scores["gsm8k"] / 100.0
        else:
            bench_val = bench_scores.get("mmlu", 50.0) / 100.0
            
        # Draw Thompson sample for feedback rating
        t_sample = await _get_thompson_score(m_id, category, query_embedding=query_embedding)
        
        # 1. Strengths Match Bonus
        strengths = meta.get("strengths", [])
        strength_match_bonus = 0.0
        if strengths:
            # Map category to typical strengths keywords
            cat_to_strength_map = {
                "code_reviewer": "code",
                "math": "math",
                "reasoning": "reasoning",
                "creative_writer": "creative",
                "creative_writing": "creative",
                "data_analysis": "data",
                "legal_advisor": "legal",
                "mail_classify": "classification",
                "technical_support": "technical",
                "tool_agent": "reasoning"
            }
            target_str = cat_to_strength_map.get(category)
            if target_str and any(target_str in s.lower() for s in strengths):
                strength_match_bonus = 0.5  # Apply bonus for aligned expert capability

        # 2. Capacity / Size optimization based on parameter count (bonus for larger models)
        param_size = meta.get("parameter_size_b", 7.0)
        size_bonus = param_size / 70.0  # Up to +1.0 bonus for large (70B+) models

        # Score calculation
        score = (
            W_WARMED * (1.0 if m["is_warmed"] else 0.0) +
            W_LOCAL * (1.0 if is_local else 0.0) +
            W_BENCH * bench_val +
            W_FEEDBACK * t_sample +
            strength_match_bonus +
            size_bonus
        )
        
        scored_models.append({
            "model_id": m_id,
            "model_name": m["model_name"],
            "endpoint": m["endpoint"],
            "score": score,
            "context_window": meta.get("context_window", 4096)
        })
        
    # Sort models by score descending
    scored_models.sort(key=lambda x: x["score"], reverse=True)
    
    # Causal Intervention Roll (5% rate)
    if len(scored_models) > 1 and random.random() < 0.05:
        default_model = scored_models[0]["model_id"]
        # pick a random runner-up model
        runner_up_idx = random.randint(1, len(scored_models) - 1)
        intervened_model = scored_models[runner_up_idx]["model_id"]
        
        # swap them
        scored_models[0], scored_models[runner_up_idx] = scored_models[runner_up_idx], scored_models[0]
        
        if interventions is not None:
            interventions.append({
                "is_intervention": True,
                "expert": category,
                "intervened": intervened_model,
                "default": default_model
            })
            
    return scored_models


async def _match_existing_template(prompt: str) -> tuple[Optional[str], Optional[str]]:
    """Checks the semantic template cache (ChromaDB) for reusable templates."""
    if _template_collection is None:
        return None, None
    try:
        # Cosine distance similarity threshold (empirically tuned, < 0.18 is extremely close)
        results = _template_collection.query(
            query_texts=[prompt],
            n_results=1
        )
        if results and results["ids"] and results["ids"][0]:
            dist = results["distances"][0][0]
            if dist < 0.18:
                tmpl_id = results["ids"][0][0]
                tmpl_name = results["metadatas"][0][0].get("name", "")
                logger.info(f"🎯 Semantic template cache L2 hit! Reusing template '{tmpl_name}' (id={tmpl_id}, distance={dist:.4f})")
                return tmpl_id, tmpl_name
    except Exception as e:
        logger.debug(f"ChromaDB: template query error: {e}")
    return None, None


async def _save_template_to_db_and_cache(name: str, desc: str, config: dict, reasoning_trace: str, cache_query_text: str) -> str:
    """Saves the dynamic template config to Postgres and registers in ChromaDB.

    `cache_query_text` is the text indexed in ChromaDB and must match the
    shape of the text `_match_existing_template()` queries with (the raw
    prompt), so future similar prompts can retrieve this template via the
    semantic cache.
    """
    tmpl_id = f"moe-dyn-{uuid.uuid4().hex[:12]}"
    config_json = json.dumps(config)
    now = datetime_now_iso()
    
    # Save to Postgres
    dsn = MOE_USERDB_URL or ""
    if dsn:
        try:
            pool = _get_pool()
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "INSERT INTO admin_expert_templates (id, name, description, config_json, is_active, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, TRUE, %s, %s)",
                        (tmpl_id, name, f"{desc} | Reasoning: {reasoning_trace}", config_json, now, now)
                    )
            logger.debug(f"Registered template in DB: {tmpl_id}")
        except Exception as e:
            logger.error(f"❌ Failed to insert template into DB: {e}")

        # Seed dynamic_template_feedback_log so a later user rating
        # (routes/feedback.py -> update_dynamic_template_feedback_rating)
        # has a row to update.
        try:
            await log_dynamic_template_feedback(
                template_id=tmpl_id,
                prompt=cache_query_text,
                config_json=config_json,
                latency_ms=None,
                tokens_used=None,
            )
        except Exception as e:
            logger.error(f"❌ Failed to seed dynamic_template_feedback_log: {e}")

    # Register in ChromaDB Template Cache
    if _template_collection is not None:
        try:
            _template_collection.add(
                ids=[tmpl_id],
                documents=[cache_query_text],  # Same text shape as _match_existing_template()'s query
                metadatas=[{"name": name, "reasoning": reasoning_trace}]
            )
            logger.debug(f"Semantic Cache: Registered template {tmpl_id} in ChromaDB")
        except Exception as e:
            logger.error(f"❌ Failed to register template in ChromaDB cache: {e}")
            
    return tmpl_id


def _is_local_url(url: str) -> bool:
    """Determine if a URL points to a local or internal network endpoint."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        netloc = parsed.netloc or parsed.path
        host = netloc.split(":")[0].strip().lower()
        if not host:
            return False
        
        # Public commercial LLM APIs are never local
        external_domains = (
            "openai.com", "deepseek.com", "anthropic.com", "groq.com", "sambanova.ai",
            "cerebras.ai", "nvidia.com", "openrouter.ai", "mistral.ai", "deepinfra.com",
            "huggingface.co", "cohere.com", "together.xyz", "ai21.com", "google.com",
            "googleapis.com", "vertex"
        )
        if any(d in host for d in external_domains):
            return False
            
        if host in ("localhost", "127.0.0.1", "::1"):
            return True
            
        import ipaddress
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private or ip.is_loopback
        except ValueError:
            pass
            
        if host.endswith(".local") or host.endswith(".lan") or host.endswith(".home") or host.endswith(".internal"):
            return True
            
        if host.startswith("192.168.") or host.startswith("10.") or host.startswith("172.16.") or host.startswith("172.17.") or host.startswith("172.18.") or host.startswith("172.19.") or host.startswith("172.20.") or host.startswith("172.21.") or host.startswith("172.22.") or host.startswith("172.23.") or host.startswith("172.24.") or host.startswith("172.25.") or host.startswith("172.26.") or host.startswith("172.27.") or host.startswith("172.28.") or host.startswith("172.29.") or host.startswith("172.30.") or host.startswith("172.31."):
            return True
            
        if "." not in host:
            return True
    except Exception:
        pass
    return False


# ── Per-category MCP tool and skill defaults (used by fallback prompt generator) ──
# Loaded at runtime so additions to the MCP server or skill directory are picked up
# without changing this mapping. The lists name tools by their mcp_server tool name.

_CATEGORY_MCP_TOOLS: dict[str, list[str]] = {
    "math":             ["calculate", "solve_equation", "statistics_calc", "unit_convert",
                         "prime_factorize", "gcd_lcm", "roman_numeral"],
    "science":          ["calculate", "unit_convert"],
    "data_analysis":    ["statistics_calc", "json_query", "calculate", "unit_convert"],
    "legal_advisor":    ["legal_get_paragraph", "legal_search_laws", "legal_get_law_overview"],
    "technical_support":["subnet_calc", "vlsm_subnet_calc", "hash_text", "regex_extract"],
    "code_reviewer":    ["regex_extract", "hash_text"],
    "vision":           ["text_analyze", "regex_extract"],
    "precision_tools":  ["calculate", "unit_convert", "date_diff", "date_add",
                         "calendar_facts", "day_of_week", "statistics_calc", "hash_text"],
    "general":          [],
    "reasoning":        [],
    "research":         [],
    "creative_writer":  [],
    "translation":      [],
    "medical_consult":  [],
    "tool_agent":       ["calculate", "unit_convert", "regex_extract", "json_query"],
}

_CATEGORY_SKILLS: dict[str, list[str]] = {
    "data_analysis":    ["web-artifacts-builder"],
    "research":         ["web-artifacts-builder"],
    "creative_writer":  ["web-artifacts-builder"],
    "code_reviewer":    ["agentic-coder"],
    "technical_support":["agentic-coder"],
}


def _resolve_expert_tools_heuristic(category: str, prompt_hint: str = "") -> list[str]:
    """Return MCP tool names for a category, optionally extended by prompt content."""
    base = list(_CATEGORY_MCP_TOOLS.get(category, []))
    # Extend from prompt signals for uncategorised / dynamic experts
    h = prompt_hint.lower()
    if not base:
        if any(k in h for k in ("rechne", "berechne", "calculate", "compute", "math", "statistics")):
            base = _CATEGORY_MCP_TOOLS["math"]
        elif any(k in h for k in ("§", "gesetz", "recht", "legal", "paragraph", "bgb", "bgh")):
            base = _CATEGORY_MCP_TOOLS["legal_advisor"]
        elif any(k in h for k in ("subnetz", "ip", "subnet", "hash", "regex", "network")):
            base = _CATEGORY_MCP_TOOLS["technical_support"]
        elif any(k in h for k in ("data", "statistik", "csv", "json", "tabelle")):
            base = _CATEGORY_MCP_TOOLS["data_analysis"]
    return base


def _resolve_expert_skills_heuristic(category: str, prompt_hint: str = "") -> list[str]:
    """Return skill names for a category based on category defaults and prompt signals."""
    base = list(_CATEGORY_SKILLS.get(category, []))
    h = prompt_hint.lower()
    if not base:
        if any(k in h for k in ("chart", "diagramm", "html", "pdf", "report", "visuali")):
            base = ["web-artifacts-builder"]
        elif any(k in h for k in ("code", "repository", "refactor", "implement", "debug")):
            base = ["agentic-coder"]
    return base


def _generate_fallback_structured_prompts(prompt: str, active_experts: list) -> dict:
    """Generate structured/persona-based fallback system prompts with zero latency."""
    # 1. Base default prompts
    _first_exp = active_experts[0] if active_experts else "general"
    planner_prompt = (
        f"You are a specialized planner model in a Mixture of Experts (MoE) system. "
        f"Coordinate planning, tool execution, and task delegation for the following expert areas: {', '.join(active_experts)}. "
        f"You MUST always produce at least one task. "
        f"NEVER return an empty JSON array. "
        f'Minimum valid response: [{{"task": "<concrete description of what to do>", "category": "{_first_exp}"}}]'
    )
    judge_prompt = f"You are a specialized synthesis judge. Consolidate and merge responses from the following expert models: {', '.join(active_experts)}. Resolve contradictions using paraconsistent logic rules and output a unified, high-quality answer."
    
    # Add prompt-specific guidance to planner/judge based on query content
    hints = []
    if any(k in prompt.lower() for k in ("deutsch", "german", "auf deutsch", "in german")):
        hints.append("Prefer generating responses and plans in German.")
    elif any(k in prompt.lower() for k in ("english", "auf englisch", "in english")):
        hints.append("Prefer generating responses and plans in English.")
        
    if "step" in prompt.lower() or "schritt" in prompt.lower():
        hints.append("Break down the reasoning into clear, numbered steps.")
        
    if hints:
        hint_text = " " + " ".join(hints)
        planner_prompt += hint_text
        judge_prompt += hint_text

    experts_prompts = {}
    for exp in active_experts:
        sys_prompt = f"You are a specialized expert in {exp}."
        if exp == "code_reviewer":
            sys_prompt = "You are an expert software engineer and code reviewer. Analyze the code, find bugs, and suggest clean improvements."
        elif exp == "technical_support":
            sys_prompt = "You are a technical support expert. Provide clear, precise, and actionable answers to technical questions."
        elif exp == "math":
            sys_prompt = "You are a mathematics expert. Provide rigorous step-by-step mathematical reasoning and calculations."
        elif exp == "legal_advisor":
            sys_prompt = "You are a legal advisor expert. Analyze the legal context, reference relevant laws or paragraphs precisely, and provide expert legal summaries."
        elif exp == "research":
            sys_prompt = "You are a research expert. Analyze scientific literature, academic papers, and reference reliable sources clearly."
        elif exp in ("creative_writer", "creative_writing"):
            sys_prompt = "You are a creative writer. Craft engaging, expressive, and high-quality creative text matching the requested style."
        elif exp == "data_analysis":
            sys_prompt = "You are a data analyst. Interpret data tables, statistics, and trends, and explain findings with clear quantitative insights."
        elif exp == "reasoning":
            sys_prompt = "You are a logical reasoning expert. Break down complex arguments, evaluate premises, and explain logical transitions step-by-step."
        elif exp == "science":
            sys_prompt = "You are a scientific expert. Apply physics, chemistry, biology, or engineering principles to explain concepts rigorously."
        elif exp == "tool_agent":
            sys_prompt = "You are an agent expert in calling tools and APIs. Coordinate tool outputs to solve the user's request."
        elif exp == "precision_tools":
            sys_prompt = "You are a calculations expert. Perform precise formatting, unit conversion, date difference calculation, and exact arithmetic."
        elif exp == "mail_classify":
            sys_prompt = "You are an email classification expert. Categorize incoming emails, extract key metadata, and draft professional responses."
            
        if hints:
            sys_prompt += " " + " ".join(hints)
        experts_prompts[exp] = sys_prompt

    return {
        "planner_prompt": planner_prompt,
        "judge_prompt": judge_prompt,
        "experts": {
            exp: {
                "system_prompt": prompt_text,
                "mcp_tools":     _resolve_expert_tools_heuristic(exp, prompt),
                "skills":        _resolve_expert_skills_heuristic(exp, prompt),
            }
            for exp, prompt_text in experts_prompts.items()
        },
    }


async def _generate_prompt_specific_prompts(prompt: str, active_experts: list) -> dict:
    """Generate prompt-adapted system prompts for planner, judge, and active experts.
    
    If DYNAMIC_SYSTEM_PROMPTS_LLM_ENABLED is True, queries the LLM to construct
    highly tailored system prompts. Otherwise, falls back to structured/persona-based
    templates with zero latency.
    """
    enabled = os.environ.get("DYNAMIC_SYSTEM_PROMPTS_LLM_ENABLED", "false").lower() in ("true", "1", "yes")
    
    if enabled:
        try:
            from services.llm_instances import planner_llm

            # Build compact tool + skill catalogs for the LLM to choose from
            available_tools = list(state._MCP_TOOLS_DICT.keys()) if state._MCP_TOOLS_DICT else []
            tool_catalog = (
                "Available MCP tools: " + ", ".join(available_tools)
                if available_tools else "No MCP tools registered."
            )
            try:
                from services.skills import _build_skill_catalog
                skill_catalog_raw = _build_skill_catalog().strip()
                skill_catalog = skill_catalog_raw if skill_catalog_raw else "No skills registered."
            except Exception:
                skill_catalog = "No skills registered."

            system_instruction = (
                "You are an expert meta-prompter for a Mixture-of-Experts (MoE) system.\n"
                "Generate optimized, query-specific system prompts for the Planner, Judge, and each Expert.\n"
                "For each expert also decide which MCP tools and output skills would IMPROVE its output quality.\n\n"
                "Rules:\n"
                "• Only assign MCP tools that are genuinely useful for the subtask — not all tools per expert.\n"
                "• Only assign skills when the expert's output benefits from a specific format (chart, PDF, code).\n"
                "• Empty lists [] are correct when no tool or skill is needed.\n\n"
                f"{tool_catalog}\n\n"
                f"Skills catalog:\n{skill_catalog}\n\n"
                "Output ONLY a valid JSON object with this exact schema (no markdown, no extra keys):\n"
                "{\n"
                '  "planner_prompt": "string",\n'
                '  "judge_prompt": "string",\n'
                '  "experts": {\n'
                '    "<expert_category>": {\n'
                '      "system_prompt": "string",\n'
                '      "mcp_tools": ["tool_name", ...],\n'
                '      "skills": ["skill_name", ...]\n'
                '    }\n'
                '  }\n'
                "}"
            )

            user_content = (
                f"User Query: {prompt}\n"
                f"Active Expert Categories: {', '.join(active_experts)}\n\n"
                "Generate the system prompts, MCP tool lists, and skill lists."
            )

            logger.info("Generating prompt-specific system prompts (with tool/skill assignment) via LLM...")
            res = await planner_llm.ainvoke([
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content}
            ])

            content = res.content.strip()
            if content.startswith("```"):
                if "\n" in content:
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                else:
                    content = content.replace("```", "").strip()

            parsed = json.loads(content)
            if (
                isinstance(parsed, dict)
                and "planner_prompt" in parsed
                and "judge_prompt" in parsed
                and "experts" in parsed
                and isinstance(parsed["experts"], dict)
            ):
                fallback_data = _generate_fallback_structured_prompts(prompt, active_experts)
                for exp in active_experts:
                    fb_exp = fallback_data["experts"].get(exp, {})
                    exp_entry = parsed["experts"].get(exp)
                    if not isinstance(exp_entry, dict) or "system_prompt" not in exp_entry:
                        # Full fallback for this expert
                        parsed["experts"][exp] = fb_exp
                    else:
                        # Ensure mcp_tools and skills are valid lists; fall back to heuristic if absent/invalid
                        if not isinstance(exp_entry.get("mcp_tools"), list):
                            exp_entry["mcp_tools"] = fb_exp.get("mcp_tools", [])
                        if not isinstance(exp_entry.get("skills"), list):
                            exp_entry["skills"] = fb_exp.get("skills", [])
                        # Filter out any tool names not actually registered
                        if available_tools:
                            exp_entry["mcp_tools"] = [
                                t for t in exp_entry["mcp_tools"] if t in available_tools
                            ]
                return parsed
            else:
                logger.warning("LLM response did not match expected schema, falling back to structured prompts.")
        except Exception as e:
            logger.error(f"Failed to generate prompts via LLM: {e}. Falling back to structured prompts.", exc_info=True)
            
    return _generate_fallback_structured_prompts(prompt, active_experts)


async def get_dynamic_template(
    prompt: str,
    local_only: bool = False,
    user_connections: Optional[dict] = None,
    global_only: bool = False,
    user_conns_only: bool = False,
    deliberation_activation: str = "adaptive",
    query_embedding: Optional[Sequence[float]] = None,
) -> Optional[dict]:
    """Main service entrypoint: matching, ONNX routing, cluster mapping, scoring and allocation.

    Args:
        prompt:           The user's raw message.
        local_only:       When True, only local (non-cloud) endpoints are considered.
        user_connections: User's private connections dictionary.
        global_only:      When True, restrict to global admin-defined endpoints.
        user_conns_only:  When True, restrict to user-created connections.
        query_embedding:  BGE embedding of `prompt`, when the caller already
                           computed one (e.g. services/pipeline/chat.py, for
                           reuse in AgentState). Reused for RouteLLM instead
                           of a second get_bge_embedding() call, and passed
                           through to the expert Thompson sampler for
                           ROUTING_PATTERN_PRIOR_ENABLED cold-start bridging.
    """
    global _onnx_session
    if _onnx_session is None:
        init_router()
    if _onnx_session is None:
        return None

    # 1. Redundancy Detector (ChromaDB cache hit check)
    tmpl_id, tmpl_name = await _match_existing_template(prompt)
    if tmpl_id:
        # Fetch matching template from DB
        dsn = MOE_USERDB_URL or ""
        if dsn:
            try:
                pool = _get_pool()
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT config_json FROM admin_expert_templates WHERE id=%s", (tmpl_id,))
                        row = await cur.fetchone()
                        if row:
                            cached_config = json.loads(row["config_json"])
                            # config_json was stored before "id"/"name" were added to the
                            # caller's dict (see _save_template_to_db_and_cache call site) —
                            # restore them so downstream tracing (chat.py: dynamic_tmpl["id"]) works.
                            from services.deliberation.contracts import dynamic_deliberation_policy

                            cached_config["deliberation_policy"] = (
                                dynamic_deliberation_policy(deliberation_activation)
                                .model_dump(mode="json")
                            )
                            cached_config["id"] = tmpl_id
                            cached_config["name"] = tmpl_name
                            return cached_config
            except Exception as e:
                logger.error(f"Failed to fetch cached template: {e}")

    # 2. Run Gating Classifier ONNX Inference
    if _embedding_function is None:
        return None

    classification = await _run_sovereign_classifier(prompt)
    if classification is None:
        return None
    active_experts = classification["active_experts"]
    complexity = classification["complexity"]
    enable_web_research = classification["enable_web_research"]
    enable_graphrag = classification["enable_graphrag"]

    # Reuse the caller's embedding (if given) for both RouteLLM below and the
    # expert Thompson sampler's cold-start prior further down.
    embedding_arr: Optional[np.ndarray] = (
        np.asarray(query_embedding, dtype=np.float32)
        if query_embedding is not None and len(query_embedding) > 0
        else None
    )

    # Run RouteLLM classifier
    prob = None
    force_weak = False
    if ROUTELLM_ENABLED:
        if _routellm_w is None:
            init_router()
        if _routellm_w is not None:
            if embedding_arr is None:
                embedding_arr = await get_bge_embedding(prompt)
            if embedding_arr is not None:
                z = np.dot(embedding_arr, _routellm_w) + _routellm_b
                prob = 1.0 / (1.0 + np.exp(-np.clip(z, -50, 50)))
                logger.info(f"RouteLLM routing score: {prob:.4f} (threshold={ROUTELLM_THRESHOLD})")
                if prob < ROUTELLM_THRESHOLD:
                    logger.info("RouteLLM decided: WEAK model path")
                    force_weak = True
                else:
                    logger.info("RouteLLM decided: STRONG model path")

    # 3. Model Scoring and Allocation
    try:
        models = []
        if not user_conns_only:
            global_models = await _get_cluster_state()
            if global_models:
                models.extend(global_models)

        if not global_only and user_connections:
            user_pool = []
            for conn_name, conn_cfg in user_connections.items():
                for mc in conn_cfg.get("models_cache", []):
                    raw_id = mc.get("id") if isinstance(mc, dict) else str(mc)
                    base = raw_id.rsplit("@", 1)[0] if "@" in (raw_id or "") else (raw_id or "")
                    if not base:
                        continue
                    user_pool.append({
                        "model_id":   f"{base}@{conn_name}",
                        "model_name": base,
                        "endpoint":   conn_name,
                        "is_warmed":  False,
                        "is_local":   _is_local_url(conn_cfg.get("url", "")),
                    })
            if user_pool:
                models.extend(user_pool)
                logger.debug("Dynamic routing: added %d models from user connections", len(user_pool))

        if not models:
            logger.warning("Dynamic routing: no active models discovered on endpoints.")
            return None

        # Fetch model metadata profiles from DB
        model_metadata = {}
        dsn = MOE_USERDB_URL or ""
        if dsn:
            pool = _get_pool()
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT model_id, context_window, benchmark_scores, parameter_size_b, family, strengths FROM model_metadata")
                    rows = await cur.fetchall()
                    for r in rows:
                        if isinstance(r, dict):
                            m_id = r["model_id"]
                            model_metadata[m_id] = {
                                "context_window": r["context_window"],
                                "benchmark_scores": r["benchmark_scores"] if isinstance(r["benchmark_scores"], dict) else json.loads(r["benchmark_scores"] or "{}"),
                                "parameter_size_b": float(r["parameter_size_b"] or 7.0),
                                "family": r["family"] or "other",
                                "strengths": (r["strengths"] if isinstance(r["strengths"], list) else (json.loads(r["strengths"] or "[]") if isinstance(r["strengths"], str) else r["strengths"])) or []
                            }
                        else:
                            model_metadata[r[0]] = {
                                "context_window": r[1],
                                "benchmark_scores": r[2] if isinstance(r[2], dict) else json.loads(r[2] or "{}"),
                                "parameter_size_b": float(r[3] or 7.0) if len(r) > 3 else 7.0,
                                "family": (r[4] or "other") if len(r) > 4 else "other",
                                "strengths": (r[5] if isinstance(r[5], list) else (json.loads(r[5] or "[]") if isinstance(r[5], str) else r[5])) if len(r) > 5 else []
                            }

        # Select Planner and Judge models dynamically using metadata
        planner_choices = [m for m in models if m["is_local"]] if local_only else models
        if not planner_choices:
            planner_choices = models
            
        planner_meta_choices = []
        for m in planner_choices:
            meta = model_metadata.get(m["model_id"], {})
            param_size = meta.get("parameter_size_b", 7.0)
            family = (meta.get("family") or "").lower()
            planner_meta_choices.append((m, param_size, family))

        # VRAM budget: largest single-node VRAM available; models exceeding ~70% are excluded as planner/judge
        # to leave room for KV-cache and concurrent models.
        _max_vram = max((s.get("vram_gb", 0) for s in INFERENCE_SERVERS_LIST if s.get("enabled", True)), default=48)
        _vram_limit_b = _max_vram * 0.7 / 0.6  # param threshold: Q4 ≈ 0.6 GB/B params

        # 1. Select Planner: honour PLANNER_MODEL env var when available, otherwise pick
        #    the largest VRAM-safe model with strong reasoning capabilities.
        planner_meta_choices.sort(key=lambda x: x[1], reverse=True)  # Sort by param_size descending
        if _PLANNER_MODEL_ENV:
            _env_base = _PLANNER_MODEL_ENV.split(":")[0]
            _pinned = next(
                (m for m, _s, _f in planner_meta_choices if m["model_id"].split(":")[0] == _env_base),
                None,
            )
            planner_model = _pinned["model_id"] if _pinned else _PLANNER_MODEL_ENV
        else:
            best_planners = [
                m for m, size, fam in planner_meta_choices
                if size <= _vram_limit_b and (size >= 30.0 or fam in ("qwen", "llama", "deepseek"))
            ]
            planner_model = best_planners[0]["model_id"] if best_planners else planner_meta_choices[0][0]["model_id"]

        # 2. Select Judge: honour JUDGE_MODEL env var when available, otherwise pick
        #    the largest VRAM-safe model for quality synthesis.
        judge_meta_choices = list(planner_meta_choices)
        judge_meta_choices.sort(key=lambda x: x[1], reverse=True)  # Sort by param_size descending
        if _JUDGE_MODEL_ENV:
            _env_base = _JUDGE_MODEL_ENV.split(":")[0]
            _pinned = next(
                (m for m, _s, _f in judge_meta_choices if m["model_id"].split(":")[0] == _env_base),
                None,
            )
            judge_model = _pinned["model_id"] if _pinned else _JUDGE_MODEL_ENV
        else:
            vram_safe_judges = [m for m, size, _f in judge_meta_choices if size <= _vram_limit_b]
            judge_model = vram_safe_judges[0]["model_id"] if vram_safe_judges else judge_meta_choices[0][0]["model_id"]

        # Compile expert models configurations
        experts_config = {}
        justification_trace = f"Complexity={complexity}, WebSearch={enable_web_research}, GraphRAG={enable_graphrag}. "
        if prob is not None:
            justification_trace = f"RouteLLM_Score={prob:.4f} (threshold={ROUTELLM_THRESHOLD}, path={'strong' if not force_weak else 'weak'}), " + justification_trace

        # Resolve prompt-specific system prompts dynamically
        resolved_prompts = await _generate_prompt_specific_prompts(prompt, active_experts)

        interventions = []
        for exp in active_experts:
            # Allocate models for this expert category
            allocated = await _score_and_allocate_model(exp, models, model_metadata, local_only, complexity, interventions, force_weak=force_weak, query_embedding=embedding_arr)
            if not allocated:
                # If local_only is active but no local model is available, fall back to whatever is available
                allocated = await _score_and_allocate_model(exp, models, model_metadata, False, complexity, interventions, force_weak=force_weak, query_embedding=embedding_arr)
                
            if allocated:
                models_cfg = []
                # Add Primary
                primary = allocated[0]
                models_cfg.append({
                    "model": primary["model_name"],
                    "endpoint": primary["endpoint"],
                    "role": "primary"
                })
                justification_trace += f"{exp} (Primary={primary['model_id']}, score={primary['score']:.2f}). "
                
                # Add Fallback (if second best model is available)
                if len(allocated) > 1:
                    fallback = allocated[1]
                    models_cfg.append({
                        "model": fallback["model_name"],
                        "endpoint": fallback["endpoint"],
                        "role": "fallback"
                    })
                
                # Context window mapping
                ctx = primary["context_window"]
                
                # System prompt, MCP tools, and skills resolved dynamically per expert
                exp_resolved = resolved_prompts.get("experts", {}).get(exp, {})
                sys_prompt = exp_resolved.get("system_prompt") or f"You are a specialized expert in {exp}."
                mcp_tools  = exp_resolved.get("mcp_tools") or []
                skills     = exp_resolved.get("skills") or []

                experts_config[exp] = {
                    "system_prompt":  sys_prompt,
                    "context_window": ctx,
                    "models":         models_cfg,
                    "mcp_tools":      mcp_tools,
                    "skills":         skills,
                }

        # Build complete dynamic template configuration
        planner_ctx = model_metadata.get(planner_model, {}).get("context_window", 0) or 0
        if PLANNER_NUM_CTX > 0:
            planner_ctx = min(planner_ctx, PLANNER_NUM_CTX) if planner_ctx > 0 else PLANNER_NUM_CTX
        judge_ctx = model_metadata.get(judge_model, {}).get("context_window", 0) or 0
        if JUDGE_NUM_CTX > 0:
            judge_ctx = min(judge_ctx, JUDGE_NUM_CTX) if judge_ctx > 0 else JUDGE_NUM_CTX
        
        # Context-aware system prompts for planner and judge resolved dynamically
        planner_prompt = resolved_prompts.get("planner_prompt")
        if not planner_prompt:
            _first_expert = active_experts[0] if active_experts else "general"
            planner_prompt = (
                f"You are a specialized planner model in a Mixture of Experts (MoE) system. "
                f"Coordinate planning, tool execution, and task delegation for the following expert areas: {', '.join(active_experts)}. "
                f"You MUST always produce at least one task. "
                f"NEVER return an empty JSON array. "
                f"Minimum valid response: "
                f'[{{"task": "<concrete description of what to do>", "category": "{_first_expert}"}}]'
            )
            
        judge_prompt = resolved_prompts.get("judge_prompt")
        if not judge_prompt:
            judge_prompt = f"You are a specialized synthesis judge. Consolidate and merge responses from the following expert models: {', '.join(active_experts)}. Resolve contradictions using paraconsistent logic rules and output a unified, high-quality answer."
        
        from services.deliberation.contracts import dynamic_deliberation_policy

        template_config = {
            "planner_model": planner_model,
            "judge_model": judge_model,
            "planner_prompt": planner_prompt,
            "judge_prompt": judge_prompt,
            "planner_num_ctx": planner_ctx,
            "judge_num_ctx": judge_ctx,
            "enable_cache": True,
            "enable_graphrag": enable_graphrag,
            "enable_web_research": enable_web_research,
            "experts": experts_config,
            "causal_intervention": interventions[0] if interventions else None,
            "deliberation_policy": dynamic_deliberation_policy(
                deliberation_activation
            ).model_dump(mode="json"),
            "reasoning_trace": justification_trace
        }
        
        # Save to Postgres and cache in ChromaDB
        hash_suffix = uuid.uuid4().hex[:6]
        template_name = f"Dynamic Template {hash_suffix}"
        template_desc = f"Dynamic gating template compiled for prompt: {prompt[:80]}..."
        
        tmpl_id = await _save_template_to_db_and_cache(
            name=template_name,
            desc=template_desc,
            config=template_config,
            reasoning_trace=justification_trace,
            cache_query_text=prompt,
        )
        
        # Add metadata for orchestrator tracing
        template_config["id"] = tmpl_id
        template_config["name"] = template_name
        template_config["description"] = template_desc
        template_config["reasoning_trace"] = justification_trace
        
        logger.info(f"✨ Dynamic template compiled and registered: '{template_name}' (id={tmpl_id})")
        return template_config
        
    except Exception as e:
        logger.error(f"❌ Failed to score and allocate models for dynamic template: {e}", exc_info=True)
        return None

def datetime_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
