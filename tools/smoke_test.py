#!/usr/bin/env python3
"""Check a real MCP connection, game observations, and final-save cleanup."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def tool_payload(result) -> dict:
    """Require game success as well as a successful MCP response."""
    if result.isError:
        raise RuntimeError(f"MCP tool failed: {result.content}")
    payload = result.structuredContent
    if payload is None:
        payload = next(
            (
                json.loads(block.text)
                for block in result.content
                if block.type == "text"
            ),
            None,
        )
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"Game tool did not report success: {payload}")
    return payload


async def check_connection(root: Path, run_dir: Path) -> dict:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m", "openttd_mcp.server", "play", "--run-dir", str(run_dir),
        ],
        env={"OPENTTD_MCP_REPO_ROOT": str(root)},
        cwd=root,
    )
    async with (
        stdio_client(parameters) as (reader, writer),
        ClientSession(
            reader, writer, read_timeout_seconds=timedelta(seconds=660)
        ) as session,
    ):
        await session.initialize()
        registry = await session.list_tools()
        names = {tool.name for tool in registry.tools}
        required = {"list_towns", "get_my_finances", "build_road"}
        if not required.issubset(names):
            raise RuntimeError(f"Missing tools: {sorted(required - names)}")
        towns = tool_payload(await session.call_tool("list_towns", {}))
        finances = tool_payload(await session.call_tool("get_my_finances", {}))
    cleanup = json.loads((run_dir / "play_cleanup.json").read_text())
    save = run_dir / "final.sav"
    if not cleanup.get("ok") or not save.is_file() or save.stat().st_size == 0:
        raise RuntimeError(f"Game did not save and shut down cleanly: {cleanup}")
    return {
        "ok": True,
        "tool_count": len(names),
        "towns": towns,
        "finances": finances,
        "run_dir": str(run_dir),
        "final_save": str(save),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, help="fresh output directory")
    args = parser.parse_args()
    root = Path(
        os.environ.get("OPENTTD_MCP_REPO_ROOT", Path(__file__).resolve().parents[1])
    ).expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (
        args.run_dir or root / ".local" / "runs" / f"{stamp}-{uuid4().hex[:8]}-smoke"
    ).resolve()
    print(f"Starting a game; allow up to 10 minutes. Run: {run_dir}", file=sys.stderr)
    try:
        result = asyncio.run(check_connection(root, run_dir))
    except Exception as exc:  # noqa: BLE001 -- CLI reports failure after transport cleanup
        print(f"Smoke test failed: {exc}\nInspect logs in {run_dir}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
