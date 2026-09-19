#!/usr/bin/env python3
"""
scripts/ingest_pdf_knowledge.py — AI-Powered PDF Developer Documentation & Knowledgebase Importer.

Extracts text, code blocks, AST signatures, architectural concepts, and semantic triples
from PDF files using the Sovereign Student Planner LLM (moe-sovereign-planner-olmo3-7b) and ingests into:
  1. Neo4j Knowledge Graph (:Document, :Page, :Chunk, :CodeEntity, :Concept, plus extracted relational triples)
  2. ChromaDB Vector Store (collection: moe_pdf_knowledge with rich metadata & AI summaries)

Usage:
  # Ingest single PDF with default AI extraction (moe-sovereign-planner-olmo3-7b on N04-RGTX):
  python3 scripts/ingest_pdf_knowledge.py --path /path/to/fastapi_guide.pdf

  # Ingest directory of PDFs:
  python3 scripts/ingest_pdf_knowledge.py --path /path/to/pdf_docs_folder/

  # Ingest without AI (regex-only fallback mode):
  python3 scripts/ingest_pdf_knowledge.py --path /path/to/doc.pdf --no-ai

  # Dry-run preview:
  python3 scripts/ingest_pdf_knowledge.py --path /path/to/doc.pdf --dry-run
"""

import argparse
import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("ingest_pdf_knowledge")

# ── Load .env if present ───────────────────────────────────────────────────────
def _load_env_file():
    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k not in os.environ:
                        os.environ[k] = v

_load_env_file()

def _get_neo4j_credentials():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD") or os.getenv("NEO4J_PASS") or ""
    return uri, user, password

def _get_chroma_config():
    host = os.getenv("CHROMA_HOST", "localhost")
    port = int(os.getenv("CHROMA_PORT", "8000"))
    return host, port

def _get_ai_config():
    endpoint = os.getenv("PLANNER_URL") or os.getenv("OLLAMA_RGTX_URL") or "http://192.168.155.224:11435"
    model = os.getenv("PLANNER_MODEL") or "hf.co/h3rb3rn/moe-sovereign-planner-olmo3-7b:Q4_K_M"
    return endpoint.rstrip("/"), model

def _hash_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

# ── AST & Code Extraction ──────────────────────────────────────────────────────
def extract_code_entities(text: str) -> List[Dict[str, str]]:
    """Extracts classes, functions, and decorators using AST with regex fallback."""
    entities: List[Dict[str, str]] = []
    seen = set()

    # Try AST extraction on python code blocks
    code_blocks = re.findall(r"```(?:python|py)?\n(.*?)```", text, re.DOTALL)
    for code in code_blocks:
        try:
            parsed = ast.parse(code)
            for node in ast.walk(parsed):
                if isinstance(node, ast.ClassDef):
                    if node.name not in seen:
                        seen.add(node.name)
                        bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
                        sig = f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
                        entities.append({"name": node.name, "type": "class", "signature": sig})
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name not in ("__init__", "__str__", "__repr__") and node.name not in seen:
                        seen.add(node.name)
                        args = [a.arg for a in node.args.args]
                        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                        sig = f"{prefix} {node.name}({', '.join(args)})"
                        entities.append({"name": node.name, "type": "function", "signature": sig})
        except Exception:
            pass

    # Regex fallback for text without explicit markdown code fences
    classes = re.findall(r"\bclass\s+([A-Za-z0-9_]+)\b", text)
    for c in classes:
        if c not in seen:
            seen.add(c)
            entities.append({"name": c, "type": "class", "signature": f"class {c}"})

    funcs = re.findall(r"\b(?:async\s+)?def\s+([A-Za-z0-9_]+)\s*\(", text)
    for f in funcs:
        if f not in ("__init__", "__str__", "__repr__") and f not in seen:
            seen.add(f)
            entities.append({"name": f, "type": "function", "signature": f"def {f}(...)"})

    decorators = re.findall(r"@[A-Za-z0-9_.]+", text)
    for d in decorators[:5]:
        if d not in seen:
            seen.add(d)
            entities.append({"name": d, "type": "decorator", "signature": d})

    return entities

def extract_concepts_heuristic(text: str) -> List[str]:
    """Heuristic fallback extraction for key architectural concepts."""
    keywords = [
        "FastAPI", "Pydantic", "SQLAlchemy", "PostgreSQL", "Redis", "Valkey", "Neo4j", "ChromaDB",
        "Docker", "Kubernetes", "GraphQL", "REST", "JWT", "OAuth2", "Middleware", "Dependency Injection",
        "Asyncio", "Celery", "Kafka", "GraphRAG", "Vector Search", "Prometheus", "Grafana", "Caddy",
        "DeepSpeed", "PyTorch", "Transformers", "LoRA", "QLoRA", "SFT", "DPO", "GGUF", "eBPF", "XDP"
    ]
    found = set()
    for kw in keywords:
        if re.search(r"\b" + re.escape(kw) + r"\b", text, re.IGNORECASE):
            found.add(kw)
    return sorted(list(found))

# ── AI-Powered Knowledge Extraction via the Sovereign planner LLM ───────────────
async def extract_ai_knowledge(
    client: httpx.AsyncClient,
    text: str,
    ai_endpoint: str,
    ai_model: str,
    timeout: float = 30.0
) -> Dict[str, Any]:
    """
    Extracts semantic summary, key concepts, and formal relational triples from text
    using the lightweight sovereign student planner model.
    """
    prompt = (
        "You are an expert technical knowledge extractor for developer documentation and tutorials.\n"
        "Extract key concepts, a 1-sentence summary, and formal factual/procedural triples as strict JSON.\n\n"
        "Allowed relation types: USES, IMPLEMENTS, DEPENDS_ON, PROVIDES, EXTENDS, REGULATES, ENABLES_ACTION, PREREQUISITE_FOR, CONFIGURES\n"
        "Allowed entity types: Framework, Library, Class, Function, Protocol, Tech_Concept, DataStructure, Tool, Condition\n\n"
        "Input Text:\n"
        f"{text[:1200]}\n\n"
        "Respond ONLY with a JSON object following this exact schema, without markdown formatting or intro:\n"
        "{\n"
        '  "summary": "1-sentence summary of what this text explains",\n'
        '  "concepts": ["Concept1", "Concept2"],\n'
        '  "triples": [\n'
        '    {"s": "SubjectName", "s_type": "Framework", "r": "USES", "o": "ObjectName", "o_type": "Library"}\n'
        "  ]\n"
        "}"
    )

    url = f"{ai_endpoint}/api/generate"
    payload = {
        "model": ai_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 300}
    }

    try:
        resp = await client.post(url, json=payload, timeout=timeout)
        if resp.status_code == 200:
            raw_content = resp.json().get("response", "").strip()
            # Extract JSON from potential markdown fence
            json_match = re.search(r"\{.*\}", raw_content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return {
                    "summary": data.get("summary", ""),
                    "concepts": data.get("concepts", []),
                    "triples": data.get("triples", [])
                }
    except Exception as e:
        logger.debug("AI extraction failed: %s — falling back to heuristics", e)

    return {
        "summary": text[:150].strip() + "...",
        "concepts": extract_concepts_heuristic(text),
        "triples": []
    }

# ── PDF Parser Engine ─────────────────────────────────────────────────────────
def parse_pdf_document(pdf_path: Path, max_chars_per_chunk: int = 1200) -> Dict[str, Any]:
    """Parses PDF page by page into structured chunks with code entities."""
    try:
        import pypdf
    except ImportError:
        logger.error("Package 'pypdf' not found. Run: pip install pypdf")
        sys.exit(1)

    reader = pypdf.PdfReader(str(pdf_path))
    doc_info = {
        "file_name": pdf_path.name,
        "file_path": str(pdf_path.resolve()),
        "total_pages": len(reader.pages),
        "chunks": []
    }

    chunk_index = 0
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if not text.strip():
            continue

        paragraphs = text.split("\n\n")
        current_chunk = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current_chunk) + len(para) <= max_chars_per_chunk:
                current_chunk += ("\n\n" if current_chunk else "") + para
            else:
                if current_chunk:
                    c_id = f"{pdf_path.stem}_p{page_num}_c{chunk_index}_{_hash_id(current_chunk)}"
                    doc_info["chunks"].append({
                        "id": c_id,
                        "page_number": page_num,
                        "chunk_index": chunk_index,
                        "text": current_chunk,
                        "code_entities": extract_code_entities(current_chunk),
                        "concepts": extract_concepts_heuristic(current_chunk),
                        "summary": "",
                        "triples": []
                    })
                    chunk_index += 1
                current_chunk = para

        if current_chunk:
            c_id = f"{pdf_path.stem}_p{page_num}_c{chunk_index}_{_hash_id(current_chunk)}"
            doc_info["chunks"].append({
                "id": c_id,
                "page_number": page_num,
                "chunk_index": chunk_index,
                "text": current_chunk,
                "code_entities": extract_code_entities(current_chunk),
                "concepts": extract_concepts_heuristic(current_chunk),
                "summary": "",
                "triples": []
            })
            chunk_index += 1

    return doc_info

async def enrich_chunks_with_ai(
    doc_info: Dict[str, Any],
    ai_endpoint: str,
    ai_model: str,
    concurrency: int = 4
) -> None:
    """Enriches all chunks of a document using async batch AI extraction."""
    chunks = doc_info["chunks"]
    if not chunks:
        return

    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        async def _process_chunk(chunk: Dict[str, Any]):
            async with semaphore:
                ai_data = await extract_ai_knowledge(client, chunk["text"], ai_endpoint, ai_model)
                if ai_data.get("summary"):
                    chunk["summary"] = ai_data["summary"]
                if ai_data.get("concepts"):
                    # Merge heuristic and AI concepts
                    merged = set(chunk["concepts"]) | set(ai_data["concepts"])
                    chunk["concepts"] = sorted(list(merged))
                if ai_data.get("triples"):
                    chunk["triples"] = ai_data["triples"]

        tasks = [_process_chunk(c) for c in chunks]
        await asyncio.gather(*tasks)

# ── Database Ingestion ─────────────────────────────────────────────────────────
def ingest_pdf_to_chroma(doc_info: Dict[str, Any], host: str, port: int, dry_run: bool = False) -> int:
    chunks = doc_info["chunks"]
    if dry_run or not chunks:
        return len(chunks)

    try:
        import chromadb
        client = chromadb.HttpClient(host=host, port=port)
        collection = client.get_or_create_collection(name="moe_pdf_knowledge")

        ids = [c["id"] for c in chunks]
        documents = [c["text"] for c in chunks]
        metadatas = [{
            "source_file": doc_info["file_name"],
            "page_number": c["page_number"],
            "chunk_index": c["chunk_index"],
            "summary": c.get("summary", "")[:500],
            "concepts": ", ".join(c["concepts"][:15]),
            "code_entities": ", ".join([e["name"] for e in c["code_entities"][:10]]),
            "triples_count": len(c.get("triples", []))
        } for c in chunks]

        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return len(chunks)
    except Exception as e:
        logger.warning("ChromaDB ingestion failed for %s: %s", doc_info["file_name"], e)
        return 0

def ingest_pdf_to_neo4j(doc_info: Dict[str, Any], uri: str, user: str, password: str, dry_run: bool = False) -> int:
    chunks = doc_info["chunks"]
    if dry_run or not chunks or not password:
        if not password:
            logger.info("Neo4j password unconfigured — skipping Neo4j graph write")
        return 0

    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(uri, auth=(user, password))

        query = """
        MERGE (d:Document {name: $doc.file_name})
        SET d.file_path = $doc.file_path, d.total_pages = $doc.total_pages, d.updated_at = timestamp()

        WITH d
        UNWIND $chunks AS item
        MERGE (p:Page {doc: $doc.file_name, number: item.page_number})
        MERGE (d)-[:HAS_PAGE]->(p)

        MERGE (c:Chunk {id: item.id})
        SET c.text = substring(item.text, 0, 1000),
            c.summary = item.summary,
            c.chunk_index = item.chunk_index,
            c.updated_at = timestamp()
        MERGE (p)-[:CONTAINS_CHUNK]->(c)

        WITH c, item
        UNWIND item.code_entities AS entity
        MERGE (e:CodeEntity {name: entity.name})
        SET e.type = entity.type, e.signature = entity.signature
        MERGE (c)-[:DEFINES_CODE]->(e)

        WITH c, item
        UNWIND item.concepts AS concept_name
        MERGE (k:Concept {name: concept_name})
        MERGE (c)-[:MENTIONS_CONCEPT]->(k)

        WITH c, item
        UNWIND item.triples AS tr
        MERGE (s:Concept {name: tr.s})
        SET s.type = coalesce(tr.s_type, 'Tech_Concept')
        MERGE (o:Concept {name: tr.o})
        SET o.type = coalesce(tr.o_type, 'Tech_Concept')
        MERGE (s)-[rel:RELATION {type: tr.r}]->(o)
        SET rel.confidence = 0.95, rel.source_chunk = c.id, rel.updated_at = timestamp()
        """

        with driver.session() as session:
            session.run(query, doc={
                "file_name": doc_info["file_name"],
                "file_path": doc_info["file_path"],
                "total_pages": doc_info["total_pages"]
            }, chunks=chunks)

            # Link sequential chunks within the document for workflow traversal
            seq_query = """
            MATCH (d:Document {name: $doc.file_name})-[:HAS_PAGE]->(:Page)-[:CONTAINS_CHUNK]->(c:Chunk)
            WITH c ORDER BY c.chunk_index
            WITH collect(c) AS chunk_list
            UNWIND range(0, size(chunk_list) - 2) AS idx
            WITH chunk_list[idx] AS c1, chunk_list[idx+1] AS c2
            MERGE (c1)-[:NEXT_CHUNK]->(c2)
            """
            session.run(seq_query, doc={"file_name": doc_info["file_name"]})

        driver.close()
        return len(chunks)
    except Exception as e:
        logger.warning("Neo4j ingestion failed for %s: %s", doc_info["file_name"], e)
        return 0

# ── Main CLI Runner ────────────────────────────────────────────────────────────
async def main_async():
    parser = argparse.ArgumentParser(description="PDF Knowledge & Developer Documentation AI Importer")
    parser.add_argument("--path", "-p", required=True, help="Path to a PDF file or directory containing PDFs")
    parser.add_argument("--no-ai", action="store_true", help="Disable AI extraction, use regex-only fallback")
    parser.add_argument("--ai-endpoint", default=None, help="Inference endpoint for AI extraction (default: http://192.168.155.224:11435)")
    parser.add_argument("--ai-model", default=None, help="LLM for extraction (default: PLANNER_MODEL)")
    parser.add_argument("--concurrency", type=int, default=4, help="Concurrent AI extraction requests")
    parser.add_argument("--dry-run", action="store_true", help="Parse PDF without writing to DBs")
    args = parser.parse_args()

    target_path = Path(args.path).resolve()
    if not target_path.exists():
        logger.error("Specified path does not exist: %s", target_path)
        sys.exit(1)

    pdf_files: List[Path] = []
    if target_path.is_file():
        if target_path.suffix.lower() == ".pdf":
            pdf_files.append(target_path)
        else:
            logger.error("Specified file is not a .pdf: %s", target_path)
            sys.exit(1)
    elif target_path.is_dir():
        pdf_files = sorted(list(target_path.glob("**/*.pdf")))

    if not pdf_files:
        logger.info("No PDF files found at path: %s", target_path)
        sys.exit(0)

    neo4j_uri, neo4j_user, neo4j_pass = _get_neo4j_credentials()
    chroma_host, chroma_port = _get_chroma_config()
    default_endpoint, default_model = _get_ai_config()

    ai_endpoint = args.ai_endpoint or default_endpoint
    ai_model = args.ai_model or default_model
    use_ai = not args.no_ai

    logger.info("==================================================================")
    logger.info("🚀 MOE SOVEREIGN PDF KNOWLEDGE IMPORTER")
    logger.info("  Target Path   : %s (%d PDF files)", target_path, len(pdf_files))
    logger.info("  AI Extraction : %s", "ENABLED" if use_ai else "DISABLED (Regex Only)")
    if use_ai:
        logger.info("  AI Model      : %s @ %s", ai_model, ai_endpoint)
    logger.info("  Neo4j URI     : %s", neo4j_uri)
    logger.info("  Chroma Host   : %s:%d", chroma_host, chroma_port)
    logger.info("  Dry Run       : %s", args.dry_run)
    logger.info("==================================================================")

    total_pages = 0
    total_chunks = 0
    total_triples = 0
    start_time = time.time()

    for pdf_file in pdf_files:
        logger.info("Parsing PDF: %s ...", pdf_file.name)
        doc_info = parse_pdf_document(pdf_file)

        c_cnt = len(doc_info["chunks"])
        total_pages += doc_info["total_pages"]
        total_chunks += c_cnt

        if use_ai and c_cnt > 0 and not args.dry_run:
            logger.info("  -> Extracting semantic concepts & triples via %s ...", ai_model)
            await enrich_chunks_with_ai(doc_info, ai_endpoint, ai_model, concurrency=args.concurrency)
            file_triples = sum(len(c.get("triples", [])) for c in doc_info["chunks"])
            total_triples += file_triples
            logger.info("  -> AI extracted %d relational triples & concepts.", file_triples)

        if c_cnt > 0:
            c_res = ingest_pdf_to_chroma(doc_info, chroma_host, chroma_port, dry_run=args.dry_run)
            n_res = ingest_pdf_to_neo4j(doc_info, neo4j_uri, neo4j_user, neo4j_pass, dry_run=args.dry_run)
            logger.info("  -> Ingested: %d chunks to ChromaDB, %d graph nodes to Neo4j.", c_res, n_res)

    elapsed = time.time() - start_time
    logger.info("==================================================================")
    logger.info("✅ PDF Ingestion Completed in %.2fs!", elapsed)
    logger.info("  Total PDF Files     : %d", len(pdf_files))
    logger.info("  Total Pages         : %d", total_pages)
    logger.info("  Total Chunks        : %d", total_chunks)
    logger.info("  Total AI Triples    : %d", total_triples)
    logger.info("==================================================================")

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
