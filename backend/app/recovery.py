"""Recovers usable JSON from model output that was cut off before it finished."""

import json


def recover_truncated_json(text: str) -> tuple[dict | None, int]:
    """Parses JSON, repairing a cut-off tail at its last complete element; returns (dict or None, chars dropped)."""
    if not text or not text.strip():
        return None, 0

    stripped = text.strip()

    try:
        return json.loads(stripped), 0
    except json.JSONDecodeError:
        pass

    frames: list[list] = []
    in_string = False
    escaped = False

    for i, ch in enumerate(stripped):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch in "{[":
            frames.append(["}" if ch == "{" else "]", None])
        elif ch in "}]":
            if frames:
                frames.pop()
            if frames:
                frames[-1][1] = i
        elif ch == "," and frames:
            frames[-1][1] = i - 1

    if not frames:
        return None, len(stripped)

    while frames:
        closer, last_complete = frames[-1]
        if last_complete is None:
            frames.pop()
            continue

        candidate = stripped[: last_complete + 1].rstrip().rstrip(",")
        repaired = candidate + "".join(f[0] for f in reversed(frames))
        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError:
            frames.pop()
            continue

        if not isinstance(parsed, dict):
            return None, len(stripped)
        return parsed, len(stripped) - len(candidate)

    return None, len(stripped)
