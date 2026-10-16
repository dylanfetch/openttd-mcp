# Working with OpenTTD MCP

Start with README.md.

## Helping someone set up

1. The runtime setup script uses Ubuntu 26.04 amd64 packages (WSL 2 works).
   Don't claim other platforms work without testing them.
2. Install the Python package and run `tools/setup_openttd_runtime.py` as the
   README describes. Downloads, saves, and logs go under the ignored `.local/`.
3. Run `tools/smoke_test.py`. It's only a pass if it makes a real MCP connection
   and tool calls and confirms the final save.
4. Configure the user's MCP client using `docs/setup/connect-a-client.md`. Use
   absolute paths and keep the client's unrelated settings. Confirm a live tool
   call from that client, and say so if the client needs a restart first.
5. Explain that disconnecting ends the game, where `final.sav` is stored, and
   that reconnecting starts a new game. See `docs/usage.md` for saves.

## Changing the code

- Tools perform explicit actions and report facts. Don't build route planning
  or strategy into a tool.
- `src/openttd_mcp/server.py` defines tool schemas and descriptions;
  `src/openttd_mcp/squirrel/mcp_bridge/` implements the in-game bridge.
- `play.py` handles game startup and cleanup, `runtime.py` locates the OpenTTD
  runtime, and `inspector.py` reviews completed saves.
- Before finishing, run `uv run pytest -q`, `uv run ruff check .`, and
  `uv run ty check`. If the tool registry changed, regenerate the catalog with
  `uv run python tools/generate_tool_catalog.py`.
- Don't commit runtime binaries, saves, logs, or client configuration.
