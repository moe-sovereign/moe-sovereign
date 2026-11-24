"""Normalise debate transcripts from plain text or the known JSON layouts.

Known JSON layouts (see the debate fixtures in the repository root) use
different keys for the turn list (``debate_rounds``, ``debate_transcript``,
``rounds``) and for the spoken text (``speech_content``, ``speech``, ``text``).
Plain text uses ``Speaker: text`` lines; lines without a speaker prefix
continue the previous turn.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from .contracts import DebateAnalysisError, Transcript, Turn

MAX_TRANSCRIPT_CHARS = 400_000

_TURN_LIST_KEYS = ("debate_rounds", "debate_transcript", "rounds", "turns")
_TEXT_KEYS = ("speech_content", "speech", "text", "content")
_SPEAKER_KEYS = ("speaker", "name", "author")

# "Name: text". The speaker part is short and contains no sentence punctuation,
# so ordinary prose containing a colon is not mistaken for a speaker label.
_SPEAKER_LINE = re.compile(r"^\s*([^\s:][^:\n]{0,79}?)\s*:\s+(\S.*)$")


class TranscriptError(DebateAnalysisError):
    """Raised when a transcript cannot be normalised."""


def _hash_turns(turns: list[Turn]) -> str:
    digest = hashlib.sha256()
    for turn in turns:
        digest.update(turn.speaker.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(turn.text.encode("utf-8"))
        digest.update(b"\x01")
    return digest.hexdigest()


def _build(turns: list[Turn], source_format: str) -> Transcript:
    if len(turns) < 2:
        raise TranscriptError("a debate needs at least two turns")
    return Transcript(
        turns=turns,
        source_format=source_format,
        sha256=_hash_turns(turns),
    )


def _first_key(item: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _from_json_list(items: list[Any], source_format: str) -> Transcript:
    turns: list[Turn] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise TranscriptError("every turn must be an object")
        speaker = _first_key(item, _SPEAKER_KEYS)
        text = _first_key(item, _TEXT_KEYS)
        if speaker is None or text is None:
            raise TranscriptError("every turn needs a speaker and a text")
        turns.append(Turn(index=len(turns), speaker=speaker, text=text))
    return _build(turns, source_format)


def _from_json(data: Any) -> Transcript:
    if isinstance(data, list):
        return _from_json_list(data, "json:list")
    if isinstance(data, Mapping):
        for key in _TURN_LIST_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                return _from_json_list(value, f"json:{key}")
    raise TranscriptError("no known turn list in JSON transcript")


def _from_text(text: str) -> Transcript:
    turns: list[Turn] = []
    speaker: str | None = None
    parts: list[str] = []

    def flush() -> None:
        if speaker is not None and parts:
            turns.append(
                Turn(index=len(turns), speaker=speaker, text=" ".join(parts).strip())
            )

    for line in text.splitlines():
        if not line.strip():
            continue
        match = _SPEAKER_LINE.match(line)
        if match:
            flush()
            speaker, parts = match.group(1).strip(), [match.group(2).strip()]
        elif speaker is not None:
            parts.append(line.strip())
        else:
            raise TranscriptError("text before the first speaker label")
    flush()
    return _build(turns, "text")


def normalize_transcript(raw: Any) -> Transcript:
    """Return a :class:`Transcript` from text, JSON text, a mapping or a list."""

    if isinstance(raw, Transcript):
        return raw
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TranscriptError("transcript is not valid UTF-8") from exc
    if isinstance(raw, str):
        if len(raw) > MAX_TRANSCRIPT_CHARS:
            raise TranscriptError("transcript exceeds the size limit")
        stripped = raw.lstrip()
        if stripped[:1] in ("{", "["):
            try:
                return _from_json(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise TranscriptError("transcript is not valid JSON") from exc
        return _from_text(raw)
    if isinstance(raw, (Mapping, list)):
        return _from_json(raw)
    raise TranscriptError("unsupported transcript type")
