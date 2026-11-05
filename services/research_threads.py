"""Caller-chosen LangGraph thread ids for research rollouts (see routes/research.py).

Kept free of route/pipeline imports so services.pipeline.chat can use it without an import cycle.
"""
import re
from typing import Optional

from config import RESEARCH_ROUTES_ENABLED

THREAD_RE = re.compile(r"^research-[A-Za-z0-9_-]{8,80}$")


def research_thread_id(header_value: Optional[str], enabled: Optional[bool] = None) -> Optional[str]:
    """Return a caller-chosen thread id when research mode is on and the id is well formed."""
    if not (RESEARCH_ROUTES_ENABLED if enabled is None else enabled) or not header_value:
        return None
    value = header_value.strip()
    return value if THREAD_RE.match(value) else None
