"""
tests/test_web_search.py — Unit tests for web_search.py.

_domain_score and _reliability_label are pure functions tested directly.
_web_search_with_citations is async; the SearxSearchWrapper is injected as a
parameter so we can pass in a mock without patching globals.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from web_search import (
    _domain_score,
    _reliability_label,
    _web_search_with_citations,
)


# ── _domain_score ──────────────────────────────────────────────────────────────

class TestDomainScore:
    def test_wikipedia_high_score(self):
        assert _domain_score("https://en.wikipedia.org/wiki/Test") == 0.95

    def test_arxiv_high_score(self):
        assert _domain_score("https://arxiv.org/abs/2301.12345") == 0.92

    def test_gov_domain(self):
        score = _domain_score("https://www.cdc.gov/flu/symptoms")
        assert score == 0.85

    def test_reddit_low_score(self):
        assert _domain_score("https://www.reddit.com/r/python/comments/xyz") == 0.38

    def test_unknown_domain_defaults_to_medium(self):
        assert _domain_score("https://www.some-random-blog.io/post/1") == 0.50

    def test_case_insensitive(self):
        score_lower = _domain_score("https://ARXIV.ORG/abs/1234")
        assert score_lower == 0.92


# ── _reliability_label ─────────────────────────────────────────────────────────

class TestReliabilityLabel:
    def test_high_threshold(self):
        assert _reliability_label(0.85) == "HIGH"
        assert _reliability_label(0.99) == "HIGH"

    def test_medium_threshold(self):
        assert _reliability_label(0.65) == "MEDIUM"
        assert _reliability_label(0.84) == "MEDIUM"

    def test_low_threshold(self):
        assert _reliability_label(0.50) == "LOW"
        assert _reliability_label(0.0) == "LOW"


# ── _web_search_with_citations ─────────────────────────────────────────────────

def _run(coro):
    # A private loop: asyncio.get_event_loop() raises once an earlier async
    # test has closed the thread's current loop, which made this file
    # order-dependent.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestWebSearchWithCitations:
    def test_none_search_returns_empty(self):
        result = _run(_web_search_with_citations(
            "test query", search=None, ddg_fallback=False,
        ))
        assert result == ""

    def test_results_sorted_by_reliability(self):
        """High-reliability sources should appear before low-reliability ones."""
        mock_search = MagicMock()
        mock_search.results = MagicMock(return_value=[
            {"snippet": "reddit post", "title": "Reddit", "link": "https://reddit.com/r/test"},
            {"snippet": "arxiv paper", "title": "ArXiv", "link": "https://arxiv.org/abs/1234"},
        ])
        with patch("asyncio.to_thread", side_effect=lambda fn, *args, **kw: asyncio.coroutine(lambda: fn(*args, **kw))()):
            pass
        # Use a simpler approach — call with a real thread wrapper
        async def run():
            return await _web_search_with_citations("test", search=mock_search)
        result = _run(run())
        # ArXiv should come before Reddit in the output
        assert result.index("arxiv") < result.index("reddit") or result.index("ArXiv") < result.index("Reddit")

    def test_fallback_to_search_run_on_empty_results(self):
        mock_search = MagicMock()
        mock_search.results = MagicMock(return_value=[])
        mock_search.run = MagicMock(return_value="fallback result")

        async def run():
            return await _web_search_with_citations("test query", search=mock_search)

        result = _run(run())
        assert result == "fallback result"

    def test_exception_returns_empty_string(self):
        mock_search = MagicMock()
        mock_search.results = MagicMock(side_effect=Exception("network error"))
        mock_search.run = MagicMock(side_effect=Exception("also failed"))

        async def run():
            return await _web_search_with_citations(
                "test query", search=mock_search, ddg_fallback=False,
            )

        result = _run(run())
        assert result == ""

    def test_empty_primary_uses_ddg_fallback(self):
        mock_search = MagicMock()
        mock_search.results.return_value = []
        mock_search.run.return_value = ""

        async def run():
            with patch(
                "web_search._ddg_search_with_citations",
                new=AsyncMock(return_value="[1] fallback\n\nSources:\n[1] source"),
            ):
                return await _web_search_with_citations(
                    "test query", search=mock_search, ddg_fallback=True,
                )

        result = _run(run())
        assert result.startswith("[DDG-FALLBACK]")

    def test_citations_include_reliability_label(self):
        mock_search = MagicMock()
        mock_search.results = MagicMock(return_value=[
            {"snippet": "wiki content", "title": "Wikipedia", "link": "https://en.wikipedia.org/wiki/Test"},
        ])

        async def run():
            return await _web_search_with_citations("test query", search=mock_search)

        result = _run(run())
        assert "[HIGH]" in result

    def test_hanging_searxng_times_out_instead_of_blocking_forever(self, monkeypatch):
        """A degraded SearXNG that never returns must not hang the pipeline
        indefinitely — confirmed live: a 502-ing instance blocked a real
        request for 6+ minutes before this timeout was added."""
        import time
        import web_search as ws
        monkeypatch.setattr(ws, "_SEARCH_TIMEOUT_S", 0.05)

        mock_search = MagicMock()
        mock_search.results = MagicMock(side_effect=lambda *a, **kw: time.sleep(1.0) or [])
        mock_search.run = MagicMock(return_value="")

        async def run():
            return await _web_search_with_citations("test query", search=mock_search, ddg_fallback=False)

        t0 = time.monotonic()
        result = _run(run())
        elapsed = time.monotonic() - t0
        assert result == ""
        assert elapsed < 1.0, f"expected fast timeout, took {elapsed:.2f}s"
