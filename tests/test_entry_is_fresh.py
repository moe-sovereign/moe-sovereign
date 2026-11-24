"""_entry_is_fresh must accept both timestamp styles the cache writers use: timezone-aware UTC (response_commit) and naive
local time (agent_enrichment). Mixing naive and aware datetimes used to raise, which the helper turned into "stale"."""
from datetime import datetime, timedelta, timezone

from services.helpers import _entry_is_fresh


def test_aware_utc_timestamp_within_ttl_is_fresh():
    assert _entry_is_fresh((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), 7)


def test_naive_local_timestamp_within_ttl_is_fresh():
    assert _entry_is_fresh((datetime.now() - timedelta(days=1)).isoformat(), 7)


def test_timestamps_older_than_the_ttl_are_stale():
    assert not _entry_is_fresh((datetime.now(timezone.utc) - timedelta(days=9)).isoformat(), 7)
    assert not _entry_is_fresh((datetime.now() - timedelta(days=9)).isoformat(), 7)


def test_missing_or_garbage_timestamps_are_stale():
    assert not _entry_is_fresh("", 7)
    assert not _entry_is_fresh("not a date", 7)
