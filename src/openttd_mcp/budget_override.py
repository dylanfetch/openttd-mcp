"""Budget-cut sentinel protocol shared by episode runner producers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

BUDGET_OVERRIDE_FILENAME = "budget_override.json"


def write_budget_override(artifact_root: Path, payload: dict[str, Any]) -> bool:
    """Atomically publish the first episode-stop sentinel.

    Multiple independent referees may stop the same episode. A complete unique
    temporary file is hard-linked into the shared path, so readers never see a
    partial JavaScript Object Notation (JSON) object and exactly one producer
    wins without a check-then-write race. Returns whether this payload won.
    """
    override_path = artifact_root / BUDGET_OVERRIDE_FILENAME
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=artifact_root,
            prefix=".budget-override-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_path, override_path)
        except FileExistsError:
            return False
        return True
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
