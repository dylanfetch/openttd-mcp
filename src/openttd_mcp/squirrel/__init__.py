"""Squirrel bridge source paths shipped with the OpenTTD MCP package."""

from pathlib import Path

MCP_BRIDGE_DIR = Path(__file__).resolve().parent / "mcp_bridge"
BUS_ROUTE_AI_DIR = MCP_BRIDGE_DIR

__all__ = ["BUS_ROUTE_AI_DIR", "MCP_BRIDGE_DIR"]
