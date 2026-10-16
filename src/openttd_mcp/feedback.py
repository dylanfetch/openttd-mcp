"""Model feedback channel helpers for the ``report_feedback`` tool.

The channel is non-executing evidence: a validated entry is appended to
``feedback.jsonl`` in the run dir, costs the model nothing in game time or money,
and grants no capability mid-run.

This channel is the playing model's own voice, recorded per turn, as opposed
to any reflective review of the finished run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FEEDBACK_KINDS = (
    "missing_observation",
    "missing_action",
    "ambiguous_tool_result",
    "strategic_note",
)

REQUIRED_STRING_FIELDS = ("description", "why_it_matters", "current_blocker")


def validate_report_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a ``report_feedback`` payload; return the normalized entry.

    Raises ``ValueError`` on a bad ``kind`` or a missing/empty required string.
    Unknown extra fields are dropped rather than recorded.
    """
    if not isinstance(payload, dict):
        raise ValueError("feedback payload must be a JSON object")
    kind = payload.get("kind")
    if kind not in FEEDBACK_KINDS:
        raise ValueError(f"feedback.kind must be one of: {', '.join(FEEDBACK_KINDS)}")
    normalized: dict[str, Any] = {"kind": kind}
    for field in REQUIRED_STRING_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"feedback.{field} must be a non-empty string")
        normalized[field] = value.strip()
    normalized["schema_version"] = 1
    return normalized


def append_feedback(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def load_feedback(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries
