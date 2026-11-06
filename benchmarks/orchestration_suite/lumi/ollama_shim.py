#!/usr/bin/env python3
"""Ollama-API shim in front of vLLM servers, for research stacks on LUMI-G.

Several orchestrator paths are Ollama-native (planner/judge via /api/chat with num_ctx, guard,
/api/ps warm-state checks). Registering vLLM directly as api_type "openai" would exercise
different code than production. This shim speaks the Ollama API the orchestrator uses and
forwards to one vLLM OpenAI server per model, so the stack runs with api_type "ollama".

Known deviations from real Ollama (documented, surfaced instead of silently ignored):
  - options.num_ctx is ignored; the context is fixed by vLLM --max-model-len per model
  - requests carrying "tools" are rejected with 501 (not used on the orchestration path)
  - /api/pull never downloads; unknown models return 404

Config (JSON file passed with --config):
  {"models": {"<ollama model name>": {"url": "http://127.0.0.1:8101", "context_length": 32768,
                                       "family": "olmo3", "parameter_size": "7B"}},
   "embed_url": "http://127.0.0.1:11434"}
"""
import argparse
import datetime as dt
import json
import time

DONE_REASON = {"stop": "stop", "length": "length", "tool_calls": "stop", None: "stop"}


class ShimError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def openai_request(body: dict, stream: bool) -> dict:
    """Translate an Ollama /api/chat body into an OpenAI chat-completions body for vLLM."""
    if body.get("tools"):
        raise ShimError(501, "tools are not supported by the research shim")
    opts = body.get("options") or {}
    out = {"model": body["model"], "messages": [], "stream": stream}
    for m in body.get("messages") or []:
        msg = {"role": m.get("role", "user"), "content": m.get("content", "")}
        out["messages"].append(msg)
    mapping = {"temperature": "temperature", "top_p": "top_p", "top_k": "top_k", "seed": "seed", "stop": "stop",
               "num_predict": "max_tokens", "repeat_penalty": "repetition_penalty", "presence_penalty": "presence_penalty",
               "frequency_penalty": "frequency_penalty", "min_p": "min_p"}
    for k, v in opts.items():
        if k in mapping and v is not None and not (k == "num_predict" and v < 0):
            out[mapping[k]] = v
    fmt = body.get("format")
    if fmt == "json":
        out["response_format"] = {"type": "json_object"}
    elif isinstance(fmt, dict):
        out["response_format"] = {"type": "json_schema", "json_schema": {"name": "schema", "schema": fmt}}
    if "think" in body:
        out["chat_template_kwargs"] = {"enable_thinking": bool(body["think"])}
    if stream:
        out["stream_options"] = {"include_usage": True}
    return out


def ollama_chat_response(model: str, oai: dict, started: float) -> dict:
    choice = (oai.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = oai.get("usage") or {}
    elapsed = int((time.monotonic() - started) * 1e9)
    message = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("reasoning_content"):
        message["thinking"] = msg["reasoning_content"]
    return {"model": model, "created_at": now_iso(), "message": message, "done": True,
            "done_reason": DONE_REASON.get(choice.get("finish_reason"), "stop"), "total_duration": elapsed,
            "load_duration": 0, "prompt_eval_count": usage.get("prompt_tokens", 0), "prompt_eval_duration": 0,
            "eval_count": usage.get("completion_tokens", 0), "eval_duration": elapsed}


def generate_as_chat(body: dict) -> dict:
    """Ollama /api/generate (non-raw) is a templated single turn; map it onto /api/chat."""
    if body.get("raw"):
        raise ShimError(501, "raw generate is not supported by the research shim")
    messages = ([{"role": "system", "content": body["system"]}] if body.get("system") else []) + \
               [{"role": "user", "content": body.get("prompt", "")}]
    return {k: v for k, v in body.items() if k not in ("prompt", "system", "raw", "context", "template")} | {"messages": messages}


def tags(models: dict) -> dict:
    return {"models": [{"name": n, "model": n, "modified_at": now_iso(), "size": 0, "digest": "",
                        "details": {"family": m.get("family", ""), "parameter_size": m.get("parameter_size", ""),
                                    "quantization_level": "BF16", "format": "safetensors"}} for n, m in models.items()]}


def ps(models: dict) -> dict:
    exp = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    return {"models": [{"name": n, "model": n, "size": 1, "size_vram": 1, "expires_at": exp,
                        "context_length": m.get("context_length", 0), "digest": ""} for n, m in models.items()]}


def show(models: dict, name: str) -> dict:
    if name not in models:
        raise ShimError(404, f"model '{name}' not found")
    m = models[name]
    arch = m.get("family", "llama")
    return {"modelfile": "", "parameters": "", "template": "", "capabilities": ["completion"],
            "details": {"family": arch, "parameter_size": m.get("parameter_size", ""), "quantization_level": "BF16"},
            "model_info": {"general.architecture": arch, f"{arch}.context_length": m.get("context_length", 0)}}


def build_app(cfg: dict):
    import httpx
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response, StreamingResponse

    models = cfg["models"]
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10))
    app = FastAPI(title="ollama-shim")

    def backend(name: str) -> str:
        if name not in models:
            raise ShimError(404, f"model '{name}' not found, try pulling it first")
        return models[name]["url"].rstrip("/")

    @app.exception_handler(ShimError)
    async def _shim_error(_req, exc: ShimError):
        return JSONResponse(status_code=exc.status, content={"error": str(exc)})

    @app.get("/api/version")
    async def version():
        return {"version": "0.0.0-research-shim"}

    @app.get("/api/tags")
    async def api_tags():
        return tags(models)

    @app.get("/api/ps")
    async def api_ps():
        return ps(models)

    @app.post("/api/show")
    async def api_show(req: Request):
        body = await req.json()
        return show(models, body.get("model") or body.get("name", ""))

    @app.post("/api/pull")
    async def api_pull(req: Request):
        body = await req.json()
        backend(body.get("model") or body.get("name", ""))
        return {"status": "success"}

    async def chat(body: dict):
        url = backend(body["model"]) + "/v1/chat/completions"
        stream = body.get("stream", True)  # Ollama streams unless told otherwise
        started = time.monotonic()
        payload = openai_request(body, stream)
        if not stream:
            r = await client.post(url, json=payload)
            if r.status_code != 200:
                raise ShimError(r.status_code, r.text[:500])
            return ollama_chat_response(body["model"], r.json(), started)

        async def gen():
            usage, reason = {}, None
            async with client.stream("POST", url, json=payload) as r:
                if r.status_code != 200:
                    yield json.dumps({"error": (await r.aread()).decode()[:500]}) + "\n"
                    return
                async for line in r.aiter_lines():
                    if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                        continue
                    chunk = json.loads(line[6:])
                    usage = chunk.get("usage") or usage
                    for ch in chunk.get("choices") or []:
                        reason = ch.get("finish_reason") or reason
                        delta = ch.get("delta") or {}
                        if delta.get("content") or delta.get("reasoning_content"):
                            msg = {"role": "assistant", "content": delta.get("content") or ""}
                            if delta.get("reasoning_content"):
                                msg["thinking"] = delta["reasoning_content"]
                            yield json.dumps({"model": body["model"], "created_at": now_iso(), "message": msg, "done": False}) + "\n"
            final = ollama_chat_response(body["model"], {"choices": [{"message": {}, "finish_reason": reason}], "usage": usage}, started)
            yield json.dumps(final) + "\n"
        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/api/chat")
    async def api_chat(req: Request):
        return await chat(await req.json())

    @app.post("/api/generate")
    async def api_generate(req: Request):
        body = await req.json()
        res = await chat(generate_as_chat(body))
        if isinstance(res, dict):
            res["response"] = res.pop("message")["content"]
        return res  # streaming generate is not used on the orchestration path; chat chunks are returned as-is

    async def _forward_embed(path: str, req: Request):
        r = await client.post(cfg["embed_url"].rstrip("/") + path, content=await req.body(), headers={"content-type": "application/json"})
        return Response(content=r.content, status_code=r.status_code, media_type="application/json")

    @app.post("/api/embed")
    async def api_embed(req: Request):
        return await _forward_embed("/api/embed", req)

    @app.post("/api/embeddings")
    async def api_embeddings(req: Request):
        return await _forward_embed("/api/embeddings", req)

    @app.get("/v1/models")
    async def v1_models():
        return {"object": "list", "data": [{"id": n, "object": "model", "owned_by": "research-shim"} for n in models]}

    @app.post("/v1/chat/completions")
    async def v1_chat(req: Request):
        body = await req.json()
        url = backend(body.get("model", "")) + "/v1/chat/completions"
        if body.get("stream"):
            async def gen():
                async with client.stream("POST", url, json=body) as r:
                    async for chunk in r.aiter_raw():
                        yield chunk
            return StreamingResponse(gen(), media_type="text/event-stream")
        r = await client.post(url, json=body)
        return Response(content=r.content, status_code=r.status_code, media_type="application/json")

    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    a = ap.parse_args()
    import uvicorn
    uvicorn.run(build_app(json.load(open(a.config))), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
