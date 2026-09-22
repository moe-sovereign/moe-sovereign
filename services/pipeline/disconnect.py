"""Cancel an orchestration run when its HTTP client goes away.

A non-streaming ``/v1/chat/completions`` handler awaits the whole LangGraph run. Without a watcher, a client that
disconnects (benchmark runner stopped, timeout on the caller side, aborted curl) leaves the run alive: it keeps
loading models and generating on the pinned Ollama instances, evicts the models of the next request and pollutes
measured latency. ``run_until_disconnect`` cancels the run (which closes the open Ollama connections and lets the
server abort the generation) as soon as the client is gone.

The watcher awaits ``request.receive()`` instead of polling ``request.is_disconnected()``: the app runs behind
``BaseHTTPMiddleware``, whose wrapped receive channel makes the non-blocking ``is_disconnected()`` probe return False
for a closed connection. Once the body has been consumed, ``receive()`` only completes with ``http.disconnect``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable

CANCEL_GRACE_S = 10.0


class ClientDisconnected(Exception):
    """The HTTP client closed the connection before the run finished."""


async def _wait_for_disconnect(raw_request: Any) -> None:
    while True:
        message = await raw_request.receive()
        if message.get("type") == "http.disconnect":
            return
        await asyncio.sleep(0.5)  # unexpected extra body chunk: ignore it, never busy-loop


async def _cancel_and_wait(*tasks: "asyncio.Future[Any]") -> None:
    pending = [t for t in tasks if not t.done()]
    for t in pending:
        t.cancel()
    if pending:
        # Bounded cleanup: a node that swallows the cancellation must not keep the handler alive forever.
        await asyncio.wait(pending, timeout=CANCEL_GRACE_S)


async def run_until_disconnect(raw_request: Any, awaitable: Awaitable[Any], timeout: float) -> Any:
    """Await ``awaitable`` like ``asyncio.wait_for`` but abort it when the client disconnects.

    Raises ``asyncio.TimeoutError`` when ``timeout`` elapses and ``ClientDisconnected`` when the client is gone.
    The run is always cancelled in both cases and when the calling task itself is cancelled.
    """
    task = asyncio.ensure_future(awaitable)
    watcher = asyncio.ensure_future(_wait_for_disconnect(raw_request))
    try:
        done, _ = await asyncio.wait({task, watcher}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        await _cancel_and_wait(task)
        if watcher in done:
            raise ClientDisconnected()
        raise asyncio.TimeoutError()
    except asyncio.CancelledError:
        await _cancel_and_wait(task)
        raise
    finally:
        await _cancel_and_wait(watcher)
