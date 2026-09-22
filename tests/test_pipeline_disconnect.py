import asyncio

import pytest

from services.pipeline.disconnect import ClientDisconnected, run_until_disconnect


class _Req:
    """Mimics the receive channel of a request whose body was consumed: blocks until the client disconnects."""

    def __init__(self, disconnect_after: float | None = None):
        self._after = disconnect_after

    async def receive(self) -> dict:
        if self._after is None:
            await asyncio.Event().wait()
        await asyncio.sleep(self._after)
        return {"type": "http.disconnect"}

    async def is_disconnected(self) -> bool:  # BaseHTTPMiddleware makes this probe useless; the helper must not use it
        return False


async def _work(seconds: float, state: dict):
    try:
        await asyncio.sleep(seconds)
        return "done"
    except asyncio.CancelledError:
        state["cancelled"] = True
        raise


@pytest.mark.asyncio
async def test_returns_result_when_client_stays():
    state: dict = {}
    assert await run_until_disconnect(_Req(), _work(0.05, state), timeout=5) == "done"
    assert not state


@pytest.mark.asyncio
async def test_cancels_run_when_client_disconnects():
    state: dict = {}
    with pytest.raises(ClientDisconnected):
        await run_until_disconnect(_Req(0.05), _work(30, state), timeout=60)
    assert state["cancelled"]


@pytest.mark.asyncio
async def test_timeout_cancels_run_and_raises_timeout_error():
    state: dict = {}
    with pytest.raises(asyncio.TimeoutError):
        await run_until_disconnect(_Req(), _work(30, state), timeout=0.1)
    assert state["cancelled"]


@pytest.mark.asyncio
async def test_cancelling_the_handler_cancels_the_run():
    state: dict = {}
    handler = asyncio.ensure_future(run_until_disconnect(_Req(), _work(30, state), timeout=60))
    await asyncio.sleep(0.1)
    handler.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handler
    assert state["cancelled"]


@pytest.mark.asyncio
async def test_run_exception_propagates():
    async def boom():
        raise ValueError("x")

    with pytest.raises(ValueError):
        await run_until_disconnect(_Req(), boom(), timeout=5)
