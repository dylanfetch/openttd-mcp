# Connect an MCP client

Finish the README setup and smoke test first. Replace every
`/ABSOLUTE/PATH/TO/openttd-mcp` below with your checkout path, and run Linux
clients inside the same Ubuntu/WSL environment as the runtime. If `uv` isn't on
the client's PATH, use the absolute path from `command -v uv`.

The client launches the server over stdio; there is no URL or daemon. The
server boots the game before it answers the client, so the client must allow a
long startup. Recommended limits:

- **Startup:** 600 seconds.
- **Tool calls:** 300 seconds. The server answers or errors within 240.

Each connection starts a new game. Closing it saves and stops that game.

## Generic configuration

Most clients accept this `mcpServers` JSON:

```json
{
  "mcpServers": {
    "openttd": {
      "command": "uv",
      "args": [
        "run", "--locked", "--no-dev", "--project",
        "/ABSOLUTE/PATH/TO/openttd-mcp", "openttd-mcp", "play"
      ],
      "env": {
        "OPENTTD_MCP_REPO_ROOT": "/ABSOLUTE/PATH/TO/openttd-mcp"
      }
    }
  }
}
```

To confirm the connection, ask the agent to call `list_towns` and
`get_my_finances`. A saved config alone does not prove the game is running.

## Codex

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.openttd]
command = "uv"
args = ["run", "--locked", "--no-dev", "--project", "/ABSOLUTE/PATH/TO/openttd-mcp", "openttd-mcp", "play"]
startup_timeout_sec = 600
tool_timeout_sec = 300
required = true

[mcp_servers.openttd.env]
OPENTTD_MCP_REPO_ROOT = "/ABSOLUTE/PATH/TO/openttd-mcp"
```

`required = true` makes Codex report a startup failure instead of continuing
without the tools. Check with `codex mcp list` and `/mcp`; remove with
`codex mcp remove openttd`. See the
[Codex MCP documentation](https://developers.openai.com/codex/mcp).

## Claude Code

From the directory where you want to play:

```console
claude mcp add --scope project --transport stdio openttd \
  -e OPENTTD_MCP_REPO_ROOT=/ABSOLUTE/PATH/TO/openttd-mcp \
  -- uv run --locked --no-dev --project /ABSOLUTE/PATH/TO/openttd-mcp openttd-mcp play
```

Start Claude Code with longer MCP limits (milliseconds):

```console
MCP_CONNECTION_NONBLOCKING=0 MCP_TIMEOUT=600000 MCP_CONNECT_TIMEOUT_MS=600000 MCP_TOOL_TIMEOUT=300000 claude
```

Approve the server when prompted and check it with `/mcp`. Remove it with
`claude mcp remove openttd --scope project`. See the
[Claude Code MCP guide](https://code.claude.com/docs/en/mcp).

## Cursor and pi

I've also played through Cursor's CLI and through pi (with the
`pi-mcp-adapter` extension), but both take more setup and are more finicky,
mainly around timeouts. Start with Codex or Claude Code.

## Using pip instead of uv

```console
python3 -m venv .venv
.venv/bin/python -m pip install .
```

In any configuration above, set the command to
`/ABSOLUTE/PATH/TO/openttd-mcp/.venv/bin/openttd-mcp` with arguments `["play"]`.
Keep `OPENTTD_MCP_REPO_ROOT` and the timeouts. To run the smoke test this way:
`OPENTTD_MCP_REPO_ROOT="$PWD" .venv/bin/python tools/smoke_test.py`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `uv` or `openttd-mcp` not found | Use the absolute executable path. |
| Missing `.local/openttd-root/usr/games/openttd` | Run the runtime setup script; `OPENTTD_MCP_REPO_ROOT` must point to the checkout containing `.local/`. |
| No tools, or startup timeout | Raise the client's startup limit, approve the server, and restart the client after editing its config. |
| A tool times out | Raise the per-tool limit to 300 seconds, or set `OPENTTD_MCP_WAIT_DAYS_MAX` lower. |
| Startup fails after launching the game | Read the latest `.local/runs/*-play/openttd.log` and `play_cleanup.json`. |
| No `final.sav` | Read `play_cleanup.json`. Let shutdown finish; force-killing can interrupt saving. |

To tell client problems from game problems, run the README smoke test again.
