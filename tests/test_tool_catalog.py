"""Public behavior checks for the generated Model Context Protocol tool catalog."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from generate_tool_catalog import live_catalog_registry, render_catalog

ROOT = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, str(ROOT / "tools" / "generate_tool_catalog.py")]


def synthetic_tool(
    name: str, *, mutates_game: bool, property_name: str
) -> dict[str, object]:
    return {
        "name": name,
        "description": f"Synthetic description for {name}.",
        "inputSchema": {
            "type": "object",
            "properties": {property_name: {"type": "integer"}},
            "required": [property_name],
            "additionalProperties": False,
        },
        "mutates_game": mutates_game,
    }


def registry(*tools: dict[str, object]) -> dict[str, object]:
    return {"tools": list(tools)}


def tool_sections(markdown: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^## `([^`]+)`$", markdown, flags=re.MULTILINE))
    return [
        (
            match.group(1),
            markdown[
                match.end() : matches[index + 1].start()
                if index + 1 < len(matches)
                else len(markdown)
            ],
        )
        for index, match in enumerate(matches)
    ]


def mutation_lines(body: str) -> list[str]:
    return [
        line for line in body.splitlines() if line.startswith("Mutates game state: ")
    ]


def run_command(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*COMMAND, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def synthetic_catalog() -> tuple[str, dict[str, dict[str, object]]]:
    sensor = synthetic_tool("zeta_sensor", mutates_game=False, property_name="tile")
    action = synthetic_tool("alpha_action", mutates_game=True, property_name="count")
    common = synthetic_tool("middle_sensor", mutates_game=False, property_name="year")
    rendered = render_catalog(registry(sensor, action, common))
    return rendered, {tool["name"]: tool for tool in (sensor, action, common)}


def test_renderer_does_not_omit_or_reorder_registry_tools() -> None:
    rendered, tools = synthetic_catalog()

    sections = tool_sections(rendered)
    assert [name for name, _ in sections] == [
        "zeta_sensor",
        "alpha_action",
        "middle_sensor",
    ]

    schemas = {}
    for name, body in sections:
        schema_text = body.split("```json\n", 1)[1].split("\n```", 1)[0]
        schemas[name] = json.loads(schema_text)
    assert schemas == {name: tool["inputSchema"] for name, tool in tools.items()}


def test_renderer_does_not_invert_the_registry_mutation_fact() -> None:
    rendered, _ = synthetic_catalog()
    sections = dict(tool_sections(rendered))

    assert mutation_lines(sections["zeta_sensor"]) == ["Mutates game state: no"]
    assert mutation_lines(sections["alpha_action"]) == ["Mutates game state: yes"]


def test_checked_in_catalog_preserves_live_registry_contract_facts() -> None:
    catalog = (ROOT / "docs" / "tool-surface" / "tool-catalog.md").read_text(
        encoding="utf-8"
    )
    catalog_sections = tool_sections(catalog)
    live_tools = live_catalog_registry()["tools"]

    assert [name for name, _ in catalog_sections] == [
        tool["name"] for tool in live_tools
    ]
    for (_, body), tool in zip(catalog_sections, live_tools, strict=True):
        schema_text = body.split("```json\n", 1)[1].split("\n```", 1)[0]
        assert json.loads(schema_text) == tool["inputSchema"]
        expected_mutation = "yes" if tool["mutates_game"] else "no"
        assert mutation_lines(body) == [f"Mutates game state: {expected_mutation}"]


def test_checked_in_catalog_is_regenerated_after_registry_edits() -> None:
    # Freshness of the generated artifact, not its prose: any served-registry
    # edit (including a description) must be followed by a regeneration, or
    # the checked-in catalog silently diverges from what the server serves.
    result = run_command(
        "--out", str(ROOT / "docs" / "tool-surface" / "tool-catalog.md"), "--check"
    )
    assert result.returncode == 0, result.stderr


def test_check_does_not_reject_a_current_catalog(tmp_path: Path) -> None:
    target = tmp_path / "catalog.md"
    generated = run_command("--out", str(target))
    assert generated.returncode == 0, generated.stderr

    current = run_command("--out", str(target), "--check")
    assert current.returncode == 0, current.stderr


def test_check_does_not_rewrite_a_stale_catalog_while_reporting_drift(
    tmp_path: Path,
) -> None:
    target = tmp_path / "catalog.md"
    generated = run_command("--out", str(target))
    assert generated.returncode == 0, generated.stderr

    stale_bytes = target.read_bytes() + b"stale marker\n"
    target.write_bytes(stale_bytes)
    stale = run_command("--out", str(target), "--check")
    assert stale.returncode != 0
    assert target.read_bytes() == stale_bytes
