#!/usr/bin/env python3
"""
scripts/clean_delimiter_leak.py — Phase 0 of the 2026-09-10 campaign-restart
plan: repair the one real defect found in the newly generated role_sft data
(distinct from the August-campaign catastrophe, see agent_status/claude-code.md
2026-09-10T19:xx entries).

Found live via full-file measurement across all 9 merged datasets
(~1.6% of lines): the USER turn contains a real, complete request, followed
by the teacher model's own generation-template delimiter
(`===ASSISTANT_RESPONSE===`, `===END===`, a literal `USER_REQUEST` echo, or
a stray `<|im_start|>`) and then the teacher's internal monologue about how
it plans to answer -- a template-leak, not a content problem. The ASSISTANT
turn in every inspected case was clean. This is mechanically repairable by
truncating the USER turn at the first delimiter occurrence, keeping
everything before it.

After truncation, a line is dropped (not repaired) if:
  - the USER turn is now shorter than _MIN_REQUEST_LEN (10 chars, same
    threshold already used in generate_diverse_training_seeds.py), or
  - the USER turn starts with a backtick/bracket fragment (a truncated
    code-block artifact, not a real request -- 42 verified live cases).

Usage:
    python3 scripts/clean_delimiter_leak.py \
        --input datasets/merged/dataset_expert_coder_merged.jsonl \
        --output datasets/merged/dataset_expert_coder_merged.jsonl \
        [--report]
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Bare core substrings, not the full "===X===" wrapper: found live that the
# leaked delimiter is frequently noisy around the marker itself -- missing
# trailing "===" (followed by "</think>" instead), a duplicated/typo'd
# "ASSISTANASSISTANT_RESPONSE", or a newline inserted mid-marker
# ("===\nASSISTANT_RESPONSE==="). Matching just the stable core catches
# every observed variant; matching the full wrapped literal missed ~80/430
# leaked lines (verified by re-scanning the first cleaning pass's output).
_DELIMITERS = [
    "ASSISTANT_RESPONSE",
    "===END===",
    "USER_REQUEST",
    "<|im_start|>",
]
_MIN_REQUEST_LEN = 10
_FRAGMENT_RE = re.compile(r"^[`)\]}]")
_PLACEHOLDER_RE = re.compile(r"^<[^<>]{3,80}>$")


def _turns(text: str):
    u_start = text.find("<|im_start|>user\n")
    u_start = u_start + len("<|im_start|>user\n") if u_start >= 0 else -1
    u_end = text.find("<|im_end|>", u_start) if u_start >= 0 else -1
    a_start = text.find("<|im_start|>assistant\n")
    a_start = a_start + len("<|im_start|>assistant\n") if a_start >= 0 else -1
    a_end = text.find("<|im_end|>", a_start) if a_start >= 0 else -1
    user = text[u_start:u_end] if u_start >= 0 and u_end > u_start else None
    return u_start, u_end, a_start, a_end, user


def clean_line(text: str):
    """Returns (new_text, changed, dropped_reason_or_None)."""
    u_start, u_end, a_start, a_end, user = _turns(text)
    if user is None:
        return text, False, None

    if _PLACEHOLDER_RE.match(user.strip()):
        return text, True, "raw_placeholder_field"

    earliest = len(user)
    for delim in _DELIMITERS:
        idx = user.find(delim)
        if idx != -1 and idx < earliest:
            earliest = idx
    truncated = user[:earliest].rstrip()

    if truncated == user.rstrip():
        return text, False, None

    if len(truncated) < _MIN_REQUEST_LEN:
        return text, True, "too_short_after_truncation"
    if _FRAGMENT_RE.match(truncated):
        return text, True, "fragment_after_truncation"

    new_text = text[:u_start] + truncated + text[u_end:]
    return new_text, True, None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--report", action="store_true", help="print before/after for every changed line")
    args = p.parse_args()

    lines = Path(args.input).read_text(encoding="utf-8").splitlines()
    kept = []
    n_changed = 0
    n_dropped = 0
    drop_reasons = {}
    for line in lines:
        if not line.strip():
            continue
        obj = json.loads(line)
        text = obj["text"]
        new_text, changed, drop_reason = clean_line(text)
        if drop_reason:
            n_dropped += 1
            drop_reasons[drop_reason] = drop_reasons.get(drop_reason, 0) + 1
            if args.report:
                print(f"DROPPED ({drop_reason}): {text[:150]!r}", file=sys.stderr)
            continue
        if changed:
            n_changed += 1
            if args.report:
                _, _, _, _, old_user = _turns(text)
                _, _, _, _, new_user = _turns(new_text)
                print(f"CLEANED:\n  before: {old_user[:150]!r}\n  after:  {new_user[:150]!r}\n", file=sys.stderr)
        obj["text"] = new_text
        kept.append(json.dumps(obj, ensure_ascii=False))

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text("\n".join(kept) + "\n", encoding="utf-8")

    print(f"=== clean_delimiter_leak: {args.input} ===")
    print(f"  total lines       : {len(lines)}")
    print(f"  cleaned (leak cut): {n_changed}")
    print(f"  dropped           : {n_dropped} {drop_reasons}")
    print(f"  final count       : {len(kept)} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
