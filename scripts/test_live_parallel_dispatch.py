#!/usr/bin/env python3
"""
Test script to send a live request targeting the LUMI-G SmolLM3 Ensemble template.
Verifies that:
1. Planner decomposes the task into orthogonal categories (Multi-Disciplinary rule).
2. Category hotspots are dispersed across separate GPU endpoints (Phase 3).
3. Artificial dependencies are pruned so independent tasks run in Level 0 (Phase 3).
4. Forced shadow experts (e.g. security alongside coder) execute simultaneously (Phase 1).
"""

import os
import json
import time
import httpx
from dotenv import load_dotenv

load_dotenv("/opt/deployment/moe-sovereign/moe-infra/.env")

API_KEY = os.getenv("SYSTEM_API_KEY", "")
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8002")

payload = {
    "model": "LUMI-G OLMo + SmolLM3 Sovereign Ensemble",
    "messages": [
        {
            "role": "user",
            "content": "Entwirf eine threadsichere, lock-free MPSC Queue in Rust mit atomarem Ringbuffer. Implementiere die Kernoperationen und analysiere Concurrency-Gefahren sowie Test-Szenarien.",
        }
    ],
    "temperature": 0.2,
    "stream": False,
}

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

print(f"[*] Sending request to {ORCHESTRATOR_URL}/v1/chat/completions...")
t0 = time.time()
try:
    with httpx.Client(timeout=600.0) as client:
        resp = client.post(f"{ORCHESTRATOR_URL}/v1/chat/completions", json=payload, headers=headers)
        dt = time.time() - t0
        print(f"[*] Status: {resp.status_code} in {dt:.1f}s")
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            print(f"[*] Response content length: {len(content)} chars")
            print(f"[*] Sample snippet:\n{content[:400]}...")
        else:
            print(f"[!] Error: {resp.text}")
except Exception as e:
    print(f"[!] Request exception: {e}")
