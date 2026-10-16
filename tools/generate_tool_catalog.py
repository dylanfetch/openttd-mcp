#!/usr/bin/env python3
"""Render the served OpenTTD MCP registry as a checked-in Markdown catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openttd_mcp.server import registry_snapshot

CATALOG_PROFILE = {
    "enable_skill": True,
    "enable_vision": True,
    "end_episode_mode": "standard",
}


def live_catalog_registry() -> dict[str, Any]:
    """Return the served registry for the catalog's declared profile."""
    return registry_snapshot(**CATALOG_PROFILE)


def render_catalog(catalog_registry: dict[str, Any]) -> str:
    """Render one explicitly declared served profile in registry order."""
    profile_lines = []
    for name, value in CATALOG_PROFILE.items():
        rendered_value = (
            ("true" if value else "false") if isinstance(value, bool) else value
        )
        profile_lines.append(f"- `{name}={rendered_value}`")
    lines = [
        "# OpenTTD MCP tool catalog",
        "",
        (
            "This file is generated from the server's served MCP registry; "
            "edit the registry or generator instead of this file."
        ),
        "",
        "Catalog profile:",
        "",
        *profile_lines,
        "",
        (
            "This profile includes the optional skill and vision surfaces rather "
            "than the command-line defaults. Vision also adds fields to existing "
            "subject-tool schemas. The `end_episode` description shown is the "
            "standard-mode version rather than the distress-mode version. "
            "`openttd-mcp play` serves the command-line defaults: no "
            "`get_bus_route_skill`, no `view_map`, and no vision-added schema "
            "fields — the rest of this catalog matches what it serves."
        ),
        "",
        "Regenerate it with:",
        "",
        "```console",
        "uv run python tools/generate_tool_catalog.py",
        "```",
        "",
        "Detect drift without rewriting the file with:",
        "",
        "```console",
        "uv run python tools/generate_tool_catalog.py --check",
        "```",
        "",
    ]

    for tool in catalog_registry["tools"]:
        name = tool["name"]
        mutates_game = "yes" if tool["mutates_game"] else "no"
        schema = json.dumps(
            tool["inputSchema"], ensure_ascii=False, indent=2, sort_keys=True
        )
        lines.extend(
            [
                f"## `{name}`",
                "",
                f"Mutates game state: {mutates_game}",
                "",
                tool["description"].strip(),
                "",
                "Input schema:",
                "",
                "```json",
                schema,
                "```",
                "",
            ]
        )

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "docs" / "tool-surface" / "tool-catalog.md",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero when --out differs; never rewrite it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rendered = render_catalog(live_catalog_registry())

    if args.check:
        try:
            current = args.out.read_text(encoding="utf-8")
        except OSError as error:
            print(f"tool catalog check failed: {error}", file=sys.stderr)
            return 1
        if current != rendered:
            print(f"tool catalog is stale: {args.out}", file=sys.stderr)
            return 1
        return 0

    args.out.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
